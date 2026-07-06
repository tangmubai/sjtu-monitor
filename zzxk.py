"""zzxkyzb(自主选课, gnmkdm=N253512)模块接口封装 —— 生产用。

由 test_full_catalog.py / test_zzxk_select_drop.py 的联网实测(2026-07-06)提炼:
  - 首页页签令牌(xkkz_id + 256 位 xkkz_xh)随首页一次抓取即有效;
  - kspage/jspage 为页号范围,递增翻页,空窗口 = 该分类翻完;
  - PartDisplay 每行 = 一个教学班,含 yxzrs(已选人数),无容量;
  - JxbWithKch 每教学班含 jxbrl(容量)与 do_jxb_id(选退课令牌),无已选人数;
  - 两接口 jxb_id 一致,可按 jxb_id join 出"已选/容量"。
  - payload 必须按 _PART_KEYS/_JXB_KEYS 白名单发送 —— 多发参数服务端报"系统运行异常"。

⚠ 与 tjxkbkk(补退选, N253519)是**不同选课轮次**:课程不重叠、id 不通用(联网实测)。
  monitor.py 对 endpoint=="zzxk" 的课程走本模块 fetch_seats;display/pe 课程仍走 tjxkbkk。

对外主要接口:
  fetch_full_catalog(session, ...)  → bootstrap 用,枚举全部分类的课程+教学班
  fetch_seats(session, courses)     → monitor 用,查 watched 课程的已选/容量
  fetch_choosed(session)            → 该轮次当前已选课程列表
低层(测试脚本复用): fetch_index / fetch_display_form / _build_source /
  fetch_part_display / fetch_jxb_capacity / sweep_category
"""
from __future__ import annotations

import json
import logging
import re
import time

import requests

import config

log = logging.getLogger("zzxk")

BASE = "https://i.sjtu.edu.cn"
INDEX_URL = f"{BASE}/xsxk/zzxkyzb_cxZzxkYzbIndex.html?gnmkdm=N253512&layout=default"
DISPLAY_URL = f"{BASE}/xsxk/zzxkyzb_cxZzxkYzbDisplay.html?gnmkdm=N253512"
PART_DISPLAY_URL = f"{BASE}/xsxk/zzxkyzb_cxZzxkYzbPartDisplay.html?gnmkdm=N253512"
JXB_WITH_KCH_URL = f"{BASE}/xsxk/zzxkyzbjk_cxJxbWithKchZzxkYzb.html?gnmkdm=N253512"
CHOOSED_DISPLAY_URL = f"{BASE}/xsxk/zzxkyzb_cxZzxkYzbChoosedDisplay.html?gnmkdm=N253512"
SELECT_URL = f"{BASE}/xsxk/zzxkyzbjk_xkBcZyZzxkYzb.html?gnmkdm=N253512"
DROP_URL = f"{BASE}/xsxk/zzxkyzb_tuikBcZzxkYzb.html?gnmkdm=N253512"

_AJAX_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": INDEX_URL,
    "Origin": BASE,
    "User-Agent": config.USER_AGENT,
}
_PAGE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "User-Agent": config.USER_AGENT,
}

# 浏览器实际发送的精确参数集(HAR_3 逐字段提取+联网实测)。多发参数会导致服务端报错。
# 不含分页(kspage/jspage)和每门课自带的(kch_id/cxbj/fxbj),那些单独补。
_PART_KEYS = (
    "rwlx", "xklc", "xkly", "bklx_id", "sfkkjyxdxnxq", "kzkcgs", "xqh_id", "jg_id",
    "njdm_id_1", "zyh_id_1", "gnjkxdnj", "zyh_id", "zyfx_id", "njdm_id", "bh_id",
    "bjgkczxbbjwcx", "xbm", "xslbdm", "mzm", "xz", "ccdm", "xsbj", "sfkknj",
    "sfkkzy", "kzybkxy", "sfznkx", "zdkxms", "sfkxq", "bhbcyxkjxb", "sfkcfx",
    "kkbk", "kkbkdj", "bklbkcj", "sfkgbcx", "sfrxtgkcxd", "xkkz_xh", "tykczgxdcs",
    "xkxnm", "xkxqm", "kklxdm", "bbhzxjxb", "zxgbxkkg", "xkkz_id", "rlkz", "xkzgbj",
)
_JXB_KEYS = (
    "rwlx", "xkly", "bklx_id", "sfkkjyxdxnxq", "kzkcgs", "xqh_id", "jg_id", "zyh_id",
    "zyfx_id", "txbsfrl", "njdm_id", "bh_id", "xbm", "xslbdm", "mzm", "xz", "ccdm",
    "xsbj", "sfkknj", "gnjkxdnj", "sfkkzy", "kzybkxy", "sfznkx", "zdkxms", "sfkxq",
    "bhbcyxkjxb", "sfkcfx", "bbhzxjxb", "kkbk", "kkbkdj", "bklbkcj", "xkxnm", "xkxqm",
    "xkxskcgskg", "rlkz", "cdrlkz", "cxcykclxxskg", "rlzlkz", "kklxdm", "jxbzcxskg",
    "zxgbxkkg", "xklc", "xkkz_id",
)

# PartDisplay 行瘦身后保留的字段(去掉 queryModel/userModel 等 60+ 冗余键,
# 避免 state.json / catalog.json 膨胀)。cxbj/fxbj/xxkbj 为选课与容量查询所需。
_SEAT_FIELDS = (
    "jxb_id", "jxbmc", "kch", "kch_id", "kcmc", "kklxdm", "kzmc",
    "yxzrs", "jxbxzrs", "jxbrl", "xf", "jxbxf", "cxbj", "fxbj", "xxkbj",
)

# queryCourse(this,'kklxdm','xkkz_id','njdm_id','zyh_id','xkkz_xh') —— 页签里嵌的分类令牌
_TAB_RE = re.compile(
    r"queryCourse\(this,'([^']*)','([^']*)','([^']*)','([^']*)','([^']*)'\)"
)
_TAB_LABEL_RE = re.compile(r'<a id="tab_kklx_[^"]*"[^>]*>([^<]*)</a>')
_HIDDEN_RE = re.compile(r"<input\b[^>]*>", re.I)


class SessionExpired(RuntimeError):
    """session 失效或选课模块不可达(首页非 200 / 无页签)。"""


def _parse_hidden_inputs(html: str) -> dict[str, str]:
    """页面全部 <input type=hidden> 的 id/name → value(首个出现为准)。"""
    out: dict[str, str] = {}
    for tag in _HIDDEN_RE.findall(html):
        if not re.search(r"type\s*=\s*[\"']hidden[\"']", tag, re.I):
            continue
        mk = re.search(r"(?:id|name)\s*=\s*[\"']([^\"']+)[\"']", tag, re.I)
        mv = re.search(r"value\s*=\s*[\"']([^\"']*)[\"']", tag, re.I)
        if mk:
            out.setdefault(mk.group(1), mv.group(1) if mv else "")
    return out


def fetch_index(session: requests.Session) -> tuple[dict, list[dict]]:
    """抓首页,返回 (页面 hidden 字典, 分类页签列表)。"""
    r = session.get(INDEX_URL, headers=_PAGE_HEADERS, timeout=20, allow_redirects=False)
    if r.status_code != 200 or "queryCourse" not in r.text:
        raise SessionExpired(
            f"zzxk 首页异常: status={r.status_code} len={len(r.text)}; "
            "session 失效或该选课模块未开放"
        )
    hidden = _parse_hidden_inputs(r.text)
    labels = _TAB_LABEL_RE.findall(r.text)
    tabs = []
    for i, (kklxdm, xkkz_id, njdm_id, zyh_id, xkkz_xh) in enumerate(_TAB_RE.findall(r.text)):
        tabs.append({
            "kklxdm": kklxdm, "xkkz_id": xkkz_id, "njdm_id": njdm_id,
            "zyh_id": zyh_id, "xkkz_xh": xkkz_xh,
            "label": labels[i] if i < len(labels) else config.KKLX_NAMES.get(kklxdm, "?"),
        })
    log.debug("zzxk 首页: hidden=%d, 页签=%s",
              len(hidden), [(t["kklxdm"], t["label"]) for t in tabs])
    return hidden, tabs


def fetch_display_form(session: requests.Session, tab: dict) -> dict[str, str]:
    """POST Display.html,返回该分类的查询条件 hidden 表单(每类不同)。"""
    payload = {
        "xkkz_id": tab["xkkz_id"], "xszxzt": "1", "kklxdm": tab["kklxdm"],
        "njdm_id": tab["njdm_id"], "zyh_id": tab["zyh_id"],
        "kspage": "0", "jspage": "0",
    }
    r = session.post(DISPLAY_URL, data=payload, headers=_AJAX_HEADERS,
                     timeout=20, allow_redirects=False)
    hidden = _parse_hidden_inputs(r.text)
    if not hidden:
        log.warning("zzxk 分类 %s Display 未解析到 hidden(令牌失效?)", tab["kklxdm"])
    return hidden


def _build_source(index_hidden: dict, display_hidden: dict, tab: dict) -> dict:
    """所有可用参数的来源池: 两份表单并集 + 分类令牌 + jg_id 映射。"""
    source = {**index_hidden, **display_hidden}
    source.update({
        "kklxdm": tab["kklxdm"], "xkkz_id": tab["xkkz_id"],
        "njdm_id": tab["njdm_id"], "zyh_id": tab["zyh_id"],
        "xkkz_xh": tab["xkkz_xh"],
    })
    # 请求里用 jg_id,首页 hidden 里叫 jg_id_1
    if "jg_id" not in source and index_hidden.get("jg_id_1"):
        source["jg_id"] = index_hidden["jg_id_1"]
    return source


def _pick(source: dict, keys: tuple[str, ...], tag: str) -> dict:
    payload = {}
    missing = []
    for k in keys:
        if k in source:
            payload[k] = source[k]
        else:
            missing.append(k)
    if missing:
        log.warning("zzxk %s 缺少参数(来源池没有): %s", tag, missing)
    return payload


def _slim_seat_row(row: dict) -> dict:
    out = {k: row.get(k) for k in _SEAT_FIELDS if row.get(k) is not None}
    # 已选人数字段归一化: zzxkyzb 用 yxzrs,统一补成 tjxkbkk 口径的 jxbxzrs,
    # 让 monitor/GUI 的 _has_spot/_seat_text 两个轮次读法一致。
    if "jxbxzrs" not in out and out.get("yxzrs") is not None:
        out["jxbxzrs"] = out["yxzrs"]
    return out


def fetch_part_display(
    session: requests.Session, source: dict, kspage: int, jspage: int,
) -> list[dict]:
    """抓一个分页窗口的课程列表(每行=一个教学班,含 yxzrs)。"""
    payload = {**_pick(source, _PART_KEYS, "PartDisplay"),
               "kspage": str(kspage), "jspage": str(jspage)}
    r = session.post(PART_DISPLAY_URL, data=payload, headers=_AJAX_HEADERS,
                     timeout=30, allow_redirects=False)
    ct = r.headers.get("content-type", "")
    if "json" not in ct:
        log.warning("zzxk PartDisplay 非 JSON (status=%s): %s",
                    r.status_code, r.text[:120].strip())
        return []
    data = r.json()
    tmp = data.get("tmpList") if isinstance(data, dict) else None
    return tmp if isinstance(tmp, list) else []


def fetch_jxb_capacity(
    session: requests.Session, source: dict, course_row: dict,
) -> list[dict]:
    """查某门课的教学班详情(含 jxbrl 容量、do_jxb_id 选退令牌、教师/时间/地点)。"""
    payload = {
        **_pick(source, _JXB_KEYS, "JxbWithKch"),
        "kch_id": course_row.get("kch_id", ""),
        "cxbj": course_row.get("cxbj", "0"),
        "fxbj": course_row.get("fxbj", "0"),
    }
    r = session.post(JXB_WITH_KCH_URL, data=payload, headers=_AJAX_HEADERS,
                     timeout=20, allow_redirects=False)
    if "json" not in r.headers.get("content-type", ""):
        log.warning("zzxk JxbWithKch 非 JSON (kch=%s): %s",
                    course_row.get("kch"), r.text[:120].strip())
        return []
    data = r.json()
    return data if isinstance(data, list) else []


def sweep_category(
    session: requests.Session, source: dict,
    max_windows: int = 15, jspage: int = 10, sleep: float = 1.0,
) -> list[dict]:
    """翻页扫完一个分类,返回瘦身+归一化后的教学班行(按 jxb_id 去重)。

    空窗口 = 翻完(实测终止信号);跨窗口边界偶有重复行,靠 jxb_id 去重。
    """
    seen: dict[str, dict] = {}
    for w in range(max_windows):
        rows = fetch_part_display(
            session, source, w * jspage + 1, (w + 1) * jspage
        )
        if not rows:
            break
        for row in rows:
            jxb_id = row.get("jxb_id")
            if jxb_id and jxb_id not in seen:
                seen[jxb_id] = _slim_seat_row(row)
        time.sleep(sleep)
    else:
        log.warning("zzxk 分类 %s 达到 max_windows=%d 上限,目录可能不完整",
                    source.get("kklxdm"), max_windows)
    return list(seen.values())


def fetch_full_catalog(
    session: requests.Session, *, with_capacity: bool = False,
    max_windows: int = 15, jspage: int = 10, sleep: float = 1.0,
) -> list[dict]:
    """枚举全部分类的课程目录(bootstrap 用)。

    返回 [{kch, kch_id, kcmc, kklxdm, xf, endpoint:"zzxk", classes:[教学班...]}]。
    with_capacity=True 时逐课调 JxbWithKch 合入 jxbrl/教师/时间/地点(慢,~1s/课)。
    """
    index_hidden, tabs = fetch_index(session)
    time.sleep(sleep)
    courses: dict[str, dict] = {}
    class_ids: set[str] = set()
    for tab in tabs:
        display_hidden = fetch_display_form(session, tab)
        time.sleep(sleep)
        source = _build_source(index_hidden, display_hidden, tab)
        rows = sweep_category(session, source,
                              max_windows=max_windows, jspage=jspage, sleep=sleep)
        # 行内 kklxdm 是课程"性质"(如任选课行里也写 01),不是选课轮次分类;
        # 统一改写成当前页签(分类)代码,GUI 分类筛选与 fetch_seats 才对得上。
        for r in rows:
            r["kklxdm"] = tab["kklxdm"]
        log.info("[zzxk] 分类 %s(%s): %d 个教学班",
                 tab["kklxdm"], tab["label"], len(rows))

        if with_capacity:
            # 按课程各查一次 JxbWithKch,合入容量与教师/时间/地点
            by_kch_id: dict[str, list[dict]] = {}
            for r in rows:
                if r.get("kch_id"):
                    by_kch_id.setdefault(r["kch_id"], []).append(r)
            for kch_id, crows in by_kch_id.items():
                details = fetch_jxb_capacity(session, source, crows[0])
                time.sleep(sleep)
                detail_by_id = {d.get("jxb_id"): d for d in details}
                for r in crows:
                    d = detail_by_id.get(r.get("jxb_id"))
                    if d:
                        for key in ("jxbrl", "jsxx", "sksj", "jxdd"):
                            if d.get(key) is not None:
                                r[key] = d[key]

        for r in rows:
            kch_id, kch = r.get("kch_id"), r.get("kch")
            if not kch_id or not kch:
                continue
            course = courses.setdefault(kch_id, {
                "kch": kch,
                "kch_id": kch_id,
                "kcmc": r.get("kcmc"),
                "kklxdm": r.get("kklxdm") or tab["kklxdm"],
                "xf": r.get("xf") or r.get("jxbxf"),
                "endpoint": "zzxk",
                "classes": [],
            })
            if r["jxb_id"] not in class_ids:
                class_ids.add(r["jxb_id"])
                course["classes"].append(r)
    log.info("[zzxk] 目录合计: %d 门课程 / %d 个教学班", len(courses), len(class_ids))
    return list(courses.values())


# ---- 监控查询(monitor.py 用) ----

# 每个 jxb_id 缓存哪些详情字段(容量基本不变,时间/地点/教师完全不变,只需查一次)
_DETAIL_CACHE_FIELDS = ("jxbrl", "sksj", "jxdd", "jsxx")


def _load_capacity_cache() -> dict[str, dict]:
    """加载教学班详情缓存(容量+时间+地点+教师,按 jxb_id)。

    兼容旧格式(jxb_id -> 容量标量,冲突校验功能上线前的格式):自动升级为
    {"jxbrl": 旧值} 字典,首次命中时会连带补齐 sksj 等字段。
    """
    if not config.ZZXK_CAPACITY_FILE.exists():
        return {}
    try:
        data = json.loads(config.ZZXK_CAPACITY_FILE.read_text("utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        jxb_id: (value if isinstance(value, dict) else {"jxbrl": value})
        for jxb_id, value in data.items()
    }


def _save_capacity_cache(cache: dict) -> None:
    tmp = config.ZZXK_CAPACITY_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(config.ZZXK_CAPACITY_FILE)


def _cache_needs_refresh(jxb_id: str, cache: dict) -> bool:
    """该 jxb_id 是否需要重新查 JxbWithKch —— 缺失,或缺 sksj(旧格式迁移场景)。"""
    cached = cache.get(jxb_id)
    return not cached or cached.get("sksj") is None


def fetch_seats(
    session: requests.Session, courses: dict[str, dict], *,
    max_windows: int = 15, jspage: int = 10, sleep: float = 0.5,
) -> dict[str, dict]:
    """查询若干 zzxk 课程当前的已选/容量(monitor 每轮调用)。

    courses: KCH_QUERIES 风格 {kch: {"endpoint":"zzxk","kch_id":...,"kklxdm":...}}。
    返回 {jxb_id: 教学班行},行含 jxbxzrs(=yxzrs 实时)与 jxbrl(容量,本地缓存,
    仅首次对该课调一次 JxbWithKch —— 容量基本不变,PartDisplay 不返回它)。
    """
    targets: dict[str, set[str]] = {}  # kklxdm -> {kch_id}
    for q in courses.values():
        if q.get("kch_id") and q.get("kklxdm"):
            targets.setdefault(str(q["kklxdm"]), set()).add(q["kch_id"])
        else:
            log.warning("zzxk 课程配置缺 kch_id/kklxdm,跳过: %s", q)
    if not targets:
        return {}

    index_hidden, tabs = fetch_index(session)
    tab_by = {t["kklxdm"]: t for t in tabs}
    cache = _load_capacity_cache()
    cache_dirty = False
    out: dict[str, dict] = {}

    for kklxdm, kch_ids in targets.items():
        tab = tab_by.get(kklxdm)
        if tab is None:
            log.warning("zzxk 首页无分类 %s,其中 %d 门课本轮拉不到", kklxdm, len(kch_ids))
            continue
        time.sleep(sleep)
        display_hidden = fetch_display_form(session, tab)
        source = _build_source(index_hidden, display_hidden, tab)
        time.sleep(sleep)
        rows = sweep_category(session, source,
                              max_windows=max_windows, jspage=jspage, sleep=sleep)
        first_row_by_kch: dict[str, dict] = {}
        for r in rows:
            if r.get("kch_id") in kch_ids and r.get("jxb_id"):
                r["kklxdm"] = kklxdm  # 统一为选课轮次分类(见 fetch_full_catalog 说明)
                out[r["jxb_id"]] = r
                first_row_by_kch.setdefault(r["kch_id"], r)
        # 详情(容量+时间+地点+教师): 该课有教学班缺缓存时查一次 JxbWithKch
        for kch_id, course_row in first_row_by_kch.items():
            need = any(
                r.get("kch_id") == kch_id and _cache_needs_refresh(r["jxb_id"], cache)
                for r in out.values()
            )
            if not need:
                continue
            time.sleep(sleep)
            for d in fetch_jxb_capacity(session, source, course_row):
                jxb_id = d.get("jxb_id")
                if not jxb_id:
                    continue
                entry = {k: d[k] for k in _DETAIL_CACHE_FIELDS if d.get(k) is not None}
                if entry:
                    cache[jxb_id] = entry
                    cache_dirty = True

    for jxb_id, row in out.items():
        cached = cache.get(jxb_id)
        if not cached:
            continue
        for key, value in cached.items():
            if row.get(key) is None and value is not None:
                row[key] = value
    if cache_dirty:
        _save_capacity_cache(cache)
    log.debug("zzxk fetch_seats: %d 个教学班", len(out))
    return out


def fetch_choosed(session: requests.Session, source: dict | None = None) -> list[dict]:
    """该轮次当前已选课程列表(选退课验证脚本已实测)。"""
    if source is None:
        index_hidden, tabs = fetch_index(session)
        if not tabs:
            return []
        time.sleep(0.3)
        source = _build_source(index_hidden, fetch_display_form(session, tabs[0]), tabs[0])
    keys = ("jg_id", "zyh_id", "njdm_id", "zyfx_id", "bh_id", "xz", "ccdm",
            "xqh_id", "xkxnm", "xkxqm", "xkly")
    payload = {k: source.get(k, "") for k in keys}
    r = session.post(CHOOSED_DISPLAY_URL, data=payload, headers=_AJAX_HEADERS,
                     timeout=20, allow_redirects=False)
    if "json" not in r.headers.get("content-type", ""):
        log.warning("zzxk ChoosedDisplay 非 JSON: %s", r.text[:120].strip())
        return []
    data = r.json()
    return data if isinstance(data, list) else []
