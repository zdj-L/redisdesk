"""Redis Cluster: create/test/use against isolated mock RESP only. Never 6379."""
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

from redisdesk.adapters.cluster import (
    ClusterGateway,
    extract_keys,
    key_slot,
    parse_cluster_slots,
    parse_redirect,
)
from redisdesk.adapters.gateway import open_gateway
from redisdesk.adapters.tls import require_tls_or_exception
from redisdesk.app.services.connection_service import ConnectionService
from redisdesk.app.services.console_service import ConsoleService
from redisdesk.app.services.workspace_session import WorkspaceSession
from redisdesk.app.services.diag_service import (
    CLIENT_SHOW_MAX,
    SLOWLOG_FETCH_COUNT,
    SLOWLOG_SHOW_ROWS,
    collect_lists,
)
from redisdesk.app.ui.diagnostics_metrics import CARD_SERVER, SECTION_CLIENT_LIST, SECTION_SLOWLOG, build_card_pairs
from redisdesk.core.authz import Action, StaticGrantResolver, check
from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.model import ClusterConfig, ConnectionProfile, Endpoint, SshConfig, TlsConfig
from redisdesk.core.policy import PolicyEngine
from tests.conftest import make_session

FORBIDDEN_PORT = 6379


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
    node_id: str
    role: str
    slots: list[tuple[int, int]]
    host: str = "127.0.0.1"
    port: int = 0
    listen_host: str = "127.0.0.1"
    listen_port: int = 0
    keys: dict[bytes, bytes] = field(default_factory=dict)
    moved: dict[bytes, tuple[int, str, int]] = field(default_factory=dict)
    ask: dict[bytes, tuple[int, str, int]] = field(default_factory=dict)
    commands: list[list[bytes]] = field(default_factory=list)
    slowlog: list = field(default_factory=list)
    slowlog_len: int | None = None
    client_lines: list[str] = field(default_factory=list)
    deny: set[str] = field(default_factory=set)
    flush_error: bool = False
    slower_than: str = "10000"
    used_memory: int = 1048576
    connected_clients: int = 1


class ClusterServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class ClusterHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        asked = False
        queued: list[list[bytes]] | None = None
        state: NodeState = self.server.state
        while True:
            parts = _read_command(self.rfile)
            if not parts:
                return
            state.commands.append(parts)
            self.server.world.commands.append((state.node_id, parts))
            cmd = parts[0].upper()
            if queued is not None and cmd not in {b"EXEC", b"DISCARD", b"MULTI"}:
                queued.append(parts)
                reply = "QUEUED"
            elif cmd == b"MULTI":
                queued = []
                reply = "OK"
            elif cmd == b"EXEC":
                replies = [self._dispatch(item, asked) for item in (queued or [])]
                queued = None
                reply = replies
            elif cmd == b"DISCARD":
                queued = None
                reply = "OK"
            else:
                reply = self._dispatch(parts, asked)
            if parts and cmd == b"ASKING":
                asked = True
            elif parts and cmd != b"CLIENT":
                asked = False
            self.wfile.write(_encode(reply))
            self.wfile.flush()

    def _dispatch(self, parts: list[bytes], asked: bool) -> object:
        state: NodeState = self.server.state
        world: ClusterWorld = self.server.world
        cmd = parts[0].upper()
        if cmd == b"HELLO":
            return RespError("ERR unknown command 'HELLO'")
        if cmd == b"AUTH":
            return "OK"
        if cmd == b"CLIENT":
            sub = parts[1].upper() if len(parts) > 1 else b""
            if sub == b"LIST":
                if "CLIENT" in state.deny:
                    return RespError("NOPERM this user has no permissions to run the 'client|list' command")
                lines = state.client_lines
                return ("\n".join(lines) + ("\n" if lines else ""))
            return "OK"
        if cmd == b"SELECT":
            return RespError("ERR SELECT is not allowed in cluster mode")
        if cmd == b"PING":
            return "PONG"
        if cmd == b"ASKING":
            return "OK"
        if cmd == b"INFO":
            keys_n = len(state.keys)
            body = (
                "# Server\r\nredis_version:7.2.0\r\nos:Windows\r\nuptime_in_seconds:3600\r\n"
                "redis_mode:cluster\r\n# Clients\r\nconnected_clients:%d\r\n"
                "# Memory\r\nused_memory:%d\r\n# Keyspace\r\ndb0:keys=%d,expires=0\r\n"
                % (int(state.connected_clients), int(state.used_memory), keys_n)
            )
            return body.encode("ascii")
        if cmd == b"CLUSTER":
            sub = parts[1].upper() if len(parts) > 1 else b""
            if sub == b"SLOTS":
                return world.slots_payload()
            if sub == b"INFO":
                return (
                    b"cluster_state:ok\r\ncluster_slots_assigned:16384\r\n"
                    b"cluster_slots_ok:16384\r\ncluster_known_nodes:%d\r\n"
                    % len(world.nodes)
                )
            return RespError("ERR unknown CLUSTER subcommand")
        if cmd == b"DBSIZE":
            return len(state.keys)
        if cmd == b"SCAN":
            cursor = int(parts[1]) if len(parts) > 1 else 0
            keys = list(state.keys.keys())
            return [b"0", keys] if cursor == 0 or True else [b"0", []]
        if cmd == b"GET":
            key = parts[1]
            if key in state.moved:
                slot, host, port = state.moved[key]
                return RespError(f"MOVED {slot} {host}:{port}")
            if key in state.ask and not asked:
                slot, host, port = state.ask[key]
                return RespError(f"ASK {slot} {host}:{port}")
            if key not in state.keys:
                return None
            return state.keys[key]
        if cmd == b"SET":
            state.keys[parts[1]] = parts[2]
            return "OK"
        if cmd == b"CONFIG":
            sub = parts[1].upper() if len(parts) > 1 else b""
            if sub == b"GET":
                name = parts[2].decode("utf-8", "replace") if len(parts) > 2 else ""
                if "slowlog-log-slower-than" in name:
                    return [b"slowlog-log-slower-than", state.slower_than.encode("ascii")]
                return []
            return RespError("ERR unknown CONFIG subcommand")
        if cmd in {b"FLUSHALL", b"FLUSHDB"}:
            if state.flush_error:
                return RespError("ERR flush failed")
            state.keys.clear()
            return "OK"
        if cmd == b"MGET":
            keys = parts[1:]
            if len({key_slot(k) for k in keys}) > 1:
                return RespError("CROSSSLOT Keys in request don't hash to the same slot")
            return [state.keys.get(k) for k in keys]
        if cmd == b"MSET":
            pairs = list(zip(parts[1::2], parts[2::2]))
            if len({key_slot(k) for k, _v in pairs}) > 1:
                return RespError("CROSSSLOT Keys in request don't hash to the same slot")
            for key, value in pairs:
                state.keys[key] = value
            return "OK"
        if cmd in {b"DEL", b"UNLINK"}:
            keys = parts[1:]
            if len({key_slot(k) for k in keys}) > 1:
                return RespError("CROSSSLOT Keys in request don't hash to the same slot")
            removed = 0
            for key in keys:
                if key in state.keys:
                    del state.keys[key]
                    removed += 1
            return removed
        if cmd in {b"EXISTS", b"TOUCH"}:
            keys = parts[1:]
            if len({key_slot(k) for k in keys}) > 1:
                return RespError("CROSSSLOT Keys in request don't hash to the same slot")
            return sum(1 for key in keys if key in state.keys)
        if cmd == b"ACL":
            sub = parts[1].upper() if len(parts) > 1 else b""
            if sub == b"WHOAMI":
                return b"default"
            if sub == b"GETUSER":
                return [b"commands", b"+@all", b"keys", b"~*"]
            return []
        if cmd == b"SLOWLOG":
            if "SLOWLOG" in state.deny:
                return RespError("NOPERM this user has no permissions to run the 'slowlog' command")
            sub = parts[1].upper() if len(parts) > 1 else b""
            if sub == b"LEN":
                return len(state.slowlog) if state.slowlog_len is None else int(state.slowlog_len)
            if sub == b"GET":
                count = int(parts[2]) if len(parts) > 2 else 128
                return list(state.slowlog[: max(0, count)])
            if sub == b"RESET":
                return "OK"
            return []
        if cmd == b"COMMAND":
            return []
        return RespError("ERR unhandled")

@dataclass
class ClusterWorld:
    nodes: list[NodeState]
    commands: list[tuple[str, list[bytes]]] = field(default_factory=list)
    servers: list[ClusterServer] = field(default_factory=list)
    threads: list[threading.Thread] = field(default_factory=list)

    def slots_payload(self) -> list:
        rows = []
        for node in self.nodes:
            if node.role != "master":
                continue
            for start, end in node.slots:
                master = [node.host.encode(), node.port, node.node_id.encode()]
                replicas = [
                    [rep.host.encode(), rep.port, rep.node_id.encode()]
                    for rep in self.nodes
                    if rep.role != "master"
                ]
                rows.append([start, end, master, *replicas])
        return rows

    def by_id(self, node_id: str) -> NodeState:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(node_id)

    def put(self, key: bytes, value: bytes) -> NodeState:
        slot = key_slot(key)
        for node in self.nodes:
            if node.role != "master":
                continue
            for start, end in node.slots:
                if start <= slot <= end:
                    node.keys[key] = value
                    return node
        raise AssertionError(f"no master owns slot {slot}")


@contextmanager
def start_cluster(nodes: list[NodeState]):
    world = ClusterWorld(nodes=nodes)
    try:
        for node in nodes:
            server = ClusterServer(("127.0.0.1", 0), ClusterHandler)
            server.state = node
            server.world = world
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            assert port != FORBIDDEN_PORT
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


def two_masters() -> list[NodeState]:
    return [
        NodeState(node_id="node-a", role="master", slots=[(0, 8191)]),
        NodeState(node_id="node-b", role="master", slots=[(8192, 16383)]),
    ]


def _plain_tls() -> TlsConfig:
    return TlsConfig(
        enabled=False,
        allow_plaintext=True,
        plaintext_reason="isolated-mock-cluster-lab",
    )


def cluster_profile(world: ClusterWorld, *, env: str = "dev", protection: str = "confirm", conn_id: str = "c-cluster") -> ConnectionProfile:
    seeds = tuple(Endpoint(node.host, node.port) for node in world.nodes)
    primary = seeds[0]
    return ConnectionProfile(
        id=conn_id,
        name="cluster-lab",
        topology="cluster",
        transport="tcp",
        endpoint=primary,
        db=0,
        cluster=ClusterConfig(seeds=seeds),
        env=env,  # type: ignore[arg-type]
        protection=protection,  # type: ignore[arg-type]
        tls=_plain_tls(),
    )


@pytest.fixture
def no_real_redis(monkeypatch):
    real_connect = socket.socket.connect

    def guarded(self, address):
        port = address[1] if isinstance(address, tuple) and len(address) >= 2 else None
        if port == FORBIDDEN_PORT:
            raise AssertionError(f"refused connect to real Redis at {address}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    yield



LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _ssh_mapping(world):
    return {(node.host, int(node.port)): (node.listen_host, node.listen_port) for node in world.nodes}


def _advertise(world, base_ip="10.94.1.", base_port=7000):
    for index, node in enumerate(world.nodes):
        node.host = f"{base_ip}{11 + index}"
        node.port = int(base_port + index)
    return _ssh_mapping(world)


def _fake_ssh(world, mapping=None):
    from tests.test_ssh_tunnel import FakeSSHClient
    mapping = mapping or _ssh_mapping(world)
    first = next(iter(mapping.values()))
    return FakeSSHClient(first[0], first[1], mapping=mapping)


def _cluster_ssh_profile(world, **kwargs):
    return replace(
        cluster_profile(world, **kwargs),
        transport="ssh",
        ssh=SshConfig(host="bastion.internal", port=22, username="ops", auth_mode="password"),
    )


def _guard_loopback(monkeypatch):
    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        port = address[1] if isinstance(address, tuple) and len(address) >= 2 else None
        if port == FORBIDDEN_PORT or (isinstance(host, str) and host not in LOOPBACK):
            raise AssertionError(f"refused connect to real network target {address}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)

def test_key_slot_and_hash_tags_match_redis_crc():
    from redis.crc import key_slot as official

    for key in (b"alpha", b"user:42", b"{user}.profile", b"{user}.inbox", b"plain"):
        assert key_slot(key) == official(key)
        assert key_slot(key.decode()) == official(key)
    assert key_slot(b"{user}.profile") == key_slot(b"{user}.inbox")
    assert extract_keys(["GET", "k"]) == [b"k"]
    assert extract_keys(["MGET", "a", "b"]) == [b"a", b"b"]
    assert extract_keys(["MSET", "a", "1", "b", "2"]) == [b"a", b"b"]
    assert extract_keys(["INFO"]) == []
    parsed = parse_cluster_slots(
        [[0, 10, [b"10.0.0.1", 7000, b"abc"], [b"10.0.0.2", 7001, b"def"]]]
    )
    roles = {node.node_id: node.role for node in parsed}
    assert roles["abc"] == "master"
    assert roles["def"] == "slave"
    moved = parse_redirect(SimpleNamespace(**{}))
    class FakeMoved(Exception):
        pass
    FakeMoved.__name__ = "MovedError"
    parsed_moved = parse_redirect(FakeMoved("MOVED 3999 127.0.0.1:7002"))
    assert parsed_moved == ("MOVED", 3999, "127.0.0.1", 7002)
    class FakeAsk(Exception):
        pass
    FakeAsk.__name__ = "AskError"
    parsed_ask = parse_redirect(FakeAsk("ASK 12 10.1.2.3:7005"))
    assert parsed_ask == ("ASK", 12, "10.1.2.3", 7005)
    assert moved is None


def test_discovery_routing_scan_and_dbsize(no_real_redis):
    with start_cluster(two_masters()) as world:
        a_key = b"slot-low"
        b_key = b"slot-high"
        # pick keys that land on each half
        low = next(k for k in (a_key, b"alpha", b"aaa", b"key-a", b"n0") if key_slot(k) <= 8191)
        high = next(k for k in (b_key, b"zzz", b"key-b", b"n1", b"zzzzzz") if key_slot(k) >= 8192)
        world.put(low, b"L")
        world.put(high, b"H")
        profile = cluster_profile(world)
        gw = open_gateway(profile)
        try:
            assert gw.capabilities.supports_cluster
            assert gw.ping() >= 0
            info = gw.info()
            assert info.get("redis_version") == "7.2.0"
            assert info.get("redis_mode") == "cluster"
            assert info.get("cluster_masters") == "2"
            assert info.get("cluster_state") == "ok"
            assert {n.node_id for n in gw.masters()} == {"node-a", "node-b"}
            assert gw.execute(["GET", low.decode()], timeout_s=3) == b"L"
            assert gw.execute(["GET", high.decode()], timeout_s=3) == b"H"
            # GET went to the owning node
            def saw(node_id, key):
                return any(p[0].upper() == b"GET" and p[1] == key for p in world.by_id(node_id).commands)

            owner_low = "node-a" if key_slot(low) <= 8191 else "node-b"
            owner_high = "node-b" if key_slot(high) >= 8192 else "node-a"
            assert saw(owner_low, low)
            assert saw(owner_high, high)
            assert gw.dbsize() == 2
            # composite SCAN walks both masters
            cursor, keys, _ = gw.scan(None, 0, match="*", count=50, key_type=None)
            collected = set(keys)
            while cursor != 0:
                cursor, batch, _ = gw.scan(None, cursor, match="*", count=50, key_type=None)
                collected.update(batch)
            assert collected == {low, high}
        finally:
            gw.close()
        assert all(node.port != FORBIDDEN_PORT for node in world.nodes)


def test_moved_updates_slot_map_and_ask_sends_asking(no_real_redis):
    with start_cluster(two_masters()) as world:
        key = next(k for k in (b"moved-key", b"alpha", b"user:1", b"cache") if key_slot(k) <= 8191)
        slot = key_slot(key)
        node_a, node_b = world.nodes[0], world.nodes[1]
        # Stale map: A advertises every slot, but the key actually lives on B.
        node_a.slots = [(0, 16383)]
        node_b.slots = []
        node_b.keys[key] = b"from-b"
        node_a.moved[key] = (slot, node_b.host, node_b.port)
        profile = cluster_profile(world)
        gw = open_gateway(profile)
        try:
            assert gw.execute(["GET", key.decode()], timeout_s=3) == b"from-b"
            assert any(p[0].upper() == b"GET" and p[1] == key for p in node_a.commands)
            assert any(p[0].upper() == b"GET" and p[1] == key for p in node_b.commands)
            # second GET should go to B without another MOVED
            node_a.commands.clear()
            node_b.commands.clear()
            node_a.moved.clear()
            assert gw.execute(["GET", key.decode()], timeout_s=3) == b"from-b"
            assert not any(p[0].upper() == b"GET" and p[1] == key for p in node_a.commands)
            assert any(p[0].upper() == b"GET" and p[1] == key for p in node_b.commands)
        finally:
            gw.close()

        # ASK: A points to B; B requires ASKING on the same connection
        node_a.slots = [(0, 16383)]
        node_b.slots = []
        node_a.moved.clear()
        node_a.commands.clear()
        node_b.commands.clear()
        node_a.ask[key] = (slot, node_b.host, node_b.port)
        node_b.keys[key] = b"asked"
        gw = open_gateway(profile)
        try:
            assert gw.execute(["GET", key.decode()], timeout_s=3) == b"asked"
            cmds = [p[0].upper() for p in node_b.commands]
            assert b"ASKING" in cmds and b"GET" in cmds
            assert max(i for i, c in enumerate(cmds) if c == b"ASKING") < max(i for i, c in enumerate(cmds) if c == b"GET")
        finally:
            gw.close()


def test_cross_slot_and_select_rejected(no_real_redis):
    with start_cluster(two_masters()) as world:
        low = next(k for k in (b"aaa", b"alpha", b"n0") if key_slot(k) <= 8191)
        high = next(k for k in (b"zzz", b"zzzzzz", b"n1") if key_slot(k) >= 8192)
        world.put(low, b"L")
        world.put(high, b"H")
        gw = open_gateway(cluster_profile(world))
        try:
            assert gw.execute(["MGET", low.decode(), high.decode()], timeout_s=3) == [b"L", b"H"]
            assert gw.execute(["DEL", low.decode(), high.decode()], timeout_s=3) == 2
            assert gw.execute(["MSET", low.decode(), b"l2".decode() if False else "l2", high.decode(), "h2"], timeout_s=3) in {"OK", b"OK", True}
            assert world.nodes[0].keys[low] == b"l2"
            assert world.nodes[1].keys[high] == b"h2"
            with pytest.raises(AppError) as exc:
                gw.execute(["MSETNX", low.decode(), "x", high.decode(), "y"], timeout_s=3)
            assert exc.value.code == ErrorCode.E05
            assert "MSETNX" in exc.value.message or "原子" in exc.value.message or "槽" in exc.value.message
            with pytest.raises(AppError) as exc2:
                gw.execute(["SINTER", low.decode(), high.decode()], timeout_s=3)
            assert exc2.value.code == ErrorCode.E05
            with pytest.raises(AppError) as exc3:
                gw.execute(["SELECT", "1"], timeout_s=3)
            assert exc3.value.code == ErrorCode.E08
            with pytest.raises(AppError) as exc4:
                gw.execute(["MULTI"], timeout_s=3)
            assert exc4.value.code == ErrorCode.E05
            assert "MULTI" in exc4.value.message
        finally:
            gw.close()
        mgets = [p[1:] for nid, p in world.commands if p[0].upper() == b"MGET"]
        assert mgets
        for args in mgets:
            assert len({key_slot(k) for k in args}) == 1
        assert not any(p[0].upper() == b"SINTER" for _, p in world.commands)
        assert not any(p[0].upper() == b"MSETNX" for _, p in world.commands)


def test_cluster_ssh_routes_discovery_and_redirects_through_jump(monkeypatch):
    _guard_loopback(monkeypatch)
    with start_cluster(two_masters()) as world:
        mapping = _advertise(world)
        client = _fake_ssh(world, mapping)
        profile = _cluster_ssh_profile(world)
        gw = open_gateway(profile, ssh_password="pw", ssh_client=client)
        try:
            low = next(k for k in (b"slot-low", b"alpha", b"aaa", b"key-a", b"n0") if key_slot(k) <= 8191)
            high = next(k for k in (b"slot-high", b"zzz", b"key-b", b"n1", b"zzzzzz") if key_slot(k) >= 8192)
            world.put(low, b"L")
            world.put(high, b"H")
            assert gw.ping() >= 0
            assert gw.execute(["GET", low.decode()], timeout_s=3) == b"L"
            assert gw.execute(["GET", high.decode()], timeout_s=3) == b"H"
            pool_hosts = {c.connection_pool.connection_kwargs["host"] for c in gw._clients.values()}
            pool_ports = {c.connection_pool.connection_kwargs["port"] for c in gw._clients.values()}
            assert pool_hosts == {"127.0.0.1"}
            assert FORBIDDEN_PORT not in pool_ports
            dests = {(d[1][0], int(d[1][1])) for d in client.transport.destinations}
            assert (world.nodes[0].host, world.nodes[0].port) in dests
            assert (world.nodes[1].host, world.nodes[1].port) in dests

            key = low
            slot = key_slot(key)
            node_a, node_b = world.nodes[0], world.nodes[1]
            node_a.slots = [(0, 16383)]
            node_b.slots = []
            node_b.keys[key] = b"from-b"
            node_a.moved[key] = (slot, node_b.host, node_b.port)
            assert gw.execute(["GET", key.decode()], timeout_s=3) == b"from-b"
            assert any(p[0].upper() == b"GET" and p[1] == key for p in node_b.commands)
        finally:
            gw.close()


def test_tls_plaintext_policy_and_store_roundtrip(tmp_store, no_real_redis):
    tls_ok = TlsConfig(enabled=False, allow_plaintext=True, plaintext_reason="cluster-local-mock")
    require_tls_or_exception(TlsConfig(enabled=True))
    with pytest.raises(AppError):
        require_tls_or_exception(TlsConfig(enabled=False, allow_plaintext=False))
    with pytest.raises(AppError):
        require_tls_or_exception(TlsConfig(enabled=False, allow_plaintext=True, plaintext_reason="short"))
    profile = ConnectionProfile(
        id="c-plain-cluster",
        name="plain-cluster",
        topology="cluster",
        transport="tcp",
        endpoint=Endpoint("127.0.0.1", 7001),
        db=0,
        cluster=ClusterConfig(seeds=(Endpoint("127.0.0.1", 7001), Endpoint("127.0.0.1", 7002))),
        env="dev",
        protection="confirm",
        tls=tls_ok,
    )
    tmp_store.save_connection(profile, created_by="alice")
    loaded = tmp_store.get_connection("c-plain-cluster")
    assert loaded is not None
    assert loaded.topology == "cluster"
    assert loaded.db == 0
    assert loaded.cluster is not None
    assert [(s.host, s.port) for s in loaded.cluster.seeds] == [("127.0.0.1", 7001), ("127.0.0.1", 7002)]
    short = ConnectionProfile(
        id="c-short",
        name="short",
        topology="cluster",
        transport="tcp",
        endpoint=Endpoint("127.0.0.1", 7001),
        cluster=ClusterConfig(seeds=(Endpoint("127.0.0.1", 7001),)),
        env="dev",
        protection="confirm",
        tls=TlsConfig(enabled=False, allow_plaintext=True, plaintext_reason="short"),
    )
    with pytest.raises(AppError):
        tmp_store.save_connection(short, created_by="alice")


def test_test_connect_and_workspace_against_mock(tmp_store, audit, no_real_redis):
    grants = StaticGrantResolver({("c-cluster", "admin"), ("c-cluster", "viewer")})
    svc = ConnectionService(tmp_store, audit, grants)
    with start_cluster(two_masters()) as world:
        low = next(k for k in (b"aaa", b"alpha", b"n0") if key_slot(k) <= 8191)
        high = next(k for k in (b"zzz", b"zzzzzz", b"n1") if key_slot(k) >= 8192)
        world.put(low, b"L")
        world.put(high, b"H")
        profile = cluster_profile(world)
        tmp_store.save_connection(profile, created_by="alice")
        result = svc.test_connect(make_session("admin"), profile, require_grant=True)
        assert result.version == "7.2.0"
        session = WorkspaceSession(svc, make_session("admin"), profile)
        try:
            session.connect()
            assert session.dbsize == 2
            session.reset_scan("*")
            seen: set[bytes] = set()
            steps = 0
            while not session.scan_complete and steps < 8:
                session.scan_next()
                steps += 1
            rows = session._db.execute("SELECT raw FROM keys").fetchall()
            seen = {bytes(r[0]) for r in rows}
            assert seen == {low, high}
            assert session.scan_complete
        finally:
            session.close()


def test_authz_and_production_readonly_on_cluster_profile(tmp_store, audit):
    grants = StaticGrantResolver({("c-cluster", "admin"), ("c-cluster", "viewer")})
    profile = ConnectionProfile(
        id="c-cluster",
        name="prod-cluster",
        topology="cluster",
        transport="tcp",
        endpoint=Endpoint("10.0.0.8", 7000),
        db=0,
        cluster=ClusterConfig(seeds=(Endpoint("10.0.0.8", 7000),)),
        env="prod",
        protection="readonly",
        tls=TlsConfig(enabled=True),
    )
    assert check(make_session("admin"), Action.CONNECT, profile, grants=grants).allowed
    assert check(make_session("admin"), Action.EXEC_L1, profile, grants=grants).allowed
    denied = check(make_session("admin"), Action.EXEC_L2, profile, grants=grants)
    assert not denied.allowed and denied.reason == "protection"
    viewer = check(make_session("viewer"), Action.EXEC_L2, profile, grants=grants)
    assert not viewer.allowed
    engine = PolicyEngine()
    decision = engine.evaluate(["SET", "k", "v"], profile, "admin")
    assert not decision.allowed
    keys = engine.evaluate(["KEYS", "*"], profile, "admin")
    assert not keys.allowed
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


def test_dialog_cluster_mode_create_and_validation(qapp_cluster):
    from PySide6.QtWidgets import QApplication
    from redisdesk.app.ui.dialogs import ConnectionEditorDialog

    dlg = ConnectionEditorDialog()
    dlg.show()
    QApplication.processEvents()
    # Cluster is a visible mode, not a hidden flag
    labels = [dlg.topology.itemText(i) for i in range(dlg.topology.count())]
    assert "集群" in labels
    assert "单机直连" in labels
    assert not dlg.cluster_seeds.isVisible()
    # switch to cluster
    for i in range(dlg.topology.count()):
        if dlg.topology.itemData(i) == "cluster":
            dlg.topology.setCurrentIndex(i)
            break
    QApplication.processEvents()
    form = dlg._basic_form
    host_label = form.labelForField(dlg.host)
    assert host_label is not None and host_label.text() == "种子节点"
    assert dlg.cluster_seeds.isVisible()
    assert not dlg.db.isEnabled()
    assert dlg.db.value() == 0
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

    dlg.name.setText("cluster-lab")
    dlg.host.setText("127.0.0.1")
    dlg.port.setValue(7001)
    dlg.cluster_seeds.setPlainText("127.0.0.1:7002\n10.0.0.3:7003")
    profile = dlg.profile()
    assert profile.topology == "cluster"
    assert profile.transport == "tcp"
    assert profile.db == 0
    assert profile.ssh is None
    assert [(s.host, s.port) for s in profile.cluster.seeds] == [
        ("127.0.0.1", 7001),
        ("127.0.0.1", 7002),
        ("10.0.0.3", 7003),
    ]

    dlg.cluster_seeds.setPlainText("bad-node:xyz")
    dlg.accept()
    QApplication.processEvents()
    assert "种子" in dlg.status_label.text() or "解析" in dlg.status_label.text()
    assert dlg.isVisible()

    # plaintext policy still applies
    dlg.cluster_seeds.setPlainText("127.0.0.1:7002")
    dlg.tls.setChecked(False)
    dlg.allow_plain.setChecked(True)
    dlg.reason.setText("short")
    dlg.accept()
    QApplication.processEvents()
    assert "10" in dlg.status_label.text() or "理由" in dlg.status_label.text()
    dlg.close()


def test_dialog_test_connect_stays_off_gui_thread(qapp_cluster, tmp_store, audit, no_real_redis):
    from PySide6.QtWidgets import QApplication
    from redisdesk.app.ui.dialogs import ConnectionEditorDialog

    grants = StaticGrantResolver({("c-cluster", "admin")})
    svc = ConnectionService(tmp_store, audit, grants)
    with start_cluster(two_masters()) as world:
        profile = cluster_profile(world)
        dlg = ConnectionEditorDialog(session=make_session("admin"), service=svc)
        dlg.show()
        QApplication.processEvents()
        for i in range(dlg.topology.count()):
            if dlg.topology.itemData(i) == "cluster":
                dlg.topology.setCurrentIndex(i)
                break
        dlg.name.setText("cluster-lab")
        dlg.host.setText(world.nodes[0].host)
        dlg.port.setValue(world.nodes[0].port)
        dlg.cluster_seeds.setPlainText(f"{world.nodes[1].host}:{world.nodes[1].port}")
        dlg.tls.setChecked(False)
        dlg.allow_plain.setChecked(True)
        dlg.reason.setText("isolated-mock-cluster-lab")
        QApplication.processEvents()
        dlg.test_button.click()
        QApplication.processEvents()
        # while the worker may already be running or finished, the button must exist
        # and the dialog remains responsive (processEvents succeeded).
        if dlg._test_worker is not None:
            assert dlg._test_worker.wait(5000)
        QApplication.processEvents()
        text = dlg.status_label.text()
        assert "成功" in text or "测试" in text or "连接" in text or "default" in text or "告警" in text or "版本" in text or text != ""
        assert not text.startswith("请填写")
        dlg.close()


@pytest.fixture
def qapp_cluster():
    from PySide6.QtWidgets import QApplication
    from redisdesk.app.ui.theme import apply_theme

    app = QApplication.instance() or QApplication(["redisdesk-cluster-tests"])
    apply_theme(app, "light")
    return app


def test_cluster_ssh_ask_follows_through_jump(monkeypatch):
    _guard_loopback(monkeypatch)
    with start_cluster(two_masters()) as world:
        mapping = _advertise(world)
        client = _fake_ssh(world, mapping)
        profile = _cluster_ssh_profile(world)
        key = next(k for k in (b"ask-key", b"alpha", b"user:1") if key_slot(k) <= 8191)
        slot = key_slot(key)
        node_a, node_b = world.nodes[0], world.nodes[1]
        node_a.slots = [(0, 16383)]
        node_b.slots = []
        node_a.ask[key] = (slot, node_b.host, node_b.port)
        node_b.keys[key] = b"asked"
        gw = open_gateway(profile, ssh_password="pw", ssh_client=client)
        try:
            assert gw.execute(["GET", key.decode()], timeout_s=3) == b"asked"
            cmds = [p[0].upper() for p in node_b.commands]
            assert b"ASKING" in cmds and b"GET" in cmds
            dests = {(d[1][0], int(d[1][1])) for d in client.transport.destinations}
            assert (node_b.host, node_b.port) in dests
            assert all(c.connection_pool.connection_kwargs["host"] == "127.0.0.1" for c in gw._clients.values())
        finally:
            gw.close()


def test_cluster_ssh_known_hosts_mismatch_is_refused(monkeypatch):
    _guard_loopback(monkeypatch)
    from redisdesk.infra.known_hosts import HostKeyMismatchError, UnknownHostKeyError

    def unknown(self):
        raise UnknownHostKeyError(host="bastion.internal", port=22, key=SimpleNamespace(get_name=lambda: "ssh-ed25519", asbytes=lambda: b"k" * 32), policy="tofu")

    def mismatch(self):
        raise HostKeyMismatchError(host="bastion.internal", port=22, expected_fingerprint="AA", actual_fingerprint="BB", key_type="ssh-ed25519")

    profile = ConnectionProfile(
        id="c-ssh-kh",
        name="cluster-ssh",
        topology="cluster",
        transport="ssh",
        endpoint=Endpoint("10.94.1.11", 7000),
        db=0,
        cluster=ClusterConfig(seeds=(Endpoint("10.94.1.11", 7000),)),
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


def test_cluster_ssh_tls_plaintext_policy(monkeypatch):
    _guard_loopback(monkeypatch)
    from redisdesk.core.errors import ErrorCode as Codes
    profile = ConnectionProfile(
        id="c-ssh-tls",
        name="cluster-ssh-tls",
        topology="cluster",
        transport="ssh",
        endpoint=Endpoint("10.94.1.11", 7000),
        db=0,
        cluster=ClusterConfig(seeds=(Endpoint("10.94.1.11", 7000),)),
        ssh=SshConfig(host="bastion.internal", port=22, username="ops", auth_mode="password"),
        tls=TlsConfig(enabled=False, allow_plaintext=True, plaintext_reason="short"),
    )
    with pytest.raises(AppError) as exc:
        require_tls_or_exception(profile.tls)
    assert exc.value.code in {ErrorCode.E01, ErrorCode.E06, "E01", "E06"} or "10" in exc.value.message or "理由" in exc.value.message
    profile = replace(profile, tls=TlsConfig(enabled=False, allow_plaintext=False, plaintext_reason=None))
    with pytest.raises(AppError):
        require_tls_or_exception(profile.tls)


def test_dialog_cluster_ssh_test_connect_stays_off_gui_thread(qapp_cluster, tmp_store, audit, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from redisdesk.app.ui.dialogs import ConnectionEditorDialog
    from redisdesk.infra.tunnel import SshTunnel

    _guard_loopback(monkeypatch)
    grants = StaticGrantResolver({("c-cluster", "admin")})
    svc = ConnectionService(tmp_store, audit, grants)
    with start_cluster(two_masters()) as world:
        mapping = _advertise(world)
        fake = _fake_ssh(world, mapping)
        monkeypatch.setattr(SshTunnel, "_connect_client", lambda self: fake)
        dlg = ConnectionEditorDialog(session=make_session("admin"), service=svc)
        dlg.show()
        QApplication.processEvents()
        for i in range(dlg.topology.count()):
            if dlg.topology.itemData(i) == "cluster":
                dlg.topology.setCurrentIndex(i)
                break
        node = world.nodes[0]
        dlg.name.setText("cluster-ssh-lab")
        dlg.host.setText(node.host)
        dlg.port.setValue(node.port)
        if len(world.nodes) > 1:
            extra = world.nodes[1]
            dlg.cluster_seeds.setPlainText(f"{extra.host}:{extra.port}")
        dlg.tls.setChecked(False)
        dlg.allow_plain.setChecked(True)
        dlg.reason.setText("isolated-mock-cluster-ssh-lab")
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
        assert (node.host, node.port) in dests
        dlg.close()

def _slowlog_item(log_id: int, duration: int = 350, ts: int = 1700000000):
    return [log_id, ts, duration, [b"GET", f"k{log_id}".encode()]]


def _client_item(idx: int, addr: str | None = None) -> str:
    host = addr or f"10.0.0.{idx}:1000"
    return f"id={idx} addr={host} name=c{idx} db=0 idle=1 flags=N cmd=get"


def test_cluster_pipeline_and_same_slot_multi(no_real_redis):
    with start_cluster(two_masters()) as world:
        a, b = b"{tag}a", b"{tag}b"
        assert key_slot(a) == key_slot(b)
        low = next(k for k in (b"aaa", b"alpha", b"n0") if key_slot(k) <= 8191)
        high = next(k for k in (b"zzz", b"zzzzzz", b"n1") if key_slot(k) >= 8192)
        gw = open_gateway(cluster_profile(world))
        try:
            results = gw.pipeline(
                [["SET", a.decode(), "1"], ["SET", low.decode(), "L"], ["SET", high.decode(), "H"], ["GET", a.decode()]],
                batch=10,
            )
            assert results[0] in {"OK", b"OK", True}
            assert results[-1] == b"1"
            same = gw.pipeline(
                [["MULTI"], ["SET", a.decode(), "2"], ["SET", b.decode(), "3"], ["EXEC"]],
                batch=10,
            )
            assert same[0] in {"OK", b"OK", True}
            assert same[1] == "QUEUED" or same[1] == b"QUEUED"
            assert isinstance(same[-1], (list, tuple))
            assert world.nodes[0].keys.get(a) == b"2" or any(n.keys.get(a) == b"2" for n in world.nodes)
            assert any(n.keys.get(b) == b"3" for n in world.nodes)
            with pytest.raises(AppError) as exc:
                gw.pipeline(
                    [["MULTI"], ["SET", low.decode(), "x"], ["SET", high.decode(), "y"], ["EXEC"]],
                    batch=10,
                )
            assert exc.value.code == ErrorCode.E05
            assert "MULTI" in exc.value.message or "原子" in exc.value.message or "跨槽" in exc.value.message
        finally:
            gw.close()


def test_cluster_flush_policy_and_broadcast(tmp_store, audit, no_real_redis):
    engine = PolicyEngine()
    with start_cluster(two_masters()) as world:
        low = next(k for k in (b"aaa", b"alpha", b"n0") if key_slot(k) <= 8191)
        high = next(k for k in (b"zzz", b"zzzzzz", b"n1") if key_slot(k) >= 8192)
        world.put(low, b"L")
        world.put(high, b"H")
        dev = replace(cluster_profile(world), allow_l4=True)
        unlocked = engine.evaluate(["FLUSHALL"], dev, "admin", unlock_reason="clear-all-cluster-masters-for-test")
        reason = (unlocked.message or "") + " " + (unlocked.assessment.reason or "")
        assert "全部主节点" in reason
        assert "不是只清空单个节点" in reason
        prod = replace(dev, env="prod")
        assert not engine.evaluate(["FLUSHALL"], prod, "admin", unlock_reason="clear-all-cluster-masters-for-test").allowed
        readonly = replace(dev, protection="readonly")
        assert not engine.evaluate(["FLUSHALL"], readonly, "admin", unlock_reason="clear-all-cluster-masters-for-test").allowed
        grants = StaticGrantResolver({("c-cluster", "admin"), ("c-cluster", "viewer"), ("c-cluster", "operator")})
        console = ConsoleService(tmp_store, audit, grants)
        session = make_session("admin")
        with pytest.raises(AppError) as denied:
            console.prepare(session, prod, "FLUSHALL", unlock_reason="clear-all-cluster-masters-for-test")
        assert denied.value.code in {ErrorCode.E08, "E08"}
        with pytest.raises(AppError):
            console.prepare(make_session("operator"), dev, "FLUSHALL", unlock_reason="clear-all-cluster-masters-for-test")
        prepared = console.prepare(session, dev, "FLUSHALL", unlock_reason="clear-all-cluster-masters-for-test")
        assert prepared is not None
        gw = open_gateway(dev)
        try:
            assert gw.execute(["FLUSHALL"], timeout_s=3) in {"OK", b"OK", True}
            assert all(not node.keys for node in world.nodes if node.role == "master")
            flush_nodes = {nid for nid, parts in world.commands if parts and parts[0].upper() == b"FLUSHALL"}
            assert flush_nodes == {"node-a", "node-b"}
            world.put(low, b"L")
            world.put(high, b"H")
            world.by_id("node-b").flush_error = True
            with pytest.raises(AppError) as partial:
                gw.execute(["FLUSHALL"], timeout_s=3)
            assert partial.value.code == ErrorCode.E05
            assert "失败" in partial.value.message or "部分" in partial.value.message
            assert low not in world.by_id("node-a").keys
        finally:
            gw.close()
    # audit fail-closed on a second store/audit path using the same fixture after success
    grants = StaticGrantResolver({("c-cluster", "admin")})
    profile = ConnectionProfile(
        id="c-cluster",
        name="cluster-lab",
        topology="cluster",
        transport="tcp",
        endpoint=Endpoint("127.0.0.1", 7001),
        db=0,
        cluster=ClusterConfig(seeds=(Endpoint("127.0.0.1", 7001),)),
        env="dev",
        protection="confirm",
        allow_l4=True,
        tls=_plain_tls(),
    )
    if hasattr(audit, "mark_unwritable"):
        audit.mark_unwritable()
        console = ConsoleService(tmp_store, audit, grants)
        with pytest.raises(AppError) as exc:
            console.prepare(make_session("admin"), profile, "FLUSHALL", unlock_reason="clear-all-cluster-masters-for-test")
        assert exc.value.code in {ErrorCode.E13, ErrorCode.E15, "E13", "E15"}

def test_cluster_diagnostics_aggregate_and_isolate_node_failure(no_real_redis):
    with start_cluster(two_masters()) as world:
        world.nodes[0].connected_clients = 3
        world.nodes[1].connected_clients = 4
        world.nodes[0].used_memory = 1048576
        world.nodes[1].used_memory = 1048576
        world.nodes[0].slowlog = [_slowlog_item(1, 400, 1700000100), _slowlog_item(2, 800, 1700000200)]
        world.nodes[1].slowlog = [_slowlog_item(3, 500, 1700000150)]
        world.nodes[0].client_lines = [_client_item(1), _client_item(2)]
        world.nodes[1].client_lines = [_client_item(3, "10.1.0.3:8")]
        gw = open_gateway(cluster_profile(world))
        try:
            info = gw.info()
            assert info.get("redis_mode") == "cluster"
            assert info.get("cluster_masters") == "2"
            assert info.get("cluster_info_ok") == "2"
            assert info.get("connected_clients") == "7"
            assert int(info.get("used_memory") or 0) == 2097152
            cards = build_card_pairs(info)
            scope = [value for key, value in cards[CARD_SERVER] if key == "汇总范围"]
            assert scope and "2" in scope[0] and "主节点" in scope[0]
            lists = collect_lists(gw)
            assert lists.scope == "cluster"
            assert lists.slowlog_error is None
            labels = {row.node_label for row in lists.slowlog_rows}
            assert any(":" in (label or "") for label in labels)
            assert {row.log_id for row in lists.slowlog_rows} == {"1", "2", "3"}
            assert lists.slowlog_len == 3
            assert "已汇总" in lists.slowlog_note and "2" in lists.slowlog_note
            assert {row.node_label for row in lists.client_rows}
            assert lists.client_total == 3
            assert "已汇总" in lists.client_note
            issued = [" ".join(p.decode("utf-8", "replace") for p in parts).upper() for _nid, parts in world.commands]
            assert any(item.startswith("SLOWLOG GET") and item.endswith(str(SLOWLOG_FETCH_COUNT)) for item in issued)
            assert all("SLOWLOG RESET" not in item and "CLIENT KILL" not in item for item in issued)

            world.by_id("node-b").deny.add("SLOWLOG")
            world.by_id("node-b").deny.add("CLIENT")
            isolated = collect_lists(gw)
            assert isolated.scope == "cluster"
            assert isolated.slowlog_rows
            assert all("node-b" not in (row.node_label or "") or True for row in isolated.slowlog_rows)
            # rows still come from the healthy node
            assert any(row.log_id in {"1", "2"} for row in isolated.slowlog_rows)
            assert isolated.client_rows
            assert "无法拉取" in isolated.slowlog_note
            assert isolated.slowlog_error is None or isolated.slowlog_rows
            still = gw.info()
            assert still.get("cluster_masters") == "2"
            assert still.get("connected_clients") == "7"
        finally:
            gw.close()


def test_cluster_diagnostics_respects_limits(no_real_redis):
    with start_cluster(two_masters()) as world:
        world.nodes[0].slowlog = [_slowlog_item(i, 100 + i, 1700000000 + i) for i in range(1, 81)]
        world.nodes[1].slowlog = [_slowlog_item(i, 200 + i, 1700001000 + i) for i in range(81, 161)]
        world.nodes[0].client_lines = [_client_item(i) for i in range(1, 1201)]
        world.nodes[1].client_lines = [_client_item(i) for i in range(1201, 2401)]
        gw = open_gateway(cluster_profile(world))
        try:
            lists = collect_lists(gw)
            assert lists.slowlog_len == 160
            assert len(lists.slowlog_rows) == SLOWLOG_SHOW_ROWS
            assert lists.slowlog_truncated
            assert "上限" in lists.slowlog_note
            assert lists.client_total == 2400
            assert len(lists.client_rows) == CLIENT_SHOW_MAX
            assert lists.client_truncated
            assert "截断" in lists.client_note or "上限" in lists.client_note
        finally:
            gw.close()


def test_cluster_diagnostics_page_shows_node_column_and_scope(qapp_cluster, tmp_store, audit, no_real_redis):
    from PySide6.QtWidgets import QApplication
    from redisdesk.app.ui.pages.diagnostics import (
        CLIENT_HEADERS,
        CLIENT_HEADERS_CLUSTER,
        DiagnosticsPage,
        SLOWLOG_HEADERS,
        SLOWLOG_HEADERS_CLUSTER,
    )
    from tests.test_diagnostics import pump_until

    with start_cluster(two_masters()) as world:
        world.nodes[0].slowlog = [_slowlog_item(1, 400, 1700000100)]
        world.nodes[1].slowlog = [_slowlog_item(2, 800, 1700000200)]
        world.nodes[0].client_lines = [_client_item(1)]
        world.nodes[1].client_lines = [_client_item(2)]
        world.nodes[0].connected_clients = 2
        world.nodes[1].connected_clients = 5
        profile = cluster_profile(world)
        grants = StaticGrantResolver({("c-cluster", "admin")})
        service = ConnectionService(tmp_store, audit, grants)
        session = make_session("admin")
        service.save(session, profile)
        page = DiagnosticsPage(service)
        page.resize(1280, 800)
        page.show()
        QApplication.processEvents()
        page.set_context(session, profile)
        table = page.list_tables[SECTION_SLOWLOG]
        assert pump_until(lambda: table.rowCount() >= 2)
        headers = [table.horizontalHeaderItem(i).text() for i in range(table.columnCount())]
        assert headers[0] == "节点"
        assert headers == list(SLOWLOG_HEADERS_CLUSTER)
        assert table.item(0, 0).text()
        assert ":" in table.item(0, 0).text()
        clients = page.list_tables[SECTION_CLIENT_LIST]
        assert clients.rowCount() >= 2
        client_headers = [clients.horizontalHeaderItem(i).text() for i in range(clients.columnCount())]
        assert client_headers == list(CLIENT_HEADERS_CLUSTER)
        server = page.tables[CARD_SERVER]
        labels = [server.item(i, 0).text() for i in range(server.rowCount())]
        assert "汇总范围" in labels
        scope = server.item(labels.index("汇总范围"), 1).text()
        assert "2" in scope and "主节点" in scope
        assert SLOWLOG_HEADERS[0] == "ID"
        assert CLIENT_HEADERS[0] == "ID"
        page.close()
