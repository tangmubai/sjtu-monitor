"""初始化向导:自动抓取选课所需的用户信息、课程目录和已选课程。

用法:
  python bootstrap.py                 # 全流程:用户信息 + 全量课程目录 + 已选课程
  python bootstrap.py --debug
  python bootstrap.py --skip-catalog  # 只抓用户信息和已选,不抓课程目录
  python bootstrap.py --with-capacity # 目录附带容量/教师/时间(逐课多查一次,慢)

产出:
  1. user_settings.json 的 query_overrides / term 分区 —— 个人查询参数自动填充,
     新用户无需再从 HAR 手工提取 zyh_id / njdm_id 等字段;
  2. catalog.json —— 全量课程目录(课程 + 教学班)及当前已选课程,
     供 GUI"选课设置"页选择监控目标、排优先级。

数据来源(2026-07-06 联网实测):
  - 选课首页 PAGE_URL 的 hidden input   → 个人查询参数、选课学年学期
  - kbcx/xskbcx_cxXsgrkb (个人课表)     → zyh_id / njdm_id / 姓名学号
  - zzxkyzb 自主选课模块 (zzxk.py)      → 全部非体育课程目录(主修/通识/公选/任选/交叉)
    ⚠ tjxkbkk display 空 kch_id 查询实测不返回数据,已弃用该目录来源
  - xsxk/..._cxJxbTjxkBkk 空 kch_id     → 体育课目录(tjxkbkk,保留原接口)
  - tjxkbkk ChoosedCourse + zzxkyzb ChoosedDisplay → 两个轮次的当前已选
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime

import requests

import config
import course_plus
import zzxk
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
    "jxb_id", "jxbmc", "kch", "kcmc",
    "jsxx", "sksj", "jxdd", "xf", "jxbxf", "kcxzmc", "kklxdm", "kzmc",
    "cxbj", "fxbj", "xxkbj",
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


def xsxx_query_variants(term: tuple[str, str] | None = None) -> list[dict]:
    variants: list[dict] = [{"xnm": "", "xqm": "", "kzlx": "ck"}]
    candidates = []
    if term and term[0]:
        candidates.append((term[0], term[1] or ""))
    candidates.append((config.XKXNM, config.XKXQM))
    seen = {("", "")}
    for xnm, xqm in candidates:
        key = (str(xnm or ""), str(xqm or ""))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        variants.append({"xnm": key[0], "xqm": key[1], "kzlx": "ck"})
    return variants


def fetch_xsxx(
    session: requests.Session, term: tuple[str, str] | None = None
) -> dict:
    """个人课表接口的 xsxx 块:ZYH_ID/NJDM_ID/XM/XH/BJMC/ZYMC。

    实测(2026-07):空学年学期服务端返回 null,须带当前学期重试;
    学期提示 term=(xnm, xqm) 可取自 zzxk 首页 hidden 的 xkxnm/xkxqm。
    """
    for data in xsxx_query_variants(term):
        r = session.post(
            config.XSGRKB_URL, data=data,
            headers=_HEADERS, timeout=15, allow_redirects=False,
        )
        if "application/json" not in r.headers.get("content-type", ""):
            raise RuntimeError(f"cxXsgrkb 非 JSON 响应: status={r.status_code}")
        payload = r.json()
        xsxx = (payload or {}).get("xsxx") or {}
        if xsxx:
            log.info("[身份] %s (%s) %s %s",
                     xsxx.get("XM"), xsxx.get("XH"),
                     xsxx.get("BJMC"), xsxx.get("ZYMC"))
            return xsxx
        log.debug("cxXsgrkb 参数 %s 返回空,尝试下一组", data)
    return {}


def fetch_choosed(session: requests.Session) -> list[dict]:
    """当前已选课程列表。"""
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


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def availability_from_row(row: dict) -> str:
    selected = _to_int(row.get("jxbxzrs", row.get("yxzrs")))
    capacity = _to_int(row.get("jxbrl"))
    if selected is None or capacity is None or capacity <= 0:
        return "unknown"
    return "open" if selected < capacity else "full"


def _slim_class(cls: dict, *, include_availability: bool = True) -> dict:
    out = {k: cls.get(k) for k in _CLASS_FIELDS if cls.get(k) is not None}
    if include_availability:
        out["availability"] = availability_from_row(cls)
    return out


def fetch_zzxk_catalog(
    session: requests.Session, with_capacity: bool = False
) -> list[dict]:
    """经 zzxkyzb 模块抓全量非体育课程目录(联网实测可用)。

    zzxk.fetch_full_catalog 已做 yxzrs→jxbxzrs 归一化;这里再走 _slim_class
    统一裁剪字段,保证 catalog.json 里 pe/zzxk 两来源的教学班结构一致。
    """
    # 目录没有独立空位接口:PartDisplay 给人数,JxbWithKch 给容量。
    # 两者只在内存中比较,原始数字不写 catalog.json。
    courses = zzxk.fetch_full_catalog(session, with_capacity=True)
    for course in courses:
        course["classes"] = [_slim_class(c) for c in course["classes"]]
    return courses


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
        # 实测(2026-07): 空 kch_id 不返回数据 → 回退为逐课查询已配置的体育课
        # (仍是同一 cxJxbTjxkBkk 接口,保持体育课接口不变)
        log.info("[目录] pe 空查询无数据,回退为逐课查询已配置体育课")
        for kch, q in config.KCH_QUERIES.items():
            if q.get("endpoint") != "pe":
                continue
            qp = config.build_query_payload(kch)
            if qp is None:
                continue
            url, per_payload = qp
            resp = session.post(url, data=per_payload, headers=_HEADERS,
                                timeout=15, allow_redirects=False)
            if "application/json" in resp.headers.get("content-type", ""):
                rows = resp.json()
                if isinstance(rows, list):
                    class_rows += rows
    if not class_rows:
        log.warning("[目录] pe 目录为空(该轮次可能未开放)")
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
    config.replace_atomic(tmp, config.CATALOG_FILE)


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


def run(
    session: requests.Session,
    skip_catalog: bool = False,
    with_capacity: bool = False,
) -> dict:
    """执行初始化流程,返回 catalog dict(同时写盘)。每步失败不阻断后续。"""
    page_params: dict[str, str] = {}
    xsxx: dict = {}
    term_hint: tuple[str, str] | None = None
    try:
        idx_hidden, _tabs = zzxk.fetch_index(session)
        term_hint = (idx_hidden.get("xkxnm", ""), idx_hidden.get("xkxqm", ""))
    except Exception as e:
        log.debug("zzxk 首页学期提示获取失败: %s", e)
    try:
        page_params = fetch_page_params(session)
    except Exception as e:
        log.warning("选课首页参数抓取失败(不阻断): %s", e)
    try:
        xsxx = fetch_xsxx(session, term=term_hint)
    except Exception as e:
        log.warning("个人课表信息抓取失败(不阻断): %s", e)
    if page_params or xsxx:
        apply_user_info(page_params, xsxx)
    else:
        log.warning("未获取到任何用户信息,user_settings.json 保持不变")

    courses: list[dict] = []
    if not skip_catalog:
        try:
            courses += fetch_zzxk_catalog(session, with_capacity=with_capacity)
        except Exception as e:
            log.warning("zzxk 全量目录抓取失败(不阻断): %s", e)
        try:
            courses += fetch_pe_catalog(session)
        except Exception as e:
            log.warning("体育课目录抓取失败(不阻断): %s", e)

    # 两个轮次的已选课程合并(tjxkbkk 补退选 + zzxkyzb 自主选课),按 jxb_id 去重
    choosed: list[dict] = []
    choosed_ids: set[str] = set()
    for name, fetch in (("tjxkbkk", fetch_choosed),
                        ("zzxk", lambda s: zzxk.fetch_choosed(s))):
        try:
            batch = fetch(session)
            fresh = [
                _slim_class(c, include_availability=False) for c in batch
                if c.get("jxb_id") and c["jxb_id"] not in choosed_ids
            ]
            choosed_ids.update(c["jxb_id"] for c in fresh)
            choosed += fresh
            log.info("[已选] %s 轮次: %d 门", name, len(batch))
        except Exception as e:
            log.warning("%s 已选课程抓取失败(不阻断): %s", name, e)

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


def _load_seat_details() -> dict:
    if not config.SEAT_DETAILS_FILE.exists():
        return {"classes": {}, "errors": {}}
    try:
        data = json.loads(config.SEAT_DETAILS_FILE.read_text("utf-8"))
        if isinstance(data, dict):
            data.setdefault("classes", {})
            data.setdefault("errors", {})
            return data
    except Exception:
        pass
    return {"classes": {}, "errors": {}}


def _save_seat_details(details: dict) -> None:
    tmp = config.SEAT_DETAILS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(details, ensure_ascii=False, indent=2), "utf-8")
    config.replace_atomic(tmp, config.SEAT_DETAILS_FILE)


def refresh_seat_details(
    session: requests.Session, jxb_ids: list[str]
) -> dict:
    """Refresh plan-only seat counts without touching monitor state."""
    catalog = json.loads(config.CATALOG_FILE.read_text("utf-8"))
    wanted = set(jxb_ids)
    courses_by_endpoint: dict[str, list[dict]] = {}
    for course in catalog.get("courses", []):
        if any(c.get("jxb_id") in wanted for c in course.get("classes", [])):
            courses_by_endpoint.setdefault(course.get("endpoint", ""), []).append(course)

    fetched: dict[str, dict] = {}
    errors: dict[str, str] = {}
    zzxk_courses = {
        course.get("kch") or course["kch_id"]: {
            "endpoint": "zzxk",
            "kch_id": course["kch_id"],
            "kklxdm": str(course.get("kklxdm") or ""),
        }
        for course in courses_by_endpoint.get("zzxk", [])
    }
    if zzxk_courses:
        try:
            fetched.update(zzxk.fetch_seats(session, zzxk_courses))
        except Exception as exc:
            for course in courses_by_endpoint.get("zzxk", []):
                for cls in course.get("classes", []):
                    if cls.get("jxb_id") in wanted:
                        errors[cls["jxb_id"]] = str(exc)

    for course in courses_by_endpoint.get("pe", []):
        payload = {**config.query_common("pe"), "kch_id": course.get("kch_id", "")}
        try:
            response = session.post(
                config.JXB_LIST_URL,
                data=payload,
                headers=_HEADERS,
                timeout=20,
                allow_redirects=False,
            )
            if "application/json" not in response.headers.get("content-type", ""):
                raise RuntimeError(f"体育课详情非 JSON: status={response.status_code}")
            rows = response.json()
            if isinstance(rows, list):
                fetched.update(
                    {row["jxb_id"]: row for row in rows if row.get("jxb_id")}
                )
        except Exception as exc:
            for cls in course.get("classes", []):
                if cls.get("jxb_id") in wanted:
                    errors[cls["jxb_id"]] = str(exc)

    details = _load_seat_details()
    now = datetime.now().isoformat(timespec="seconds")
    for jxb_id in wanted:
        row = fetched.get(jxb_id)
        if row:
            details["classes"][jxb_id] = {
                "jxbxzrs": row.get("jxbxzrs", row.get("yxzrs")),
                "jxbrl": row.get("jxbrl"),
                # sksj(上课时间) 用于 GUI 跨方案组时间冲突校验;pe 课程原始响应自带,
                # zzxk 课程由 zzxk.fetch_seats 从教学班详情缓存回填。
                "sksj": row.get("sksj"),
                "jxdd": row.get("jxdd"),
                "jsxx": row.get("jsxx"),
                "availability": availability_from_row(row),
                "updated_at": now,
            }
            details["errors"].pop(jxb_id, None)
        else:
            details["errors"][jxb_id] = errors.get(jxb_id, "接口未返回该教学班")
    details["updated_at"] = now
    _save_seat_details(details)
    return details


def _load_ratings() -> dict:
    if not config.RATINGS_FILE.exists():
        return {"courses": {}, "errors": {}}
    try:
        data = json.loads(config.RATINGS_FILE.read_text("utf-8"))
        if isinstance(data, dict):
            data.setdefault("courses", {})
            data.setdefault("errors", {})
            return data
    except Exception:
        pass
    return {"courses": {}, "errors": {}}


def _save_ratings(ratings: dict) -> None:
    tmp = config.RATINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ratings, ensure_ascii=False, indent=2), "utf-8")
    config.replace_atomic(tmp, config.RATINGS_FILE)


def _first_teacher_name(course: dict) -> str | None:
    """从该课第一个教学班的 jsxx("工号/姓名/职称;...")里取第一位老师姓名,当消歧提示用。"""
    classes = course.get("classes") or []
    if not classes:
        return None
    jsxx = classes[0].get("jsxx") or ""
    first = jsxx.split(";", 1)[0]
    parts = first.split("/")
    return parts[1] if len(parts) >= 2 else None


def refresh_ratings(
    courses: list[dict] | None = None, kch_list: list[str] | None = None
) -> dict:
    """按 kch 批量拉 course.sjtu.plus 评分,写入 ratings.json。

    courses=None 时从 catalog.json 读;kch_list=None 时刷新全部,否则只刷新给定课程代码。
    course.sjtu.plus 与 i.sjtu.edu.cn 是完全独立的站点,用自己的 requests.Session 登录,
    不复用 jaccount 的 session。缺凭据/登录失败均只记警告,返回现有缓存,不阻断调用方。
    """
    ratings = _load_ratings()
    if not config.COURSE_PLUS_PASSWORD and not config.COURSE_PLUS_EMAIL:
        log.warning("[评分] COURSE_PLUS_PASSWORD 未配置,跳过评分抓取")
        return ratings

    if courses is None:
        catalog = json.loads(config.CATALOG_FILE.read_text("utf-8"))
        courses = catalog.get("courses", [])
    if kch_list is not None:
        wanted = set(kch_list)
        courses = [c for c in courses if c.get("kch") in wanted]

    cp_session = requests.Session()
    try:
        course_plus.login(cp_session)
    except course_plus.LoginError as exc:
        log.warning("[评分] course.sjtu.plus 登录失败,跳过: %s", exc)
        return ratings

    now = datetime.now().isoformat(timespec="seconds")
    ok = 0
    for course in courses:
        kch = course.get("kch")
        if not kch:
            continue
        teacher_hint = _first_teacher_name(course)
        try:
            result = course_plus.get_rating_by_code(cp_session, kch, teacher_hint)
        except Exception as exc:
            # reason 区分"确实抓取失败,不代表没有评分"(fetch_failed) 与
            # "course.sjtu.plus 没有这门课"(not_found,和"有课但 0 条评价"一样都是
            # 合法的"无评分"状态) —— GUI 靠 reason 展示不同文案,而不是猜错误文本。
            ratings["errors"][kch] = {"reason": "fetch_failed", "message": str(exc)}
            continue
        if result:
            ratings["courses"][kch] = {**result, "updated_at": now}
            ratings["errors"].pop(kch, None)
            ok += 1
        else:
            ratings["errors"][kch] = {
                "reason": "not_found",
                "message": "course.sjtu.plus 未收录该课程",
            }

    ratings["updated_at"] = now
    _save_ratings(ratings)
    log.info("[评分] 刷新完成: %d 门成功, %d 门失败/未收录", ok, len(ratings["errors"]))
    return ratings


def main():
    ap = argparse.ArgumentParser(description="自动抓取用户信息与课程目录")
    ap.add_argument("--debug", action="store_true", help="DEBUG 日志")
    ap.add_argument("--skip-catalog", action="store_true",
                    help="只抓用户信息和已选,不抓课程目录")
    ap.add_argument("--with-capacity", action="store_true",
                    help="目录附带容量/教师/时间(逐课多查一次 JxbWithKch,慢)")
    ap.add_argument("--seat-details", nargs="+", metavar="JXB_ID",
                    help="仅刷新方案内教学班人数/容量缓存")
    ap.add_argument("--with-ratings", action="store_true",
                    help="抓课程目录时顺带拉取 course.sjtu.plus 评分(逐课查询,较慢)")
    ap.add_argument("--fetch-ratings-all", action="store_true",
                    help="仅刷新 catalog.json 全部课程的 course.sjtu.plus 评分缓存")
    ap.add_argument("--fetch-ratings", nargs="+", metavar="KCH",
                    help="仅刷新指定课程代码的 course.sjtu.plus 评分缓存")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    if args.fetch_ratings_all or args.fetch_ratings:
        # 评分是 course.sjtu.plus 独立站点,不需要 jaccount session
        result = refresh_ratings(
            kch_list=None if args.fetch_ratings_all else args.fetch_ratings
        )
        print(f"评分刷新: {len(result.get('courses', {}))} 门成功, "
              f"{len(result.get('errors', {}))} 门失败/未收录")
        return

    session = requests.Session()
    login(session)
    if args.seat_details:
        details = refresh_seat_details(session, args.seat_details)
        failed = [jxb_id for jxb_id in args.seat_details
                  if jxb_id in details.get("errors", {})]
        print(f"详情刷新: {len(args.seat_details) - len(failed)} 成功, {len(failed)} 失败")
        return
    catalog = run(session, skip_catalog=args.skip_catalog,
                  with_capacity=args.with_capacity)
    if args.with_ratings and catalog.get("courses"):
        refresh_ratings(courses=catalog["courses"])

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
