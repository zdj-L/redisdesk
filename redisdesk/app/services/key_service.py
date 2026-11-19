"""扫描会话生命周期。"""

from __future__ import annotations

from redisdesk.adapters.gateway import RedisGateway
from redisdesk.adapters.scan import CancellationToken, ScanEngine
from redisdesk.core.authz import Action, GrantResolver, check_or_raise
from redisdesk.core.model import ConnectionProfile, ScanQuery, SessionContext


class KeyService:
    def __init__(self, grants: GrantResolver) -> None:
        self.grants = grants

    def create_engine(
        self,
        session: SessionContext,
        profile: ConnectionProfile,
        gateway: RedisGateway,
        query: ScanQuery,
        *,
        token: CancellationToken | None = None,
    ) -> ScanEngine:
        check_or_raise(session, Action.BROWSE_KEYS, profile, grants=self.grants)
        return ScanEngine(gateway, query, token=token)
