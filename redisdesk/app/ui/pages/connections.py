"""连接管理页：列表进入点 + 连接绑定的工作区。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QTabWidget,
    QSplitter,
)

from redisdesk.app.services.connection_service import ConnectionService
from redisdesk.app.ui.dialogs import ConnectionEditorDialog
from redisdesk.app.ui.i18n import env_label, protection_label, topology_label
from redisdesk.app.ui.jobs import FunctionWorker
from redisdesk.app.ui.widgets import Badge, Card, DataTable, EmptyState, KeyValueTable, PageFrame, RowStatusCell
from redisdesk.core.errors import AppError
from redisdesk.core.model import ConnectionProfile, SessionContext


class RowActions(QWidget):
    def __init__(self, profile_id: str, on_browser, on_console, parent=None) -> None:
        super().__init__(parent)
        self.profile_id = profile_id
        self.setObjectName("rowActions")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 8, 0)
        row.setSpacing(4)
        self.browser_btn = QPushButton("目录")
        self.console_btn = QPushButton("控制台")
        for button in (self.browser_btn, self.console_btn):
            button.setObjectName("rowAction")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFlat(True)
            button.setStyleSheet("padding: 2px 8px 2px 0px; text-align: left; min-width: 0;")
        self.browser_btn.clicked.connect(lambda: on_browser(profile_id))
        self.console_btn.clicked.connect(lambda: on_console(profile_id))
        row.addWidget(self.browser_btn)
        row.addWidget(self.console_btn)
        row.addStretch()


class ConnectionsPage(QWidget):
    profiles_changed = Signal()
    workspace_status_changed = Signal()

    def __init__(self, service: ConnectionService, parent=None, *, console=None) -> None:
        super().__init__(parent)
        self.service = service
        self.console_service = console
        self.workspaces = {}
        self.session: SessionContext | None = None
        self.hide_unauthorized = False
        self.profiles: list[ConnectionProfile] = []
        self._status_cells: dict[str, RowStatusCell] = {}
        self._test_jobs: dict[str, FunctionWorker] = {}
        self._action_cells: dict[str, RowActions] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.page = PageFrame("", "")
        self.page.layout().setContentsMargins(16, 12, 16, 12)
        self.page.layout().setSpacing(8)
        self.page.body.setSpacing(8)
        self.add_btn = QPushButton("新建连接")
        self.add_btn.setObjectName("primary")
        self.add_btn.clicked.connect(self._add)
        self.test_btn = QPushButton("测试连接")
        self.test_btn.clicked.connect(self._test)
        self.edit_btn = QPushButton("编辑")
        self.edit_btn.clicked.connect(self._edit)
        self.del_btn = QPushButton("删除")
        self.del_btn.setObjectName("dangerOutline")
        self.del_btn.clicked.connect(self._delete)
        self.details_btn = QPushButton("详情")
        self.details_btn.setCheckable(True)
        self.details_btn.setObjectName("tool")
        self.page.add_action(self.add_btn)
        self.page.add_action(self.test_btn)
        self.page.add_action(self.edit_btn)
        self.page.add_action(self.del_btn)
        self.page.add_action(self.details_btn)

        self.split = QWidget()
        self.split.setObjectName("connectionManager")
        manager = QVBoxLayout(self.split)
        manager.setContentsMargins(0, 0, 0, 0)
        manager.setSpacing(8)
        self.table = DataTable(["名称", "环境", "地址", "TLS", "档位", "授权", "状态", "操作"])
        self.table.setObjectName("connectionTable")
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(56)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.installEventFilter(self)
        self.table.viewport().installEventFilter(self)
        self._distribute_column_widths()
        self.table.setMinimumWidth(0)
        self.table.setMinimumHeight(100)
        self.table.itemSelectionChanged.connect(self._on_select)
        manager.addWidget(self.table, 1)
        self.detail = Card("连接详情")
        self.detail_title = QLabel("请选择一条连接")
        self.detail_title.setObjectName("sectionTitle")
        self.detail.add_body(self.detail_title)
        self.badges = QWidget()
        badge_row = QHBoxLayout(self.badges)
        badge_row.setContentsMargins(0, 0, 0, 0)
        badge_row.setSpacing(8)
        self.env_badge = Badge("—", "muted")
        self.tls_badge = Badge("—", "muted")
        self.grant_badge = Badge("—", "muted")
        badge_row.addWidget(self.env_badge)
        badge_row.addWidget(self.tls_badge)
        badge_row.addWidget(self.grant_badge)
        badge_row.addStretch()
        self.detail.add_body(self.badges)
        self.kv = KeyValueTable()
        self.detail.add_body(self.kv)
        self.detail.hide()
        self.details_btn.toggled.connect(self.detail.setVisible)
        manager.addWidget(self.detail)
        self.table.cellDoubleClicked.connect(lambda *_: self.open_workspace("browser"))

        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setObjectName("connectionWorkspaces")
        self.workspace_tabs.setTabsClosable(True)
        self.workspace_tabs.tabCloseRequested.connect(self.close_workspace_tab)
        self.workspace_tabs.currentChanged.connect(lambda *_: self.workspace_status_changed.emit())
        self.workspace_tabs.hide()
        self.return_list_btn = QPushButton("连接列表")
        self.return_list_btn.setToolTip("返回连接管理，工作区会话保留；再次打开会复用原标签。")
        self.return_list_btn.setObjectName("tool")
        self.return_list_btn.clicked.connect(self.show_manager)
        self.workspace_tabs.setCornerWidget(self.return_list_btn)
        self.manager_header = self.page.findChild(QWidget, "pageHeader")
        self.vertical = QSplitter(Qt.Orientation.Vertical)
        self.vertical.setChildrenCollapsible(False)
        self.vertical.addWidget(self.split)
        self.vertical.addWidget(self.workspace_tabs)
        self.vertical.setStretchFactor(0, 0)
        self.vertical.setStretchFactor(1, 1)
        self.page.add_body(self.vertical)

        self.empty = EmptyState(
            "还没有连接",
            "添加一台 Redis 服务器后，即可开始浏览和排查。",
        )
        self.page.add_body(self.empty, 1)
        self.empty.hide()
        root.addWidget(self.page)
        # Compatibility: previous toolbar buttons are now row actions; keep attributes disabled/hidden.
        self.browser_btn = QPushButton("缓存目录")
        self.console_btn = QPushButton("控制台")
        self.browser_btn.hide()
        self.console_btn.hide()
        self._set_detail(None)

    def set_session(self, session: SessionContext) -> None:
        self.session = session
        self.add_btn.setEnabled(True)
        self.reload()

    def reload(self) -> None:
        if self.session is None:
            return
        try:
            self.profiles = self.service.list_profiles(self.session, hide_unauthorized=self.hide_unauthorized)
        except AppError as exc:
            QMessageBox.warning(self, "无法加载连接", exc.message)
            return
        rows = []
        for p in self.profiles:
            authorized = self.service.is_authorized(self.session, p)
            tls = "明文" if not p.tls.enabled else "已启用"
            grant = "已授权" if authorized else "未授权"
            rows.append((p.name, env_label(p.env), f"{p.host}:{p.port}", tls, protection_label(p.protection), grant, "—"))
        self.table.set_rows(rows)
        self._distribute_column_widths()
        self._status_cells = {}
        self._action_cells = {}
        for i, p in enumerate(self.profiles):
            for col in range(7):
                item = self.table.item(i, col)
                if item is not None:
                    item.setToolTip(item.text())
            item = self.table.item(i, 0)
            if item is not None:
                item.setData(Qt.ItemDataRole.UserRole, p.id)
            cell = RowStatusCell(self.table)
            if p.id in self._test_jobs and self._test_jobs[p.id].isRunning():
                cell.set_busy("测试中…")
            self.table.setCellWidget(i, 6, cell)
            self._status_cells[p.id] = cell
            actions = RowActions(p.id, lambda cid: self.open_workspace("browser", cid), lambda cid: self.open_workspace("console", cid), self.table)
            if self.console_service is None:
                actions.browser_btn.setEnabled(False)
                actions.console_btn.setEnabled(False)
            self.table.setCellWidget(i, 7, actions)
            self._action_cells[p.id] = actions
        has_rows = bool(rows)
        self.split.setVisible(has_rows)
        self.empty.setVisible(not has_rows)
        if has_rows:
            self.table.selectRow(0)
        else:
            self._set_detail(None)
        self._sync_actions()
        self.profiles_changed.emit()

    def _profile_by_id(self, connection_id: str | None) -> ConnectionProfile | None:
        if not connection_id:
            return None
        return next((p for p in self.profiles if p.id == connection_id), None)

    def _selected(self) -> ConnectionProfile | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.profiles):
            return None
        return self.profiles[row]

    def _on_select(self) -> None:
        self._set_detail(self._selected())

    def _sync_actions(self) -> None:
        selected = self._selected() is not None
        self.test_btn.setEnabled(selected)
        self.edit_btn.setEnabled(selected)
        self.del_btn.setEnabled(selected)
        self.details_btn.setEnabled(selected)
        enabled = selected and self.console_service is not None
        self.browser_btn.setEnabled(enabled)
        self.console_btn.setEnabled(enabled)

    def _set_detail(self, profile: ConnectionProfile | None) -> None:
        self._sync_actions()
        if profile is None:
            self.detail_title.setText("请选择一条连接")
            self.env_badge.apply("muted", "—")
            self.tls_badge.apply("muted", "—")
            self.grant_badge.apply("muted", "—")
            self.kv.set_pairs([])
            return
        authorized = bool(self.session and self.service.is_authorized(self.session, profile))
        self.detail_title.setText(profile.name)
        self.env_badge.apply("prod" if profile.env == "prod" else "info", env_label(profile.env))
        if profile.tls.enabled:
            self.tls_badge.apply("tls", "TLS")
        else:
            self.tls_badge.apply("plaintext", "明文")
        self.grant_badge.apply("success" if authorized else "unauthorized", "已授权" if authorized else "未授权")
        self.kv.set_pairs(
            [
                ("主机", f"{profile.host}:{profile.port}"),
                ("数据库", str(profile.db)),
                ("拓扑", topology_label(profile.topology)),
                ("传输", profile.transport.upper()),
                ("用户名", profile.username or "—"),
                ("保护档位", protection_label(profile.protection)),
                ("CA 路径", profile.tls.ca_path or "—"),
                ("明文理由", profile.tls.plaintext_reason or "—"),
                ("备注", profile.note or "—"),
            ]
        )

    def _add(self) -> None:
        if self.session is None:
            return
        dlg = ConnectionEditorDialog(parent=self, session=self.session, service=self.service)
        if dlg.exec():
            try:
                self.service.save(self.session, dlg.profile(), password=dlg.password_value() or None, ssh_password=dlg.ssh_password_value() or None, ssh_passphrase=dlg.ssh_passphrase_value() or None)
                self.invalidate_workspace(dlg.profile().id)
            except AppError as exc:
                QMessageBox.warning(self, "保存失败", exc.message)
                return
            self.reload()

    def _edit(self) -> None:
        profile = self._selected()
        if profile is None or self.session is None:
            return
        dlg = ConnectionEditorDialog(profile, parent=self, session=self.session, service=self.service)
        if dlg.exec():
            try:
                self.service.save(self.session, dlg.profile(), password=dlg.password_value() or None, ssh_password=dlg.ssh_password_value() or None, ssh_passphrase=dlg.ssh_passphrase_value() or None)
                self.invalidate_workspace(dlg.profile().id)
            except AppError as exc:
                QMessageBox.warning(self, "保存失败", exc.message)
                return
            self.reload()

    def _delete(self) -> None:
        profile = self._selected()
        if profile is None or self.session is None:
            return
        if QMessageBox.question(self, "删除连接", f"确定删除「{profile.name}」？凭据将一并删除。") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.delete(self.session, profile.id)
            self.invalidate_workspace(profile.id)
        except AppError as exc:
            QMessageBox.warning(self, "删除失败", exc.message)
            return
        self.reload()

    def _is_testing(self, connection_id: str) -> bool:
        job = self._test_jobs.get(connection_id)
        return job is not None and job.isRunning()

    def _test(self) -> None:
        profile = self._selected()
        if profile is None or self.session is None:
            return
        if self._is_testing(profile.id):
            return
        cell = self._status_cells.get(profile.id)
        if cell is not None:
            cell.set_busy("测试中…")
        self.test_btn.setEnabled(False)
        self.test_btn.setText("测试中…")
        session = self.session

        def work():
            return self.service.test_connect(session, profile)

        worker = FunctionWorker(work, parent=self)
        self._test_jobs[profile.id] = worker
        worker.signals.finished.connect(lambda result, cid=profile.id, name=profile.name: self._on_test_ok(cid, name, result))
        worker.signals.failed.connect(lambda exc, cid=profile.id, name=profile.name: self._on_test_fail(cid, name, exc))
        worker.start()

    def _on_test_ok(self, connection_id: str, name: str, result) -> None:  # noqa: ANN001
        self._finish_test(connection_id)
        cell = self._status_cells.get(connection_id)
        extra = "（请确认默认用户 ACL）" if getattr(result, "first_default_warning", False) else ""
        if cell is not None:
            cell.set_ok(f"测试通过{extra}")
        QMessageBox.information(self, "连接测试成功", f"已成功访问「{name}」。测试不会保留会话；请点击操作列中的目录或控制台建立活动连接。{extra}")

    def _on_test_fail(self, connection_id: str, name: str, exc) -> None:  # noqa: ANN001
        self._finish_test(connection_id)
        message = exc.message if isinstance(exc, AppError) else str(exc)
        cell = self._status_cells.get(connection_id)
        if cell is not None:
            cell.set_fail("失败：" + (message or ""), tooltip=message)
        QMessageBox.warning(self, "连接测试失败", message)

    def _finish_test(self, connection_id: str) -> None:
        job = self._test_jobs.pop(connection_id, None)
        if job is not None and job.isRunning():
            job.wait(100)
        selected = self._selected()
        self.test_btn.setText("测试连接")
        self.test_btn.setEnabled(selected is not None)

    def open_workspace(self, kind="browser", profile=None):
        if isinstance(profile, str):
            profile = self._profile_by_id(profile)
        if profile is None:
            profile = self._selected()
        if profile is None or self.session is None or self.console_service is None:
            return
        from redisdesk.app.ui.connection_workspace import ConnectionWorkspace
        workspace = self.workspaces.get(profile.id)
        if workspace is None:
            workspace = ConnectionWorkspace(self.service, self.console_service, self.session, profile, self)
            workspace.state_changed.connect(lambda *_: self.workspace_status_changed.emit())
            self.workspaces[profile.id] = workspace
            index = self.workspace_tabs.addTab(workspace, f"{profile.name} · db{profile.db}")
            self.workspace_tabs.setTabToolTip(index, f"{profile.name} | {profile.host}:{profile.port} | DB {profile.db} | {env_label(profile.env)}")
        self.workspace_tabs.setCurrentWidget(workspace)
        if kind == "console":
            workspace.show_console()
        else:
            workspace.tabs.setCurrentWidget(workspace.detail)
        self.workspace_tabs.show()
        if self.height() < 650:
            self.split.hide()
            self.manager_header.hide()
        self.vertical.setSizes([120, 500])
        self.workspace_status_changed.emit()
        return workspace

    def current_workspace(self):
        if not self.workspace_tabs.isVisible():
            return None
        return self.workspace_tabs.currentWidget()

    def close_workspace_tab(self, index):
        workspace = self.workspace_tabs.widget(index)
        if workspace is None:
            return
        self.workspaces.pop(workspace.profile.id, None)
        workspace.close_workspace()
        self.workspace_tabs.removeTab(index)
        workspace.deleteLater()
        if self.workspace_tabs.count():
            self.workspace_tabs.show()
        else:
            self.show_manager()
        self.workspace_status_changed.emit()

    def invalidate_workspace(self, connection_id):
        workspace = self.workspaces.get(connection_id)
        if workspace is not None:
            self.close_workspace_tab(self.workspace_tabs.indexOf(workspace))

    def close_workspaces(self):
        for connection_id in list(self.workspaces):
            self.invalidate_workspace(connection_id)

    def show_manager(self):
        self.workspace_tabs.hide()
        self.split.setVisible(bool(self.profiles))
        self.manager_header.show()
        self.workspace_status_changed.emit()

    def eventFilter(self, obj, event):  # noqa: ANN001
        table = getattr(self, "table", None)
        if table is not None and event.type() == QEvent.Type.Resize and obj in {table, table.viewport()}:
            QTimer.singleShot(0, self._distribute_column_widths)
        return super().eventFilter(obj, event)

    def _distribute_column_widths(self) -> None:
        """Fill the table width without dumping leftover space into name/address only."""
        if getattr(self, "_distributing_columns", False):
            return
        table = getattr(self, "table", None)
        if table is None or table.columnCount() < 8:
            return
        self._distributing_columns = True
        try:
            self._distribute_column_widths_body(table)
        finally:
            self._distributing_columns = False

    def _distribute_column_widths_body(self, table) -> None:
        header = table.horizontalHeader()
        # Keep leftover off stretch columns; we assign every pixel ourselves.
        for column in range(table.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        viewport = table.viewport().width() if table.viewport().width() > 0 else max(table.width() - 2, 0)
        if viewport <= 0:
            return
        status_min = max(120, RowStatusCell.minimum_column_width(table, "测试通过"))
        actions_min = 132
        # Comfortable mins: short labels stay readable; status keeps icon+text unclipped.
        mins = [140, 88, 168, 76, 88, 88, status_min, actions_min]
        # Caps stop name/address from swallowing half the row when the window is wide.
        caps = [int(viewport * 0.32), 130, int(viewport * 0.36), 110, 130, 130, max(status_min + 48, 200), 168]
        if sum(mins) > viewport:
            widths = list(mins)
            overflow = sum(widths) - viewport
            # Shrink name/address first, then other flexible columns. Keep status and actions at mins.
            floors = [56, 56, 56, 56, 56, 56, status_min, actions_min]
            for column in (2, 0, 1, 4, 5, 3):
                if overflow <= 0:
                    break
                room = widths[column] - floors[column]
                if room <= 0:
                    continue
                take = min(room, overflow)
                widths[column] -= take
                overflow -= take
            if overflow > 0:
                # Last resort: shave a few pixels from actions, never from status.
                room = widths[7] - 120
                if room > 0:
                    take = min(room, overflow)
                    widths[7] -= take
                    overflow -= take
            for column, width in enumerate(widths):
                table.setColumnWidth(column, width)
            return
        leftover = viewport - sum(mins)
        widths = list(mins)
        # First give leftover to name/address, then a little to short columns, honoring caps.
        share = [0.36, 0.08, 0.40, 0.02, 0.03, 0.03, 0.06, 0.02]
        for column in range(8):
            widths[column] = min(caps[column], mins[column] + int(leftover * share[column]))
        used = sum(widths)
        rest = viewport - used
        # Any rounding remainder goes to the widest remaining cap room, usually address then name.
        for column in (2, 0, 7, 4, 5, 1, 3, 6):
            if rest == 0:
                break
            room = caps[column] - widths[column]
            if room <= 0:
                continue
            if rest > 0:
                add = min(room, rest)
                widths[column] += add
                rest -= add
            else:
                break
        if rest > 0:
            limit = viewport // 2
            for column in (1, 4, 5, 7, 3, 6, 0, 2):
                if rest <= 0:
                    break
                hard = limit if column in (0, 2) else viewport
                room = hard - widths[column]
                if room <= 0:
                    continue
                add = min(room, rest)
                widths[column] += add
                rest -= add
            if rest > 0:
                widths[7] += rest
        for column, width in enumerate(widths):
            table.setColumnWidth(column, width)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "table", None) is not None and self.table.isVisible():
            self._distribute_column_widths()
        if hasattr(self, "workspace_tabs") and self.workspace_tabs.isVisible() and self.height() < 650:
            self.split.hide()
            if self.manager_header is not None:
                self.manager_header.hide()
