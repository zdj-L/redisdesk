"""诊断页：拉取只读 INFO、慢查询和客户端列表，以标题+内容分区展示。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QFrame,
    QHeaderView,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from redisdesk.app.services.diag_service import DiagnosticLists, DiagnosticsSnapshot
from redisdesk.app.ui.diagnostics_metrics import (
    DASH,
    PAGE_SECTION_ORDER,
    SECTION_CLIENT_LIST,
    SECTION_ORDER,
    SECTION_SLOWLOG,
    build_card_pairs,
)
from redisdesk.app.ui.i18n import env_label
from redisdesk.app.ui.jobs import FunctionWorker
from redisdesk.app.ui.widgets import DataTable, EmptyState, KeyValueTable, PageFrame
from redisdesk.core.errors import AppError
from redisdesk.core.model import ConnectionProfile, SessionContext

# Compact metrics tables so early sections fit in a 1280x800 main window.
_ROW_H = 32
_HEADER_H = 32
_LIST_ROW_H = 28
_LIST_VISIBLE_ROWS = 16
_SLOWLOG_MAX_ROWS_HEIGHT = 50


class DiagSection(QWidget):
    """A typographic heading plus a compact key-value table. Height follows content."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("diagSection")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("diagSectionTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        rule = QFrame()
        rule.setObjectName("diagSectionRule")
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setFixedHeight(1)
        rule.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.table = KeyValueTable()
        self.table.setObjectName("diagTable")
        self.table.verticalHeader().setDefaultSectionSize(_ROW_H)
        header = self.table.horizontalHeader()
        header.setMinimumHeight(_HEADER_H)
        header.setMaximumHeight(_HEADER_H)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.title_label, 0)
        layout.addWidget(rule, 0)
        layout.addWidget(self.table, 0, Qt.AlignmentFlag.AlignTop)

    def natural_height(self) -> int:
        self.table.fit_to_contents()
        layout = self.layout()
        margins = layout.contentsMargins() if layout is not None else self.contentsMargins()
        spacing = layout.spacing() if layout is not None else 0
        title_h = max(self.title_label.sizeHint().height(), 16)
        rule_h = 1
        return margins.top() + title_h + spacing + rule_h + spacing + self.table.height() + margins.bottom()


class DiagListSection(QWidget):
    """Same typographic heading, hosting a compact read-only list table plus a muted note."""

    def __init__(self, title: str, headers: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("diagSection")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("diagSectionTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        rule = QFrame()
        rule.setObjectName("diagSectionRule")
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setFixedHeight(1)
        rule.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.note = QLabel("")
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        self.table = DataTable(headers)
        self.table.setObjectName("diagListTable")
        self.table.verticalHeader().setDefaultSectionSize(_LIST_ROW_H)
        header = self.table.horizontalHeader()
        header.setMinimumHeight(_HEADER_H)
        header.setMaximumHeight(_HEADER_H)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.title_label, 0)
        layout.addWidget(rule, 0)
        layout.addWidget(self.note, 0)
        layout.addWidget(self.table, 0, Qt.AlignmentFlag.AlignTop)

    def set_rows(self, rows: list[list[str]], note: str, *, visible_rows: int, follow_content: bool) -> None:
        self.note.setText(note)
        self.note.setVisible(bool(note))
        self.table.set_rows(rows)
        for r in range(self.table.rowCount()):
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                if item is not None and item.text():
                    item.setToolTip(item.text())
        self.fit_table(visible_rows=visible_rows, follow_content=follow_content)

    def fit_table(self, *, visible_rows: int, follow_content: bool) -> None:
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        if self.table.columnCount() >= 2:
            last = self.table.columnCount() - 1
            for col in range(last):
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(last, QHeaderView.ResizeMode.Stretch)
        row_h = self.table.verticalHeader().defaultSectionSize() or _LIST_ROW_H
        rows = self.table.rowCount()
        header_h = _HEADER_H
        margins = self.table.contentsMargins()
        frame = max(self.table.frameWidth(), 1) * 2
        extra = margins.top() + margins.bottom() + 4
        if rows <= 0:
            self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.table.setFixedHeight(header_h + frame + extra)
            return
        content_h = header_h + rows * row_h + frame + extra
        cap = header_h + visible_rows * row_h + frame + extra
        if follow_content or rows <= visible_rows:
            self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.table.setFixedHeight(content_h)
        else:
            self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.table.setFixedHeight(cap)

    def natural_height(self) -> int:
        follow = self.table.objectName() == "diagListTable" and self.table.verticalScrollBar().maximum() == 0
        visible = _SLOWLOG_MAX_ROWS_HEIGHT if self.title_label.text() == SECTION_SLOWLOG else _LIST_VISIBLE_ROWS
        self.fit_table(visible_rows=visible, follow_content=(self.title_label.text() == SECTION_SLOWLOG))
        layout = self.layout()
        margins = layout.contentsMargins() if layout is not None else self.contentsMargins()
        spacing = layout.spacing() if layout is not None else 0
        title_h = max(self.title_label.sizeHint().height(), 16)
        rule_h = 1
        note_h = self.note.sizeHint().height() if self.note.isVisible() and self.note.text() else 0
        parts = [title_h, rule_h]
        if note_h:
            parts.append(note_h)
        parts.append(self.table.height())
        return margins.top() + sum(parts) + spacing * (len(parts) - 1) + margins.bottom()


def _slowlog_rows(lists: DiagnosticLists) -> list[list[str]]:
    return [[row.log_id, row.time_text, row.duration_text, row.command] for row in lists.slowlog_rows]


def _client_rows(lists: DiagnosticLists) -> list[list[str]]:
    return [
        [row.client_id, row.addr, row.name, row.db, row.idle, row.flags, row.command]
        for row in lists.client_rows
    ]


class DiagnosticsPage(QWidget):
    def __init__(self, service=None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("diagPage")
        self.service = service
        self.session: SessionContext | None = None
        self.profile: ConnectionProfile | None = None
        self._worker: FunctionWorker | None = None
        self._generation = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.page = PageFrame(
            "",
            "\u67e5\u770b\u670d\u52a1\u5668 INFO \u5173\u952e\u6307\u6807\u3001\u6162\u67e5\u8be2\u548c\u5ba2\u6237\u7aef\u5217\u8868\u3002",
        )
        self.status = QLabel("\u5c1a\u672a\u8fde\u63a5")
        self.status.setObjectName("hint")
        self.status.setWordWrap(False)
        self.status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.page.add_tool(self.status, stretch=1)

        self.empty = EmptyState(
            "\u8fd8\u6ca1\u6709\u53ef\u8bca\u65ad\u7684\u8fde\u63a5",
            "\u8bf7\u5148\u5728\u9876\u90e8\u9009\u62e9\u4e00\u6761\u5df2\u6388\u6743\u8fde\u63a5\uff0c\u5373\u53ef\u67e5\u770b\u670d\u52a1\u5668\u3001\u5185\u5b58\u3001\u6162\u67e5\u8be2\u548c\u5ba2\u6237\u7aef\u4fe1\u606f\u3002",
        )
        page_layout = self.page.layout()
        if page_layout is not None:
            page_layout.setContentsMargins(28, 16, 28, 16)
            page_layout.setSpacing(12)
        self.page.body.setSpacing(12)

        self.grid_host = QWidget()
        self.grid_host.setObjectName("diagGridHost")
        self.grid_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.grid_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        stack = QVBoxLayout(self.grid_host)
        stack.setContentsMargins(0, 0, 0, 8)
        stack.setSpacing(12)
        stack.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.tables: dict[str, KeyValueTable] = {}
        self.list_tables: dict[str, DataTable] = {}
        self.sections: dict[str, QWidget] = {}
        placeholders = build_card_pairs({})
        for title in SECTION_ORDER:
            section = DiagSection(title)
            section.table.set_pairs(placeholders.get(title) or [("\u6307\u6807", DASH)])
            stack.addWidget(section, 0)
            self.tables[title] = section.table
            self.sections[title] = section
        self.slowlog_section = DiagListSection(
            SECTION_SLOWLOG,
            ["ID", "\u65f6\u95f4", "\u8017\u65f6", "\u547d\u4ee4"],
        )
        self.client_list_section = DiagListSection(
            SECTION_CLIENT_LIST,
            ["ID", "\u5730\u5740", "\u540d\u79f0", "\u5e93", "\u7a7a\u95f2", "\u6807\u5fd7", "\u547d\u4ee4"],
        )
        empty_lists = DiagnosticLists()
        empty_lists.slowlog_note = "\u6682\u65e0\u6570\u636e"
        empty_lists.client_note = "\u6682\u65e0\u6570\u636e"
        self.slowlog_section.set_rows([], empty_lists.slowlog_note, visible_rows=_SLOWLOG_MAX_ROWS_HEIGHT, follow_content=True)
        self.client_list_section.set_rows([], empty_lists.client_note, visible_rows=_LIST_VISIBLE_ROWS, follow_content=False)
        stack.addWidget(self.slowlog_section, 0)
        stack.addWidget(self.client_list_section, 0)
        self.sections[SECTION_SLOWLOG] = self.slowlog_section
        self.sections[SECTION_CLIENT_LIST] = self.client_list_section
        self.list_tables[SECTION_SLOWLOG] = self.slowlog_section.table
        self.list_tables[SECTION_CLIENT_LIST] = self.client_list_section.table
        stack.addStretch(1)
        # Alias kept so older probes can still iterate the metric sections.
        self.cards = {title: self.sections[title] for title in SECTION_ORDER}
        self.grid_scroll = QScrollArea()
        self.grid_scroll.setObjectName("diagGridScroll")
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.grid_scroll.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.grid_scroll.viewport().setObjectName("diagGridViewport")
        self.grid_scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.grid_scroll.setWidget(self.grid_host)
        self.page.add_body(self.empty, 1)
        self.page.add_body(self.grid_scroll, 1)
        self.grid_scroll.hide()
        self._sync_stack_height()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root.addWidget(self.page)
        self.set_context(None, None)

    def set_context(self, session: SessionContext | None, profile: ConnectionProfile | None) -> None:
        self.session = session
        changed = profile is not self.profile
        self.profile = profile
        connected = profile is not None
        self.empty.setVisible(not connected)
        self.grid_scroll.setVisible(connected)
        if connected:
            self._sync_stack_height()
        if not profile:
            self.status.setText("\u5c1a\u672a\u9009\u62e9\u8fde\u63a5\u3002")
            self.show_info({})
            self.show_lists(DiagnosticLists(slowlog_note="\u6682\u65e0\u6570\u636e", client_note="\u6682\u65e0\u6570\u636e"))
            self._generation += 1
            return
        if changed or not self._has_live_values():
            self.show_info({})
            self.show_lists(DiagnosticLists(slowlog_note="\u6682\u65e0\u6570\u636e", client_note="\u6682\u65e0\u6570\u636e"))
            self._start_fetch()

    def _has_live_values(self) -> bool:
        table = next(iter(self.tables.values()), None)
        if table is None or table.rowCount() == 0:
            return False
        item = table.item(0, 1)
        return item is not None and item.text() not in {"", DASH}

    def show_info(self, info: dict[str, str]) -> None:
        sections = build_card_pairs(info)
        for title, table in self.tables.items():
            table.set_pairs(sections.get(title) or [("\u6307\u6807", DASH)])
        self._sync_stack_height()

    def show_lists(self, lists: DiagnosticLists | None) -> None:
        data = lists or DiagnosticLists()
        self.slowlog_section.set_rows(
            _slowlog_rows(data),
            data.slowlog_note or ("\u65e0\u6cd5\u62c9\u53d6\u6162\u67e5\u8be2\uff1a{0}".format(data.slowlog_error) if data.slowlog_error else "\u6682\u65e0\u6570\u636e"),
            visible_rows=_SLOWLOG_MAX_ROWS_HEIGHT,
            follow_content=True,
        )
        self.client_list_section.set_rows(
            _client_rows(data),
            data.client_note or ("\u65e0\u6cd5\u62c9\u53d6\u5ba2\u6237\u7aef\u5217\u8868\uff1a{0}".format(data.client_error) if data.client_error else "\u6682\u65e0\u6570\u636e"),
            visible_rows=_LIST_VISIBLE_ROWS,
            follow_content=False,
        )
        self._sync_stack_height()

    def show_snapshot(self, snapshot: DiagnosticsSnapshot) -> None:
        self.show_info(snapshot.info)
        self.show_lists(snapshot.lists)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._sync_stack_height()

    def _sync_stack_height(self) -> None:
        """Size each section to its table and lock the host min height so a short viewport scrolls."""
        if not hasattr(self, "grid_host") or getattr(self, "_syncing_stack", False):
            return
        self._syncing_stack = True
        try:
            for title in PAGE_SECTION_ORDER:
                section = self.sections.get(title)
                if section is None:
                    continue
                needed = section.natural_height()
                section.setMinimumHeight(needed)
                section.setMaximumHeight(16777215)
            layout = self.grid_host.layout()
            if layout is None:
                return
            layout.activate()
            host_h = max(layout.sizeHint().height(), layout.minimumSize().height())
            self.grid_host.setMinimumHeight(host_h)
        finally:
            self._syncing_stack = False

    def _start_fetch(self) -> None:
        profile = self.profile
        session = self.session
        if profile is None:
            return
        self._generation += 1
        generation = self._generation
        if self.service is None or session is None:
            self.status.setText("\u5f53\u524d\u65e0\u6cd5\u62c9\u53d6 INFO\uff1a\u672a\u7ed1\u5b9a\u8fde\u63a5\u670d\u52a1\u3002")
            self.show_info({})
            self.show_lists(DiagnosticLists(slowlog_note="\u6682\u65e0\u6570\u636e", client_note="\u6682\u65e0\u6570\u636e"))
            return
        env = env_label(profile.env)
        self.status.setText("\u6b63\u5728\u62c9\u53d6\u5b9e\u2060\u65f6 INFO\u3001\u6162\u67e5\u8be2\u548c\u5ba2\u6237\u7aef\u5217\u8868\u2026")
        service = self.service

        def work():
            fetch = getattr(service, "fetch_diagnostics", None)
            if fetch is not None:
                return fetch(session, profile)
            info = service.fetch_info(session, profile)
            return DiagnosticsSnapshot(info={str(k): v if isinstance(v, str) else str(v) for k, v in (info or {}).items()}, lists=DiagnosticLists())

        worker = FunctionWorker(work, parent=self)
        self._worker = worker
        worker.signals.finished.connect(lambda result, g=generation, p=profile, e=env: self._on_fetch_ok(g, p, e, result))
        worker.signals.failed.connect(lambda exc, g=generation, p=profile: self._on_fetch_fail(g, p, exc))
        worker.start()

    def _on_fetch_ok(self, generation: int, profile: ConnectionProfile, env: str, result) -> None:
        if generation != self._generation or self.profile is not profile:
            return
        if isinstance(result, DiagnosticsSnapshot):
            self.show_snapshot(result)
            lists = result.lists
        else:
            data = {str(k): v if isinstance(v, str) else str(v) for k, v in (result or {}).items()}
            self.show_info(data)
            lists = DiagnosticLists()
            self.show_lists(lists)
        parts = ["\u5f53\u524d\u8fde\u63a5\uff1a{name}\uff08{env}\uff09\u3002\u6307\u6807\u6765\u81ea\u5b9e\u2060\u65f6 INFO".format(name=profile.name, env=env)]
        if lists.slowlog_error and lists.client_error:
            parts.append("\u6162\u67e5\u8be2\u4e0e\u5ba2\u6237\u7aef\u5217\u8868\u672a\u80fd\u5b8c\u6574\u62c9\u53d6")
        elif lists.slowlog_error:
            parts.append("\u6162\u67e5\u8be2\u672a\u80fd\u62c9\u53d6")
        elif lists.client_error:
            parts.append("\u5ba2\u6237\u7aef\u5217\u8868\u672a\u80fd\u62c9\u53d6")
        else:
            parts.append("\u5df2\u62c9\u53d6\u6162\u67e5\u8be2\u4e0e\u5ba2\u6237\u7aef\u5217\u8868")
        self.status.setText("\u3002".join(parts) + "\u3002")

    def _on_fetch_fail(self, generation: int, profile: ConnectionProfile, exc) -> None:
        if generation != self._generation or self.profile is not profile:
            return
        self.show_info({})
        self.show_lists(DiagnosticLists(slowlog_note="\u6682\u65e0\u6570\u636e", client_note="\u6682\u65e0\u6570\u636e"))
        message = exc.message if isinstance(exc, AppError) else str(exc)
        self.status.setText("\u65e0\u6cd5\u62c9\u53d6 INFO\uff1a{reason}".format(reason=message or type(exc).__name__))
