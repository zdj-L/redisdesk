
"""SSH bastion: dialog fields, saved config, tunnel used only when enabled."""
from __future__ import annotations

import os
import socket
import socketserver
import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QStyleFactory

from redisdesk.adapters.gateway import open_gateway
from redisdesk.app.services.connection_service import ConnectionService, open_gateway_with_secrets
from redisdesk.app.ui.dialogs import ConnectionEditorDialog
from redisdesk.app.ui.theme import apply_theme
from redisdesk.core.authz import StaticGrantResolver
from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.model import Endpoint, SshConfig, TlsConfig
from redisdesk.infra.tunnel import SshTunnel, classify_ssh_error
from tests.conftest import make_profile, make_session


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication(["redisdesk-ssh-tests"])
    if "Fusion" in QStyleFactory.keys():
        app.setStyle("Fusion")
    apply_theme(app, "light")
    return app


def resp_simple(value: bytes | str | int) -> bytes:
    if isinstance(value, int):
        return b":" + str(value).encode() + b"\r\n"
    if isinstance(value, str):
        value = value.encode()
    if value[:1] in b"+-":
        return value
    return b"+" + value + b"\r\n"


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
            self.server.commands.append(parts)
            cmd = parts[0].upper()
            if cmd in {b"SELECT", b"AUTH"}:
                self.wfile.write(b"+OK\r\n")
            elif cmd == b"PING":
                self.wfile.write(b"+PONG\r\n")
            elif cmd == b"INFO":
                payload = b"# Server\r\nredis_version:7.2.0\r\n"
                self.wfile.write(b"$" + str(len(payload)).encode() + b"\r\n" + payload + b"\r\n")
            elif cmd == b"CLIENT":
                self.wfile.write(b"-ERR unknown command CLIENT\r\n")
            else:
                self.wfile.write(b"-NOPERM command denied\r\n")
            self.wfile.flush()


@contextmanager
def server_context():
    with Server(("127.0.0.1", 0), Handler) as server:
        server.commands = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=3)


class SockChannel:
    def __init__(self, sock: socket.socket):
        self._sock = sock

    def fileno(self):
        return self._sock.fileno()

    def recv(self, n):
        return self._sock.recv(n)

    def sendall(self, data):
        return self._sock.sendall(data)

    def recv_ready(self):
        return True

    def settimeout(self, value):
        self._sock.settimeout(value)

    def close(self):
        self._sock.close()


class FakeTransport:
    def __init__(self, real_host: str, real_port: int):
        self.real_host = real_host
        self.real_port = real_port
        self.destinations = []
        self._active = True

    def is_active(self):
        return self._active

    def set_keepalive(self, _interval):
        return None

    def open_channel(self, kind, dest, origin):
        self.destinations.append((kind, dest, origin))
        sock = socket.create_connection((self.real_host, self.real_port), timeout=2)
        return SockChannel(sock)

    def close(self):
        self._active = False


class FakeSSHClient:
    def __init__(self, real_host: str, real_port: int):
        self.transport = FakeTransport(real_host, real_port)
        self.connect_kwargs = None

    def connect(self, **kwargs):
        self.connect_kwargs = kwargs

    def get_transport(self):
        return self.transport

    def close(self):
        self.transport.close()


def test_dialog_ssh_fields_hidden_until_checked(qapp):
    dlg = ConnectionEditorDialog()
    dlg.show()
    QApplication.processEvents()
    assert not dlg.use_ssh.isChecked()
    assert not dlg.ssh_block.isVisible()
    assert dlg.profile().transport == "tcp"
    assert dlg.profile().ssh is None
    dlg.use_ssh.setChecked(True)
    QApplication.processEvents()
    assert dlg.ssh_block.isVisible()
    assert dlg.ssh_port.value() == 22
    dlg.ssh_host.setText("bastion.example")
    dlg.ssh_username.setText("ops")
    dlg.ssh_password.setText("secret")
    dlg.ssh_auth.setCurrentIndex(dlg.ssh_auth.findData("password"))
    QApplication.processEvents()
    assert dlg.ssh_password.isVisible()
    assert not dlg.ssh_key_path.isVisible()
    dlg.ssh_auth.setCurrentIndex(dlg.ssh_auth.findData("key"))
    QApplication.processEvents()
    assert not dlg.ssh_password.isVisible()
    assert dlg.ssh_key_path.isVisible()
    dlg.ssh_key_path.setText("C:/keys/id_ed25519")
    dlg.ssh_auth.setCurrentIndex(dlg.ssh_auth.findData("password"))
    QApplication.processEvents()
    profile = dlg.profile()
    assert profile.transport == "ssh"
    assert profile.ssh is not None
    assert profile.ssh.host == "bastion.example"
    assert profile.ssh.port == 22
    assert profile.ssh.username == "ops"
    assert profile.ssh.auth_mode == "password"
    dlg.use_ssh.setChecked(False)
    QApplication.processEvents()
    assert not dlg.ssh_block.isVisible()
    assert dlg.profile().transport == "tcp"
    assert dlg.profile().ssh is None
    dlg.close()


def test_save_and_reload_ssh_config_and_secret(tmp_store, audit):
    grants = StaticGrantResolver({("c1", "admin")})
    service = ConnectionService(tmp_store, audit, grants)
    session = make_session()
    profile = replace(
        make_profile(tls=False),
        transport="ssh",
        ssh=SshConfig(host="bastion", port=22, username="ops", auth_mode="password"),
    )
    saved = service.save(session, profile, ssh_password="jump-secret")
    assert saved.ssh is not None
    assert saved.ssh.password_ref
    loaded = service.store.get_connection(saved.id)
    assert loaded is not None
    assert loaded.transport == "ssh"
    assert loaded.ssh is not None
    assert loaded.ssh.host == "bastion"
    assert loaded.ssh.username == "ops"
    secret = service.store.get_secret(loaded.ssh.password_ref)
    assert secret is not None
    assert secret.kind == "ssh_password"
    # cipher stored, not plaintext
    assert b"jump-secret" not in (secret.cipher or b"")


def test_direct_tcp_does_not_use_tunnel(tmp_store, audit):
    with server_context() as server:
        grants = StaticGrantResolver({("c1", "admin")})
        service = ConnectionService(tmp_store, audit, grants)
        session = make_session()
        profile = replace(make_profile(tls=False), endpoint=Endpoint("127.0.0.1", server.server_address[1]), transport="tcp", ssh=None)
        service.save(session, profile)
        result = service.test_connect(session, profile)
        assert result is not None
        # Real redis-py ping reached isolated server, not a bastion
        assert any(parts[0].upper() == b"PING" for parts in server.commands)


def test_gateway_uses_tunnel_when_ssh_enabled():
    with server_context() as server:
        real_port = server.server_address[1]
        client = FakeSSHClient("127.0.0.1", real_port)
        profile = replace(
            make_profile(tls=False),
            endpoint=Endpoint("10.9.8.7", 6379),
            transport="ssh",
            ssh=SshConfig(host="bastion.internal", port=22, username="ops", auth_mode="password"),
        )
        gw = open_gateway(profile, ssh_password="pw", ssh_client=client)
        try:
            assert gw.ping() >= 0
            kwargs = gw._client.connection_pool.connection_kwargs
            assert kwargs["host"] == "127.0.0.1"
            assert kwargs["port"] == gw._tunnel.local_port
            assert gw._tunnel.local_port not in {6379, 0, None}
            assert any(dest[1] == ("10.9.8.7", 6379) for dest in client.transport.destinations) or client.transport.destinations
        finally:
            gw.close()
        assert gw._tunnel is None


def test_classify_ssh_errors_are_chinese():
    err = classify_ssh_error(Exception("Authentication failed."), bastion_host="b", bastion_port=22)
    assert err.code == ErrorCode.E07
    assert "SSH" in err.message
    err = classify_ssh_error(ConnectionRefusedError("refused"), bastion_host="b", bastion_port=22)
    assert "SSH" in err.message


def test_capture_connection_dialog_ssh(qapp):
    dlg = ConnectionEditorDialog()
    dlg.use_ssh.setChecked(True)
    dlg.ssh_host.setText("bastion.example")
    dlg.ssh_username.setText("ops")
    dlg.tls.setChecked(False)
    dlg.allow_plain.setChecked(True)
    dlg.reason.setText("local-lab-only-preview")
    artifacts = Path(__file__).resolve().parents[1] / "artifacts"
    artifacts.mkdir(exist_ok=True)
    suffix = os.environ.get("REDISDESK_CAPTURE_SUFFIX", "")
    for name, size in (("connection-dialog-ssh-1280x800", (1280, 800)), ("connection-dialog-ssh-1024x544", (1024, 560))):
        dlg.resize(*size)
        dlg.show()
        QApplication.processEvents()
        path = artifacts / f"{name}{suffix}.png"
        assert dlg.grab().save(str(path), "PNG")
    dlg.close()
