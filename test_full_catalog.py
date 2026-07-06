"""zzxkyzb 全量目录抓取 —— 验证/探索 CLI(核心逻辑已提炼到 zzxk.py 生产模块)。

已在校园网实测通过(2026-07-06):
  A. 令牌 xkkz_xh 随首页一次抓取即有效,未见过期;
  B. kspage/jspage 是"页号范围",空窗口 = 该分类翻完,跨窗口重复靠 jxb_id 去重;
  C. 实测本账号本轮: 主修 1 / 交叉 0 / 任选 34 / 通识 28 / 公选 26,合计 89 门;
  D. 请求间隔 1s 未触发频控;
  - payload 必须按白名单发送(多发参数服务端报"系统运行异常")——白名单在 zzxk.py。
  ⚠ zzxkyzb 与 tjxkbkk 是不同轮次,课程不重叠、id 不通用(实测):
    zzxk 课程监控走 zzxk.fetch_seats,不能用 tjxkbkk display 查询。

用法:
  python test_full_catalog.py                     # 默认: 只测"公选(11)", 翻 1 个窗口, 查 3 门课容量
  python test_full_catalog.py --kklxdm 01         # 换分类(01主修/10通识/11公选/30任选/69交叉)
  python test_full_catalog.py --all-categories --max-windows 12
  python test_full_catalog.py --jxb-limit 0       # 跳过容量抽查
  python test_full_catalog.py --out result.json   # 汇总写盘
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import requests

import zzxk
from login import login

log = logging.getLogger("full_catalog")


def scan_category(session: requests.Session, index_hidden: dict, tab: dict, args) -> dict:
    """扫描单个分类: Display → 翻页 PartDisplay → (可选)容量抽查。返回汇总。"""
    log.info("=" * 60)
    log.info("开始扫描分类 %s(%s)", tab["kklxdm"], tab["label"])
    display_hidden = zzxk.fetch_display_form(session, tab)
    time.sleep(args.sleep)
    source = zzxk._build_source(index_hidden, display_hidden, tab)

    rows = zzxk.sweep_category(
        session, source,
        max_windows=args.max_windows, jspage=args.jspage, sleep=args.sleep,
    )
    courses: dict[str, dict] = {}
    for row in rows:
        if row.get("kch_id"):
            courses.setdefault(row["kch_id"], row)
    log.info("分类 %s: %d 个教学班 / %d 门课程", tab["kklxdm"], len(rows), len(courses))

    sampled = []
    for course in list(courses.values())[: args.jxb_limit]:
        classes = zzxk.fetch_jxb_capacity(session, source, course)
        log.info("  容量抽查 %s(%s): %d 个教学班, jxbrl=%s",
                 course.get("kch"), course.get("kcmc"), len(classes),
                 [c.get("jxbrl") for c in classes])
        sampled.append({
            "kch": course.get("kch"), "kcmc": course.get("kcmc"),
            "classes": [(c.get("jxb_id"), c.get("jxbrl")) for c in classes],
        })
        time.sleep(args.sleep)

    return {
        "kklxdm": tab["kklxdm"], "label": tab["label"],
        "class_count": len(rows), "course_count": len(courses),
        "courses": [
            {"kch": c.get("kch"), "kch_id": c.get("kch_id"),
             "kcmc": c.get("kcmc"), "yxzrs": c.get("yxzrs"), "kzmc": c.get("kzmc")}
            for c in courses.values()
        ],
        "capacity_samples": sampled,
    }


def main():
    ap = argparse.ArgumentParser(description="zzxkyzb 全量目录抓取验证")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--kklxdm", default="11", help="要测的分类代码(默认 11 公选)")
    ap.add_argument("--all-categories", action="store_true", help="遍历全部分类")
    ap.add_argument("--max-windows", type=int, default=1, help="每类最多翻几个分页窗口")
    ap.add_argument("--jspage", type=int, default=10, help="每个窗口的页范围大小")
    ap.add_argument("--jxb-limit", type=int, default=3, help="每类抽查容量的课程数(0=跳过)")
    ap.add_argument("--sleep", type=float, default=1.0, help="请求间隔秒")
    ap.add_argument("--out", default=None, help="把汇总结果写到该 JSON 文件")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    session = requests.Session()
    login(session)

    index_hidden, tabs = zzxk.fetch_index(session)
    for t in tabs:
        log.info("分类 %s(%s) xkkz_id=%s… xkkz_xh长度=%d",
                 t["kklxdm"], t["label"], t["xkkz_id"][:12], len(t["xkkz_xh"]))
    time.sleep(args.sleep)

    selected = tabs if args.all_categories else [
        t for t in tabs if t["kklxdm"] == args.kklxdm
    ]
    if not selected:
        log.error("首页无分类 %s; 可用: %s", args.kklxdm, [t["kklxdm"] for t in tabs])
        return

    results = []
    for tab in selected:
        try:
            results.append(scan_category(session, index_hidden, tab, args))
        except Exception as e:
            log.exception("分类 %s 扫描失败: %s", tab["kklxdm"], e)

    print()
    print("=" * 60)
    print("汇总")
    total = 0
    for r in results:
        total += r["course_count"]
        print(f"  分类 {r['kklxdm']}({r['label']}): "
              f"{r['class_count']} 个教学班 / {r['course_count']} 门课; "
              f"容量抽查 {len(r['capacity_samples'])} 门")
    print(f"合计课程: {total} 门")

    if args.out:
        Path(args.out).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), "utf-8"
        )
        print(f"汇总已写入 {args.out}")


if __name__ == "__main__":
    main()
