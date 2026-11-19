"""诊断页：拉取只读 INFO，以标题+内容分区展示；未选择连接时展示空状态。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from redisdesk.app.ui.diagnostics_metrics import DASH, SECTION_ORDER, build_card_pairs
from redisdesk.app.ui.i18n import env_label
from redisdesk.app.ui.jobs import FunctionWorker
from redisdesk.app.ui.widgets import EmptyState, KeyValueTable, PageFrame
from redisdesk.core.errors import AppError
from redisdesk.core.model import ConnectionProfile, SessionContext

# Compact metrics tables so two section rows fit in a 1280x800 main window.
_ROW_H = 32
_HEADER_H = 32


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
        # title, rule, table with two spacings
        return margins.top() + title_h + spacing + rule_h + spacing + self.table.height() + margins.bottom()


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
        self.page = PageFrame("", "\u67e5\u770b\u670d\u52a1\u5668 INFO \u5173\u952e\u6307\u6807\u3002\u6162\u67e5\u8be2\u548c\u5ba2\u6237\u7aef\u5217\u8868\u5c06\u5728\u540e\u7eed\u7248\u672c\u63d0\u4f9b\u3002")
        self.status = QLabel("\u5c1a\u672a\u8fde\u63a5")
        self.status.setObjectName("hint")
        self.status.setWordWrap(False)
        self.status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.page.add_tool(self.status, stretch=1)

        self.empty = EmptyState("\u8fd8\u6ca1\u6709\u53ef\u8bca\u65ad\u7684\u8fde\u63a5", "\u8bf7\u5148\u5728\u9876\u90e8\u9009\u62e9\u4e00\u6761\u5df2\u6388\u6743\u8fde\u63a5\uff0c\u5373\u53ef\u67e5\u770b\u670d\u52a1\u5668\u3001\u5185\u5b58\u548c\u5ba2\u6237\u7aef\u4fe1\u606f\u3002")
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
        self.sections: dict[str, DiagSection] = {}
        placeholders = build_card_pairs({})
        for title in SECTION_ORDER:
            section = DiagSection(title)
            section.table.set_pairs(placeholders.get(title) or [("\u6307\u6807", DASH)])
            stack.addWidget(section, 0)
            self.tables[title] = section.table
            self.sections[title] = section
        stack.addStretch(1)
                # Alias kept so older probes can still iterate the same widgets.
        self.cards = self.sections
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
            self._generation += 1
            return
        if changed or not self._has_live_values():
            self.show_info({})
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

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._sync_stack_height()

    def _sync_stack_height(self) -> None:
        """Size each section to its table and lock the host min height so a short viewport scrolls."""
        if not hasattr(self, "grid_host") or getattr(self, "_syncing_stack", False):
            return
        self._syncing_stack = True
        try:
            for section in self.sections.values():
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
            return
        env = env_label(profile.env)
        self.status.setText("\u6b63\u5728\u62c9\u53d6\u5b9e\u2060\u65f6 INFO\u2026")
        service = self.service

        def work():
            return service.fetch_info(session, profile)

        worker = FunctionWorker(work, parent=self)
        self._worker = worker
        worker.signals.finished.connect(lambda result, g=generation, p=profile, e=env: self._on_info_ok(g, p, e, result))
        worker.signals.failed.connect(lambda exc, g=generation, p=profile: self._on_info_fail(g, p, exc))
        worker.start()

    def _on_info_ok(self, generation: int, profile: ConnectionProfile, env: str, info) -> None:
        if generation != self._generation or self.profile is not profile:
            return
        data = {str(k): v if isinstance(v, str) else str(v) for k, v in (info or {}).items()}
        self.show_info(data)
        self.status.setText("\u5f53\u524d\u8fde\u63a5\uff1a{name}\uff08{env}\uff09\u3002\u6307\u6807\u6765\u81ea\u5b9e\u2060\u65f6 INFO\u3002".format(name=profile.name, env=env))

    def _on_info_fail(self, generation: int, profile: ConnectionProfile, exc) -> None:
        if generation != self._generation or self.profile is not profile:
            return
        self.show_info({})
        message = exc.message if isinstance(exc, AppError) else str(exc)
        self.status.setText("\u65e0\u6cd5\u62c9\u53d6 INFO\uff1a{reason}".format(reason=message or type(exc).__name__))
