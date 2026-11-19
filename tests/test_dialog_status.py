from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QFrame

from redisdesk.app.ui.dialogs import ConnectionEditorDialog
from redisdesk.app.ui.theme import apply_theme
from redisdesk.core.model import ConnectionProfile, Endpoint, TlsConfig


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication(["redisdesk-tests"])
    apply_theme(app, "light")
    return app


def _show_dialog(profile: ConnectionProfile | None = None) -> ConnectionEditorDialog:
    dlg = ConnectionEditorDialog(profile)
    dlg.show()
    QApplication.processEvents()
    dlg.fit_to_work_area()
    QApplication.processEvents()
    return dlg


def _reason_invalid(dlg: ConnectionEditorDialog) -> bool:
    return dlg.reason.property("invalid") in {True, "true"}


def _status_visible(dlg: ConnectionEditorDialog) -> bool:
    strip = dlg.findChild(QFrame, "dialogStatus")
    return bool(strip is not None and strip.isVisible() and dlg.status_label.text().strip())


def _assert_status_above_footer(dlg: ConnectionEditorDialog) -> None:
    QApplication.processEvents()
    dlg.fit_to_work_area()
    QApplication.processEvents()
    footer = dlg.findChild(QFrame, "dialogFooter")
    strip = dlg.findChild(QFrame, "dialogStatus")
    assert footer is not None
    assert strip is not None
    assert strip.isVisible()
    assert footer.isVisible()
    assert strip.geometry().bottom() <= footer.geometry().top()
    assert footer.geometry().top() >= 0
    assert footer.geometry().bottom() <= dlg.rect().bottom()
    assert footer.geometry().left() >= 0
    assert footer.geometry().right() <= dlg.rect().right()
    assert dlg.rect().contains(footer.geometry())
    for btn in (dlg.test_button, dlg.cancel_button, dlg.ok_button):
        assert btn.isVisible()
        assert footer.isAncestorOf(btn)
        btn_rect = QRect(btn.mapTo(dlg, QPoint(0, 0)), btn.size())
        assert dlg.rect().contains(btn_rect)
        strip_rect = QRect(strip.mapTo(dlg, QPoint(0, 0)), strip.size())
        assert not btn_rect.intersects(strip_rect)


def test_status_banner_does_not_overlap_footer(qapp):
    dlg = _show_dialog()
    dlg.set_status("danger", "请填写不少于 10 字的明文例外理由。")
    QApplication.processEvents()
    _assert_status_above_footer(dlg)
    dlg.set_status(
        "danger",
        "连接超时（127.0.0.1:6379）。当前已启用 TLS，若本机 Redis 是明文服务，TLS 握手会等到超时。请关闭「启用 TLS」并填写明文例外理由后再试。",
    )
    QApplication.processEvents()
    _assert_status_above_footer(dlg)
    text = dlg.status_label.text()
    assert "连接超时" in text
    assert "测试明文连接前" not in text
    assert not (
        "请填写不少于 10 字的明文例外理由" in text
        and "本机 Redis 若未启用 TLS" in text
    )
    dlg.close()


def test_status_shows_one_message_when_plaintext_reason_missing(qapp):
    dlg = _show_dialog()
    dlg.tls.setChecked(False)
    QApplication.processEvents()
    dlg.reason.clear()
    dlg.test_button.click()
    QApplication.processEvents()
    text = dlg.status_label.text()
    assert text == "请填写不少于 10 字的明文例外理由。"
    assert "取消勾选" not in text
    assert "本机 Redis 若未启用 TLS" not in text
    assert "测试明文连接前" not in text
    assert _reason_invalid(dlg)
    _assert_status_above_footer(dlg)
    dlg.close()


def test_unchecking_tls_does_not_show_error_until_submit(qapp):
    dlg = _show_dialog()
    assert dlg.tls.isChecked()
    assert dlg.tls.isEnabled()
    assert not dlg.allow_plain.isVisible()
    assert not dlg.reason_row.isVisible()
    dlg.tls.setChecked(False)
    QApplication.processEvents()
    assert not dlg.tls.isChecked()
    assert dlg.tls.isEnabled()
    assert dlg.allow_plain.isVisible()
    assert dlg.allow_plain.isEnabled()
    assert dlg.allow_plain.isChecked()
    assert dlg.reason_row.isVisible()
    assert "10" in dlg.reason.placeholderText()
    assert not _reason_invalid(dlg)
    assert not _status_visible(dlg)
    dlg.tls.setChecked(True)
    QApplication.processEvents()
    assert dlg.tls.isChecked()
    assert dlg.tls.isEnabled()
    assert not dlg.allow_plain.isVisible()
    assert not dlg.reason_row.isVisible()
    assert not _reason_invalid(dlg)
    assert not _status_visible(dlg)
    dlg.close()


def test_opening_tls_off_profile_auto_checks_plaintext_without_error(qapp):
    profile = ConnectionProfile(
        id="c_plain",
        name="local",
        endpoint=Endpoint("127.0.0.1", 6379),
        tls=TlsConfig(enabled=False, allow_plaintext=False, plaintext_reason=None),
    )
    dlg = _show_dialog(profile)
    assert not dlg.tls.isChecked()
    assert dlg.tls.isEnabled()
    assert dlg.allow_plain.isVisible()
    assert dlg.allow_plain.isChecked()
    assert dlg.reason_row.isVisible()
    assert not _reason_invalid(dlg)
    assert not _status_visible(dlg)
    dlg.close()


def test_validation_error_appears_only_after_test_or_save(qapp):
    dlg = _show_dialog()
    dlg.tls.setChecked(False)
    QApplication.processEvents()
    assert not _reason_invalid(dlg)
    assert not _status_visible(dlg)
    dlg.test_button.click()
    QApplication.processEvents()
    assert dlg.status_label.text() == "请填写不少于 10 字的明文例外理由。"
    assert _reason_invalid(dlg)
    _assert_status_above_footer(dlg)
    dlg.reason.setText("本机开发环境明文 Redis")
    QApplication.processEvents()
    assert not _reason_invalid(dlg)
    assert not _status_visible(dlg)
    dlg.reason.clear()
    QApplication.processEvents()
    assert _reason_invalid(dlg)
    assert dlg.status_label.text() == "请填写不少于 10 字的明文例外理由。"
    dlg.allow_plain.setChecked(False)
    QApplication.processEvents()
    assert dlg.status_label.text() == "关闭 TLS 时请勾选「允许明文」。"
    assert "理由" not in dlg.status_label.text()
    assert not dlg.reason_row.isVisible()
    assert not _reason_invalid(dlg)
    dlg.close()


def test_ok_button_validates_plaintext_without_accepting(qapp):
    dlg = _show_dialog()
    dlg.tls.setChecked(False)
    QApplication.processEvents()
    dlg.ok_button.click()
    QApplication.processEvents()
    assert dlg.isVisible()
    assert dlg.status_label.text() == "请填写不少于 10 字的明文例外理由。"
    assert _reason_invalid(dlg)
    dlg.close()

def test_reason_hidden_until_plaintext_is_checked(qapp):
    dlg = _show_dialog()
    dlg.tls.setChecked(False)
    QApplication.processEvents()
    assert dlg.allow_plain.isChecked()
    assert dlg.reason_row.isVisible()
    assert not _reason_invalid(dlg)
    assert not _status_visible(dlg)

    dlg.allow_plain.setChecked(False)
    QApplication.processEvents()
    assert not dlg.allow_plain.isChecked()
    assert not dlg.reason_row.isVisible()
    assert not _reason_invalid(dlg)
    assert not _status_visible(dlg)

    dlg.test_button.click()
    QApplication.processEvents()
    assert dlg.status_label.text() == "关闭 TLS 时请勾选「允许明文」。"
    assert "理由" not in dlg.status_label.text()
    assert not dlg.reason_row.isVisible()
    assert not _reason_invalid(dlg)
    _assert_status_above_footer(dlg)

    dlg.allow_plain.setChecked(True)
    QApplication.processEvents()
    assert dlg.reason_row.isVisible()
    assert dlg.status_label.text() == "请填写不少于 10 字的明文例外理由。"
    assert _reason_invalid(dlg)
    dlg.close()

