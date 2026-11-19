"""Opt-in packaged regression. Ephemeral loopback RESP and fake SSH only."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import socket
import socketserver
import tempfile
import threading
import traceback
from datetime import datetime, timezone, timedelta


def resp(value):
    if isinstance(value, int):
        return b":" + str(value).encode() + b"\r\n"
    if isinstance(value, (list, tuple)):
        return b"*" + str(len(value)).encode() + b"\r\n" + b"".join(resp(x) for x in value)
    if isinstance(value, str):
        value = value.encode()
    return b"$" + str(len(value)).encode() + b"\r\n" + value + b"\r\n"


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        while line := self.rfile.readline():
            if not line.startswith(b"*"):
                return
            parts = []
            for _ in range(int(line[1:])):
                size = int(self.rfile.readline()[1:])
                parts.append(self.rfile.read(size))
                self.rfile.read(2)
            self.server.commands.append(parts)
            cmd = parts[0].upper()
            if cmd == b"CLIENT":
                out = b"-ERR unsupported CLIENT\r\n"
            elif cmd in {b"SELECT", b"AUTH"}:
                out = b"+OK\r\n"
            elif cmd == b"PING":
                out = b"+PONG\r\n"
            elif cmd == b"INFO":
                out = resp("# Server\r\nredis_version:3.2.100\r\nos:Windows\r\nuptime_in_seconds:90000\r\nredis_mode:standalone\r\nrole:master\r\n# Keyspace\r\ndb0:keys=1,expires=0,avg_ttl=0\r\n")
            elif cmd == b"SCAN":
                out = resp([b"0", [b"smoke:key"]])
            elif cmd == b"DBSIZE":
                out = resp(1)
            elif cmd == b"TYPE":
                out = resp("string")
            elif cmd == b"TTL":
                out = resp(-1)
            elif cmd == b"STRLEN":
                out = resp(5)
            elif cmd in {b"GET", b"GETRANGE"}:
                out = resp("value")
            else:
                out = b"-NOPERM smoke read-only\r\n"
            self.wfile.write(out)
            self.wfile.flush()


class Channel:
    def __init__(self, sock): self.sock = sock
    def fileno(self): return self.sock.fileno()
    def recv(self, n): return self.sock.recv(n)
    def sendall(self, data): return self.sock.sendall(data)
    def recv_ready(self): return True
    def settimeout(self, value): self.sock.settimeout(value)
    def close(self): self.sock.close()


class FakeTransport:
    def __init__(self, port): self.port = port
    def is_active(self): return True
    def set_keepalive(self, _): pass
    def open_channel(self, kind, dest, origin):
        assert kind == 'direct-tcpip'
        # Ignore requested host: this fake can only connect to our ephemeral mock.
        return Channel(socket.create_connection(('127.0.0.1', self.port), timeout=3))


class FakeSSH:
    def __init__(self, port): self.transport = FakeTransport(port)
    def get_transport(self): return self.transport


def run(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument('--smoke-test', action='store_true')
    parser.add_argument('--smoke-report', required=True)
    args = parser.parse_args(argv)
    report_path = Path(args.smoke_report).resolve()
    data_dir = Path(tempfile.mkdtemp(prefix='RedisDesk-packaged-smoke-'))
    os.environ['REDISDESK_APPDATA'] = str(data_dir)
    report = {'ok': False, 'data_dir': str(data_dir), 'checks': [], 'scope': 'local ephemeral RESP / fake SSH; no real Redis or bastion',
              'timestamp_shanghai': datetime.now(timezone(timedelta(hours=8))).isoformat()}

    def save():
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')

    def check(window, app, session, service, console):
        server = None
        runtime = None
        try:
            import paramiko
            import cryptography
            import redis
            from PySide6.QtCore import QLibraryInfo
            from redisdesk.infra.crypto import dpapi_protect, dpapi_unprotect
            from redisdesk.infra.paths import app_data_dir
            from redisdesk.app.ui.theme import UiSettings
            from redisdesk.app.services.workspace_session import WorkspaceSession
            from redisdesk.core.model import ConnectionProfile, Endpoint, TlsConfig, SshConfig
            from redisdesk.infra.tunnel import SshTunnel
            report['qt_platform'] = app.platformName()
            report['qt_plugins'] = QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)
            assert window.isVisible()
            assert app_data_dir().resolve() == data_dir.resolve()
            report['checks'].append('entry_mainwindow_and_isolated_data')
            plain = b'RedisDesk disposable DPAPI smoke fixture'
            assert dpapi_unprotect(dpapi_protect(plain)) == plain
            report['checks'].append('dpapi_roundtrip_current_windows_user')
            class Server(socketserver.ThreadingTCPServer):
                allow_reuse_address = True
                daemon_threads = True
            server = Server(('127.0.0.1', 0), Handler)
            server.commands = []
            threading.Thread(target=server.serve_forever, daemon=True).start()
            port = server.server_address[1]
            assert port != 6379
            profile = ConnectionProfile(id='packaged-smoke', name='Disposable mock only', topology='direct', transport='tcp',
                endpoint=Endpoint('127.0.0.1', port), env='dev', protection='confirm',
                tls=TlsConfig(enabled=False, allow_plaintext=True, plaintext_reason='isolated packaging smoke'))
            service.save(session, profile)
            service.grant(session, profile.id, session.role)
            report['checks'].append('connection_save_temporary_store')
            runtime = WorkspaceSession(service, session, profile)
            runtime.connect()
            result = runtime.scan_next()
            assert result['total'] == 1
            assert runtime.read_value(b'smoke:key')
            report['checks'].append('directory_scan_and_value_read')
            command = runtime.execute_confirmed(console, 'PING')
            assert command.ok
            report['checks'].append('console_ping')
            info = service.fetch_info(session, profile)
            assert info['redis_version'] == '3.2.100'
            window.diag_page.show_info(info)
            assert len(window.diag_page.cards) == 7
            assert all(title in window.diag_page.sections for title in window.diag_page.cards)
            assert len(window.diag_page.sections) == 9
            report['checks'].append('info_and_diagnostics_seven_sections')
            report['checks'].append('diagnostics_slowlog_and_client_list_sections')
            runtime.close()
            runtime = None
            with_tunnel = SshTunnel(SshConfig(host='mock.invalid', username='smoke', auth_mode='password'),
                                    'mock-redis.invalid', port, client=FakeSSH(port))
            try:
                local_port = with_tunnel.start()
                client = redis.Redis(host='127.0.0.1', port=local_port, socket_timeout=3)
                try: assert client.ping()
                finally: client.close()
            finally: with_tunnel.close()
            report['checks'].append('ssh_forwarding_with_injected_fake_transport')
            settings = UiSettings()
            settings.set('theme', 'light')
            assert UiSettings().get('theme') == 'light'
            report['checks'].append('settings_save_reload')
            allowed = {b'CLIENT', b'SELECT', b'AUTH', b'PING', b'INFO', b'SCAN', b'DBSIZE', b'TYPE', b'TTL', b'STRLEN', b'GET', b'GETRANGE'}
            assert all(p[0].upper() in allowed for p in server.commands)
            report['wire_commands'] = sorted({p[0].decode() for p in server.commands})
            report['dependencies'] = {'redis': redis.__version__, 'paramiko': paramiko.__version__, 'cryptography': cryptography.__version__}
            report['ok'] = True
        except Exception:
            report['error'] = traceback.format_exc()
        finally:
            if runtime is not None: runtime.close()
            if server is not None:
                server.shutdown()
                server.server_close()
            save()
            app.exit(0 if report['ok'] else 1)

    try:
        from redisdesk.app.main import _run_ui
        from redisdesk.infra.log import setup_logging
        setup_logging()
        result = _run_ui(data_dir, startup_hook=check)
        return result
    except Exception:
        report['error'] = traceback.format_exc()
        save()
        return 1
