"""选课 / 退选 demo。

用法 (强烈建议先用 --dry-run 试):

  # 演练模式 (只打印请求,不发)
  python swap.py drop   <jxb_id> --dry-run
  python swap.py select <jxb_id> --dry-run
  python swap.py swap   <drop_jxb_id> <select_jxb_id> --dry-run

  # 真执行 (要加 --yes 二次确认)
  python swap.py drop   <jxb_id> --yes
  python swap.py select <jxb_id> --yes
  python swap.py swap   <drop_jxb_id> <select_jxb_id> --yes

  # 体育课选课要加 --pe (因为 bklx_id 不一样)
  python swap.py select 52B1F0425B7E82C2E065F8163EE1DCCC --pe --yes

swap 流程:**先选新课**(乐观策略),成功后**再退旧课**。
这样万一新课抢不到,旧课还在,不会落空。
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Literal

import requests

import config
from login import login

log = logging.getLogger("swap")

_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": config.REFERER,
    "Origin": "https://i.sjtu.edu.cn",
    "User-Agent": config.USER_AGENT,
}


def drop_course(
    session: requests.Session, jxb_id: str, dry_run: bool = False
) -> tuple[bool, str]:
    """退选。成功:服务端返回字符串 '1'。"""
    data = {
        "xkxnm": config.XKXNM, "xkxqm": config.XKXQM,
        "jxb_ids": jxb_id, "txbsfrl": "1",
    }
    log.info("[退选] jxb_id=%s data=%s", jxb_id, data)
    if dry_run:
        log.info("[退选] dry-run,不发请求")
        return True, "DRY_RUN"
    r = session.post(config.DROP_URL, data=data, headers=_HEADERS, timeout=15)
    body = r.text.strip().strip('"')
    log.info("[退选] status=%s ct=%s body=%r", r.status_code, r.headers.get("content-type"), body)
    return body == "1", body


def select_course(
    session: requests.Session, jxb_id: str,
    is_pe: bool = False, dry_run: bool = False,
) -> tuple[bool, str]:
    """选课。成功:JSON {'flag': '1'}。体育课的 bklx_id 不一样。"""
    data = {
        "jxb_ids": jxb_id,
        "bklx_id": config.PE_BKLX_ID if is_pe else "0",
        "qz": "0",
        "cxbj": "0",
    }
    log.info("[选课] jxb_id=%s is_pe=%s data=%s", jxb_id, is_pe, data)
    if dry_run:
        log.info("[选课] dry-run,不发请求")
        return True, "DRY_RUN"
    r = session.post(config.SELECT_URL, data=data, headers=_HEADERS, timeout=15)
    log.info("[选课] status=%s ct=%s body=%r", r.status_code, r.headers.get("content-type"), r.text[:200])
    try:
        flag = r.json().get("flag")
        return flag == "1", str(r.json())
    except ValueError:
        return False, r.text[:200]


def swap(
    session: requests.Session,
    drop_jxb_id: str,
    select_jxb_id: str,
    is_pe: bool = False,
    dry_run: bool = False,
) -> bool:
    """乐观 swap (已废弃,仅保留供手动调用):先选新课再退旧课。"""
    log.info("=== SWAP[乐观]: select %s, then drop %s (is_pe=%s) ===",
             select_jxb_id, drop_jxb_id, is_pe)
    ok, msg = select_course(session, select_jxb_id, is_pe=is_pe, dry_run=dry_run)
    if not ok:
        log.error("[SWAP] 选新课失败 (%s),旧课保留,中止 swap", msg)
        return False
    log.info("[SWAP] 选新课成功,继续退旧课")
    ok2, msg2 = drop_course(session, drop_jxb_id, dry_run=dry_run)
    if not ok2:
        log.error("[SWAP] 退旧课失败 (%s) — 注意:新旧两门课现在都在已选列表!", msg2)
        return False
    log.info("[SWAP] 完成")
    return True


def drop_then_select(
    session: requests.Session,
    drop_jxb_id: str,
    select_jxb_id: str,
    is_pe: bool = False,
    dry_run: bool = False,
    retries: int = 5,
    retry_interval: float = 0.3,
) -> tuple[bool, str]:
    """**生产用** 悲观 swap:先退旧课,立即选新课。同 kch 必须这个顺序。

    返回 (success, status_msg)。
    - success=True: 新课已选上,旧课已退
    - success=False: 状态说明在 status_msg。可能值:
        - "drop_failed":     退选失败,什么都没变
        - "select_failed":   退选成功但新课选不上,且回选旧课**成功** → 状态不变
        - "FATAL_LOST":      退选成功、新课选不上、回选旧课也失败 → **落空,必须人工处理**
    """
    log.info("=== drop_then_select: drop %s → select %s (is_pe=%s, dry=%s) ===",
             drop_jxb_id, select_jxb_id, is_pe, dry_run)

    drop_ok, drop_body = drop_course(session, drop_jxb_id, dry_run=dry_run)
    if not drop_ok:
        log.warning("[swap] 退选失败,旧课保留,本次 swap 放弃: %s", drop_body)
        return False, "drop_failed"

    # 立即选新课,无 sleep 间隔
    for attempt in range(1, retries + 1):
        sel_ok, sel_body = select_course(session, select_jxb_id, is_pe=is_pe, dry_run=dry_run)
        if sel_ok:
            log.info("[swap] ✅ 成功 (尝试 %d/%d)", attempt, retries)
            return True, "ok"
        log.warning("[swap] 第 %d/%d 次选新课失败: %s", attempt, retries, sel_body)
        if attempt < retries:
            time.sleep(retry_interval)

    # 兜底:试着选回旧课
    log.error("[swap] 新课 %d 次都选不上,尝试选回旧课 %s", retries, drop_jxb_id)
    fb_ok, fb_body = select_course(session, drop_jxb_id, is_pe=is_pe, dry_run=dry_run)
    if fb_ok:
        log.warning("[swap] 旧课已选回,本次 swap 失败但无损失")
        return False, "select_failed"

    log.error("=" * 60)
    log.error("⚠️  FATAL: 退选成功但新课和旧课都选不回!")
    log.error("    drop = %s (这门课已没了)", drop_jxb_id)
    log.error("    select = %s (这门课也没拿到)", select_jxb_id)
    log.error("    旧课回选错误: %s", fb_body)
    log.error("    ⚠️  立即人工登录处理!")
    log.error("=" * 60)
    return False, "FATAL_LOST"


def _confirm(args, action_desc: str) -> bool:
    if args.dry_run:
        return True
    if args.yes:
        return True
    log.error("[安全] 真执行需要加 --yes 或 --dry-run,放弃 %s", action_desc)
    return False


def main():
    ap = argparse.ArgumentParser(description="SJTU 选课/退选 demo")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_drop = sub.add_parser("drop", help="退选指定 jxb_id")
    p_drop.add_argument("jxb_id")

    p_sel = sub.add_parser("select", help="选课指定 jxb_id")
    p_sel.add_argument("jxb_id")
    p_sel.add_argument("--pe", action="store_true", help="体育课 (bklx_id 不同)")

    p_swap = sub.add_parser("swap", help="先选新课,成功后再退旧课")
    p_swap.add_argument("drop_jxb_id", help="要退的 jxb_id")
    p_swap.add_argument("select_jxb_id", help="要选的 jxb_id")
    p_swap.add_argument("--pe", action="store_true", help="体育课")

    for p in (p_drop, p_sel, p_swap):
        p.add_argument("--dry-run", action="store_true", help="只打印请求,不实际发")
        p.add_argument("--yes", action="store_true", help="确认真执行(二次保险)")
        p.add_argument("--debug", action="store_true")

    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    desc = {
        "drop":   f"退选 {getattr(args, 'jxb_id', '')}",
        "select": f"选课 {getattr(args, 'jxb_id', '')}",
        "swap":   f"swap (退 {getattr(args, 'drop_jxb_id', '')} → 选 {getattr(args, 'select_jxb_id', '')})",
    }[args.cmd]
    if not _confirm(args, desc):
        sys.exit(2)

    session = requests.Session()
    login(session)

    if args.cmd == "drop":
        ok, _ = drop_course(session, args.jxb_id, dry_run=args.dry_run)
    elif args.cmd == "select":
        ok, _ = select_course(session, args.jxb_id, is_pe=args.pe, dry_run=args.dry_run)
    elif args.cmd == "swap":
        ok = swap(session, args.drop_jxb_id, args.select_jxb_id,
                  is_pe=args.pe, dry_run=args.dry_run)
    else:
        ok = False

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
