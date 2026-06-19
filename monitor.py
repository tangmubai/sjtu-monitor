"""SJTU 选课监控主程序。

用法:
  python monitor.py            # 长跑,持续轮询
  python monitor.py --once     # 拉一次就退
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

import config
import notifier
import swap as swap_mod
from login import login, LoginError

log = logging.getLogger("monitor")

# 选课开放前所有人数/容量字段都是 '0',开放后学校实际填的是哪个不确定 ——
# 把所有相关字段都纳入 diff,任何一个变了都通知。
DIFF_FIELDS = (
    "yxzrs",    # 已选中人数 (最可能的"已选")
    "xzzrs",    # 选中人数
    "cxrs",     # 抽选人数
    "jxbrs",    # 教学班人数
    "jxbxzrs",  # 教学班选中人数
    "syddrs",   # 剩余可选人数
    "jxbrl",    # 教学班容量 (最可能的"容量")
    "yl",       # 容量
    "krrl",     # 可容容量
    "cxrl",     # 抽选容量
)


class SessionExpired(RuntimeError):
    pass


_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": config.REFERER,
    "Origin": "https://i.sjtu.edu.cn",
    "User-Agent": config.USER_AGENT,
}


def _fetch_one_course(session: requests.Session, kch: str) -> list[dict]:
    """查询单门课的全部教学班。普通课/体育课走不同接口,由 KCH_QUERIES 决定。"""
    qp = config.build_query_payload(kch)
    if qp is None:
        log.warning("kch=%s 没在 KCH_QUERIES 里,跳过", kch)
        return []
    url, payload = qp
    r = session.post(
        url, data=payload, headers=_HEADERS,
        timeout=15, allow_redirects=False,
    )
    ct = r.headers.get("content-type", "")
    if r.status_code in (302, 401, 403) or "application/json" not in ct:
        raise SessionExpired(
            f"status={r.status_code} ct={ct} body={r.text[:120]}"
        )
    endpoint = config.KCH_QUERIES[kch]["endpoint"]
    return config.parse_class_list(endpoint, r.json())


def fetch_courses(session: requests.Session) -> list[dict]:
    """对 KCH_QUERIES 里的每门课查一次,返回所有教学班(按 jxb_id 去重)。"""
    all_classes: list[dict] = []
    seen: set[str] = set()
    for kch in config.KCH_QUERIES.keys():
        classes = _fetch_one_course(session, kch)
        for c in classes:
            jxb_id = c.get("jxb_id")
            if jxb_id and jxb_id not in seen:
                seen.add(jxb_id)
                all_classes.append(c)
        log.debug("kch=%s 拉到 %d 个教学班", kch, len(classes))
    return all_classes


def load_state() -> dict[str, dict]:
    if not config.STATE_FILE.exists():
        return {}
    try:
        return json.loads(config.STATE_FILE.read_text("utf-8"))
    except Exception as e:
        log.warning("state.json 损坏,重建: %s", e)
        return {}


def save_state(state: dict[str, dict]) -> None:
    tmp = config.STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(config.STATE_FILE)


def _to_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _has_spot(course: dict) -> bool | None:
    """jxbxzrs < jxbrl ⇒ 有空位。任一字段缺失/不可比 → None。"""
    sel = _to_int(course.get("jxbxzrs"))
    cap = _to_int(course.get("jxbrl"))
    if sel is None or cap is None or cap <= 0:
        return None
    return sel < cap


def _open_targets(
    current: dict[str, dict], watched: set[str]
) -> list[dict]:
    """构造当前确有空位的自动升级候选,不依赖空位是否刚刚出现。"""
    targets = []
    for jxb_id in watched:
        course = current.get(jxb_id)
        if course and _has_spot(course):
            targets.append({
                "kind": "spot_open",
                "jxb_id": jxb_id,
                "jxbmc": course.get("jxbmc"),
                "kcmc": course.get("kcmc"),
                "msg": f"有空位! {course.get('jxbxzrs')}/{course.get('jxbrl')}",
            })
    return targets


def _held_by_group(completed: set[str]) -> dict[str, str]:
    """根据初始配置和成功记录计算每组当前持有的最高优先级班。"""
    held = config.initial_held()
    for group, group_cfg in config.PRIORITY_GROUPS.items():
        completed_in_group = [
            jxb_id for jxb_id in group_cfg["priority"] if jxb_id in completed
        ]
        if completed_in_group:
            # priority 从高到低排列,取已完成目标中优先级最高的一项。
            held[group] = completed_in_group[0]
    return held


def _watched_ids(sw_state: dict | None = None) -> set[str]:
    """只返回各组当前持有班之前的目标;致命失败组暂停监控。"""
    sw_state = _load_swap_state() if sw_state is None else sw_state
    completed = set(sw_state.get("completed", []))
    held = _held_by_group(completed)
    watched = config.watched_ids(held)
    for group in sw_state.get("fatal_groups", []):
        group_cfg = config.PRIORITY_GROUPS.get(group)
        if group_cfg:
            watched.difference_update(group_cfg["priority"])
    return watched


def diff(
    old: dict[str, dict],
    new: dict[str, dict],
    watched: set[str] | None = None,
) -> list[dict]:
    """只对当前优先级范围内的教学班产生通知;其他班存盘但不打扰。

    特别地:当 jxbxzrs < jxbrl(有空位) 且上一轮还是满的(或第一次见到)时,
    额外产生一条 kind='spot_open' 的紧急通知。
    """
    changes = []
    watched = _watched_ids() if watched is None else watched
    for jxb_id, course in new.items():
        if jxb_id not in watched:
            continue
        prev = old.get(jxb_id)

        # 1) 空位告警:上轮无 / 上轮满 → 这轮有空位
        now_open = _has_spot(course)
        was_open = _has_spot(prev) if prev else False
        if now_open and not was_open:
            sel = course.get("jxbxzrs")
            cap = course.get("jxbrl")
            changes.append({
                "kind": "spot_open",
                "jxb_id": jxb_id,
                "jxbmc": course.get("jxbmc"),
                "kcmc": course.get("kcmc"),
                "msg": f"有空位! {sel}/{cap}",
            })

        # 2) 常规 diff
        if prev is None:
            changes.append({"kind": "added", **course})
            continue
        field_diffs = {}
        for f in DIFF_FIELDS:
            if prev.get(f) != course.get(f):
                field_diffs[f] = (prev.get(f), course.get(f))
        if field_diffs:
            changes.append({
                "kind": "changed",
                "jxbmc": course.get("jxbmc"),
                "kcmc": course.get("kcmc"),
                "changes": field_diffs,
            })

    for jxb_id, course in old.items():
        if jxb_id not in watched:
            continue
        if jxb_id not in new:
            changes.append({"kind": "removed", **course})
    return changes


def append_log(changes: list[dict]) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    with config.LOG_FILE.open("a", encoding="utf-8") as f:
        for c in changes:
            f.write(f"{ts} {json.dumps(c, ensure_ascii=False)}\n")


def _load_swap_state() -> dict:
    if not config.SWAP_STATE_FILE.exists():
        return {"completed": [], "fatal": [], "fatal_groups": []}
    try:
        return json.loads(config.SWAP_STATE_FILE.read_text("utf-8"))
    except Exception:
        return {"completed": [], "fatal": [], "fatal_groups": []}


def _save_swap_state(state: dict) -> None:
    tmp = config.SWAP_STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(config.SWAP_STATE_FILE)


def maybe_auto_swap(
    session: requests.Session,
    spot_open_changes: list[dict],
) -> list[dict]:
    """每组只选择当前空闲目标中优先级最高的一项执行升级。

    返回 swap 操作的结果列表,可作为额外通知项。
    """
    if not config.AUTO_SWAP:
        return []
    sw_state = _load_swap_state()
    completed: set[str] = set(sw_state.get("completed", []))
    fatal: set[str] = set(sw_state.get("fatal", []))
    fatal_groups: set[str] = set(sw_state.get("fatal_groups", []))
    held = _held_by_group(completed)
    results = []

    # 同组可能同时有多个班空出。先分组,再只处理优先级最高的目标。
    candidates: dict[str, list[dict]] = {}
    for c in spot_open_changes:
        target_id = c.get("jxb_id")
        if not target_id:
            continue
        group = config.find_group(target_id)
        if group is None:
            log.warning("[swap] %s 不属于任何优先级组,跳过", target_id)
            continue
        if group in fatal_groups:
            log.warning("[swap] %s 组之前发生 FATAL,人工处理前暂停", group)
            continue
        ids = config.PRIORITY_GROUPS[group]["priority"]
        current_held = held.get(group)
        if current_held not in ids or ids.index(target_id) >= ids.index(current_held):
            log.info("[swap] %s 不高于当前持有 %s,跳过", target_id, current_held)
            continue
        candidates.setdefault(group, []).append(c)

    for group, group_candidates in candidates.items():
        group_cfg = config.PRIORITY_GROUPS[group]
        ids = group_cfg["priority"]
        c = min(group_candidates, key=lambda item: ids.index(item["jxb_id"]))
        target_id = c["jxb_id"]
        drop_id = held[group]
        is_pe = group_cfg.get("is_pe", False)
        log.info("[swap] 触发: group=%s target=%s drop=%s is_pe=%s dry=%s",
                 group, target_id, drop_id, is_pe, config.AUTO_SWAP_DRY_RUN)
        ok, status = swap_mod.drop_then_select(
            session,
            drop_jxb_id=drop_id,
            select_jxb_id=target_id,
            is_pe=is_pe,
            dry_run=config.AUTO_SWAP_DRY_RUN,
        )
        results.append({
            "kind": "swap_result",
            "jxbmc": c.get("jxbmc"),
            "kcmc": c.get("kcmc"),
            "ok": ok,
            "status": status,
            "group": group,
            "target": target_id,
            "drop": drop_id,
        })
        if ok and not config.AUTO_SWAP_DRY_RUN:
            completed.add(target_id)
            held[group] = target_id
        elif status == "FATAL_LOST":
            fatal.add(target_id)
            fatal_groups.add(group)
    sw_state["completed"] = sorted(completed)
    sw_state["fatal"] = sorted(fatal)
    sw_state["fatal_groups"] = sorted(fatal_groups)
    _save_swap_state(sw_state)
    return results


def run_once(session: requests.Session, state: dict[str, dict]) -> dict[str, dict]:
    courses = fetch_courses(session)
    current = {c["jxb_id"]: c for c in courses}
    log.info("本轮拉取 %d 个教学班", len(current))
    watched = _watched_ids()
    log.info("当前监控 %d 个更高优先级教学班", len(watched))
    if not state:
        log.info("首轮:保存初始快照,不发普通变更通知")
        changes = []
    else:
        changes = diff(state, current, watched)
    if changes:
        log.info("检测到 %d 条变更", len(changes))
    swap_results = []
    if config.AUTO_SWAP:
        try:
            swap_results = maybe_auto_swap(session, _open_targets(current, watched))
        except Exception as e:
            log.exception("auto swap 异常: %s", e)
    all_changes = list(changes) + swap_results
    if all_changes:
        # 变更和自动换班结果统一通知。
        try:
            notifier.send(all_changes)
        except Exception as e:
            log.warning("通知发送异常: %s", e)
        append_log(all_changes)
    save_state(current)
    return current


def main_loop() -> None:
    session = requests.Session()
    login(session)
    state = load_state()
    backoff = 0
    while True:
        try:
            state = run_once(session, state)
            backoff = 0
        except SessionExpired as e:
            log.info("session 失效,重新登录: %s", e)
            try:
                login(session)
            except LoginError as le:
                log.error("重新登录失败,5 分钟后重试: %s", le)
                time.sleep(300)
            continue
        except requests.RequestException as e:
            backoff = min(backoff * 2 + 30, 600)
            log.warning("网络异常,%d 秒后重试: %s", backoff, e)
            time.sleep(backoff)
            continue
        except Exception as e:
            log.exception("未预期错误,30 秒后重试: %s", e)
            time.sleep(30)
            continue
        sleep_s = random.uniform(config.POLL_MIN, config.POLL_MAX)
        log.info("休眠 %.1f 秒", sleep_s)
        time.sleep(sleep_s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="只跑一轮")
    ap.add_argument("--debug", action="store_true", help="DEBUG 日志")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    def _bye(signum, frame):
        log.info("收到信号 %s,退出", signum)
        sys.exit(0)

    signal.signal(signal.SIGINT, _bye)
    signal.signal(signal.SIGTERM, _bye)

    if args.once:
        s = requests.Session()
        login(s)
        state = load_state()
        run_once(s, state)
    else:
        main_loop()


if __name__ == "__main__":
    main()
