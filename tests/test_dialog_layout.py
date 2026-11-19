from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QFrame, QLabel, QScrollArea, QWidget

from redisdesk.app.ui.dialog_kit import StyledComboBox, available_work_area
from redisdesk.app.ui.dialogs import ConnectionEditorDialog, PasswordDialog
from redisdesk.app.ui.theme import apply_theme


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication(["redisdesk-tests"])
    apply_theme(app, "light")
    return app


def test_connection_dialog_has_scroll_and_fixed_footer(qapp):
    dlg = ConnectionEditorDialog()
    dlg.show()
    QApplication.processEvents()
    dlg.fit_to_work_area()
    QApplication.processEvents()

    scroll = dlg.findChild(QScrollArea, "dialogScroll")
    footer = dlg.findChild(QFrame, "dialogFooter")
    assert scroll is not None
    assert footer is not None
    assert dlg.ok_button is not None
    assert dlg.ok_button.text() == "确定"
    assert dlg.cancel_button.text() == "取消"
    # 页脚不在滚动区内，表单字段在滚动区内。
    parent = footer.parentWidget()
    while parent is not None:
        assert parent is not scroll
        parent = parent.parentWidget()
    assert scroll.isAncestorOf(dlg.reason)
    assert scroll.isAncestorOf(dlg.tls)
    assert scroll.isAncestorOf(dlg.protection)
    assert not scroll.isAncestorOf(footer)
    assert not scroll.isAncestorOf(dlg.ok_button)

    area = available_work_area(dlg)
    max_h = max(320, int(area.height() * 0.8))
    assert dlg.maximumHeight() <= max_h
    assert dlg.height() <= max_h
    assert dlg.width() >= 480
    assert dlg.width() <= 640
    # 页脚完全落在对话框客户区内，确定按钮未被推出。
    assert footer.geometry().top() >= 0
    assert footer.geometry().bottom() <= dlg.rect().bottom()
    assert dlg.ok_button.isEnabled()
    assert not dlg.ok_button.isHidden()
    ok_global = dlg.ok_button.mapTo(dlg, QPoint(0, 0))
    assert QRect(ok_global, dlg.ok_button.size()).intersects(dlg.rect())
    dlg.close()


def test_connection_dialog_title_is_typography_only_and_spinboxes_have_no_steppers(qapp):
    dlg = ConnectionEditorDialog()
    dlg.show()
    QApplication.processEvents()
    dlg.fit_to_work_area()
    QApplication.processEvents()

    title = dlg.findChild(QLabel, "dialogTitle")
    assert title is not None
    assert title.text() == "新建连接"
    pixmap = title.pixmap() if hasattr(title, "pixmap") else None
    if pixmap is not None:
        assert pixmap.isNull()
    header = dlg.findChild(QFrame, "dialogHeader")
    assert header is not None
    # 标题区只有排版文字和细线关闭按钮，不能再出现廉价色块图标。
    for lab in header.findChildren(QLabel):
        pix = lab.pixmap() if hasattr(lab, "pixmap") else None
        if pix is not None:
            assert pix.isNull()
        assert lab.styleSheet() == "" or "background" not in lab.styleSheet().lower()
    assert dlg.findChild(QWidget, "dialogTitleIcon") is None
    icon = dlg.windowIcon()
    assert icon.isNull() or not icon.availableSizes()
    assert dlg.port.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.NoButtons
    assert dlg.db.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.NoButtons
    dlg.close()


def test_password_dialog_uses_same_shell(qapp):
    dlg = PasswordDialog("输入口令", "请输入口令")
    dlg.show()
    QApplication.processEvents()
    dlg.fit_to_work_area()
    assert dlg.findChild(QScrollArea, "dialogScroll") is not None
    assert dlg.findChild(QFrame, "dialogFooter") is not None
    title = dlg.findChild(QLabel, "dialogTitle")
    assert title is not None
    assert title.text() == "输入口令"
    area = available_work_area(dlg)
    assert dlg.height() <= max(320, int(area.height() * 0.8))
    dlg.close()


def test_combo_popup_attaches_to_combo(qapp):
    host = QWidget()
    host.resize(400, 240)
    combo = StyledComboBox(host)
    combo.addItems(["开发", "测试", "生产"])
    combo.setGeometry(20, 40, 200, 32)
    host.show()
    QApplication.processEvents()
    combo.showPopup()
    QApplication.processEvents()
    popup = combo.view().window()
    assert popup is not None
    assert popup is not host
    assert abs(popup.width() - combo.width()) <= 2
    origin = combo.mapToGlobal(QPoint(0, combo.height() - 1))
    assert abs(popup.x() - origin.x()) <= 2
    # 弹出层紧贴控件下方，或空间不足时翻到上方，不允许随意漂移。
    below = abs(popup.y() - origin.y()) <= 4
    above_bottom = combo.mapToGlobal(QPoint(0, 0)).y()
    above = popup.y() + popup.height() <= above_bottom + 4
    assert below or above
    combo.hidePopup()
    host.close()
