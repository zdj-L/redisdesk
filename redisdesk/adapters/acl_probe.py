"""连接测试 ACL 自检。不阻断连接；无权限时不误报。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from redisdesk.adapters.gateway import RedisGateway


@dataclass
class AclProbeResult:
    username: Optional[str] = None
    is_default: bool = False
    warnings: list[str] = field(default_factory=list)
    can_inspect: bool = False
    version: Optional[str] = None
    too_old: bool = False
    first_default_warning: bool = False


def probe(gateway: RedisGateway) -> AclProbeResult:
    result = AclProbeResult()
    try:
        info = gateway.info("server")
        version = info.get("redis_version") or ""
        result.version = version
        caps = gateway.capabilities
        if not caps.supports_scan_type:
            result.too_old = True
            result.warnings.append("Redis 版本低于 6.0，无法使用 ACL 自检与 SCAN TYPE，类型过滤将降级")
    except Exception:
        pass
    try:
        who = gateway.execute(["ACL", "WHOAMI"], timeout_s=5)
        name = _as_str(who)
        result.username = name
        if name == "default":
            result.is_default = True
            result.warnings.append("当前账号为 default，建议改用专用 ACL 账号（redisdesk_ro / redisdesk_rw / redisdesk_admin）")
            result.first_default_warning = True
    except Exception:
        result.warnings.append("无法检测当前 ACL 账号，请人工核对")
        return result
    try:
        raw = gateway.execute(["ACL", "GETUSER", result.username or "default"], timeout_s=5)
        result.can_inspect = True
        text = _flatten(raw).lower()
        if "+@all" in text or "~*" in text:
            result.warnings.append("权限过宽（+@all 或 ~*），排查账号应收敛到最小权限")
        for cat in ("@dangerous", "@admin", "@scripting"):
            if cat in text:
                result.warnings.append(f"账号具备 {cat}，排查账号不应具备该类权限")
    except Exception:
        result.warnings.append("无法检测详细 ACL（可能无 GETUSER 权限），请人工核对")
    return result


def _as_str(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="surrogateescape")
    return str(value)


def _flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten(v) for v in value)
    return _as_str(value)
