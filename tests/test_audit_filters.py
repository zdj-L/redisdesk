"""Audit filter combos must show full labels at common window widths."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QStyleFactory

from redisdesk.app.services.audit_service import AuditService
from redisdesk.app.ui.pages.audit import AuditPage
from redisdesk.app.ui.theme import apply_theme
from redisdesk.core.authz import StaticGrantResolver
from tests.conftest import make_session


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication(["redisdesk-audit-filters"])
    if "Fusion" in QStyleFactory.keys():
        app.setStyle("Fusion")
    apply_theme(app, "light")
    return app


def _build_page(tmp_store, audit) -> AuditPage:
    grants = StaticGrantResolver({("c1", "admin")})
    service = AuditService(audit, grants)
    session = make_session()
    # Seed a couple of event types so the event combo has real labels.
    for ev in ("command", "login", "connect"):
        try:
            audit.append(
                {
                    "actor_user_id": "u1",
                    "actor_username": "alice",
                    "actor_role": "admin",
                    "client_host": "localhost",
                    "client_ip": None,
                    "session_id": "s1",
                    "connection_id": "c1",
                    "connection_name": "local-redis",
                    "env": "dev",
                    "host": "127.0.0.1",
                    "port": 1,
                    "event": ev,
                    "command_name": "INFO",
                    "argv_masked": "INFO",
                    "argv_digest": ev,
                    "risk_level": "L1",
                    "decision": "allow",
                    "deny_reason": None,
                    "result": "ok",
                    "duration_ms": 1,
                    "impact_estimate": None,
                    "error_code": None,
                }
            )
        except Exception:
            pass
    page = AuditPage(service)
    page.set_session(session)
    return page


def _assert_combo_shows_full_text(combo) -> None:
    combo.fit_to_contents()
    QApplication.processEvents()
    fm = combo.fontMetrics()
    for index in range(combo.count()):
        text = combo.itemText(index)
        needed = fm.horizontalAdvance(text) + 12 + 32 + 8
        assert combo.width() >= needed - 2, (text, combo.width(), needed)
        assert combo.minimumWidth() >= needed - 2, (text, combo.minimumWidth(), needed)


def test_audit_filter_combos_show_full_labels(qapp, tmp_store, audit):
    page = _build_page(tmp_store, audit)
    page.show()
    for width, height in ((1280, 800), (1024, 544)):
        page.setMinimumSize(width, height)
        page.resize(width, height)
        page.setGeometry(0, 0, width, height)
        QApplication.processEvents()
        page.event_filter.fit_to_contents()
        page.result_filter.fit_to_contents()
        QApplication.processEvents()
        # Default items "all events" / "all results" must be fully visible.
        assert page.event_filter.count() >= 1
        assert page.result_filter.count() >= 1
        _assert_combo_shows_full_text(page.event_filter)
        _assert_combo_shows_full_text(page.result_filter)
    page.close()


def test_capture_audit_filters(qapp, tmp_store, audit):
    page = _build_page(tmp_store, audit)
    artifacts = Path(__file__).resolve().parents[1] / "artifacts"
    artifacts.mkdir(exist_ok=True)
    suffix = os.environ.get("REDISDESK_CAPTURE_SUFFIX", "")
    for name, size in (("audit-filters-1280x800", (1280, 800)), ("audit-filters-1024x544", (1024, 544))):
        page.show()
        QApplication.processEvents()
        page.setMinimumSize(*size)
        page.resize(*size)
        page.setGeometry(0, 0, *size)
        page.event_filter.fit_to_contents()
        page.result_filter.fit_to_contents()
        QApplication.processEvents()
        assert (page.width(), page.height()) == size, (page.width(), page.height(), size)
        path = artifacts / f"{name}{suffix}.png"
        assert page.grab().save(str(path), "PNG")
    page.close()
