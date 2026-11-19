"""审计查询与完整性校验门面。"""

from __future__ import annotations

from redisdesk.core.authz import Action, GrantResolver, check_or_raise
from redisdesk.core.model import SessionContext
from redisdesk.infra.audit import AuditLog
from redisdesk.infra.audit_chain import ChainStatus


class AuditService:
    def __init__(self, audit: AuditLog, grants: GrantResolver) -> None:
        self.audit = audit
        self.grants = grants

    def query(self, session: SessionContext, **kwargs):  # noqa: ANN003
        check_or_raise(session, Action.VIEW_AUDIT, grants=self.grants)
        return self.audit.query(**kwargs)

    def verify_full(self, session: SessionContext) -> ChainStatus:
        check_or_raise(session, Action.VERIFY_AUDIT_CHAIN, grants=self.grants)
        return self.audit.verify_full()

    def start_status(self) -> ChainStatus:
        return self.audit.verify_on_start()
