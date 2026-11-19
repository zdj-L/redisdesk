"""角色能力矩阵、连接授权与判定入口。

依赖规则 R7：角色/风险判定只允许出现在 core/authz.py 与 core/policy.py。
本模块不得 import adapters / infra / app / PySide6。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Iterable, Optional, Protocol

from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.model import (
    AuthzDecision,
    ConnectionProfile,
    SessionContext,
)


class Action(str, Enum):
    VIEW_CONNECTIONS = "view_connections"
    CONNECT = "connect"
    BROWSE_KEYS = "browse_keys"
    VIEW_VALUE = "view_value"
    REVEAL_SENSITIVE = "reveal_sensitive"
    EXEC_L1 = "exec_l1"
    EXEC_L2 = "exec_l2"
    EXEC_L3 = "exec_l3"
    EXEC_L4 = "exec_l4"
    BATCH_DELETE = "batch_delete"
    CLIENT_KILL = "client_kill"
    MANAGE_CONNECTION = "manage_connection"
    GRANT_ACCESS = "grant_access"
    ALLOW_PLAINTEXT = "allow_plaintext"
    VIEW_AUDIT = "view_audit"
    EXPORT_AUDIT = "export_audit"
    EXPORT_MASKED = "export_masked"
    EXPORT_UNMASKED = "export_unmasked"
    MANAGE_USERS = "manage_users"
    MANAGE_SECURITY_SETTINGS = "manage_security_settings"
    VERIFY_AUDIT_CHAIN = "verify_audit_chain"


# 角色最低要求。viewer 已默认拥有未列出? 否：未列出即拒绝。
# 全集来自 08 §3.2。
_ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}

_ACTION_MIN_ROLE: dict[Action, str] = {
    Action.VIEW_CONNECTIONS: "viewer",
    Action.CONNECT: "viewer",
    Action.BROWSE_KEYS: "viewer",
    Action.VIEW_VALUE: "viewer",
    Action.REVEAL_SENSITIVE: "operator",  # Q10 默认：Operator 起
    Action.EXEC_L1: "viewer",
    Action.EXEC_L2: "operator",
    Action.EXEC_L3: "operator",
    Action.EXEC_L4: "admin",
    Action.BATCH_DELETE: "admin",
    Action.CLIENT_KILL: "admin",
    Action.MANAGE_CONNECTION: "admin",
    Action.GRANT_ACCESS: "admin",
    Action.ALLOW_PLAINTEXT: "admin",
    Action.VIEW_AUDIT: "admin",
    Action.EXPORT_AUDIT: "admin",
    Action.EXPORT_MASKED: "operator",
    Action.EXPORT_UNMASKED: "admin",
    Action.MANAGE_USERS: "admin",
    Action.MANAGE_SECURITY_SETTINGS: "admin",
    Action.VERIFY_AUDIT_CHAIN: "admin",
}

# 需要「该角色已被授予该连接」的动作。
_CONNECTION_SCOPED: frozenset[Action] = frozenset(
    {
        Action.CONNECT,
        Action.BROWSE_KEYS,
        Action.VIEW_VALUE,
        Action.REVEAL_SENSITIVE,
        Action.EXEC_L1,
        Action.EXEC_L2,
        Action.EXEC_L3,
        Action.EXEC_L4,
        Action.BATCH_DELETE,
        Action.CLIENT_KILL,
        Action.EXPORT_MASKED,
        Action.EXPORT_UNMASKED,
    }
)

# 受保护档位约束的动作（只读档拒绝写/高危）。
_PROTECTION_SCOPED: frozenset[Action] = frozenset(
    {
        Action.EXEC_L2,
        Action.EXEC_L3,
        Action.EXEC_L4,
        Action.BATCH_DELETE,
        Action.CLIENT_KILL,
    }
)


class GrantResolver(Protocol):
    def has_grant(self, connection_id: str, role: str) -> bool: ...


class StaticGrantResolver:
    """测试与无 IO 调用用的静态授权表。"""

    def __init__(self, grants: Iterable[tuple[str, str]] = ()) -> None:
        self._grants = {(cid, role) for cid, role in grants}

    def has_grant(self, connection_id: str, role: str) -> bool:
        return (connection_id, role) in self._grants


def role_allows(role: str, action: Action) -> bool:
    required = _ACTION_MIN_ROLE.get(action)
    if required is None:
        return False
    return _ROLE_RANK.get(role, 0) >= _ROLE_RANK[required]


def min_role_for(action: Action) -> str:
    return _ACTION_MIN_ROLE[action]


def check(
    session: SessionContext | None,
    action: Action,
    connection: ConnectionProfile | None = None,
    *,
    grants: GrantResolver | None = None,
    now: datetime | None = None,
) -> AuthzDecision:
    """授权单一入口。顺序：会话 → 角色矩阵 → 连接授权 → 保护档位。

    不在此做命令风险判定（那是 policy.py 的职责）。
    """
    clock = now or datetime.now(timezone.utc)

    if session is None:
        return _deny("unauthenticated", action, None, "需要登录后才能执行此操作", None)

    if not session.unlocked:
        return _deny("locked", action, session.role, "会话已锁定，请先解锁", connection)

    if session.absolute_expires_at is not None and clock >= session.absolute_expires_at:
        return _deny("unauthenticated", action, session.role, "会话已超过绝对时长，请重新登录", connection)

    required = min_role_for(action)
    if not role_allows(session.role, action):
        return AuthzDecision(
            allowed=False,
            reason="role",
            required_role=required,  # type: ignore[arg-type]
            action=action.value,
            resource_id=connection.id if connection else None,
            message=_role_message(required),
        )

    if action in _CONNECTION_SCOPED:
        if connection is None:
            return _deny(
                "grant",
                action,
                required,
                "此操作需要指定已授权的连接",
                None,
            )
        if grants is None or not grants.has_grant(connection.id, session.role):
            # Admin 新建连接会自动授予 admin，但仍须显式存在 grant 行。
            return AuthzDecision(
                allowed=False,
                reason="grant",
                required_role=required,  # type: ignore[arg-type]
                action=action.value,
                resource_id=connection.id,
                message="当前角色无该连接权限，请联系管理员授权",
            )

    if action in _PROTECTION_SCOPED and connection is not None:
        if connection.protection == "readonly":
            return AuthzDecision(
                allowed=False,
                reason="protection",
                required_role=required,  # type: ignore[arg-type]
                action=action.value,
                resource_id=connection.id,
                message="当前连接为只读保护档，禁止写入与高危操作；生产连接请另建 env=test 连接",
            )
        if action == Action.EXEC_L4 and connection.env == "prod":
            return AuthzDecision(
                allowed=False,
                reason="protection",
                required_role="admin",
                action=action.value,
                resource_id=connection.id,
                message="生产连接永久禁止 L4 操作，无解锁路径",
            )
        if action == Action.EXEC_L4 and not connection.allow_l4:
            return AuthzDecision(
                allowed=False,
                reason="protection",
                required_role="admin",
                action=action.value,
                resource_id=connection.id,
                message="当前连接未开启 allow_l4，无法执行受控 L4 命令",
            )

    return AuthzDecision(
        allowed=True,
        reason="",
        required_role=required,  # type: ignore[arg-type]
        action=action.value,
        resource_id=connection.id if connection else None,
        message="",
    )


def check_or_raise(
    session: SessionContext | None,
    action: Action,
    connection: ConnectionProfile | None = None,
    *,
    grants: GrantResolver | None = None,
    now: datetime | None = None,
) -> AuthzDecision:
    decision = check(session, action, connection, grants=grants, now=now)
    if not decision.allowed:
        code = ErrorCode.E14 if decision.reason in {"unauthenticated", "locked"} else ErrorCode.E08
        raise AppError(code, decision.message, hint=f"deny_reason={decision.reason}")
    return decision


def _deny(
    reason: str,
    action: Action,
    required: Optional[str],
    message: str,
    connection: ConnectionProfile | None,
) -> AuthzDecision:
    return AuthzDecision(
        allowed=False,
        reason=reason,
        required_role=required,  # type: ignore[arg-type]
        action=action.value,
        resource_id=connection.id if connection else None,
        message=message,
    )


def _role_message(required: str) -> str:
    title = {"viewer": "Viewer", "operator": "Operator", "admin": "Admin"}[required]
    return f"需要 {title} 角色才能执行此操作。请联系管理员申请角色或改用已授权账号。"
