"""审计页：可筛选表格 + 详情面板。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget

from redisdesk.app.services.audit_service import AuditService
from redisdesk.app.ui.dialog_kit import StyledComboBox
from redisdesk.app.ui.i18n import (
    decision_label,
    event_label,
    format_ts,
    result_label,
    role_label,
)
from redisdesk.app.ui.widgets import Card, DataTable, KeyValueTable, PageFrame, SplitView
from redisdesk.core.errors import AppError
from redisdesk.core.model import SessionContext


class AuditPage(QWidget):
    def __init__(self, service: AuditService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.session: SessionContext | None = None
        self._rows: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.page = PageFrame(
            "",
            "审计只追加。哈希链异常时 fail-closed：拒绝写命令，并进入只读应急。",
        )
        self.actor_filter = QLineEdit()
        self.actor_filter.setPlaceholderText("按操作者筛选")
        self.actor_filter.setClearButtonEnabled(True)
        self.event_filter = StyledComboBox()
        self.event_filter.addItem("全部事件", "")
        self.result_filter = StyledComboBox()
        self.result_filter.addItem("全部结果", "")
        self.result_filter.addItem("成功", "ok")
        self.result_filter.addItem("失败", "err")
        self.result_filter.addItem("已阻断", "blocked")
        self.result_filter.addItem("处理中", "pending")
        self.event_filter.fit_to_contents()
        self.result_filter.fit_to_contents()
        self.refresh = QPushButton("刷新")
        self.verify = QPushButton("校验完整性")
        self.refresh.clicked.connect(self.reload)
        self.verify.clicked.connect(self._verify)
        self.actor_filter.textChanged.connect(self._apply_filter)
        self.event_filter.currentIndexChanged.connect(self._apply_filter)
        self.result_filter.currentIndexChanged.connect(self._apply_filter)
        self.page.add_action(self.refresh)
        self.page.add_action(self.verify)
        self.page.add_tool(self.actor_filter)
        self.page.add_tool(self.event_filter)
        self.page.add_tool(self.result_filter)
        self.page.add_toolbar_stretch()

        self.status = QLabel()
        self.status.setObjectName("hint")
        self.page.add_body(self.status, 0)

        split = SplitView(Qt.Orientation.Horizontal)
        self.table = DataTable(["时间", "账号", "事件", "命令", "决策", "结果"])
        self.table.itemSelectionChanged.connect(self._on_select)
        self.detail = Card("记录详情")
        self.kv = KeyValueTable()
        self.detail.add_body(self.kv)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setSizes([720, 360])
        self.page.add_body(split, 1)
        root.addWidget(self.page)

    def set_session(self, session: SessionContext) -> None:
        self.session = session
        admin = session.role == "admin"
        self.refresh.setEnabled(admin)
        self.verify.setEnabled(admin)
        self.actor_filter.setEnabled(admin)
        self.event_filter.setEnabled(admin)
        self.result_filter.setEnabled(admin)
        if admin:
            self.reload()
        else:
            self.status.setText("当前无法查看审计详情。启动时的完整性校验结果仍会显示在底部状态栏。")
            self.table.set_rows([])
            self.kv.set_pairs([])

    def reload(self) -> None:
        if self.session is None:
            return
        try:
            rows = list(self.service.query(self.session, limit=200))
        except AppError as exc:
            self.status.setText(exc.message)
            return
        self._rows = rows
        events = sorted({str(r.get("event") or "") for r in rows if r.get("event")})
        current = self.event_filter.currentData()
        self.event_filter.blockSignals(True)
        self.event_filter.clear()
        self.event_filter.addItem("全部事件", "")
        for ev in events:
            self.event_filter.addItem(event_label(ev), ev)
        index = self.event_filter.findData(current)
        self.event_filter.setCurrentIndex(index if index >= 0 else 0)
        self.event_filter.fit_to_contents()
        self.result_filter.fit_to_contents()
        self.event_filter.blockSignals(False)
        self._apply_filter()
        self.status.setText(f"共 {len(rows)} 条（最新在前）")

    def _apply_filter(self) -> None:
        actor = self.actor_filter.text().strip().lower()
        event = self.event_filter.currentData() or ""
        result = self.result_filter.currentData() or ""
        visible: list[dict] = []
        for row in self._rows:
            if actor and actor not in str(row.get("actor_username") or "").lower():
                continue
            if event and str(row.get("event") or "") != event:
                continue
            if result and str(row.get("result") or "") != result:
                continue
            visible.append(row)
        self.table.set_rows(
            [
                (
                    format_ts(r.get("ts")),
                    r.get("actor_username") or "",
                    event_label(r.get("event")),
                    r.get("command_name") or "",
                    decision_label(r.get("decision")),
                    result_label(r.get("result")),
                )
                for r in visible
            ]
        )
        self.table.setProperty("filtered_rows", visible)
        self._visible = visible
        if visible:
            self.table.selectRow(0)
            self._show_detail(visible[0])
        else:
            self.kv.set_pairs([])

    def _on_select(self) -> None:
        row = self.table.currentRow()
        visible = getattr(self, "_visible", [])
        if 0 <= row < len(visible):
            self._show_detail(visible[row])

    def _show_detail(self, row: dict) -> None:
        self.kv.set_pairs(
            [
                ("序号", row.get("seq") if row.get("seq") is not None else "—"),
                ("时间", format_ts(row.get("ts"))),
                ("账号", row.get("actor_username") or "—"),
                ("角色", role_label(row.get("actor_role"))),
                ("事件", event_label(row.get("event"))),
                ("命令", row.get("command_name") or "—"),
                ("参数（已脱敏）", row.get("argv_masked") or "—"),
                ("决策", decision_label(row.get("decision"))),
                ("结果", result_label(row.get("result"))),
                ("拒绝原因", row.get("deny_reason") or "—"),
                ("连接", row.get("connection_name") or "—"),
                ("环境", row.get("env") or "—"),
                ("风险等级", row.get("risk_level") or "—"),
                ("耗时（毫秒）", row.get("duration_ms") if row.get("duration_ms") is not None else "—"),
                ("错误码", row.get("error_code") or "—"),
            ]
        )

    def _verify(self) -> None:
        if self.session is None:
            return
        try:
            status = self.service.verify_full(self.session)
        except AppError as exc:
            QMessageBox.warning(self, "校验失败", exc.message)
            return
        if status.ok:
            QMessageBox.information(self, "完整性校验", f"审计链完整，共校验 {status.checked} 条。")
        else:
            QMessageBox.critical(self, "审计链异常", status.message)
