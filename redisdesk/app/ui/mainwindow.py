"""无边框主窗口：自定义标题栏 + 横向导航 + 内凹内容画布。启动后直接进入，无登录。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from redisdesk.app.services.audit_service import AuditService
from redisdesk.app.services.auth_service import AuthService
from redisdesk.app.services.connection_service import ConnectionService
from redisdesk.app.services.console_service import ConsoleService
from redisdesk.app.ui.chrome import FramelessMixin, HorizontalNav, TitleBar, WindowRoot, work_area_for
from redisdesk.app.ui.dialog_kit import StyledComboBox
from redisdesk.app.ui.icons import NAV_ITEMS
from redisdesk.app.ui.i18n import env_label
from redisdesk.app.ui.pages.audit import AuditPage
from redisdesk.app.ui.pages.connections import ConnectionsPage
from redisdesk.app.ui.pages.diagnostics import DiagnosticsPage
from redisdesk.app.ui.pages.settings import SettingsPage
from redisdesk.app.ui.pages.users import UsersPage
from redisdesk.app.ui.theme import UiSettings, apply_theme
from redisdesk.app.ui.widgets import Badge, StatusDot
from redisdesk.core.model import SessionContext
from redisdesk.infra.session import SessionManager

PAGE_TITLES = {
    "connections": "连接",
    "browser": "浏览",
    "console": "控制台",
    "diagnostics": "诊断",
    "audit": "审计",
    "users": "本机",
    "settings": "设置",
}


class MainWindow(FramelessMixin, QMainWindow):
    logout_requested = Signal()

    def __init__(
        self,
        *,
        auth: AuthService,
        connections: ConnectionService,
        console: ConsoleService,
        audits: AuditService,
        sessions: SessionManager,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.auth = auth
        self.connections = connections
        self.console = console
        self.audits = audits
        self.sessions = sessions
        self.ui_settings = UiSettings()
        self.session: SessionContext | None = None
        self._audit_ok = True
        self.setWindowTitle("RedisDesk")
        self.setMinimumSize(800, 560)
        self._launch_placed = False
        self.init_frameless()
        self._apply_theme()
        self._build()
        self.arm_resize_filters()
        self._goto(0)
        self.place_normal_on_work_area()

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        if self._launch_placed:
            return
        self._launch_placed = True
        self.place_normal_on_work_area()
        QTimer.singleShot(0, self._enforce_normal_launch)

    def _enforce_normal_launch(self) -> None:
        """防止系统在 show 之后把无边框窗口拉成铺满（含任务栏）的状态。"""
        if self.is_work_area_maximized():
            return
        area = work_area_for(self)
        overflow = (
            self.isMaximized()
            or self.isFullScreen()
            or self.geometry().left() < area.left()
            or self.geometry().top() < area.top()
            or self.geometry().right() > area.right()
            or self.geometry().bottom() > area.bottom()
        )
        if overflow:
            self.place_normal_on_work_area()

    def changeEvent(self, event) -> None:  # noqa: ANN001
        super().changeEvent(event)
        if event.type() in {QEvent.Type.ActivationChange, QEvent.Type.WindowStateChange, QEvent.Type.StyleChange}:
            self._sync_frame_active()

    def _sync_frame_active(self) -> None:
        header = getattr(self, "chrome_header", None)
        if header is None:
            return
        active = bool(self.isActiveWindow())
        header.setProperty("chromeActive", "true" if active else "false")
        style = header.style()
        if style is not None:
            style.unpolish(header)
            style.polish(header)
        header.update()

    def _apply_theme(self, mode: str | None = None) -> None:
        if mode:
            self.ui_settings.set("theme", mode)
        else:
            self.ui_settings.load()
            mode = self.ui_settings.get("theme", "light")
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, mode)
        self._sync_frame_active()

    def _build(self) -> None:
        root = WindowRoot()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.chrome_header = QFrame()
        self.chrome_header.setObjectName("chromeHeader")
        header_l = QVBoxLayout(self.chrome_header)
        header_l.setContentsMargins(0, 0, 0, 0)
        header_l.setSpacing(0)

        self.title_bar = TitleBar()
        self.page_title = self.title_bar.page_title
        self.conn_combo = StyledComboBox()
        self.conn_combo.setMinimumWidth(220)
        self.conn_combo.setMaximumWidth(280)
        self.conn_combo.setToolTip("选择一条已保存的连接，供诊断使用。工作区标签绑定自己的连接，不会随此项误发命令。")
        self.conn_combo.currentIndexChanged.connect(self._on_conn_changed)
        self.env_badge = Badge("未连接", "muted")
        self.tls_badge = Badge("—", "muted")
        self.title_bar.add_trailing(self.conn_combo)
        self.title_bar.add_trailing(self.env_badge)
        self.title_bar.add_trailing(self.tls_badge)
        self.title_bar.win_min.clicked.connect(self.showMinimized)
        self.title_bar.win_max.clicked.connect(self.toggle_work_area_maximize)
        self.title_bar.win_close.clicked.connect(self.close)
        self.title_bar.move_requested.connect(self.begin_chrome_move)
        self.title_bar.maximize_requested.connect(self.toggle_work_area_maximize)
        header_l.addWidget(self.title_bar)

        self.nav_bar = HorizontalNav()
        self.nav_buttons = self.nav_bar.buttons
        self.nav_keys: list[str] = []
        self.stack = QStackedWidget()
        self.stack.setObjectName("pageStack")
        self.connections_page = ConnectionsPage(self.connections, console=self.console)
        self.diag_page = DiagnosticsPage(self.connections)
        self.audit_page = AuditPage(self.audits)
        self.users_page = UsersPage(self.connections.store)
        self.settings_page = SettingsPage()
        self.settings_page.theme_changed.connect(self._on_theme_changed)
        self.connections_page.profiles_changed.connect(self._reload_connections)
        self.connections_page.workspace_status_changed.connect(self._sync_session_status)
        pages = [
            self.connections_page,
            self.diag_page,
            self.audit_page,
            self.users_page,
            self.settings_page,
        ]
        for i, ((key, label, _icon), page) in enumerate(zip([item for item in NAV_ITEMS if item[0] not in {"browser", "console"}], pages)):
            btn = self.nav_bar.add_item(key, label)
            btn.clicked.connect(lambda _=False, idx=i: self._goto(idx))
            self.nav_keys.append(key)
            self.stack.addWidget(page)
            QShortcut(QKeySequence(f"Ctrl+{i+1}"), self, activated=lambda idx=i: self._goto(idx))
        self.nav_bar.finish()
        header_l.addWidget(self.nav_bar)
        outer.addWidget(self.chrome_header)
        self._sync_frame_active()

        self.banner = QLabel("")
        self.banner.setObjectName("banner")
        self.banner.setVisible(False)
        self.banner.setWordWrap(True)
        outer.addWidget(self.banner)

        stage = QWidget()
        stage.setObjectName("contentStage")
        stage_l = QVBoxLayout(stage)
        stage_l.setContentsMargins(16, 8, 16, 16)
        stage_l.setSpacing(0)

        self.canvas = QFrame()
        self.canvas.setObjectName("contentCanvas")
        canvas_l = QVBoxLayout(self.canvas)
        canvas_l.setContentsMargins(0, 0, 0, 0)
        canvas_l.setSpacing(0)
        canvas_l.addWidget(self.stack, 1)

        status = QFrame()
        status.setObjectName("canvasFooter")
        st = QHBoxLayout(status)
        st.setContentsMargins(20, 8, 20, 8)
        st.setSpacing(16)
        self.status_dot = StatusDot("已就绪", "muted")
        self.audit_status = QLabel("审计链待校验")
        self.audit_status.setObjectName("muted")
        st.addWidget(self.status_dot)
        st.addStretch()
        st.addWidget(self.audit_status)
        canvas_l.addWidget(status)
        stage_l.addWidget(self.canvas, 1)
        outer.addWidget(stage, 1)

        root.setMouseTracking(True)
        stage.setMouseTracking(True)
        self._sync_frame_active()

    def closeEvent(self, event):
        # Do not let main() close the shared store while background audit/connection work is running.
        resources = getattr(self, '_closing_resources', None)
        if resources is None:
            resources = [workspace.io for workspace in self.connections_page.workspaces.values()]
            self._closing_resources = resources
            self.connections_page.close_workspaces()
        pending_cleanup = any(not io.cleanup_future.done() for io in resources)
        pending_test = any(job.isRunning() for job in self.connections_page._test_jobs.values())
        if pending_cleanup or pending_test:
            event.ignore()
            self.setEnabled(False)
            self.status_dot.set_status('muted', '正在释放连接…')
            QTimer.singleShot(50, self.close)
            return
        super().closeEvent(event)

    def bind_session(self, session: SessionContext) -> None:
        self.session = session
        self.sessions.current = session
        self.connections_page.set_session(session)
        self.audit_page.set_session(session)
        self.users_page.set_session(session)
        self.diag_page.set_context(session, None)
        self.status_dot.set_status("ok", "已就绪")
        self._reload_connections()

    def show_audit_banner(self, message: str) -> None:
        self._audit_ok = False
        self.banner.setText(message)
        self.banner.setVisible(True)
        self.audit_status.setText("审计链异常 · 只读应急")

    def set_audit_ok(self, ok: bool, *, last_seq: int | None = None) -> None:
        self._audit_ok = ok
        if ok:
            if last_seq is not None:
                self.audit_status.setText(f"审计链完整 · {last_seq} 条")
            else:
                self.audit_status.setText("审计链完整")
        else:
            self.audit_status.setText("审计链异常 · 只读应急")

    def _on_theme_changed(self, mode: str) -> None:
        self._apply_theme(mode)
        self._on_conn_changed()
        if self.session:
            self.status_dot.set_status("ok", "已就绪")
        self._sync_max_button()

    def _goto(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == index)
        if 0 <= index < len(self.nav_keys):
            self.page_title.setText(PAGE_TITLES.get(self.nav_keys[index], ""))

    def _reload_connections(self) -> None:
        current = self.conn_combo.currentData()
        self.conn_combo.blockSignals(True)
        self.conn_combo.clear()
        self.conn_combo.addItem("（未选择）", None)
        if self.session:
            try:
                profiles = self.connections.list_profiles(self.session)
            except Exception:
                profiles = []
            for p in profiles:
                label = f"{p.name} · {env_label(p.env)}"
                if not p.tls.enabled:
                    label += " · 明文"
                self.conn_combo.addItem(label, p.id)
        index = 0
        if current:
            found = self.conn_combo.findData(current)
            if found >= 0:
                index = found
        self.conn_combo.setCurrentIndex(index)
        self.conn_combo.blockSignals(False)
        self._on_conn_changed()

    def _on_conn_changed(self) -> None:
        self._sync_session_status()

    def _sync_session_status(self) -> None:
        workspace = self.connections_page.current_workspace()
        if workspace is not None and getattr(workspace, "state", None) == "connected":
            profile = workspace.profile
            self.env_badge.apply("prod" if profile.env == "prod" else "info", env_label(profile.env))
            if profile.tls.enabled:
                self.tls_badge.apply("tls", "TLS")
            else:
                self.tls_badge.apply("plaintext", "明文")
            self.status_dot.set_status("ok", f"已连接 · {profile.name}")
            self.diag_page.set_context(self.session, profile)
            return
        if workspace is not None and getattr(workspace, "state", None) == "connecting":
            profile = workspace.profile
            self.env_badge.apply("prod" if profile.env == "prod" else "info", env_label(profile.env))
            self.status_dot.set_status("muted", "正在连接…")
            self.diag_page.set_context(self.session, profile)
            return
        if self.session is None:
            self.env_badge.apply("muted", "未选择")
            self.tls_badge.apply("muted", "—")
            return
        cid = self.conn_combo.currentData()
        if not cid:
            self.env_badge.apply("muted", "未选择")
            self.tls_badge.apply("muted", "—")
            self.diag_page.set_context(self.session, None)
            if self.session:
                self.status_dot.set_status("ok", "已就绪")
            return
        profile = self.connections.store.get_connection(cid)
        if profile is None:
            return
        self.env_badge.apply("prod" if profile.env == "prod" else "info", env_label(profile.env))
        if profile.tls.enabled:
            self.tls_badge.apply("tls", "TLS")
        else:
            self.tls_badge.apply("plaintext", "明文")
        self.diag_page.set_context(self.session, profile)
