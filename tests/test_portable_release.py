"""Release isolation and entrypoint contracts; no live services."""
import importlib.util
import os
from pathlib import Path


def load_entry():
    path = Path(__file__).resolve().parents[1] / "scripts" / "portable_entry.py"
    spec = importlib.util.spec_from_file_location("portable_entry_contract", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_portable_default_does_not_use_development_data(monkeypatch, tmp_path):
    monkeypatch.delenv("REDISDESK_APPDATA", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from redisdesk.infra import paths as paths_mod
    paths_mod.unlock_app_data_dir()
    paths_mod.set_packaged_default_data_dir(None)
    paths_mod.set_app_root_override(tmp_path / "app-root")
    (tmp_path / "app-root").mkdir()
    load_entry().configure_portable_data()
    assert "REDISDESK_APPDATA" not in os.environ
    actual = paths_mod.resolve_app_data_dir()
    assert actual == tmp_path / "RedisDesk-Internal" / "1.0.0"
    assert actual != tmp_path / "RedisDesk"
    assert paths_mod.default_system_data_dir() == tmp_path / "RedisDesk"


def test_portable_explicit_data_path_is_respected(monkeypatch, tmp_path):
    chosen = tmp_path / "temporary-test-only"
    monkeypatch.setenv("REDISDESK_APPDATA", str(chosen))
    from redisdesk.infra import paths as paths_mod
    paths_mod.unlock_app_data_dir()
    load_entry().configure_portable_data()
    assert Path(os.environ["REDISDESK_APPDATA"]) == chosen
    assert paths_mod.resolve_app_data_dir() == chosen


def test_smoke_fixture_accepts_only_generated_loopback_server():
    from redisdesk.app.packaging_smoke import FakeTransport
    transport = FakeTransport(12345)
    assert transport.port != 6379
    assert transport.is_active()
