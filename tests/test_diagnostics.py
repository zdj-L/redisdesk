"""Diagnostics page pulls read-only INFO from an isolated RESP server."""
from __future__ import annotations

import os
import socketserver
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QStyleFactory

from redisdesk.app.services.connection_service import ConnectionService
from redisdesk.app.ui.pages.diagnostics import DiagnosticsPage
from redisdesk.app.ui.diagnostics_metrics import CARD_CLIENTS, CARD_KEYSPACE, CARD_MEMORY, CARD_PERSIST, CARD_REPLICATION, CARD_SERVER, CARD_STATS, DASH, SECTION_ORDER, format_save_time
from redisdesk.app.ui.theme import apply_theme
from redisdesk.core.authz import StaticGrantResolver
from redisdesk.core.model import Endpoint
from tests.conftest import make_profile, make_session


INFO_TEXT = (
    "# Server\r\nredis_version:7.2.0\r\nredis_mode:standalone\r\nos:Windows\r\n"
    "uptime_in_seconds:12345\r\n"
    "# Memory\r\nused_memory_human:1.00M\r\nused_memory_peak_human:2.00M\r\n"
    "used_memory_rss_human:1.20M\r\nmem_fragmentation_ratio:1.15\r\n"
    "maxmemory_human:0B\r\nmaxmemory_policy:allkeys-lru\r\n"
    "# Clients\r\nconnected_clients:3\r\nblocked_clients:0\r\n"
    "rejected_connections:0\r\ntotal_connections_received:9\r\n"
    "# Stats\r\ninstantaneous_ops_per_sec:42\r\nkeyspace_hits:90\r\n"
    "keyspace_misses:10\r\nexpired_keys:5\r\nevicted_keys:1\r\n"
    "total_commands_processed:1000\r\n"
    "# Persistence\r\nrdb_last_bgsave_status:ok\r\n"
    "rdb_changes_since_last_save:7\r\nrdb_last_save_time:1700000000\r\naof_enabled:0\r\n"
    "# Replication\r\nrole:master\r\nconnected_slaves:0\r\n"
    "# Keyspace\r\ndb0:keys=14,expires=0,avg_ttl=0\r\n"
    "db1:keys=0,expires=0,avg_ttl=0\r\ndb2:keys=3,expires=1,avg_ttl=100\r\n"
)



def bulk(text: str) -> bytes:
    data = text.encode()
    return b"$" + str(len(data)).encode() + b"\r\n" + data + b"\r\n"


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        while True:
            line = self.rfile.readline()
            if not line:
                return
            if not line.startswith(b"*"):
                continue
            n = int(line[1:])
            parts = []
            for _ in range(n):
                size = int(self.rfile.readline()[1:])
                parts.append(self.rfile.read(size))
                self.rfile.read(2)
            cmd = parts[0].upper()
            if cmd in {b"SELECT", b"AUTH"}:
                self.wfile.write(b"+OK\r\n")
            elif cmd == b"PING":
                self.wfile.write(b"+PONG\r\n")
            elif cmd == b"INFO":
                self.wfile.write(bulk(INFO_TEXT))
            elif cmd == b"CLIENT":
                self.wfile.write(b"-ERR unknown command CLIENT\r\n")
            else:
                self.wfile.write(b"-NOPERM command denied\r\n")
            self.wfile.flush()


@contextmanager
def server_context():
    with Server(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=3)


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication(["redisdesk-diag-tests"])
    if "Fusion" in QStyleFactory.keys():
        app.setStyle("Fusion")
    apply_theme(app, "light")
    return app


def pump_until(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _profile_on(port: int, name: str = "orders-dev"):
    return replace(make_profile(tls=False, name=name), endpoint=Endpoint("127.0.0.1", port))

def test_diagnostics_fetches_live_info_and_tables_fit(qapp, tmp_store, audit):
    with server_context() as server:
        profile = _profile_on(server.server_address[1])
        grants = StaticGrantResolver({("c1", "admin")})
        service = ConnectionService(tmp_store, audit, grants)
        session = make_session()
        service.save(session, profile)
        page = DiagnosticsPage(service)
        page.resize(1280, 800)
        page.show()
        QApplication.processEvents()
        page.set_context(session, profile)
        server_table = page.tables[CARD_SERVER]
        assert pump_until(lambda: server_table.item(0, 1) is not None and server_table.item(0, 1).text() == "7.2.0")
        assert "1.00M" in page.tables[CARD_MEMORY].item(0, 1).text()
        assert page.tables[CARD_CLIENTS].item(0, 1).text() == "3"
        assert page.tables[CARD_SERVER].item(2, 1).text() == "3 \u5c0f\u65f6 25 \u5206\u949f"
        assert page.tables[CARD_SERVER].item(3, 1).text().startswith("\u5355\u673a")
        assert all(page.tables[CARD_SERVER].item(i, 0).text() != "\u89d2\u8272" for i in range(page.tables[CARD_SERVER].rowCount()))
        assert page.tables[CARD_MEMORY].item(2, 1).text() == "1.20M"
        assert "allkeys-lru" in page.tables[CARD_MEMORY].item(5, 1).text()
        assert page.tables[CARD_STATS].item(0, 1).text() == "42 \u6b21/\u79d2"
        assert page.tables[CARD_STATS].item(1, 1).text() == "90.0%"
        assert page.tables[CARD_PERSIST].item(3, 1).text() == "\u5173\u95ed"
        assert page.tables[CARD_PERSIST].item(2, 1).text() == format_save_time("1700000000")
        assert all("keys=" not in page.tables[CARD_PERSIST].item(i, 1).text() for i in range(page.tables[CARD_PERSIST].rowCount()))
        assert page.tables[CARD_REPLICATION].item(0, 1).text() == "\u4e3b\u8282\u70b9"
        assert page.tables[CARD_REPLICATION].item(1, 1).text() == "\u672c\u8282\u70b9\u4e3a\u4e3b"
        ks = page.tables[CARD_KEYSPACE]
        assert [ks.item(i, 0).text() for i in range(ks.rowCount())] == ["DB 0", "DB 2"]
        assert "14 \u4e2a\u952e" in ks.item(0, 1).text()
        assert "3 \u4e2a\u952e" in ks.item(1, 1).text() and "1 \u4e2a\u5e26\u8fc7\u671f" in ks.item(1, 1).text()
        assert set(page.sections) == {CARD_SERVER, CARD_MEMORY, CARD_STATS, CARD_CLIENTS, CARD_PERSIST, CARD_REPLICATION, CARD_KEYSPACE}
        assert "INFO" in page.status.text()
        assert page.status.wordWrap() is False
        assert page.status.width() >= 240
        page.resize(1280, 800)
        QApplication.processEvents()
        page._sync_stack_height()
        QApplication.processEvents()
        _assert_sections_fit(page)
        page.close()


def test_diagnostics_shows_chinese_error_without_fake_values(qapp, tmp_store, audit):
    grants = StaticGrantResolver({("c1", "admin")})
    service = ConnectionService(tmp_store, audit, grants)
    session = make_session()
    profile = replace(make_profile(tls=False), endpoint=Endpoint("127.0.0.1", 1))
    service.save(session, profile)
    page = DiagnosticsPage(service)
    page.show()
    QApplication.processEvents()
    page.set_context(session, profile)
    assert pump_until(lambda: page.status.text().startswith("\u65e0\u6cd5\u62c9\u53d6 INFO"))
    assert page.tables[CARD_SERVER].item(0, 1).text() == DASH
    page.set_context(session, None)
    QApplication.processEvents()
    assert "\u5c1a\u672a\u9009\u62e9" in page.status.text()
    page.close()


def _open_diagnostics_window(tmp_store, audit, server, name="local-redis"):
    from redisdesk.app.services.audit_service import AuditService
    from redisdesk.app.services.auth_service import AuthService
    from redisdesk.app.services.console_service import ConsoleService
    from redisdesk.app.ui.mainwindow import MainWindow
    from redisdesk.infra.session import SessionManager
    from redisdesk.infra.store import StoreGrantResolver

    profile = _profile_on(server.server_address[1], name)
    sessions = SessionManager()
    grants = StoreGrantResolver(tmp_store)
    service = ConnectionService(tmp_store, audit, grants)
    auth = AuthService(tmp_store, audit, sessions)
    session = auth.ensure_local_session()
    service.save(session, profile)
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
    window.diag_page.set_context(session, profile)
    server_table = window.diag_page.tables[CARD_SERVER]
    assert pump_until(lambda: server_table.item(0, 1) is not None and server_table.item(0, 1).text() == "7.2.0")
    if "diagnostics" in getattr(window, "nav_keys", []):
        window._goto(window.nav_keys.index("diagnostics"))
    else:
        window.stack.setCurrentWidget(window.diag_page)
    apply_theme(QApplication.instance(), "light")
    return window


def _size_main_window(window, size: tuple[int, int]) -> None:
    window.show()
    QApplication.processEvents()
    window.setMinimumSize(*size)
    window.resize(*size)
    window.setGeometry(0, 0, *size)
    QApplication.processEvents()
    window.diag_page._sync_stack_height()
    QApplication.processEvents()
    assert (window.width(), window.height()) == size, (window.width(), window.height(), size)


def _global_rect(widget) -> QRect:
    return QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())


def _assert_last_rows_inside_sections(page) -> None:
    for title, table in page.tables.items():
        hbar = table.horizontalScrollBar()
        vbar = table.verticalScrollBar()
        assert (not hbar.isVisible()) or hbar.maximum() == 0, title
        assert (not vbar.isVisible()) or vbar.maximum() == 0, title
        assert table.rowCount() >= 1, title
        last = table.rowCount() - 1
        last_rect = table.visualRect(table.model().index(last, 0))
        assert last_rect.isValid(), title
        assert last_rect.bottom() <= table.viewport().height(), (title, last_rect.bottom(), table.viewport().height())
        section = page.sections[title]
        mapped = table.viewport().mapTo(section, last_rect.bottomRight())
        limit = section.height() - section.contentsMargins().bottom()
        assert mapped.y() <= limit + 1, (title, mapped.y(), section.height(), limit)


def _assert_sections_fit(page) -> None:
    page._sync_stack_height()
    QApplication.processEvents()
    _assert_last_rows_inside_sections(page)
    rects = {title: QRect(section.mapTo(page.grid_host, QPoint(0, 0)), section.size()) for title, section in page.sections.items()}
    titles = list(rects)
    for i, a in enumerate(titles):
        for b in titles[i + 1 :]:
            assert not rects[a].intersects(rects[b]), (a, b, rects[a], rects[b])
    _assert_single_column_flow(rects)


def _assert_single_column_flow(rects: dict, *, max_gap: int = 28) -> None:
    """Sections stack downward with a tight gap and no hole beside a short block."""
    order = [title for title in SECTION_ORDER if title in rects]
    assert order, "no sections"
    lefts = [rects[title].left() for title in order]
    widths = [rects[title].width() for title in order]
    assert max(lefts) - min(lefts) <= 2, lefts
    assert max(widths) - min(widths) <= 2, widths
    for previous, current in zip(order, order[1:]):
        upper, lower = rects[previous], rects[current]
        assert upper.bottom() <= lower.top(), (previous, current, upper, lower)
        gap = lower.top() - upper.bottom()
        assert 0 <= gap <= max_gap, (previous, current, gap, upper, lower, "large empty gap under a short section")


def _assert_diag_sections_in_main_window(window, *, first_sections_visible: bool) -> None:
    page = window.diag_page
    page._sync_stack_height()
    QApplication.processEvents()
    rects = {title: _global_rect(section) for title, section in page.sections.items()}
    titles = list(rects)
    for i, a in enumerate(titles):
        for b in titles[i + 1 :]:
            assert not rects[a].intersects(rects[b]), (a, b, rects[a], rects[b], "sections overlap")
    _assert_single_column_flow(rects)
    for title, section in page.sections.items():
        label = section.title_label
        lr = _global_rect(label)
        sr = rects[title]
        assert lr.left() >= sr.left()
        assert lr.top() >= sr.top()
        assert lr.bottom() <= sr.bottom() + 1
        assert lr.right() <= sr.right() + 1
        assert 0 <= lr.left() - sr.left() <= 16, (title, lr.left() - sr.left())
    _assert_last_rows_inside_sections(page)
    viewport = _global_rect(page.grid_scroll.viewport())
    if first_sections_visible:
        bar = page.grid_scroll.verticalScrollBar()
        assert bar.value() == 0 or bar.maximum() == 0
        visible = []
        for title in SECTION_ORDER:
            sr = rects[title]
            if sr.top() >= viewport.top() - 1 and sr.bottom() <= viewport.bottom() + 1:
                visible.append(title)
        # 1280x800 should show several stacked sections, including the first two in full.
        assert CARD_SERVER in visible and CARD_MEMORY in visible, (visible, viewport, {k: (rects[k].top(), rects[k].bottom()) for k in SECTION_ORDER})
        assert len(visible) >= 2, (visible, viewport)


def _parse_hex(color: str) -> QColor:
    return QColor(color)


def _color_distance(actual: QColor, expected: QColor) -> int:
    return abs(actual.red() - expected.red()) + abs(actual.green() - expected.green()) + abs(actual.blue() - expected.blue())


def _diag_content_gap_color(page) -> QColor:
    """Sample the tight gap between two stacked sections (must follow theme surface)."""
    upper = page.sections[CARD_SERVER]
    lower = page.sections[CARD_MEMORY]
    host = page.grid_host
    above = upper.mapTo(host, QPoint(max(24, upper.width() // 2), upper.height() - 1))
    below = lower.mapTo(host, QPoint(max(24, lower.width() // 2), 0))
    mid = QPoint(above.x(), (above.y() + below.y()) // 2)
    QApplication.processEvents()
    image = host.grab().toImage()
    assert 0 <= mid.x() < image.width() and 0 <= mid.y() < image.height(), (mid, image.width(), image.height(), above, below)
    return image.pixelColor(mid)


def _assert_diag_content_follows_surface(page, theme_name: str) -> None:
    from redisdesk.app.ui.theme import palette

    tokens = palette(theme_name)
    sampled = _diag_content_gap_color(page)
    surface = _parse_hex(tokens["surface"])
    bg = _parse_hex(tokens["bg"])
    assert _color_distance(sampled, surface) <= 18, (theme_name, sampled.name(), tokens["surface"], tokens["bg"])
    assert _color_distance(sampled, surface) < _color_distance(sampled, bg), (
        theme_name, sampled.name(), tokens["surface"], tokens["bg"]
    )
    if theme_name == "light":
        assert sampled.red() >= 248 and sampled.green() >= 248 and sampled.blue() >= 248
        assert _color_distance(sampled, _parse_hex("#FFFFFF")) < _color_distance(sampled, _parse_hex("#F7F8FA"))


def test_diagnostics_sections_layout_in_main_window(qapp, tmp_store, audit):
    """Geometry must be measured in the real main window with mapToGlobal."""
    with server_context() as server:
        window = _open_diagnostics_window(tmp_store, audit, server)
        _size_main_window(window, (1280, 800))
        _assert_diag_sections_in_main_window(window, first_sections_visible=True)
        _size_main_window(window, (1024, 544))
        _assert_diag_sections_in_main_window(window, first_sections_visible=False)
        page = window.diag_page
        bar = page.grid_scroll.verticalScrollBar()
        if bar.maximum() > 0:
            bar.setValue(bar.maximum())
            QApplication.processEvents()
            page._sync_stack_height()
            QApplication.processEvents()
        last = page.sections[CARD_KEYSPACE]
        viewport = _global_rect(page.grid_scroll.viewport())
        lr = _global_rect(last)
        # After scrolling to the end, the last section's bottom is in the viewport and rows are unclipped.
        assert lr.bottom() <= viewport.bottom() + 2, (lr, viewport, bar.maximum(), bar.value())
        _assert_last_rows_inside_sections(page)
        rects = {title: _global_rect(section) for title, section in page.sections.items()}
        titles = list(rects)
        for i, a in enumerate(titles):
            for b in titles[i + 1 :]:
                assert not rects[a].intersects(rects[b]), (a, b, rects[a], rects[b])
        window.close()


def test_diagnostics_content_background_matches_theme_surface(qapp, tmp_store, audit):
    with server_context() as server:
        window = _open_diagnostics_window(tmp_store, audit, server)
        _size_main_window(window, (1280, 800))
        apply_theme(qapp, "light")
        QApplication.processEvents()
        _assert_diag_content_follows_surface(window.diag_page, "light")
        apply_theme(qapp, "dark")
        QApplication.processEvents()
        _assert_diag_content_follows_surface(window.diag_page, "dark")
        apply_theme(qapp, "light")
        QApplication.processEvents()
        window.close()


def test_diagnostics_missing_fields_show_dash(qapp):
    page = DiagnosticsPage()
    page.resize(1280, 800)
    page.show()
    QApplication.processEvents()
    page.show_info({"redis_version": "3.2.100", "os": "Linux", "uptime_in_seconds": "60", "role": "master"})
    assert page.tables[CARD_SERVER].item(0, 1).text() == "3.2.100"
    assert page.tables[CARD_SERVER].item(2, 1).text() == "1 \u5206\u949f"
    assert page.tables[CARD_SERVER].item(3, 1).text() == DASH
    assert page.tables[CARD_MEMORY].item(2, 1).text() == DASH
    assert page.tables[CARD_STATS].item(1, 1).text() == DASH
    assert page.tables[CARD_PERSIST].item(2, 1).text() == DASH
    assert page.tables[CARD_REPLICATION].item(0, 1).text() == "\u4e3b\u8282\u70b9"
    assert page.tables[CARD_REPLICATION].item(1, 1).text() == "\u672c\u8282\u70b9\u4e3a\u4e3b"
    assert page.tables[CARD_REPLICATION].item(3, 1).text() == DASH
    assert page.tables[CARD_KEYSPACE].item(0, 1).text() == DASH
    page.show_info({"keyspace_hits": "0", "keyspace_misses": "0"})
    assert page.tables[CARD_STATS].item(1, 1).text() == DASH
    page.close()


def test_capture_diagnostics_info(qapp, tmp_store, audit):
    with server_context() as server:
        window = _open_diagnostics_window(tmp_store, audit, server)
        artifacts = Path(__file__).resolve().parents[1] / "artifacts"
        artifacts.mkdir(exist_ok=True)
        suffix = os.environ.get("REDISDESK_CAPTURE_SUFFIX", "")
        for name, size in (("diagnostics-info-1280x800", (1280, 800)), ("diagnostics-info-1024x544", (1024, 544))):
            _size_main_window(window, size)
            path = artifacts / f"{name}{suffix}.png"
            assert window.grab().save(str(path), "PNG")
            assert (window.width(), window.height()) == size
        window.close()
