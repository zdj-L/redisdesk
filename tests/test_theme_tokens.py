"""Theme palettes, persistence, live apply, and settings combo sizing."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QStyleFactory

from redisdesk.app.ui.theme import (
    THEME_CHOICES,
    TOKENS,
    UiSettings,
    apply_theme,
    current_palette,
    current_theme,
    make_qss,
    palette,
    resolve_theme,
)
from redisdesk.app.ui.pages.settings import SettingsPage
from redisdesk.app.ui.theme import TOKENS_LIGHT, make_qss as _make_qss, palette as _palette


def test_light_theme_is_contemporary_not_legacy_navy():
    tokens = _palette("light")
    assert tokens["accent"] == "#4F46E5"
    assert tokens["text_primary"] == "#0F172A"
    assert tokens["text_secondary"] == "#475569"
    assert tokens["text_muted"] == "#94A3B8"
    assert tokens["bg"] == "#F7F8FA"
    assert tokens["nav_bg"] == "#FFFFFF"
    assert tokens["nav_bg"].upper() != "#001529"
    assert tokens["accent"].upper() != "#1677FF"
    assert "001529" not in _make_qss(TOKENS_LIGHT)
    assert "1677FF" not in _make_qss(TOKENS_LIGHT)
    assert tokens["radius"] == "12px"
    assert tokens["radius_sm"] == "8px"


def test_theme_catalog_has_professional_options():
    ids = [item[0] for item in THEME_CHOICES]
    labels = [item[1] for item in THEME_CHOICES]
    assert ids == ["light", "dark", "forest", "amber", "ocean", "slate"]
    assert set(ids) <= set(TOKENS)
    # Chinese names must be complete, not truncated placeholders.
    expected = {
        "light": "\u6d45\u8272",
        "dark": "\u6df1\u8272",
        "forest": "\u68ee\u6797\u7eff",
        "amber": "\u6696\u9633\u9ec4",
        "ocean": "\u6d77\u6d0b\u84dd",
        "slate": "\u84dd\u7070",
    }
    for key, label in expected.items():
        assert label in labels
        assert resolve_theme(key) == key
    dark = palette("dark")
    assert dark["bg"].lower() < "#444444"
    assert dark["text_primary"].lower() > "#cccccc"
    assert dark["surface"].lower() < "#333333"
    forest = palette("forest")
    assert forest["accent"] != palette("light")["accent"]
    assert forest["accent"].startswith("#")
    amber = palette("amber")
    assert amber["bg"] != palette("light")["bg"]


def test_theme_preference_persists(tmp_path):
    path = tmp_path / "ui-settings.json"
    settings = UiSettings(path)
    settings.set("theme", "forest")
    loaded = UiSettings(path)
    assert loaded.get("theme") == "forest"


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication(["redisdesk-theme-tests"])
    if "Fusion" in QStyleFactory.keys():
        app.setStyle("Fusion")
    apply_theme(app, "light")
    yield app
    apply_theme(app, "light")


def test_apply_theme_changes_palette_and_stylesheet(qapp):
    seen = {}
    for mode in ("light", "dark", "forest", "amber", "ocean", "slate"):
        tokens = apply_theme(qapp, mode)
        assert current_theme() == mode
        assert current_palette()["accent"] == tokens["accent"]
        sheet = qapp.styleSheet()
        assert tokens["bg"].upper() in sheet.upper() or tokens["bg"] in sheet
        seen[mode] = tokens["accent"], tokens["bg"]
    assert len({item[0] for item in seen.values()}) >= 5
    assert seen["dark"][1] != seen["light"][1]


def test_settings_theme_combo_shows_full_names(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("REDISDESK_APPDATA", str(tmp_path))
    from redisdesk.app.ui import pages as pages_mod
    # Reconstruct page with isolated settings path.
    page = SettingsPage()
    page.ui_settings = __import__("redisdesk.app.ui.theme", fromlist=["UiSettings"]).UiSettings(tmp_path / "ui-settings.json")
    page.resize(1280, 800)
    page.show()
    QApplication.processEvents()
    page.theme.fit_to_contents()
    QApplication.processEvents()
    assert page.theme.count() == len(THEME_CHOICES)
    fm = page.theme.fontMetrics()
    for index in range(page.theme.count()):
        text = page.theme.itemText(index)
        needed = fm.horizontalAdvance(text) + 12 + 32 + 8
        assert page.theme.minimumWidth() >= needed - 2, (text, page.theme.minimumWidth(), needed)
        assert page.theme.width() >= needed - 2, (text, page.theme.width(), needed)
        assert page.theme.width() < 1280 * 0.5
    # Switching writes preference and applies QSS.
    forest_index = page.theme.findData("forest")
    assert forest_index >= 0
    page.theme.setCurrentIndex(forest_index)
    QApplication.processEvents()
    page._on_theme()
    assert page.ui_settings.get("theme") == "forest"
    page.close()


def test_capture_settings_themes(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("REDISDESK_APPDATA", str(tmp_path))
    from redisdesk.app.services.audit_service import AuditService
    from redisdesk.app.services.auth_service import AuthService
    from redisdesk.app.services.connection_service import ConnectionService
    from redisdesk.app.services.console_service import ConsoleService
    from redisdesk.app.ui.mainwindow import MainWindow
    from redisdesk.infra.audit import AuditLog
    from redisdesk.infra.session import SessionManager
    from redisdesk.infra.store import Store, StoreGrantResolver

    store = Store(tmp_path / "redisdesk.db")
    audit = AuditLog(store)
    sessions = SessionManager()
    grants = StoreGrantResolver(store)
    service = ConnectionService(store, audit, grants)
    auth = AuthService(store, audit, sessions)
    session = auth.ensure_local_session()
    window = MainWindow(
        auth=auth,
        connections=service,
        console=ConsoleService(store, audit, grants),
        audits=AuditService(audit, grants),
        sessions=sessions,
    )
    window.bind_session(session)
    # Open settings page
    if "settings" in window.nav_keys:
        window._goto(window.nav_keys.index("settings"))
    else:
        window.stack.setCurrentWidget(window.settings_page)
    artifacts = Path(__file__).resolve().parents[1] / "artifacts"
    artifacts.mkdir(exist_ok=True)
    shots = (("light", "settings-theme-light-1280x800"), ("dark", "settings-theme-dark-1280x800"), ("forest", "settings-theme-forest-1280x800"))
    for mode, name in shots:
        window._apply_theme(mode)
        # keep combo in sync for the preview
        index = window.settings_page.theme.findData(mode)
        if index >= 0:
            window.settings_page.theme.blockSignals(True)
            window.settings_page.theme.setCurrentIndex(index)
            window.settings_page.theme.blockSignals(False)
            window.settings_page.theme.fit_to_contents()
        window.show()
        QApplication.processEvents()
        window.setMinimumSize(1280, 800)
        window.resize(1280, 800)
        window.setGeometry(0, 0, 1280, 800)
        QApplication.processEvents()
        path = artifacts / f"{name}.png"
        assert window.grab().save(str(path), "PNG")
        assert (window.width(), window.height()) == (1280, 800)
    window.close()
    store.close()
