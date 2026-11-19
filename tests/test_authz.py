from redisdesk.core.authz import Action, StaticGrantResolver, check
from tests.conftest import make_profile, make_session


GRANTS = StaticGrantResolver({("c1", "admin"), ("c1", "operator"), ("c1", "viewer")})
NO_GRANTS = StaticGrantResolver()


def test_locked_and_unauthenticated():
    d = check(None, Action.EXEC_L1)
    assert not d.allowed and d.reason == "unauthenticated"
    d = check(make_session(unlocked=False), Action.EXEC_L1, make_profile(), grants=GRANTS)
    assert not d.allowed and d.reason == "locked"


def test_role_matrix():
    s = make_session("viewer")
    p = make_profile()
    assert check(s, Action.EXEC_L1, p, grants=GRANTS).allowed
    d = check(s, Action.EXEC_L2, p, grants=GRANTS)
    assert not d.allowed and d.reason == "role" and d.required_role == "operator"
    d = check(s, Action.REVEAL_SENSITIVE, p, grants=GRANTS)
    assert not d.allowed and d.reason == "role"
    assert check(make_session("operator"), Action.REVEAL_SENSITIVE, p, grants=GRANTS).allowed
    d = check(make_session("operator"), Action.EXEC_L4, p, grants=GRANTS)
    assert not d.allowed and d.reason == "role"
    assert check(make_session("admin"), Action.MANAGE_USERS).allowed


def test_missing_grant():
    d = check(make_session("admin"), Action.CONNECT, make_profile(), grants=NO_GRANTS)
    assert not d.allowed and d.reason == "grant"


def test_readonly_protection():
    p = make_profile(protection="readonly")
    d = check(make_session("admin"), Action.EXEC_L2, p, grants=GRANTS)
    assert not d.allowed and d.reason == "protection"


def test_prod_l4_no_unlock():
    p = make_profile(env="prod", protection="readonly", allow_l4=False)
    d = check(make_session("admin"), Action.EXEC_L4, p, grants=GRANTS)
    assert not d.allowed and d.reason == "protection"


def test_q9_view_connections_does_not_require_grant():
    assert check(make_session("viewer"), Action.VIEW_CONNECTIONS, grants=NO_GRANTS).allowed
