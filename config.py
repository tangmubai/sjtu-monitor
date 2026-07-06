"""配置:从 .env 读取凭据,固定参数从 HAR 提取。"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

JACCOUNT_USER = os.getenv("JACCOUNT_USER", "")
JACCOUNT_PASS = os.getenv("JACCOUNT_PASS", "")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
MAIL_FROM = os.getenv("MAIL_FROM", SMTP_USER)
MAIL_TO = os.getenv("MAIL_TO", SMTP_USER)

POLL_MIN = int(os.getenv("POLL_MIN", "60"))
POLL_MAX = int(os.getenv("POLL_MAX", "120"))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0"
)

# === 选课数据接口 ===
# 普通课(主修/选修): cxTjxkBkkDisplay,返回 {tmpList: [[summary], [classes]]}
# 体育课:             cxJxbTjxkBkk,返回 list[class]
# 两个接口参数差别比较大,所以为每门课各自存一份完整查询模板。
DISPLAY_URL = "https://i.sjtu.edu.cn/xsxk/tjxkbkk_cxTjxkBkkDisplay.html?gnmkdm=N253519"
JXB_LIST_URL = "https://i.sjtu.edu.cn/xsxk/tjxkbkk_cxJxbTjxkBkk.html?gnmkdm=N253519"
# 退选 / 选课 接口 (从新 HAR 提取)
DROP_URL = "https://i.sjtu.edu.cn/xsxk/tjxkbkk_tuikBcTjxkBkk.html?gnmkdm=N253519"
SELECT_URL = "https://i.sjtu.edu.cn/xsxk/tjxkbkk_xkBcZyTjxkBkk.html?gnmkdm=N253519"
# 已选课程列表 (test_choosed.py 已验证)
CHOOSED_URL = "https://i.sjtu.edu.cn/xsxk/tjxkbkk_cxTjxkBkkChoosedCourse.html?gnmkdm=N253519"
# 个人课表接口,响应里的 xsxx 块含 ZYH_ID/NJDM_ID/姓名/学号 (HAR 抓包验证)
XSGRKB_URL = "https://i.sjtu.edu.cn/kbcx/xskbcx_cxXsgrkb.html?gnmkdm=N253508"
# 体育课用的 bklx_id (新 HAR 中 PE003C20 查询用的值)
PE_BKLX_ID = "84A6A72B2E885480E0530200A8C00319"
PAGE_URL = "https://i.sjtu.edu.cn/xsxk/tjxkbkk_cxTjxkBkkIndex.html?gnmkdm=N253519&layout=default"
REFERER = PAGE_URL
INDEX_URL = "https://i.sjtu.edu.cn/"

# 普通课公共参数 (HAR 提取,zyh_id 是该学生的专业 UUID)。
# 其中因人而异的字段 (zyh_id/njdm_id/xbm 等) 可在 user_settings.json 的
# query_overrides.display 分区覆盖,无需改代码;xkxnm/xkxqm 由 term 分区统一决定。
_DISPLAY_COMMON = {
    "zyh_id": "16FBC4936CF888F8E065F8163EE1DCCC",
    "njdm_id": "2025", "xkxnm": "2026", "xqh_id": "02", "jg_id": "01000",
    "xbm": "1", "xslbdm": "111", "mzm": "", "ccdm": "w", "xz": "4",
    "rlkz": "0", "cdrlkz": "0", "rlzlkz": "1", "xkxqm": "3", "xsbj": "524288",
    "zyfx_id": "wfx", "bh_id": "wbj", "xkly": "1",
    "sfkgbcx": "1", "sfrxtgkcxd": "1", "tykczgxdcs": "5",
    "kklxdm": "01", "bklx_id": "0",
}
# 体育课公共参数 (新 HAR 提取)。个人字段可在 query_overrides.pe 分区覆盖。
_PE_COMMON = {
    "cxbj": "0", "rlkz": "0", "cdrlkz": "0", "rlzlkz": "1",
    "njdm_id": "2025", "zyh_id": "16FBC4936CF888F8E065F8163EE1DCCC",
    "zyfx_id": "wfx", "kklxdm": "06", "bh_id": "wbj",
    "xkxnm": "2026", "xkxqm": "3", "xkly": "1", "xqh_id": "02",
    "sfrxtgkcxd": "1", "tykczgxdcs": "5", "sfkgbcx": "1",
    "bklx_id": "84A6A72B2E885480E0530200A8C00319",
}

# 每门要监控的课的查询模板(内置默认)。endpoint=display/pe 决定走哪个接口、怎么解析响应。
# 可在 user_settings.json 的 courses 分区整体覆盖(格式相同),换用户/换学期无需改代码。
_DEFAULT_KCH_QUERIES = {
    "PHY1262":  {"endpoint": "display", "kch_id": "36E21CBEA9CF4F0DE065F8163EE1DCCC",
                 "jxb_id": "506FCEEDEC9878EFE065F8163EE1DCCC"},
    "MATH1206": {"endpoint": "display", "kch_id": "MA1206",
                 "jxb_id": "5081099FD76E19BDE065F8163EE1DCCC"},
    "CS0501":   {"endpoint": "display", "kch_id": "CS0501",
                 "jxb_id": "509A3E12116A05E1E065F8163EE1DCCC"},
    "CS0502":   {"endpoint": "display", "kch_id": "AE708F889EEC0D68E055F8163ED16360",
                 "jxb_id": "509A3E12128805E1E065F8163EE1DCCC"},
    "PE003C20": {"endpoint": "pe",      "kch_id": "PE003C20"},
}

# === 优先级组 ===
# 每组是一个有序优先级列表:首位 = 最想要;末位 = 当前已选(初始 held)。
# 监控范围 = 每组中比 held 更高优先级的全部 jxb_id;swap 只升不降。
# is_pe 控制 select 时是否走体育课的 bklx_id。
#
# 代码里的字典是内置初始基线。用户可在 GUI("选课设置" 标签页)里挑选要监控的
# 教学班并调整优先级,保存后写入 user_settings.json 的 priority_groups 分区。
_DEFAULT_PRIORITY_GROUPS: dict[str, dict] = {
    "PHY": {
        "is_pe": False,
        "priority": [
            "506FCEEDEC9878EFE065F8163EE1DCCC",  # PHY1262-01 (最高优先级)
            "507009BB29018139E065F8163EE1DCCC",  # PHY1262-06
            "507009BB290B8139E065F8163EE1DCCC",  # PHY1262-07
            "506F02ACC24350B1E065F8163EE1DCCC",  # PHY1262-03 (当前持有)
        ],
    },
    "CS": {
        "is_pe": False,
        "priority": [
            "509A0CA5A6133983E065F8163EE1DCCC",  # CS0501-06 (最高)
            "509A3E12128805E1E065F8163EE1DCCC",  # CS0502-03
            "509A3E12116A05E1E065F8163EE1DCCC",  # CS0501-05 (当前持有)
        ],
    },
    "MATH": {
        "is_pe": False,
        "priority": [
            "50800417E5FC25C1E065F8163EE1DCCC",  # MATH1206-07 (最高)
            "50805BDD232B2EF6E065F8163EE1DCCC",  # MATH1206-06
            "5080E28D07CD12CFE065F8163EE1DCCC",  # 当前持有
        ],
    },
    "PE": {
        "is_pe": True,
        "priority": [
            "52B1F0425B7E82C2E065F8163EE1DCCC",  # PE003C20-05 (最高)
            "5290D7F41D3F5F1AE065F8163EE1DCCC",  # 当前持有
        ],
    },
}


# === 用户设置(user_settings.json)===
# 所有"因人而异 / 运行期可改"的配置统一放在这一个 JSON 文件里,按分区组织:
#   term            选课学年 xkxnm / 学期 xkxqm
#   query_overrides 查询接口的个人参数覆盖(如 zyh_id、njdm_id),display/pe 分开
#   courses         监控哪些课(即 KCH_QUERIES,整体覆盖)
#   auto_swap       自动换课开关 {enabled, dry_run}
#   priority_groups 优先级组(GUI"选课设置"页编辑,整体覆盖)
# 文件里只需写想覆盖的分区,缺省自动回落到代码内置默认值。
USER_SETTINGS_FILE = ROOT / "user_settings.json"
# 旧版单独存优先级组的文件,存在且新文件缺失时自动迁移读取
_LEGACY_PRIORITY_FILE = ROOT / "priority_groups.json"

_DEFAULT_SETTINGS = {
    "term": {"xkxnm": "2026", "xkxqm": "3"},
    "query_overrides": {"display": {}, "pe": {}},
    "courses": _DEFAULT_KCH_QUERIES,
    "auto_swap": {"enabled": False, "dry_run": False},
    "notifications": {"email_enabled": True},
    "priority_groups": _DEFAULT_PRIORITY_GROUPS,
}


def _deep_copy(value):
    return json.loads(json.dumps(value))


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def default_settings() -> dict:
    return _deep_copy(_DEFAULT_SETTINGS)


def default_priority_groups() -> dict[str, dict]:
    return _deep_copy(_DEFAULT_PRIORITY_GROUPS)


def load_user_settings() -> dict:
    """加载用户设置。文件缺失/损坏时对应分区回落到内置默认值。"""
    settings = default_settings()
    data: dict = {}
    if USER_SETTINGS_FILE.exists():
        try:
            raw = json.loads(USER_SETTINGS_FILE.read_text("utf-8"))
            if isinstance(raw, dict):
                data = raw
        except Exception:
            pass
    elif _LEGACY_PRIORITY_FILE.exists():
        try:
            groups = json.loads(_LEGACY_PRIORITY_FILE.read_text("utf-8"))
            if isinstance(groups, dict) and groups:
                data = {"priority_groups": groups}
        except Exception:
            pass
    # 小字典分区:逐键合并,允许只覆盖个别键
    for key in ("term", "query_overrides", "auto_swap", "notifications"):
        if isinstance(data.get(key), dict):
            settings[key] = _deep_merge(settings[key], data[key])
    # 整体替换分区:用户删掉的课/组不应被默认值"复活"。
    # 显式写了该分区(即使是空 dict)就以文件为准;只有缺失/类型错误才回落默认。
    for key in ("courses", "priority_groups"):
        if isinstance(data.get(key), dict):
            settings[key] = data[key]
    return settings


def load_priority_groups() -> dict[str, dict]:
    """重读用户设置里的优先级组(GUI"重新载入"用)。"""
    return load_user_settings()["priority_groups"]


def save_user_settings(settings: dict) -> None:
    tmp = USER_SETTINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(USER_SETTINGS_FILE)


def update_user_settings(**sections) -> None:
    """更新并保存指定分区,同时同步模块级变量(对本进程立即生效)。

    例: update_user_settings(priority_groups={...}, auto_swap={"enabled": True, "dry_run": True})
    """
    for key, value in sections.items():
        USER_SETTINGS[key] = value
    save_user_settings(USER_SETTINGS)
    _apply_settings(USER_SETTINGS)


def _apply_settings(settings: dict) -> None:
    global XKXNM, XKXQM, QUERY_OVERRIDES, KCH_QUERIES
    global PRIORITY_GROUPS, AUTO_SWAP, AUTO_SWAP_DRY_RUN
    global EMAIL_ENABLED
    XKXNM = settings["term"]["xkxnm"]
    XKXQM = settings["term"]["xkxqm"]
    QUERY_OVERRIDES = settings["query_overrides"]
    KCH_QUERIES = settings["courses"]
    PRIORITY_GROUPS = settings["priority_groups"]
    AUTO_SWAP = bool(settings["auto_swap"]["enabled"])
    AUTO_SWAP_DRY_RUN = bool(settings["auto_swap"]["dry_run"])
    EMAIL_ENABLED = bool(settings["notifications"]["email_enabled"])


USER_SETTINGS = load_user_settings()
_apply_settings(USER_SETTINGS)


def initial_held() -> dict[str, str]:
    """初始 held = 每组优先级列表的最后一项。"""
    return {g: cfg["priority"][-1] for g, cfg in PRIORITY_GROUPS.items() if cfg["priority"]}


def watched_ids(held: dict[str, str]) -> set[str]:
    """计算当前监控集合:每组中,比 held 优先级更高的 jxb_id。

    如果某组没有持有 (held 为空) → 监控整组。
    如果 held 已是该组最高优先级 → 不再监控该组。
    """
    out: set[str] = set()
    for group, cfg in PRIORITY_GROUPS.items():
        ids = cfg["priority"]
        h = held.get(group)
        if h is None or h not in ids:
            out.update(ids)
            continue
        idx = ids.index(h)
        out.update(ids[:idx])
    return out


def find_group(jxb_id: str) -> str | None:
    """该 jxb_id 属于哪个优先级组。"""
    for g, cfg in PRIORITY_GROUPS.items():
        if jxb_id in cfg["priority"]:
            return g
    return None


def query_common(endpoint: str) -> dict:
    """某接口的完整公共参数(不含课程自身参数)。

    参数优先级: 内置公共参数 < term 学年学期 < query_overrides 个人覆盖。
    """
    term = {"xkxnm": XKXNM, "xkxqm": XKXQM}
    if endpoint == "display":
        return {**_DISPLAY_COMMON, **term, **QUERY_OVERRIDES.get("display", {})}
    if endpoint == "pe":
        return {**_PE_COMMON, **term, **QUERY_OVERRIDES.get("pe", {})}
    raise ValueError(f"未知 endpoint: {endpoint}")


def build_query_payload(kch: str) -> tuple[str, dict] | None:
    """返回 (url, post_data) 用于查询该课程的全部教学班。"""
    q = KCH_QUERIES.get(kch)
    if not q:
        return None
    if q["endpoint"] == "display":
        return DISPLAY_URL, {
            **query_common("display"), "kch_id": q["kch_id"], "jxb_id": q["jxb_id"],
        }
    if q["endpoint"] == "pe":
        return JXB_LIST_URL, {**query_common("pe"), "kch_id": q["kch_id"]}
    return None


def parse_class_list(endpoint: str, data) -> list[dict]:
    """根据接口类型从响应中抽出教学班数组。"""
    if endpoint == "display":
        if isinstance(data, dict) and "tmpList" in data and len(data["tmpList"]) > 1:
            return data["tmpList"][1] or []
        return []
    if endpoint == "pe":
        return data if isinstance(data, list) else []
    return []

# jaccount CAS 入口 — i.sjtu.edu.cn 走这个 URL 才会触发 jaccount 重定向
# (默认 i.sjtu.edu.cn/ 落到本地 zfsoft 登录页,不是 jaccount)
JACCOUNT_ENTRY_URL = "https://i.sjtu.edu.cn/jaccountlogin"
JACCOUNT_CAPTCHA_URL = "https://jaccount.sjtu.edu.cn/jaccount/captcha"
JACCOUNT_ULOGIN_URL = "https://jaccount.sjtu.edu.cn/jaccount/ulogin"

STATE_FILE = ROOT / "state.json"
LOG_FILE = ROOT / "changes.log"
CAPTCHA_DEBUG_DIR = ROOT / "captcha_debug"
SWAP_STATE_FILE = ROOT / "swap_state.json"
# bootstrap.py 抓取的课程目录(全部可选课程+教学班+当前已选),GUI"选课设置"页读取
CATALOG_FILE = ROOT / "catalog.json"
# zzxkyzb 监控用的教学班容量缓存(容量基本不变,PartDisplay 不返回,JxbWithKch 查一次后缓存)
ZZXK_CAPACITY_FILE = ROOT / "zzxk_capacity.json"
SEAT_DETAILS_FILE = ROOT / "seat_details.json"

# 课程分类代码 → 名称(zzxkyzb 首页页签实测 + 体育课 06)
KKLX_NAMES = {
    "01": "主修", "10": "通识", "11": "公选",
    "30": "任选", "69": "交叉", "06": "体育",
}

# === 自动 swap 配置 ===
# AUTO_SWAP / AUTO_SWAP_DRY_RUN 由 user_settings.json 的 auto_swap 分区决定
# (见上方 _apply_settings),GUI"优先级与 swap"页可直接开关。
# enabled=False:仅告警,不动手(默认)。enabled=True:满足条件时自动"先退后选"。
# dry_run=True:即使 enabled,所有 swap 调用只打印不发。
#   生产前建议先 enabled=True + dry_run=True 跑一阵,验证触发逻辑无误。
# 自动换班的目标和要退的班均由 PRIORITY_GROUPS 动态决定,无需另配映射。


def _quote_env_value(value: str) -> str:
    """Quote an environment value without exposing it to variable expansion."""
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def save_env_settings(values: dict[str, str], path: Path | None = None) -> None:
    """Atomically update selected .env keys while preserving unrelated content."""
    global JACCOUNT_USER, JACCOUNT_PASS
    global SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_FROM, MAIL_TO
    global POLL_MIN, POLL_MAX

    env_path = path or ROOT / ".env"
    lines = (
        env_path.read_text("utf-8").splitlines()
        if env_path.exists()
        else []
    )
    pending = {str(key): str(value) for key, value in values.items()}
    output: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in pending:
            output.append(f"{key}={_quote_env_value(pending.pop(key))}")
        else:
            output.append(line)
    if pending and output and output[-1] != "":
        output.append("")
    output.extend(f"{key}={_quote_env_value(value)}" for key, value in pending.items())
    tmp = env_path.with_suffix(env_path.suffix + ".tmp")
    tmp.write_text("\n".join(output) + "\n", "utf-8")
    tmp.replace(env_path)

    for key, value in values.items():
        os.environ[key] = str(value)
    JACCOUNT_USER = str(values.get("JACCOUNT_USER", JACCOUNT_USER))
    JACCOUNT_PASS = str(values.get("JACCOUNT_PASS", JACCOUNT_PASS))
    SMTP_HOST = str(values.get("SMTP_HOST", SMTP_HOST))
    SMTP_PORT = int(values.get("SMTP_PORT", SMTP_PORT))
    SMTP_USER = str(values.get("SMTP_USER", SMTP_USER))
    SMTP_PASS = str(values.get("SMTP_PASS", SMTP_PASS))
    MAIL_FROM = str(values.get("MAIL_FROM", MAIL_FROM))
    MAIL_TO = str(values.get("MAIL_TO", MAIL_TO))
    POLL_MIN = int(values.get("POLL_MIN", POLL_MIN))
    POLL_MAX = int(values.get("POLL_MAX", POLL_MAX))
