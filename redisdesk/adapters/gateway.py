"""RedisGateway 协议 + Capabilities + 工厂。"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable
from typing import NamedTuple

from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.model import ConnectionProfile, NodeInfo


class Capabilities(NamedTuple):
    supports_scan_type: bool
    supports_unlink: bool
    supports_memory: bool
    supports_stream: bool
    supports_cluster: bool
    supports_select_db: bool
    server_version: str


def capabilities_from_version(version: str, *, cluster: bool = False) -> Capabilities:
    parts: list[int] = []
    for token in version.split("."):
        digits = "".join(ch for ch in token if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    while len(parts) < 2:
        parts.append(0)
    major, minor = parts[0], parts[1]
    numeric = major * 100 + minor
    return Capabilities(
        supports_scan_type=numeric >= 600,
        supports_unlink=numeric >= 400,
        supports_memory=numeric >= 400,
        supports_stream=numeric >= 500,
        supports_cluster=cluster,
        supports_select_db=not cluster,
        server_version=version,
    )


@runtime_checkable
class RedisGateway(Protocol):
    capabilities: Capabilities

    def ping(self) -> float: ...
    def info(self, section: str | None = None) -> dict[str, str]: ...
    def scan(
        self,
        node_id: str | None,
        cursor: int,
        *,
        match: str | None,
        count: int,
        key_type: str | None,
    ) -> tuple[int, list[bytes], str | None]: ...
    def execute(self, argv: list[str], *, timeout_s: float) -> Any: ...
    def pipeline(self, commands: list[list[str]], *, batch: int) -> list[Any]: ...
    def route(self, keys: list[bytes]) -> dict[str, list[bytes]]: ...
    def nodes(self) -> list[NodeInfo]: ...
    def close(self) -> None: ...


def open_gateway(
    profile: ConnectionProfile,
    *,
    password: memoryview | None = None,
    ssh_password: memoryview | bytes | str | None = None,
    ssh_passphrase: memoryview | bytes | str | None = None,
    ssh_client=None,
) -> RedisGateway:
    from redisdesk.adapters.topology import TopologyResolver

    return TopologyResolver().open(
        profile,
        password=password,
        ssh_password=ssh_password,
        ssh_passphrase=ssh_passphrase,
        ssh_client=ssh_client,
    )


class GatewayNotImplemented:
    """P1 拓扑的占位实现。"""

    def __init__(self, name: str) -> None:
        self._name = name
        self.capabilities = capabilities_from_version("0.0.0")

    def _deny(self) -> None:
        raise AppError(ErrorCode.E01, f"{self._name} 将在 P1 实现", hint="当前版本仅支持 direct + tcp")

    def ping(self) -> float:
        self._deny()
        return 0.0

    def info(self, section: str | None = None) -> dict[str, str]:
        self._deny()
        return {}

    def scan(self, node_id: str | None, cursor: int, *, match: str | None, count: int, key_type: str | None) -> tuple[int, list[bytes], str | None]:
        self._deny()
        return 0, [], None

    def execute(self, argv: list[str], *, timeout_s: float) -> Any:
        self._deny()

    def pipeline(self, commands: list[list[str]], *, batch: int) -> list[Any]:
        self._deny()
        return []

    def route(self, keys: list[bytes]) -> dict[str, list[bytes]]:
        self._deny()
        return {}

    def nodes(self) -> list[NodeInfo]:
        self._deny()
        return []

    def close(self) -> None:
        return None
