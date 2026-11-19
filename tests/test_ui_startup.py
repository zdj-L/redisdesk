from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from redisdesk.app.services.audit_service import AuditService
from redisdesk.app.services.auth_service import AuthService
from redisdesk.app.services.connection_service import ConnectionService
from redisdesk.app.services.console_service import ConsoleService
from redisdesk.app.ui.i18n import env_label, role_label
from redisdesk.app.ui.mainwindow import MainWindow
from redisdesk.app.ui.theme import apply_theme
from redisdesk.infra.audit import AuditLog
from redisdesk.infra.session import SessionManager
from redisdesk.infra.store import StoreGrantResolver


def test_i18n_labels():
    assert env_label("prod") == "生产"
    assert env_label("dev") == "开发"
    assert role_label("admin") == "管理员"
    assert role_label(None) == "—"


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication(["redisdesk-tests"])
    apply_theme(app, "light")
    return app


def _make_window(tmp_store) -> MainWindow:
    audit = AuditLog(tmp_store)
    sessions = SessionManager()
    grants = StoreGrantResolver(tmp_store)
    auth = AuthService(tmp_store, audit, sessions)
    connections = ConnectionService(tmp_store, audit, grants)
    console = ConsoleService(tmp_store, audit, grants)
    audits = AuditService(audit, grants)
    window = MainWindow(
        auth=auth,
        connections=connections,
        console=console,
        audits=audits,
        sessions=sessions,
    )
    session = auth.ensure_local_session()
    window.bind_session(session)
    window.place_normal_on_work_area()
    window.showNormal()
    QApplication.processEvents()
    return window


def test_main_window_opens_without_login(qapp, tmp_store):
    window = _make_window(tmp_store)
    names = {w.objectName() for w in window.findChildren(QWidget)}
    assert "loginCard" not in names
    texts = [b.text() for b in window.nav_buttons]
    assert texts == ["连接", "诊断", "审计", "本机", "设置"]
    assert window.stack.count() == 5
    assert window.isVisible()
    assert window.page_title.text() == "连接"
    assert window.connections_page.empty.isVisible()
    empty = window.connections_page.empty
    assert empty.title_label.text() == "还没有连接"
    assert "添加一台 Redis 服务器后，即可开始浏览和排查。" in empty.hint_label.text()
    assert not empty.button.isVisible()
    assert window.connections_page.workspace_tabs.count() == 0
    assert not window.connections_page.details_btn.isChecked()
    window.close()


def test_window_chrome_is_frameless_with_horizontal_nav(qapp, tmp_store):
    window = _make_window(tmp_store)
    assert bool(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
    assert window.findChild(QWidget, "titleBar") is not None
    assert window.findChild(QPushButton, "winMin") is not None
    assert window.findChild(QPushButton, "winMax") is not None
    assert window.findChild(QPushButton, "winClose") is not None
    assert window.findChild(QPushButton, "winClose").isEnabled()
    # 启动路径里不能再留下侧栏。
    assert window.findChild(QWidget, "sidebar") is None

    ys = [btn.mapTo(window, QPoint(0, 0)).y() for btn in window.nav_buttons]
    xs = [btn.mapTo(window, QPoint(0, 0)).x() for btn in window.nav_buttons]
    assert max(ys) - min(ys) <= 8
    assert xs == sorted(xs)
    assert window.nav_bar.height() <= 56

    canvas = window.findChild(QWidget, "contentCanvas")
    assert canvas is not None
    top_left = canvas.mapTo(window, QPoint(0, 0))
    assert top_left.x() >= 16
    assert window.width() - (top_left.x() + canvas.width()) >= 16
    assert window.height() - (top_left.y() + canvas.height()) >= 16
    window.close()

def test_main_window_starts_normal_inside_work_area(qapp, tmp_store):
    from redisdesk.app.ui.chrome import LAUNCH_SIZE_RATIO, PREFERRED_WINDOW_SIZE, normal_launch_geometry, work_area_for

    window = _make_window(tmp_store)
    QApplication.processEvents()
    area = work_area_for(window)
    expected = normal_launch_geometry(area)
    assert not window.isMaximized()
    assert not window.isFullScreen()
    assert not window.is_work_area_maximized()
    geom = window.geometry()
    assert geom.width() <= area.width()
    assert geom.height() <= area.height()
    # 启动尺寸必须小于工作区（任务栏保持可见），且不超过 1280x800 / 75%。
    assert geom.width() <= min(PREFERRED_WINDOW_SIZE[0], int(area.width() * LAUNCH_SIZE_RATIO), area.width())
    assert geom.height() <= min(PREFERRED_WINDOW_SIZE[1], int(area.height() * LAUNCH_SIZE_RATIO), area.height())
    if area.width() > 200 and area.height() > 200:
        assert geom.width() < area.width() or geom.height() < area.height()
    assert geom.left() >= area.left()
    assert geom.top() >= area.top()
    assert geom.right() <= area.right()
    assert geom.bottom() <= area.bottom()
    assert abs(geom.x() - expected.x()) <= 2
    assert abs(geom.y() - expected.y()) <= 2
    assert abs(geom.width() - expected.width()) <= 2
    assert abs(geom.height() - expected.height()) <= 2
    window.close()

def test_empty_state_title_is_unobstructed_and_status_bar_has_no_identity(qapp, tmp_store):
    window = _make_window(tmp_store)
    QApplication.processEvents()
    empty = window.connections_page.empty
    title = empty.title_label
    assert title.isVisible()
    assert title.text() == "还没有连接"
    assert empty.findChild(QWidget, "emptyWell") is None
    title_rect = QRect(title.mapTo(empty, QPoint(0, 0)), title.size())
    assert title_rect.width() > 0 and title_rect.height() > 0
    for widget in empty.findChildren(QWidget):
        if widget is title or widget is empty or not widget.isVisible():
            continue
        if widget.isAncestorOf(title) or title.isAncestorOf(widget):
            continue
        other = QRect(widget.mapTo(empty, QPoint(0, 0)), widget.size())
        assert not title_rect.intersects(other), f"{widget.objectName() or widget.__class__.__name__} overlaps empty title"

    # 页头不再重复放大写导航名「连接」。
    assert not window.connections_page.page.title_label.isVisible()
    # 没有选中行时，删除必须禁用。
    assert not window.connections_page.del_btn.isEnabled()
    assert not window.connections_page.test_btn.isEnabled()
    assert not window.connections_page.edit_btn.isEnabled()
    assert window.connections_page.add_btn.isEnabled()

    footer = window.findChild(QWidget, "canvasFooter")
    assert footer is not None
    footer_text = " ".join(lab.text() for lab in footer.findChildren(QLabel))
    assert "管理员" not in footer_text
    assert "root" not in footer_text.lower()
    assert not hasattr(window, "role_status") or window.findChild(QLabel, "roleStatus") is None
    window.close()


def test_audit_status_wording_is_clear_chinese(qapp, tmp_store):
    window = _make_window(tmp_store)
    window.set_audit_ok(True, last_seq=7)
    QApplication.processEvents()
    assert window.audit_status.text() == "审计链完整 · 7 条"
    window.set_audit_ok(False)
    assert "只读应急" in window.audit_status.text()
    assert "未条" not in window.audit_status.text()
    window.close()
