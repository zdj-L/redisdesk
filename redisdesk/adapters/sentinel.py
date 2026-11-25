"""Sentinel discovery and master following.

Talks to Sentinel nodes with explicit SENTINEL commands (RESP2), then opens a
DirectGateway to the discovered master. This is not a thin redis.sentinel wrapper.
"""

from __future__ import annotations

import ssl
import time
from dataclasses import replace
from typing import Any, Callable, Optional, TypeVar

from redisdesk.adapters.direct import DirectGateway, _get_tunnel_ssl_connection_class, classify_connect_error
from redisdesk.adapters.gateway import Capabilities, capabilities_from_version
from redisdesk.adapters.tls import build_ssl_context
from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.limits import (
    CONNECT_TIMEOUT_S,
    HEALTH_CHECK_INTERVAL_S,
    POOL_MAX_CONNECTIONS,
    SENTINEL_DISCOVER_BACKOFF_MS,
    SENTINEL_DISCOVER_RETRY,
    SENTINEL_PORT,
    SOCKET_TIMEOUT_S,
)
from redisdesk.core.model import ConnectionProfile, Endpoint, NodeInfo, TlsConfig

T = TypeVar("T")

_READ_COMMANDS = {
    "GET", "MGET", "EXISTS", "TYPE", "TTL", "PTTL", "STRLEN", "GETRANGE",
    "HGET", "HMGET", "HKEYS", "HVALS", "HLEN", "HEXISTS", "HSCAN", "HSTRLEN", "HRANDFIELD",
    "LLEN", "LINDEX", "LRANGE", "LPOS",
    "SCARD", "SISMEMBER", "SSCAN", "SRANDMEMBER",
    "ZCARD", "ZSCORE", "ZRANGE", "ZREVRANGE", "ZRANGEBYSCORE", "ZREVRANGEBYSCORE",
    "ZRANK", "ZREVRANK", "ZCOUNT", "ZSCAN", "ZMSCORE",
    "XLEN", "XRANGE", "XREVRANGE", "XINFO", "XPENDING",
    "PFCOUNT",
    "SCAN", "DBSIZE", "INFO", "PING", "ECHO", "TIME", "ROLE", "SLOWLOG",
    "MEMORY", "OBJECT", "DUMP", "LASTSAVE", "LOLWUT", "ACL",
}


def _as_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="surrogateescape")
    return str(value)


def _is_read(argv: list[str]) -> bool:
    if not argv:
        return False
    name = argv[0].upper()
    if name == "CONFIG":
        return len(argv) > 1 and argv[1].upper() == "GET"
    if name == "CLIENT":
        return len(argv) > 1 and argv[1].upper() in {"LIST", "INFO", "GETNAME", "ID"}
    if name == "COMMAND":
        return True
    if name == "SENTINEL":
        sub = argv[1].upper() if len(argv) > 1 else ""
        return sub in {"GET-MASTER-ADDR-BY-NAME", "MASTERS", "MASTER", "REPLICAS", "SLAVES", "SENTINELS", "CKQUORUM", "INFO"}
    return name in _READ_COMMANDS


def _is_failover_trigger(exc: BaseException) -> bool:
    if isinstance(exc, AppError):
        text = f"{exc.message} {exc.hint}".lower()
        if "readonly" in text or "read only" in text or "\u53ea\u8bfb" in exc.message:
            return True
        if exc.code in {ErrorCode.E01, ErrorCode.E03} and exc.retryable:
            return True
        if exc.code == ErrorCode.E03:
            return True
        if exc.code == ErrorCode.E01 and any(k in text for k in ("\u62d2\u7edd", "\u5931\u8d25", "timeout", "timed out")):
            return True
        return False
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "readonly" in text or "read only" in text:
        return True
    if "masterdown" in text or "loading redis" in text or "loading" in text:
        return True
    if any(k in name for k in ("connection", "timeout")):
        return True
    if any(k in text for k in ("connection refused", "timed out", "timeout", "connection reset", "broken pipe")):
        return True
    return False


def sentinel_error(message: str, *, hint: str = "", retryable: bool = False) -> AppError:
    return AppError(ErrorCode.E06, message, hint=hint, retryable=retryable)


class SentinelGateway:
    def __init__(
        self,
        profile: ConnectionProfile,
        *,
        password: memoryview | bytes | bytearray | None = None,
        sentinel_password: memoryview | bytes | bytearray | str | None = None,
        connect_timeout_s: float = CONNECT_TIMEOUT_S,
        socket_timeout_s: float = SOCKET_TIMEOUT_S,
        pool_max: int = POOL_MAX_CONNECTIONS,
        jump=None,
    ) -> None:
        self._jump = jump
        cfg = profile.sentinel
        if cfg is None or not (cfg.master_name or "").strip():
            raise sentinel_error("\u8bf7\u586b\u5199\u54e8\u5175\u76d1\u63a7\u7684\u4e3b\u8282\u70b9\u540d\u79f0\u3002")
        if not self._seed_list(profile):
            raise sentinel_error("\u8bf7\u586b\u5199\u81f3\u5c11\u4e00\u4e2a\u54e8\u5175\u8282\u70b9\u3002")
        self.profile = profile
        self.capabilities: Capabilities = capabilities_from_version("0.0.0")
        self._password = password
        self._sentinel_password = sentinel_password
        self._connect_timeout_s = connect_timeout_s
        self._socket_timeout_s = socket_timeout_s
        self._pool_max = pool_max
        self._master_gw: DirectGateway | None = None
        self._master_endpoint: Endpoint | None = None
        self._last_switch_message = ""
        self._sentinel_info: dict[str, str] = {}
        try:
            self._discover_and_connect(force=True)
        except Exception:
            self.close()
            raise

    def _seed_list(self, profile: ConnectionProfile | None = None) -> list[Endpoint]:
        profile = profile or self.profile
        seeds: list[Endpoint] = []
        cfg = profile.sentinel
        if cfg and cfg.sentinels:
            seeds.extend(cfg.sentinels)
        if profile.endpoint and profile.endpoint.host:
            primary = Endpoint(profile.endpoint.host, int(profile.endpoint.port or SENTINEL_PORT))
            if not any(s.host == primary.host and s.port == primary.port for s in seeds):
                seeds.insert(0, primary)
        out: list[Endpoint] = []
        seen: set[tuple[str, int]] = set()
        for seed in seeds:
            port = int(seed.port or SENTINEL_PORT)
            key = (seed.host, port)
            if not seed.host or key in seen:
                continue
            seen.add(key)
            out.append(Endpoint(seed.host, port))
        return out

    def _sentinel_tls(self) -> TlsConfig:
        cfg = self.profile.sentinel
        if cfg is not None and cfg.tls is not None:
            return cfg.tls
        return self.profile.tls or TlsConfig()

    def _build_sentinel_client(self, host: str, port: int) -> Any:
        try:
            import redis
            from redis.retry import Retry
            from redis.backoff import NoBackoff
        except ImportError as exc:  # pragma: no cover
            raise AppError(ErrorCode.E12, "\u672a\u5b89\u88c5 redis-py") from exc
        tls = self._sentinel_tls()
        ssl_ctx = build_ssl_context(tls)
        cfg = self.profile.sentinel
        passwd: Optional[str] = None
        secret = self._sentinel_password
        if secret is not None:
            if isinstance(secret, str):
                passwd = secret or None
            else:
                raw = secret.tobytes() if isinstance(secret, memoryview) else bytes(secret)
                passwd = raw.decode("utf-8", errors="surrogateescape") if raw else None
        logical_host, logical_port = host, int(port)
        connect_host, connect_port = logical_host, logical_port
        jump = getattr(self, "_jump", None)
        if jump is not None:
            connect_host, connect_port = jump.endpoint(logical_host, logical_port)
        kwargs: dict[str, Any] = {
            "host": connect_host,
            "port": int(connect_port),
            "db": 0,
            "username": (cfg.sentinel_username if cfg else None) or None,
            "password": passwd,
            "socket_connect_timeout": self._connect_timeout_s,
            "socket_timeout": self._socket_timeout_s,
            "health_check_interval": HEALTH_CHECK_INTERVAL_S,
            "max_connections": 2,
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
            if jump is not None:
                kwargs["connection_class"] = _get_tunnel_ssl_connection_class()
                kwargs["tls_server_hostname"] = logical_host
        try:
            return redis.Redis(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise classify_connect_error(exc, host=logical_host, port=int(logical_port), tls_enabled=bool(tls.enabled)) from exc
        finally:
            passwd = None

    def _query_master_addr(self) -> tuple[str, int]:
        name = (self.profile.sentinel.master_name if self.profile.sentinel else "").strip()
        failures: list[str] = []
        saw_nil = False
        saw_reachable = False
        for seed in self._seed_list():
            client = None
            loc = f"{seed.host}:{seed.port}"
            try:
                client = self._build_sentinel_client(seed.host, seed.port)
                client.ping()
                saw_reachable = True
                raw = client.execute_command("SENTINEL", "GET-MASTER-ADDR-BY-NAME", name)
                if raw in (None, [], b"", ""):
                    saw_nil = True
                    failures.append(f"{loc} \u672a\u767b\u8bb0\u4e3b\u8282\u70b9 {name}")
                    continue
                if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                    failures.append(f"{loc} \u8fd4\u56de\u65e0\u6548\u5730\u5740")
                    continue
                host = _as_text(raw[0]).strip()
                try:
                    port = int(_as_text(raw[1]))
                except (TypeError, ValueError):
                    failures.append(f"{loc} \u8fd4\u56de\u65e0\u6548\u7aef\u53e3")
                    continue
                if not host or port <= 0:
                    failures.append(f"{loc} \u8fd4\u56de\u65e0\u6548\u5730\u5740")
                    continue
                self._remember_sentinel_master(client, name, host, port)
                return host, port
            except AppError as exc:
                failures.append(f"{loc} {exc.message}")
            except Exception as exc:  # noqa: BLE001
                mapped = classify_connect_error(
                    exc,
                    host=seed.host,
                    port=seed.port,
                    tls_enabled=bool(self._sentinel_tls().enabled),
                    command="SENTINEL GET-MASTER-ADDR-BY-NAME",
                )
                failures.append(f"{loc} {mapped.message}")
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass
        hint = "\uff1b".join(failures)
        if saw_nil and not any("\u672a\u767b\u8bb0" not in item for item in failures if item):
            raise sentinel_error(
                f"\u54e8\u5175\u4e2d\u672a\u627e\u5230\u4e3b\u8282\u70b9\u300c{name}\u300d\u3002\u8bf7\u6838\u5bf9\u4e3b\u8282\u70b9\u540d\u79f0\u3002",
                hint=hint,
                retryable=False,
            )
        if not saw_reachable:
            raise sentinel_error(
                "\u5168\u90e8\u54e8\u5175\u4e0d\u53ef\u8fbe\uff0c\u65e0\u6cd5\u53d1\u73b0\u5f53\u524d\u4e3b\u8282\u70b9\u3002",
                hint=hint,
                retryable=True,
            )
        raise sentinel_error("\u65e0\u6cd5\u4ece\u54e8\u5175\u53d1\u73b0\u5f53\u524d\u4e3b\u8282\u70b9\u3002", hint=hint, retryable=True)

    def _remember_sentinel_master(self, client: Any, name: str, host: str, port: int) -> None:
        info = {
            "sentinel_master_name": name,
            "sentinel_master_host": host,
            "sentinel_master_port": str(port),
        }
        try:
            raw = client.execute_command("SENTINEL", "MASTER", name)
            if isinstance(raw, (list, tuple)):
                pairs = [_as_text(item) for item in raw]
                for i in range(0, len(pairs) - 1, 2):
                    key = pairs[i]
                    if key in {"flags", "num-slaves", "num-other-sentinels", "quorum", "ip", "port"}:
                        info[f"sentinel_{key.replace('-', '_')}"] = pairs[i + 1]
        except Exception:
            pass
        self._sentinel_info = info

    def _read_role(self, gw: DirectGateway) -> str:
        try:
            raw = gw.execute(["ROLE"], timeout_s=self._socket_timeout_s)
            if isinstance(raw, (list, tuple)) and raw:
                return _as_text(raw[0]).lower()
        except Exception:
            pass
        try:
            info = gw.info("replication")
            return (info.get("role") or "").lower()
        except Exception:
            return ""

    def _connect_master(self, host: str, port: int) -> DirectGateway:
        master_profile = replace(
            self.profile,
            topology="direct",
            transport="tcp",
            ssh=None,
            endpoint=Endpoint(host, int(port)),
            cluster=None,
        )
        tunnel = None
        jump = getattr(self, "_jump", None)
        if jump is not None:
            tunnel = jump.open(host, int(port))
        gw = DirectGateway(
            master_profile,
            password=self._password,
            tunnel=tunnel,
            pool_max=self._pool_max,
            connect_timeout_s=self._connect_timeout_s,
            socket_timeout_s=self._socket_timeout_s,
        )
        cfg = self.profile.sentinel
        if cfg is None or cfg.require_master_role:
            role = self._read_role(gw)
            if role and role != "master":
                gw.close()
                raise sentinel_error(
                    f"{host}:{port} \u5f53\u524d\u89d2\u8272\u4e3a {role}\uff0c\u4e0d\u662f\u4e3b\u8282\u70b9\u3002",
                    retryable=True,
                )
        try:
            gw.discover_capabilities()
        except AppError:
            gw.close()
            raise
        except Exception:
            pass
        return gw

    def _discover_and_connect(self, *, force: bool = False) -> bool:
        last_error: BaseException | None = None
        attempts = max(1, int(SENTINEL_DISCOVER_RETRY))
        for attempt in range(attempts):
            try:
                host, port = self._query_master_addr()
                current = self._master_endpoint
                if (
                    not force
                    and self._master_gw is not None
                    and current is not None
                    and current.host == host
                    and current.port == port
                ):
                    role = self._read_role(self._master_gw) if (self.profile.sentinel and self.profile.sentinel.require_master_role) else "master"
                    if role in {"", "master"}:
                        return False
                gw = self._connect_master(host, port)
                previous = self._master_endpoint
                old = self._master_gw
                self._master_gw = gw
                self._master_endpoint = Endpoint(host, int(port))
                self.capabilities = gw.capabilities
                if old is not None and old is not gw:
                    try:
                        old.close()
                    except Exception:
                        pass
                if previous is not None and (previous.host != host or previous.port != port):
                    self._last_switch_message = f"\u4e3b\u8282\u70b9\u5df2\u5207\u6362\uff1a\u65b0\u5730\u5740 {host}:{port}"
                return True
            except AppError as exc:
                last_error = exc
                if exc.code == ErrorCode.E06 and not exc.retryable:
                    raise
                if exc.code == ErrorCode.E02:
                    raise
                if attempt + 1 < attempts:
                    time.sleep(max(0, SENTINEL_DISCOVER_BACKOFF_MS) / 1000.0)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(max(0, SENTINEL_DISCOVER_BACKOFF_MS) / 1000.0)
        if isinstance(last_error, AppError):
            raise last_error
        if last_error is not None:
            raise sentinel_error("\u54e8\u5175\u53d1\u73b0\u5931\u8d25\u3002", hint=str(last_error), retryable=True)
        raise sentinel_error("\u54e8\u5175\u53d1\u73b0\u5931\u8d25\u3002")

    def _master(self) -> DirectGateway:
        if self._master_gw is None:
            self._discover_and_connect(force=True)
        assert self._master_gw is not None
        return self._master_gw

    def _recover(self, exc: BaseException, *, retry: bool) -> None:
        if not _is_failover_trigger(exc):
            if isinstance(exc, AppError):
                raise exc
            ep = self._master_endpoint or self.profile.endpoint
            raise classify_connect_error(
                exc,
                host=ep.host if ep else self.profile.host,
                port=int(ep.port) if ep else self.profile.port,
                tls_enabled=bool(self.profile.tls and self.profile.tls.enabled),
            )
        try:
            self._discover_and_connect(force=True)
        except AppError as discovered:
            if discovered.code == ErrorCode.E06 and self._master_gw is not None:
                raise classify_connect_error(
                    exc,
                    host=self._master_endpoint.host if self._master_endpoint else self.profile.host,
                    port=int(self._master_endpoint.port) if self._master_endpoint else self.profile.port,
                    tls_enabled=bool(self.profile.tls and self.profile.tls.enabled),
                ) from discovered
            raise
        if not retry:
            addr = f"{self._master_endpoint.host}:{self._master_endpoint.port}" if self._master_endpoint else ""
            raise AppError(
                ErrorCode.E04,
                self._last_switch_message or f"\u4e3b\u8282\u70b9\u5df2\u5207\u6362\uff1a\u65b0\u5730\u5740 {addr}\u3002\u5199\u547d\u4ee4\u672a\u81ea\u52a8\u91cd\u8bd5\uff0c\u8bf7\u786e\u8ba4\u540e\u91cd\u65b0\u6267\u884c\u3002",
            )

    def _call(self, fn: Callable[[], T], *, is_read: bool) -> T:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            self._recover(exc, retry=is_read)
            return fn()

    def discover_capabilities(self) -> Capabilities:
        def work() -> Capabilities:
            return self._master().discover_capabilities()

        caps = self._call(work, is_read=True)
        self.capabilities = caps
        return caps

    def ping(self) -> float:
        return self._call(lambda: self._master().ping(), is_read=True)

    def info(self, section: str | None = None) -> dict[str, str]:
        def work() -> dict[str, str]:
            out = dict(self._master().info(section))
            out.update(self._sentinel_info)
            if self._master_endpoint is not None:
                out.setdefault("sentinel_master_host", self._master_endpoint.host)
                out.setdefault("sentinel_master_port", str(self._master_endpoint.port))
            if self.profile.sentinel:
                out.setdefault("sentinel_master_name", self.profile.sentinel.master_name)
            return out

        return self._call(work, is_read=True)

    def scan(
        self,
        node_id: str | None,
        cursor: int,
        *,
        match: str | None,
        count: int,
        key_type: str | None,
    ) -> tuple[int, list[bytes], str | None]:
        def work() -> tuple[int, list[bytes], str | None]:
            return self._master().scan(node_id, cursor, match=match, count=count, key_type=key_type)

        return self._call(work, is_read=True)

    def execute(self, argv: list[str], *, timeout_s: float) -> Any:
        if not argv:
            raise AppError(ErrorCode.E10, "\u7a7a\u547d\u4ee4")
        return self._call(lambda: self._master().execute(argv, timeout_s=timeout_s), is_read=_is_read(argv))

    def pipeline(self, commands: list[list[str]], *, batch: int) -> list[Any]:
        is_read = all(_is_read(argv) for argv in commands) if commands else True
        return self._call(lambda: self._master().pipeline(commands, batch=batch), is_read=is_read)

    def route(self, keys: list[bytes]) -> dict[str, list[bytes]]:
        node = self.nodes()
        label = node[0].node_id if node else "master"
        return {label: list(keys)}

    def nodes(self) -> list[NodeInfo]:
        ep = self._master_endpoint
        if ep is None:
            return []
        return [NodeInfo(node_id=f"{ep.host}:{ep.port}", host=ep.host, port=ep.port, role="master")]

    def close(self) -> None:
        gw = self._master_gw
        self._master_gw = None
        if gw is not None:
            try:
                gw.close()
            except Exception:
                pass
        jump = getattr(self, "_jump", None)
        self._jump = None
        if jump is not None:
            try:
                jump.close()
            except Exception:
                pass
