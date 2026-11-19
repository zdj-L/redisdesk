
"""Connection table header and cell alignment, plus offscreen list-align captures."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication, QStyleFactory, QWidget

from redisdesk.app.services.connection_service import ConnectionService
from redisdesk.app.ui.widgets import RowStatusCell
from redisdesk.app.ui.pages.connections import ConnectionsPage
from redisdesk.app.ui.theme import apply_theme
from redisdesk.core.authz import StaticGrantResolver
from tests.conftest import make_profile, make_session


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication(["redisdesk-align-tests"])
    if "Fusion" in QStyleFactory.keys():
        app.setStyle("Fusion")
    apply_theme(app, "light")
    return app


def _images_diff_left(filled: QImage, blank: QImage, *, threshold: int = 28, min_run: int = 2) -> int | None:
    """Left edge of painted glyphs, not borders: first x where filled and blank differ."""
    width = min(filled.width(), blank.width())
    height = min(filled.height(), blank.height())
    if width <= 2 or height <= 2:
        return None
    y0 = max(1, height // 5)
    y1 = min(height - 1, height - height // 5)
    run = 0
    first = None
    for x in range(0, width):
        hit = False
        for y in range(y0, y1):
            a = filled.pixelColor(x, y)
            b = blank.pixelColor(x, y)
            if abs(a.red() - b.red()) + abs(a.green() - b.green()) + abs(a.blue() - b.blue()) >= threshold:
                hit = True
                break
        if hit:
            if first is None:
                first = x
            run += 1
            if run >= min_run:
                return first
        else:
            first = None
            run = 0
    return first


def _force_paint(widget) -> None:
    QWidget.update(widget)
    widget.repaint()
    QApplication.processEvents()


def _grab_rect(widget, rect: QRect) -> QImage:
    _force_paint(widget)
    pix = widget.grab(rect)
    return pix.toImage()


def _header_text_left(header, col) -> int | None:
    section_x = header.sectionViewportPosition(col)
    section_w = header.sectionSize(col)
    rect = QRect(section_x, 0, section_w, header.height())
    item = header.parent().horizontalHeaderItem(col) if hasattr(header.parent(), "horizontalHeaderItem") else None
    text = item.text() if item is not None else header.model().headerData(col, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
    filled = _grab_rect(header, rect)
    if item is not None:
        item.setText("")
    QApplication.processEvents()
    blank = _grab_rect(header, rect)
    if item is not None:
        item.setText(str(text))
    QApplication.processEvents()
    return _images_diff_left(filled, blank)


def _cell_text_left(table, row, col) -> int | None:
    index = table.model().index(row, col)
    rect = table.visualRect(index)
    viewport = table.viewport()
    widget = table.cellWidget(row, col)
    if widget is not None:
        # Compare the cell with the widget hidden so we measure button/label glyphs.
        filled = _grab_rect(viewport, rect)
        widget.setVisible(False)
        QApplication.processEvents()
        blank = _grab_rect(viewport, rect)
        widget.setVisible(True)
        QApplication.processEvents()
        return _images_diff_left(filled, blank)
    item = table.item(row, col)
    original = item.text() if item is not None else ""
    filled = _grab_rect(viewport, rect)
    if item is not None:
        item.setText("")
    QApplication.processEvents()
    blank = _grab_rect(viewport, rect)
    if item is not None:
        item.setText(original)
    QApplication.processEvents()
    return _images_diff_left(filled, blank)


def test_connection_table_header_matches_cell_alignment_and_padding(qapp, tmp_store, audit):
    previous = qapp.style().objectName()
    styles = ["Fusion"]
    for name in ("Windows", "windowsvista", "windows11"):
        if name in QStyleFactory.keys() and name not in styles:
            styles.append(name)
    try:
        for style_name in styles:
            qapp.setStyle(QStyleFactory.create(style_name))
            apply_theme(qapp, "light")
            _assert_header_cell_text_edges_align(tmp_store, audit, style_name)
    finally:
        if previous:
            qapp.setStyle(previous)
        apply_theme(qapp, "light")


def _assert_header_cell_text_edges_align(tmp_store, audit, style_name: str) -> None:
    profile = make_profile(tls=False, name="local-redis")
    grants = StaticGrantResolver({("c1", "admin")})
    service = ConnectionService(tmp_store, audit, grants)
    session = make_session()
    # Saving twice in parametrized styles is fine; reuse id c1.
    try:
        service.save(session, profile)
    except Exception:
        pass
    page = ConnectionsPage(service, console=None)
    page.set_session(session)
    page.resize(1280, 800)
    page.show()
    QApplication.processEvents()
    page._distribute_column_widths()
    QApplication.processEvents()
    table = page.table
    header = table.horizontalHeader()
    used = sum(table.columnWidth(i) for i in range(table.columnCount()))
    viewport = table.viewport().width()
    assert used >= viewport - 2
    _assert_column_widths_balanced(table)
    from redisdesk.app.ui.widgets import LeftAlignHeaderView
    assert isinstance(header, LeftAlignHeaderView)
    QApplication.processEvents()
    for col in range(table.columnCount()):
        head_x = _header_text_left(header, col)
        cell_x = _cell_text_left(table, 0, col)
        assert head_x is not None, f"{style_name} header text missing col={col}"
        assert cell_x is not None, f"{style_name} cell text missing col={col}"
        assert abs(head_x - cell_x) <= 2, f"{style_name} col={col} header_x={head_x} cell_x={cell_x}"
    page.close()
    QApplication.processEvents()



def _assert_column_widths_balanced(table) -> None:
    viewport = table.viewport().width()
    name_w = table.columnWidth(0)
    addr_w = table.columnWidth(2)
    assert name_w * 2 <= viewport, (name_w, viewport)
    assert addr_w * 2 <= viewport, (addr_w, viewport)
    status_min = RowStatusCell.minimum_column_width(table, "测试通过")
    mins = {1: 80, 3: 72, 4: 80, 5: 80, 6: status_min, 7: 120}
    for column, minimum in mins.items():
        assert table.columnWidth(column) >= minimum, (column, table.columnWidth(column), minimum)
    assert name_w >= table.columnWidth(1)
    assert addr_w >= table.columnWidth(1)
    bar = table.horizontalScrollBar()
    assert (not bar.isVisible()) or bar.maximum() == 0


def test_connection_table_column_widths_balanced_at_common_sizes(qapp, tmp_store, audit):
    profile = make_profile(tls=False, name="local-redis")
    grants = StaticGrantResolver({("c1", "admin")})
    service = ConnectionService(tmp_store, audit, grants)
    session = make_session()
    service.save(session, profile)
    page = ConnectionsPage(service, console=None)
    page.set_session(session)
    page.show()
    for width, height in ((1280, 800), (1024, 544)):
        page.setMinimumSize(width, height)
        page.resize(width, height)
        page.setGeometry(0, 0, width, height)
        QApplication.processEvents()
        page._distribute_column_widths()
        QApplication.processEvents()
        _assert_column_widths_balanced(page.table)
    page.close()
    QApplication.processEvents()




def test_status_column_shows_full_test_passed(qapp, tmp_store, audit):
    profile = make_profile(tls=False, name="local-redis")
    other = make_profile(tls=False, name="orders-dev", conn_id="c-other")
    grants = StaticGrantResolver({("c1", "admin")})
    service = ConnectionService(tmp_store, audit, grants)
    session = make_session()
    service.save(session, profile)
    try:
        service.save(session, other)
    except Exception:
        other = None
    page = ConnectionsPage(service, console=None)
    page.set_session(session)
    page.show()
    for width, height in ((1280, 800), (1024, 544)):
        page.setMinimumSize(width, height)
        page.resize(width, height)
        page.setGeometry(0, 0, width, height)
        QApplication.processEvents()
        page.reload()
        QApplication.processEvents()
        cells = list(page._status_cells.values())
        assert cells, (width, height)
        cells[0].set_ok("\u6d4b\u8bd5\u901a\u8fc7")
        if len(cells) > 1:
            cells[1].set_fail("\u5931\u8d25\uff1a" + "connection refused by peer" * 2, tooltip="connection refused by peer")
        page._distribute_column_widths()
        QApplication.processEvents()
        for cell in cells:
            cell._apply_elide()
        QApplication.processEvents()
        needed = RowStatusCell.minimum_column_width(page.table, "\u6d4b\u8bd5\u901a\u8fc7")
        assert page.table.columnWidth(6) >= needed, (width, page.table.columnWidth(6), needed)
        assert cells[0].width() >= needed - 2, (width, cells[0].width(), needed)
        assert cells[0].label.fontMetrics().horizontalAdvance("\u6d4b\u8bd5\u901a\u8fc7") <= cells[0].label.width(), (
            width, cells[0].label.width(), cells[0].label.text(), cells[0].label.fontMetrics().horizontalAdvance("\u6d4b\u8bd5\u901a\u8fc7")
        )
        assert cells[0].label.text() == "\u6d4b\u8bd5\u901a\u8fc7"
        assert page.table.columnWidth(7) >= 120, (width, page.table.columnWidth(7))
        if len(cells) > 1:
            assert cells[1].label.text().startswith("\u5931\u8d25"), cells[1].label.text()
            assert "\u5931\u8d25\uff1a" in cells[1]._full_text or cells[1]._full_text.startswith("\u5931\u8d25")
            assert "connection refused" in (cells[1].toolTip() or "")
        _assert_column_widths_balanced(page.table)
    page.close()
    QApplication.processEvents()


def test_capture_connection_list_align(qapp, tmp_store, audit):
    from redisdesk.app.services.audit_service import AuditService
    from redisdesk.app.services.auth_service import AuthService
    from redisdesk.app.services.console_service import ConsoleService
    from redisdesk.app.ui.mainwindow import MainWindow
    from redisdesk.infra.session import SessionManager
    from redisdesk.infra.store import StoreGrantResolver

    profile = make_profile(tls=False, name="local-redis")
    sessions = SessionManager()
    grants = StoreGrantResolver(tmp_store)
    service = ConnectionService(tmp_store, audit, grants)
    auth = AuthService(tmp_store, audit, sessions)
    session = auth.ensure_local_session()
    service.save(session, profile)
    try:
        from dataclasses import replace as _replace
        other = _replace(make_profile(tls=False, name="orders-dev"), id="c-orders")
        service.save(session, other)
        try:
            service.grant(session, other.id, session.role)
        except Exception:
            pass
    except Exception:
        pass
    try:
        service.grant(session, profile.id, session.role)
    except Exception:
        pass
    window = MainWindow(
        auth=auth,
        connections=service,
        console=ConsoleService(tmp_store, audit, grants),
        audits=AuditService(audit, grants),
        sessions=sessions,
    )
    window.bind_session(session)
    page = window.connections_page
    page.reload()
    QApplication.processEvents()
    cells = list(page._status_cells.values())
    if cells:
        cells[0].set_ok("测试通过")
        if len(cells) > 1:
            cells[1].set_fail("失败：连接被拒绝", tooltip="连接被拒绝")
    artifacts = Path(__file__).resolve().parents[1] / "artifacts"
    artifacts.mkdir(exist_ok=True)
    suffix = os.environ.get("REDISDESK_CAPTURE_SUFFIX", "")

    def capture(name, size):
        window.show()
        QApplication.processEvents()
        window.setMinimumSize(*size)
        window.resize(*size)
        window.setGeometry(0, 0, *size)
        QApplication.processEvents()
        assert (window.width(), window.height()) == size, (window.width(), window.height(), size)
        path = artifacts / f"{name}{suffix}.png"
        assert window.grab().save(str(path), "PNG")
        return path

    capture("connection-list-align-1280x800", (1280, 800))
    capture("connection-list-align-1024x544", (1024, 544))
    window.close()
    QApplication.processEvents()
