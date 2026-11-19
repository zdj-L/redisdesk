"""会话、口令策略与本地单操作员会话。登录/恢复 API 仍保留给后端测试使用，界面不再走登录。"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from redisdesk.core.authz import Action, check
from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.limits import (
    LOGIN_DELAY_AFTER_FAIL,
    LOGIN_FAIL_DAILY_LOCK_MINUTES,
    LOGIN_FAIL_DAILY_THRESHOLD,
    LOGIN_FAIL_LOCK_MINUTES,
    LOGIN_FAIL_LOCK_THRESHOLD,
    PASSWORD_HISTORY_COUNT,
    PASSWORD_MAX_AGE_DAYS,
    RECOVERY_CODE_LENGTH,
)
from redisdesk.core.model import SessionContext
from redisdesk.infra import crypto
from redisdesk.infra.audit import AuditLog
from redisdesk.infra.session import SessionManager
from redisdesk.infra.store import Store, row_to_user, utc_now


class AuthService:
    def __init__(self, store: Store, audit: AuditLog, sessions: SessionManager | None = None) -> None:
        self.store = store
        self.audit = audit
        self.sessions = sessions or SessionManager()

    LOCAL_OPERATOR_USERNAME = "local"
    LOCAL_OPERATOR_DISPLAY_NAME = "本机操作员"

    def ensure_local_session(self) -> SessionContext:
        """打开本地单操作员管理员会话，无需交互式登录。

        若库中已有可用管理员（例如早期引导创建），则复用第一条；
        否则创建名为 local 的本机管理员。口令随机生成且不向界面展示。
        审计写入失败不阻止进入主窗口。
        """
        import secrets

        row = self.store.get_user_by_username(self.LOCAL_OPERATOR_USERNAME)
        created = False
        if row is None:
            admins = [u for u in self.store.list_users() if u["role"] == "admin" and not u["disabled"]]
            row = admins[0] if admins else None
        if row is None:
            pwd_hash = crypto.hash_password(secrets.token_urlsafe(48))
            user_id = self.store.insert_user(
                username=self.LOCAL_OPERATOR_USERNAME,
                display_name=self.LOCAL_OPERATOR_DISPLAY_NAME,
                role="admin",
                password_hash=pwd_hash,
                recovery_code_hash=None,
            )
            row = self.store.get_user(user_id)
            created = True
        assert row is not None
        self.store.update_user(row["id"], last_login_at=utc_now())
        ctx = self.sessions.create(user_id=row["id"], username=row["username"], role=row["role"])
        try:
            self._audit(
                event="auth_local_bootstrap" if created else "auth_local_session",
                actor_user_id=row["id"],
                actor_username=row["username"],
                actor_role=row["role"],
                session_id=ctx.session_id,
                decision="allow",
                result="ok",
            )
        except AppError:
            pass
        return ctx

    def needs_bootstrap(self) -> bool:
        return self.store.count_admins() == 0

    def bootstrap_admin(self, username: str, password: str, display_name: str = "") -> tuple[str, str]:
        if not self.needs_bootstrap():
            raise AppError(ErrorCode.E08, "已存在管理员，不能重复初始化")
        errors = crypto.validate_password_policy(password, username=username)
        if errors:
            raise AppError(ErrorCode.E08, "；".join(errors))
        recovery = crypto.generate_recovery_code(RECOVERY_CODE_LENGTH)
        pwd_hash = crypto.hash_password(password)
        rec_hash = crypto.hash_password(recovery)
        user_id = self.store.insert_user(
            username=username.strip(),
            display_name=display_name or username.strip(),
            role="admin",
            password_hash=pwd_hash,
            recovery_code_hash=rec_hash,
        )
        self._audit(
            event="auth_bootstrap",
            actor_user_id=user_id,
            actor_username=username,
            actor_role="admin",
            session_id="bootstrap",
            decision="allow",
            result="ok",
        )
        return user_id, recovery

    def login(self, username: str, password: str, *, client_host: str = "localhost", client_ip: str | None = None) -> SessionContext:
        row = self.store.get_user_by_username(username.strip())
        # 统一提示，不透露账号是否存在
        def fail(delay_count: int = 0, row_id: str | None = None) -> None:
            if delay_count >= LOGIN_DELAY_AFTER_FAIL:
                time.sleep(1)
            self._audit(
                event="auth_login",
                actor_user_id=row_id or "unknown",
                actor_username=username,
                actor_role="unknown",
                session_id="login",
                decision="deny",
                result="err",
                deny_reason="unauthenticated",
                client_host=client_host,
                client_ip=client_ip,
            )
            raise AppError(ErrorCode.E14, "用户名或口令错误")

        if row is None:
            fail()
        assert row is not None
        if row["disabled"]:
            fail(row_id=row["id"])
        now = datetime.now(timezone.utc)
        locked_until = _parse_ts(row["locked_until"])
        if locked_until and now < locked_until:
            self._audit(
                event="auth_lockout",
                actor_user_id=row["id"],
                actor_username=username,
                actor_role=row["role"],
                session_id="login",
                decision="deny",
                result="blocked",
                deny_reason="locked",
                client_host=client_host,
                client_ip=client_ip,
            )
            raise AppError(ErrorCode.E14, "用户名或口令错误")
        if not crypto.verify_password(password, row["password_hash"]):
            self._register_failure(row, now)
            fail(int(row["failed_count"]) + 1, row["id"])
        self.store.update_user(
            row["id"],
            failed_count=0,
            locked_until=None,
            last_login_at=utc_now(),
        )
        ctx = self.sessions.create(user_id=row["id"], username=row["username"], role=row["role"])
        self._audit(
            event="auth_login",
            actor_user_id=row["id"],
            actor_username=row["username"],
            actor_role=row["role"],
            session_id=ctx.session_id,
            decision="allow",
            result="ok",
            client_host=client_host,
            client_ip=client_ip,
        )
        return ctx

    def unlock(self, password: str) -> SessionContext:
        ctx = self.sessions.current
        if ctx is None:
            raise AppError(ErrorCode.E14, "请先登录")
        if self.sessions.is_absolute_expired():
            self.sessions.clear()
            raise AppError(ErrorCode.E14, "会话已超过绝对时长，请重新登录")
        row = self.store.get_user(ctx.user_id)
        if row is None or not crypto.verify_password(password, row["password_hash"]):
            raise AppError(ErrorCode.E14, "用户名或口令错误")
        self.sessions.unlock()
        assert self.sessions.current is not None
        return self.sessions.current

    def change_password(self, session: SessionContext, old: str, new: str) -> None:
        row = self.store.get_user(session.user_id)
        if row is None or not crypto.verify_password(old, row["password_hash"]):
            raise AppError(ErrorCode.E14, "用户名或口令错误")
        errors = crypto.validate_password_policy(new, username=row["username"])
        if errors:
            raise AppError(ErrorCode.E08, "；".join(errors))
        history = self.store.password_history_hashes(session.user_id, PASSWORD_HISTORY_COUNT)
        for item in history:
            if crypto.verify_password(new, item):
                raise AppError(ErrorCode.E08, "口令不得与最近使用过的口令重复")
        new_hash = crypto.hash_password(new)
        self.store.update_user(session.user_id, password_hash=new_hash, password_updated_at=utc_now(), must_change_password=0)
        self.store.add_password_history(session.user_id, new_hash)
        self._audit(
            event="user_change",
            actor_user_id=session.user_id,
            actor_username=session.username,
            actor_role=session.role,
            session_id=session.session_id,
            decision="allow",
            result="ok",
        )

    def recover_admin(self, username: str, recovery_code: str, new_password: str) -> None:
        row = self.store.get_user_by_username(username.strip())
        if row is None or row["role"] != "admin" or not row["recovery_code_hash"]:
            raise AppError(ErrorCode.E14, "用户名或口令错误")
        if not crypto.verify_password(recovery_code, row["recovery_code_hash"]):
            raise AppError(ErrorCode.E14, "用户名或口令错误")
        errors = crypto.validate_password_policy(new_password, username=username)
        if errors:
            raise AppError(ErrorCode.E08, "；".join(errors))
        new_hash = crypto.hash_password(new_password)
        # 恢复码一次性使用
        self.store.update_user(
            row["id"],
            password_hash=new_hash,
            password_updated_at=utc_now(),
            recovery_code_hash=None,
            failed_count=0,
            locked_until=None,
            must_change_password=0,
        )
        self._audit(
            event="auth_recover",
            actor_user_id=row["id"],
            actor_username=username,
            actor_role="admin",
            session_id="recover",
            decision="allow",
            result="ok",
        )

    def _register_failure(self, row, now: datetime) -> None:
        failed = int(row["failed_count"]) + 1
        daily = int(row["daily_fail_count"] or 0) + 1
        window = _parse_ts(row["daily_fail_window"])
        if window is None or now - window >= timedelta(hours=24):
            daily = 1
            window = now
        locked_until = None
        event = "auth_login"
        if daily >= LOGIN_FAIL_DAILY_THRESHOLD:
            locked_until = now + timedelta(minutes=LOGIN_FAIL_DAILY_LOCK_MINUTES)
            event = "auth_lockout"
        elif failed >= LOGIN_FAIL_LOCK_THRESHOLD:
            locked_until = now + timedelta(minutes=LOGIN_FAIL_LOCK_MINUTES)
            event = "auth_lockout"
        self.store.update_user(
            row["id"],
            failed_count=failed,
            daily_fail_count=daily,
            daily_fail_window=window.isoformat() if window else None,
            locked_until=locked_until.isoformat() if locked_until else None,
        )
        if event == "auth_lockout":
            try:
                self._audit(
                    event=event,
                    actor_user_id=row["id"],
                    actor_username=row["username"],
                    actor_role=row["role"],
                    session_id="login",
                    decision="deny",
                    result="blocked",
                    deny_reason="locked",
                )
            except AppError:
                pass

    def _audit(self, **kwargs) -> None:  # noqa: ANN003
        try:
            payload = {
                "actor_user_id": kwargs.get("actor_user_id", "system"),
                "actor_username": kwargs.get("actor_username", "system"),
                "actor_role": kwargs.get("actor_role", "admin"),
                "client_host": kwargs.get("client_host") or "localhost",
                "client_ip": kwargs.get("client_ip"),
                "session_id": kwargs.get("session_id") or "n/a",
                "connection_id": None,
                "connection_name": None,
                "env": None,
                "host": None,
                "port": None,
                "event": kwargs["event"],
                "command_name": None,
                "argv_masked": None,
                "argv_digest": None,
                "risk_level": None,
                "decision": kwargs.get("decision", "allow"),
                "deny_reason": kwargs.get("deny_reason"),
                "result": kwargs.get("result"),
                "duration_ms": None,
                "impact_estimate": None,
                "error_code": kwargs.get("error_code"),
            }
            self.audit.append(payload)
        except AppError:
            # 登录事件写失败不应静默吞掉（连接事件 fail-closed），但引导阶段空库应能写。
            raise

    def password_expired(self, user_id: str) -> bool:
        if PASSWORD_MAX_AGE_DAYS <= 0:
            return False
        row = self.store.get_user(user_id)
        if row is None or not row["password_updated_at"]:
            return False
        updated = _parse_ts(row["password_updated_at"])
        if updated is None:
            return False
        return datetime.now(timezone.utc) - updated >= timedelta(days=PASSWORD_MAX_AGE_DAYS)


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None
