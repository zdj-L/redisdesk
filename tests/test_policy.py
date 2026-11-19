from redisdesk.core.model import RuntimeFacts
from redisdesk.core.policy import PolicyEngine
from tests.conftest import make_profile

ENGINE = PolicyEngine()


def assess(argv, **kwargs):
    return ENGINE.assess(argv, make_profile(**kwargs))


def evaluate(argv, role="admin", **kwargs):
    unlock = kwargs.pop("unlock_reason", None)
    return ENGINE.evaluate(argv, make_profile(**kwargs), role, unlock_reason=unlock)


def test_l1_get_and_scan():
    assert assess(["GET", "k"]).level == "L1"
    assert assess(["SCAN", "0", "COUNT", "1000"]).level == "L1"
    assert evaluate(["PING"], role="viewer").allowed


def test_viewer_cannot_set():
    d = evaluate(["SET", "k", "v"], role="viewer")
    assert not d.allowed
    assert d.deny_reason == "role"


def test_operator_set_needs_confirm_but_flushall_denied():
    d = evaluate(["SET", "k", "v"], role="operator", protection="confirm")
    assert d.allowed and d.decision == "confirm"
    d = evaluate(["FLUSHALL"], role="operator", env="test", allow_l4=True)
    assert not d.allowed
    assert d.deny_reason == "role"


def test_readonly_blocks_writes():
    d = evaluate(["SET", "k", "v"], role="admin", protection="readonly")
    assert not d.allowed
    assert d.deny_reason == "protection"


def test_prod_flushall_permanently_blocked_even_for_admin():
    d = evaluate(["FLUSHALL"], role="admin", env="prod", protection="readonly", allow_l4=False)
    assert not d.allowed
    # 只读档或 L4 生产阻断都可以
    assert d.deny_reason in {"risk", "protection"}


def test_keys_is_l4_controlled_prod_blocked():
    a = assess(["KEYS", "*"], env="prod", protection="readonly")
    assert a.level == "L4"
    d = evaluate(["KEYS", "*"], role="admin", env="prod", protection="readonly")
    assert not d.allowed


def test_shutdown_debug_subscribe_blocked_for_admin():
    for argv in (["SHUTDOWN"], ["DEBUG", "SLEEP", "10"], ["SUBSCRIBE", "ch"], ["BLPOP", "q", "0"]):
        a = assess(argv, env="test", allow_l4=True)
        assert a.l4_permanent and a.hard_deny
        d = evaluate(argv, role="admin", env="test", allow_l4=True, unlock_reason="abcdefghijklmnopqrstuvwxyz")
        assert not d.allowed


def test_lrange_full_range_denied():
    a = assess(["LRANGE", "k", "0", "-1"])
    assert a.hard_deny
    a = assess(["LRANGE", "k", "0", "600"])
    assert a.hard_deny
    a = assess(["LRANGE", "k", "0", "99"])
    assert a.level == "L1" and not a.hard_deny


def test_zrange_zero_minus_one_denied():
    a = assess(["ZRANGE", "k", "0", "-1"])
    assert a.hard_deny


def test_xrange_requires_count():
    assert assess(["XRANGE", "s", "-", "+"]).hard_deny
    assert not assess(["XRANGE", "s", "-", "+", "COUNT", "50"]).hard_deny


def test_del_scale_upgrades_to_l3():
    small = assess(["DEL", "a"])
    assert small.level == "L2"
    keys = ["DEL"] + [f"k{i}" for i in range(51)]
    big = assess(keys)
    assert big.level == "L3"
    batch = ENGINE.assess(["DEL", "a", "b"], make_profile(), RuntimeFacts(from_batch_delete=True))
    assert batch.level == "L3"


def test_unknown_command_is_l3_and_readonly_denied():
    a = assess(["NEWCMD", "x"])
    assert a.level == "L3"
    d = evaluate(["NEWCMD", "x"], role="admin", protection="readonly")
    assert not d.allowed


def test_admin_l4_requires_reason_on_nonprod():
    d = evaluate(["FLUSHALL"], role="admin", env="test", protection="unrestricted", allow_l4=True, unlock_reason="short")
    assert not d.allowed
    d = evaluate(
        ["FLUSHALL"],
        role="admin",
        env="test",
        protection="unrestricted",
        allow_l4=True,
        unlock_reason="clearing-test-data",
    )
    assert d.allowed and d.requires_typed_confirm
