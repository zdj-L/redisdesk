"""单机直连 Gateway。浏览只走 SCAN，不调用 KEYS / HGETALL / SMEMBERS。"""

from __future__ import annotations

import ssl
import time
from typing import Any, Optional

from redisdesk.adapters.gateway import Capabilities, capabilities_from_version
from redisdesk.adapters.tls import build_ssl_context
from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.limits import (
    CONNECT_TIMEOUT_S,
    HEALTH_CHECK_INTERVAL_S,
    POOL_MAX_CONNECTIONS,
    SOCKET_TIMEOUT_S,
)
from redisdesk.core.model import ConnectionProfile, NodeInfo, TlsConfig

# 模块级禁止名单（测试会检查本文件源码）。
# 不得出现全量读取命令（见 docs/04 §9）


class DirectGateway:
    def __init__(
        self,
        profile: ConnectionProfile,
        *,
        password: memoryview | bytes | bytearray | None = None,
        client: Any | None = None,
        tunnel: Any | None = None,
        pool_max: int = POOL_MAX_CONNECTIONS,
        connect_timeout_s: float = CONNECT_TIMEOUT_S,
        socket_timeout_s: float = SOCKET_TIMEOUT_S,
    ) -> None:
        self.profile = profile
        self.capabilities: Capabilities = capabilities_from_version("0.0.0")
        self._tunnel = tunnel
        self._client = client
        if self._client is None:
            try:
                self._client = self._build_client(
                    profile,
                    password=password,
                    pool_max=pool_max,
                    connect_timeout_s=connect_timeout_s,
                    socket_timeout_s=socket_timeout_s,
                )
            except Exception:
                self.close()
                raise

    def _build_client(
        self,
        profile: ConnectionProfile,
        *,
        password: memoryview | bytes | bytearray | None,
        pool_max: int,
        connect_timeout_s: float,
        socket_timeout_s: float,
    ) -> Any:
        try:
            import redis
            from redis.retry import Retry
            from redis.backoff import NoBackoff
        except ImportError as exc:  # pragma: no cover
            raise AppError(ErrorCode.E12, "未安装 redis-py") from exc

        endpoint = profile.endpoint
        if endpoint is None or not endpoint.host:
            raise AppError(ErrorCode.E01, "连接缺少 host")
        tls = profile.tls or TlsConfig()
        ssl_ctx = build_ssl_context(tls)
        connect_host = endpoint.host
        connect_port = endpoint.port
        tls_server_hostname = endpoint.host
        if getattr(self, "_tunnel", None) is not None:
            if not getattr(self._tunnel, "local_port", None):
                raise AppError(ErrorCode.E07, "SSH 隧道尚未就绪。")
            connect_host = "127.0.0.1"
            connect_port = int(self._tunnel.local_port)
        passwd: Optional[str] = None
        if password is not None:
            raw = password.tobytes() if isinstance(password, memoryview) else bytes(password)
            # redis-py 需要 str；随后尽量不保留
            passwd = raw.decode("utf-8", errors="surrogateescape")
        kwargs: dict[str, Any] = {
            "host": connect_host,
            "port": connect_port,
            "db": profile.db,
            "username": profile.username or None,
            "password": passwd,
            "socket_connect_timeout": connect_timeout_s,
            "socket_timeout": socket_timeout_s,
            "health_check_interval": HEALTH_CHECK_INTERVAL_S,
            "max_connections": pool_max,
            # RESP2 supports old Redis and compatible services without HELLO.
            # AUTH remains redis-py managed; never retry without credentials or TLS.
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
            if getattr(self, "_tunnel", None) is not None:
                kwargs["connection_class"] = _get_tunnel_ssl_connection_class()
                kwargs["tls_server_hostname"] = tls_server_hostname
        try:
            return redis.Redis(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise AppError(ErrorCode.E01, f"连接失败 {endpoint.host}:{endpoint.port}") from exc
        finally:
            passwd = None

    def discover_capabilities(self) -> Capabilities:
        info = self.info("server")
        version = info.get("redis_version") or info.get("redis-version") or "0.0.0"
        self.capabilities = capabilities_from_version(version, cluster=False)
        return self.capabilities

    def ping(self) -> float:
        started = time.perf_counter()
        try:
            self._client.ping()
        except Exception as exc:  # noqa: BLE001
            raise _map_redis_error(exc, profile=self.profile) from exc
        return time.perf_counter() - started

    def info(self, section: str | None = None) -> dict[str, str]:
        try:
            raw = self._client.info(section) if section else self._client.info()
        except Exception as exc:  # noqa: BLE001
            raise _map_redis_error(exc, profile=self.profile) from exc
        out: dict[str, str] = {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                out[str(k)] = v if isinstance(v, str) else str(v)
        return out

    def scan(
        self,
        node_id: str | None,
        cursor: int,
        *,
        match: str | None,
        count: int,
        key_type: str | None,
    ) -> tuple[int, list[bytes], str | None]:
        # node_id 在直连下忽略
        kwargs: dict[str, Any] = {"cursor": cursor, "count": count}
        if match:
            kwargs["match"] = match
        if key_type and self.capabilities.supports_scan_type:
            kwargs["_type"] = key_type
        try:
            next_cursor, keys = self._client.scan(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise _map_redis_error(exc, profile=self.profile) from exc
        items = [k if isinstance(k, bytes) else str(k).encode("utf-8") for k in keys]
        return int(next_cursor), items, None

    def execute(self, argv: list[str], *, timeout_s: float) -> Any:
        if not argv:
            raise AppError(ErrorCode.E10, "空命令")
        try:
            # A single connection carries both the timeout and the actual command.
            conn = self._client.connection_pool.get_connection(argv[0])
            old = getattr(conn, "socket_timeout", None)
            sock = getattr(conn, "_sock", None)
            try:
                conn.socket_timeout = timeout_s
                if sock is not None:
                    sock.settimeout(timeout_s)
                conn.send_command(*argv)
                return self._client.parse_response(conn, argv[0])
            except BaseException:
                conn.disconnect()
                raise
            finally:
                conn.socket_timeout = old
                if getattr(conn, "_sock", None) is not None:
                    conn._sock.settimeout(old)
                self._client.connection_pool.release(conn)
        except Exception as exc:  # noqa: BLE001
            raise _map_redis_error(exc, argv[0], profile=self.profile) from exc

    def pipeline(self, commands: list[list[str]], *, batch: int) -> list[Any]:
        results: list[Any] = []
        if not commands:
            return results
        step = max(1, batch)
        for i in range(0, len(commands), step):
            chunk = commands[i : i + step]
            try:
                pipe = self._client.pipeline(transaction=False)
                for argv in chunk:
                    pipe.execute_command(*argv)
                results.extend(pipe.execute())
            except Exception as exc:  # noqa: BLE001
                raise _map_redis_error(exc, profile=self.profile) from exc
        return results

    def route(self, keys: list[bytes]) -> dict[str, list[bytes]]:
        return {"default": list(keys)}

    def nodes(self) -> list[NodeInfo]:
        ep = self.profile.endpoint
        node_id = f"{ep.host}:{ep.port}" if ep else "default"
        return [NodeInfo(node_id=node_id, host=ep.host if ep else "", port=ep.port if ep else 6379)]

    def close(self) -> None:
        try:
            if self._client is not None:
                self._client.close()
        except Exception:
            pass
        tunnel = getattr(self, "_tunnel", None)
        self._tunnel = None
        if tunnel is not None:
            try:
                tunnel.close()
            except Exception:
                pass


def classify_connect_error(
    exc: BaseException,
    *,
    host: str = "",
    port: int = 0,
    tls_enabled: bool = False,
    command: str = "",
) -> AppError:
    """把底层网络/TLS/认证异常翻译成可执行的中文说明。"""
    if isinstance(exc, AppError):
        return exc
    name = type(exc).__name__
    msg = str(exc)
    low = msg.lower()
    loc = f"{host}:{port}" if host else ""
    loc_suffix = f"（{loc}）" if loc else ""
    cmd = f"：{command}" if command else ""

    if isinstance(exc, ssl.SSLCertVerificationError) or "certificate verify" in low or "certificate_verify" in low:
        return AppError(
            ErrorCode.E01,
            f"TLS 证书校验失败{loc_suffix}。请检查 CA 文件与主机名是否匹配。",
        )
    if (
        isinstance(exc, ssl.SSLError)
        or "ssl" in low
        or "handshake" in low
        or name in {"SSLError", "SSLWantReadError", "SSLZeroReturnError"}
    ):
        extra = "若该 Redis 未启用 TLS，请关闭「启用 TLS」并登记明文例外后再试。" if tls_enabled else ""
        return AppError(ErrorCode.E01, f"TLS 握手失败{loc_suffix}。{extra}".strip())
    if "timeout" in low or name in {"TimeoutError", "socket.timeout"} or "timed out" in low:
        if tls_enabled:
            return AppError(
                ErrorCode.E03,
                f"连接超时{loc_suffix}。当前已启用 TLS，若本机 Redis 是明文服务，TLS 握手会等到超时。"
                "请关闭「启用 TLS」并填写明文例外理由后再试。",
                retryable=False,
            )
        return AppError(
            ErrorCode.E03,
            f"连接超时{loc_suffix}{cmd}，数秒内未响应。请检查地址、端口与防火墙。",
            retryable=False,
        )
    if name in {"AuthenticationError", "AuthorizationError"} or "noauth" in low or "wrongpass" in low or "invalid username-password" in low or "invalid password" in low:
        return AppError(ErrorCode.E02, "用户名或口令错误，或 ACL 拒绝")
    if "noperm" in low or ("unknown command" in low and "acl" in low):
        return AppError(ErrorCode.E02, "ACL 命令被拒")
    if "refused" in low or "10061" in msg or name in {"ConnectionRefusedError"}:
        return AppError(
            ErrorCode.E01,
            f"连接被拒绝{loc_suffix}，该端口没有服务在监听。",
            retryable=True,
        )
    if "connection" in low or "resolve" in low or "dns" in low or "name or service not known" in low:
        return AppError(ErrorCode.E01, f"连接失败{loc_suffix}", retryable=True)
    if "readonly" in low:
        return AppError(ErrorCode.E04, msg or "当前节点只读")
    if "wrongtype" in low or "oom" in low:
        return AppError(ErrorCode.E04, msg)
    return AppError(ErrorCode.E04, msg or name)


def _map_redis_error(exc: BaseException, command: str = "", profile: ConnectionProfile | None = None) -> AppError:
    host = profile.host if profile is not None else ""
    port = profile.port if profile is not None else 0
    tls_enabled = bool(profile is not None and profile.tls and profile.tls.enabled)
    return classify_connect_error(exc, host=host, port=port, tls_enabled=tls_enabled, command=command)
