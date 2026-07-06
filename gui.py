"""Modern Qt desktop interface for the SJTU course monitor.

The UI remains a thin desktop shell around the verified command-line programs.
It reads local runtime files and launches monitor.py/bootstrap.py as child
processes; network and course-selection behavior stays in the existing backend.
"""
from __future__ import annotations

import json
import html
import re
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QItemSelectionModel,
    QProcess,
    QProcessEnvironment,
    QTimer,
    Qt,
)
from PySide6.QtGui import QColor, QCloseEvent, QFont, QFontDatabase, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFrame,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import config
import timetable


ROOT = Path(__file__).resolve().parent
USER_ROLE = Qt.ItemDataRole.UserRole

COLORS = {
    "bg": "#F3F6FA",
    "surface": "#FFFFFF",
    "surface_alt": "#F7F9FC",
    "border": "#DDE4EE",
    "text": "#182230",
    "muted": "#637083",
    "primary": "#2F6FED",
    "primary_hover": "#245CC5",
    "primary_soft": "#EAF1FF",
    "success": "#15803D",
    "success_soft": "#E9F7EF",
    "warning": "#B45309",
    "warning_soft": "#FFF4E5",
    "danger": "#C2413A",
    "danger_soft": "#FDECEC",
    "console": "#111827",
}


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _short_id(value: str | None) -> str:
    if not value:
        return ""
    return value if len(value) <= 14 else f"{value[:8]}…{value[-6:]}"


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _seat_text(course: dict) -> str:
    selected = course.get("jxbxzrs")
    capacity = course.get("jxbrl")
    if selected is None and capacity is None:
        return "—"
    return f"{selected or '?'} / {capacity or '?'}"


def _has_spot(course: dict) -> bool | None:
    selected = _to_int(course.get("jxbxzrs"))
    capacity = _to_int(course.get("jxbrl"))
    if selected is None or capacity is None or capacity <= 0:
        return None
    return selected < capacity


def _availability(course: dict) -> str:
    value = course.get("availability")
    if value in {"open", "full", "unknown"}:
        return value
    spot = _has_spot(course)
    return "open" if spot is True else ("full" if spot is False else "unknown")


def _availability_text(value: str) -> str:
    return {"open": "有空位", "full": "已满", "unknown": "未知"}.get(value, "未知")


def _split_info_lines(value) -> list[str]:
    if value is None:
        return []
    text = html.unescape(str(value))
    parts = re.split(r"<br\s*/?>|\r?\n", text, flags=re.IGNORECASE)
    return [
        re.sub(r"\s+", " ", part).strip()
        for part in parts
        if re.sub(r"\s+", " ", part).strip() not in {"", "--", "不排教室"}
    ]


def _teacher_details(course: dict) -> list[tuple[str, str]]:
    teachers: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in _split_info_lines(str(course.get("jsxx") or "").replace(";", "<br/>")):
        parts = [part.strip() for part in raw.split("/") if part.strip()]
        name = parts[1] if len(parts) >= 2 else (parts[0] if parts else "")
        title = "/".join(parts[2:]) if len(parts) >= 3 else ""
        item = (name, title)
        if name and item not in seen:
            seen.add(item)
            teachers.append(item)
    return teachers


def _teacher_names(course: dict) -> str:
    names = [name for name, _title in _teacher_details(course)]
    return "、".join(names) if names else "教师待定"


def _course_title(course: dict) -> str:
    name = str(course.get("kcmc") or "").strip() or "课程名称待定"
    return f"{name} · {_teacher_names(course)}"


def _class_suffix(course: dict) -> str:
    jxbmc = str(course.get("jxbmc") or "").strip()
    match = re.search(r"-([A-Za-z0-9]+)\)?$", jxbmc)
    return f"{match.group(1)}班" if match else "班级待定"


def _summary_value(lines: list[str], empty: str, unit: str) -> str:
    if not lines:
        return empty
    suffix = f"（另 {len(lines) - 1} 个{unit}）" if len(lines) > 1 else ""
    return f"{lines[0]}{suffix}"


def _course_summary(course: dict) -> str:
    times = _split_info_lines(course.get("sksj"))
    places = _split_info_lines(course.get("jxdd"))
    time_text = _summary_value(times, "时间待定", "时段")
    place_text = _summary_value(places, "地点待定", "地点")
    code = str(course.get("kch") or "").strip() or "课程号待定"
    return f"{time_text} · {place_text} · {code} · {_class_suffix(course)}"


def _course_search_text(course: dict) -> str:
    values = (
        course.get("kcmc"),
        course.get("kch"),
        course.get("jxbmc"),
        course.get("jxb_id"),
        course.get("jsxx"),
        course.get("sksj"),
        course.get("jxdd"),
    )
    return " ".join(html.unescape(str(value or "")) for value in values).casefold()


def _course_detail_text(course: dict, group: str | None = None) -> str:
    teachers = _teacher_details(course)
    teacher_text = "、".join(
        f"{name}（{title}）" if title else name for name, title in teachers
    ) or "教师待定"
    times = _split_info_lines(course.get("sksj"))
    places = _split_info_lines(course.get("jxdd"))
    schedule = []
    for index in range(max(len(times), len(places), 1)):
        time_text = times[index] if index < len(times) else "时间待定"
        place_text = places[index] if index < len(places) else "地点待定"
        schedule.append(f"{index + 1}. {time_text} ｜ {place_text}")
    return "\n".join(
        [
            _course_title(course),
            f"教师：{teacher_text}",
            "时间与地点：",
            *schedule,
            (
                f"课程号：{course.get('kch') or '待定'}    "
                f"分类：{course.get('category') or '—'}    "
                f"空位：{_availability_text(_availability(course))}"
            ),
            f"教学班：{course.get('jxbmc') or '待定'}",
            f"教学班 ID：{course.get('jxb_id') or '待定'}",
            f"所在方案：{group or '未加入'}",
        ]
    )


def _held_by_group(completed: set[str]) -> dict[str, str]:
    held = config.initial_held()
    for group, group_cfg in config.PRIORITY_GROUPS.items():
        completed_in_group = [
            jxb_id for jxb_id in group_cfg["priority"] if jxb_id in completed
        ]
        if completed_in_group:
            held[group] = completed_in_group[0]
    return held


def _watched_ids(swap_state: dict) -> set[str]:
    completed = set(swap_state.get("completed", []))
    held = _held_by_group(completed)
    watched = config.watched_ids(held)
    for group in swap_state.get("fatal_groups", []):
        group_cfg = config.PRIORITY_GROUPS.get(group)
        if group_cfg:
            watched.difference_update(group_cfg["priority"])
    return watched


def _merge_priority_ids(
    existing: list[str],
    additions: list[str],
    chosen_ids: set[str],
) -> list[str]:
    """合并教学班，始终保持目标在前、当前已选在末尾。

    已存在项的相对优先级不会因重复加入而改变；新增目标排在当前持有项之前。
    """
    merged = list(dict.fromkeys([*existing, *additions]))
    targets = [jxb_id for jxb_id in merged if jxb_id not in chosen_ids]
    held = [jxb_id for jxb_id in merged if jxb_id in chosen_ids]
    return [*targets, *held]


def _layout_mode(width: int) -> str:
    """Return the centralized responsive tier for a window width."""
    if width >= 1200:
        return "wide"
    if width >= 900:
        return "medium"
    return "narrow"


def _metric_columns(mode: str) -> int:
    return {"wide": 6, "medium": 3, "narrow": 2}[mode]


def _parse_swap_records(lines: list[str]) -> list[dict]:
    records = []
    for line in lines:
        timestamp, separator, payload = line.partition(" ")
        if not separator:
            continue
        try:
            item = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if item.get("kind") == "swap_result":
            records.append({"timestamp": timestamp, **item})
    return records


def _message(
    parent: QWidget,
    icon: QMessageBox.Icon,
    title: str,
    text: str,
    *,
    ask: bool = False,
) -> bool:
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    if ask:
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes
    box.exec()
    return True


class MetricCard(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(5)
        title_label = QLabel(title)
        title_label.setObjectName("muted")
        self.value_label = QLabel("—")
        self.value_label.setObjectName("metricValue")
        layout.addWidget(title_label)
        layout.addWidget(self.value_label)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class MonitorWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("交我选监控")
        self.resize(1320, 840)
        self.setMinimumSize(760, 600)

        self.monitor_process: QProcess | None = None
        self.once_process: QProcess | None = None
        self.bootstrap_process: QProcess | None = None
        self.seat_process: QProcess | None = None
        self._seat_pending: set[str] = set()
        self._seat_active: set[str] = set()
        self._runtime_lines: list[str] = []
        self._settings_loaded = False
        self._groups_dirty = False
        self._refreshing_swap_options = False
        self._course_rows: list[dict] = []
        self._course_by_id: dict[str, dict] = {}
        self._seat_details_by_id: dict[str, dict] = {}
        self._seat_errors: dict[str, str] = {}
        self._seat_runtime_errors: dict[str, str] = {}
        self._choosed_ids: set[str] = set()
        self.groups: dict[str, dict] = {}
        self.selected_group: str | None = None
        self._layout_mode: str | None = None
        self._snapshot_state: dict[str, dict] = {}
        self._snapshot_watched: set[str] = set()

        self._build_ui()
        self._load_groups_from_config()
        self.refresh()

    # ---------- shell ----------

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(220)
        side_layout = QVBoxLayout(self.sidebar)
        side_layout.setContentsMargins(18, 24, 18, 18)
        side_layout.setSpacing(12)

        self.brand = QLabel("交我选监控")
        self.brand.setObjectName("brand")
        self.sidebar_subtitle = QLabel("课程监控与自动换课")
        self.sidebar_subtitle.setObjectName("sidebarMuted")
        side_layout.addWidget(self.brand)
        side_layout.addWidget(self.sidebar_subtitle)
        side_layout.addSpacing(18)

        self.nav = QListWidget()
        self.nav.setObjectName("navigation")
        self.nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for text in (
            "运行概览", "选课设置", "换课记录",
            "课程快照", "运行日志", "用户设置",
        ):
            self.nav.addItem(QListWidgetItem(text))
        side_layout.addWidget(self.nav)
        side_layout.addStretch()
        self.sidebar_version = QLabel("Qt Desktop · 本地运行")
        self.sidebar_version.setObjectName("sidebarMuted")
        side_layout.addWidget(self.sidebar_version)
        shell.addWidget(self.sidebar)

        content = QWidget()
        content.setObjectName("content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 18, 24, 14)
        content_layout.setSpacing(14)

        topbar = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.page_title = QLabel("运行概览")
        self.page_title.setObjectName("pageTitle")
        self.page_hint = QLabel("查看监控状态并控制后台任务")
        self.page_hint.setObjectName("muted")
        title_box.addWidget(self.page_title)
        title_box.addWidget(self.page_hint)
        topbar.addLayout(title_box)
        topbar.addStretch()
        self.monitor_badge = QLabel("●  未运行")
        self.monitor_badge.setObjectName("statusBadge")
        self.monitor_badge.setProperty("running", False)
        topbar.addWidget(self.monitor_badge)
        content_layout.addLayout(topbar)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._scroll_page(self._build_overview_page()))
        self.stack.addWidget(self._scroll_page(self._build_course_page()))
        self.stack.addWidget(self._scroll_page(self._build_swap_page()))
        self.stack.addWidget(self._scroll_page(self._build_snapshot_page()))
        self.stack.addWidget(self._scroll_page(self._build_log_page()))
        self.stack.addWidget(self._scroll_page(self._build_settings_page()))
        content_layout.addWidget(self.stack, 1)

        status_row = QHBoxLayout()
        self.status_label = QLabel("●  就绪")
        self.status_label.setObjectName("statusText")
        self.shortcuts_label = QLabel("F5 刷新  ·  Ctrl+Enter 运行一次")
        self.shortcuts_label.setObjectName("muted")
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        status_row.addWidget(self.shortcuts_label)
        content_layout.addLayout(status_row)
        shell.addWidget(content, 1)

        self.nav.currentRowChanged.connect(self._switch_page)
        self.nav.setCurrentRow(0)
        self._install_shortcuts()
        self._apply_responsive_layout(self.width())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._apply_responsive_layout(event.size().width())

    def _apply_responsive_layout(self, width: int) -> None:
        mode = _layout_mode(width)
        if mode == self._layout_mode:
            return
        self._layout_mode = mode
        compact = mode != "wide"
        narrow = mode == "narrow"
        self.sidebar.setFixedWidth(76 if compact else 220)
        self.brand.setText("交我选" if compact else "交我选监控")
        self.brand.setAlignment(
            Qt.AlignmentFlag.AlignCenter if compact else Qt.AlignmentFlag.AlignLeft
        )
        self.sidebar_subtitle.setVisible(not compact)
        self.sidebar_version.setVisible(not compact)
        self.shortcuts_label.setVisible(not compact)
        full_names = (
            "运行概览", "选课设置", "换课记录",
            "课程快照", "运行日志", "用户设置",
        )
        short_names = ("概览", "选课", "换课", "快照", "日志", "设置")
        for index, text in enumerate(short_names if compact else full_names):
            self.nav.item(index).setText(text)
            self.nav.item(index).setTextAlignment(
                Qt.AlignmentFlag.AlignCenter
                if compact
                else Qt.AlignmentFlag.AlignVCenter
            )
        if hasattr(self, "course_splitter"):
            self.course_splitter.setOrientation(
                Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal
            )
            self.course_splitter.setSizes(
                [360, 360] if narrow else ([560, 440] if compact else [700, 500])
            )
        if hasattr(self, "metrics_layout"):
            for index, card in enumerate(self.metrics.values()):
                self.metrics_layout.removeWidget(card)
                columns = _metric_columns(mode)
                self.metrics_layout.addWidget(card, index // columns, index % columns)
        direction = (
            QBoxLayout.Direction.TopToBottom
            if narrow
            else QBoxLayout.Direction.LeftToRight
        )
        for name in (
            "overview_actions",
            "overview_details",
            "steps_layout",
            "data_actions",
            "course_filters",
            "course_footer",
            "plan_group_actions",
            "plan_member_actions",
            "save_actions",
            "swap_options",
        ):
            layout = getattr(self, name, None)
            if layout is not None:
                self._set_box_direction(layout, direction, narrow)
        if hasattr(self, "course_table"):
            self.course_table.setColumnHidden(2, narrow)
            self.member_table.setColumnHidden(3, narrow)
            self.swap_table.setColumnHidden(1, narrow)
            self.swap_table.setColumnHidden(4, narrow)
            self.snapshot_table.setColumnHidden(1, narrow)
            self.snapshot_table.setColumnHidden(3, narrow)
        if hasattr(self, "page_hint"):
            self.page_hint.setVisible(not narrow)
        if hasattr(self, "course_splitter"):
            self.course_splitter.setMinimumHeight(680 if narrow else 360)

    @staticmethod
    def _set_box_direction(
        layout: QBoxLayout,
        direction: QBoxLayout.Direction,
        narrow: bool,
    ) -> None:
        layout.setDirection(direction)
        for index in range(layout.count()):
            spacer = layout.itemAt(index).spacerItem()
            if spacer is not None:
                spacer.changeSize(
                    0,
                    0,
                    QSizePolicy.Policy.Minimum
                    if narrow
                    else QSizePolicy.Policy.Expanding,
                    QSizePolicy.Policy.Minimum,
                )
        layout.invalidate()

    @staticmethod
    def _scroll_page(page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll

    def _install_shortcuts(self) -> None:
        from PySide6.QtGui import QKeySequence, QShortcut

        QShortcut(QKeySequence("F5"), self, activated=self.refresh)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.refresh)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.run_once)

    def _switch_page(self, index: int) -> None:
        titles = (
            ("运行概览", "查看监控状态并控制后台任务"),
            ("选课设置", "按方案组织教学班，并直观调整升级优先级"),
            ("换课记录", "查看自动换课模式、执行结果和异常记录"),
            ("课程快照", "查看最近一次本地课程人数快照"),
            ("运行日志", "查看课程变更与后台任务输出"),
            ("用户设置", "管理账号、轮询间隔和邮件通知"),
        )
        self.stack.setCurrentIndex(index)
        self.page_title.setText(titles[index][0])
        self.page_hint.setText(titles[index][1])

    # ---------- reusable widgets ----------

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        return page, layout

    def _card(self, *, horizontal: bool = False) -> tuple[QFrame, QVBoxLayout | QHBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        layout_cls = QHBoxLayout if horizontal else QVBoxLayout
        layout = layout_cls(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        return card, layout

    def _button(
        self,
        text: str,
        callback,
        *,
        role: str = "secondary",
    ) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("role", role)
        button.clicked.connect(callback)
        return button

    def _table(
        self,
        headers: tuple[str, ...],
        *,
        multi: bool = False,
    ) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
            if multi
            else QAbstractItemView.SelectionMode.SingleSelection
        )
        table.horizontalHeader().setHighlightSections(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        return table

    @staticmethod
    def _item(text: str, data=None) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setToolTip(text)
        if data is not None:
            item.setData(USER_ROLE, data)
        return item

    @staticmethod
    def _set_row_tint(table: QTableWidget, row: int, color: str, foreground: str) -> None:
        for column in range(table.columnCount()):
            item = table.item(row, column)
            if item:
                item.setBackground(QColor(color))
                item.setForeground(QColor(foreground))

    # ---------- overview ----------

    def _build_overview_page(self) -> QWidget:
        page, layout = self._page()

        actions, action_layout = self._card(horizontal=True)
        self.overview_actions = action_layout
        heading = QVBoxLayout()
        heading.setSpacing(2)
        label = QLabel("运行控制")
        label.setObjectName("sectionTitle")
        hint = QLabel("监控逻辑继续由 monitor.py 统一执行")
        hint.setObjectName("muted")
        heading.addWidget(label)
        heading.addWidget(hint)
        action_layout.addLayout(heading)
        action_layout.addStretch()
        self.debug_check = QCheckBox("调试输出")
        action_layout.addWidget(self.debug_check)
        action_layout.addWidget(self._button("刷新", self.refresh))
        self.stop_button = self._button("停止", self.stop_monitor, role="danger")
        self.once_button = self._button("运行一次", self.run_once)
        self.start_button = self._button("启动监控", self.start_monitor, role="primary")
        action_layout.addWidget(self.stop_button)
        action_layout.addWidget(self.once_button)
        action_layout.addWidget(self.start_button)
        layout.addWidget(actions)

        self.metrics_layout = QGridLayout()
        self.metrics_layout.setSpacing(10)
        self.metrics: dict[str, MetricCard] = {}
        for key, title in (
            ("queries", "监控课程"),
            ("groups", "选课方案"),
            ("watched", "当前目标"),
            ("snapshot", "快照班级"),
            ("interval", "轮询间隔"),
            ("swap", "自动换课"),
        ):
            card = MetricCard(title)
            self.metrics[key] = card
            self.metrics_layout.addWidget(card, 0, len(self.metrics) - 1)
        layout.addLayout(self.metrics_layout)

        self.overview_details = QHBoxLayout()
        plan_card, plan_layout = self._card()
        label = QLabel("监控方案摘要")
        label.setObjectName("sectionTitle")
        plan_layout.addWidget(label)
        self.overview_plan_table = self._table(
            ("方案组", "当前持有", "监控目标", "状态")
        )
        plan_layout.addWidget(self.overview_plan_table)
        self.overview_details.addWidget(plan_card, 3)

        activity_card, activity_layout = self._card()
        label = QLabel("最近活动")
        label.setObjectName("sectionTitle")
        activity_layout.addWidget(label)
        self.recent_activity = QListWidget()
        self.recent_activity.setObjectName("activityList")
        self.recent_activity.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        activity_layout.addWidget(self.recent_activity)
        self.overview_details.addWidget(activity_card, 2)
        layout.addLayout(self.overview_details, 1)
        return page

    # ---------- course setup ----------

    def _build_course_page(self) -> QWidget:
        page, layout = self._page()

        self.steps_layout = QHBoxLayout()
        self.steps_layout.setSpacing(10)
        for number, title, hint in (
            ("1", "准备课程数据", "首次使用时自动获取"),
            ("2", "选择教学班", "从课程目录加入方案"),
            ("3", "调整并保存", "上方优先，底部为当前持有"),
        ):
            card, card_layout = self._card(horizontal=True)
            badge = QLabel(number)
            badge.setObjectName("stepBadge")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            copy = QVBoxLayout()
            copy.setSpacing(2)
            title_label = QLabel(title)
            title_label.setObjectName("sectionTitle")
            hint_label = QLabel(hint)
            hint_label.setObjectName("muted")
            copy.addWidget(title_label)
            copy.addWidget(hint_label)
            card_layout.addWidget(badge)
            card_layout.addLayout(copy)
            card_layout.addStretch()
            self.steps_layout.addWidget(card)
        layout.addLayout(self.steps_layout)

        data_card, data_layout = self._card(horizontal=True)
        self.data_actions = data_layout
        data_copy = QVBoxLayout()
        data_copy.setSpacing(2)
        title = QLabel("课程数据")
        title.setObjectName("sectionTitle")
        self.catalog_info = QLabel("正在读取本地课程数据…")
        self.catalog_info.setObjectName("muted")
        data_copy.addWidget(title)
        data_copy.addWidget(self.catalog_info)
        data_layout.addLayout(data_copy)
        data_layout.addStretch()
        self.bootstrap_button = self._button("自动获取用户与课程", self.run_bootstrap)
        data_layout.addWidget(self.bootstrap_button)
        layout.addWidget(data_card)

        self.course_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.course_splitter.setChildrenCollapsible(False)
        self.course_splitter.addWidget(self._build_catalog_panel())
        self.course_splitter.addWidget(self._build_plan_panel())
        self.course_splitter.setStretchFactor(0, 3)
        self.course_splitter.setStretchFactor(1, 2)
        self.course_splitter.setSizes([700, 500])
        layout.addWidget(self.course_splitter, 1)

        self.save_actions = QHBoxLayout()
        self.save_state_label = QLabel("配置已保存")
        self.save_state_label.setObjectName("savedBadge")
        self.save_actions.addWidget(self.save_state_label)
        self.save_actions.addStretch()
        self.save_actions.addWidget(
            self._button("恢复内置默认", self._reset_default_groups)
        )
        self.save_actions.addWidget(
            self._button("放弃未保存修改", self._reload_groups)
        )
        self.save_groups_button = self._button(
            "保存选课方案", self._save_groups, role="primary"
        )
        self.save_groups_button.setEnabled(False)
        self.save_actions.addWidget(self.save_groups_button)
        layout.addLayout(self.save_actions)
        return page

    def _build_catalog_panel(self) -> QFrame:
        panel, layout = self._card()
        title_row = QHBoxLayout()
        title = QLabel("课程目录")
        title.setObjectName("sectionTitle")
        self.course_count_label = QLabel("0 个教学班")
        self.course_count_label.setObjectName("muted")
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self.course_count_label)
        layout.addLayout(title_row)

        self.course_filters = QHBoxLayout()
        self.category_filter = QComboBox()
        self.category_filter.addItem("全部分类", None)
        self.category_filter.currentIndexChanged.connect(self._refresh_course_table)
        self.course_filter = QLineEdit()
        self.course_filter.setPlaceholderText(
            "搜索课程、教师、编号、时间或地点…"
        )
        self.course_filter.setClearButtonEnabled(True)
        # 全量目录有数百行,搜索按键做 200ms 去抖,避免每次击键都全表重建
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(200)
        self._filter_timer.timeout.connect(self._refresh_course_table)
        self.course_filter.textChanged.connect(
            lambda _text: self._filter_timer.start()
        )
        self.only_unassigned = QCheckBox("仅未加入方案")
        self.only_open = QCheckBox("仅有空位")
        self.only_unassigned.toggled.connect(self._refresh_course_table)
        self.only_open.toggled.connect(self._refresh_course_table)
        self.course_filters.addWidget(self.category_filter)
        self.course_filters.addWidget(self.course_filter, 1)
        self.course_filters.addWidget(self.only_unassigned)
        self.course_filters.addWidget(self.only_open)
        layout.addLayout(self.course_filters)

        self.course_table = self._table(
            ("课程信息", "空位状态", "所在方案"), multi=True
        )
        self.course_table.setWordWrap(True)
        self.course_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        header = self.course_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.course_table.itemSelectionChanged.connect(self._on_course_selection)
        self.course_table.itemDoubleClicked.connect(
            lambda _item: self._add_selected_to_group()
        )
        layout.addWidget(self.course_table, 1)

        detail_frame = QFrame()
        detail_frame.setObjectName("detailPanel")
        detail_layout = QVBoxLayout(detail_frame)
        detail_layout.setContentsMargins(12, 10, 12, 10)
        detail_title = QLabel("课程完整信息")
        detail_title.setObjectName("sectionTitle")
        self.course_detail_label = QLabel("选择一个教学班后在此显示完整信息")
        self.course_detail_label.setObjectName("detailText")
        self.course_detail_label.setWordWrap(True)
        self.course_detail_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        detail_layout.addWidget(detail_title)
        detail_layout.addWidget(self.course_detail_label)
        layout.addWidget(detail_frame)

        self.course_footer = QHBoxLayout()
        self.course_selection_label = QLabel("可多选；双击也可快速加入当前方案")
        self.course_selection_label.setObjectName("muted")
        self.add_course_button = self._button(
            "加入当前方案  →", self._add_selected_to_group, role="primary"
        )
        self.add_course_button.setEnabled(False)
        self.course_footer.addWidget(self.course_selection_label, 1)
        self.course_footer.addWidget(self.add_course_button)
        layout.addLayout(self.course_footer)
        return panel

    def _build_plan_panel(self) -> QFrame:
        panel, layout = self._card()
        title = QLabel("我的选课方案")
        title.setObjectName("sectionTitle")
        hint = QLabel("每组代表同一门课，或只能保留一个的课程")
        hint.setObjectName("muted")
        layout.addWidget(title)
        layout.addWidget(hint)

        self.plan_group_actions = QHBoxLayout()
        self.plan_group_actions.addWidget(
            self._button("+ 新建方案组", self._new_group)
        )
        self.plan_group_actions.addWidget(self._button("重命名", self._rename_group))
        self.plan_group_actions.addWidget(
            self._button("删除", self._delete_group, role="danger")
        )
        self.plan_group_actions.addStretch()
        self.pe_check = QCheckBox("体育课方案")
        self.pe_check.toggled.connect(self._toggle_group_pe)
        self.plan_group_actions.addWidget(self.pe_check)
        layout.addLayout(self.plan_group_actions)

        self.group_table = self._table(("方案组", "类型", "教学班"))
        self.group_table.setMaximumHeight(165)
        self.group_table.itemSelectionChanged.connect(self._on_group_selection)
        layout.addWidget(self.group_table)

        priority = QFrame()
        priority.setObjectName("priorityHint")
        priority_layout = QHBoxLayout(priority)
        priority_layout.setContentsMargins(12, 8, 12, 8)
        priority_layout.addWidget(QLabel("↑ 更想要"))
        center = QLabel("只会向上升级，不会向下换课")
        center.setObjectName("muted")
        priority_layout.addWidget(center)
        priority_layout.addStretch()
        priority_layout.addWidget(QLabel("当前持有 ↓"))
        layout.addWidget(priority)

        self.member_table = self._table(
            ("优先级", "课程", "教学班", "人数/容量", "状态")
        )
        layout.addWidget(self.member_table, 1)

        self.plan_member_actions = QHBoxLayout()
        self.group_summary_label = QLabel("请选择一个方案组")
        self.group_summary_label.setObjectName("muted")
        self.plan_member_actions.addWidget(self.group_summary_label, 1)
        self.plan_member_actions.addWidget(
            self._button("移出", self._remove_from_group, role="danger")
        )
        self.plan_member_actions.addWidget(
            self._button("设为当前持有", self._set_as_held)
        )
        self.plan_member_actions.addWidget(
            self._button("下移", lambda: self._move_member(1))
        )
        self.plan_member_actions.addWidget(
            self._button("上移", lambda: self._move_member(-1))
        )
        layout.addLayout(self.plan_member_actions)
        return panel

    # ---------- swap / snapshot / logs ----------

    def _build_swap_page(self) -> QWidget:
        page, layout = self._page()
        notice, notice_layout = self._card()
        title = QLabel("安全规则")
        title.setObjectName("sectionTitle")
        copy = QLabel(
            "选择了高优先级课程后，即使低优先级课程出现空位，也不会退选高优先级课程。"
        )
        copy.setWordWrap(True)
        copy.setObjectName("muted")
        notice_layout.addWidget(title)
        notice_layout.addWidget(copy)
        layout.addWidget(notice)

        options, options_layout = self._card(horizontal=True)
        self.swap_options = options_layout
        self.auto_swap_check = QCheckBox("启用自动换课")
        self.dry_run_check = QCheckBox("演练模式（只记录，不发送换课请求）")
        self.auto_swap_check.toggled.connect(self._on_auto_swap_toggle)
        self.dry_run_check.toggled.connect(self._on_auto_swap_toggle)
        options_layout.addWidget(self.auto_swap_check)
        options_layout.addWidget(self.dry_run_check)
        options_layout.addStretch()
        saved = QLabel("修改即保存，重启监控后生效")
        saved.setObjectName("muted")
        options_layout.addWidget(saved)
        layout.addWidget(options)

        card, card_layout = self._card()
        title = QLabel("方案执行状态")
        title.setObjectName("sectionTitle")
        card_layout.addWidget(title)
        self.swap_table = self._table(
            ("方案组", "类型", "当前持有", "仍监控", "已完成", "失败/暂停")
        )
        card_layout.addWidget(self.swap_table)
        layout.addWidget(card)

        history, history_layout = self._card()
        title = QLabel("历史换课记录")
        title.setObjectName("sectionTitle")
        history_layout.addWidget(title)
        self.swap_history_table = self._table(
            ("时间", "模式", "方案组", "目标", "退选", "结果")
        )
        history_layout.addWidget(self.swap_history_table)
        layout.addWidget(history, 1)
        return page

    def _build_snapshot_page(self) -> QWidget:
        page, layout = self._page()
        card, card_layout = self._card()
        title_row = QHBoxLayout()
        title = QLabel("最近课程快照")
        title.setObjectName("sectionTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        self.snapshot_filter = QComboBox()
        self.snapshot_filter.addItem("全部课程", "all")
        self.snapshot_filter.addItem("仅当前监控", "watched")
        self.snapshot_filter.addItem("仅有空位", "open")
        self.snapshot_filter.currentIndexChanged.connect(
            lambda: self._refresh_snapshot_table(
                self._snapshot_state, self._snapshot_watched
            )
        )
        title_row.addWidget(self.snapshot_filter)
        title_row.addWidget(self._button("刷新", self.refresh))
        card_layout.addLayout(title_row)
        self.snapshot_table = self._table(
            ("监控", "方案组", "课程", "教学班", "人数/容量", "空位")
        )
        card_layout.addWidget(self.snapshot_table)
        layout.addWidget(card, 1)
        return page

    def _build_log_page(self) -> QWidget:
        page, layout = self._page()
        card, card_layout = self._card()
        title_row = QHBoxLayout()
        title = QLabel("最近 300 行")
        title.setObjectName("sectionTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        self.log_filter = QLineEdit()
        self.log_filter.setPlaceholderText("筛选日志内容…")
        self.log_filter.setClearButtonEnabled(True)
        self.log_filter.setMaximumWidth(300)
        self.log_filter.textChanged.connect(self._refresh_log)
        title_row.addWidget(self.log_filter)
        title_row.addWidget(self._button("刷新日志", self.refresh))
        card_layout.addLayout(title_row)
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setObjectName("console")
        self.log_output.setFont(QFont("Cascadia Mono", 9))
        card_layout.addWidget(self.log_output, 1)
        layout.addWidget(card, 1)
        return page

    def _build_settings_page(self) -> QWidget:
        page, layout = self._page()

        info_card, info_layout = self._card()
        title = QLabel("用户信息")
        title.setObjectName("sectionTitle")
        info_layout.addWidget(title)
        info_form = QFormLayout()
        self.user_info_labels: dict[str, QLabel] = {}
        for key, label in (
            ("xm", "姓名"),
            ("xh", "学号"),
            ("bjmc", "班级"),
            ("zymc", "专业"),
            ("term", "当前学期"),
        ):
            value = QLabel("—")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.user_info_labels[key] = value
            info_form.addRow(label, value)
        info_layout.addLayout(info_form)
        layout.addWidget(info_card)

        account_card, account_layout = self._card()
        title = QLabel("JAccount 账号")
        title.setObjectName("sectionTitle")
        account_layout.addWidget(title)
        account_form = QFormLayout()
        self.account_user_edit = QLineEdit()
        self.account_pass_edit = QLineEdit()
        self.account_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        account_form.addRow("用户名", self.account_user_edit)
        account_form.addRow("密码", self.account_pass_edit)
        self.show_secrets_check = QCheckBox("临时显示密码和授权码")
        self.show_secrets_check.toggled.connect(self._toggle_secret_visibility)
        account_form.addRow("", self.show_secrets_check)
        account_layout.addLayout(account_form)
        layout.addWidget(account_card)

        monitor_card, monitor_layout = self._card()
        title = QLabel("监控参数")
        title.setObjectName("sectionTitle")
        monitor_layout.addWidget(title)
        monitor_form = QFormLayout()
        self.poll_min_edit = QLineEdit()
        self.poll_max_edit = QLineEdit()
        self.poll_min_edit.setPlaceholderText("正整数，单位：秒")
        self.poll_max_edit.setPlaceholderText("不得小于最小间隔")
        monitor_form.addRow("最小轮询间隔", self.poll_min_edit)
        monitor_form.addRow("最大轮询间隔", self.poll_max_edit)
        monitor_layout.addLayout(monitor_form)
        layout.addWidget(monitor_card)

        mail_card, mail_layout = self._card()
        title = QLabel("邮件通知")
        title.setObjectName("sectionTitle")
        mail_layout.addWidget(title)
        mail_form = QFormLayout()
        self.email_enabled_check = QCheckBox("启用邮件通知")
        self.smtp_host_edit = QLineEdit()
        self.smtp_port_edit = QLineEdit()
        self.smtp_user_edit = QLineEdit()
        self.smtp_pass_edit = QLineEdit()
        self.smtp_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.mail_from_edit = QLineEdit()
        self.mail_to_edit = QLineEdit()
        mail_form.addRow("", self.email_enabled_check)
        mail_form.addRow("SMTP 主机", self.smtp_host_edit)
        mail_form.addRow("SMTP 端口", self.smtp_port_edit)
        mail_form.addRow("邮箱账号", self.smtp_user_edit)
        mail_form.addRow("SMTP 授权码", self.smtp_pass_edit)
        mail_form.addRow("发件人", self.mail_from_edit)
        mail_form.addRow("收件人", self.mail_to_edit)
        mail_layout.addLayout(mail_form)
        layout.addWidget(mail_card)

        actions = QHBoxLayout()
        self.settings_status = QLabel("设置保存在本地 .env 与 user_settings.json")
        self.settings_status.setObjectName("muted")
        actions.addWidget(self.settings_status)
        actions.addStretch()
        actions.addWidget(self._button("重新载入", self._load_settings_form))
        actions.addWidget(
            self._button("保存用户设置", self._save_user_settings, role="primary")
        )
        layout.addLayout(actions)
        return page

    # ---------- data refresh ----------

    def refresh(self) -> None:
        swap_state = _read_json(
            config.SWAP_STATE_FILE,
            {"completed": [], "fatal": [], "fatal_groups": []},
        )
        state = _read_json(config.STATE_FILE, {})
        watched = _watched_ids(swap_state)
        held = _held_by_group(set(swap_state.get("completed", [])))

        self.metrics["queries"].set_value(str(len(config.KCH_QUERIES)))
        self.metrics["groups"].set_value(str(len(config.PRIORITY_GROUPS)))
        self.metrics["snapshot"].set_value(str(len(state)))
        self.metrics["watched"].set_value(str(len(watched)))
        self.metrics["interval"].set_value(f"{config.POLL_MIN}–{config.POLL_MAX}s")
        swap_label = (
            "关闭"
            if not config.AUTO_SWAP
            else ("演练" if config.AUTO_SWAP_DRY_RUN else "已启用")
        )
        self.metrics["swap"].set_value(swap_label)

        self._load_course_rows(state)
        self._refresh_course_table()
        self._refresh_member_table()
        self._refresh_swap_table(swap_state, watched, held)
        self._refresh_swap_history()
        self._refresh_snapshot_table(state, watched)
        self._refresh_log()
        self._refresh_overview(held, watched, swap_state)
        self._refresh_user_info()
        if not self._settings_loaded:
            self._load_settings_form()
        self._refresh_swap_options()
        self._refresh_process_buttons()
        self._set_status("已刷新本地状态")

    def _refresh_overview(
        self, held: dict[str, str], watched: set[str], swap_state: dict
    ) -> None:
        fatal_groups = set(swap_state.get("fatal_groups", []))
        self.overview_plan_table.setRowCount(len(config.PRIORITY_GROUPS))
        for row, (name, group) in enumerate(config.PRIORITY_GROUPS.items()):
            held_id = held.get(name)
            values = (
                name,
                self._course_label_for_id(held_id) if held_id else "—",
                str(sum(jxb_id in watched for jxb_id in group.get("priority", []))),
                "已暂停" if name in fatal_groups else "正常",
            )
            for column, value in enumerate(values):
                self.overview_plan_table.setItem(row, column, self._item(value))
            if name in fatal_groups:
                self._set_row_tint(
                    self.overview_plan_table, row, COLORS["danger_soft"], COLORS["danger"]
                )
        self.overview_plan_table.resizeRowsToContents()
        self._refresh_recent_activity()

    def _course_label_for_id(
        self, jxb_id: str | None, fallback: dict | None = None
    ) -> str:
        if not jxb_id:
            return "—"
        row = {**(fallback or {}), **self._course_by_id.get(jxb_id, {})}
        has_identity = any(row.get(key) for key in ("kcmc", "jsxx", "jxbmc", "kch"))
        if not has_identity:
            return _short_id(jxb_id)
        return f"{_course_title(row)} · {_class_suffix(row)}"

    def _refresh_recent_activity(self) -> None:
        entries = self._combined_log_lines()[-6:]
        self.recent_activity.clear()
        if not entries:
            self.recent_activity.addItem("暂无运行活动")
            return
        for line in entries:
            self.recent_activity.addItem(QListWidgetItem(line[:180]))

    def _refresh_swap_history(self) -> None:
        lines = []
        if config.LOG_FILE.exists():
            lines = config.LOG_FILE.read_text("utf-8", errors="replace").splitlines()
        records = list(reversed(_parse_swap_records(lines)))
        self.swap_history_table.setRowCount(len(records))
        for row, record in enumerate(records):
            dry_run = record.get("dry_run")
            mode = "演练" if dry_run is True else ("真实" if dry_run is False else "未知")
            result = "成功" if record.get("ok") else str(record.get("status") or "失败")
            values = (
                record.get("timestamp", "—"),
                mode,
                record.get("group", "—"),
                self._course_label_for_id(record.get("target"), record),
                self._course_label_for_id(record.get("drop")),
                result,
            )
            for column, value in enumerate(values):
                self.swap_history_table.setItem(row, column, self._item(str(value)))
            color = COLORS["success"] if record.get("ok") else COLORS["danger"]
            soft = COLORS["success_soft"] if record.get("ok") else COLORS["danger_soft"]
            self._set_row_tint(self.swap_history_table, row, soft, color)
        self.swap_history_table.resizeRowsToContents()

    def _refresh_user_info(self) -> None:
        catalog = _read_json(config.CATALOG_FILE, {})
        user = catalog.get("user") or {}
        values = {
            "xm": user.get("xm") or "—",
            "xh": user.get("xh") or "—",
            "bjmc": user.get("bjmc") or "—",
            "zymc": user.get("zymc") or "—",
            "term": f"{config.XKXNM}-{config.XKXQM}",
        }
        for key, value in values.items():
            self.user_info_labels[key].setText(str(value))

    def _load_settings_form(self) -> None:
        self.account_user_edit.setText(config.JACCOUNT_USER)
        self.account_pass_edit.setText(config.JACCOUNT_PASS)
        self.poll_min_edit.setText(str(config.POLL_MIN))
        self.poll_max_edit.setText(str(config.POLL_MAX))
        self.email_enabled_check.setChecked(config.EMAIL_ENABLED)
        self.smtp_host_edit.setText(config.SMTP_HOST)
        self.smtp_port_edit.setText(str(config.SMTP_PORT))
        self.smtp_user_edit.setText(config.SMTP_USER)
        self.smtp_pass_edit.setText(config.SMTP_PASS)
        self.mail_from_edit.setText(config.MAIL_FROM)
        self.mail_to_edit.setText(config.MAIL_TO)
        self._settings_loaded = True
        self.settings_status.setText("已载入本地设置")

    def _toggle_secret_visibility(self, visible: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        self.account_pass_edit.setEchoMode(mode)
        self.smtp_pass_edit.setEchoMode(mode)

    def _save_user_settings(self) -> None:
        try:
            poll_min = int(self.poll_min_edit.text().strip())
            poll_max = int(self.poll_max_edit.text().strip())
            smtp_port = int(self.smtp_port_edit.text().strip())
        except ValueError:
            _message(self, QMessageBox.Icon.Warning, "设置无效", "轮询间隔和端口必须是整数。")
            return
        if poll_min <= 0 or poll_max <= 0 or poll_max < poll_min:
            _message(
                self, QMessageBox.Icon.Warning, "设置无效",
                "轮询间隔必须为正整数，且最大值不得小于最小值。",
            )
            return
        if not 1 <= smtp_port <= 65535:
            _message(self, QMessageBox.Icon.Warning, "设置无效", "SMTP 端口必须在 1–65535。")
            return
        values = {
            "JACCOUNT_USER": self.account_user_edit.text().strip(),
            "JACCOUNT_PASS": self.account_pass_edit.text(),
            "POLL_MIN": str(poll_min),
            "POLL_MAX": str(poll_max),
            "SMTP_HOST": self.smtp_host_edit.text().strip(),
            "SMTP_PORT": str(smtp_port),
            "SMTP_USER": self.smtp_user_edit.text().strip(),
            "SMTP_PASS": self.smtp_pass_edit.text(),
            "MAIL_FROM": self.mail_from_edit.text().strip(),
            "MAIL_TO": self.mail_to_edit.text().strip(),
        }
        config.save_env_settings(values)
        config.update_user_settings(
            notifications={"email_enabled": self.email_enabled_check.isChecked()}
        )
        suffix = "；正在运行的监控将在下次启动时使用新设置" if self.monitor_process else ""
        self.settings_status.setText(f"设置已保存{suffix}")
        self.metrics["interval"].set_value(f"{config.POLL_MIN}–{config.POLL_MAX}s")
        self._set_status("用户设置已保存")

    def _load_course_rows(self, state: dict | None = None) -> None:
        state = state if state is not None else _read_json(config.STATE_FILE, {})
        catalog = _read_json(config.CATALOG_FILE, {})
        seat_details = _read_json(
            config.SEAT_DETAILS_FILE, {"classes": {}, "errors": {}}
        )
        self._seat_details_by_id = seat_details.get("classes", {})
        self._seat_errors = {
            **seat_details.get("errors", {}),
            **self._seat_runtime_errors,
        }
        self._choosed_ids = {
            course.get("jxb_id")
            for course in catalog.get("choosed", [])
            if course.get("jxb_id")
        }
        rows: dict[str, dict] = {}
        for course in catalog.get("courses", []):
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
        for jxb_id, course in state.items():
            rows[jxb_id] = {**rows.get(jxb_id, {}), **course, "jxb_id": jxb_id}
        for jxb_id, detail in self._seat_details_by_id.items():
            if jxb_id in rows:
                rows[jxb_id]["availability"] = _availability(detail)
                # sksj/jxdd/jsxx 供时间冲突校验与详情展示;缺目录数据的教学班
                # (如尚未跑 --with-capacity 的 zzxk 课程)靠这份缓存补齐。
                for key in ("sksj", "jxdd", "jsxx"):
                    if detail.get(key) is not None:
                        rows[jxb_id][key] = detail[key]
        for row in rows.values():
            row["category"] = config.KKLX_NAMES.get(str(row.get("kklxdm") or ""), "—")
        self._course_rows = list(rows.values())
        self._course_by_id = rows
        self._rebuild_category_filter()

        fetched = catalog.get("fetched_at")
        user = catalog.get("user") or {}
        if fetched:
            who = user.get("xm") or user.get("xh") or "当前用户"
            self.catalog_info.setText(f"{who} · 最近更新 {fetched}")
        else:
            self.catalog_info.setText("尚未获取课程目录，目前仅显示本地监控快照")

    def _group_of_jxb(self, jxb_id: str) -> str | None:
        for name, group in self.groups.items():
            if jxb_id in group.get("priority", []):
                return name
        return None

    def _rebuild_category_filter(self) -> None:
        """按当前数据重建分类下拉(保持选中项),顺序跟随 KKLX_NAMES。"""
        if not hasattr(self, "category_filter"):
            return
        present = {row.get("category") for row in self._course_rows}
        names = [n for n in config.KKLX_NAMES.values() if n in present]
        if "—" in present:
            names.append("—")
        current = self.category_filter.currentData()
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItem("全部分类", None)
        for name in names:
            self.category_filter.addItem(name, name)
        index = self.category_filter.findData(current)
        self.category_filter.setCurrentIndex(index if index >= 0 else 0)
        self.category_filter.blockSignals(False)

    def _refresh_course_table(self) -> None:
        if not hasattr(self, "course_table"):
            return
        selected_ids = set(self._selected_course_ids())
        current_item = self.course_table.item(self.course_table.currentRow(), 0)
        current_id = current_item.data(USER_ROLE) if current_item else None
        scroll_value = self.course_table.verticalScrollBar().value()
        text = self.course_filter.text().strip().casefold()
        category = self.category_filter.currentData()
        visible: list[tuple[dict, str | None]] = []
        for row in self._course_rows:
            if category and row.get("category") != category:
                continue
            group = self._group_of_jxb(row["jxb_id"])
            if self.only_unassigned.isChecked() and group:
                continue
            if self.only_open.isChecked() and _availability(row) != "open":
                continue
            if text and text not in _course_search_text(row):
                continue
            visible.append((row, group))

        visible.sort(
            key=lambda item: (
                str(item[0].get("category") or ""),
                str(item[0].get("kcmc") or item[0].get("kch") or ""),
                _teacher_names(item[0]),
                str(item[0].get("sksj") or ""),
                _class_suffix(item[0]),
            )
        )
        self.course_table.setUpdatesEnabled(False)
        self.course_table.blockSignals(True)
        self.course_table.setSortingEnabled(False)
        self.course_table.setRowCount(len(visible))
        row_by_id: dict[str, int] = {}
        for table_row, (course, group) in enumerate(visible):
            jxb_id = course["jxb_id"]
            row_by_id[jxb_id] = table_row
            chosen = jxb_id in self._choosed_ids
            availability = _availability(course)
            values = (
                f"{_course_title(course)}\n{_course_summary(course)}",
                "当前已选" if chosen else _availability_text(availability),
                group or "—",
            )
            for column, value in enumerate(values):
                item = self._item(str(value), jxb_id if column == 0 else None)
                # 课程完整信息固定显示在表格下方，不再用悬浮气泡重复展示。
                item.setToolTip("")
                self.course_table.setItem(table_row, column, item)
            if chosen:
                self._set_row_tint(
                    self.course_table,
                    table_row,
                    COLORS["primary_soft"],
                    COLORS["primary_hover"],
                )
            elif availability == "open":
                self._set_row_tint(
                    self.course_table,
                    table_row,
                    COLORS["success_soft"],
                    COLORS["success"],
                )
        self.course_table.resizeRowsToContents()
        for row in range(self.course_table.rowCount()):
            self.course_table.setRowHeight(row, max(52, self.course_table.rowHeight(row)))
        selection_model = self.course_table.selectionModel()
        for jxb_id in selected_ids:
            if jxb_id in row_by_id:
                selection_model.select(
                    self.course_table.model().index(row_by_id[jxb_id], 0),
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
        if current_id in row_by_id:
            self.course_table.setCurrentCell(row_by_id[current_id], 0)
        self.course_table.verticalScrollBar().setValue(scroll_value)
        self.course_table.blockSignals(False)
        self.course_table.setUpdatesEnabled(True)
        self.course_count_label.setText(
            f"显示 {len(visible)} / 共 {len(self._course_rows)} 个教学班"
        )
        self._on_course_selection()

    def _on_course_selection(self) -> None:
        rows = sorted({index.row() for index in self.course_table.selectedIndexes()})
        if rows:
            target = f"“{self.selected_group}”方案" if self.selected_group else "方案"
            self.course_selection_label.setText(
                f"已选择 {len(rows)} 个教学班，将加入{target}"
            )
            current_row = self.course_table.currentRow()
            if current_row not in rows:
                current_row = rows[0]
            item = self.course_table.item(current_row, 0)
            jxb_id = item.data(USER_ROLE) if item else None
            course = self._course_by_id.get(jxb_id, {})
            group = self._group_of_jxb(jxb_id) if jxb_id else None
            prefix = f"已多选 {len(rows)} 项；当前详情：\n" if len(rows) > 1 else ""
            self.course_detail_label.setText(
                prefix + _course_detail_text(course, group)
            )
        else:
            self.course_selection_label.setText("可多选；双击也可快速加入当前方案")
            self.course_detail_label.setText("选择一个教学班后在此显示完整信息")
        self.add_course_button.setEnabled(bool(rows and self.selected_group))

    # ---------- plan editing ----------

    def _load_groups_from_config(self) -> None:
        self.groups = {
            name: {
                "is_pe": group.get("is_pe", False),
                "priority": list(group.get("priority", [])),
            }
            for name, group in config.load_priority_groups().items()
        }
        self.selected_group = next(iter(self.groups), None)
        self._refresh_group_table()
        self._refresh_member_table()
        self._set_groups_clean()

    def _refresh_group_table(self) -> None:
        self.group_table.blockSignals(True)
        self.group_table.setRowCount(len(self.groups))
        selected_row = -1
        for row, (name, group) in enumerate(self.groups.items()):
            values = (
                name,
                "体育" if group.get("is_pe") else "普通",
                str(len(group.get("priority", []))),
            )
            for column, value in enumerate(values):
                self.group_table.setItem(
                    row,
                    column,
                    self._item(value, name if column == 0 else None),
                )
            if name == self.selected_group:
                selected_row = row
        if selected_row < 0 and self.groups:
            selected_row = 0
            self.selected_group = self.group_table.item(0, 0).data(USER_ROLE)
        if selected_row >= 0:
            self.group_table.selectRow(selected_row)
        else:
            self.selected_group = None
        self.group_table.blockSignals(False)
        self.pe_check.blockSignals(True)
        self.pe_check.setChecked(
            bool(
                self.selected_group
                and self.groups[self.selected_group].get("is_pe", False)
            )
        )
        self.pe_check.blockSignals(False)
        self._on_course_selection()

    def _on_group_selection(self) -> None:
        row = self.group_table.currentRow()
        item = self.group_table.item(row, 0) if row >= 0 else None
        self.selected_group = item.data(USER_ROLE) if item else None
        self.pe_check.blockSignals(True)
        self.pe_check.setChecked(
            bool(
                self.selected_group
                and self.groups[self.selected_group].get("is_pe", False)
            )
        )
        self.pe_check.blockSignals(False)
        self._refresh_member_table()
        self._on_course_selection()

    def _refresh_member_table(self) -> None:
        if not hasattr(self, "member_table"):
            return
        if not self.selected_group or self.selected_group not in self.groups:
            self.member_table.setRowCount(0)
            self.group_summary_label.setText("请先选择或新建一个方案组")
            return
        ids = self.groups[self.selected_group]["priority"]
        self.member_table.setRowCount(len(ids))
        # 与"其他组当前持有"实时比对——每次刷新都重算,反映最新持有状态
        # (某组换课成功后,其他组候选新产生的冲突会在下次刷新时自然显现)。
        held_elsewhere = {
            name: group["priority"][-1]
            for name, group in self.groups.items()
            if name != self.selected_group and group.get("priority")
        }
        for row_index, jxb_id in enumerate(ids):
            course = self._course_by_id.get(jxb_id, {})
            detail = self._seat_details_by_id.get(jxb_id)
            sksj = course.get("sksj")
            conflict_groups: list[str] = []
            conflict_unknown = False
            for other_name, other_held_id in held_elsewhere.items():
                if other_held_id == jxb_id:
                    continue
                other_sksj = self._course_by_id.get(other_held_id, {}).get("sksj")
                verdict = timetable.conflicts(sksj, other_sksj)
                if verdict is True:
                    conflict_groups.append(other_name)
                elif verdict is None:
                    conflict_unknown = True
            notes = []
            if row_index == len(ids) - 1:
                notes.append("当前持有")
            if jxb_id in self._choosed_ids:
                notes.append("已选")
            if conflict_groups:
                notes.append(f"⚠冲突({'/'.join(conflict_groups)})")
            elif conflict_unknown:
                notes.append("时间未知")
            if jxb_id in self._seat_pending or jxb_id in self._seat_active:
                seat_text = "加载中…"
            elif detail:
                seat_text = _seat_text(detail)
            elif jxb_id in self._seat_errors:
                seat_text = "加载失败"
            else:
                seat_text = _seat_text(course)
            values = (
                str(row_index + 1),
                _course_title(course),
                _course_summary(course),
                seat_text,
                " / ".join(notes) or "目标",
            )
            for column, value in enumerate(values):
                self.member_table.setItem(
                    row_index,
                    column,
                    self._item(value, jxb_id if column == 0 else None),
                )
            if conflict_groups:
                self._set_row_tint(
                    self.member_table,
                    row_index,
                    COLORS["danger_soft"],
                    COLORS["danger"],
                )
            elif row_index == len(ids) - 1:
                self._set_row_tint(
                    self.member_table,
                    row_index,
                    COLORS["warning_soft"],
                    COLORS["warning"],
                )
        self.member_table.resizeRowsToContents()
        if ids:
            held = self._course_by_id.get(ids[-1], {})
            held_name = self._course_label_for_id(ids[-1])
            self.group_summary_label.setText(
                f"{len(ids)} 个教学班 · 当前持有：{held_name}"
            )
        else:
            self.group_summary_label.setText(f"“{self.selected_group}”方案为空")

    def _selected_course_ids(self) -> list[str]:
        rows = sorted({index.row() for index in self.course_table.selectedIndexes()})
        ids = []
        for row in rows:
            item = self.course_table.item(row, 0)
            if item and item.data(USER_ROLE):
                ids.append(item.data(USER_ROLE))
        return ids

    def _selected_member_id(self) -> str | None:
        row = self.member_table.currentRow()
        item = self.member_table.item(row, 0) if row >= 0 else None
        return item.data(USER_ROLE) if item else None

    def _toggle_group_pe(self, checked: bool) -> None:
        if self.selected_group:
            self.groups[self.selected_group]["is_pe"] = checked
            self._refresh_group_table()
            self._mark_groups_dirty("方案类型已修改，尚未保存")

    def _new_group(self) -> None:
        name, ok = QInputDialog.getText(
            self,
            "新建选课方案组",
            "输入容易识别的名称，例如“大学物理”或“体育课”：",
        )
        name = name.strip()
        if not ok or not name:
            return
        if name in self.groups:
            _message(
                self,
                QMessageBox.Icon.Warning,
                "新建选课方案组",
                f"“{name}”已经存在。",
            )
            return
        self.groups[name] = {"is_pe": False, "priority": []}
        self.selected_group = name
        self._refresh_group_table()
        self._refresh_member_table()
        self._mark_groups_dirty(f"已新建“{name}”方案组，尚未保存")

    def _rename_group(self) -> None:
        if not self.selected_group:
            return
        old = self.selected_group
        name, ok = QInputDialog.getText(
            self,
            "重命名选课方案组",
            "输入新的方案组名称：",
            text=old,
        )
        name = name.strip()
        if not ok or not name or name == old:
            return
        if name in self.groups:
            _message(
                self,
                QMessageBox.Icon.Warning,
                "重命名选课方案组",
                f"“{name}”已经存在。",
            )
            return
        self.groups = {
            (name if group_name == old else group_name): group
            for group_name, group in self.groups.items()
        }
        self.selected_group = name
        self._refresh_group_table()
        self._refresh_member_table()
        self._mark_groups_dirty(f"方案组已重命名为“{name}”，尚未保存")

    def _delete_group(self) -> None:
        if not self.selected_group:
            return
        group = self.selected_group
        if not _message(
            self,
            QMessageBox.Icon.Question,
            "删除选课方案组",
            f"确定删除“{group}”吗？其中的教学班会回到课程目录。",
            ask=True,
        ):
            return
        del self.groups[group]
        self.selected_group = next(iter(self.groups), None)
        self._refresh_group_table()
        self._refresh_member_table()
        self._refresh_course_table()
        self._mark_groups_dirty(f"已删除“{group}”方案组，尚未保存")

    def _add_selected_to_group(self) -> None:
        if not self.selected_group:
            _message(
                self,
                QMessageBox.Icon.Information,
                "加入选课方案",
                "请先选择或新建一个选课方案组。",
            )
            return
        jxb_ids = self._selected_course_ids()
        if not jxb_ids:
            return
        moved_from: dict[str, list[str]] = {}
        for jxb_id in jxb_ids:
            for name, group in self.groups.items():
                if name != self.selected_group and jxb_id in group["priority"]:
                    moved_from.setdefault(name, []).append(jxb_id)
        if moved_from:
            summary = "、".join(
                f"{name}（{len(ids)} 个）" for name, ids in moved_from.items()
            )
            if not _message(
                self,
                QMessageBox.Icon.Question,
                "移动教学班",
                f"所选教学班已属于 {summary}。\n"
                f"确定将它们移动到“{self.selected_group}”吗？",
                ask=True,
            ):
                return
        for group in self.groups.values():
            group["priority"] = [
                jxb_id for jxb_id in group["priority"] if jxb_id not in jxb_ids
            ]
        target = self.groups[self.selected_group]["priority"]
        self.groups[self.selected_group]["priority"] = _merge_priority_ids(
            target, jxb_ids, self._choosed_ids
        )
        self._refresh_group_table()
        self._refresh_member_table()
        self._refresh_course_table()
        self._mark_groups_dirty(
            f"已加入 {len(jxb_ids)} 个教学班到“{self.selected_group}”，尚未保存"
        )
        self._queue_seat_refresh(jxb_ids)
        self._warn_new_conflicts(jxb_ids)

    def _warn_new_conflicts(self, new_jxb_ids: list[str]) -> None:
        """新加入的教学班与其他方案组全部候选(含已选)做一次性时间冲突校验。

        只增量检查这次新加入的项,不重扫已保存的方案——已存在的数据默认视为
        无冲突。冲突只警告,不阻止添加;数据缺失明确标"无法判断",不当无冲突。
        """
        conflicts: list[str] = []
        unknowns: list[str] = []
        seen_pairs: set[tuple[str, str]] = set()
        for jxb_id in new_jxb_ids:
            sksj = self._course_by_id.get(jxb_id, {}).get("sksj")
            label = self._course_label_for_id(jxb_id)
            for other_name, other_group in self.groups.items():
                if other_name == self.selected_group:
                    continue
                for other_id in other_group.get("priority", []):
                    if other_id == jxb_id:
                        continue
                    pair = (min(jxb_id, other_id), max(jxb_id, other_id))
                    if pair in seen_pairs:
                        continue
                    other_sksj = self._course_by_id.get(other_id, {}).get("sksj")
                    verdict = timetable.conflicts(sksj, other_sksj)
                    if verdict is None:
                        seen_pairs.add(pair)
                        unknowns.append(
                            f"{label} 与 “{other_name}”组的 "
                            f"{self._course_label_for_id(other_id)}"
                        )
                    elif verdict:
                        seen_pairs.add(pair)
                        detail = timetable.describe_conflict(sksj, other_sksj) or ""
                        conflicts.append(
                            f"{label} 与 “{other_name}”组的 "
                            f"{self._course_label_for_id(other_id)}　{detail}"
                        )
        if not conflicts and not unknowns:
            return
        lines: list[str] = []
        if conflicts:
            lines.append("确定存在时间冲突：")
            lines.extend(f"　{line}" for line in conflicts)
        if unknowns:
            lines.append("以下缺少时间数据，无法判断是否冲突：")
            lines.extend(f"　{line}" for line in unknowns)
        _message(self, QMessageBox.Icon.Warning, "时间冲突提醒", "\n".join(lines))

    def _queue_seat_refresh(self, jxb_ids: list[str]) -> None:
        self._seat_pending.update(jxb_ids)
        if self.seat_process and (
            self.seat_process.state() != QProcess.ProcessState.NotRunning
        ):
            self._refresh_member_table()
            return
        self._start_next_seat_refresh()

    def _start_next_seat_refresh(self) -> None:
        if not self._seat_pending:
            return
        self._seat_active = set(self._seat_pending)
        self._seat_pending.clear()
        for jxb_id in self._seat_active:
            self._seat_runtime_errors.pop(jxb_id, None)
        args = self._process_args(
            "bootstrap.py", "--seat-details", *sorted(self._seat_active)
        )
        self.seat_process = self._start_process("seat-details", args)
        self._refresh_member_table()

    def _remove_from_group(self) -> None:
        if not self.selected_group:
            return
        jxb_id = self._selected_member_id()
        if not jxb_id:
            return
        self.groups[self.selected_group]["priority"].remove(jxb_id)
        self._refresh_group_table()
        self._refresh_member_table()
        self._refresh_course_table()
        self._mark_groups_dirty("已从方案中移出教学班，尚未保存")

    def _move_member(self, delta: int) -> None:
        if not self.selected_group:
            return
        jxb_id = self._selected_member_id()
        if not jxb_id:
            return
        ids = self.groups[self.selected_group]["priority"]
        old_index = ids.index(jxb_id)
        new_index = old_index + delta
        if new_index < 0 or new_index >= len(ids):
            return
        ids[old_index], ids[new_index] = ids[new_index], ids[old_index]
        self._refresh_member_table()
        self.member_table.selectRow(new_index)
        self._mark_groups_dirty("优先级顺序已调整，尚未保存")

    def _set_as_held(self) -> None:
        if not self.selected_group:
            return
        jxb_id = self._selected_member_id()
        if not jxb_id:
            return
        ids = self.groups[self.selected_group]["priority"]
        if ids and ids[-1] == jxb_id:
            return
        ids.remove(jxb_id)
        ids.append(jxb_id)
        self._refresh_member_table()
        self.member_table.selectRow(len(ids) - 1)
        self._mark_groups_dirty("当前持有教学班已调整，尚未保存")

    def _mark_groups_dirty(self, text: str) -> None:
        self._groups_dirty = True
        self.save_state_label.setText(text)
        self.save_state_label.setObjectName("dirtyBadge")
        self._repolish(self.save_state_label)
        self.save_groups_button.setEnabled(True)

    def _set_groups_clean(self) -> None:
        self._groups_dirty = False
        if hasattr(self, "save_state_label"):
            self.save_state_label.setText("配置已保存")
            self.save_state_label.setObjectName("savedBadge")
            self._repolish(self.save_state_label)
            self.save_groups_button.setEnabled(False)

    def _find_duplicate_assignments(self) -> dict[str, list[str]]:
        owners: dict[str, list[str]] = {}
        for name, group in self.groups.items():
            for jxb_id in group.get("priority", []):
                owners.setdefault(jxb_id, []).append(name)
        return {jxb_id: names for jxb_id, names in owners.items() if len(names) > 1}

    def _group_setup_warnings(self) -> list[str]:
        if not self._choosed_ids:
            return []
        warnings = []
        for name, group in self.groups.items():
            ids = group.get("priority", [])
            if not ids:
                continue
            selected = [jxb_id for jxb_id in ids if jxb_id in self._choosed_ids]
            if not selected:
                warnings.append(f"“{name}”没有包含当前已选教学班")
            elif len(selected) > 1:
                warnings.append(
                    f"“{name}”包含 {len(selected)} 个当前已选教学班，"
                    "无法唯一确定当前持有"
                )
            elif ids[-1] not in selected:
                warnings.append(f"“{name}”的最后一项不是当前已选教学班")
        return warnings

    def _derive_courses(self) -> tuple[dict, list[str]]:
        derived: dict[str, dict] = {}
        unresolved: list[str] = []
        for group in self.groups.values():
            for jxb_id in group.get("priority", []):
                row = self._course_by_id.get(jxb_id) or {}
                kch = row.get("kch")
                if row.get("kch_id"):
                    endpoint = row.get("endpoint") or (
                        "pe" if row.get("kklxdm") == "06" else "display"
                    )
                    entry: dict = {"endpoint": endpoint, "kch_id": row["kch_id"]}
                    if endpoint == "display":
                        entry["jxb_id"] = jxb_id
                    elif endpoint == "zzxk":
                        # zzxk 监控按分类批量查询,需记录该课所在分类
                        entry["kklxdm"] = str(row.get("kklxdm") or "")
                    derived.setdefault(kch or row["kch_id"], entry)
                elif kch and kch in config.KCH_QUERIES:
                    derived.setdefault(kch, config.KCH_QUERIES[kch])
                else:
                    unresolved.append(jxb_id)
        return derived, unresolved

    def _save_groups(self) -> None:
        duplicates = self._find_duplicate_assignments()
        if duplicates:
            lines = []
            for jxb_id, names in duplicates.items():
                row = self._course_by_id.get(jxb_id, {})
                label = self._course_label_for_id(jxb_id, row)
                lines.append(f"{label}：{', '.join(names)}")
            if not _message(
                self,
                QMessageBox.Icon.Warning,
                "教学班重复",
                "以下教学班同时出现在多个方案组中：\n"
                + "\n".join(lines)
                + "\n\n仍要保存吗？",
                ask=True,
            ):
                return
        warnings = self._group_setup_warnings()
        if warnings and not _message(
            self,
            QMessageBox.Icon.Warning,
            "确认当前持有课程",
            "\n".join(warnings)
            + "\n\n当前持有必须位于每个方案组的最下方。仍要保存吗？",
            ask=True,
        ):
            return
        derived, unresolved = self._derive_courses()
        sections: dict = {"priority_groups": self.groups}
        if derived:
            sections["courses"] = derived
        config.update_user_settings(**sections)
        if unresolved:
            _message(
                self,
                QMessageBox.Icon.Warning,
                "部分课程数据不完整",
                f"有 {len(unresolved)} 个教学班无法确定查询模板。"
                "请先点击“自动获取用户与课程”。",
            )
        self.refresh()
        self._set_groups_clean()
        suffix = f"，共监控 {len(derived)} 门课程" if derived else ""
        self._set_status(f"选课方案已保存{suffix}；重启监控后生效")

    def _reload_groups(self) -> None:
        if self._groups_dirty and not _message(
            self,
            QMessageBox.Icon.Question,
            "放弃修改",
            "当前选课方案尚未保存，确定放弃这些修改吗？",
            ask=True,
        ):
            return
        self._load_groups_from_config()
        self._refresh_course_table()
        self._set_status("已重新载入已保存的选课方案")

    def _reset_default_groups(self) -> None:
        if not _message(
            self,
            QMessageBox.Icon.Question,
            "恢复内置默认",
            "确定恢复项目内置的选课方案吗？当前未保存的修改会被替换。",
            ask=True,
        ):
            return
        self.groups = config.default_priority_groups()
        self.selected_group = next(iter(self.groups), None)
        self._refresh_group_table()
        self._refresh_member_table()
        self._refresh_course_table()
        self._mark_groups_dirty("已恢复内置默认方案，尚未保存")

    # ---------- swap state ----------

    def _refresh_swap_options(self) -> None:
        self._refreshing_swap_options = True
        self.auto_swap_check.setChecked(config.AUTO_SWAP)
        self.dry_run_check.setChecked(config.AUTO_SWAP_DRY_RUN)
        self._refreshing_swap_options = False

    def _on_auto_swap_toggle(self) -> None:
        if self._refreshing_swap_options:
            return
        enabled = self.auto_swap_check.isChecked()
        dry_run = self.dry_run_check.isChecked()
        was_armed = config.AUTO_SWAP and not config.AUTO_SWAP_DRY_RUN
        if enabled and not dry_run and not was_armed:
            if not _message(
                self,
                QMessageBox.Icon.Warning,
                "启用真实自动换课",
                "监控到空位后会真实执行退课和选课，存在两头落空风险。"
                "建议先使用演练模式。\n\n确定启用吗？",
                ask=True,
            ):
                self._refresh_swap_options()
                return
        config.update_user_settings(
            auto_swap={"enabled": enabled, "dry_run": dry_run}
        )
        self.refresh()
        self._set_status("自动换课设置已保存；重启监控后生效")

    def _refresh_swap_table(
        self, swap_state: dict, watched: set[str], held: dict[str, str]
    ) -> None:
        completed = set(swap_state.get("completed", []))
        fatal = set(swap_state.get("fatal", []))
        fatal_groups = set(swap_state.get("fatal_groups", []))
        self.swap_table.setRowCount(len(config.PRIORITY_GROUPS))
        for row, (name, group) in enumerate(config.PRIORITY_GROUPS.items()):
            ids = group["priority"]
            done = [jxb_id for jxb_id in ids if jxb_id in completed]
            failed = [jxb_id for jxb_id in ids if jxb_id in fatal]
            if name in fatal_groups:
                failed.append("方案已暂停")
            values = (
                name,
                "体育" if group.get("is_pe") else "普通",
                self._course_label_for_id(held.get(name)),
                str(sum(jxb_id in watched for jxb_id in ids)),
                "\n".join(self._course_label_for_id(jxb_id) for jxb_id in done)
                or "—",
                "\n".join(
                    "方案已暂停"
                    if jxb_id == "方案已暂停"
                    else self._course_label_for_id(jxb_id)
                    for jxb_id in failed
                )
                or "—",
            )
            for column, value in enumerate(values):
                self.swap_table.setItem(row, column, self._item(value))
            if failed:
                self._set_row_tint(
                    self.swap_table, row, COLORS["danger_soft"], COLORS["danger"]
                )
            elif done:
                self._set_row_tint(
                    self.swap_table, row, COLORS["success_soft"], COLORS["success"]
                )
        self.swap_table.resizeRowsToContents()

    def _refresh_snapshot_table(self, state: dict, watched: set[str]) -> None:
        self._snapshot_state = state
        self._snapshot_watched = watched
        mode = (
            self.snapshot_filter.currentData()
            if hasattr(self, "snapshot_filter")
            else "all"
        )
        rows = []
        for jxb_id, course in state.items():
            spot = _has_spot(course)
            if mode == "watched" and jxb_id not in watched:
                continue
            if mode == "open" and not spot:
                continue
            rows.append(
                (
                    jxb_id not in watched,
                    jxb_id,
                    "是" if jxb_id in watched else "—",
                    config.find_group(jxb_id) or "—",
                    _course_title({**self._course_by_id.get(jxb_id, {}), **course}),
                    _course_summary(
                        {**self._course_by_id.get(jxb_id, {}), **course}
                    ),
                    _seat_text(course),
                    "是" if spot else ("否" if spot is False else "—"),
                )
            )
        rows.sort()
        self.snapshot_table.setRowCount(len(rows))
        for row_index, (_, _jxb_id, *values) in enumerate(rows):
            for column, value in enumerate(values):
                self.snapshot_table.setItem(row_index, column, self._item(str(value)))
            if values[-1] == "是":
                self._set_row_tint(
                    self.snapshot_table,
                    row_index,
                    COLORS["success_soft"],
                    COLORS["success"],
                )
            elif values[0] == "是":
                self._set_row_tint(
                    self.snapshot_table,
                    row_index,
                    COLORS["primary_soft"],
                    COLORS["primary_hover"],
                )
        self.snapshot_table.resizeRowsToContents()

    def _refresh_log(self) -> None:
        lines = self._combined_log_lines()
        query = (
            self.log_filter.text().strip().casefold()
            if hasattr(self, "log_filter")
            else ""
        )
        if query:
            lines = [line for line in lines if query in line.casefold()]
        visible = lines[-300:]
        self.log_output.setPlainText(
            "\n".join(visible)
            if visible
            else ("没有匹配的日志。" if query else "changes.log 暂无记录。")
        )
        scrollbar = self.log_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _combined_log_lines(self) -> list[str]:
        persisted = []
        if config.LOG_FILE.exists():
            persisted = [
                f"[变更] {line}"
                for line in config.LOG_FILE.read_text(
                    "utf-8", errors="replace"
                ).splitlines()[-300:]
            ]
        return [*persisted, *self._runtime_lines[-500:]]

    # ---------- processes ----------

    def _process_args(self, script: str, *args: str) -> list[str]:
        result = [str(ROOT / script), *args]
        if self.debug_check.isChecked():
            result.append("--debug")
        return result

    def _start_process(self, label: str, args: list[str]) -> QProcess:
        process = QProcess(self)
        process.setWorkingDirectory(str(ROOT))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUNBUFFERED", "1")
        # 子进程默认按 Windows 控制台代码页(GBK)输出中文,而下方按 UTF-8 解码 → 乱码。
        # 强制子进程 stdout/stderr 走 UTF-8,与 _read_process_output 的解码保持一致。
        environment.insert("PYTHONIOENCODING", "utf-8")
        environment.insert("PYTHONUTF8", "1")
        process.setProcessEnvironment(environment)
        process.readyReadStandardOutput.connect(
            lambda p=process, source=label: self._read_process_output(p, source)
        )
        process.finished.connect(
            lambda code, _status, name=label, p=process: self._process_finished(
                name, p, code
            )
        )
        self._append_output(f"$ {sys.executable} {' '.join(args)}", label)
        process.start(sys.executable, args)
        if not process.waitForStarted(3000):
            self._append_output(f"启动失败：{process.errorString()}", label)
        return process

    def _read_process_output(self, process: QProcess, source: str = "后台") -> None:
        data = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if data:
            for line in data.rstrip().splitlines():
                self._append_output(line, source)

    def _process_finished(self, label: str, process: QProcess, code: int) -> None:
        self._read_process_output(process, label)
        self._append_output(f"exit={code}", label)
        if label == "monitor" and process is self.monitor_process:
            self.monitor_process = None
            self._set_monitor_running(False)
        elif label == "once" and process is self.once_process:
            self.once_process = None
        elif label == "bootstrap" and process is self.bootstrap_process:
            self.bootstrap_process = None
        elif label == "seat-details" and process is self.seat_process:
            self.seat_process = None
            if code != 0:
                for jxb_id in self._seat_active:
                    self._seat_runtime_errors[jxb_id] = "后台查询失败"
            self._seat_active.clear()
            QTimer.singleShot(0, self._start_next_seat_refresh)
        self._refresh_process_buttons()
        self.refresh()

    def _append_output(self, text: str, source: str = "系统") -> None:
        timestamp = datetime.now().isoformat(timespec="seconds")
        self._runtime_lines.append(f"[{source}] {timestamp} {text}")
        self._runtime_lines = self._runtime_lines[-500:]
        if hasattr(self, "log_output"):
            self._refresh_log()
        if hasattr(self, "recent_activity"):
            self._refresh_recent_activity()

    def run_once(self) -> None:
        if self.once_process and self.once_process.state() != QProcess.ProcessState.NotRunning:
            return
        args = self._process_args("monitor.py", "--once")
        self.once_process = self._start_process("once", args)
        self._refresh_process_buttons()
        self.nav.setCurrentRow(0)

    def start_monitor(self) -> None:
        if self.monitor_process and (
            self.monitor_process.state() != QProcess.ProcessState.NotRunning
        ):
            return
        self.monitor_process = self._start_process(
            "monitor", self._process_args("monitor.py")
        )
        self._set_monitor_running(
            self.monitor_process.state() != QProcess.ProcessState.NotRunning
        )
        self._refresh_process_buttons()

    def stop_monitor(self) -> None:
        if not self.monitor_process:
            return
        self.monitor_process.terminate()
        self._set_status("正在停止监控…")
        QTimer.singleShot(1500, self._kill_monitor_if_needed)

    def _kill_monitor_if_needed(self) -> None:
        if self.monitor_process and (
            self.monitor_process.state() != QProcess.ProcessState.NotRunning
        ):
            self.monitor_process.kill()
            self._append_output("监控进程未及时退出，已强制结束。", "monitor")

    def run_bootstrap(self) -> None:
        if self.bootstrap_process and (
            self.bootstrap_process.state() != QProcess.ProcessState.NotRunning
        ):
            return
        self.bootstrap_process = self._start_process(
            "bootstrap", self._process_args("bootstrap.py")
        )
        self._refresh_process_buttons()
        self.nav.setCurrentRow(0)
        self._set_status("正在获取用户信息与课程目录…")

    def _refresh_process_buttons(self) -> None:
        monitor_running = bool(
            self.monitor_process
            and self.monitor_process.state() != QProcess.ProcessState.NotRunning
        )
        once_running = bool(
            self.once_process
            and self.once_process.state() != QProcess.ProcessState.NotRunning
        )
        bootstrap_running = bool(
            self.bootstrap_process
            and self.bootstrap_process.state() != QProcess.ProcessState.NotRunning
        )
        seat_running = bool(
            self.seat_process
            and self.seat_process.state() != QProcess.ProcessState.NotRunning
        )
        self.start_button.setEnabled(not monitor_running)
        self.stop_button.setEnabled(monitor_running)
        self.once_button.setEnabled(not once_running)
        self.bootstrap_button.setEnabled(not bootstrap_running and not seat_running)

    def _set_monitor_running(self, running: bool) -> None:
        self.monitor_badge.setText("●  监控运行中" if running else "●  未运行")
        self.monitor_badge.setProperty("running", running)
        self._repolish(self.monitor_badge)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(f"●  {text}")

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._groups_dirty and not _message(
            self,
            QMessageBox.Icon.Question,
            "退出",
            "选课方案还有未保存的修改。确定放弃修改并退出吗？",
            ask=True,
        ):
            event.ignore()
            return
        if self.monitor_process and (
            self.monitor_process.state() != QProcess.ProcessState.NotRunning
        ):
            if not _message(
                self,
                QMessageBox.Icon.Question,
                "退出",
                "监控仍在运行。确定停止监控并退出吗？",
                ask=True,
            ):
                event.ignore()
                return
            self.monitor_process.terminate()
            if not self.monitor_process.waitForFinished(1000):
                self.monitor_process.kill()
        for process in (self.once_process, self.bootstrap_process, self.seat_process):
            if process and process.state() != QProcess.ProcessState.NotRunning:
                process.terminate()
                if not process.waitForFinished(800):
                    process.kill()
        event.accept()


STYLE_SHEET = f"""
* {{
    font-family: "__UI_FONT__";
    font-size: 10pt;
    color: {COLORS["text"]};
}}
QMainWindow {{
    background: {COLORS["bg"]};
}}
QWidget {{
    background: {COLORS["bg"]};
}}
QWidget#appRoot, QWidget#content {{
    background: {COLORS["bg"]};
}}
QWidget#page, QScrollArea#pageScroll, QScrollArea#pageScroll > QWidget > QWidget {{
    background: transparent;
}}
QLabel, QCheckBox {{
    background: transparent;
}}
QFrame#sidebar {{
    background: #142033;
}}
QLabel#brand {{
    color: white;
    font-size: 18pt;
    font-weight: 700;
}}
QLabel#sidebarMuted {{
    color: #94A3B8;
    font-size: 9pt;
}}
QListWidget#navigation {{
    background: transparent;
    border: none;
    outline: none;
    color: #CBD5E1;
}}
QListWidget#navigation::item {{
    padding: 12px 14px;
    margin: 2px 0;
    border-radius: 8px;
}}
QListWidget#navigation::item:hover {{
    background: #243047;
}}
QListWidget#navigation::item:selected {{
    background: {COLORS["primary"]};
    color: white;
    font-weight: 600;
}}
QListWidget#activityList {{
    background: transparent;
    border: none;
    outline: none;
}}
QListWidget#activityList::item {{
    background: {COLORS["surface_alt"]};
    border-radius: 7px;
    padding: 9px;
    margin: 2px 0;
}}
QLabel#pageTitle {{
    font-size: 19pt;
    font-weight: 700;
}}
QLabel#sectionTitle {{
    font-size: 11pt;
    font-weight: 650;
}}
QLabel#muted {{
    color: {COLORS["muted"]};
    font-size: 9pt;
}}
QLabel#metricValue {{
    font-size: 17pt;
    font-weight: 700;
}}
QFrame#card {{
    background: {COLORS["surface"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 12px;
}}
QLabel#stepBadge {{
    background: {COLORS["primary"]};
    color: white;
    border-radius: 13px;
    min-width: 26px;
    max-width: 26px;
    min-height: 26px;
    max-height: 26px;
    font-weight: 700;
}}
QFrame#priorityHint {{
    background: {COLORS["primary_soft"]};
    border: 1px solid #C9D8FA;
    border-radius: 7px;
}}
QFrame#detailPanel {{
    background: {COLORS["surface_alt"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
}}
QLabel#detailText {{
    color: {COLORS["muted"]};
}}
QLabel#statusBadge {{
    background: {COLORS["surface_alt"]};
    color: {COLORS["muted"]};
    border-radius: 14px;
    padding: 7px 13px;
    font-weight: 600;
}}
QLabel#statusBadge[running="true"] {{
    background: {COLORS["success_soft"]};
    color: {COLORS["success"]};
}}
QLabel#savedBadge {{
    background: {COLORS["success_soft"]};
    color: {COLORS["success"]};
    border-radius: 6px;
    padding: 7px 11px;
    font-weight: 600;
}}
QLabel#dirtyBadge {{
    background: {COLORS["warning_soft"]};
    color: {COLORS["warning"]};
    border-radius: 6px;
    padding: 7px 11px;
    font-weight: 600;
}}
QLabel#statusText {{
    color: {COLORS["primary"]};
    font-weight: 600;
}}
QPushButton {{
    background: {COLORS["surface"]};
    border: 1px solid #CBD5E1;
    border-radius: 8px;
    padding: 8px 14px;
    min-height: 20px;
}}
QPushButton:hover {{
    background: {COLORS["surface_alt"]};
    border-color: #94A3B8;
}}
QPushButton:disabled {{
    background: #EEF2F6;
    color: #98A2B3;
    border-color: #E2E8F0;
}}
QPushButton[role="primary"] {{
    background: {COLORS["primary"]};
    color: white;
    border-color: {COLORS["primary"]};
    font-weight: 600;
}}
QPushButton[role="primary"]:hover {{
    background: {COLORS["primary_hover"]};
}}
QPushButton[role="danger"] {{
    color: {COLORS["danger"]};
    border-color: #F0B8B5;
}}
QPushButton[role="danger"]:hover {{
    background: {COLORS["danger_soft"]};
}}
QLineEdit {{
    background: white;
    border: 1px solid #CBD5E1;
    border-radius: 7px;
    padding: 8px 10px;
    selection-background-color: {COLORS["primary"]};
}}
QComboBox {{
    background: white;
    border: 1px solid #CBD5E1;
    border-radius: 7px;
    padding: 7px 10px;
    min-height: 20px;
}}
QComboBox:focus {{
    border-color: {COLORS["primary"]};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QLineEdit:focus {{
    border: 1px solid {COLORS["primary"]};
}}
QCheckBox {{
    spacing: 7px;
}}
QCheckBox::indicator {{
    width: 17px;
    height: 17px;
}}
QTableWidget {{
    background: white;
    alternate-background-color: {COLORS["surface_alt"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 7px;
    selection-background-color: {COLORS["primary_soft"]};
    selection-color: {COLORS["primary_hover"]};
    outline: none;
}}
QTableWidget::item {{
    padding: 7px;
    border-bottom: 1px solid #EEF2F6;
}}
QHeaderView::section {{
    background: {COLORS["surface_alt"]};
    color: {COLORS["muted"]};
    border: none;
    border-bottom: 1px solid {COLORS["border"]};
    padding: 8px;
    font-weight: 600;
}}
QPlainTextEdit#console {{
    background: {COLORS["console"]};
    color: #D1E7DD;
    border: none;
    border-radius: 7px;
    padding: 10px;
    selection-background-color: #334155;
}}
QSplitter::handle {{
    background: transparent;
    width: 10px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #CBD5E1;
    border-radius: 4px;
    min-height: 28px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
"""


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("交我选监控")
    app.setStyle("Fusion")
    installed_fonts = set(QFontDatabase.families())
    ui_family = next(
        (
            family
            for family in (
                "Microsoft YaHei UI",
                "Microsoft YaHei",
                "Noto Sans CJK SC",
                "Source Han Sans SC",
                "Segoe UI",
            )
            if family in installed_fonts
        ),
        "Segoe UI",
    )
    app.setFont(QFont(ui_family, 10))
    app.setStyleSheet(STYLE_SHEET.replace("__UI_FONT__", ui_family))
    window = MonitorWindow()
    window.show()
    if "--smoke-test" in sys.argv:
        QTimer.singleShot(250, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
