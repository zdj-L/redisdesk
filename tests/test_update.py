"""In-app updates: version compare, skip/snooze, hash refusal, settings entry.

All HTTP is mocked. Tests never touch GitHub, real Redis, or a real installer.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.infra.update import (
    PREF_AUTO_CHECK,
    PREF_LAST_CHECK,
    PREF_SKIPPED,
    PREF_SNOOZE_UNTIL,
    UpdateAsset,
    UpdateOffer,
    check_for_update,
    download_and_verify,
    is_newer,
    looks_like_setup,
    parse_sha256sums,
    parse_version,
    record_skip,
    record_snooze,
    should_auto_check,
    should_prompt,
    verify_file,
    write_manifest,
)


def test_version_compare_and_setup_name():
    assert parse_version("1.0.0") == (1, 0, 0)
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("1.0.1-internal") == (1, 0, 1)
    assert is_newer("1.0.1", "1.0.0")
    assert is_newer("1.1.0", "1.0.9")
    assert is_newer("2.0.0", "1.9.9")
    assert not is_newer("1.0.0", "1.0.0")
    assert not is_newer("v1.0.0", "1.0.0")
    assert not is_newer("1.0.0", "1.0.1")
    assert looks_like_setup("RedisDesk-1.0.1-internal-win64-setup.exe")
    assert not looks_like_setup("RedisDesk.exe")
    assert not looks_like_setup("RedisDesk-1.0.1-internal-win64-portable.zip")
    assert not looks_like_setup("uninstall.exe")
    with pytest.raises(ValueError):
        parse_version("not-a-version")


def test_skip_and_remind_later(tmp_path, monkeypatch):
    monkeypatch.setenv("REDISDESK_APPDATA", str(tmp_path))
    from redisdesk.app.ui.theme import UiSettings

    settings = UiSettings(tmp_path / "ui-settings.json")
    offer = UpdateOffer(
        version="1.0.1",
        current_version="1.0.0",
        notes="",
        asset=UpdateAsset("RedisDesk-1.0.1-internal-win64-setup.exe", "https://github.com/zdj-L/redisdesk/releases/download/v1.0.1/x-setup.exe", 10, "a" * 64),
    )
    assert should_prompt(offer, settings)
    record_skip(settings, "1.0.1")
    assert settings.get(PREF_SKIPPED) == "1.0.1"
    assert not should_prompt(offer, settings)
    settings.set(PREF_SKIPPED, "")
    now = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
    record_snooze(settings, now=now)
    assert not should_prompt(offer, settings, now=now + timedelta(hours=1))
    assert should_prompt(offer, settings, now=now + timedelta(hours=25))


def test_auto_check_respects_disable_cadence_and_dev_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("REDISDESK_APPDATA", str(tmp_path))
    monkeypatch.delenv("REDISDESK_SKIP_UPDATE_CHECK", raising=False)
    monkeypatch.delenv("REDISDESK_FORCE_UPDATE_CHECK", raising=False)
    from redisdesk.app.ui.theme import UiSettings

    settings = UiSettings(tmp_path / "ui-settings.json")
    now = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
    assert not should_auto_check(settings, now=now, frozen=False)
    assert should_auto_check(settings, now=now, frozen=True)
    settings.set(PREF_AUTO_CHECK, False)
    assert not should_auto_check(settings, now=now, frozen=True)
    settings.set(PREF_AUTO_CHECK, True)
    settings.set(PREF_LAST_CHECK, now.isoformat())
    assert not should_auto_check(settings, now=now + timedelta(hours=1), frozen=True)
    assert should_auto_check(settings, now=now + timedelta(hours=25), frozen=True)
    monkeypatch.setenv("REDISDESK_SKIP_UPDATE_CHECK", "1")
    assert not should_auto_check(settings, now=now + timedelta(hours=25), frozen=True)


def test_hash_and_size_mismatch_delete_file(tmp_path):
    payload = b"setup-bytes"
    path = tmp_path / "RedisDesk-1.0.1-internal-win64-setup.exe"
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    assert verify_file(path, sha256=digest, size=len(payload)) == digest
    path.write_bytes(payload)
    with pytest.raises(AppError) as exc:
        verify_file(path, sha256="0" * 64, size=len(payload))
    assert exc.value.code == ErrorCode.E15
    assert "SHA-256" in exc.value.message
    assert not path.exists()
    path.write_bytes(payload)
    with pytest.raises(AppError) as exc2:
        verify_file(path, sha256=digest, size=len(payload) + 8)
    assert exc2.value.code == ErrorCode.E15
    assert "大小" in exc2.value.message
    assert not path.exists()
    path.write_bytes(payload)
    with pytest.raises(AppError) as exc3:
        verify_file(path, sha256=None, size=len(payload))
    assert exc3.value.code == ErrorCode.E15
    assert "摘要" in exc3.value.message
    assert not path.exists()


def test_download_refuses_hash_mismatch_and_missing_digest(tmp_path):
    payload = b"installer-payload"
    digest = hashlib.sha256(payload).hexdigest()
    url = "https://github.com/zdj-L/redisdesk/releases/download/v1.0.1/RedisDesk-1.0.1-internal-win64-setup.exe"

    def http_ok(request_url, timeout):
        assert request_url == url
        return payload, {"content-length": str(len(payload))}

    good = UpdateOffer(
        version="1.0.1",
        current_version="1.0.0",
        notes="fixes",
        asset=UpdateAsset("RedisDesk-1.0.1-internal-win64-setup.exe", url, len(payload), digest),
    )
    dest = download_and_verify(good, tmp_path, http_get=http_ok)
    assert dest.read_bytes() == payload

    bad = UpdateOffer(
        version="1.0.1",
        current_version="1.0.0",
        notes="",
        asset=UpdateAsset("RedisDesk-1.0.1-internal-win64-setup.exe", url, len(payload), "0" * 64),
    )
    with pytest.raises(AppError) as exc:
        download_and_verify(bad, tmp_path, http_get=http_ok)
    assert exc.value.code == ErrorCode.E15
    assert not (tmp_path / "RedisDesk-1.0.1-internal-win64-setup.exe.part").exists()

    missing = UpdateOffer(
        version="1.0.1",
        current_version="1.0.0",
        notes="",
        asset=UpdateAsset("RedisDesk-1.0.1-internal-win64-setup.exe", url, len(payload), None),
    )
    with pytest.raises(AppError) as exc2:
        download_and_verify(missing, tmp_path, http_get=http_ok)
    assert exc2.value.code == ErrorCode.E15
    assert "摘要" in exc2.value.message


def test_check_for_update_uses_mocked_manifest_and_github(tmp_path, monkeypatch):
    monkeypatch.setenv("REDISDESK_APPDATA", str(tmp_path))
    payload = b"abc"
    digest = hashlib.sha256(payload).hexdigest()
    manifest = {
        "version": "1.0.1",
        "unsigned": True,
        "notes": "mock notes",
        "asset": {
            "name": "RedisDesk-1.0.1-internal-win64-setup.exe",
            "url": "https://github.com/zdj-L/redisdesk/releases/download/v1.0.1/RedisDesk-1.0.1-internal-win64-setup.exe",
            "size": 3,
            "sha256": digest,
        },
    }
    seen: list[str] = []

    def http_get(url, timeout):
        seen.append(url)
        if url.endswith("latest.json"):
            return json.dumps(manifest).encode(), {}
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setenv("REDISDESK_UPDATE_MANIFEST_URL", "https://github.com/zdj-L/redisdesk/releases/latest/download/latest.json")
    offer = check_for_update(current_version="1.0.0", http_get=http_get)
    assert offer is not None
    assert offer.version == "1.0.1"
    assert offer.asset.sha256 == digest
    assert check_for_update(current_version="1.0.1", http_get=http_get) is None
    monkeypatch.delenv("REDISDESK_UPDATE_MANIFEST_URL")

    release = {
        "tag_name": "v1.0.2",
        "body": "from api",
        "assets": [
            {
                "name": "SHA256SUMS.txt",
                "browser_download_url": "https://github.com/zdj-L/redisdesk/releases/download/v1.0.2/SHA256SUMS.txt",
            },
            {
                "name": "RedisDesk-1.0.2-internal-win64-setup.exe",
                "size": 3,
                "browser_download_url": "https://github.com/zdj-L/redisdesk/releases/download/v1.0.2/RedisDesk-1.0.2-internal-win64-setup.exe",
            },
        ],
    }

    def http_api(url, timeout):
        if url.endswith("/releases/latest"):
            return json.dumps(release).encode(), {}
        if url.endswith("SHA256SUMS.txt"):
            return f"{digest}  RedisDesk-1.0.2-internal-win64-setup.exe\n".encode(), {}
        raise AssertionError(url)

    monkeypatch.setenv("REDISDESK_UPDATE_API_URL", "https://api.github.com/repos/zdj-L/redisdesk/releases/latest")
    offer2 = check_for_update(current_version="1.0.0", http_get=http_api)
    assert offer2 is not None
    assert offer2.version == "1.0.2"
    assert offer2.asset.sha256 == digest
    assert offer2.notes == "from api"


def test_parse_sums_and_write_manifest(tmp_path):
    setup = tmp_path / "RedisDesk-1.0.1-internal-win64-setup.exe"
    setup.write_bytes(b"hello-setup")
    latest, sums = write_manifest(setup, version="1.0.1", notes="n")
    data = json.loads(latest.read_text(encoding="utf-8"))
    mapped = parse_sha256sums(sums.read_text(encoding="utf-8"))
    assert data["version"] == "1.0.1"
    assert data["unsigned"] is True
    assert mapped[setup.name] == data["asset"]["sha256"]
    assert data["asset"]["size"] == setup.stat().st_size


@pytest.fixture
def qapp_update(tmp_path, monkeypatch):
    monkeypatch.setenv("REDISDESK_APPDATA", str(tmp_path))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from redisdesk.app.ui.theme import apply_theme

    app = QApplication.instance() or QApplication(["redisdesk-update-tests"])
    apply_theme(app, "light")
    return app


def test_settings_has_update_entry_and_can_disable_auto_check(qapp_update, tmp_path):
    from PySide6.QtWidgets import QApplication
    from redisdesk.app.ui.pages.settings import SettingsPage

    page = SettingsPage()
    page.show()
    QApplication.processEvents()
    assert page.check_update_btn is not None
    assert page.check_update_btn.text() == "检查更新"
    assert "1.0.0" in page.update_version.text()
    assert page.update_auto.isChecked()
    page.update_auto.setChecked(False)
    QApplication.processEvents()
    from redisdesk.app.ui.theme import UiSettings

    assert UiSettings().get(PREF_AUTO_CHECK) is False
    page.close()


def test_manual_check_uses_worker_and_shows_status(qapp_update, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from redisdesk.app.ui.pages.settings import SettingsPage
    from redisdesk.app.ui.update_flow import UpdateAvailableDialog
    import redisdesk.infra.update as update_mod

    def fake_check(*, current_version=None, http_get=None):
        return UpdateOffer(
            version="1.0.1",
            current_version=current_version or "1.0.0",
            notes="mock",
            asset=UpdateAsset(
                "RedisDesk-1.0.1-internal-win64-setup.exe",
                "https://github.com/zdj-L/redisdesk/releases/download/v1.0.1/RedisDesk-1.0.1-internal-win64-setup.exe",
                12,
                "a" * 64,
            ),
        )

    monkeypatch.setattr(update_mod, "check_for_update", fake_check)
    monkeypatch.setattr("redisdesk.app.ui.update_flow.check_for_update", fake_check)

    page = SettingsPage()
    page.show()
    QApplication.processEvents()

    def fake_exec(self):
        self.choice = UpdateAvailableDialog.CHOICE_SNOOZE
        return 0

    monkeypatch.setattr(UpdateAvailableDialog, "exec", fake_exec)
    page.check_update_btn.click()
    QApplication.processEvents()
    if page.update_controller._worker is not None:
        assert page.update_controller._worker.wait(5000)
    QApplication.processEvents()
    assert "1.0.1" in page.update_status.text() or "稍后" in page.update_status.text() or "发现" in page.update_status.text()
    page.close()
