"""测试脚本:对 MARX1202-12 先退选再立即选回,测真实场景下的速度/可行性。

使用:
  python test_drop_reselect.py --dry-run    # 演练
  python test_drop_reselect.py --yes        # 真执行

策略:
  1. 登录一次
  2. 紧接着 退选 → 选课,两步之间不 sleep,只有 HTTP 往返延迟
  3. 打印每步耗时,失败时立即重试 select 几次 (退了选不回的兜底)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

import requests

import config
from login import login
from swap import drop_course, select_course

log = logging.getLogger("test")

# 测试目标:MARX1202-12 (你目前已选,有空位)
TARGET_JXB_ID = "50842EA6CEDEB7A3E065F8163EE1DCCC"
TARGET_NAME = "MARX1202-12 中国近现代史纲要"

# 退选后,若立刻选不回来的重试策略
RESELECT_MAX_RETRIES = 3
RESELECT_RETRY_INTERVAL = 0.5  # 秒


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印请求不实发")
    ap.add_argument("--yes", action="store_true", help="确认真执行")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--jxb-id", default=TARGET_JXB_ID,
                    help=f"目标 jxb_id (默认 {TARGET_JXB_ID[:16]}...)")
    args = ap.parse_args()

    if not (args.dry_run or args.yes):
        print("拒跑:必须加 --dry-run 或 --yes")
        sys.exit(2)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    jxb_id = args.jxb_id
    log.info("=" * 60)
    log.info("目标教学班: %s", TARGET_NAME)
    log.info("jxb_id    : %s", jxb_id)
    log.info("模式      : %s", "DRY-RUN (不发请求)" if args.dry_run else "实战")
    log.info("=" * 60)

    # 登录
    t0 = time.monotonic()
    session = requests.Session()
    login(session)
    t_login = time.monotonic() - t0
    log.info("登录耗时: %.2fs", t_login)

    # 退选
    log.info("--- Phase 1: 退选 ---")
    t1 = time.monotonic()
    drop_ok, drop_body = drop_course(session, jxb_id, dry_run=args.dry_run)
    t_drop = time.monotonic() - t1
    log.info("退选耗时: %.3fs, 结果: %s, body=%r", t_drop, drop_ok, drop_body)

    if not drop_ok:
        log.error("退选失败,中止。课程仍在已选列表里(预期),无需补救。")
        sys.exit(1)

    # 立即重新选回 (退选成功后无 sleep,直接发 select)
    log.info("--- Phase 2: 立即选回 ---")
    for attempt in range(1, RESELECT_MAX_RETRIES + 1):
        t2 = time.monotonic()
        sel_ok, sel_body = select_course(session, jxb_id, dry_run=args.dry_run)
        t_sel = time.monotonic() - t2
        log.info("尝试 %d/%d 选课耗时: %.3fs, 结果: %s, body=%r",
                 attempt, RESELECT_MAX_RETRIES, t_sel, sel_ok, sel_body)
        if sel_ok:
            break
        if attempt < RESELECT_MAX_RETRIES:
            log.warning("选课失败,%.1fs 后重试", RESELECT_RETRY_INTERVAL)
            time.sleep(RESELECT_RETRY_INTERVAL)
    else:
        log.error("=" * 60)
        log.error("⚠️  退了但 %d 次都选不回!请立即手动登录处理!", RESELECT_MAX_RETRIES)
        log.error("    jxb_id = %s", jxb_id)
        log.error("=" * 60)
        sys.exit(2)

    # 总结
    log.info("=" * 60)
    log.info("✅ 测试完成")
    log.info("  登录:     %.2fs", t_login)
    log.info("  退选:     %.3fs", t_drop)
    log.info("  选课:     %.3fs", t_sel)
    log.info("  退-选间隔: 仅 HTTP 往返延迟 (无 sleep)")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
