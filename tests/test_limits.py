from redisdesk.core import limits


def test_appendix_a_spot_checks():
    assert limits.SCAN_COUNT == 1000
    assert limits.KEY_PAGE_SIZE == 500
    assert limits.SCAN_BUDGET_CURSOR_STEPS == 2_000_000
    assert limits.TLS_VERSION_MIN == "TLSv1.2"
    assert limits.TLS_VERIFY_HOSTNAME is True
    assert limits.PLAINTEXT_REASON_MIN_CHARS == 10
    assert limits.PASSWORD_MIN_LENGTH == 12
    assert limits.SESSION_IDLE_LOCK_MINUTES == 10
    assert limits.SESSION_ABSOLUTE_HOURS == 8
    assert limits.AUDIT_RETENTION_DAYS == 180
    assert limits.AUDIT_CHAIN_GENESIS == "0" * 64
    assert limits.AUDIT_FAIL_CLOSED is True
    assert limits.SENSITIVE_REVEAL_TTL_S == 60
    assert limits.SCRYPT_N == 32768
    assert limits.DEFAULTS["batch_delete_total_max"] == 10_000
    assert "scan_count" in limits.TUNABLE_PARAMETERS
    assert "batch_delete_total_max" not in limits.TUNABLE_PARAMETERS
