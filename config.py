"""配置:从 .env 读取凭据,固定参数从 HAR 提取。"""
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
# 体育课用的 bklx_id (新 HAR 中 PE003C20 查询用的值)
PE_BKLX_ID = "84A6A72B2E885480E0530200A8C00319"
PAGE_URL = "https://i.sjtu.edu.cn/xsxk/tjxkbkk_cxTjxkBkkIndex.html?gnmkdm=N253519&layout=default"
REFERER = PAGE_URL
INDEX_URL = "https://i.sjtu.edu.cn/"

# 普通课公共参数 (HAR 提取,zyh_id 是该学生的专业 UUID)
_DISPLAY_COMMON = {
    "zyh_id": "16FBC4936CF888F8E065F8163EE1DCCC",
    "njdm_id": "2025", "xkxnm": "2026", "xqh_id": "02", "jg_id": "01000",
    "xbm": "1", "xslbdm": "111", "mzm": "", "ccdm": "w", "xz": "4",
    "rlkz": "0", "cdrlkz": "0", "rlzlkz": "1", "xkxqm": "3", "xsbj": "524288",
    "zyfx_id": "wfx", "bh_id": "wbj", "xkly": "1",
    "sfkgbcx": "1", "sfrxtgkcxd": "1", "tykczgxdcs": "5",
    "kklxdm": "01", "bklx_id": "0",
}
# 体育课公共参数 (新 HAR 提取)
_PE_COMMON = {
    "cxbj": "0", "rlkz": "0", "cdrlkz": "0", "rlzlkz": "1",
    "njdm_id": "2025", "zyh_id": "16FBC4936CF888F8E065F8163EE1DCCC",
    "zyfx_id": "wfx", "kklxdm": "06", "bh_id": "wbj",
    "xkxnm": "2026", "xkxqm": "3", "xkly": "1", "xqh_id": "02",
    "sfrxtgkcxd": "1", "tykczgxdcs": "5", "sfkgbcx": "1",
    "bklx_id": "84A6A72B2E885480E0530200A8C00319",
}

# 每门要监控的课的查询模板。endpoint=display/pe 决定走哪个接口、怎么解析响应。
KCH_QUERIES = {
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
PRIORITY_GROUPS: dict[str, dict] = {
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


def build_query_payload(kch: str) -> tuple[str, dict] | None:
    """返回 (url, post_data) 用于查询该课程的全部教学班。"""
    q = KCH_QUERIES.get(kch)
    if not q:
        return None
    if q["endpoint"] == "display":
        return DISPLAY_URL, {**_DISPLAY_COMMON, "kch_id": q["kch_id"], "jxb_id": q["jxb_id"]}
    if q["endpoint"] == "pe":
        return JXB_LIST_URL, {**_PE_COMMON, "kch_id": q["kch_id"]}
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

# === 自动 swap 配置 ===
# AUTO_SWAP=False:仅告警,不动手。默认。
# AUTO_SWAP=True:满足条件时自动执行"先退后选"。
# AUTO_SWAP_DRY_RUN=True:即使 AUTO_SWAP=True,所有 swap 调用走 dry-run(只打印不发)。
#   生产前建议先 AUTO_SWAP=True + DRY_RUN=True 跑一阵,验证触发逻辑无误。
AUTO_SWAP = False
AUTO_SWAP_DRY_RUN = False

# 自动换班的目标和要退的班均由 PRIORITY_GROUPS 动态决定,无需另配映射。
