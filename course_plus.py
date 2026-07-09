"""course.sjtu.plus(选课社区)登录 + 按课程编号查评分。

与 jaccount 完全无关联的独立站点账号体系(邮箱+密码),抓包(2026-07-08 HAR)验证:
  - 未登录访问任何 /api/ 接口一律 401。
  - 登录流程: GET /api/auth/csrf 拿 x-csrf-token 响应头,
    POST /api/auth/login 带该 token 提交 {email, password}。
  - 登录后是标准会话态,requests.Session 自动接管 cookie,无需手工解析。
  - 评分内嵌在 /api/course/{id} 里: rating.{count, avg, score, distribution}。
  - 同课程代码可能对应多个老师/学期的班级(same_code_courses),需按老师名消歧。

对外主要接口:
  login(session)                              → 登录,幂等
  search_course_by_code(session, code)        → 按课程代码精确匹配候选列表
  get_course_detail(session, course_id)       → 单课详情(含 rating)
  get_rating_by_code(session, code, teacher)  → 便捷入口,返回精简评分字典
"""
from __future__ import annotations

import logging
import sys

import requests

import config

log = logging.getLogger(__name__)

BASE = "https://course.sjtu.plus/api"
CSRF_URL = f"{BASE}/auth/csrf"
LOGIN_URL = f"{BASE}/auth/login"
API_KEY_PROBE_URL = f"{BASE}/api-key/"
COURSE_LIST_URL = f"{BASE}/course/"
COURSE_DETAIL_URL = f"{BASE}/course/{{id}}"


class LoginError(RuntimeError):
    pass


def _resolve_email() -> str:
    """course.sjtu.plus 邮箱: 优先用 .env 里显式配置的 COURSE_PLUS_EMAIL(手工覆盖用);

    否则回退为 jaccount 用户名同前缀的 @sjtu.edu.cn 邮箱 —— 该站账号邮箱与
    jaccount 用户名相同前缀这件事已由用户实测确认,GUI 因此不再单独收用户名。
    """
    if config.COURSE_PLUS_EMAIL:
        return config.COURSE_PLUS_EMAIL
    if config.JACCOUNT_USER:
        return f"{config.JACCOUNT_USER}@sjtu.edu.cn"
    return ""


def _is_logged_in(session: requests.Session) -> bool:
    try:
        r = session.get(
            API_KEY_PROBE_URL,
            headers={"User-Agent": config.USER_AGENT},
            timeout=10,
        )
        return r.status_code == 200
    except requests.RequestException:
        return False


def login(session: requests.Session) -> None:
    email = _resolve_email()
    if not email or not config.COURSE_PLUS_PASSWORD:
        raise LoginError(
            "course.sjtu.plus 邮箱/密码未配置: 需要 JACCOUNT_USER(或 COURSE_PLUS_EMAIL) "
            "+ COURSE_PLUS_PASSWORD"
        )

    session.headers.setdefault("User-Agent", config.USER_AGENT)

    if _is_logged_in(session):
        log.info("session 仍有效,跳过登录")
        return

    csrf_resp = session.get(CSRF_URL, timeout=10)
    token = csrf_resp.headers.get("x-csrf-token")
    if not token:
        raise LoginError(f"未拿到 csrf token(HTTP {csrf_resp.status_code})")

    login_resp = session.post(
        LOGIN_URL,
        json={"email": email, "password": config.COURSE_PLUS_PASSWORD},
        headers={"x-csrf-token": token},
        timeout=10,
    )
    if login_resp.status_code != 200:
        raise LoginError(
            f"登录失败: HTTP {login_resp.status_code} {login_resp.text[:200]!r}"
        )

    if not _is_logged_in(session):
        raise LoginError("登录请求返回 200 但会话探针仍未通过,请检查响应体")

    log.info("course.sjtu.plus 登录成功")


def search_course_by_code(session: requests.Session, code: str) -> list[dict]:
    """按课程代码在课程列表接口里精确匹配(q 可能模糊匹配到课程名,需要过滤)。"""
    r = session.get(
        COURSE_LIST_URL,
        params={"q": code, "page_size": 20},
        timeout=10,
    )
    r.raise_for_status()
    items = r.json().get("items", [])
    return [item for item in items if item.get("code") == code]


def get_course_detail(session: requests.Session, course_id: int) -> dict:
    r = session.get(COURSE_DETAIL_URL.format(id=course_id), timeout=10)
    r.raise_for_status()
    return r.json()


def _to_rating_dict(entry: dict) -> dict:
    return {
        "code": entry.get("code"),
        "teacher": (entry.get("main_teacher") or {}).get("name"),
        "semester": entry.get("last_semester"),
        "rating": entry.get("rating"),
    }


def get_rating_by_code(
    session: requests.Session, code: str, teacher_name: str | None = None
) -> dict | None:
    """按课程代码查评分,可选按老师姓名消歧多个候选班级。"""
    candidates = search_course_by_code(session, code)
    if not candidates:
        return None

    if teacher_name:
        for c in candidates:
            if (c.get("main_teacher") or {}).get("name") == teacher_name:
                return _to_rating_dict(c)

    if len(candidates) > 1:
        candidates = sorted(
            candidates, key=lambda c: c.get("last_semester") or "", reverse=True
        )

    return _to_rating_dict(candidates[0])


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    if len(sys.argv) < 2:
        print(f"用法: python {sys.argv[0]} <课程代码> [老师姓名]")
        sys.exit(1)

    kch = sys.argv[1]
    teacher = sys.argv[2] if len(sys.argv) > 2 else None

    s = requests.Session()
    login(s)
    result = get_rating_by_code(s, kch, teacher)
    print(result if result is not None else f"未找到课程代码 {kch} 的评分数据")
