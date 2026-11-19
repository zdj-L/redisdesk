from __future__ import annotations

from redisdesk.app.services.auth_service import AuthService
from redisdesk.infra.audit import AuditLog
from redisdesk.infra.session import SessionManager


def test_ensure_local_session_creates_admin(tmp_store):
    auth = AuthService(tmp_store, AuditLog(tmp_store), SessionManager())
    first = auth.ensure_local_session()
    assert first.role == "admin"
    assert first.username == "local"
    assert first.unlocked is True
    row = tmp_store.get_user_by_username("local")
    assert row is not None
    assert row["display_name"] == "本机操作员"

    second = auth.ensure_local_session()
    assert second.user_id == first.user_id
    assert second.username == "local"
    # 会话编号每次打开都会更新
    assert second.session_id != first.session_id


def test_ensure_local_session_reuses_existing_admin(tmp_store, audit):
    sessions = SessionManager()
    auth = AuthService(tmp_store, audit, sessions)
    user_id, _recovery = auth.bootstrap_admin("alice", "Correct-Horse-Battery-1")
    ctx = auth.ensure_local_session()
    assert ctx.user_id == user_id
    assert ctx.username == "alice"
    assert ctx.role == "admin"
    assert tmp_store.get_user_by_username("local") is None
