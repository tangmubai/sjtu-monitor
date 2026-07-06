"""zzxkyzb 选课/退选接口验证脚本(核心接口封装已提炼到 zzxk.py)。

⚠ 危险: 本脚本会真实操作账号选课/退选! 默认 --dry-run(只解析 do_jxb_id 并打印,
   不实际选退)。加 --yes 才会真的选课; 每门课都是 选→查验→退→查验 一气呵成,
   逐门处理, 任何一步失败立即停止, 避免账号停留在"选了没退"的状态。

关键机制(从 zzxkYzbChoosedZy.js 逆向, 2026-07-06 联网实测全部通过):
  - 选课/退课的 jxb_ids 用的都是 **do_jxb_id**(256 位令牌), 不是明文 jxb_id;
    do_jxb_id 来自 JxbWithKch 接口(每次查询新生成, 不能硬编码)。
  - 选课 POST zzxkyzbjk_xkBcZyZzxkYzb.html → 成功 JSON data.flag in ("1","3")
  - 退课 POST zzxkyzb_tuikBcZzxkYzb.html   → 成功 文本 data == "1"
  - 已选查询 POST zzxkyzb_cxZzxkYzbChoosedDisplay.html → 已选课程 JSON 列表

用法:
  python test_zzxk_select_drop.py                 # dry-run: 只解析并打印, 不动账号
  python test_zzxk_select_drop.py --yes           # 真实执行: 选→查→退→查(逐门, 自动还原)
  python test_zzxk_select_drop.py --yes --no-drop # 只选不退(危险! 会留下已选课程)
  python test_zzxk_select_drop.py --debug
"""
from __future__ import annotations

import argparse
import logging
import time

import requests

import zzxk
from login import login

log = logging.getLogger("zzxk_xk")

# 要测试的三门课(初始均未选)。用 (kklxdm 分类, kch 课程号, jxbmc 关键字) 定位。
TARGETS = [
    {"kklxdm": "01", "kch": "NAOE2309", "jxbmc_kw": "NAOE2309-01", "name": "认识实习"},
    {"kklxdm": "30", "kch": "FL3438", "jxbmc_kw": "FL3438-01", "name": "专业实习(日语)"},
    {"kklxdm": "11", "kch": "ART1127", "jxbmc_kw": "ART1127-01", "name": "造型艺术实践-水彩"},
]


def _is_chosen(choosed: list[dict], jxb_id: str) -> bool:
    return any(c.get("jxb_id") == jxb_id for c in choosed)


def resolve_target(
    session: requests.Session, index_hidden: dict, tab: dict, target: dict, args,
) -> dict | None:
    """解析出选课所需的全部字段: jxb_id / do_jxb_id / cxbj / xxkbj + 分类表单。"""
    display_hidden = zzxk.fetch_display_form(session, tab)
    time.sleep(args.sleep)
    source = zzxk._build_source(index_hidden, display_hidden, tab)

    # 1) PartDisplay 翻页找到该课程行(拿 kch_id / cxbj / xxkbj / 目标 jxb_id)
    course_row = None
    for w in range(args.max_windows):
        rows = zzxk.fetch_part_display(
            session, source, w * args.jspage + 1, (w + 1) * args.jspage
        )
        for r in rows:
            if r.get("kch") == target["kch"] and target["jxbmc_kw"] in (r.get("jxbmc") or ""):
                course_row = r
                break
        if course_row or not rows:
            break
        time.sleep(args.sleep)
    if not course_row:
        log.error("未在分类 %s 找到 %s(%s)", tab["kklxdm"], target["kch"], target["jxbmc_kw"])
        return None
    jxb_id = course_row["jxb_id"]
    kch_id = course_row["kch_id"]
    log.info("定位到 %s %s: jxb_id=%s cxbj=%s xxkbj=%s yxzrs(已选)=%s",
             target["kch"], course_row.get("jxbmc"), jxb_id[:12],
             course_row.get("cxbj"), course_row.get("xxkbj"), course_row.get("yxzrs"))
    time.sleep(args.sleep)

    # 2) JxbWithKch 拿该 jxb_id 的 do_jxb_id + 容量
    classes = zzxk.fetch_jxb_capacity(session, source, course_row)
    match = next((c for c in classes if c.get("jxb_id") == jxb_id), None)
    if not match or not match.get("do_jxb_id"):
        log.error("%s 未取到 do_jxb_id(教学班 %d 个)", target["kch"], len(classes))
        return None
    log.info("  取到 do_jxb_id(长度 %d) 容量 jxbrl=%s",
             len(match["do_jxb_id"]), match.get("jxbrl"))

    sxbj = "1" if any(display_hidden.get(k) == "1"
                      for k in ("rlkz", "cdrlkz", "rlzlkz")) else "0"
    return {
        "source": source, "display_hidden": display_hidden,
        "jxb_id": jxb_id, "kch_id": kch_id, "do_jxb_id": match["do_jxb_id"],
        "cxbj": course_row.get("cxbj", "0"), "xxkbj": course_row.get("xxkbj", "0"),
        "sxbj": sxbj, "kcmc": course_row.get("kcmc", ""),
        "jxbrl": match.get("jxbrl"), "yxzrs": course_row.get("yxzrs"),
    }


def do_select(session: requests.Session, tab: dict, r: dict) -> tuple[bool, str]:
    d, s = r["display_hidden"], r["source"]
    payload = {
        "kcmc": r["kcmc"], "kch_id": r["kch_id"], "jxb_ids": r["do_jxb_id"],
        "rwlx": d.get("rwlx", ""), "rlkz": d.get("rlkz", "0"),
        "cdrlkz": d.get("cdrlkz", "0"), "rlzlkz": d.get("rlzlkz", "0"),
        "sxbj": r["sxbj"], "xxkbj": r["xxkbj"], "cxbj": r["cxbj"],
        "xkkz_id": tab["xkkz_id"], "kklxdm": tab["kklxdm"],
        "njdm_id": tab["njdm_id"], "zyh_id": tab["zyh_id"],
        "xklc": d.get("xklc", ""), "xkxnm": s.get("xkxnm", ""),
        "xkxqm": s.get("xkxqm", ""), "qz": d.get("qz", "0"), "jcxx_id": "",
    }
    resp = session.post(zzxk.SELECT_URL, data=payload, headers=zzxk._AJAX_HEADERS,
                        timeout=20, allow_redirects=False)
    try:
        data = resp.json()
        flag = str(data.get("flag"))
        return flag in ("1", "3"), f"flag={flag} msg={data.get('msg') or data.get('message')}"
    except ValueError:
        return False, f"非JSON status={resp.status_code} body={resp.text[:150]}"


def do_drop(session: requests.Session, r: dict) -> tuple[bool, str]:
    s = r["source"]
    payload = {
        "kch_id": r["kch_id"], "jxb_ids": r["do_jxb_id"],
        "xkxnm": s.get("xkxnm", ""), "xkxqm": s.get("xkxqm", ""),
        "txbsfrl": r["display_hidden"].get("txbsfrl", "1"),
    }
    resp = session.post(zzxk.DROP_URL, data=payload, headers=zzxk._AJAX_HEADERS,
                        timeout=20, allow_redirects=False)
    body = resp.text.strip().strip('"')
    return body == "1", f"body={body[:80]}"


def main():
    ap = argparse.ArgumentParser(description="zzxkyzb 选课/退选验证")
    ap.add_argument("--yes", action="store_true", help="真实执行选/退(否则仅解析)")
    ap.add_argument("--no-drop", action="store_true", help="选后不退(危险!)")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--max-windows", type=int, default=6)
    ap.add_argument("--jspage", type=int, default=10)
    ap.add_argument("--sleep", type=float, default=1.0)
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    session = requests.Session()
    login(session)
    index_hidden, tabs = zzxk.fetch_index(session)
    tab_by_kklxdm = {t["kklxdm"]: t for t in tabs}
    time.sleep(args.sleep)

    # 建一个用于 ChoosedDisplay 的 source(任取一个分类的 Display 补全个人字段)
    any_tab = tabs[0]
    base_source = zzxk._build_source(
        index_hidden, zzxk.fetch_display_form(session, any_tab), any_tab
    )
    time.sleep(args.sleep)

    choosed0 = zzxk.fetch_choosed(session, base_source)
    log.info("初始已选课程: %d 门 %s", len(choosed0),
             [c.get("kcmc") for c in choosed0])

    print()
    print("=" * 64)
    print(f"模式: {'真实执行 (--yes)' if args.yes else 'DRY-RUN (仅解析, 不操作账号)'}")
    print("=" * 64)

    results = []
    for target in TARGETS:
        print(f"\n--- {target['kch']} {target['name']} ({target['jxbmc_kw']}) ---")
        tab = tab_by_kklxdm.get(target["kklxdm"])
        if not tab:
            log.error("首页无分类 %s, 跳过", target["kklxdm"])
            continue
        r = resolve_target(session, index_hidden, tab, target, args)
        if not r:
            results.append((target, "解析失败"))
            continue
        already = _is_chosen(zzxk.fetch_choosed(session, base_source), r["jxb_id"])
        print(f"  jxb_id={r['jxb_id']}  容量={r['jxbrl']} 已选={r['yxzrs']}  "
              f"当前是否已选={already}")
        if not args.yes:
            print("  [dry-run] 已解析出 do_jxb_id, 未执行选课")
            results.append((target, "dry-run 解析成功"))
            time.sleep(args.sleep)
            continue

        if already:
            log.warning("  %s 已在已选列表, 跳过选课以免误操作", target["kch"])
            results.append((target, "已选,跳过"))
            continue

        # 选课
        ok, msg = do_select(session, tab, r)
        print(f"  选课: {'成功' if ok else '失败'}  ({msg})")
        time.sleep(args.sleep)
        chosen_now = _is_chosen(zzxk.fetch_choosed(session, base_source), r["jxb_id"])
        print(f"  查验: 选课后已选列表包含该班 = {chosen_now}")
        if not ok or not chosen_now:
            results.append((target, f"选课失败: {msg}"))
            log.error("  选课未成功且未在已选列表, 停止后续以防状态混乱")
            break

        if args.no_drop:
            results.append((target, "已选(--no-drop 未还原)"))
            continue

        # 退课还原
        time.sleep(args.sleep)
        ok2, msg2 = do_drop(session, r)
        print(f"  退课: {'成功' if ok2 else '失败'}  ({msg2})")
        time.sleep(args.sleep)
        chosen_after = _is_chosen(zzxk.fetch_choosed(session, base_source), r["jxb_id"])
        print(f"  查验: 退课后已选列表包含该班 = {chosen_after} (应为 False)")
        if ok2 and not chosen_after:
            results.append((target, "选退成功,已还原"))
        else:
            results.append((target, f"⚠退课异常! 请人工检查: {msg2}"))
            log.error("  退课后该班仍在已选列表! 立即停止, 请人工登录处理")
            break
        time.sleep(args.sleep)

    choosedN = zzxk.fetch_choosed(session, base_source)
    print()
    print("=" * 64)
    print("汇总")
    for target, status in results:
        print(f"  {target['kch']:<10} {status}")
    print(f"结束时已选课程: {len(choosedN)} 门 {[c.get('kcmc') for c in choosedN]}")
    if len(choosedN) != len(choosed0):
        print("⚠ 结束时已选数量与初始不一致, 请人工核对账号状态!")


if __name__ == "__main__":
    main()
