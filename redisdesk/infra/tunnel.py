"""SSH 本地端口转发：先连跳板，再从跳板直达 Redis。"""

from __future__ import annotations

import os
import socket
import threading
from typing import Any, Optional

from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.limits import CONNECT_TIMEOUT_S, SSH_KEEPALIVE_S
from redisdesk.core.model import SshConfig
from redisdesk.infra.known_hosts import (
    HostKeyMismatchError,
    KnownHostsStore,
    MissingHostKeyPolicy,
    UnknownHostKeyError,
    assert_pinned_fingerprint,
    mismatch_from_paramiko,
)


def _decode_secret(value: memoryview | bytes | bytearray | str | None) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    raw = value.tobytes() if isinstance(value, memoryview) else bytes(value)
    if not raw:
        return None
    return raw.decode("utf-8", errors="surrogateescape")


def classify_ssh_error(
    exc: BaseException,
    *,
    bastion_host: str = "",
    bastion_port: int = 22,
    redis_host: str = "",
    redis_port: int = 6379,
    phase: str = "bastion",
) -> AppError:
    """把跳板或隧道异常翻译成中文原因。"""
    if isinstance(exc, AppError):
        return exc
    name = type(exc).__name__
    msg = str(exc)
    low = msg.lower()
    loc = f"{bastion_host}:{bastion_port}" if bastion_host else ""
    redis_loc = f"{redis_host}:{redis_port}" if redis_host else ""

    auth_names = {"AuthenticationException", "PasswordRequiredException", "SSHException", "BadAuthenticationType"}
    if name in {"AuthenticationException", "PasswordRequiredException", "BadAuthenticationType"} or "auth" in low and "fail" in low or "authentication failed" in low:
        return AppError(ErrorCode.E07, "SSH 跳板认证失败，请检查用户名、密码或私钥。")
    if "no existing session" in low or "not a valid" in low and "key" in low or "invalid key" in low or "could not deserialize" in low:
        return AppError(ErrorCode.E07, "SSH 私钥无法使用，请检查文件路径和口令。")
    if isinstance(exc, (UnknownHostKeyError, HostKeyMismatchError)):
        return exc
    if name in {"BadHostKeyException"}:
        return mismatch_from_paramiko(exc, host=bastion_host, port=bastion_port)
    if "host key" in low or "unknown host key" in low:
        return AppError(ErrorCode.E07, f"SSH 跳板主机密钥不受信任（{loc}）。")
    if phase == "redis" or "connect failed" in low or "administratively prohibited" in low or "channel open" in low:
        target = f"?{redis_loc}?" if redis_loc else ""
        return AppError(ErrorCode.E07, f"已连上跳板，但无法到达 Redis{target}。")
    if "timed out" in low or "timeout" in low or name in {"TimeoutError", "socket.timeout"}:
        return AppError(ErrorCode.E07, f"无法在限定时间内连上 SSH 跳板（{loc}）。", retryable=False)
    if "refused" in low or "10061" in msg or name in {"ConnectionRefusedError", "NoValidConnectionsError"}:
        return AppError(ErrorCode.E07, f"无法连接到 SSH 跳板（{loc}），连接被拒绝。", retryable=True)
    if "resolve" in low or "name or service not known" in low or "getaddrinfo" in low or "11001" in msg:
        return AppError(ErrorCode.E07, f"无法解析 SSH 跳板主机（{loc}）。", retryable=True)
    if "connection" in low or name in {"SSHException", "socket.error", "OSError", "EOFError"}:
        return AppError(ErrorCode.E07, f"无法连接到 SSH 跳板（{loc}）。", retryable=True)
    return AppError(ErrorCode.E07, f"SSH 隧道失败（{loc}）：{msg or name}")


class SshTunnel:
    """本机监听 127.0.0.1:local_port，经 SSH 跳板转发到 Redis host:port。"""

    def __init__(
        self,
        config: SshConfig,
        remote_host: str,
        remote_port: int,
        *,
        password: memoryview | bytes | bytearray | str | None = None,
        passphrase: memoryview | bytes | bytearray | str | None = None,
        client: Any | None = None,
        connect_timeout_s: float = CONNECT_TIMEOUT_S,
        known_hosts_store: KnownHostsStore | None = None,
    ) -> None:
        self.config = config
        self.remote_host = remote_host
        self.remote_port = int(remote_port)
        self._password = password
        self._passphrase = passphrase
        self._known_hosts_store = known_hosts_store
        self._external_client = client
        self._client = client
        self._connect_timeout_s = connect_timeout_s
        self.local_port: int | None = None
        self._listen: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._workers: list[threading.Thread] = []
        self._lock = threading.Lock()

    def start(self) -> int:
        if not self.config.host:
            raise AppError(ErrorCode.E07, "请填写 SSH 跳板主机。")
        if not self.config.username:
            raise AppError(ErrorCode.E07, "请填写 SSH 跳板用户名。")
        if self._client is None:
            self._client = self._connect_client()
        listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen.bind(("127.0.0.1", int(self.config.local_port or 0)))
        listen.listen(32)
        listen.settimeout(0.5)
        self._listen = listen
        self.local_port = int(listen.getsockname()[1])
        self._stop.clear()
        self._thread = threading.Thread(target=self._accept_loop, name="redisdesk-ssh-tunnel", daemon=True)
        self._thread.start()
        return self.local_port

    def close(self) -> None:
        self._stop.set()
        listen = self._listen
        self._listen = None
        if listen is not None:
            try:
                listen.close()
            except Exception:
                pass
        if self._thread is not None and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2)
        self._thread = None
        if self._external_client is None and self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
        self._client = self._external_client
        self.local_port = None

    def _connect_client(self) -> Any:
        try:
            import paramiko
        except ImportError as exc:  # pragma: no cover
            raise AppError(ErrorCode.E07, "未安装 paramiko，无法建立 SSH 跳板。") from exc

        client = paramiko.SSHClient()
        policy = (self.config.known_hosts_policy or "strict").lower()
        self._load_host_keys(client)
        # Never AutoAdd: unknown keys raise, and the UI may prompt only for tofu.
        client.set_missing_host_key_policy(MissingHostKeyPolicy(port=int(self.config.port or 22), policy=policy))

        password = _decode_secret(self._password)
        passphrase = _decode_secret(self._passphrase)
        kwargs: dict[str, Any] = {
            "hostname": self.config.host,
            "port": int(self.config.port or 22),
            "username": self.config.username,
            "timeout": self._connect_timeout_s,
            "banner_timeout": self._connect_timeout_s,
            "auth_timeout": self._connect_timeout_s,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if self.config.auth_mode == "password":
            if not password:
                raise AppError(ErrorCode.E07, "请填写 SSH 跳板密码。")
            kwargs["password"] = password
        else:
            key_path = (self.config.private_key_path or "").strip()
            if not key_path:
                raise AppError(ErrorCode.E07, "请选择 SSH 私钥文件。")
            if not os.path.isfile(key_path):
                raise AppError(ErrorCode.E07, "找不到 SSH 私钥文件，请检查路径。")
            kwargs["key_filename"] = key_path
            if passphrase:
                kwargs["passphrase"] = passphrase
        try:
            client.connect(**kwargs)
            transport = client.get_transport()
            if transport is not None:
                remote = transport.get_remote_server_key()
                if remote is not None:
                    assert_pinned_fingerprint(
                        remote,
                        self.config.host_key_sha256,
                        host=self.config.host,
                        port=int(self.config.port or 22),
                    )
                transport.set_keepalive(int(SSH_KEEPALIVE_S))
            return client
        except Exception as exc:  # noqa: BLE001
            try:
                client.close()
            except Exception:
                pass
            raise classify_ssh_error(
                exc,
                bastion_host=self.config.host,
                bastion_port=int(self.config.port or 22),
                redis_host=self.remote_host,
                redis_port=self.remote_port,
                phase="bastion",
            ) from exc
        finally:
            password = None
            passphrase = None

    def _load_host_keys(self, client: Any) -> None:
        store = getattr(self, "_known_hosts_store", None) or KnownHostsStore()
        store.load_into_client(client)

    def _accept_loop(self) -> None:
        listen = self._listen
        if listen is None:
            return
        while not self._stop.is_set():
            try:
                src, addr = listen.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            worker = threading.Thread(target=self._forward, args=(src,), name="redisdesk-ssh-fwd", daemon=True)
            with self._lock:
                self._workers = [item for item in self._workers if item.is_alive()]
                self._workers.append(worker)
            worker.start()

    def _forward(self, src: socket.socket) -> None:
        channel = None
        try:
            client = self._client
            transport = client.get_transport() if client is not None else None
            if transport is None or not transport.is_active():
                src.close()
                return
            try:
                origin = src.getpeername()
            except OSError:
                origin = ("127.0.0.1", 0)
            try:
                channel = transport.open_channel("direct-tcpip", (self.remote_host, self.remote_port), origin)
            except Exception as exc:  # noqa: BLE001
                raise classify_ssh_error(
                    exc,
                    bastion_host=self.config.host,
                    bastion_port=int(self.config.port or 22),
                    redis_host=self.remote_host,
                    redis_port=self.remote_port,
                    phase="redis",
                ) from exc
            src.settimeout(1.0)
            if hasattr(channel, "settimeout"):
                try:
                    channel.settimeout(1.0)
                except Exception:
                    pass
            while not self._stop.is_set():
                rlist = [src]
                if hasattr(channel, "fileno"):
                    try:
                        rlist.append(channel)
                    except Exception:
                        pass
                readable: list[Any] = []
                try:
                    import select as _select
                    readable, _, _ = _select.select(rlist, [], [], 0.5)
                except Exception:
                    readable = rlist
                if src in readable:
                    data = src.recv(32768)
                    if not data:
                        break
                    channel.sendall(data)
                if channel in readable or not hasattr(channel, "fileno"):
                    if channel.recv_ready() if hasattr(channel, "recv_ready") else True:
                        chunk = channel.recv(32768)
                        if not chunk:
                            break
                        src.sendall(chunk)
                    elif channel not in readable:
                        continue
        except Exception:
            pass
        finally:
            try:
                src.close()
            except Exception:
                pass
            if channel is not None:
                try:
                    channel.close()
                except Exception:
                    pass
