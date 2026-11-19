"""命令执行 + 历史（唯一命令门面）。任何入口必须走授权 → 风险 → 审计。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from redisdesk.adapters.gateway import RedisGateway
from redisdesk.core.authz import Action, GrantResolver, check
from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.limits import CONSOLE_HISTORY_MAX, CONSOLE_TIMEOUT_S, TYPED_CONFIRM_REQUIRED
from redisdesk.core.model import CommandResult, ConnectionProfile, SessionContext
from redisdesk.core.parser import ParseError, parse_command
from redisdesk.core.policy import PolicyDecision, PolicyEngine
from redisdesk.core.redact import argv_digest, format_argv_masked
from redisdesk.infra.audit import AuditLog
from redisdesk.infra.store import Store


LEVEL_TO_ACTION = {
    "L1": Action.EXEC_L1,
    "L2": Action.EXEC_L2,
    "L3": Action.EXEC_L3,
    "L4": Action.EXEC_L4,
}


@dataclass(frozen=True, slots=True)
class ConsolePrepare:
    argv: tuple[str, ...]
    policy: PolicyDecision
    authz_ok: bool
    pending_seq: Optional[int]
    message: str = ""


class ConsoleService:
    def __init__(
        self,
        store: Store,
        audit: AuditLog,
        grants: GrantResolver,
        *,
        policy: PolicyEngine | None = None,
    ) -> None:
        self.store = store
        self.audit = audit
        self.grants = grants
        self.policy = policy or PolicyEngine()

    def parse(self, raw: str) -> tuple[str, ...]:
        result = parse_command(raw)
        if not result.argv:
            raise ParseError("空输入", 1, "请输入一条 Redis 命令")
        return result.argv

    def prepare(
        self,
        session: SessionContext | None,
        profile: ConnectionProfile,
        raw: str,
        *,
        unlock_reason: str | None = None,
        typed_confirm_required: bool = TYPED_CONFIRM_REQUIRED,
        emergency_readonly: bool = False,
        record_pending: bool = True,
    ) -> ConsolePrepare:
        argv = self.parse(raw)
        decision = self.policy.evaluate(
            argv,
            profile,
            session.role if session else "viewer",
            unlock_reason=unlock_reason,
            typed_confirm_required=typed_confirm_required if profile.env != "prod" else True,
        )
        level = decision.assessment.level
        action = LEVEL_TO_ACTION[level]
        authz = check(session, action, profile, grants=self.grants)
        if not authz.allowed:
            self._audit_command(
                session,
                profile,
                argv,
                decision,
                decision_name="deny",
                deny_reason=authz.reason,
                result="blocked",
                error_code=ErrorCode.E08 if authz.reason not in {"unauthenticated", "locked"} else ErrorCode.E14,
            )
            raise AppError(
                ErrorCode.E14 if authz.reason in {"unauthenticated", "locked"} else ErrorCode.E08,
                authz.message,
                hint=f"deny_reason={authz.reason}",
            )
        if emergency_readonly or self.audit.is_chain_broken():
            if level != "L1":
                self._audit_command(
                    session, profile, argv, decision, "deny", "audit_broken", "blocked", ErrorCode.E15
                )
                raise AppError(ErrorCode.E15, "审计链完整性异常，已进入只读应急模式，禁止写命令与 L3/L4")
        if not decision.allowed:
            self._audit_command(
                session, profile, argv, decision, "deny", decision.deny_reason or "risk", "blocked", ErrorCode.E08
            )
            raise AppError(ErrorCode.E08, decision.message or decision.assessment.reason, hint=f"deny_reason={decision.deny_reason or 'risk'}")

        pending_seq = None
        needs_audit = level != "L1" or decision.decision != "allow"
        # 文档：写命令与全部 L3/L4 尝试（含被拦截）写审计。L1 不需要 pending。
        if level in {"L2", "L3", "L4"}:
            if not self.audit.writable or not self.audit._writable:
                raise AppError(ErrorCode.E13, "审计不可写，已拒绝执行写命令")
            if record_pending:
                pending_seq = self._audit_command(
                    session,
                    profile,
                    argv,
                    decision,
                    decision.decision,
                    None,
                    "pending",
                    None,
                )
        return ConsolePrepare(argv=argv, policy=decision, authz_ok=True, pending_seq=pending_seq)

    def execute(
        self,
        session: SessionContext,
        profile: ConnectionProfile,
        prepared: ConsolePrepare,
        gateway: RedisGateway,
        *,
        timeout_s: float = CONSOLE_TIMEOUT_S,
        runner: Callable[[list[str], float], Any] | None = None,
    ) -> CommandResult:
        started = datetime.now(timezone.utc)
        t0 = started.timestamp()
        try:
            exec_fn = runner or (lambda argv, timeout: gateway.execute(list(argv), timeout_s=timeout))
            raw = exec_fn(list(prepared.argv), timeout_s)
            duration = int((datetime.now(timezone.utc).timestamp() - t0) * 1000)
            result = CommandResult(
                ok=True,
                argv_name=prepared.argv[0],
                rows=_to_rows(raw),
                duration_ms=duration,
                hit=_hit_of(prepared.argv[0], raw),
            )
            self._append_result(session, profile, prepared, result, "ok")
            self._history(session, profile, prepared, "ok")
            return result
        except AppError as exc:
            duration = int((datetime.now(timezone.utc).timestamp() - t0) * 1000)
            result = CommandResult(
                ok=False,
                argv_name=prepared.argv[0],
                duration_ms=duration,
                error_code=exc.code,
                error_message=exc.message,
            )
            try:
                self._append_result(session, profile, prepared, result, "err")
            except AppError:
                # 结果回填失败：阻止后续写命令
                self.audit.mark_unwritable()
            self._history(session, profile, prepared, "err")
            raise

    def confirm_matches(self, profile: ConnectionProfile, typed: str) -> bool:
        return typed == profile.name

    def _audit_command(
        self,
        session: SessionContext | None,
        profile: ConnectionProfile,
        argv: tuple[str, ...] | list[str],
        decision: PolicyDecision,
        decision_name: str,
        deny_reason: str | None,
        result: str | None,
        error_code: str | None,
    ) -> Optional[int]:
        payload = {
            "actor_user_id": session.user_id if session else "anonymous",
            "actor_username": session.username if session else "anonymous",
            "actor_role": session.role if session else "viewer",
            "client_host": "localhost",
            "client_ip": None,
            "session_id": session.session_id if session else "n/a",
            "connection_id": profile.id,
            "connection_name": profile.name,
            "env": profile.env,
            "host": profile.host,
            "port": profile.port,
            "event": "command",
            "command_name": argv[0] if argv else None,
            "argv_masked": format_argv_masked(list(argv)),
            "argv_digest": argv_digest(list(argv)),
            "risk_level": decision.assessment.level,
            "decision": decision_name,
            "deny_reason": deny_reason,
            "result": result,
            "duration_ms": None,
            "impact_estimate": decision.assessment.impact_estimate or None,
            "error_code": error_code,
        }
        return self.audit.append(payload)

    def _append_result(
        self,
        session: SessionContext,
        profile: ConnectionProfile,
        prepared: ConsolePrepare,
        result: CommandResult,
        status: str,
    ) -> None:
        if prepared.pending_seq is None and prepared.policy.assessment.level == "L1":
            return
        payload = {
            "actor_user_id": session.user_id,
            "actor_username": session.username,
            "actor_role": session.role,
            "client_host": "localhost",
            "client_ip": None,
            "session_id": session.session_id,
            "connection_id": profile.id,
            "connection_name": profile.name,
            "env": profile.env,
            "host": profile.host,
            "port": profile.port,
            "event": "command_result",
            "command_name": prepared.argv[0],
            "argv_masked": format_argv_masked(list(prepared.argv)),
            "argv_digest": argv_digest(list(prepared.argv)),
            "risk_level": prepared.policy.assessment.level,
            "decision": prepared.policy.decision,
            "deny_reason": None,
            "result": status,
            "duration_ms": result.duration_ms,
            "impact_estimate": f"ref:{prepared.pending_seq}" if prepared.pending_seq else None,
            "error_code": result.error_code,
        }
        try:
            self.audit.append(payload)
        except AppError:
            self.audit.mark_unwritable()
            raise

    def _history(
        self,
        session: SessionContext,
        profile: ConnectionProfile,
        prepared: ConsolePrepare,
        result: str,
    ) -> None:
        try:
            self.store.add_history(
                connection_id=profile.id,
                actor=session.username,
                raw=format_argv_masked(list(prepared.argv)),
                argv_digest=argv_digest(list(prepared.argv)),
                risk_level=prepared.policy.assessment.level,
                decision=prepared.policy.decision,
                result=result,
                keep=CONSOLE_HISTORY_MAX,
            )
        except AppError:
            pass


def _to_rows(raw: Any) -> tuple[tuple[str, ...], ...]:
    if raw is None:
        return (("nil",),)
    if isinstance(raw, bool):
        return (("OK" if raw else "false",),)
    if isinstance(raw, bytes):
        return ((_cell(raw),),)
    if isinstance(raw, (int, float)):
        return ((str(raw),),)
    if isinstance(raw, str):
        return ((raw,),)
    if isinstance(raw, dict):
        return tuple((_cell(key), _cell(value)) for key, value in raw.items())
    if isinstance(raw, (list, tuple)):
        rows = []
        for item in raw:
            if isinstance(item, (list, tuple)):
                rows.append(tuple(_cell(x) for x in item))
            else:
                rows.append((_cell(item),))
        return tuple(rows)
    return ((str(raw),),)


def _cell(value: Any) -> str:
    if value is None:
        return "nil"
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8")
            if all(c.isprintable() or c in "\n\t\r" for c in text):
                return text
        except UnicodeDecodeError:
            pass
        return repr(value)
    return str(value)


def _hit_of(name: str, raw: Any) -> bool | None:
    if name in {"GET", "EXISTS"}:
        if raw is None or raw == 0 or raw == b"":
            return False
        return True
    return None
