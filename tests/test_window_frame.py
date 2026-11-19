from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QWidget

from redisdesk.app.services.audit_service import AuditService
from redisdesk.app.services.auth_service import AuthService
from redisdesk.app.services.connection_service import ConnectionService
from redisdesk.app.services.console_service import ConsoleService
from redisdesk.app.ui.chrome import work_area_for
from redisdesk.app.ui.mainwindow import MainWindow
from redisdesk.app.ui.theme import apply_theme, current_palette, make_qss
from redisdesk.infra.audit import AuditLog
from redisdesk.infra.session import SessionManager
from redisdesk.infra.store import StoreGrantResolver


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
    # Window init may load a user-saved theme; this test asserts the light chrome tokens.
    apply_theme(QApplication.instance(), "light")
    window.showNormal()
    QApplication.processEvents()
    return window


def _qss_block(qss: str, selector: str, fallback_end: str | None = None) -> str:
    start = qss.find(selector)
    if start < 0:
        return ""
    nxt = len(qss)
    for marker in (fallback_end, "QFrame#titleBar", "QFrame#contentStage", "QPushButton#topNav"):
        if not marker:
            continue
        pos = qss.find(marker, start + len(selector))
        if pos >= 0:
            nxt = min(nxt, pos)
    return qss[start:nxt]


def test_main_window_has_no_outer_frame_and_colored_nav(qapp, tmp_store):
    window = _make_window(tmp_store)
    root = window.findChild(QWidget, "windowRoot")
    assert root is not None
    assert root is window.centralWidget()
    margins = root.layout().contentsMargins()
    assert margins.left() == 0
    assert margins.top() == 0
    assert margins.right() == 0
    assert margins.bottom() == 0
    assert not hasattr(window, "_shadow_margin") or int(getattr(window, "_shadow_margin") or 0) == 0

    header = window.findChild(QWidget, "chromeHeader")
    assert header is not None
    assert header.isAncestorOf(window.title_bar)
    assert header.isAncestorOf(window.nav_bar)
    pos = header.mapTo(window, QPoint(0, 0))
    assert pos.x() == 0
    assert pos.y() == 0
    assert header.width() == window.width()

    tokens = current_palette()
    accent = tokens["accent"].lower()
    assert tokens["chrome_bg"].lower() != tokens["bg"].lower()
    assert tokens["chrome_bg"].lower() == "#eef2ff"
    assert tokens["chrome_bg_inactive"].lower() == "#f1f5f9"
    assert tokens["bg"].lower() == "#f7f8fa"

    qss = QApplication.instance().styleSheet()
    root_qss = _qss_block(qss, "QWidget#windowRoot", "QFrame#chromeHeader")
    assert "QWidget#windowRoot" in qss
    assert accent not in root_qss.lower()
    assert "border: 2px solid" not in root_qss.lower()
    assert f"background: {tokens['accent']}" not in root_qss
    assert "QFrame#chromeHeader" in qss
    assert tokens["chrome_bg"] in qss
    assert tokens["chrome_bg_inactive"] in qss

    window._sync_frame_active()
    QApplication.processEvents()
    assert header.property("chromeActive") in {"true", "false", True, False}

    area = work_area_for(window)
    geom = window.geometry()
    assert geom.left() >= area.left()
    assert geom.top() >= area.top()
    assert geom.right() <= area.right()
    assert geom.bottom() <= area.bottom()

    window.maximize_to_work_area()
    QApplication.processEvents()
    margins = root.layout().contentsMargins()
    assert margins.left() == 0
    assert margins.bottom() == 0
    header = window.findChild(QWidget, "chromeHeader")
    assert header.mapTo(window, QPoint(0, 0)).x() == 0
    assert header.width() == window.width()
    geom = window.geometry()
    assert geom.left() >= area.left()
    assert geom.top() >= area.top()
    assert geom.right() <= area.right()
    assert geom.bottom() <= area.bottom()
    window.close()


def test_compiled_qss_colors_nav_not_window_ring():
    tokens = current_palette()
    qss = make_qss(tokens)
    root_qss = _qss_block(qss, "QWidget#windowRoot", "QFrame#chromeHeader")
    assert tokens["accent"].lower() not in root_qss.lower()
    assert "QFrame#chromeHeader" in qss
    assert tokens["chrome_bg"] in qss
    assert tokens["chrome_bg"] != tokens["bg"]
    dialog = qss[qss.find("QDialog#scrollableDialog") : qss.find("QDialog#scrollableDialog") + 280]
    assert "border: 1px solid" in dialog
    assert tokens["border"] in dialog
    assert tokens["accent"] not in dialog
