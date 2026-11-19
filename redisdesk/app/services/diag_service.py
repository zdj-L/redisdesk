"""诊断：P0 仅 INFO 关键指标；慢查询/客户端列表在 P1。"""

from __future__ import annotations

from redisdesk.adapters.gateway import RedisGateway
from redisdesk.core.authz import Action, GrantResolver, check_or_raise
from redisdesk.core.model import ConnectionProfile, SessionContext


class DiagService:
    def __init__(self, grants: GrantResolver) -> None:
        self.grants = grants

    def overview(self, session: SessionContext, profile: ConnectionProfile, gateway: RedisGateway) -> dict[str, str]:
        check_or_raise(session, Action.EXEC_L1, profile, grants=self.grants)
        info = gateway.info()
        keys = (
            "redis_version",
            "os",
            "uptime_in_seconds",
            "connected_clients",
            "used_memory_human",
            "used_memory_peak_human",
            "role",
            "db0",
        )
        return {k: info[k] for k in keys if k in info}
