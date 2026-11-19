"""Redis Cluster gateway: slot routing, MOVED/ASK follow, per-master SCAN.

Uses one redis-py standalone client per node (RESP2, inherited TLS/auth).
This is not a thin wrapper around RedisCluster: cross-slot commands are
rejected, SCAN is per-master, and SELECT is not used.
"""

from __future__ import annotations

import ssl
import time
from typing import Any, Optional

from redisdesk.adapters.direct import classify_connect_error
from redisdesk.adapters.gateway import Capabilities, capabilities_from_version
from redisdesk.adapters.tls import build_ssl_context
from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.limits import (
    CLUSTER_MAX_NODES,
    CLUSTER_SLOT_CACHE_TTL_S,
    CONNECT_TIMEOUT_S,
    HEALTH_CHECK_INTERVAL_S,
    POOL_MAX_CONNECTIONS,
    SOCKET_TIMEOUT_S,
)
from redisdesk.core.model import ClusterConfig, ConnectionProfile, Endpoint, NodeInfo, TlsConfig

CLUSTER_SLOTS = 16384
_MAX_REDIRECTS = 5

# Commands that do not target a data key (run on any master / broadcast as noted).
_ANY_NODE_COMMANDS = {
    "PING", "ECHO", "INFO", "TIME", "ROLE", "COMMAND", "CONFIG", "CLIENT",
    "CLUSTER", "SLOWLOG", "ACL", "MEMORY", "LATENCY", "MODULE", "SCRIPT",
    "LASTSAVE", "LOLWUT", "REPLICAOF", "SLAVEOF", "DEBUG", "MONITOR",
    "DBSIZE", "FLUSHALL", "FLUSHDB", "KEYS", "SCAN", "RANDOMKEY",
}

# argv[1] is the first key; remaining keys vary. Used when we need to route.
_SINGLE_KEY_COMMANDS = {
    "GET", "SET", "SETNX", "SETEX", "PSETEX", "GETSET", "GETDEL", "GETEX",
    "APPEND", "STRLEN", "GETRANGE", "SETRANGE", "INCR", "INCRBY", "INCRBYFLOAT",
    "DECR", "DECRBY", "TTL", "PTTL", "EXPIRE", "PEXPIRE", "EXPIREAT", "PEXPIREAT",
    "PERSIST", "TYPE", "DUMP", "RESTORE", "OBJECT", "TOUCH", "EXISTS",
    "HGET", "HSET", "HSETNX", "HDEL", "HLEN", "HEXISTS", "HKEYS", "HVALS",
    "HGETALL", "HMGET", "HMSET", "HINCRBY", "HINCRBYFLOAT", "HSCAN", "HRANDFIELD",
    "HSTRLEN",
    "LPUSH", "RPUSH", "LPOP", "RPOP", "LLEN", "LINDEX", "LSET", "LRANGE",
    "LTRIM", "LINSERT", "LPOS", "LMOVE",
    "SADD", "SREM", "SCARD", "SISMEMBER", "SMEMBERS", "SSCAN", "SPOP",
    "SRANDMEMBER",
    "ZADD", "ZREM", "ZCARD", "ZSCORE", "ZINCRBY", "ZRANGE", "ZREVRANGE",
    "ZRANGEBYSCORE", "ZREVRANGEBYSCORE", "ZRANK", "ZREVRANK", "ZCOUNT",
    "ZREMRANGEBYRANK", "ZREMRANGEBYSCORE", "ZSCAN", "ZPOPMIN", "ZPOPMAX",
    "ZMSCORE", "ZRANGESTORE",
    "XADD", "XLEN", "XRANGE", "XREVRANGE", "XREAD", "XTRIM", "XDEL", "XACK",
    "XGROUP", "XINFO", "XCLAIM", "XPENDING", "XAUTOCLAIM",
    "PFADD", "PFCOUNT",
    "JSON.GET", "JSON.SET", "JSON.DEL", "JSON.TYPE",
}

_MULTI_KEY_ALL = {"DEL", "UNLINK", "EXISTS", "TOUCH", "MGET", "WATCH"}
_MULTI_KEY_PAIRS = {"MSET", "MSETNX"}


def key_slot(key: bytes | str) -> int:
    if isinstance(key, str):
        key = key.encode("utf-8", errors="surrogateescape")
    try:
        from redis.crc import key_slot as _key_slot
        return int(_key_slot(key))
    except Exception:
        return _fallback_slot(key)


def _fallback_slot(key: bytes) -> int:
    start = key.find(b"{")
    if start >= 0:
        end = key.find(b"}", start + 1)
        if end > start + 1:
            key = key[start + 1 : end]
    crc = 0
    for byte in key:
        crc = ((crc << 8) ^ _CRC16_TAB[((crc >> 8) ^ byte) & 0xFF]) & 0xFFFF
    return crc % CLUSTER_SLOTS


_CRC16_TAB = (
    0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50A5, 0x60C6, 0x70E7,
    0x8108, 0x9129, 0xA14A, 0xB16B, 0xC18C, 0xD1AD, 0xE1CE, 0xF1EF,
    0x1231, 0x0210, 0x3273, 0x2252, 0x52B5, 0x4294, 0x72F7, 0x62D6,
    0x9339, 0x8318, 0xB37B, 0xA35A, 0xD3BD, 0xC39C, 0xF3FF, 0xE3DE,
    0x2462, 0x3443, 0x0420, 0x1401, 0x64E6, 0x74C7, 0x44A4, 0x5485,
    0xA56A, 0xB54B, 0x8528, 0x9509, 0xE5EE, 0xF5CF, 0xC5AC, 0xD58D,
    0x3653, 0x2672, 0x1611, 0x0630, 0x76D7, 0x66F6, 0x5695, 0x46B4,
    0xB75B, 0xA77A, 0x9719, 0x8738, 0xF7DF, 0xE7FE, 0xD79D, 0xC7BC,
    0x48C4, 0x58E5, 0x6886, 0x78A7, 0x0840, 0x1861, 0x2802, 0x3823,
    0xC9CC, 0xD9ED, 0xE98E, 0xF9AF, 0x8948, 0x9969, 0xA90A, 0xB92B,
    0x5AF5, 0x4AD4, 0x7AB7, 0x6A96, 0x1A71, 0x0A50, 0x3A33, 0x2A12,
    0xDBFD, 0xCBDC, 0xFBBF, 0xEB9E, 0x9B79, 0x8B58, 0xBB3B, 0xAB1A,
    0x6CA6, 0x7C87, 0x4CE4, 0x5CC5, 0x2C22, 0x3C03, 0x0C60, 0x1C41,
    0xEDAE, 0xFD8F, 0xCDEC, 0xDDCD, 0xAD2A, 0xBD0B, 0x8D68, 0x9D49,
    0x7E97, 0x6EB6, 0x5ED5, 0x4EF4, 0x3E13, 0x2E32, 0x1E51, 0x0E70,
    0xFF9F, 0xEFBE, 0xDFDD, 0xCFFC, 0xBF1B, 0xAF3A, 0x9F59, 0x8F78,
    0x9188, 0x81A9, 0xB1CA, 0xA1EB, 0xD10C, 0xC12D, 0xF14E, 0xE16F,
    0x1080, 0x00A1, 0x30C2, 0x20E3, 0x5004, 0x4025, 0x7046, 0x6067,
    0x83B9, 0x9398, 0xA3FB, 0xB3DA, 0xC33D, 0xD31C, 0xE37F, 0xF35E,
    0x02B1, 0x1290, 0x22F3, 0x32D2, 0x4235, 0x5214, 0x6277, 0x7256,
    0xB5EA, 0xA5CB, 0x95A8, 0x8589, 0xF56E, 0xE54F, 0xD52C, 0xC50D,
    0x34E2, 0x24C3, 0x14A0, 0x0481, 0x7466, 0x6447, 0x5424, 0x4405,
    0xA7DB, 0xB7FA, 0x8799, 0x97B8, 0xE75F, 0xF77E, 0xC71D, 0xD73C,
    0x26D3, 0x36F2, 0x0691, 0x16B0, 0x6657, 0x7676, 0x4615, 0x5634,
    0xD94C, 0xC96D, 0xF90E, 0xE92F, 0x99C8, 0x89E9, 0xB98A, 0xA9AB,
    0x5844, 0x4865, 0x7806, 0x6827, 0x18C0, 0x08E1, 0x3882, 0x28A3,
    0xCB7D, 0xDB5C, 0xEB3F, 0xFB1E, 0x8BF9, 0x9BD8, 0xABBB, 0xBB9A,
    0x4A75, 0x5A54, 0x6A37, 0x7A16, 0x0AF1, 0x1AD0, 0x2AB3, 0x3A92,
    0xFD2E, 0xED0F, 0xDD6C, 0xCD4D, 0xBDAA, 0xAD8B, 0x9DE8, 0x8DC9,
    0x7C26, 0x6C07, 0x5C64, 0x4C45, 0x3CA2, 0x2C83, 0x1CE0, 0x0CC1,
    0xEF1F, 0xFF3E, 0xCF5D, 0xDF7C, 0xAF9B, 0xBFBA, 0x8FD9, 0x9FF8,
    0x6E17, 0x7E36, 0x4E55, 0x5E74, 0x2E93, 0x3EB2, 0x0ED1, 0x1EF0,
)


def _as_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, memoryview):
        return value.tobytes()
    if value is None:
        return b""
    return str(value).encode("utf-8", errors="surrogateescape")


def _as_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="surrogateescape")
    return "" if value is None else str(value)


def extract_keys(argv: list[str] | list[object]) -> list[bytes]:
    if not argv:
        return []
    name = _as_text(argv[0]).upper()
    if name in _ANY_NODE_COMMANDS and name not in _MULTI_KEY_ALL:
        if name in {"EVAL", "EVALSHA"}:
            pass
        else:
            return []
    if name in {"EVAL", "EVALSHA"}:
        if len(argv) < 3:
            return []
        try:
            nkeys = int(argv[2])
        except (TypeError, ValueError):
            nkeys = 0
        return [_as_bytes(part) for part in argv[3:3 + max(0, nkeys)]]
    if name in _MULTI_KEY_PAIRS:
        return [_as_bytes(argv[i]) for i in range(1, len(argv), 2)]
    if name in _MULTI_KEY_ALL or name in {"SINTER", "SUNION", "SDIFF", "PFCOUNT", "PFMERGE"}:
        return [_as_bytes(part) for part in argv[1:]]
    if name in {"SINTERSTORE", "SUNIONSTORE", "SDIFFSTORE", "ZUNIONSTORE", "ZINTERSTORE", "ZDIFFSTORE"}:
        return [_as_bytes(part) for part in argv[1:] if not str(part).isdigit()][:32]
    if name == "XREAD" or name == "XREADGROUP":
        parts = [_as_text(p) for p in argv]
        try:
            idx = parts.index("STREAMS")
        except ValueError:
            return []
        rest = argv[idx + 1:]
        return [_as_bytes(part) for part in rest[: len(rest) // 2]]
    if len(argv) >= 2:
        return [_as_bytes(argv[1])]
    return []


def parse_cluster_slots(raw: object) -> list[NodeInfo]:
    nodes: dict[str, NodeInfo] = {}
    if not raw:
        return []
    rows = raw if isinstance(raw, (list, tuple)) else []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        try:
            start, end = int(row[0]), int(row[1])
        except (TypeError, ValueError):
            continue
        for index, node in enumerate(row[2:]):
            if not isinstance(node, (list, tuple)) or len(node) < 2:
                continue
            host = _as_text(node[0])
            try:
                port = int(node[1])
            except (TypeError, ValueError):
                continue
            if not host:
                continue
            node_id = _as_text(node[2]) if len(node) > 2 and node[2] else f"{host}:{port}"
            role = "master" if index == 0 else "slave"
            current = nodes.get(node_id)
            slots = list(current.slots) if current is not None else []
            if role == "master":
                slots.append((start, end))
            nodes[node_id] = NodeInfo(
                node_id=node_id,
                host=host,
                port=port,
                role=role if current is None else current.role,
                slots=tuple(slots) if role == "master" or (current is not None and current.role == "master") else current.slots if current else (),
            )
            if current is not None and current.role == "master":
                nodes[node_id] = NodeInfo(
                    node_id=node_id, host=host, port=port, role="master",
                    slots=tuple(slots) if slots else current.slots,
                )
    return list(nodes.values())


def parse_redirect(exc: BaseException) -> tuple[str, int, str, int] | None:
    """Return (kind, slot, host, port) for MOVED/ASK, else None."""
    kind = None
    slot = None
    host = None
    port = None
    name = type(exc).__name__
    if name == "MovedError":
        kind = "MOVED"
    elif name == "AskError":
        kind = "ASK"
    msg = str(exc).strip()
    head = msg.split(None, 1)[0].upper() if msg else ""
    if head in {"MOVED", "ASK"}:
        kind = head
    if kind is None:
        return None
    slot = getattr(exc, "slot_id", None)
    host = getattr(exc, "host", None)
    port = getattr(exc, "port", None)
    parts = msg.split()
    # "MOVED 3999 127.0.0.1:7002" or redis-py str form
    if len(parts) >= 3 and parts[0].upper() in {"MOVED", "ASK"}:
        try:
            slot = int(parts[1])
        except ValueError:
            pass
        loc = parts[2]
        if ":" in loc:
            host, port_text = loc.rsplit(":", 1)
            try:
                port = int(port_text)
            except ValueError:
                pass
    if kind and slot is not None and host and port is not None:
        return kind, int(slot), str(host), int(port)
    return None


def cluster_error(message: str, *, hint: str = "") -> AppError:
    return AppError(ErrorCode.E05, message, hint=hint)


class ClusterGateway:
    def __init__(
        self,
        profile: ConnectionProfile,
        *,
        password: memoryview | bytes | bytearray | None = None,
        connect_timeout_s: float = CONNECT_TIMEOUT_S,
        socket_timeout_s: float = SOCKET_TIMEOUT_S,
        pool_max: int = POOL_MAX_CONNECTIONS,
        slot_ttl_s: float | None = None,
    ) -> None:
        if profile.transport == "ssh":
            raise AppError(
                ErrorCode.E07,
                "\u5f53\u524d\u7248\u672c\u7684\u96c6\u7fa4\u8fde\u63a5\u6682\u4e0d\u652f\u6301 SSH \u8df3\u677f\uff08\u91cd\u5b9a\u5411\u4f1a\u8fde\u5230\u5176\u4ed6\u8282\u70b9\uff0c\u9700\u8981\u6309\u8282\u70b9\u6620\u5c04\uff09\u3002\u8bf7\u6539\u7528 TCP \u76f4\u8fde\u3002",
            )
        self.profile = profile
        self.capabilities: Capabilities = capabilities_from_version("0.0.0", cluster=True)
        self._password = password
        self._connect_timeout_s = connect_timeout_s
        self._socket_timeout_s = socket_timeout_s
        self._pool_max = pool_max
        cfg = profile.cluster or ClusterConfig()
        self._slot_ttl_s = float(slot_ttl_s if slot_ttl_s is not None else (cfg.slot_cache_ttl_s or CLUSTER_SLOT_CACHE_TTL_S))
        self._clients: dict[str, Any] = {}
        self._nodes: dict[str, NodeInfo] = {}
        self._slot_owner: list[str | None] = [None] * CLUSTER_SLOTS
        self._loaded_at = 0.0
        try:
            self._discover()
            self.discover_capabilities()
        except Exception:
            self.close()
            raise

    def _seeds(self) -> list[Endpoint]:
        seeds: list[Endpoint] = []
        if self.profile.cluster and self.profile.cluster.seeds:
            seeds.extend(self.profile.cluster.seeds)
        if self.profile.endpoint and self.profile.endpoint.host:
            primary = self.profile.endpoint
            if not any(s.host == primary.host and s.port == primary.port for s in seeds):
                seeds.insert(0, primary)
        # unique preserve order
        out: list[Endpoint] = []
        seen: set[tuple[str, int]] = set()
        for seed in seeds:
            key = (seed.host, int(seed.port))
            if key in seen or not seed.host:
                continue
            seen.add(key)
            out.append(Endpoint(seed.host, int(seed.port)))
        return out

    def _build_client(self, host: str, port: int) -> Any:
        try:
            import redis
            from redis.retry import Retry
            from redis.backoff import NoBackoff
        except ImportError as exc:  # pragma: no cover
            raise AppError(ErrorCode.E12, "\u672a\u5b89\u88c5 redis-py") from exc
        tls = self.profile.tls or TlsConfig()
        ssl_ctx = build_ssl_context(tls)
        passwd: Optional[str] = None
        password = self._password
        if password is not None:
            raw = password.tobytes() if isinstance(password, memoryview) else bytes(password)
            passwd = raw.decode("utf-8", errors="surrogateescape")
        kwargs: dict[str, Any] = {
            "host": host,
            "port": int(port),
            "db": 0,
            "username": self.profile.username or None,
            "password": passwd,
            "socket_connect_timeout": self._connect_timeout_s,
            "socket_timeout": self._socket_timeout_s,
            "health_check_interval": HEALTH_CHECK_INTERVAL_S,
            "max_connections": self._pool_max,
            "protocol": 2,
            "decode_responses": False,
            "retry": Retry(NoBackoff(), 0),
            "retry_on_timeout": False,
        }
        if ssl_ctx is not None:
            kwargs.update(
                {
                    "ssl": True,
                    "ssl_cert_reqs": "required",
                    "ssl_check_hostname": True,
                    "ssl_ca_certs": tls.ca_path,
                    "ssl_certfile": tls.client_cert_path,
                    "ssl_keyfile": tls.client_key_path,
                    "ssl_min_version": ssl_ctx.minimum_version,
                }
            )
        try:
            return redis.Redis(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise classify_connect_error(
                exc,
                host=host,
                port=int(port),
                tls_enabled=bool(tls.enabled),
            ) from exc
        finally:
            passwd = None

    def _client_for(self, node: NodeInfo) -> Any:
        existing = self._clients.get(node.node_id)
        if existing is not None:
            return existing
        if len(self._clients) >= CLUSTER_MAX_NODES:
            raise cluster_error(f"\u96c6\u7fa4\u8282\u70b9\u8d85\u8fc7\u4e0a\u9650\uff08{CLUSTER_MAX_NODES}\uff09\uff0c\u5df2\u505c\u6b62\u65b0\u5efa\u8fde\u63a5\u3002")
        client = self._build_client(node.host, node.port)
        self._clients[node.node_id] = client
        return client

    def _discover(self) -> None:
        last_error: BaseException | None = None
        seeds = self._seeds()
        if not seeds:
            raise AppError(ErrorCode.E01, "\u8bf7\u586b\u5199\u96c6\u7fa4\u79cd\u5b50\u8282\u70b9\u3002")
        for seed in seeds:
            client = None
            try:
                client = self._build_client(seed.host, seed.port)
                raw = client.execute_command("CLUSTER", "SLOTS")
                nodes = parse_cluster_slots(raw)
                if not nodes:
                    raise cluster_error("CLUSTER SLOTS \u672a\u8fd4\u56de\u4efb\u4f55\u8282\u70b9\u3002")
                self._apply_nodes(nodes)
                seed_id = f"{seed.host}:{seed.port}"
                # keep the working seed client if it matches a node
                matched = None
                for node in nodes:
                    if node.host == seed.host and node.port == seed.port:
                        matched = node.node_id
                        break
                if matched:
                    self._clients[matched] = client
                    client = None
                return
            except AppError as exc:
                last_error = exc
            except Exception as exc:  # noqa: BLE001
                text = str(exc).lower()
                if "cluster support disabled" in text or "this instance has cluster support disabled" in text:
                    last_error = cluster_error(
                        f"{seed.host}:{seed.port} \u4e0d\u662f\u96c6\u7fa4\u8282\u70b9\uff08\u672a\u5f00\u542f cluster\uff09\u3002",
                    )
                elif "clusterdown" in text:
                    last_error = cluster_error("\u96c6\u7fa4\u5f53\u524d\u4e0d\u53ef\u7528\uff08CLUSTERDOWN\uff09\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002")
                else:
                    last_error = classify_connect_error(
                        exc,
                        host=seed.host,
                        port=seed.port,
                        tls_enabled=bool(self.profile.tls and self.profile.tls.enabled),
                        command="CLUSTER SLOTS",
                    )
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass
        if isinstance(last_error, AppError):
            raise last_error
        raise cluster_error("\u65e0\u6cd5\u4ece\u79cd\u5b50\u8282\u70b9\u53d1\u73b0\u96c6\u7fa4\u62d3\u6251\u3002")

    def _apply_nodes(self, nodes: list[NodeInfo]) -> None:
        owners: list[str | None] = [None] * CLUSTER_SLOTS
        mapped: dict[str, NodeInfo] = {}
        for node in nodes:
            mapped[node.node_id] = node
            if node.role != "master":
                continue
            for start, end in node.slots:
                lo = max(0, int(start))
                hi = min(CLUSTER_SLOTS - 1, int(end))
                for slot in range(lo, hi + 1):
                    owners[slot] = node.node_id
        self._nodes = mapped
        self._slot_owner = owners
        self._loaded_at = time.monotonic()

    def _refresh_slots(self, *, force: bool = False) -> None:
        if not force and self._loaded_at and (time.monotonic() - self._loaded_at) < self._slot_ttl_s:
            return
        master = self._any_master()
        raw = self._client_for(master).execute_command("CLUSTER", "SLOTS")
        self._apply_nodes(parse_cluster_slots(raw))

    def _any_master(self) -> NodeInfo:
        for node in self._nodes.values():
            if node.role == "master":
                return node
        if self._nodes:
            return next(iter(self._nodes.values()))
        seeds = self._seeds()
        if not seeds:
            raise cluster_error("\u96c6\u7fa4\u6ca1\u6709\u53ef\u7528\u8282\u70b9\u3002")
        return NodeInfo(node_id=f"{seeds[0].host}:{seeds[0].port}", host=seeds[0].host, port=seeds[0].port)

    def _master_for_slot(self, slot: int) -> NodeInfo:
        self._refresh_slots()
        node_id = self._slot_owner[slot] if 0 <= slot < CLUSTER_SLOTS else None
        if not node_id or node_id not in self._nodes:
            self._refresh_slots(force=True)
            node_id = self._slot_owner[slot] if 0 <= slot < CLUSTER_SLOTS else None
        if not node_id or node_id not in self._nodes:
            raise cluster_error(f"\u69fd {slot} \u6682\u65e0\u4e3b\u8282\u70b9\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002")
        node = self._nodes[node_id]
        if node.role != "master":
            raise cluster_error(f"\u69fd {slot} \u7684\u8def\u7531\u4e0d\u662f\u4e3b\u8282\u70b9\u3002")
        return node

    def _ensure_node(self, host: str, port: int) -> NodeInfo:
        for node in self._nodes.values():
            if node.host == host and node.port == port:
                return node
        node_id = f"{host}:{port}"
        node = NodeInfo(node_id=node_id, host=host, port=int(port), role="master")
        self._nodes[node_id] = node
        return node

    def _apply_moved(self, slot: int, host: str, port: int) -> NodeInfo:
        node = self._ensure_node(host, port)
        if 0 <= slot < CLUSTER_SLOTS:
            self._slot_owner[slot] = node.node_id
        return node

    def discover_capabilities(self) -> Capabilities:
        info = self.info("server")
        version = info.get("redis_version") or info.get("redis-version") or "0.0.0"
        self.capabilities = capabilities_from_version(version, cluster=True)
        return self.capabilities

    def ping(self) -> float:
        started = time.perf_counter()
        try:
            self._client_for(self._any_master()).ping()
        except Exception as exc:  # noqa: BLE001
            raise self._map(exc, command="PING") from exc
        return time.perf_counter() - started

    def info(self, section: str | None = None) -> dict[str, str]:
        master = self._any_master()
        try:
            raw = self._client_for(master).info(section) if section else self._client_for(master).info()
        except Exception as exc:  # noqa: BLE001
            raise self._map(exc, command="INFO") from exc
        out: dict[str, str] = {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                out[str(key)] = value if isinstance(value, str) else str(value)
        out.setdefault("redis_mode", "cluster")
        try:
            extra = self._client_for(master).execute_command("CLUSTER", "INFO")
            text = _as_text(extra)
            for line in text.splitlines():
                if ":" in line and not line.startswith("#"):
                    key, value = line.split(":", 1)
                    out.setdefault(key.strip(), value.strip())
        except Exception:
            pass
        out["cluster_masters"] = str(len(self.masters()))
        return out

    def masters(self) -> list[NodeInfo]:
        self._refresh_slots()
        return [node for node in self._nodes.values() if node.role == "master"]

    def nodes(self) -> list[NodeInfo]:
        self._refresh_slots()
        masters = [node for node in self._nodes.values() if node.role == "master"]
        replicas = [node for node in self._nodes.values() if node.role != "master"]
        return masters + replicas

    def scan(
        self,
        node_id: str | None,
        cursor: int,
        *,
        match: str | None,
        count: int,
        key_type: str | None,
    ) -> tuple[int, list[bytes], str | None]:
        masters = self.masters()
        if not masters:
            raise cluster_error("\u96c6\u7fa4\u6ca1\u6709\u53ef\u626b\u63cf\u7684\u4e3b\u8282\u70b9\u3002")
        if node_id:
            node = self._nodes.get(node_id)
            if node is None:
                raise cluster_error(f"\u672a\u77e5\u7684\u96c6\u7fa4\u8282\u70b9\uff1a{node_id}")
            return self._scan_node(node, cursor, match=match, count=count, key_type=key_type)
        # Composite cursor: high 16 bits = master index, low 48 bits = node cursor.
        index, node_cursor = _split_cursor(cursor, len(masters))
        keys: list[bytes] = []
        while index < len(masters):
            nxt, batch, _ = self._scan_node(
                masters[index],
                node_cursor,
                match=match,
                count=count,
                key_type=key_type,
            )
            keys.extend(batch)
            if nxt != 0:
                return _join_cursor(index, nxt), keys, masters[index].node_id
            index += 1
            node_cursor = 0
            if keys:
                # Finished this master; let the caller paginate onto the next one.
                if index >= len(masters):
                    return 0, keys, None
                return _join_cursor(index, 0), keys, masters[index].node_id
        return 0, keys, None

    def _scan_node(
        self,
        node: NodeInfo,
        cursor: int,
        *,
        match: str | None,
        count: int,
        key_type: str | None,
    ) -> tuple[int, list[bytes], str | None]:
        kwargs: dict[str, Any] = {"cursor": int(cursor), "count": count}
        if match:
            kwargs["match"] = match
        if key_type and self.capabilities.supports_scan_type:
            kwargs["_type"] = key_type
        try:
            next_cursor, keys = self._client_for(node).scan(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise self._map(exc, command="SCAN") from exc
        items = [k if isinstance(k, bytes) else str(k).encode("utf-8") for k in keys]
        return int(next_cursor), items, node.node_id

    def execute(self, argv: list[str], *, timeout_s: float) -> Any:
        if not argv:
            raise AppError(ErrorCode.E10, "\u7a7a\u547d\u4ee4")
        name = argv[0].upper()
        if name == "SELECT":
            raise AppError(ErrorCode.E08, "\u96c6\u7fa4\u4e0d\u652f\u6301 SELECT\uff0c\u8bf7\u4f7f\u7528 db 0\u3002")
        if name == "DBSIZE":
            return self.dbsize()
        keys = extract_keys(argv)
        if len(keys) > 1:
            slots = {key_slot(key) for key in keys}
            if len(slots) > 1:
                raise cluster_error(
                    "\u8be5\u547d\u4ee4\u6d89\u53ca\u591a\u4e2a\u69fd\u7684 key\uff0c\u96c6\u7fa4\u4e0d\u5141\u8bb8\u5728\u4e00\u6b21\u8bf7\u6c42\u4e2d\u8de8\u69fd\u6267\u884c\u3002\u8bf7\u6539\u4e3a\u5355\u4e2a key\uff0c\u6216\u6309\u69fd\u62c6\u5206\u540e\u518d\u8bd5\u3002",
                    hint=", ".join(f"{_as_text(k)}->{key_slot(k)}" for k in keys[:8]),
                )
        target: NodeInfo | None = None
        if keys:
            target = self._master_for_slot(key_slot(keys[0]))
        return self._send(argv, node=target, timeout_s=timeout_s)

    def dbsize(self) -> int:
        total = 0
        for node in self.masters():
            try:
                raw = self._send(["DBSIZE"], node=node, timeout_s=self._socket_timeout_s, allow_redirect=True)
                total += int(raw)
            except Exception as exc:  # noqa: BLE001
                raise self._map(exc, command="DBSIZE") from exc
        return total

    def pipeline(self, commands: list[list[str]], *, batch: int) -> list[Any]:
        # Non-transactional only. Each command is routed independently.
        results: list[Any] = []
        for argv in commands:
            results.append(self.execute(argv, timeout_s=self._socket_timeout_s))
        return results

    def route(self, keys: list[bytes]) -> dict[str, list[bytes]]:
        grouped: dict[str, list[bytes]] = {}
        for key in keys:
            node = self._master_for_slot(key_slot(key))
            grouped.setdefault(node.node_id, []).append(key)
        return grouped

    def _send(
        self,
        argv: list[str],
        *,
        node: NodeInfo | None,
        timeout_s: float,
        allow_redirect: bool = True,
        asking: bool = False,
    ) -> Any:
        current = node or self._any_master()
        redirects = 0
        while True:
            client = self._client_for(current)
            try:
                return self._raw_execute(client, argv, timeout_s=timeout_s, asking=asking)
            except Exception as exc:  # noqa: BLE001
                redirect = parse_redirect(exc) if allow_redirect else None
                if redirect is None:
                    text = str(exc).lower()
                    if "clusterdown" in text:
                        raise cluster_error("\u96c6\u7fa4\u5f53\u524d\u4e0d\u53ef\u7528\uff08CLUSTERDOWN\uff09\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5\u3002") from exc
                    if "crossslot" in text:
                        raise cluster_error("\u8de8\u69fd\u547d\u4ee4\u88ab\u670d\u52a1\u7aef\u62d2\u7edd\u3002") from exc
                    raise self._map(exc, command=argv[0] if argv else "") from exc
                kind, slot, host, port = redirect
                redirects += 1
                if redirects > _MAX_REDIRECTS:
                    raise cluster_error(f"\u8ddf\u968f MOVED/ASK \u91cd\u5b9a\u5411\u6b21\u6570\u8fc7\u591a\uff08\u69fd {slot}\uff09\u3002") from exc
                if kind == "MOVED":
                    current = self._apply_moved(slot, host, port)
                    asking = False
                    if redirects == 2:
                        try:
                            self._refresh_slots(force=True)
                            current = self._master_for_slot(slot)
                        except AppError:
                            pass
                else:
                    current = self._ensure_node(host, port)
                    asking = True

    def _raw_execute(self, client: Any, argv: list[str], *, timeout_s: float, asking: bool) -> Any:
        conn = client.connection_pool.get_connection(argv[0])
        old = getattr(conn, "socket_timeout", None)
        sock = getattr(conn, "_sock", None)
        try:
            conn.socket_timeout = timeout_s
            if sock is not None:
                sock.settimeout(timeout_s)
            if asking:
                conn.send_command("ASKING")
                client.parse_response(conn, "ASKING")
            conn.send_command(*argv)
            return client.parse_response(conn, argv[0])
        except BaseException:
            conn.disconnect()
            raise
        finally:
            conn.socket_timeout = old
            if getattr(conn, "_sock", None) is not None:
                conn._sock.settimeout(old)
            client.connection_pool.release(conn)

    def _map(self, exc: BaseException, command: str = "") -> AppError:
        if isinstance(exc, AppError):
            return exc
        return classify_connect_error(
            exc,
            host=self.profile.host,
            port=self.profile.port,
            tls_enabled=bool(self.profile.tls and self.profile.tls.enabled),
            command=command,
        )

    def close(self) -> None:
        for client in list(self._clients.values()):
            try:
                client.close()
            except Exception:
                pass
        self._clients.clear()


def _split_cursor(cursor: int, master_count: int) -> tuple[int, int]:
    value = max(0, int(cursor))
    index = (value >> 48) & 0xFFFF
    node_cursor = value & ((1 << 48) - 1)
    if index >= max(1, master_count) and node_cursor == 0:
        return master_count, 0
    return index, node_cursor


def _join_cursor(index: int, node_cursor: int) -> int:
    return ((int(index) & 0xFFFF) << 48) | (int(node_cursor) & ((1 << 48) - 1))
