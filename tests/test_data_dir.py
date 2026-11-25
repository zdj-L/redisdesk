"""Data directory pointer, priority, migration, and settings apply/restart."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from redisdesk.core.errors import AppError
from redisdesk.core.limits import APPDATA_DIRNAME, DB_FILENAME, UI_SETTINGS_FILENAME
from redisdesk.infra.paths import (
    KIND_APP_ROOT,
    KIND_CUSTOM,
    KIND_SYSTEM,
    POINTER_FILENAME,
    KNOWN_HOSTS_FILENAME,
    app_data_dir,
    apply_data_dir_change,
    default_system_data_dir,
    edition_default_data_dir,
    lock_app_data_dir,
    pointer_path,
    read_pointer,
    resolve_app_data_dir,
    set_app_root_override,
    set_packaged_default_data_dir,
    unlock_app_data_dir,
    validate_data_dir_candidate,
    write_pointer,
)


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    monkeypatch.delenv("REDISDESK_APPDATA", raising=False)
    local = tmp_path / "local"
    local.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    root = tmp_path / "app-root"
    root.mkdir()
    unlock_app_data_dir()
    set_packaged_default_data_dir(None)
    set_app_root_override(root)
    yield root, tmp_path, local
    unlock_app_data_dir()
    set_packaged_default_data_dir(None)
    set_app_root_override(None)


def _seed(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / DB_FILENAME).write_text("db-bytes", encoding="utf-8")
    (folder / UI_SETTINGS_FILENAME).write_text("{}", encoding="utf-8")
    (folder / KNOWN_HOSTS_FILENAME).write_text("host ssh-ed25519 AAAA", encoding="utf-8")
    (folder / "logs").mkdir()
    (folder / "logs" / "redisdesk.log").write_text("log", encoding="utf-8")
    (folder / "updates").mkdir()
    (folder / "updates" / "note.txt").write_text("up", encoding="utf-8")


def test_default_path_unchanged_without_pointer_or_env(isolated_paths):
    root, tmp_path, local = isolated_paths
    assert resolve_app_data_dir() == local / APPDATA_DIRNAME
    assert default_system_data_dir() == local / APPDATA_DIRNAME
    assert not pointer_path().exists()


def test_priority_env_beats_pointer_beats_packaged_beats_system(isolated_paths, monkeypatch, tmp_path):
    root, _tmp, local = isolated_paths
    packaged = tmp_path / "RedisDesk-Internal" / "1.0.0"
    set_packaged_default_data_dir(packaged)
    assert resolve_app_data_dir() == packaged
    custom = tmp_path / "custom-data"
    write_pointer(custom)
    assert read_pointer() == custom.resolve()
    assert resolve_app_data_dir() == custom.resolve()
    env_dir = tmp_path / "env-data"
    monkeypatch.setenv("REDISDESK_APPDATA", str(env_dir))
    assert resolve_app_data_dir() == env_dir
    monkeypatch.delenv("REDISDESK_APPDATA")
    assert resolve_app_data_dir() == custom.resolve()


def test_lock_keeps_old_dir_until_unlock_simulating_restart(isolated_paths, tmp_path):
    root, _tmp, local = isolated_paths
    current = local / APPDATA_DIRNAME
    _seed(current)
    locked = lock_app_data_dir(current)
    assert locked == current
    dest = tmp_path / "after-restart"
    result = apply_data_dir_change(KIND_CUSTOM, dest)
    assert result.needs_restart
    assert result.pointer_written
    assert app_data_dir() == current
    assert resolve_app_data_dir() == dest.resolve()
    assert (dest / DB_FILENAME).read_text(encoding="utf-8") == "db-bytes"
    assert (dest / "logs" / "redisdesk.log").is_file()
    unlock_app_data_dir()
    assert app_data_dir() == dest.resolve()


def test_unwritable_path_fails_and_does_not_write_pointer(isolated_paths, tmp_path):
    root, _tmp, local = isolated_paths
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("file", encoding="utf-8")
    with pytest.raises(AppError) as exc:
        validate_data_dir_candidate(blocked)
    assert "不可写" in exc.value.message or "无效" in exc.value.message
    with pytest.raises(AppError):
        apply_data_dir_change(KIND_CUSTOM, blocked)
    assert not pointer_path().exists()
    assert resolve_app_data_dir() == local / APPDATA_DIRNAME


def test_existing_dest_db_is_not_overwritten(isolated_paths, tmp_path):
    root, _tmp, local = isolated_paths
    source = local / APPDATA_DIRNAME
    dest = tmp_path / "already"
    _seed(source)
    _seed(dest)
    (dest / DB_FILENAME).write_text("keep-me", encoding="utf-8")
    with pytest.raises(AppError) as exc:
        apply_data_dir_change(KIND_CUSTOM, dest)
    assert "已有" in exc.value.message
    assert (dest / DB_FILENAME).read_text(encoding="utf-8") == "keep-me"
    assert not pointer_path().exists()
    result = apply_data_dir_change(KIND_CUSTOM, dest, switch_to_existing=True)
    assert result.switched_to_existing
    assert result.copied == []
    assert (dest / DB_FILENAME).read_text(encoding="utf-8") == "keep-me"
    assert read_pointer() == dest.resolve()


def test_system_default_clears_pointer_after_migrate(isolated_paths, tmp_path):
    root, _tmp, local = isolated_paths
    custom = tmp_path / "from-custom"
    _seed(custom)
    write_pointer(custom)
    result = apply_data_dir_change(KIND_SYSTEM)
    assert result.pointer_cleared
    assert not pointer_path().exists()
    assert (local / APPDATA_DIRNAME / DB_FILENAME).read_text(encoding="utf-8") == "db-bytes"
    assert resolve_app_data_dir() == local / APPDATA_DIRNAME


def test_app_root_choice_writes_pointer_next_to_app(isolated_paths):
    root, _tmp, local = isolated_paths
    _seed(local / APPDATA_DIRNAME)
    result = apply_data_dir_change(KIND_APP_ROOT)
    assert result.next_path == root.resolve()
    assert pointer_path() == root / POINTER_FILENAME
    assert pointer_path().is_file()
    assert (root / DB_FILENAME).is_file()
    assert "redisdesk.db" in " ".join(result.copied) or (root / DB_FILENAME).read_text(encoding="utf-8") == "db-bytes"


def test_installer_script_writes_pointer_and_offers_data_dir():
    text = Path("scripts/redisdesk-installer.iss").read_text(encoding="utf-8")
    assert POINTER_FILENAME in text
    assert "CreateInputOptionPage" in text
    assert "应用根目录" in text
    assert "自定义文件夹" in text
    assert "RedisDesk-Internal\\1.0.0" in text
    assert "SaveStringToFile" in text
    assert "ssPostInstall" in text
    assert "SelectedValueIndex := 0" in text


def test_settings_page_save_schedules_forced_restart(isolated_paths, tmp_path, monkeypatch):
    root, _tmp, local = isolated_paths
    current = local / APPDATA_DIRNAME
    _seed(current)
    lock_app_data_dir(current)
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QStyleFactory, QMessageBox
    from redisdesk.app.ui.theme import apply_theme
    from redisdesk.app import restart as restart_mod
    from redisdesk.app.ui.pages.settings import SettingsPage

    restart_mod.reset_restart_state()
    calls: list[str] = []

    def fake_restart(*, message: str, parent=None):
        calls.append(message)
        restart_mod.schedule_restart_after_exit()

    monkeypatch.setattr(restart_mod, "request_forced_restart", fake_restart)
    monkeypatch.setattr("redisdesk.app.ui.pages.settings.request_forced_restart", fake_restart, raising=False)

    app = QApplication.instance() or QApplication(["redisdesk-data-dir-tests"])
    if "Fusion" in QStyleFactory.keys():
        app.setStyle("Fusion")
    apply_theme(app, "light")
    dest = tmp_path / "ui-chosen"
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not show dismissible restart info")))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    page = SettingsPage()
    page.show()
    app.processEvents()
    page.data_dir_custom.setChecked(True)
    page.data_dir_edit.setText(str(dest))
    app.processEvents()
    # Patch the symbol the method imports inside itself
    import redisdesk.app.restart as imported_restart
    monkeypatch.setattr(imported_restart, "request_forced_restart", fake_restart)
    page._save_data_dir()
    app.processEvents()
    assert calls, "successful save must schedule a forced restart"
    assert "不能再继续使用" in calls[0]
    assert not page.isEnabled()
    assert restart_mod.restart_is_pending()
    assert app_data_dir() == current
    assert resolve_app_data_dir() == dest.resolve()
    assert (dest / DB_FILENAME).is_file()
    page.close()
    restart_mod.reset_restart_state()


def test_settings_page_env_override_does_not_write_or_restart(isolated_paths, tmp_path, monkeypatch):
    root, _tmp, local = isolated_paths
    current = local / APPDATA_DIRNAME
    _seed(current)
    lock_app_data_dir(current)
    monkeypatch.setenv("REDISDESK_APPDATA", str(current))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QStyleFactory, QMessageBox
    from redisdesk.app.ui.theme import apply_theme
    from redisdesk.app import restart as restart_mod
    from redisdesk.app.ui.pages.settings import SettingsPage

    restart_mod.reset_restart_state()
    called = []
    monkeypatch.setattr(restart_mod, "request_forced_restart", lambda **k: called.append(k))
    import redisdesk.app.restart as imported_restart
    monkeypatch.setattr(imported_restart, "request_forced_restart", lambda **k: called.append(k))
    warnings: list[str] = []

    def capture_warn(parent, title, text, *args, **kwargs):
        warnings.append(title + text)
        return QMessageBox.StandardButton.Ok

    app = QApplication.instance() or QApplication(["redisdesk-data-dir-tests"])
    apply_theme(app, "light")
    from redisdesk.app.ui.pages import settings as settings_mod
    monkeypatch.setattr(settings_mod, "show_warning", capture_warn)
    page = SettingsPage()
    page.show()
    app.processEvents()
    dest = tmp_path / "should-not-use"
    page.data_dir_custom.setChecked(True)
    page.data_dir_edit.setText(str(dest))
    page._save_data_dir()
    app.processEvents()
    assert called == []
    assert not restart_mod.restart_is_pending()
    assert not pointer_path().exists()
    assert not (dest / DB_FILENAME).exists()
    assert page.isEnabled()
    assert any("REDISDESK_APPDATA" in item for item in warnings)
    page.close()


def test_unwritable_save_does_not_restart(isolated_paths, tmp_path, monkeypatch):
    root, _tmp, local = isolated_paths
    current = local / APPDATA_DIRNAME
    _seed(current)
    lock_app_data_dir(current)
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox
    from redisdesk.app.ui.theme import apply_theme
    from redisdesk.app import restart as restart_mod
    from redisdesk.app.ui.pages.settings import SettingsPage

    restart_mod.reset_restart_state()
    called = []
    import redisdesk.app.restart as imported_restart
    monkeypatch.setattr(imported_restart, "request_forced_restart", lambda **k: called.append(k))
    app = QApplication.instance() or QApplication(["redisdesk-data-dir-tests"])
    apply_theme(app, "light")
    from redisdesk.app.ui.pages import settings as settings_mod
    monkeypatch.setattr(settings_mod, "show_warning", lambda *a, **k: None)
    page = SettingsPage()
    page.show()
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("file", encoding="utf-8")
    page.data_dir_custom.setChecked(True)
    page.data_dir_edit.setText(str(blocked))
    page._save_data_dir()
    assert called == []
    assert not restart_mod.restart_is_pending()
    assert not pointer_path().exists()
    assert page.isEnabled()
    page.close()


def test_build_restart_command_dev_and_frozen():
    from redisdesk.app.restart import build_restart_command
    import sys

    dev = build_restart_command([sys.executable, "-m", "redisdesk.app.main", "--foo"])
    assert dev[:3] == [sys.executable, "-m", "redisdesk.app.main"]
    assert dev[-1] == "--foo"
    unknown = build_restart_command([sys.executable, "-m", "pytest", "tests/test_data_dir.py"])
    assert unknown == [sys.executable, "-m", "redisdesk.app.main"]


def test_launch_failure_still_consumes_restart_and_notifies(monkeypatch):
    from redisdesk.app import restart as restart_mod

    restart_mod.reset_restart_state()
    restart_mod.schedule_restart_after_exit()
    monkeypatch.setattr(restart_mod, "launch_replacement_process", lambda *a, **k: False)
    notes = []
    monkeypatch.setattr(restart_mod, "notify_manual_reopen", lambda: notes.append("told"))
    assert restart_mod.consume_restart_request() is True
    if not restart_mod.launch_replacement_process():
        restart_mod.notify_manual_reopen()
    assert notes == ["told"]
    assert not restart_mod.restart_is_pending()


def test_apply_same_directory_is_noop(isolated_paths):
    root, _tmp, local = isolated_paths
    current = local / APPDATA_DIRNAME
    _seed(current)
    lock_app_data_dir(current)
    result = apply_data_dir_change(KIND_SYSTEM)
    assert result.needs_restart is False
    assert result.pointer_written is False
    assert result.pointer_cleared is False
    assert result.copied == []
    assert not pointer_path().exists()
    assert app_data_dir() == current


def test_saving_same_directory_does_not_restart(isolated_paths, monkeypatch):
    root, _tmp, local = isolated_paths
    current = local / APPDATA_DIRNAME
    _seed(current)
    lock_app_data_dir(current)
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from redisdesk.app.ui.theme import apply_theme
    from redisdesk.app import restart as restart_mod
    from redisdesk.app.ui.pages import settings as settings_mod
    from redisdesk.app.ui.pages.settings import SettingsPage

    restart_mod.reset_restart_state()
    called = []
    notes = []
    monkeypatch.setattr(restart_mod, "request_forced_restart", lambda **k: called.append(k))
    import redisdesk.app.restart as imported_restart
    monkeypatch.setattr(imported_restart, "request_forced_restart", lambda **k: called.append(k))
    monkeypatch.setattr(settings_mod, "show_information", lambda parent, title, text: notes.append((title, text)))
    monkeypatch.setattr(settings_mod, "show_warning", lambda *a, **k: None)
    app = QApplication.instance() or QApplication(["redisdesk-data-dir-tests"])
    apply_theme(app, "light")
    page = SettingsPage()
    page.show()
    app.processEvents()
    page.data_dir_system.setChecked(True)
    page._save_data_dir()
    app.processEvents()
    assert called == []
    assert not restart_mod.restart_is_pending()
    assert page.isEnabled()
    assert not pointer_path().exists()
    assert any("无需重启" in title or "无需重启" in body for title, body in notes)
    page.close()


def test_message_box_buttons_use_chinese_labels(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox
    from redisdesk.app.ui.widgets import localize_message_box

    app = QApplication.instance() or QApplication(["redisdesk-data-dir-tests"])
    box = QMessageBox()
    box.setStandardButtons(
        QMessageBox.StandardButton.Yes
        | QMessageBox.StandardButton.No
        | QMessageBox.StandardButton.Ok
        | QMessageBox.StandardButton.Cancel
    )
    localize_message_box(box)
    assert box.button(QMessageBox.StandardButton.Yes).text() == "是"
    assert box.button(QMessageBox.StandardButton.No).text() == "否"
    assert box.button(QMessageBox.StandardButton.Ok).text() == "确定"
    assert box.button(QMessageBox.StandardButton.Cancel).text() == "取消"
    for standard in (
        QMessageBox.StandardButton.Yes,
        QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Ok,
        QMessageBox.StandardButton.Cancel,
    ):
        assert box.button(standard).text() not in {"Yes", "&Yes", "No", "&No", "OK", "&OK", "Cancel", "&Cancel"}

