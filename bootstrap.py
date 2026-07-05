"""初始化向导:自动抓取选课所需的用户信息、课程目录和已选课程。

用法:
  python bootstrap.py                # 全流程:用户信息 + 课程目录 + 已选课程
  python bootstrap.py --debug
  python bootstrap.py --skip-catalog # 只抓用户信息和已选,不抓课程目录

产出:
  1. user_settings.json 的 query_overrides / term 分区 —— 个人查询参数自动填充,
     新用户无需再从 HAR 手工提取 zyh_id / njdm_id 等字段;
  2. catalog.json —— 可选课程目录(课程 + 教学班)及当前已选课程,
     供 GUI"选课设置"页选择监控目标、排优先级。

数据来源(与 HAR 抓包对照):
  - 选课首页 PAGE_URL 的 hidden input        → 个人查询参数、选课学年学期
  - kbcx/xskbcx_cxXsgrkb (个人课表)         → zyh_id / njdm_id / 姓名学号 (已验证)
  - xsxk/..._cxTjxkBkkDisplay 空 kch_id 查询 → 全部普通课程及教学班
  - xsxk/..._cxJxbTjxkBkk 空 kch_id 查询     → 全部体育课教学班
  - xsxk/..._cxTjxkBkkChoosedCourse          → 当前已选课程 (test_choosed.py 已验证)

⚠ 以下行为在当前网络环境无法实测,首次在校园网运行请加 --debug 观察:
  - 选课首页 hidden input 是否包含全部个人参数
  - display / pe 接口空 kch_id 查询是否返回全部课程(还是要求必填)
  - cxXsgrkb 用空 xnm/xqm 参数是否默认返回当前学期
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime

import requests

import config
from login import login

log = logging.getLogger("bootstrap")

_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": config.REFERER,
    "Origin": "https://i.sjtu.edu.cn",
    "User-Agent": config.USER_AGENT,
}
_PAGE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "User-Agent": config.USER_AGENT,
}

# hidden input 里这些 key 属于"每次查询单独给"的参数,不写进个人覆盖
_NON_PERSONAL_KEYS = {"kch_id", "jxb_id", "kklxdm", "bklx_id", "xkxnm", "xkxqm"}

# catalog.json 里每个教学班保留的字段(全量字段太大,只留展示和监控要用的)
_CLASS_FIELDS = (
    "jxb_id", "jxbmc", "kch", "kcmc", "jxbrl", "jxbxzrs", "yxzrs",
    "jsxx", "sksj", "jxdd", "xf", "kcxzmc", "kklxdm",
)


def _parse_hidden_inputs(html: str) -> dict[str, str]:
    """提取页面中全部 <input type="hidden"> 的 id/name → value。"""
    found: dict[str, str] = {}
    for tag in re.findall(r"<input\b[^>]*>", html, re.I):
        if not re.search(r"type\s*=\s*[\"']hidden[\"']", tag, re.I):
            continue
        m_key = re.search(r"(?:id|name)\s*=\s*[\"']([^\"']+)[\"']", tag, re.I)
        m_val = re.search(r"value\s*=\s*[\"']([^\"']*)[\"']", tag, re.I)
        if m_key and m_val:
            found.setdefault(m_key.group(1), m_val.group(1))
    return found


def fetch_page_params(session: requests.Session) -> dict[str, str]:
    """GET 选课首页,收集 hidden input 里的查询参数(个人字段 + 学期)。"""
    r = session.get(
        config.PAGE_URL, headers=_PAGE_HEADERS, timeout=15, allow_redirects=False
    )
    if r.status_code != 200:
        raise RuntimeError(f"选课首页返回 status={r.status_code},session 可能失效")
    hidden = _parse_hidden_inputs(r.text)
    known = set(config.query_common("display")) | set(config.query_common("pe"))
    params = {k: v for k, v in hidden.items() if k in known}
    log.info("[首页] 共 %d 个 hidden input,命中查询参数 %d 个: %s",
             len(hidden), len(params), sorted(params))
    return params


def fetch_xsxx(session: requests.Session) -> dict:
    """个人课表接口的 xsxx 块:ZYH_ID/NJDM_ID/XM/XH/BJMC/ZYMC。"""
    r = session.post(
        config.XSGRKB_URL,
        data={"xnm": "", "xqm": "", "kzlx": "ck"},
        headers=_HEADERS, timeout=15, allow_redirects=False,
    )
    if "application/json" not in r.headers.get("content-type", ""):
        raise RuntimeError(f"cxXsgrkb 非 JSON 响应: status={r.status_code}")
    xsxx = r.json().get("xsxx") or {}
    if xsxx:
        log.info("[身份] %s (%s) %s %s",
                 xsxx.get("XM"), xsxx.get("XH"), xsxx.get("BJMC"), xsxx.get("ZYMC"))
    return xsxx


def fetch_choosed(session: requests.Session) -> list[dict]:
    """当前已选课程列表(接口已在 test_choosed.py 验证)。"""
    common = config.query_common("display")
    data = {
        k: common.get(k, "")
        for k in ("xkxnm", "xkxqm", "xkly", "njdm_id", "zyh_id",
                  "zyfx_id", "bh_id", "xz", "ccdm")
    }
    r = session.post(
        config.CHOOSED_URL, data=data, headers=_HEADERS,
        timeout=15, allow_redirects=False,
    )
    if "application/json" not in r.headers.get("content-type", ""):
        raise RuntimeError(f"choosed 非 JSON 响应: status={r.status_code}")
    result = r.json()
    return result if isinstance(result, list) else []


def _slim_class(cls: dict) -> dict:
    return {k: cls.get(k) for k in _CLASS_FIELDS if cls.get(k) is not None}


def fetch_display_catalog(session: requests.Session) -> list[dict]:
    """空 kch_id 查询 display 接口,返回课程目录(课程含其教学班列表)。"""
    payload = {**config.query_common("display"), "kch_id": "", "jxb_id": ""}
    r = session.post(
        config.DISPLAY_URL, data=payload, headers=_HEADERS,
        timeout=30, allow_redirects=False,
    )
    if "application/json" not in r.headers.get("content-type", ""):
        raise RuntimeError(f"display 目录查询非 JSON 响应: status={r.status_code}")
    data = r.json()
    tmp = data.get("tmpList") if isinstance(data, dict) else None
    if not tmp or len(tmp) < 2:
        log.warning("[目录] display 空查询未返回 tmpList,可能接口要求必填 kch_id")
        return []
    course_rows, class_rows = tmp[0] or [], tmp[1] or []
    by_kch: dict[str, dict] = {}
    for c in course_rows:
        kch = c.get("kch")
        if not kch:
            continue
        by_kch[kch] = {
            "kch": kch,
            "kch_id": c.get("kch_id"),
            "kcmc": c.get("kcmc"),
            "kklxdm": c.get("kklxdm"),
            "xf": c.get("xf"),
            "endpoint": "display",
            "classes": [],
        }
    for cls in class_rows:
        course = by_kch.get(cls.get("kch"))
        if course is not None:
            course["classes"].append(_slim_class(cls))
    log.info("[目录] display: %d 门课程 / %d 个教学班", len(by_kch), len(class_rows))
    return list(by_kch.values())


def fetch_pe_catalog(session: requests.Session) -> list[dict]:
    """空 kch_id 查询体育课接口,返回体育课目录(按 kch 分组)。"""
    payload = {**config.query_common("pe"), "kch_id": ""}
    r = session.post(
        config.JXB_LIST_URL, data=payload, headers=_HEADERS,
        timeout=30, allow_redirects=False,
    )
    if "application/json" not in r.headers.get("content-type", ""):
        raise RuntimeError(f"pe 目录查询非 JSON 响应: status={r.status_code}")
    data = r.json()
    class_rows = data if isinstance(data, list) else []
    if not class_rows:
        log.warning("[目录] pe 空查询未返回数据,可能接口要求必填 kch_id")
        return []
    by_kch: dict[str, dict] = {}
    for cls in class_rows:
        kch = cls.get("kch")
        if not kch:
            continue
        course = by_kch.setdefault(kch, {
            "kch": kch,
            # 体育课查询直接用课程号做 kch_id (与 KCH_QUERIES 现有约定一致)
            "kch_id": kch,
            "kcmc": cls.get("kcmc"),
            "kklxdm": cls.get("kklxdm") or "06",
            "endpoint": "pe",
            "classes": [],
        })
        course["classes"].append(_slim_class(cls))
    log.info("[目录] pe: %d 门课程 / %d 个教学班", len(by_kch), len(class_rows))
    return list(by_kch.values())


def _save_catalog(catalog: dict) -> None:
    tmp = config.CATALOG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(config.CATALOG_FILE)


def apply_user_info(page_params: dict[str, str], xsxx: dict) -> None:
    """把抓到的个人参数写进 user_settings.json 的 query_overrides / term 分区。

    优先级: 选课首页 hidden input > cxXsgrkb(仅补 zyh_id/njdm_id)> 已有配置。
    """
    display_keys = set(config.query_common("display")) - _NON_PERSONAL_KEYS
    pe_keys = set(config.query_common("pe")) - _NON_PERSONAL_KEYS
    display_over = {k: v for k, v in page_params.items() if k in display_keys}
    pe_over = {k: v for k, v in page_params.items() if k in pe_keys}
    if xsxx.get("ZYH_ID"):
        display_over.setdefault("zyh_id", xsxx["ZYH_ID"])
        pe_over.setdefault("zyh_id", xsxx["ZYH_ID"])
    if xsxx.get("NJDM_ID"):
        display_over.setdefault("njdm_id", xsxx["NJDM_ID"])
        pe_over.setdefault("njdm_id", xsxx["NJDM_ID"])

    sections: dict = {}
    old = config.QUERY_OVERRIDES
    sections["query_overrides"] = {
        "display": {**old.get("display", {}), **display_over},
        "pe": {**old.get("pe", {}), **pe_over},
    }
    term = {}
    if page_params.get("xkxnm"):
        term["xkxnm"] = page_params["xkxnm"]
    if page_params.get("xkxqm"):
        term["xkxqm"] = page_params["xkxqm"]
    if term:
        sections["term"] = {**config.USER_SETTINGS["term"], **term}
    config.update_user_settings(**sections)
    log.info("[设置] 已写入 user_settings.json: display 覆盖 %d 项, pe 覆盖 %d 项, term=%s",
             len(display_over), len(pe_over), term or "(未变)")


def run(session: requests.Session, skip_catalog: bool = False) -> dict:
    """执行初始化流程,返回 catalog dict(同时写盘)。每步失败不阻断后续。"""
    page_params: dict[str, str] = {}
    xsxx: dict = {}
    try:
        page_params = fetch_page_params(session)
    except Exception as e:
        log.warning("选课首页参数抓取失败(不阻断): %s", e)
    try:
        xsxx = fetch_xsxx(session)
    except Exception as e:
        log.warning("个人课表信息抓取失败(不阻断): %s", e)
    if page_params or xsxx:
        apply_user_info(page_params, xsxx)
    else:
        log.warning("未获取到任何用户信息,user_settings.json 保持不变")

    courses: list[dict] = []
    if not skip_catalog:
        try:
            courses += fetch_display_catalog(session)
        except Exception as e:
            log.warning("普通课目录抓取失败(不阻断): %s", e)
        try:
            courses += fetch_pe_catalog(session)
        except Exception as e:
            log.warning("体育课目录抓取失败(不阻断): %s", e)

    choosed: list[dict] = []
    try:
        choosed = [_slim_class(c) for c in fetch_choosed(session)]
        log.info("[已选] 当前已选 %d 门", len(choosed))
    except Exception as e:
        log.warning("已选课程抓取失败(不阻断): %s", e)

    catalog = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "user": {
            "xm": xsxx.get("XM"),
            "xh": xsxx.get("XH"),
            "bjmc": xsxx.get("BJMC"),
            "zymc": xsxx.get("ZYMC"),
            "zyh_id": xsxx.get("ZYH_ID"),
            "njdm_id": xsxx.get("NJDM_ID"),
        },
        "courses": courses,
        "choosed": choosed,
    }
    if courses or choosed:
        _save_catalog(catalog)
        log.info("[目录] 已写入 %s: %d 门课程, %d 门已选",
                 config.CATALOG_FILE.name, len(courses), len(choosed))
    else:
        log.warning("课程目录与已选均为空,不写 catalog.json")
    return catalog


def main():
    ap = argparse.ArgumentParser(description="自动抓取用户信息与课程目录")
    ap.add_argument("--debug", action="store_true", help="DEBUG 日志")
    ap.add_argument("--skip-catalog", action="store_true",
                    help="只抓用户信息和已选,不抓课程目录")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    session = requests.Session()
    login(session)
    catalog = run(session, skip_catalog=args.skip_catalog)

    user = catalog["user"]
    print()
    print(f"用户: {user.get('xm') or '?'} ({user.get('xh') or '?'})  "
          f"{user.get('bjmc') or ''} {user.get('zymc') or ''}")
    print(f"课程目录: {len(catalog['courses'])} 门课程, "
          f"{sum(len(c['classes']) for c in catalog['courses'])} 个教学班")
    print(f"当前已选: {len(catalog['choosed'])} 门")
    print("下一步: 打开 GUI 的\"选课设置\"页挑选监控目标并排优先级。")


if __name__ == "__main__":
    main()
