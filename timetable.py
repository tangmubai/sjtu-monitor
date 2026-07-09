"""解析 `sksj`(上课时间)字符串并判断两门课是否时间冲突。

纯 stdlib,不依赖 Qt/requests,可被 gui.py 和 monitor.py 同时导入。
(命名避开 pip 包 `schedule`,以免混淆。)

sksj 真实样例(来自 catalog.json,已联网验证):
    星期二第7-10节{1-2周}<br/>星期三第7-10节{1-2周}
    星期六第1-4,7-10节{1-4周}<br/>星期日第1-4节{1-4周}
    星期一第7-10节{1-3周}<br/>星期三第7-10节{1-3周}<br/>星期五第7-10节{1-2周}   (前后学期分段)
    星期二第1-4节{2-4周(双)}<br/>星期四第1-4节{2-4周(双)}                     (双周)
    星期一第3-4节{1-16周}<br/>星期四第9-10节{1-15周(单)}                      (单周)
    星期三第9-10节{4周,8周,12周,15周}                                         (非连续离散周)
    星期二第3-4,7-8节{1-4周}                                                  (同天非连续节次)

核心思路:把每门课展开成 {(星期, 周次, 节次), ...} 三元组集合,冲突 ⟺ 两个集合有交集。
这天然正确处理"前后学期分段"(周次不重叠就不冲突)和"单双周交替"(周次集合本就不同)。
"""
from __future__ import annotations

import html
import re

_WEEKDAY_TO_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
_NUM_TO_WEEKDAY = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日"}

_SEGMENT_RE = re.compile(
    r"星期(?P<day>[一二三四五六日天])第(?P<periods>[\d,\-]+)节(?:\{(?P<weeks>[^}]*)\})?"
)
_WEEK_TOKEN_RE = re.compile(r"^(\d+)(?:-(\d+))?周(?:\((单|双)\))?$")

_PLACEHOLDER_VALUES = {"", "--", "不排教室", "待定"}


class ScheduleUnknown(Exception):
    """sksj 缺失、为占位符或无法解析——调用方应视为"无法判断",不是"无冲突"。"""


def _expand_periods(spec: str) -> set[int]:
    out: set[int] = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            a, b = token.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(token))
    return out


def _expand_weeks(spec: str) -> set[int]:
    out: set[int] = set()
    for token in spec.split(","):
        token = token.strip()
        m = _WEEK_TOKEN_RE.match(token)
        if not m:
            continue
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else start
        parity = m.group(3)
        weeks = range(start, end + 1)
        if parity == "单":
            out.update(w for w in weeks if w % 2 == 1)
        elif parity == "双":
            out.update(w for w in weeks if w % 2 == 0)
        else:
            out.update(weeks)
    return out


def parse_sksj(sksj: str | None) -> frozenset[tuple[int, int, int]]:
    """解析上课时间字符串为 {(星期, 周次, 节次), ...} 三元组集合。

    解析失败/为空/占位符 → 抛 ScheduleUnknown,调用方应视为"无法判断"而非"无冲突"。
    """
    if not sksj or not str(sksj).strip():
        raise ScheduleUnknown(sksj)
    text = html.unescape(str(sksj)).strip()
    if text in _PLACEHOLDER_VALUES:
        raise ScheduleUnknown(sksj)
    segments = re.split(r"<br\s*/?>|\r?\n", text, flags=re.IGNORECASE)
    slots: set[tuple[int, int, int]] = set()
    matched_any = False
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        m = _SEGMENT_RE.search(seg)
        if not m:
            continue
        day = _WEEKDAY_TO_NUM.get(m.group("day"))
        periods = _expand_periods(m.group("periods"))
        weeks_spec = m.group("weeks")
        weeks = _expand_weeks(weeks_spec) if weeks_spec else set()
        if day is None or not periods or not weeks:
            continue
        matched_any = True
        for week in weeks:
            for period in periods:
                slots.add((day, week, period))
    if not matched_any or not slots:
        raise ScheduleUnknown(sksj)
    return frozenset(slots)


def conflicts(sksj_a: str | None, sksj_b: str | None) -> bool | None:
    """两门课是否时间冲突。True=确定冲突 False=确定不冲突 None=无法判断(至少一侧数据缺失/解析失败)。"""
    try:
        slots_a = parse_sksj(sksj_a)
        slots_b = parse_sksj(sksj_b)
    except ScheduleUnknown:
        return None
    return not slots_a.isdisjoint(slots_b)


def describe_conflict(sksj_a: str | None, sksj_b: str | None) -> str | None:
    """冲突的人类可读描述(取第一个重叠的时段),用于警告/通知文案;无冲突或无法判断则 None。"""
    try:
        slots_a = parse_sksj(sksj_a)
        slots_b = parse_sksj(sksj_b)
    except ScheduleUnknown:
        return None
    common = sorted(slots_a & slots_b)
    if not common:
        return None
    day, week, period = common[0]
    return f"周{_NUM_TO_WEEKDAY.get(day, day)} 第{period}节 (第{week}周)"
