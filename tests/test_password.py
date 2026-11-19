from redisdesk.infra.crypto import hash_password, validate_password_policy, verify_password


def test_policy_rejects_short_and_weak_and_username():
    assert validate_password_policy("short")
    assert any("常见" in e or "常见" in e for e in validate_password_policy("password123"))
    assert any("用户名" in e for e in validate_password_policy("AliceHasALong1!", username="alice"))
    assert validate_password_policy("CorrectHorseBattery1") == []


def test_hash_and_verify():
    encoded = hash_password("CorrectHorseBattery1")
    assert encoded.startswith("scrypt$32768$8$1$")
    assert verify_password("CorrectHorseBattery1", encoded)
    assert not verify_password("wrong-password-1", encoded)
