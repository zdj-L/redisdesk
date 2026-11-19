"""Wire-level compatibility tests using redis-py against a local RESP2 test server.
All credentials here are synthetic. No user Redis data or stored secrets are read.
"""
from contextlib import contextmanager
import socketserver
import threading

import pytest
import redis

from redisdesk.adapters.gateway import open_gateway
from redisdesk.adapters.direct import DirectGateway
from redisdesk.app.services.connection_service import ConnectionService
from redisdesk.core.errors import AppError
from redisdesk.core.model import ConnectionProfile, Endpoint, TlsConfig


class RespServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        authenticated = False
        while True:
            line = self.rfile.readline()
            if not line:
                return
            assert line.startswith(b'*')
            parts = []
            for _ in range(int(line[1:])):
                size = int(self.rfile.readline()[1:])
                parts.append(self.rfile.read(size))
                assert self.rfile.read(2) == b'\r\n'
            self.server.commands.append(parts)
            command = parts[0].upper()
            if command == b'HELLO':
                reply = b"-ERR unknown command 'HELLO'\r\n"
            elif command == b'AUTH':
                expected = [b'fixture-password'] if self.server.username is None else [self.server.username, b'fixture-password']
                authenticated = parts[1:] == expected
                reply = b'+OK\r\n' if authenticated else b'-WRONGPASS invalid username-password pair\r\n'
            elif not authenticated:
                reply = b'-NOAUTH Authentication required.\r\n'
            elif command == b'CLIENT':
                reply = b"-ERR unknown command 'CLIENT'\r\n"
            elif command == b'SELECT':
                reply = b'+OK\r\n'
            elif command == b'PING':
                reply = b'+PONG\r\n'
            elif command == b'INFO':
                data = b'# Server\r\nredis_version:' + self.server.version + b'\r\n'
                reply = b'$' + str(len(data)).encode() + b'\r\n' + data + b'\r\n'
            elif command == b'ACL':
                if self.server.version.startswith(b'3.'):
                    reply = b"-ERR unknown command 'ACL'\r\n"
                elif parts[1].upper() == b'WHOAMI':
                    name = self.server.username or b'default'
                    reply = b'$' + str(len(name)).encode() + b'\r\n' + name + b'\r\n'
                else:
                    reply = b'*0\r\n'
            else:
                reply = b'-NOPERM command denied\r\n'
            self.wfile.write(reply)
            self.wfile.flush()


@contextmanager
def resp_service(version=b'3.2.100', username=None):
    with RespServer(('127.0.0.1', 0), Handler) as server:
        server.version = version
        server.username = username
        server.commands = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=3)


def profile_for(server, username=None):
    return ConnectionProfile(
        id='wire-test', name='wire-test',
        endpoint=Endpoint('127.0.0.1', server.server_address[1]),
        username=username, db=2,
        tls=TlsConfig(enabled=False, allow_plaintext=True, plaintext_reason='本机兼容性自动化测试专用'),
    )


@pytest.mark.parametrize('version, username', [(b'3.2.100', None), (b'7.2.0', None), (b'7.2.0', 'reader')])
def test_gateway_and_test_probe_use_resp2_without_hello(version, username):
    with resp_service(version, username.encode() if username else None) as server:
        profile = profile_for(server, username)
        password = memoryview(b'fixture-password')
        # Real connection factory, capability discovery and PING.
        gw = open_gateway(profile, password=password)
        try:
            assert gw._client.connection_pool.connection_kwargs['protocol'] == 2
            assert gw.capabilities.server_version == version.decode()
            gw.ping()
        finally:
            gw.close()
        # Same service path used by draft and saved-profile test actions.
        service = ConnectionService.__new__(ConnectionService)
        result = service._open_and_probe(profile, password)
        assert result.version == version.decode()
        commands = server.commands
        assert not any(parts[0] == b'HELLO' for parts in commands)
        auth = [parts[1:] for parts in commands if parts[0] == b'AUTH']
        expected = [username.encode(), b'fixture-password'] if username else [b'fixture-password']
        assert auth and all(args == expected for args in auth)
        assert [b'SELECT', b'2'] in commands
        assert all(parts[0] in {b'AUTH', b'CLIENT', b'SELECT', b'PING', b'INFO', b'ACL'} for parts in commands)
        if version.startswith(b'3.'):
            assert result.warnings  # unsupported ACL is a warning, not a failed handshake


@pytest.mark.parametrize('password', [b'wrong-fixture-password', None])
def test_authentication_failure_is_not_swallowed_or_retried_anonymously(password):
    with resp_service() as server:
        profile = profile_for(server)
        service = ConnectionService.__new__(ConnectionService)
        with pytest.raises(AppError) as err:
            service._open_and_probe(profile, memoryview(password) if password else None)
        assert err.value.code == 'E02'
        assert not any(parts[0] == b'HELLO' for parts in server.commands)
        # Must fail during capability discovery, not claim successful PING/probe.
        assert not any(parts[0] == b'ACL' for parts in server.commands)


def test_tls_configuration_is_not_downgraded(monkeypatch):
    captured = {}
    def client(**kwargs):
        captured.update(kwargs)
        return object()
    monkeypatch.setattr(redis, 'Redis', client)
    profile = ConnectionProfile(id='tls', name='tls', endpoint=Endpoint('localhost', 6380))
    DirectGateway(profile, password=memoryview(b'fixture-password'))
    assert captured['protocol'] == 2
    assert captured['ssl'] is True
    assert captured['ssl_cert_reqs'] == 'required'
    assert captured['ssl_check_hostname'] is True
    assert captured['username'] is None
    assert captured['password'] == 'fixture-password'


@pytest.mark.parametrize('error, code', [
    (redis.exceptions.ResponseError('NOPERM command denied'), 'E02'),
    (redis.exceptions.TimeoutError('timed out'), 'E03'),
    (redis.exceptions.ConnectionError('connection refused'), 'E01'),
])
def test_protocol_compatibility_does_not_hide_other_errors(error, code):
    class FailingClient:
        def ping(self):
            raise error
    profile = ConnectionProfile(id='errors', name='errors', endpoint=Endpoint('localhost', 6379))
    gw = DirectGateway(profile, client=FailingClient())
    with pytest.raises(AppError) as exc:
        gw.ping()
    assert exc.value.code == code
