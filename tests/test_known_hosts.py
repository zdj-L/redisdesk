"""SSH known_hosts store, verification, and settings UI. Isolated appdata only."""
from __future__ import annotations

import os
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import paramiko
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QStyleFactory

from redisdesk.app.ui.host_key_prompt import HostKeyConfirmDialog, register_unknown_host_key
from redisdesk.app.ui.pages.settings import SettingsPage
from redisdesk.app.ui.theme import apply_theme
from redisdesk.core.model import SshConfig
from redisdesk.infra.known_hosts import (
    HostKeyMismatchError,
    KnownHostsStore,
    UnknownHostKeyError,
    fingerprint_sha256,
    system_known_hosts_path,
)
from redisdesk.infra.paths import known_hosts_path
from redisdesk.infra.tunnel import SshTunnel


def _user_known_hosts() -> Path:
    return Path.home() / ".ssh" / "known_hosts"


def _snapshot_user_known_hosts():
    path = _user_known_hosts()
    if not path.exists():
        return False, None, None
    stat = path.stat()
    return True, stat.st_mtime_ns, path.read_bytes()


def _assert_user_known_hosts_untouched(before) -> None:
    exists, mtime, data = before
    path = _user_known_hosts()
    if not exists:
        assert not path.exists(), f"must not create {path}"
        return
    stat = path.stat()
    assert stat.st_mtime_ns == mtime
    assert path.read_bytes() == data


@pytest.fixture
def isolated_appdata(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setenv("REDISDESK_APPDATA", str(app))
    monkeypatch.setenv("REDISDESK_SYSTEM_KNOWN_HOSTS", "")
    return app


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication(["redisdesk-known-hosts-tests"])
    if "Fusion" in QStyleFactory.keys():
        app.setStyle("Fusion")
    apply_theme(app, "light")
    return app


def _rsa():
    return paramiko.RSAKey.generate(1024)


class _StubServer(paramiko.ServerInterface):
    def __init__(self, username: str = "ops", password: str = "pw") -> None:
        self.username = username
        self.password = password

    def check_channel_request(self, kind, chanid):  # noqa: ANN001
        return paramiko.OPEN_SUCCEEDED

    def check_auth_password(self, username, password):  # noqa: ANN001
        if username == self.username and password == self.password:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):  # noqa: ANN001
        return "password"


@contextmanager
def mock_ssh_server(host_key, *, username="ops", password="pw"):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    port = sock.getsockname()[1]
    stop = threading.Event()

    def run() -> None:
        sock.settimeout(0.4)
        while not stop.is_set():
            try:
                conn, _addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            transport = None
            try:
                transport = paramiko.Transport(conn)
                transport.add_server_key(host_key)
                transport.start_server(server=_StubServer(username, password))
                transport.accept(2)
                deadline = time.time() + 4
                while time.time() < deadline and transport.is_active() and not stop.is_set():
                    time.sleep(0.05)
            except Exception:
                pass
            finally:
                if transport is not None:
                    try:
                        transport.close()
                    except Exception:
                        pass

    thread = threading.Thread(target=run, name="redisdesk-mock-ssh", daemon=True)
    thread.start()
    try:
        yield port
    finally:
        stop.set()
        try:
            sock.close()
        except Exception:
            pass
        thread.join(timeout=3)


def _connect(port: int, *, policy: str, store: KnownHostsStore, password="pw", pinned=None):
    tunnel = SshTunnel(
        SshConfig(
            host="127.0.0.1",
            port=port,
            username="ops",
            auth_mode="password",
            known_hosts_policy=policy,
            host_key_sha256=pinned,
        ),
        "10.0.0.1",
        6379,
        password=password,
        connect_timeout_s=4,
        known_hosts_store=store,
    )
    try:
        client = tunnel._connect_client()
        try:
            return client
        finally:
            client.close()
    finally:
        tunnel.close()


def test_paths_use_appdata_and_can_disable_system_file(isolated_appdata, monkeypatch):
    path = known_hosts_path()
    assert path.parent == isolated_appdata
    assert path.name == "known_hosts_redisdesk"
    assert system_known_hosts_path() is None
    monkeypatch.setenv("REDISDESK_SYSTEM_KNOWN_HOSTS", str(isolated_appdata / "sys_known_hosts"))
    assert system_known_hosts_path() == isolated_appdata / "sys_known_hosts"
    monkeypatch.delenv("REDISDESK_SYSTEM_KNOWN_HOSTS")
    default = system_known_hosts_path()
    assert default == Path.home() / ".ssh" / "known_hosts"


def test_store_list_add_remove_and_does_not_touch_user_file(isolated_appdata):
    before = _snapshot_user_known_hosts()
    store = KnownHostsStore()
    assert store.path.parent == isolated_appdata
    assert store.list_entries() == []
    key = _rsa()
    entry = store.add("bastion.example", 22, key)
    assert entry.host == "bastion.example"
    assert entry.port == 22
    assert entry.key_type == "ssh-rsa"
    assert entry.fingerprint == fingerprint_sha256(key)
    listed = store.list_entries()
    assert len(listed) == 1
    assert listed[0].fingerprint == entry.fingerprint
    # port 2222 uses OpenSSH bracket notation
    store.add("bastion.example", 2222, key)
    names = {(item.host, item.port, item.key_type) for item in store.list_entries()}
    assert ("bastion.example", 22, "ssh-rsa") in names
    assert ("bastion.example", 2222, "ssh-rsa") in names
    removed = store.remove("bastion.example", 22, "ssh-rsa")
    assert removed == 1
    left = store.list_entries()
    assert len(left) == 1
    assert left[0].port == 2222
    assert store.path.exists()
    assert store.path.read_text(encoding="utf-8")
    _assert_user_known_hosts_untouched(before)
    assert _user_known_hosts() != store.path


def test_accept_unknown_then_connect_and_mismatch_reject(isolated_appdata):
    before = _snapshot_user_known_hosts()
    store = KnownHostsStore()
    host_key = _rsa()
    other_key = _rsa()
    with mock_ssh_server(host_key) as port:
        with pytest.raises(UnknownHostKeyError) as raised:
            _connect(port, policy="tofu", store=store)
        err = raised.value
        assert err.host == "127.0.0.1"
        assert err.port == port
        assert err.policy == "tofu"
        assert err.fingerprint == fingerprint_sha256(host_key)
        assert "SHA256:" in err.fingerprint
        # strict does not add
        with pytest.raises(UnknownHostKeyError) as strict_err:
            _connect(port, policy="strict", store=store)
        assert strict_err.value.policy == "strict"
        assert store.list_entries() == []
        register_unknown_host_key(err)
        entries = store.list_entries()
        assert len(entries) == 1
        assert entries[0].fingerprint == err.fingerprint
        # accepted key can connect
        client = _connect(port, policy="tofu", store=store)
        assert client is not None
    # mismatch: recorded key A, server presents B
    with mock_ssh_server(other_key) as port2:
        store.add("127.0.0.1", port2, host_key)
        with pytest.raises(HostKeyMismatchError) as mismatch:
            _connect(port2, policy="tofu", store=store)
        info = mismatch.value
        assert info.expected_fingerprint == fingerprint_sha256(host_key)
        assert info.actual_fingerprint == fingerprint_sha256(other_key)
        assert "\u5ffd\u7565" in info.message
        # pinned fingerprint also rejects even if store has the new key
        store.add("127.0.0.1", port2, other_key)
        with pytest.raises(HostKeyMismatchError):
            _connect(port2, policy="tofu", store=store, pinned=fingerprint_sha256(host_key))
    _assert_user_known_hosts_untouched(before)


def test_system_known_hosts_are_read_only(isolated_appdata, monkeypatch):
    before = _snapshot_user_known_hosts()
    system = isolated_appdata / "readonly_system_known_hosts"
    key = _rsa()
    sys_store = KnownHostsStore(system)
    sys_store.add("127.0.0.1", 22, key)
    original = system.read_bytes()
    monkeypatch.setenv("REDISDESK_SYSTEM_KNOWN_HOSTS", str(system))
    store = KnownHostsStore()
    with mock_ssh_server(key) as port:
        # key only exists in the read-only system file under host:22, not this ephemeral port.
        # Put the same key in the system file with the actual port so strict can trust it.
        sys_store.add("127.0.0.1", port, key)
        original = system.read_bytes()
        client = _connect(port, policy="strict", store=store)
        assert client is not None
        # accepting a different first-seen key writes only the app file
        other = _rsa()
    # system file must stay identical after app-store write
    store.add("other.example", 22, other)
    assert system.read_bytes() == original
    assert store.path.exists()
    assert "other.example" in store.path.read_text(encoding="utf-8")
    assert "other.example" not in system.read_text(encoding="utf-8")
    _assert_user_known_hosts_untouched(before)


def test_settings_lists_and_delete_requires_confirmation(qapp, isolated_appdata, monkeypatch):
    before = _snapshot_user_known_hosts()
    store = KnownHostsStore()
    key = _rsa()
    entry = store.add("jump.internal", 22, key)
    page = SettingsPage()
    page.show()
    QApplication.processEvents()
    deadline = time.time() + 3
    while time.time() < deadline and page.known_table.rowCount() != 1:
        QApplication.processEvents()
        time.sleep(0.02)
    assert page.known_table.rowCount() == 1
    assert page.known_table.item(0, 0).text() == "jump.internal"
    assert page.known_table.item(0, 1).text() == "22"
    assert "SHA256:" in page.known_table.item(0, 3).text()
    assert str(store.path) in page.known_path.text()
    page.known_table.selectRow(0)
    QApplication.processEvents()
    # cancel keeps the entry
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.No))
    page._delete_selected_known_host()
    QApplication.processEvents()
    assert len(KnownHostsStore().list_entries()) == 1
    # confirm removes it
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes))
    page._delete_selected_known_host()
    deadline = time.time() + 4
    while time.time() < deadline and (KnownHostsStore().list_entries() or page.known_table.rowCount()):
        QApplication.processEvents()
        time.sleep(0.02)
    QApplication.processEvents()
    assert KnownHostsStore().list_entries() == []
    assert page.known_table.rowCount() == 0
    page.close()
    _assert_user_known_hosts_untouched(before)
    assert entry.fingerprint


def test_host_key_confirm_dialog_has_no_ignore_button(qapp, isolated_appdata):
    key = _rsa()
    err = UnknownHostKeyError(host="bastion.example", port=22, key=key, policy="tofu")
    dialog = HostKeyConfirmDialog(err)
    dialog.show()
    QApplication.processEvents()
    texts = [w.text() for w in dialog.findChildren(type(dialog.title_label)) if hasattr(w, "text")]
    joined = "\n".join(texts)
    assert err.fingerprint in joined
    assert "\u4fe1\u4efb\u5e76\u767b\u8bb0" in dialog.ok_button.text()
    assert "\u5ffd\u7565" not in dialog.ok_button.text()
    assert "\u5ffd\u7565\u5e76\u7ee7\u7eed" not in dialog.ok_button.text()
    assert "\u4e0d\u63d0\u4f9b" in joined and "\u5ffd\u7565\u5e76\u7ee7\u7eed" in joined
    dialog.close()


def test_editor_exposes_host_key_policy(qapp, isolated_appdata):
    from redisdesk.app.ui.dialogs import ConnectionEditorDialog

    dlg = ConnectionEditorDialog()
    dlg.use_ssh.setChecked(True)
    QApplication.processEvents()
    assert dlg.ssh_known_hosts_policy.currentData() == "tofu"
    assert dlg.profile().ssh is not None
    assert dlg.profile().ssh.known_hosts_policy == "tofu"
    dlg.ssh_known_hosts_policy.setCurrentIndex(dlg.ssh_known_hosts_policy.findData("strict"))
    dlg.ssh_host.setText("bastion")
    dlg.ssh_username.setText("ops")
    dlg.ssh_password.setText("pw")
    dlg.ssh_auth.setCurrentIndex(dlg.ssh_auth.findData("password"))
    QApplication.processEvents()
    assert dlg.profile().ssh.known_hosts_policy == "strict"
    dlg.close()
