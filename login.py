"""jaccount CAS 登录 + ddddocr 验证码识别。

调用 `login(session)` 把 session cookie 装到给定 requests.Session,
失败抛 LoginError。幂等,可在 session 过期后重复调用。

实现要点(2026-06 验证):
- i.sjtu.edu.cn 默认是正方教务的本地登录,要走 /jaccountlogin 才会跳 jaccount。
- jaccount 登录页用 JS 对象 `loginContext = {sid, client, returl, se, v, uuid}`
  存所有参数,不是隐藏 input。
- POST /jaccount/ulogin,字段:sid, client, returl, se, v, uuid, user, pass, captcha。
- 登录成功后会 302 链回 i.sjtu.edu.cn,session cookie 自动落到 jar。
"""
from __future__ import annotations

import logging
import random
import re
import time
from urllib.parse import urljoin

import requests

import config

log = logging.getLogger(__name__)

_ocr = None


def _get_ocr():
    global _ocr
    if _ocr is None:
        import ddddocr
        # beta=True 用更新的模型,对小写字母+数字的纯文本验证码识别率更高
        _ocr = ddddocr.DdddOcr(beta=True, show_ad=False)
    return _ocr


class LoginError(RuntimeError):
    pass


_CTX_KEY_RE = re.compile(
    r'(sid|client|returl|se|v|uuid)\s*:\s*"([^"]*)"'
)


def _parse_login_context(html: str) -> dict[str, str]:
    """从 jaccount 登录页 JS 里抽 loginContext 的 6 个字段。"""
    # 限定在 loginContext = { ... } 块内,避免误抓全页 JS 里同名变量
    m = re.search(r"loginContext\s*=\s*\{([^}]+)\}", html, re.DOTALL)
    block = m.group(1) if m else html
    fields = {k: v for k, v in _CTX_KEY_RE.findall(block)}
    return fields


def _is_logged_in(session: requests.Session) -> bool:
    """探针:任选一门已知课程查询,返回 JSON 即为登录有效。"""
    # 用 KCH_QUERIES 里的第一门课构造一次查询
    kch = next(iter(config.KCH_QUERIES.keys()))
    qp = config.build_query_payload(kch)
    if qp is None:
        return False
    url, payload = qp
    try:
        r = session.post(
            url,
            data=payload,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": config.REFERER,
                "User-Agent": config.USER_AGENT,
            },
            timeout=10,
            allow_redirects=False,
        )
        return r.status_code == 200 and "application/json" in r.headers.get(
            "content-type", ""
        )
    except requests.RequestException:
        return False


def login(session: requests.Session, max_attempts: int = 5) -> None:
    if not config.JACCOUNT_USER or not config.JACCOUNT_PASS:
        raise LoginError("JACCOUNT_USER / JACCOUNT_PASS 未在 .env 中配置")

    session.headers.setdefault("User-Agent", config.USER_AGENT)

    if _is_logged_in(session):
        log.info("session 仍有效,跳过登录")
        return

    # 1. 触发 i.sjtu.edu.cn → jaccount 重定向
    r = session.get(config.JACCOUNT_ENTRY_URL, allow_redirects=True, timeout=20)
    if "jaccount.sjtu.edu.cn" not in r.url:
        raise LoginError(f"未跳转到 jaccount,最终 URL: {r.url}")
    login_page_url = r.url
    ctx = _parse_login_context(r.text)
    required = {"sid", "client", "returl", "se", "uuid"}
    missing = required - ctx.keys()
    if missing:
        raise LoginError(f"jaccount 登录页缺少字段: {missing}")
    log.info("到达 jaccount, uuid=%s", ctx["uuid"][:8])

    ocr = _get_ocr()

    config.CAPTCHA_DEBUG_DIR.mkdir(exist_ok=True)

    for attempt in range(1, max_attempts + 1):
        # 2. 拉验证码图
        cap_resp = session.get(
            config.JACCOUNT_CAPTCHA_URL,
            params={"uuid": ctx["uuid"], "t": str(int(time.time() * 1000))},
            headers={"Referer": login_page_url},
            timeout=10,
        )
        if cap_resp.status_code != 200 or not cap_resp.content:
            raise LoginError(f"拉验证码失败: HTTP {cap_resp.status_code}")

        captcha_text = re.sub(r"\s+", "", str(ocr.classification(cap_resp.content)))
        log.info(
            "尝试 %d/%d, 验证码 OCR: %s (图片 %d 字节)",
            attempt, max_attempts, captcha_text, len(cap_resp.content),
        )
        # debug:把验证码图存盘,方便人工核对 OCR 对不对
        if log.isEnabledFor(logging.DEBUG):
            img_path = config.CAPTCHA_DEBUG_DIR / f"{int(time.time()*1000)}_{captcha_text}.png"
            img_path.write_bytes(cap_resp.content)
            log.debug("验证码图存到 %s", img_path)

        post_data = {
            "sid": ctx["sid"],
            "client": ctx["client"],
            "returl": ctx["returl"],
            "se": ctx["se"],
            "v": ctx.get("v", ""),
            "uuid": ctx["uuid"],
            "user": config.JACCOUNT_USER,
            "pass": config.JACCOUNT_PASS,
            "captcha": captcha_text,
            "lt": "p",  # 必须:p=password 登录;否则服务端误报"验证码错误"
        }

        # 拉验证码到提交之间加 300-700ms 随机延迟,规避"太快"反爬
        time.sleep(random.uniform(0.3, 0.7))

        # 3. 提交凭据 — ulogin 返回 JSON {errno, error, url},不是 302
        ul = session.post(
            config.JACCOUNT_ULOGIN_URL,
            data=post_data,
            headers={
                "Referer": login_page_url,
                "Origin": "https://jaccount.sjtu.edu.cn",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            },
            allow_redirects=False,
            timeout=20,
        )

        try:
            payload = ul.json()
        except ValueError:
            log.warning(
                "第 %d 次:ulogin 返回非 JSON,status=%s ct=%s body=%s",
                attempt, ul.status_code, ul.headers.get("content-type"),
                ul.text[:300].replace("\n", " "),
            )
            continue

        errno = payload.get("errno")
        error_msg = payload.get("error", "")
        next_url = payload.get("url")

        if errno == 0 and next_url:
            # 4. JS 里的 window.location.href = url —— 手动 GET 完成跳回 i.sjtu.edu.cn
            # 注意 url 可能是相对路径 (/jaccount/jalogin?...),要拼回绝对 URL
            absolute_url = urljoin(config.JACCOUNT_ULOGIN_URL, next_url)
            cb = session.get(absolute_url, allow_redirects=True, timeout=20)
            if _is_logged_in(session):
                log.info("登录成功 -> %s", cb.url)
                return
            log.warning(
                "ulogin errno=0 但回跳后 session 仍不可用,回跳 URL: %s",
                cb.url,
            )
            continue

        log.warning(
            "第 %d 次登录失败:errno=%s error=%r",
            attempt, errno, error_msg,
        )
        # 区分密码错与验证码错:密码错没必要继续重试
        if any(kw in str(error_msg).lower() for kw in
               ("password", "credential", "用户名", "密码", "incorrect")):
            raise LoginError(f"用户名或密码错误: {error_msg}")
        if "lock" in str(error_msg).lower() or "锁" in str(error_msg):
            raise LoginError(f"账号被锁定: {error_msg}")
        # 验证码错或其他临时错误 → 重新拉登录页拿新 uuid 再试
        r2 = session.get(config.JACCOUNT_ENTRY_URL, allow_redirects=True, timeout=15)
        new_ctx = _parse_login_context(r2.text)
        if "uuid" in new_ctx and new_ctx["uuid"]:
            ctx = {**ctx, **new_ctx}
            login_page_url = r2.url

    raise LoginError(f"登录失败:连续 {max_attempts} 次验证码识别都没成功")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    s = requests.Session()
    login(s)
    print("Cookies:")
    for c in s.cookies:
        print(f"  {c.domain} {c.name} = {c.value[:30]}...")
