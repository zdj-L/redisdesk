"""Key 浏览 + 六类型值查看器（左右分栏）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from redisdesk.app.ui.dialog_kit import StyledComboBox, configure_spinbox
from redisdesk.app.ui.i18n import KEY_TYPE_LABELS, format_ttl, key_type_label
from redisdesk.app.ui.value_views import ValueInspector
from redisdesk.app.ui.widgets import DataTable, PageFrame, SplitView
from redisdesk.core.model import ConnectionProfile, SessionContext


class BrowserPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.session: SessionContext | None = None
        self.profile: ConnectionProfile | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.page = PageFrame(
            "",
            "浏览只走 SCAN，不会调用 KEYS。大集合按页读取，敏感值默认遮罩，页面内不提供值编辑。",
        )
        self.pattern = QLineEdit()
        self.pattern.setPlaceholderText("MATCH 模式，例如 cache:*")
        self.pattern.setMinimumWidth(200)
        self.count = configure_spinbox(QSpinBox())
        self.count.setRange(10, 10000)
        self.count.setValue(1000)
        self.count.setPrefix("COUNT ")
        self.type_filter = StyledComboBox()
        self.type_filter.addItem("全部类型", "")
        for key, label in KEY_TYPE_LABELS.items():
            if key in {"string", "hash", "list", "set", "zset", "stream"}:
                self.type_filter.addItem(label, key)
        self.start = QPushButton("开始扫描")
        self.start.setObjectName("primary")
        self.pause = QPushButton("暂停")
        self.cancel = QPushButton("取消")
        self.status = QLabel("尚未连接")
        self.status.setObjectName("hint")
        self.page.add_action(self.start)
        self.page.add_action(self.pause)
        self.page.add_action(self.cancel)
        self.page.add_tool(self.pattern, stretch=1)
        self.page.add_tool(self.count)
        self.page.add_tool(self.type_filter)
        self.page.add_toolbar_stretch()
        self.page.add_tool(self.status)

        split = SplitView(Qt.Orientation.Horizontal)
        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(8)
        self.keys_header = QLabel("扫描结果")
        self.keys_header.setObjectName("sectionTitle")
        left_l.addWidget(self.keys_header)
        self.keys = DataTable(["键", "类型", "TTL", "节点"])
        self.keys.itemSelectionChanged.connect(self._on_key_selected)
        left_l.addWidget(self.keys, 1)
        self.inspector = ValueInspector()
        split.addWidget(left)
        split.addWidget(self.inspector)
        split.setSizes([560, 560])
        self.page.add_body(split)
        root.addWidget(self.page)

        self.start.clicked.connect(self._start_scan)
        self.pause.setEnabled(False)
        self.cancel.setEnabled(False)
        self.set_context(None, None)

    def set_context(self, session: SessionContext | None, profile: ConnectionProfile | None) -> None:
        self.session = session
        self.profile = profile
        connected = profile is not None
        self.start.setEnabled(connected)
        self.inspector.set_disconnected(not connected)
        if connected:
            extra = "明文" if not profile.tls.enabled else "TLS"
            self.status.setText(f"当前连接：{profile.name}（{extra}）。扫描尚未开始。")
        else:
            self.status.setText("尚未连接。请先在顶部选择一条已授权连接。")
            self.keys.set_rows([])
            self.keys_header.setText("扫描结果")

    def _start_scan(self) -> None:
        if self.profile is None:
            self.status.setText("请先在顶部选择已授权的连接，再开始扫描。")
            return
        self.status.setText("扫描需要已建立的 Redis 连接。当前尚未接入活动连接，列表保持为空。")

    def _on_key_selected(self) -> None:
        row = self.keys.currentRow()
        if row < 0 or self.keys.item(row, 0) is None:
            return
        # 活动连接接入后，将在此加载对应类型的值页。
        key = self.keys.item(row, 0).text()
        key_type = self.keys.item(row, 1).data(Qt.ItemDataRole.UserRole) if self.keys.item(row, 1) else None
        self.inspector.meta.bind(key, str(key_type or "string"))
