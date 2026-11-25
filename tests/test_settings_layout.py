"""Settings page cards must size to their content and stay readable."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QLabel,
    QStyleFactory,
    QVBoxLayout,
    QWidget,
)

from redisdesk.app.ui.pages.settings import SettingsPage
from redisdesk.app.ui.theme import apply_theme
from redisdesk.app.ui.widgets import FormSection
from redisdesk.core.limits import AUDIT_RETENTION_DAYS


@pytest.fixture
def qapp(tmp_path, monkeypatch):
    monkeypatch.setenv("REDISDESK_APPDATA", str(tmp_path))
    app = QApplication.instance() or QApplication(["redisdesk-settings-layout"])
    if "Fusion" in QStyleFactory.keys():
        app.setStyle("Fusion")
    apply_theme(app, "light")
    return app


def _pump(app: QApplication, times: int = 8) -> None:
    for _ in range(times):
        app.processEvents()


def _wait_known_hosts(page: SettingsPage, app: QApplication) -> None:
    worker = getattr(page, "_known_worker", None)
    if worker is not None and worker.isRunning():
        worker.wait(3000)
    _pump(app)


def _mapped(child: QWidget, ancestor: QWidget) -> QRect:
    return QRect(child.mapTo(ancestor, child.rect().topLeft()), child.size())


def _visible_content(section: FormSection) -> list[tuple[QWidget, QRect]]:
    rows: list[tuple[QWidget, QRect]] = []
    for widget in section.findChildren(QWidget):
        if widget is section or not widget.isVisible() or widget.width() <= 0 or widget.height() <= 0:
            continue
        name = widget.objectName()
        if name.startswith("qt_"):
            continue
        if widget.parent() is not section and not isinstance(widget.parent(), type(section.body.layout() and None)):
            # Skip internal descendants of compound controls (combo popup, table header/viewport).
            parent = widget.parentWidget()
            if parent is not None and parent is not section and not isinstance(parent, QLabel):
                # keep direct body fields; skip children of DataTable / combo
                if parent.parentWidget() is not section and parent is not section:
                    continue
                if parent.__class__.__name__ in {"DataTable", "StyledComboBox", "QComboBox", "QPushButton", "QCheckBox"}:
                    continue
        rows.append((widget, _mapped(widget, section)))
    return rows


def _overlaps(section: FormSection) -> list[str]:
    items = _visible_content(section)
    found: list[str] = []
    for i, (left, ra) in enumerate(items):
        for right, rb in items[i + 1 :]:
            if left.isAncestorOf(right) or right.isAncestorOf(left):
                continue
            inter = ra.intersected(rb)
            if inter.width() > 2 and inter.height() > 2:
                found.append(
                    f"{left.__class__.__name__}#{left.objectName()} {ra} vs "
                    f"{right.__class__.__name__}#{right.objectName()} {rb}"
                )
    return found


def _host_settings(page: SettingsPage, width: int = 1100, height: int = 700) -> tuple[QWidget, QFrame]:
    outer = QWidget()
    layout = QVBoxLayout(outer)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(page, 1)
    status = QFrame()
    status.setObjectName("canvasFooter")
    status.setFixedHeight(32)
    layout.addWidget(status, 0)
    outer.resize(width, height)
    outer.show()
    return outer, status


def test_settings_cards_do_not_overlap_and_scroll_in_short_window(qapp):
    page = SettingsPage()
    outer, status = _host_settings(page)
    _pump(qapp)
    _wait_known_hosts(page, qapp)

    sections = page.findChildren(FormSection)
    assert [sec.findChild(QLabel, "sectionTitle").text() for sec in sections] == [
        "外观",
        "浏览默认值",
        "安全与审计",
        "数据目录",
        "应用更新",
        "SSH 已知主机",
    ]
    for sec in sections:
        assert sec.graphicsEffect() is None or not isinstance(sec.graphicsEffect(), QGraphicsDropShadowEffect)
        overlaps = _overlaps(sec)
        assert overlaps == [], f"{sec.findChild(QLabel, 'sectionTitle').text()}: {overlaps}"
        for label in sec.findChildren(QLabel):
            if not label.wordWrap() or not label.isVisible() or not label.text():
                continue
            needed = label.heightForWidth(label.width()) if label.width() else 0
            assert label.height() >= max(0, needed - 1), (
                f"{sec.findChild(QLabel, 'sectionTitle').text()} label {label.objectName()!r} "
                f"height={label.height()} hfw={needed} text={label.text()[:24]!r}"
            )

    bar = page.scroll.verticalScrollBar()
    assert bar.maximum() > 0
    # Buttons on the last card start out under the status bar, but scrolling reveals them.
    last = sections[-1]
    before = _mapped(page.delete_known_btn, outer)
    assert before.bottom() > status.y() or _mapped(last, outer).bottom() > status.y()
    bar.setValue(bar.maximum())
    _pump(qapp)
    after = _mapped(page.delete_known_btn, outer)
    assert after.bottom() <= status.y(), f"delete button still clipped: {after} status_y={status.y()}"
    refresh = _mapped(page.refresh_known_btn, outer)
    assert refresh.bottom() <= status.y()
    outer.close()


def test_settings_wrapping_hint_matches_font_metrics(qapp):
    desc = (
        "从 GitHub Releases（zdj-L/redisdesk）检查新的 Windows 安装包。"
        "发现新版本后必须由你确认才会下载和安装；清单提供 SHA-256 时必须校验通过。"
    )
    host = QWidget()
    layout = QVBoxLayout(host)
    section = FormSection("应用更新", desc)
    layout.addWidget(section, 0, Qt.AlignmentFlag.AlignTop)
    host.resize(600, 400)
    host.show()
    _pump(qapp)
    hint = [child for child in section.findChildren(QLabel) if child.objectName() == "hint"][0]
    needed = hint.fontMetrics().boundingRect(
        0, 0, hint.width(), 4000, int(Qt.TextFlag.TextWordWrap), hint.text()
    ).height()
    assert hint.width() < 600
    assert needed > hint.fontMetrics().height()
    assert hint.height() >= needed - 1
    assert _overlaps(section) == []
    host.close()


def test_settings_security_limits_stay_read_only(qapp):
    page = SettingsPage()
    page.resize(1100, 900)
    page.show()
    _pump(qapp)
    _wait_known_hosts(page, qapp)
    labels = [lab.text() for lab in page.findChildren(QLabel)]
    assert any("无需登录" in text for text in labels)
    assert any(str(AUDIT_RETENTION_DAYS) == text for text in labels)
    # The page must not offer a control that raises those product limits.
    assert page.findChild(QWidget, "allowL4") is None
    page.close()
