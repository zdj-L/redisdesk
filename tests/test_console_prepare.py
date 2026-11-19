import pytest

from redisdesk.app.services.console_service import ConsoleService
from redisdesk.core.authz import StaticGrantResolver
from redisdesk.core.errors import AppError
from tests.conftest import make_profile, make_session


def _svc(store, audit):
    grants = StaticGrantResolver({("c1", "admin"), ("c1", "operator"), ("c1", "viewer")})
    return ConsoleService(store, audit, grants)


def test_viewer_set_denied_and_audited(tmp_store, audit):
    svc = _svc(tmp_store, audit)
    with pytest.raises(AppError) as exc:
        svc.prepare(make_session("viewer"), make_profile(), "SET k v")
    assert exc.value.code == "E08"
    rows = audit.query(event="command")
    assert rows and rows[0]["decision"] == "deny"
    assert rows[0]["deny_reason"] == "role"


def test_keys_denied_without_network(tmp_store, audit):
    svc = _svc(tmp_store, audit)
    with pytest.raises(AppError):
        svc.prepare(make_session("admin"), make_profile(env="prod", protection="readonly"), "KEYS *")
    rows = audit.query(event="command")
    assert rows and rows[0]["decision"] == "deny"


def test_l2_writes_pending_audit(tmp_store, audit):
    svc = _svc(tmp_store, audit)
    prepared = svc.prepare(make_session("admin"), make_profile(protection="confirm"), 'SET k "v"')
    assert prepared.pending_seq is not None
    rows = audit.query()
    assert any(r["result"] == "pending" and r["event"] == "command" for r in rows)


def test_broken_chain_blocks_writes(tmp_store, audit):
    svc = _svc(tmp_store, audit)
    audit.append(
        {
            "actor_user_id": "u1",
            "actor_username": "alice",
            "actor_role": "admin",
            "client_host": "localhost",
            "client_ip": None,
            "session_id": "s1",
            "connection_id": None,
            "connection_name": None,
            "env": None,
            "host": None,
            "port": None,
            "event": "auth_login",
            "command_name": None,
            "argv_masked": None,
            "argv_digest": None,
            "risk_level": None,
            "decision": "allow",
            "deny_reason": None,
            "result": "ok",
            "duration_ms": None,
            "impact_estimate": None,
            "error_code": None,
        }
    )
    tmp_store.execute("UPDATE audit_log SET decision='tamper' WHERE seq=1")
    audit.verify_on_start()
    assert audit.is_chain_broken()
    with pytest.raises(AppError) as exc:
        svc.prepare(make_session("admin"), make_profile(), "SET k v")
    assert exc.value.code == "E15"
