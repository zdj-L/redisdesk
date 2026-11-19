from redisdesk.core.limits import AUDIT_CHAIN_GENESIS
from redisdesk.infra.audit_chain import compute_record_hash, verify_chain
from redisdesk.infra.store import utc_now


def _row(seq: int, prev: str, event: str = "command") -> dict:
    base = {
        "seq": seq,
        "ts": "2026-11-10T00:00:00.000Z",
        "actor_user_id": "u1",
        "actor_username": "alice",
        "actor_role": "admin",
        "client_host": "localhost",
        "client_ip": None,
        "session_id": "s1",
        "connection_id": "c1",
        "connection_name": "dev",
        "env": "dev",
        "host": "127.0.0.1",
        "port": 6379,
        "event": event,
        "command_name": "SET",
        "argv_masked": "SET k ***",
        "argv_digest": "abcd",
        "risk_level": "L2",
        "decision": "allow",
        "deny_reason": None,
        "result": "ok",
        "duration_ms": 3,
        "impact_estimate": None,
        "error_code": None,
        "hash_algo": "SHA-256",
        "prev_hash": prev,
    }
    base["record_hash"] = compute_record_hash(base, prev)
    return base


def test_genesis_and_append():
    r1 = _row(1, AUDIT_CHAIN_GENESIS)
    r2 = _row(2, r1["record_hash"])
    status = verify_chain([r1, r2], expected_last_seq=2, expected_last_hash=r2["record_hash"])
    assert status.ok
    assert len(r1["record_hash"]) == 64
    assert r1["prev_hash"] == AUDIT_CHAIN_GENESIS


def test_tamper_detected():
    r1 = _row(1, AUDIT_CHAIN_GENESIS)
    r2 = _row(2, r1["record_hash"])
    r2["command_name"] = "FLUSHALL"
    status = verify_chain([r1, r2])
    assert not status.ok
    assert status.broken_at_seq == 2


def test_store_append_and_fail_closed(tmp_store, audit):
    seq = audit.append(
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
    assert seq == 1
    status = audit.verify_on_start()
    assert status.ok
    # 篡改一行后启动校验失败
    tmp_store.execute("UPDATE audit_log SET decision='deny' WHERE seq=1")
    status = audit.verify_on_start()
    assert not status.ok
    assert audit.is_chain_broken()


def test_empty_chain_ok(audit):
    status = audit.verify_on_start()
    assert status.ok
