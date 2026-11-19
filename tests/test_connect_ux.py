from __future__ import annotations

import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QFrame, QPushButton, QScrollArea

from redisdesk.adapters.direct import classify_connect_error
from redisdesk.app.ui.dialog_kit import available_work_area
from redisdesk.app.ui.dialogs import ConnectionEditorDialog
from redisdesk.app.ui.jobs import FunctionWorker
from redisdesk.app.ui.theme import apply_theme
from redisdesk.core.errors import AppError, ErrorCode


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication(["redisdesk-tests"])
    apply_theme(app, "light")
    return app


def test_classify_timeout_mentions_tls_when_enabled():
    err = classify_connect_error(
        TimeoutError("Timeout connecting to server"),
        host="127.0.0.1",
        port=6379,
        tls_enabled=True,
    )
    assert isinstance(err, AppError)
    assert err.code == ErrorCode.E03
    assert "超时" in err.message
    assert "TLS" in err.message
    assert "明文" in err.message


def test_classify_timeout_without_tls_is_plain():
    err = classify_connect_error(
        TimeoutError("timed out"),
        host="10.0.0.9",
        port=6379,
        tls_enabled=False,
    )
    assert err.code == ErrorCode.E03
    assert "超时" in err.message
    assert "TLS" not in err.message


def test_classify_refused_and_auth():
    refused = classify_connect_error(ConnectionRefusedError("connection refused"), host="127.0.0.1", port=6380)
    assert refused.code == ErrorCode.E01
    assert "拒绝" in refused.message
    auth = classify_connect_error(Exception("WRONGPASS invalid username-password pair"))
    assert auth.code == ErrorCode.E02
    assert "口令" in auth.message or "用户名" in auth.message


def test_connection_dialog_has_test_action_in_footer(qapp):
    dlg = ConnectionEditorDialog()
    dlg.show()
    QApplication.processEvents()
    dlg.fit_to_work_area()
    QApplication.processEvents()
    assert dlg.test_button is not None
    assert dlg.test_button.text() == "测试连接"
    footer = dlg.findChild(QFrame, "dialogFooter")
    scroll = dlg.findChild(QScrollArea, "dialogScroll")
    assert footer is not None
    assert footer.isAncestorOf(dlg.test_button)
    assert not scroll.isAncestorOf(dlg.test_button)
    assert not scroll.isAncestorOf(dlg.ok_button)
    area = available_work_area(dlg)
    assert dlg.height() <= max(320, int(area.height() * 0.8))
    dlg.close()


def test_function_worker_does_not_run_on_gui_thread(qapp):
    main_ident = threading.get_ident()
    seen: dict[str, int] = {}

    def work() -> str:
        seen["ident"] = threading.get_ident()
        seen["is_gui"] = int(QThread.currentThread() is QApplication.instance().thread())
        time.sleep(0.05)
        return "ok"

    worker = FunctionWorker(work)
    results: list[str] = []
    worker.signals.finished.connect(lambda value: results.append(value))
    worker.start()
    assert worker.wait(3000)
    QApplication.processEvents()
    assert results == ["ok"]
    assert seen["ident"] != main_ident
    assert seen["is_gui"] == 0
    worker.deleteLater()
