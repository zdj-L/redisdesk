"""Redis Sentinel: create/test/use against isolated mock RESP only. Never 6379/26379."""
from __future__ import annotations

import os
import socket
import socketserver
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from redisdesk.adapters.gateway import open_gateway
from redisdesk.adapters.tls import require_tls_or_exception
from redisdesk.app.services.connection_service import ConnectionService
from redisdesk.app.services.console_service import ConsoleService
from redisdesk.app.services.workspace_session import WorkspaceSession
from redisdesk.core.authz import Action, StaticGrantResolver, check
from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.model import ConnectionProfile, Endpoint, SentinelConfig, SshConfig, TlsConfig
from redisdesk.core.policy import PolicyEngine
from tests.conftest import make_session

FORBIDDEN_PORTS = {6379, 26379}


class RespError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _encode(value: object) -> bytes:
    if isinstance(value, RespError):
        return b"-" + value.message.encode("utf-8") + b"\r\n"
    if value is None:
        return b"$-1\r\n"
    if value is True or value == "OK":
        return b"+OK\r\n"
    if value == "PONG":
        return b"+PONG\r\n"
    if isinstance(value, bool):
        return b":1\r\n" if value else b":0\r\n"
    if isinstance(value, int) and not isinstance(value, bool):
        return f":{value}\r\n".encode("ascii")
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return b"$" + str(len(raw)).encode("ascii") + b"\r\n" + raw + b"\r\n"
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        return b"$" + str(len(raw)).encode("ascii") + b"\r\n" + raw + b"\r\n"
    if isinstance(value, (list, tuple)):
        out = f"*{len(value)}\r\n".encode("ascii")
        for item in value:
            out += _encode(item)
        return out
    raw = str(value).encode("utf-8")
    return b"$" + str(len(raw)).encode("ascii") + b"\r\n" + raw + b"\r\n"


def _read_command(rfile):
    line = rfile.readline()
    if not line:
        return None
    if not line.startswith(b"*"):
        return None
    count = int(line[1:])
    parts: list[bytes] = []
    for _ in range(count):
        size = int(rfile.readline()[1:])
        parts.append(rfile.read(size))
        crlf = rfile.read(2)
        if crlf != b"\r\n":
            return parts
    return parts


@dataclass
class NodeState:
    kind: str  # sentinel | redis
    node_id: str
    role: str = "master"
    host: str = "127.0.0.1"
    port: int = 0
    listen_host: str = "127.0.0.1"
    listen_port: int = 0
    keys: dict[bytes, bytes] = field(default_factory=dict)
    commands: list[list[bytes]] = field(default_factory=list)
    require_password: bytes | None = None


class IsolatedServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        state: NodeState = self.server.state
        authed = state.require_password is None
        while True:
            parts = _read_command(self.rfile)
            if not parts:
                return
            state.commands.append(parts)
            self.server.world.commands.append((state.node_id, parts))
            reply = self._dispatch(parts, authed)
            if parts and parts[0].upper() == b"AUTH":
                expected = state.require_password
                authed = expected is not None and expected in parts[1:]
                reply = "OK" if authed else RespError("WRONGPASS invalid username-password pair")
            self.wfile.write(_encode(reply))
            self.wfile.flush()

    def _dispatch(self, parts: list[bytes], authed: bool) -> object:
        state: NodeState = self.server.state
        world: SentinelWorld = self.server.world
        cmd = parts[0].upper()
        if cmd == b"HELLO":
            return RespError("ERR unknown command 'HELLO'")
        if cmd == b"AUTH":
            return "OK"
        if cmd == b"CLIENT":
            return "OK"
        if not authed:
            return RespError("NOAUTH Authentication required.")
        if cmd == b"PING":
            return "PONG"
        if cmd == b"SELECT":
            return "OK"
        if cmd == b"INFO":
            role = b"master" if state.role == "master" else b"slave"
            return (
                b"# Server\r\nredis_version:7.2.0\r\nredis_mode:standalone\r\n"
                b"# Replication\r\nrole:" + role + b"\r\n"
            )
        if cmd == b"ROLE":
            if state.role == "master":
                return [b"master", 0, []]
            master = world.current_master()
            return [b"slave", master.host.encode(), master.port, b"connected", 0]
        if cmd == b"SENTINEL":
            return world.handle_sentinel(parts)
        if state.kind != "redis":
            return RespError("ERR unknown command")
        if state.role != "master" and cmd in {b"SET", b"GET", b"DEL", b"SCAN", b"DBSIZE"}:
            # After failover the old master refuses writes and we also surface READONLY on reads
            # so the client rediscovers. GET still returns READONLY to trigger follow.
            if cmd in {b"SET", b"DEL"} or world.readonly_on_old_master:
                return RespError("READONLY You can't write against a read only replica.")
        if cmd == b"GET":
            return state.keys.get(parts[1])
        if cmd == b"SET":
            state.keys[parts[1]] = parts[2]
            return "OK"
        if cmd == b"SCAN":
            return [b"0", list(state.keys.keys())]
        if cmd == b"DBSIZE":
            return len(state.keys)
        if cmd == b"ACL":
            sub = parts[1].upper() if len(parts) > 1 else b""
            if sub == b"WHOAMI":
                return b"default"
            if sub == b"GETUSER":
                return [b"commands", b"+@all", b"keys", b"~*"]
            return []
        if cmd == b"SLOWLOG":
            return []
        return RespError("ERR unhandled")


@dataclass
class SentinelWorld:
    master_name: str
    nodes: list[NodeState]
    current_id: str
    commands: list[tuple[str, list[bytes]]] = field(default_factory=list)
    servers: list[IsolatedServer] = field(default_factory=list)
    threads: list[threading.Thread] = field(default_factory=list)
    readonly_on_old_master: bool = False

    def current_master(self) -> NodeState:
        for node in self.nodes:
            if node.node_id == self.current_id:
                return node
        raise KeyError(self.current_id)

    def handle_sentinel(self, parts: list[bytes]) -> object:
        sub = parts[1].upper() if len(parts) > 1 else b""
        name = parts[2].decode("utf-8") if len(parts) > 2 else ""
        if sub == b"GET-MASTER-ADDR-BY-NAME":
            if name != self.master_name:
                return None
            master = self.current_master()
            return [master.host.encode(), str(master.port).encode()]
        if sub == b"MASTER":
            if name != self.master_name:
                return RespError("ERR No such master")
            master = self.current_master()
            return [
                b"name", self.master_name.encode(),
                b"ip", master.host.encode(),
                b"port", str(master.port).encode(),
                b"flags", b"master",
                b"num-slaves", b"1",
                b"num-other-sentinels", b"1",
                b"quorum", b"2",
            ]
        return RespError("ERR unknown SENTINEL subcommand")

    def failover(self, new_id: str) -> None:
        old = self.current_master()
        old.role = "slave"
        self.readonly_on_old_master = True
        self.current_id = new_id
        new = self.current_master()
        new.role = "master"
        # copy keys so reads after switch still work
        new.keys.update(old.keys)


@contextmanager
def start_world(nodes: list[NodeState], *, master_name: str = "mymaster", current_id: str = "master-a"):
    world = SentinelWorld(master_name=master_name, nodes=nodes, current_id=current_id)
    try:
        for node in nodes:
            server = IsolatedServer(("127.0.0.1", 0), Handler)
            server.state = node
            server.world = world
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            assert port not in FORBIDDEN_PORTS
            assert port != 0
            node.listen_host, node.listen_port = host, int(port)
            if node.port == 0:
                node.host, node.port = host, int(port)
            world.servers.append(server)
            world.threads.append(thread)
        yield world
    finally:
        for server in world.servers:
            server.shutdown()
            server.server_close()
        for thread in world.threads:
            thread.join(timeout=3)


def typical_nodes() -> list[NodeState]:
    return [
        NodeState(kind="sentinel", node_id="sent-1"),
        NodeState(kind="sentinel", node_id="sent-2"),
        NodeState(kind="redis", node_id="master-a", role="master"),
        NodeState(kind="redis", node_id="master-b", role="slave"),
    ]


def _plain_tls() -> TlsConfig:
    return TlsConfig(enabled=False, allow_plaintext=True, plaintext_reason="isolated-mock-sentinel-lab")


def sentinel_profile(world: SentinelWorld, *, env: str = "dev", protection: str = "confirm", conn_id: str = "c-sentinel", db: int = 0) -> ConnectionProfile:
    sentinels = tuple(Endpoint(n.host, n.port) for n in world.nodes if n.kind == "sentinel")
    return ConnectionProfile(
        id=conn_id,
        name="sentinel-lab",
        topology="sentinel",
        transport="tcp",
        endpoint=sentinels[0],
        db=db,
        sentinel=SentinelConfig(master_name=world.master_name, sentinels=sentinels, require_master_role=True),
        env=env,  # type: ignore[arg-type]
        protection=protection,  # type: ignore[arg-type]
        tls=_plain_tls(),
    )


@pytest.fixture
def no_real_redis(monkeypatch):
    real_connect = socket.socket.connect

    def guarded(self, address):
        port = address[1] if isinstance(address, tuple) and len(address) >= 2 else None
        if port in FORBIDDEN_PORTS:
            raise AssertionError(f"refused connect to real Redis/Sentinel at {address}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    yield



LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _ssh_mapping(world):
    return {(node.host, int(node.port)): (node.listen_host, node.listen_port) for node in world.nodes}


def _advertise(world):
    sentinels = [node for node in world.nodes if node.kind == "sentinel"]
    redises = [node for node in world.nodes if node.kind == "redis"]
    for index, node in enumerate(sentinels):
        node.host = f"10.94.2.{11 + index}"
        node.port = 27001 + index
    for index, node in enumerate(redises):
        node.host = f"10.94.2.{21 + index}"
        node.port = 7101 + index
    return _ssh_mapping(world)


def _fake_ssh(world, mapping=None):
    from tests.test_ssh_tunnel import FakeSSHClient
    mapping = mapping or _ssh_mapping(world)
    first = next(iter(mapping.values()))
    return FakeSSHClient(first[0], first[1], mapping=mapping)


def _sentinel_ssh_profile(world, **kwargs):
    return replace(
        sentinel_profile(world, **kwargs),
        transport="ssh",
        ssh=SshConfig(host="bastion.internal", port=22, username="ops", auth_mode="password"),
    )


def _guard_loopback(monkeypatch):
    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        port = address[1] if isinstance(address, tuple) and len(address) >= 2 else None
        if port in FORBIDDEN_PORTS or (isinstance(host, str) and host not in LOOPBACK):
            raise AssertionError(f"refused connect to real network target {address}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)

def test_discover_master_info_scan_and_db(no_real_redis):
    with start_world(typical_nodes()) as world:
        master = world.current_master()
        master.keys[b"cache:a"] = b"value"
        profile = sentinel_profile(world, db=2)
        gw = open_gateway(profile)
        try:
            assert gw.ping() >= 0
            info = gw.info()
            assert info.get("redis_version") == "7.2.0"
            assert info.get("sentinel_master_name") == "mymaster"
            assert info.get("sentinel_master_host") == master.host
            assert info.get("sentinel_master_port") == str(master.port)
            assert gw.execute(["GET", "cache:a"], timeout_s=3) == b"value"
            assert gw.execute(["DBSIZE"], timeout_s=3) == 1
            cursor, keys, _ = gw.scan(None, 0, match="*", count=50, key_type=None)
            collected = set(keys)
            while cursor != 0:
                cursor, batch, _ = gw.scan(None, cursor, match="*", count=50, key_type=None)
                collected.update(batch)
            assert collected == {b"cache:a"}
            nodes = gw.nodes()
            assert nodes and nodes[0].role == "master"
            assert nodes[0].port == master.port
            # data commands went to the redis master, not only to sentinels
            assert any(p[0].upper() == b"GET" for p in master.commands)
            assert not any(p[0].upper() == b"GET" for n in world.nodes if n.kind == "sentinel" for p in n.commands)
        finally:
            gw.close()
        assert all(n.port not in FORBIDDEN_PORTS for n in world.nodes)


def test_unknown_master_name_and_unreachable_sentinels(no_real_redis):
    with start_world(typical_nodes()) as world:
        profile = sentinel_profile(world)
        bad = replace(profile, sentinel=SentinelConfig(master_name="no-such-master", sentinels=profile.sentinel.sentinels))
        with pytest.raises(AppError) as exc:
            open_gateway(bad)
        assert exc.value.code == ErrorCode.E06
        assert "主节点" in exc.value.message

    # no listeners at all: bind nothing, point at a closed ephemeral port
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    assert port not in FORBIDDEN_PORTS
    sock.close()
    profile = ConnectionProfile(
        id="c-down",
        name="down",
        topology="sentinel",
        transport="tcp",
        endpoint=Endpoint(host, port),
        sentinel=SentinelConfig(master_name="mymaster", sentinels=(Endpoint(host, port),)),
        tls=_plain_tls(),
    )
    with pytest.raises(AppError) as exc2:
        open_gateway(profile)
    assert exc2.value.code == ErrorCode.E06
    assert "哨兵" in exc2.value.message or "发现" in exc2.value.message


def test_failover_retries_reads_but_not_writes(no_real_redis):
    with start_world(typical_nodes()) as world:
        world.current_master().keys[b"k"] = b"from-a"
        profile = sentinel_profile(world)
        gw = open_gateway(profile)
        try:
            assert gw.execute(["GET", "k"], timeout_s=3) == b"from-a"
            world.failover("master-b")
            # read follows the new master
            assert gw.execute(["GET", "k"], timeout_s=3) == b"from-a"
            assert gw.nodes()[0].node_id.endswith(str(world.current_master().port))
            # force the gateway back onto the old node to observe write behaviour
            # after another flip: make current master readonly by failing over again? 
            # Instead issue SET while pointing at a node that will return READONLY once.
            world.failover("master-a")
            world.current_master().keys[b"k"] = b"from-a"
            # first, a read to attach to A
            assert gw.execute(["GET", "k"], timeout_s=3) in {b"from-a", b"from-a"}
            world.failover("master-b")
            with pytest.raises(AppError) as exc:
                gw.execute(["SET", "k", "new"], timeout_s=3)
            assert exc.value.code == ErrorCode.E04
            assert "切换" in exc.value.message or "主节点" in exc.value.message
            # subsequent write should hit the new master
            assert gw.execute(["SET", "k", "new"], timeout_s=3) == "OK" or gw.execute(["SET", "k", "new"], timeout_s=3) in (True, b"OK", "OK")
            assert world.current_master().keys[b"k"] == b"new"
        finally:
            gw.close()


def test_sentinel_ssh_routes_discovery_and_failover_through_jump(monkeypatch):
    _guard_loopback(monkeypatch)
    with start_world(typical_nodes()) as world:
        mapping = _advertise(world)
        client = _fake_ssh(world, mapping)
        profile = _sentinel_ssh_profile(world)
        gw = open_gateway(profile, ssh_password="pw", ssh_client=client)
        try:
            info = gw.info()
            assert info.get("sentinel_master_name") == "mymaster"
            assert gw.ping() >= 0
            assert gw.execute(["SET", "alpha", "1"], timeout_s=3) in {True, b"OK", "OK"}
            assert gw.execute(["GET", "alpha"], timeout_s=3) == b"1"
            host = gw._master_gw._client.connection_pool.connection_kwargs["host"]
            assert host == "127.0.0.1"
            dests = {(d[1][0], int(d[1][1])) for d in client.transport.destinations}
            current = world.current_master()
            assert (current.host, current.port) in dests
            sentinels = [n for n in world.nodes if n.kind == "sentinel"]
            assert any((n.host, n.port) in dests for n in sentinels)
            assert all(n.port not in FORBIDDEN_PORTS for n in world.nodes)

            old_master = current
            world.failover("master-b")
            new_master = world.current_master()
            assert new_master is not old_master
            assert gw.execute(["GET", "alpha"], timeout_s=3) == b"1"
            dests = {(d[1][0], int(d[1][1])) for d in client.transport.destinations}
            assert (new_master.host, new_master.port) in dests
            host = gw._master_gw._client.connection_pool.connection_kwargs["host"]
            assert host == "127.0.0.1"
            ep = gw._master_endpoint
            assert ep is not None
            assert (ep.host, ep.port) == (new_master.host, new_master.port)
        finally:
            gw.close()


def test_tls_plaintext_policy_and_store_roundtrip(tmp_store, no_real_redis):
    require_tls_or_exception(TlsConfig(enabled=True))
    with pytest.raises(AppError):
        require_tls_or_exception(TlsConfig(enabled=False, allow_plaintext=False))
    with pytest.raises(AppError):
        require_tls_or_exception(TlsConfig(enabled=False, allow_plaintext=True, plaintext_reason="short"))
    profile = ConnectionProfile(
        id="c-plain-sentinel",
        name="plain-sentinel",
        topology="sentinel",
        transport="tcp",
        endpoint=Endpoint("127.0.0.1", 27001),
        db=3,
        sentinel=SentinelConfig(
            master_name="mymaster",
            sentinels=(Endpoint("127.0.0.1", 27001), Endpoint("127.0.0.1", 27002)),
            sentinel_username="sentinel-user",
        ),
        env="dev",
        protection="confirm",
        tls=TlsConfig(enabled=False, allow_plaintext=True, plaintext_reason="sentinel-local-mock"),
    )
    tmp_store.save_connection(profile, created_by="alice")
    loaded = tmp_store.get_connection("c-plain-sentinel")
    assert loaded is not None
    assert loaded.topology == "sentinel"
    assert loaded.db == 3
    assert loaded.sentinel is not None
    assert loaded.sentinel.master_name == "mymaster"
    assert [(s.host, s.port) for s in loaded.sentinel.sentinels] == [("127.0.0.1", 27001), ("127.0.0.1", 27002)]
    assert loaded.sentinel.sentinel_username == "sentinel-user"
    short = ConnectionProfile(
        id="c-short",
        name="short",
        topology="sentinel",
        transport="tcp",
        endpoint=Endpoint("127.0.0.1", 27001),
        sentinel=SentinelConfig(master_name="mymaster", sentinels=(Endpoint("127.0.0.1", 27001),)),
        env="dev",
        protection="confirm",
        tls=TlsConfig(enabled=False, allow_plaintext=True, plaintext_reason="short"),
    )
    with pytest.raises(AppError):
        tmp_store.save_connection(short, created_by="alice")


def test_test_connect_and_workspace_against_mock(tmp_store, audit, no_real_redis):
    grants = StaticGrantResolver({("c-sentinel", "admin"), ("c-sentinel", "viewer")})
    svc = ConnectionService(tmp_store, audit, grants)
    with start_world(typical_nodes()) as world:
        world.current_master().keys[b"cache:a"] = b"value"
        profile = sentinel_profile(world)
        tmp_store.save_connection(profile, created_by="alice")
        result = svc.test_connect(make_session("admin"), profile, require_grant=True)
        assert result.version == "7.2.0"
        session = WorkspaceSession(svc, make_session("admin"), profile)
        try:
            session.connect()
            assert session.dbsize == 1
            session.reset_scan("*")
            steps = 0
            while not session.scan_complete and steps < 8:
                session.scan_next()
                steps += 1
            rows = session._db.execute("SELECT raw FROM keys").fetchall()
            assert {bytes(r[0]) for r in rows} == {b"cache:a"}
        finally:
            session.close()


def test_authz_and_production_readonly_on_sentinel_profile(tmp_store, audit):
    grants = StaticGrantResolver({("c-sentinel", "admin"), ("c-sentinel", "viewer")})
    profile = ConnectionProfile(
        id="c-sentinel",
        name="prod-sentinel",
        topology="sentinel",
        transport="tcp",
        endpoint=Endpoint("10.0.0.8", 26379),
        db=0,
        sentinel=SentinelConfig(master_name="mymaster", sentinels=(Endpoint("10.0.0.8", 26379),)),
        env="prod",
        protection="readonly",
        tls=TlsConfig(enabled=True),
    )
    assert check(make_session("admin"), Action.CONNECT, profile, grants=grants).allowed
    assert check(make_session("admin"), Action.EXEC_L1, profile, grants=grants).allowed
    denied = check(make_session("admin"), Action.EXEC_L2, profile, grants=grants)
    assert not denied.allowed and denied.reason == "protection"
    engine = PolicyEngine()
    assert not engine.evaluate(["SET", "k", "v"], profile, "admin").allowed
    assert not engine.evaluate(["KEYS", "*"], profile, "admin").allowed
    console = ConsoleService(tmp_store, audit, grants)
    with pytest.raises(AppError) as exc:
        console.prepare(make_session("admin"), profile, "SET k v")
    assert exc.value.code == "E08"
    with pytest.raises(AppError):
        console.prepare(make_session("admin"), profile, "KEYS *")
    with pytest.raises(AppError):
        console.prepare(make_session("viewer"), profile, "SET k v")
    prepared = console.prepare(make_session("admin"), profile, "GET k")
    assert prepared is not None


def test_sentinel_auth_is_used_for_sentinels_not_data_node(no_real_redis):
    nodes = typical_nodes()
    for node in nodes:
        if node.kind == "sentinel":
            node.require_password = b"sentinel-secret"
    with start_world(nodes) as world:
        world.current_master().keys[b"k"] = b"v"
        profile = sentinel_profile(world)
        profile = replace(profile, sentinel=SentinelConfig(
            master_name="mymaster",
            sentinels=profile.sentinel.sentinels,
            require_master_role=True,
        ))
        with pytest.raises(AppError) as exc:
            open_gateway(profile)
        assert exc.value.code in {ErrorCode.E02, ErrorCode.E06}
        gw = open_gateway(profile, sentinel_password=b"sentinel-secret")
        try:
            assert gw.execute(["GET", "k"], timeout_s=3) == b"v"
        finally:
            gw.close()


@pytest.fixture
def qapp_sentinel():
    from PySide6.QtWidgets import QApplication
    from redisdesk.app.ui.theme import apply_theme

    app = QApplication.instance() or QApplication(["redisdesk-sentinel-tests"])
    apply_theme(app, "light")
    return app


def test_dialog_sentinel_mode_create_and_validation(qapp_sentinel):
    from PySide6.QtWidgets import QApplication
    from redisdesk.app.ui.dialogs import ConnectionEditorDialog

    dlg = ConnectionEditorDialog()
    dlg.show()
    QApplication.processEvents()
    labels = [dlg.topology.itemText(i) for i in range(dlg.topology.count())]
    assert "哨兵" in labels
    assert "单机直连" in labels
    assert "集群" in labels
    assert not dlg.master_name.isVisible()
    for i in range(dlg.topology.count()):
        if dlg.topology.itemData(i) == "sentinel":
            dlg.topology.setCurrentIndex(i)
            break
    QApplication.processEvents()
    form = dlg._basic_form
    host_label = form.labelForField(dlg.host)
    assert host_label is not None and host_label.text() == "哨兵节点"
    assert dlg.master_name.isVisible()
    assert dlg.sentinel_extra.isVisible()
    assert dlg.db.isEnabled()
    assert dlg.port.value() == 26379
    assert dlg.use_ssh.isEnabled()
    assert not dlg.use_ssh.isChecked()
    dlg.use_ssh.setChecked(True)
    dlg.ssh_host.setText("bastion.example")
    dlg.ssh_username.setText("ops")
    dlg.ssh_password.setText("secret")
    QApplication.processEvents()
    ssh_profile = dlg.profile()
    assert ssh_profile.transport == "ssh"
    assert ssh_profile.ssh is not None
    assert ssh_profile.ssh.host == "bastion.example"
    dlg.use_ssh.setChecked(False)
    QApplication.processEvents()
    dlg.name.setText("sentinel-lab")
    dlg.host.setText("127.0.0.1")
    dlg.port.setValue(27001)
    dlg.db.setValue(2)
    dlg.master_name.setText("mymaster")
    dlg.sentinel_extra.setPlainText("127.0.0.1:27002")
    profile = dlg.profile()
    assert profile.topology == "sentinel"
    assert profile.transport == "tcp"
    assert profile.db == 2
    assert profile.ssh is None
    assert profile.sentinel.master_name == "mymaster"
    assert [(s.host, s.port) for s in profile.sentinel.sentinels] == [
        ("127.0.0.1", 27001),
        ("127.0.0.1", 27002),
    ]
    dlg.master_name.setText("")
    dlg.accept()
    QApplication.processEvents()
    assert "主节点" in dlg.status_label.text()
    assert dlg.isVisible()
    dlg.master_name.setText("mymaster")
    dlg.tls.setChecked(False)
    dlg.allow_plain.setChecked(True)
    dlg.reason.setText("short")
    dlg.accept()
    QApplication.processEvents()
    assert "10" in dlg.status_label.text() or "理由" in dlg.status_label.text()
    dlg.close()


def test_dialog_test_connect_stays_off_gui_thread(qapp_sentinel, tmp_store, audit, no_real_redis):
    from PySide6.QtWidgets import QApplication
    from redisdesk.app.ui.dialogs import ConnectionEditorDialog

    grants = StaticGrantResolver({("c-sentinel", "admin")})
    svc = ConnectionService(tmp_store, audit, grants)
    with start_world(typical_nodes()) as world:
        dlg = ConnectionEditorDialog(session=make_session("admin"), service=svc)
        dlg.show()
        QApplication.processEvents()
        for i in range(dlg.topology.count()):
            if dlg.topology.itemData(i) == "sentinel":
                dlg.topology.setCurrentIndex(i)
                break
        dlg.name.setText("sentinel-lab")
        dlg.host.setText(world.nodes[0].host)
        dlg.port.setValue(world.nodes[0].port)
        dlg.sentinel_extra.setPlainText(f"{world.nodes[1].host}:{world.nodes[1].port}")
        dlg.master_name.setText("mymaster")
        dlg.tls.setChecked(False)
        dlg.allow_plain.setChecked(True)
        dlg.reason.setText("isolated-mock-sentinel-lab")
        QApplication.processEvents()
        dlg.test_button.click()
        QApplication.processEvents()
        if dlg._test_worker is not None:
            assert dlg._test_worker.wait(5000)
        QApplication.processEvents()
        text = dlg.status_label.text()
        assert "已成功连接" in text
        dlg.close()


def test_sentinel_ssh_known_hosts_mismatch_is_refused(monkeypatch):
    _guard_loopback(monkeypatch)
    from redisdesk.infra.known_hosts import HostKeyMismatchError, UnknownHostKeyError

    def unknown(self):
        raise UnknownHostKeyError(
            host="bastion.internal",
            port=22,
            key=SimpleNamespace(get_name=lambda: "ssh-ed25519", asbytes=lambda: b"k" * 32),
            policy="tofu",
        )

    def mismatch(self):
        raise HostKeyMismatchError(
            host="bastion.internal",
            port=22,
            expected_fingerprint="AA",
            actual_fingerprint="BB",
            key_type="ssh-ed25519",
        )

    profile = ConnectionProfile(
        id="s-ssh-kh",
        name="sentinel-ssh",
        topology="sentinel",
        transport="ssh",
        endpoint=Endpoint("10.94.2.11", 27001),
        db=0,
        sentinel=SentinelConfig(master_name="mymaster", sentinels=(Endpoint("10.94.2.11", 27001),)),
        ssh=SshConfig(host="bastion.internal", port=22, username="ops", auth_mode="password"),
        tls=_plain_tls(),
    )
    monkeypatch.setattr("redisdesk.infra.tunnel.SshTunnel._connect_client", unknown)
    with pytest.raises(AppError) as exc:
        open_gateway(profile, ssh_password="pw")
    assert exc.value.code == ErrorCode.E07
    assert "SSH" in exc.value.message
    monkeypatch.setattr("redisdesk.infra.tunnel.SshTunnel._connect_client", mismatch)
    with pytest.raises(AppError) as exc:
        open_gateway(profile, ssh_password="pw")
    assert exc.value.code == ErrorCode.E07
    assert "不一致" in exc.value.message or "已拒绝" in exc.value.message


def test_sentinel_ssh_tls_plaintext_policy(monkeypatch):
    _guard_loopback(monkeypatch)
    profile = ConnectionProfile(
        id="s-ssh-tls",
        name="sentinel-ssh-tls",
        topology="sentinel",
        transport="ssh",
        endpoint=Endpoint("10.94.2.11", 27001),
        db=0,
        sentinel=SentinelConfig(master_name="mymaster", sentinels=(Endpoint("10.94.2.11", 27001),)),
        ssh=SshConfig(host="bastion.internal", port=22, username="ops", auth_mode="password"),
        tls=TlsConfig(enabled=False, allow_plaintext=True, plaintext_reason="short"),
    )
    with pytest.raises(AppError) as exc:
        require_tls_or_exception(profile.tls)
    blob = f"{exc.value.message} {exc.value.hint}"
    assert exc.value.code == ErrorCode.E01
    assert "10" in blob or "理由" in blob or "明文" in blob
    profile = replace(profile, tls=TlsConfig(enabled=False, allow_plaintext=False, plaintext_reason=None))
    with pytest.raises(AppError):
        require_tls_or_exception(profile.tls)


def test_dialog_sentinel_ssh_test_connect_stays_off_gui_thread(qapp_sentinel, tmp_store, audit, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from redisdesk.app.ui.dialogs import ConnectionEditorDialog
    from redisdesk.infra.tunnel import SshTunnel

    _guard_loopback(monkeypatch)
    grants = StaticGrantResolver({("c-sentinel", "admin")})
    svc = ConnectionService(tmp_store, audit, grants)
    with start_world(typical_nodes()) as world:
        mapping = _advertise(world)
        fake = _fake_ssh(world, mapping)
        monkeypatch.setattr(SshTunnel, "_connect_client", lambda self: fake)
        dlg = ConnectionEditorDialog(session=make_session("admin"), service=svc)
        dlg.show()
        QApplication.processEvents()
        for i in range(dlg.topology.count()):
            if dlg.topology.itemData(i) == "sentinel":
                dlg.topology.setCurrentIndex(i)
                break
        sentinels = [n for n in world.nodes if n.kind == "sentinel"]
        dlg.name.setText("sentinel-ssh-lab")
        dlg.host.setText(sentinels[0].host)
        dlg.port.setValue(sentinels[0].port)
        if len(sentinels) > 1:
            dlg.sentinel_extra.setPlainText(f"{sentinels[1].host}:{sentinels[1].port}")
        dlg.master_name.setText("mymaster")
        dlg.tls.setChecked(False)
        dlg.allow_plain.setChecked(True)
        dlg.reason.setText("isolated-mock-sentinel-ssh-lab")
        dlg.use_ssh.setChecked(True)
        dlg.ssh_host.setText("bastion.internal")
        dlg.ssh_username.setText("ops")
        dlg.ssh_password.setText("secret")
        QApplication.processEvents()
        dlg.test_button.click()
        QApplication.processEvents()
        if dlg._test_worker is not None:
            assert dlg._test_worker.wait(5000)
        QApplication.processEvents()
        assert "已成功连接" in dlg.status_label.text()
        dests = {(d[1][0], int(d[1][1])) for d in fake.transport.destinations}
        assert (sentinels[0].host, sentinels[0].port) in dests
        master = world.current_master()
        assert (master.host, master.port) in dests
        dlg.close()
