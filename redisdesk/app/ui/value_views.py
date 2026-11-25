"""六种 Redis 类型的值查看器面板（浏览页右侧）。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from redisdesk.app.ui.i18n import format_ttl, key_type_label
from redisdesk.app.ui.widgets import Badge, DataTable, EmptyState
from redisdesk.core.model import ValuePage


class _MetaBar(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.key_label = QLabel("未选择键")
        self.key_label.setObjectName("sectionTitle")
        self.type_badge = Badge("—", "muted")
        self.ttl_badge = Badge("TTL —", "muted")
        self.size_label = QLabel("")
        self.size_label.setObjectName("muted")
        row.addWidget(self.key_label, 1)
        row.addWidget(self.type_badge)
        row.addWidget(self.ttl_badge)
        row.addWidget(self.size_label)

    def clear(self) -> None:
        self.key_label.setText("未选择键")
        self.type_badge.apply("muted", "—")
        self.ttl_badge.apply("muted", "TTL —")
        self.size_label.setText("")

    def bind(self, key: str, key_type: str, ttl: int | None = None, size: str = "") -> None:
        self.key_label.setText(key)
        self.type_badge.apply("info", key_type_label(key_type))
        self.ttl_badge.apply("muted", f"TTL {format_ttl(ttl)}")
        self.size_label.setText(size)


class StringViewer(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        self.meta = QLabel("长度 — · 编码 —")
        self.meta.setObjectName("muted")
        self.note = QLabel("")
        self.note.setObjectName("inlineInfo")
        self.note.setWordWrap(True)
        self.note.hide()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.body = QPlainTextEdit()
        self.body.setObjectName("mono")
        self.body.setReadOnly(True)
        self.body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.body.setMinimumHeight(120)
        self.body.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.body.setPlaceholderText("字符串值将显示在这里。超过内联上限时按块加载。")
        self.more = QPushButton("加载下一块")
        self.more.setEnabled(False)
        self.more.hide()
        layout.addWidget(self.meta)
        layout.addWidget(self.note)
        layout.addWidget(self.body, 1)

    def clear(self) -> None:
        self.meta.setText("长度 — · 编码 —")
        self.note.hide()
        self.body.setPlainText("")
        self.more.setEnabled(False)

    def show_page(self, page: ValuePage) -> None:
        item = page.items[0] if page.items else None
        encoding = "二进制" if (item and item.is_binary) else "UTF-8"
        length = page.total if page.total is not None else (item.value_bytes_len if item else 0)
        self.meta.setText(f"长度 {length} 字节 · {encoding}")
        if page.note or (item and item.masked):
            parts = [p for p in (page.note, item.mask_reason if item and item.masked else "") if p]
            self.note.setText("；".join(parts) if parts else "")
            self.note.setVisible(bool(parts))
        else:
            self.note.hide()
        self.body.setPlainText(item.value_text if item else "")
        self.more.setEnabled(page.has_more)


class FieldTableViewer(QWidget):
    row_action_requested = Signal(int)

    def __init__(self, headers: list[str], empty_hint: str, parent: QWidget | None = None, *, action_label: str | None = None) -> None:
        super().__init__(parent)
        self.action_label = action_label
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        self.filter = QLineEdit()
        self.filter.setObjectName("pageFilter")
        self.filter.setPlaceholderText("筛选当前页…")
        self.filter.setClearButtonEnabled(True)
        self.filter.setMinimumHeight(28)
        self.filter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bar.addWidget(self.filter, 1)
        layout.addLayout(bar)
        labels = list(headers) + (['操作'] if action_label else [])
        self.table = DataTable(labels)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.table.setMinimumHeight(120)
        if action_label:
            header = self.table.horizontalHeader()
            header.setStretchLastSection(False)
            value_col = 1 if len(headers) > 1 else 0
            header.setSectionResizeMode(value_col, header.ResizeMode.Stretch)
            header.setSectionResizeMode(len(headers), header.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)
        pager = QHBoxLayout()
        pager.setContentsMargins(0, 0, 0, 0)
        pager.setSpacing(8)
        self.prev = QPushButton("上一页")
        self.next = QPushButton("下一页")
        self.page_info = QLabel("暂无数据")
        self.page_info.setObjectName("muted")
        self.prev.setEnabled(False)
        self.next.setEnabled(False)
        self.prev.setMinimumHeight(28)
        self.next.setMinimumHeight(28)
        pager.addWidget(self.prev)
        pager.addWidget(self.next)
        pager.addWidget(self.page_info, 1)
        layout.addLayout(pager)
        self.note = QLabel("")
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        self.note.hide()
        layout.addWidget(self.note)
        self._all_rows: list[list[str]] = []
        self.filter.textChanged.connect(self._apply_filter)

    def clear(self) -> None:
        self._all_rows = []
        self.table.set_rows([])
        self.page_info.setText("暂无数据")
        self.note.hide()
        self.prev.setEnabled(False)
        self.next.setEnabled(False)
        self.filter.setText("")

    def show_page(self, rows: list[list[str]], *, total: int | None, has_more: bool, page_index: int, note: str = "", identities: list | None = None) -> None:
        if identities is None:
            identities = [None] * len(rows)
        self._all_rows = list(zip(list(rows), list(identities)))
        self._apply_filter()
        total_text = f"共 {total} 条" if total is not None else f"{len(rows)} 条"
        self.page_info.setText(f"{total_text} · 第 {page_index + 1} 页")
        self.next.setEnabled(has_more)
        self.prev.setEnabled(page_index > 0)
        if note:
            self.note.setText(note)
            self.note.show()
        else:
            self.note.hide()

    def _apply_filter(self) -> None:
        keyword = self.filter.text().strip().lower()
        paired = self._all_rows
        if keyword:
            paired = [item for item in paired if any(keyword in cell.lower() for cell in item[0])]
        self.table.set_rows([row for row, _ident in paired])
        for r, (_row, ident) in enumerate(paired):
            item = self.table.item(r, 0)
            if item is not None and ident is not None:
                item.setData(Qt.ItemDataRole.UserRole, ident)
        self._install_row_actions()

    def _install_row_actions(self) -> None:
        if not self.action_label:
            return
        action_col = self.table.columnCount() - 1
        for row in range(self.table.rowCount()):
            button = QPushButton(self.action_label)
            button.setObjectName('rowAction')
            button.setFlat(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet('padding: 2px 8px 2px 0px; text-align: left; min-width: 0;')
            button.clicked.connect(lambda *_args, visible_row=row: self._emit_row_action(visible_row))
            host = QWidget()
            host.setObjectName('rowActions')
            layout = QHBoxLayout(host)
            layout.setContentsMargins(0, 0, 8, 0)
            layout.setSpacing(4)
            layout.addWidget(button)
            layout.addStretch()
            self.table.setCellWidget(row, action_col, host)

    def _emit_row_action(self, row: int) -> None:
        if 0 <= row < self.table.rowCount():
            self.table.selectRow(row)
            self.row_action_requested.emit(row)


class ValueInspector(QWidget):
    """键元信息 + 六类型面板。未连接/未选键时展示空状态。"""

    TAB_KEYS = ("string", "hash", "list", "set", "zset", "stream")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)
        self.disconnected = QLabel("尚未连接 Redis。请先在顶部选择连接，扫描与取值将在建立连接后可用。")
        self.disconnected.setObjectName("inlineInfo")
        self.disconnected.setWordWrap(True)
        root.addWidget(self.disconnected)
        self.meta = _MetaBar()
        root.addWidget(self.meta)

        self.tabs = QTabWidget()
        self.string_view = StringViewer()
        self.hash_view = FieldTableViewer(["字段", "值", "长度"], "该哈希的字段将按页显示。")
        self.list_view = FieldTableViewer(["索引", "值", "长度"], "列表元素按页显示。")
        self.set_view = FieldTableViewer(["成员", "长度"], "集合成员按页显示。")
        self.zset_view = FieldTableViewer(["成员", "分数", "长度"], "有序集合成员按分数页显示。")
        self.stream_view = FieldTableViewer(["消息 ID", "字段", "值"], "流消息按 ID 分页显示。")
        self.views = {
            "string": self.string_view,
            "hash": self.hash_view,
            "list": self.list_view,
            "set": self.set_view,
            "zset": self.zset_view,
            "stream": self.stream_view,
        }
        labels = {
            "string": "字符串",
            "hash": "哈希",
            "list": "列表",
            "set": "集合",
            "zset": "有序集合",
            "stream": "流",
        }
        for key in self.TAB_KEYS:
            self.tabs.addTab(self.views[key], labels[key])
        root.addWidget(self.tabs, 1)
        self.set_disconnected(True)

    def set_disconnected(self, disconnected: bool) -> None:
        self.disconnected.setVisible(disconnected)
        if disconnected:
            self.meta.clear()
            self.clear_values()

    def clear_values(self) -> None:
        self.string_view.clear()
        self.hash_view.clear()
        self.list_view.clear()
        self.set_view.clear()
        self.zset_view.clear()
        self.stream_view.clear()

    def show_empty_key(self) -> None:
        self.meta.clear()
        self.clear_values()
        self.tabs.setCurrentIndex(0)

    def show_page(self, page: ValuePage, *, ttl: int | None = None) -> None:
        key_type = page.key_type if page.key_type in self.views else "string"
        size = f"{page.total} 项" if page.total is not None and key_type != "string" else (
            f"{page.total} 字节" if page.total is not None else ""
        )
        self.meta.bind(page.key, key_type, ttl, size)
        self.clear_values()
        if key_type == "string":
            self.string_view.show_page(page)
        elif key_type == "hash":
            rows = [
                [item.field or "", item.value_text, str(item.value_bytes_len)]
                for item in page.items
            ]
            self.hash_view.show_page(rows, total=page.total, has_more=page.has_more, page_index=page.page_index, note=page.note)
        elif key_type == "list":
            rows = [
                [str(item.field or i), item.value_text, str(item.value_bytes_len)]
                for i, item in enumerate(page.items)
            ]
            self.list_view.show_page(rows, total=page.total, has_more=page.has_more, page_index=page.page_index, note=page.note)
        elif key_type == "set":
            rows = [[item.value_text, str(item.value_bytes_len)] for item in page.items]
            self.set_view.show_page(rows, total=page.total, has_more=page.has_more, page_index=page.page_index, note=page.note)
        elif key_type == "zset":
            rows = [[item.value_text, item.score or "", str(item.value_bytes_len)] for item in page.items]
            self.zset_view.show_page(rows, total=page.total, has_more=page.has_more, page_index=page.page_index, note=page.note)
        else:
            rows = [
                [item.item_id or "", item.field or "", item.value_text]
                for item in page.items
            ]
            self.stream_view.show_page(rows, total=page.total, has_more=page.has_more, page_index=page.page_index, note=page.note)
        self.tabs.setCurrentIndex(self.TAB_KEYS.index(key_type))
