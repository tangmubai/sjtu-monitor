"""JSON bridge for the Tauri desktop UI.

This module keeps GUI-facing data shaping close to the verified Python backend.
The Tauri shell calls it as a subprocess and exchanges UTF-8 JSON over stdout
or stdin. Network access remains in monitor.py/bootstrap.py/course_plus.py.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import config
import secure_store


ROOT = Path(__file__).resolve().parent


def is_release_mode() -> bool:
    return os.getenv("SJTU_MONITOR_RELEASE", "").strip() == "1"


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def short_id(value: str | None) -> str:
    if not value:
        return "-"
    return value[:8] + "..." + value[-6:] if len(value) > 18 else value


def to_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def split_info_lines(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).replace("<br/>", "\n").replace("<br>", "\n")
    return [line.strip() for line in re.split(r"[\n;；]+", text) if line.strip()]


def teacher_names(course: dict[str, Any]) -> str:
    lines = split_info_lines(course.get("jsxx"))
    names: list[str] = []
    for line in lines:
        parts = [part.strip() for part in str(line).split("/") if part.strip()]
        if len(parts) >= 2:
            names.append(parts[1])
        elif parts:
            names.append(parts[0])
    return "、".join(dict.fromkeys(names)) or "-"


def course_title(course: dict[str, Any]) -> str:
    return str(course.get("kcmc") or course.get("kch") or "未命名课程")


def class_suffix(course: dict[str, Any]) -> str:
    value = course.get("jxbmc") or course.get("jxb_id") or ""
    return str(value).strip() or short_id(course.get("jxb_id"))


def summary_value(lines: list[str], empty: str, unit: str) -> str:
    if not lines:
        return empty
    if len(lines) == 1:
        return lines[0]
    return f"{lines[0]} 等 {len(lines)} {unit}"


def course_summary(course: dict[str, Any]) -> str:
    teacher = teacher_names(course)
    schedule = summary_value(split_info_lines(course.get("sksj")), "时间未定", "段")
    location = summary_value(split_info_lines(course.get("jxdd")), "地点未定", "处")
    kch = course.get("kch") or course.get("kch_id") or "-"
    return f"{teacher} / {schedule} / {location} / {kch}"


def course_detail_text(course: dict[str, Any], group: str | None = None) -> str:
    if not course:
        return "未找到教学班详情"
    parts = [
        f"课程: {course_title(course)}",
        f"教学班: {class_suffix(course)}",
        f"课程号: {course.get('kch') or course.get('kch_id') or '-'}",
        f"教学班ID: {course.get('jxb_id') or '-'}",
        f"教师: {teacher_names(course)}",
        f"时间: {'; '.join(split_info_lines(course.get('sksj'))) or '-'}",
        f"地点: {'; '.join(split_info_lines(course.get('jxdd'))) or '-'}",
        f"容量: {seat_text(course)}",
        f"方案: {group or '-'}",
    ]
    return "\n".join(parts)


def course_search_text(course: dict[str, Any]) -> str:
    fields = [
        course.get("kcmc"),
        course.get("kch"),
        course.get("kch_id"),
        course.get("jxb_id"),
        course.get("jxbmc"),
        course.get("jsxx"),
        course.get("sksj"),
        course.get("jxdd"),
        course.get("category"),
    ]
    return " ".join(str(item) for item in fields if item).casefold()


def seat_text(course: dict[str, Any]) -> str:
    selected = course.get("jxbxzrs")
    capacity = course.get("jxbrl")
    if selected is None and capacity is None:
        return "-"
    return f"{selected if selected is not None else '?'} / {capacity if capacity is not None else '?'}"


def has_spot(course: dict[str, Any]) -> bool | None:
    selected = to_int(course.get("jxbxzrs"))
    capacity = to_int(course.get("jxbrl"))
    if selected is None or capacity is None or capacity <= 0:
        return None
    return selected < capacity


def availability(course: dict[str, Any]) -> str:
    explicit = course.get("availability")
    if explicit in {"open", "full", "unknown"}:
        return str(explicit)
    spot = has_spot(course)
    if spot is True:
        return "open"
    if spot is False:
        return "full"
    return "unknown"


def availability_text(value: str) -> str:
    return {"open": "有空位", "full": "已满", "unknown": "未知"}.get(value, "未知")


def normalized_rating(row: dict[str, Any]) -> dict[str, Any]:
    record = row.get("rating")
    error = row.get("rating_error")
    if isinstance(record, dict):
        rating = record.get("rating")
        if isinstance(rating, dict):
            score = rating.get("score")
            if score is None:
                score = rating.get("avg")
            count = rating.get("count")
            status = "empty" if count == 0 else ("rated" if score is not None else "empty")
            return {
                "status": status,
                "score": score,
                "count": count,
                "teacher": record.get("teacher"),
                "semester": record.get("semester"),
                "updated_at": record.get("updated_at"),
                "message": None,
            }
        return {
            "status": "empty",
            "score": None,
            "count": 0,
            "teacher": record.get("teacher"),
            "semester": record.get("semester"),
            "updated_at": record.get("updated_at"),
            "message": None,
        }
    if isinstance(error, dict):
        return {
            "status": "not_found" if error.get("reason") == "not_found" else "failed",
            "score": None,
            "count": None,
            "teacher": None,
            "semester": None,
            "updated_at": error.get("updated_at"),
            "message": error.get("message"),
        }
    return {
        "status": "unknown",
        "score": None,
        "count": None,
        "teacher": None,
        "semester": None,
        "updated_at": None,
        "message": None,
    }


def rating_text(row: dict[str, Any]) -> str:
    rating = normalized_rating(row)
    if rating["status"] == "rated":
        count = rating["count"]
        score = f"{float(rating['score']):.1f}"
        return f"{score} / {count}评" if count is not None else score
    return {
        "empty": "暂无评价",
        "not_found": "未收录",
        "failed": "获取失败",
        "unknown": "-",
    }[rating["status"]]


def held_by_group(completed: set[str]) -> dict[str, str]:
    held = config.initial_held()
    for group, group_cfg in config.PRIORITY_GROUPS.items():
        completed_in_group = [
            jxb_id for jxb_id in group_cfg.get("priority", []) if jxb_id in completed
        ]
        if completed_in_group:
            held[group] = completed_in_group[0]
    return held


def watched_ids(swap_state: dict[str, Any]) -> set[str]:
    completed = set(swap_state.get("completed", []))
    held = held_by_group(completed)
    watched = config.watched_ids(held)
    for group in swap_state.get("fatal_groups", []):
        group_cfg = config.PRIORITY_GROUPS.get(group)
        if group_cfg:
            watched.difference_update(group_cfg.get("priority", []))
    return watched


def merge_priority_ids(
    existing: list[str], new_ids: list[str], chosen_ids: set[str]
) -> list[str]:
    chosen = [jxb_id for jxb_id in existing if jxb_id in chosen_ids]
    targets = [jxb_id for jxb_id in existing if jxb_id not in chosen_ids]
    for jxb_id in new_ids:
        if jxb_id in targets or jxb_id in chosen:
            continue
        if jxb_id in chosen_ids:
            chosen.append(jxb_id)
        else:
            targets.append(jxb_id)
    return targets + chosen


def parse_swap_records(lines: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in lines:
        if "swap_result" not in line:
            continue
        match = re.search(r"(\{.*\})", line)
        if not match:
            continue
        try:
            item = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if item.get("kind") == "swap_result":
            item.setdefault("timestamp", line[:19])
            records.append(item)
    return records


class CourseModel:
    def __init__(self) -> None:
        self.swap_state = read_json(
            config.SWAP_STATE_FILE, {"completed": [], "fatal": [], "fatal_groups": []}
        )
        self.state = read_json(config.STATE_FILE, {})
        self.catalog = read_json(config.CATALOG_FILE, {})
        self.seat_details = read_json(
            config.SEAT_DETAILS_FILE, {"classes": {}, "errors": {}}
        )
        self.ratings = read_json(config.RATINGS_FILE, {"courses": {}, "errors": {}})
        self.groups = config.load_priority_groups()
        self.choosed_ids = {
            course.get("jxb_id")
            for course in self.catalog.get("choosed", [])
            if course.get("jxb_id")
        }
        self.rows_by_id = self._load_rows()

    def _load_rows(self) -> dict[str, dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        for course in self.catalog.get("courses", []):
            for class_info in course.get("classes", []):
                jxb_id = class_info.get("jxb_id")
                if not jxb_id:
                    continue
                rows[jxb_id] = {
                    **class_info,
                    "jxb_id": jxb_id,
                    "kch": class_info.get("kch") or course.get("kch"),
                    "kcmc": class_info.get("kcmc") or course.get("kcmc"),
                    "kch_id": course.get("kch_id"),
                    "kklxdm": class_info.get("kklxdm") or course.get("kklxdm"),
                    "endpoint": course.get("endpoint")
                    or ("pe" if course.get("kklxdm") == "06" else "display"),
                }
        for jxb_id, course in self.state.items():
            rows[jxb_id] = {**rows.get(jxb_id, {}), **course, "jxb_id": jxb_id}
        for jxb_id, detail in self.seat_details.get("classes", {}).items():
            if jxb_id in rows:
                rows[jxb_id]["availability"] = availability(detail)
                for key in ("sksj", "jxdd", "jsxx", "jxbxzrs", "jxbrl"):
                    if detail.get(key) is not None:
                        rows[jxb_id][key] = detail[key]
        for row in rows.values():
            row["category"] = config.KKLX_NAMES.get(str(row.get("kklxdm") or ""), "-")
            kch = row.get("kch") or ""
            row["rating"] = self.ratings.get("courses", {}).get(kch)
            row["rating_error"] = self.ratings.get("errors", {}).get(kch)
        return rows

    def group_of(self, jxb_id: str | None) -> str | None:
        if not jxb_id:
            return None
        for name, group in self.groups.items():
            if jxb_id in group.get("priority", []):
                return name
        return None

    def label_for(self, jxb_id: str | None, fallback: dict[str, Any] | None = None) -> str:
        if not jxb_id:
            return "-"
        row = {**(fallback or {}), **self.rows_by_id.get(jxb_id, {})}
        if not any(row.get(key) for key in ("kcmc", "jsxx", "jxbmc", "kch")):
            return short_id(jxb_id)
        return f"{course_title(row)} - {class_suffix(row)}"

    def course_rows(self) -> list[dict[str, Any]]:
        rows = []
        for jxb_id, row in self.rows_by_id.items():
            group = self.group_of(jxb_id)
            item = {
                **row,
                "title": course_title(row),
                "class_name": class_suffix(row),
                "summary": course_summary(row),
                "detail": course_detail_text(row, group),
                "teachers": teacher_names(row),
                "schedule": split_info_lines(row.get("sksj")),
                "locations": split_info_lines(row.get("jxdd")),
                "search_text": course_search_text(row),
                "seat_text": seat_text(row),
                "availability": availability(row),
                "availability_text": availability_text(availability(row)),
                "group": group,
                "chosen": jxb_id in self.choosed_ids,
                "rating": normalized_rating(row),
                "rating_text": rating_text(row),
            }
            rows.append(item)
        return sorted(
            rows,
            key=lambda item: (
                str(item.get("category") or ""),
                str(item.get("title") or ""),
                teacher_names(item),
                str(item.get("sksj") or ""),
                str(item.get("class_name") or ""),
            ),
        )


def build_snapshot() -> dict[str, Any]:
    model = CourseModel()
    watched = watched_ids(model.swap_state)
    held = held_by_group(set(model.swap_state.get("completed", [])))
    log_lines = []
    if config.LOG_FILE.exists():
        log_lines = config.LOG_FILE.read_text("utf-8", errors="replace").splitlines()
    swap_records = list(reversed(parse_swap_records(log_lines)))
    user = model.catalog.get("user") or {}
    courses = model.course_rows()
    open_count = sum(1 for row in courses if row["availability"] == "open")
    fatal_groups = set(model.swap_state.get("fatal_groups", []))
    groups = []
    for name, group in model.groups.items():
        ids = list(group.get("priority", []))
        groups.append(
            {
                "name": name,
                "is_pe": bool(group.get("is_pe")),
                "priority": ids,
                "held": held.get(name),
                "held_label": model.label_for(held.get(name)),
                "watched_count": sum(jxb_id in watched for jxb_id in ids),
                "fatal": name in fatal_groups,
                "members": [
                    {
                        "jxb_id": jxb_id,
                        "label": model.label_for(jxb_id),
                        "detail": course_detail_text(model.rows_by_id.get(jxb_id, {}), name),
                        "chosen": jxb_id in model.choosed_ids,
                        "watched": jxb_id in watched,
                        "availability": availability(model.rows_by_id.get(jxb_id, {})),
                    }
                    for jxb_id in ids
                ],
            }
        )
    state_rows = []
    for jxb_id, row in model.state.items():
        merged = {**model.rows_by_id.get(jxb_id, {}), **row, "jxb_id": jxb_id}
        state_rows.append(
            {
                "jxb_id": jxb_id,
                "watched": jxb_id in watched,
                "group": model.group_of(jxb_id),
                "title": course_title(merged),
                "summary": course_summary(merged),
                "seat_text": seat_text(merged),
                "open": has_spot(merged),
            }
        )
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "metrics": {
            "queries": len(config.KCH_QUERIES),
            "groups": len(model.groups),
            "snapshot": len(model.state),
            "watched": len(watched),
            "open_courses": open_count,
            "interval": f"{config.POLL_MIN}-{config.POLL_MAX}s",
            "auto_swap": "off"
            if not config.AUTO_SWAP
            else ("dry_run" if config.AUTO_SWAP_DRY_RUN else "enabled"),
        },
        "settings": {
            "jaccount_user": config.JACCOUNT_USER,
            "jaccount_pass": "",
            "course_plus_password": "",
            "has_jaccount_pass": bool(config.JACCOUNT_PASS),
            "has_course_plus_password": bool(config.COURSE_PLUS_PASSWORD),
            "poll_min": config.POLL_MIN,
            "poll_max": config.POLL_MAX,
            "email_enabled": config.EMAIL_ENABLED,
            "smtp_host": config.SMTP_HOST,
            "smtp_port": config.SMTP_PORT,
            "smtp_user": config.SMTP_USER,
            "smtp_pass": "",
            "has_smtp_pass": bool(config.SMTP_PASS),
            "secret_backend": secure_store.backend_name(),
            "mail_from": config.MAIL_FROM,
            "mail_to": config.MAIL_TO,
        },
        "onboarding": {
            "completed": bool(config.USER_SETTINGS["onboarding"]["completed"]),
            "has_account": bool(config.JACCOUNT_USER and config.JACCOUNT_PASS),
            "catalog_ready": bool(
                model.catalog.get("courses") or model.catalog.get("choosed")
            ),
        },
        "user": {
            "name": user.get("xm") or "-",
            "student_id": user.get("xh") or "-",
            "class_name": user.get("bjmc") or "-",
            "major": user.get("zymc") or "-",
            "term": f"{config.XKXNM}-{config.XKXQM}",
            "catalog_fetched_at": model.catalog.get("fetched_at"),
        },
        "groups": groups,
        "courses": courses,
        "state_rows": sorted(state_rows, key=lambda row: (not row["watched"], row["title"])),
        "swap_state": model.swap_state,
        "swap_history": swap_records[:80],
        "logs": [f"[changes] {line}" for line in log_lines[-300:]],
        "categories": sorted({row.get("category") or "-" for row in courses}),
    }


def find_duplicate_assignments(groups: dict[str, Any]) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for name, group in groups.items():
        for jxb_id in group.get("priority", []):
            owners.setdefault(jxb_id, []).append(name)
    return {jxb_id: names for jxb_id, names in owners.items() if len(names) > 1}


def group_setup_warnings(groups: dict[str, Any], choosed_ids: set[str]) -> list[str]:
    if not choosed_ids:
        return []
    warnings = []
    for name, group in groups.items():
        ids = group.get("priority", [])
        if not ids:
            continue
        selected = [jxb_id for jxb_id in ids if jxb_id in choosed_ids]
        if not selected:
            warnings.append(f"{name} 没有包含当前已选教学班")
        elif len(selected) > 1:
            warnings.append(f"{name} 包含 {len(selected)} 个当前已选教学班，无法唯一确定当前持有")
        elif ids[-1] not in selected:
            warnings.append(f"{name} 的最后一项不是当前已选教学班")
    return warnings


def derive_courses(groups: dict[str, Any], rows_by_id: dict[str, dict[str, Any]]) -> tuple[dict, list[str]]:
    derived: dict[str, dict] = {}
    unresolved: list[str] = []
    for group in groups.values():
        for jxb_id in group.get("priority", []):
            row = rows_by_id.get(jxb_id) or {}
            kch = row.get("kch")
            if row.get("kch_id"):
                endpoint = row.get("endpoint") or (
                    "pe" if row.get("kklxdm") == "06" else "display"
                )
                entry: dict[str, Any] = {"endpoint": endpoint, "kch_id": row["kch_id"]}
                if endpoint == "display":
                    entry["jxb_id"] = jxb_id
                elif endpoint == "zzxk":
                    entry["kklxdm"] = str(row.get("kklxdm") or "")
                derived.setdefault(kch or row["kch_id"], entry)
            elif kch and kch in config.KCH_QUERIES:
                derived.setdefault(kch, config.KCH_QUERIES[kch])
            else:
                unresolved.append(jxb_id)
    return derived, unresolved


def save_groups(payload: dict[str, Any]) -> dict[str, Any]:
    groups = payload.get("groups")
    if not isinstance(groups, dict):
        raise ValueError("groups must be an object")
    normalized = {
        str(name): {
            "is_pe": bool(group.get("is_pe")),
            "priority": [str(jxb_id) for jxb_id in group.get("priority", []) if jxb_id],
        }
        for name, group in groups.items()
        if str(name).strip()
    }
    model = CourseModel()
    duplicates = find_duplicate_assignments(normalized)
    warnings = group_setup_warnings(normalized, model.choosed_ids)
    derived, unresolved = derive_courses(normalized, model.rows_by_id)
    sections: dict[str, Any] = {"priority_groups": normalized}
    if derived:
        sections["courses"] = derived
    config.update_user_settings(**sections)
    return {
        "ok": True,
        "warnings": warnings,
        "duplicates": duplicates,
        "unresolved": unresolved,
        "course_count": len(derived),
    }


def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    poll_min = int(payload.get("poll_min"))
    poll_max = int(payload.get("poll_max"))
    smtp_port = int(payload.get("smtp_port"))
    if poll_min <= 0 or poll_max <= 0 or poll_max < poll_min:
        raise ValueError("poll interval must be positive and max >= min")
    if not 1 <= smtp_port <= 65535:
        raise ValueError("smtp_port must be in 1..65535")
    env_values = {
        "JACCOUNT_USER": str(payload.get("jaccount_user", "")).strip(),
        "POLL_MIN": str(poll_min),
        "POLL_MAX": str(poll_max),
        "SMTP_HOST": str(payload.get("smtp_host", "")).strip(),
        "SMTP_PORT": str(smtp_port),
        "SMTP_USER": str(payload.get("smtp_user", "")).strip(),
        "MAIL_FROM": str(payload.get("mail_from", "")).strip(),
        "MAIL_TO": str(payload.get("mail_to", "")).strip(),
    }
    secret_fields = {
        "JACCOUNT_PASS": "jaccount_pass",
        "COURSE_PLUS_PASSWORD": "course_plus_password",
        "SMTP_PASS": "smtp_pass",
    }
    for env_key, payload_key in secret_fields.items():
        value = str(payload.get(payload_key, ""))
        if value:
            env_values[env_key] = value
    config.save_env_settings(env_values)
    config.update_user_settings(
        notifications={"email_enabled": bool(payload.get("email_enabled"))}
    )
    return {"ok": True}


def set_auto_swap(payload: dict[str, Any]) -> dict[str, Any]:
    dry_run = bool(payload.get("dry_run"))
    if is_release_mode() and dry_run:
        raise ValueError("发行版不支持演练模式")
    config.update_user_settings(
        auto_swap={
            "enabled": bool(payload.get("enabled")),
            "dry_run": dry_run,
        }
    )
    return {"ok": True}


def complete_onboarding() -> dict[str, Any]:
    if not config.JACCOUNT_USER or not config.JACCOUNT_PASS:
        raise ValueError("请先保存 JAccount 账号和密码")
    catalog = read_json(config.CATALOG_FILE, {})
    if not catalog.get("courses") and not catalog.get("choosed"):
        raise ValueError("请先同步课程目录")
    config.update_user_settings(onboarding={"completed": True})
    return {"ok": True}


def read_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("payload must be a JSON object")
    return data


def emit(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SJTU Monitor Tauri JSON bridge")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot")
    sub.add_parser("save-settings")
    sub.add_parser("save-groups")
    sub.add_parser("set-auto-swap")
    sub.add_parser("complete-onboarding")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "snapshot":
            emit(build_snapshot())
        elif args.cmd == "save-settings":
            emit(save_settings(read_payload()))
        elif args.cmd == "save-groups":
            emit(save_groups(read_payload()))
        elif args.cmd == "set-auto-swap":
            emit(set_auto_swap(read_payload()))
        elif args.cmd == "complete-onboarding":
            emit(complete_onboarding())
        return 0
    except Exception as exc:
        emit({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
