"""Modern Qt desktop interface for the SJTU course monitor.

The UI remains a thin desktop shell around the verified command-line programs.
It reads local runtime files and launches monitor.py/bootstrap.py as child
processes; network and course-selection behavior stays in the existing backend.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer, Qt
from PySide6.QtGui import QColor, QCloseEvent, QFont, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFrame,
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
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import config


ROOT = Path(__file__).resolve().parent
USER_ROLE = Qt.ItemDataRole.UserRole

COLORS = {
    "bg": "#F5F7FB",
    "surface": "#FFFFFF",
    "surface_alt": "#F8FAFC",
    "border": "#E2E8F0",
    "text": "#172033",
    "muted": "#667085",
    "primary": "#2563EB",
    "primary_hover": "#1D4ED8",
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
        self.setMinimumSize(1100, 720)

        self.monitor_process: QProcess | None = None
        self.once_process: QProcess | None = None
        self.bootstrap_process: QProcess | None = None
        self._groups_dirty = False
        self._refreshing_swap_options = False
        self._course_rows: list[dict] = []
        self._course_by_id: dict[str, dict] = {}
        self._choosed_ids: set[str] = set()
        self.groups: dict[str, dict] = {}
        self.selected_group: str | None = None

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

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(18, 24, 18, 18)
        side_layout.setSpacing(12)

        brand = QLabel("交我选监控")
        brand.setObjectName("brand")
        subtitle = QLabel("课程监控与优先换课")
        subtitle.setObjectName("sidebarMuted")
        side_layout.addWidget(brand)
        side_layout.addWidget(subtitle)
        side_layout.addSpacing(18)

        self.nav = QListWidget()
        self.nav.setObjectName("navigation")
        self.nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for text in ("运行概览", "选课设置", "优先换课", "课程快照", "运行日志"):
            self.nav.addItem(QListWidgetItem(text))
        side_layout.addWidget(self.nav)
        side_layout.addStretch()
        version = QLabel("Qt Desktop · 本地运行")
        version.setObjectName("sidebarMuted")
        side_layout.addWidget(version)
        shell.addWidget(sidebar)

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
        self.stack.addWidget(self._build_overview_page())
        self.stack.addWidget(self._build_course_page())
        self.stack.addWidget(self._build_swap_page())
        self.stack.addWidget(self._build_snapshot_page())
        self.stack.addWidget(self._build_log_page())
        content_layout.addWidget(self.stack, 1)

        status_row = QHBoxLayout()
        self.status_label = QLabel("●  就绪")
        self.status_label.setObjectName("statusText")
        shortcuts = QLabel("F5 刷新  ·  Ctrl+Enter 运行一次")
        shortcuts.setObjectName("muted")
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        status_row.addWidget(shortcuts)
        content_layout.addLayout(status_row)
        shell.addWidget(content, 1)

        self.nav.currentRowChanged.connect(self._switch_page)
        self.nav.setCurrentRow(0)
        self._install_shortcuts()

    def _install_shortcuts(self) -> None:
        from PySide6.QtGui import QKeySequence, QShortcut

        QShortcut(QKeySequence("F5"), self, activated=self.refresh)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.refresh)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.run_once)

    def _switch_page(self, index: int) -> None:
        titles = (
            ("运行概览", "查看监控状态并控制后台任务"),
            ("选课设置", "按方案组织教学班，并直观调整升级优先级"),
            ("优先换课", "检查自动换课模式和各方案执行状态"),
            ("课程快照", "查看最近一次本地课程人数快照"),
            ("运行日志", "查看最近 300 行变更记录"),
        )
        self.stack.setCurrentIndex(index)
        self.page_title.setText(titles[index][0])
        self.page_hint.setText(titles[index][1])

    # ---------- reusable widgets ----------

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
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

        metric_row = QHBoxLayout()
        metric_row.setSpacing(10)
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
            metric_row.addWidget(card)
        layout.addLayout(metric_row)

        output_card, output_layout = self._card()
        output_title = QHBoxLayout()
        label = QLabel("实时命令输出")
        label.setObjectName("sectionTitle")
        clear = self._button("清空", lambda: self.command_output.clear())
        output_title.addWidget(label)
        output_title.addStretch()
        output_title.addWidget(clear)
        output_layout.addLayout(output_title)
        self.command_output = QPlainTextEdit()
        self.command_output.setReadOnly(True)
        self.command_output.setObjectName("console")
        self.command_output.setFont(QFont("Cascadia Mono", 9))
        output_layout.addWidget(self.command_output, 1)
        layout.addWidget(output_card, 1)
        return page

    # ---------- course setup ----------

    def _build_course_page(self) -> QWidget:
        page, layout = self._page()

        steps = QHBoxLayout()
        steps.setSpacing(10)
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
            steps.addWidget(card)
        layout.addLayout(steps)

        data_card, data_layout = self._card(horizontal=True)
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

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_catalog_panel())
        splitter.addWidget(self._build_plan_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([700, 500])
        layout.addWidget(splitter, 1)

        footer = QHBoxLayout()
        self.save_state_label = QLabel("配置已保存")
        self.save_state_label.setObjectName("savedBadge")
        footer.addWidget(self.save_state_label)
        footer.addStretch()
        footer.addWidget(self._button("恢复内置默认", self._reset_default_groups))
        footer.addWidget(self._button("放弃未保存修改", self._reload_groups))
        self.save_groups_button = self._button(
            "保存选课方案", self._save_groups, role="primary"
        )
        self.save_groups_button.setEnabled(False)
        footer.addWidget(self.save_groups_button)
        layout.addLayout(footer)
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

        filters = QHBoxLayout()
        self.course_filter = QLineEdit()
        self.course_filter.setPlaceholderText("搜索课程名称、课程号或教学班…")
        self.course_filter.setClearButtonEnabled(True)
        self.course_filter.textChanged.connect(self._refresh_course_table)
        self.only_unassigned = QCheckBox("仅未加入方案")
        self.only_open = QCheckBox("仅有空位")
        self.only_unassigned.toggled.connect(self._refresh_course_table)
        self.only_open.toggled.connect(self._refresh_course_table)
        filters.addWidget(self.course_filter, 1)
        filters.addWidget(self.only_unassigned)
        filters.addWidget(self.only_open)
        layout.addLayout(filters)

        self.course_table = self._table(
            ("课程", "教学班", "人数/容量", "状态", "所在方案"), multi=True
        )
        self.course_table.itemSelectionChanged.connect(self._on_course_selection)
        self.course_table.itemDoubleClicked.connect(
            lambda _item: self._add_selected_to_group()
        )
        layout.addWidget(self.course_table, 1)

        footer = QHBoxLayout()
        self.course_selection_label = QLabel("可多选；双击也可快速加入当前方案")
        self.course_selection_label.setObjectName("muted")
        self.add_course_button = self._button(
            "加入当前方案  →", self._add_selected_to_group, role="primary"
        )
        self.add_course_button.setEnabled(False)
        footer.addWidget(self.course_selection_label, 1)
        footer.addWidget(self.add_course_button)
        layout.addLayout(footer)
        return panel

    def _build_plan_panel(self) -> QFrame:
        panel, layout = self._card()
        title = QLabel("我的选课方案")
        title.setObjectName("sectionTitle")
        hint = QLabel("每组代表同一门课，或只能保留一个的课程")
        hint.setObjectName("muted")
        layout.addWidget(title)
        layout.addWidget(hint)

        group_actions = QHBoxLayout()
        group_actions.addWidget(self._button("+ 新建方案组", self._new_group))
        group_actions.addWidget(self._button("重命名", self._rename_group))
        group_actions.addWidget(
            self._button("删除", self._delete_group, role="danger")
        )
        group_actions.addStretch()
        self.pe_check = QCheckBox("体育课方案")
        self.pe_check.toggled.connect(self._toggle_group_pe)
        group_actions.addWidget(self.pe_check)
        layout.addLayout(group_actions)

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

        member_actions = QHBoxLayout()
        self.group_summary_label = QLabel("请选择一个方案组")
        self.group_summary_label.setObjectName("muted")
        member_actions.addWidget(self.group_summary_label, 1)
        member_actions.addWidget(
            self._button("移出", self._remove_from_group, role="danger")
        )
        member_actions.addWidget(self._button("设为当前持有", self._set_as_held))
        member_actions.addWidget(
            self._button("下移", lambda: self._move_member(1))
        )
        member_actions.addWidget(
            self._button("上移", lambda: self._move_member(-1))
        )
        layout.addLayout(member_actions)
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
        layout.addWidget(card, 1)
        return page

    def _build_snapshot_page(self) -> QWidget:
        page, layout = self._page()
        card, card_layout = self._card()
        title_row = QHBoxLayout()
        title = QLabel("最近课程快照")
        title.setObjectName("sectionTitle")
        title_row.addWidget(title)
        title_row.addStretch()
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
        title_row.addWidget(self._button("刷新日志", self.refresh))
        card_layout.addLayout(title_row)
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setObjectName("console")
        self.log_output.setFont(QFont("Cascadia Mono", 9))
        card_layout.addWidget(self.log_output, 1)
        layout.addWidget(card, 1)
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
        self._refresh_snapshot_table(state, watched)
        self._refresh_log()
        self._refresh_swap_options()
        self._refresh_process_buttons()
        self._set_status("已刷新本地状态")

    def _load_course_rows(self, state: dict | None = None) -> None:
        state = state if state is not None else _read_json(config.STATE_FILE, {})
        catalog = _read_json(config.CATALOG_FILE, {})
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
                    "endpoint": course.get("endpoint")
                    or ("pe" if course.get("kklxdm") == "06" else "display"),
                }
        for jxb_id, course in state.items():
            rows[jxb_id] = {**rows.get(jxb_id, {}), **course, "jxb_id": jxb_id}
        self._course_rows = list(rows.values())
        self._course_by_id = rows

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

    def _refresh_course_table(self) -> None:
        if not hasattr(self, "course_table"):
            return
        text = self.course_filter.text().strip().lower()
        visible: list[tuple[dict, str | None]] = []
        for row in self._course_rows:
            group = self._group_of_jxb(row["jxb_id"])
            if self.only_unassigned.isChecked() and group:
                continue
            if self.only_open.isChecked() and not _has_spot(row):
                continue
            haystack = " ".join(
                str(row.get(key) or "") for key in ("kcmc", "jxbmc", "kch")
            ).lower()
            if text and text not in haystack:
                continue
            visible.append((row, group))

        visible.sort(
            key=lambda item: (
                str(item[0].get("kcmc") or item[0].get("kch") or ""),
                str(item[0].get("jxbmc") or ""),
            )
        )
        self.course_table.setSortingEnabled(False)
        self.course_table.setRowCount(len(visible))
        for table_row, (course, group) in enumerate(visible):
            jxb_id = course["jxb_id"]
            chosen = jxb_id in self._choosed_ids
            spot = _has_spot(course)
            values = (
                course.get("kcmc") or course.get("kch") or "未知课程",
                course.get("jxbmc") or "未知教学班",
                _seat_text(course),
                "当前已选" if chosen else ("有空位" if spot else "—"),
                group or "—",
            )
            for column, value in enumerate(values):
                self.course_table.setItem(
                    table_row,
                    column,
                    self._item(str(value), jxb_id if column == 0 else None),
                )
            if chosen:
                self._set_row_tint(
                    self.course_table,
                    table_row,
                    COLORS["primary_soft"],
                    COLORS["primary_hover"],
                )
            elif spot:
                self._set_row_tint(
                    self.course_table,
                    table_row,
                    COLORS["success_soft"],
                    COLORS["success"],
                )
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
        else:
            self.course_selection_label.setText("可多选；双击也可快速加入当前方案")
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
        for row_index, jxb_id in enumerate(ids):
            course = self._course_by_id.get(jxb_id, {})
            notes = []
            if row_index == len(ids) - 1:
                notes.append("当前持有")
            if jxb_id in self._choosed_ids:
                notes.append("已选")
            values = (
                str(row_index + 1),
                course.get("kcmc") or "未获取课程数据",
                course.get("jxbmc") or "未知教学班",
                _seat_text(course),
                " / ".join(notes) or "目标",
            )
            for column, value in enumerate(values):
                self.member_table.setItem(
                    row_index,
                    column,
                    self._item(value, jxb_id if column == 0 else None),
                )
            if row_index == len(ids) - 1:
                self._set_row_tint(
                    self.member_table,
                    row_index,
                    COLORS["warning_soft"],
                    COLORS["warning"],
                )
        if ids:
            held = self._course_by_id.get(ids[-1], {})
            held_name = held.get("jxbmc") or _short_id(ids[-1])
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
        target = self.groups[self.selected_group]["priority"]
        for jxb_id in jxb_ids:
            for group in self.groups.values():
                if jxb_id in group["priority"]:
                    group["priority"].remove(jxb_id)
            if jxb_id in self._choosed_ids or not target:
                target.append(jxb_id)
            else:
                target.insert(len(target) - 1, jxb_id)
        self._refresh_group_table()
        self._refresh_member_table()
        self._refresh_course_table()
        self._mark_groups_dirty(
            f"已加入 {len(jxb_ids)} 个教学班到“{self.selected_group}”，尚未保存"
        )

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
                label = row.get("jxbmc") or row.get("kcmc") or _short_id(jxb_id)
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
                _short_id(held.get(name)) or "—",
                str(sum(jxb_id in watched for jxb_id in ids)),
                ", ".join(map(_short_id, done)) or "—",
                ", ".join(map(_short_id, failed)) or "—",
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

    def _refresh_snapshot_table(self, state: dict, watched: set[str]) -> None:
        rows = []
        for jxb_id, course in state.items():
            spot = _has_spot(course)
            rows.append(
                (
                    jxb_id not in watched,
                    jxb_id,
                    "是" if jxb_id in watched else "—",
                    config.find_group(jxb_id) or "—",
                    course.get("kcmc") or course.get("kch_id") or "未知课程",
                    course.get("jxbmc") or "未知教学班",
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

    def _refresh_log(self) -> None:
        if not config.LOG_FILE.exists():
            self.log_output.setPlainText("changes.log 尚不存在。")
            return
        lines = config.LOG_FILE.read_text("utf-8", errors="replace").splitlines()
        self.log_output.setPlainText("\n".join(lines[-300:]))
        scrollbar = self.log_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

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
        process.setProcessEnvironment(environment)
        process.readyReadStandardOutput.connect(
            lambda p=process: self._read_process_output(p)
        )
        process.finished.connect(
            lambda code, _status, name=label, p=process: self._process_finished(
                name, p, code
            )
        )
        self._append_output(f"$ {sys.executable} {' '.join(args)}")
        process.start(sys.executable, args)
        if not process.waitForStarted(3000):
            self._append_output(f"[{label}] 启动失败：{process.errorString()}")
        return process

    def _read_process_output(self, process: QProcess) -> None:
        data = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if data:
            self.command_output.moveCursor(QTextCursor.MoveOperation.End)
            self.command_output.insertPlainText(data)
            self.command_output.verticalScrollBar().setValue(
                self.command_output.verticalScrollBar().maximum()
            )

    def _process_finished(self, label: str, process: QProcess, code: int) -> None:
        self._read_process_output(process)
        self._append_output(f"[{label}] exit={code}")
        if label == "monitor" and process is self.monitor_process:
            self.monitor_process = None
            self._set_monitor_running(False)
        elif label == "once" and process is self.once_process:
            self.once_process = None
        elif label == "bootstrap" and process is self.bootstrap_process:
            self.bootstrap_process = None
        self._refresh_process_buttons()
        self.refresh()

    def _append_output(self, text: str) -> None:
        self.command_output.appendPlainText(text)
        self.command_output.verticalScrollBar().setValue(
            self.command_output.verticalScrollBar().maximum()
        )

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
            self._append_output("监控进程未及时退出，已强制结束。")

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
        self.start_button.setEnabled(not monitor_running)
        self.stop_button.setEnabled(monitor_running)
        self.once_button.setEnabled(not once_running)
        self.bootstrap_button.setEnabled(not bootstrap_running)

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
        event.accept()


STYLE_SHEET = f"""
* {{
    font-family: "Segoe UI";
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
QLabel, QCheckBox {{
    background: transparent;
}}
QFrame#sidebar {{
    background: #172033;
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
    border-radius: 10px;
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
    border-radius: 7px;
    padding: 7px 13px;
    min-height: 18px;
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
    app.setStyleSheet(STYLE_SHEET)
    window = MonitorWindow()
    window.show()
    if "--smoke-test" in sys.argv:
        QTimer.singleShot(250, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
