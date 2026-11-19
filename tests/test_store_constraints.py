import pytest

from redisdesk.core.errors import AppError
from redisdesk.core.model import ConnectionProfile, Endpoint, TlsConfig
from redisdesk.infra.store import Store


def _profile(**kwargs):
    base = dict(
        id="c-prod",
        name="prod-1",
        topology="direct",
        transport="tcp",
        endpoint=Endpoint("10.0.0.1", 6379),
        env="prod",
        protection="readonly",
        allow_l4=False,
        tls=TlsConfig(enabled=True),
    )
    base.update(kwargs)
    return ConnectionProfile(**base)  # type: ignore[arg-type]


def test_prod_cannot_be_writable(tmp_store: Store):
    with pytest.raises(AppError) as exc:
        tmp_store.save_connection(_profile(protection="confirm"), created_by="alice")
    assert exc.value.code == "E11"


def test_prod_cannot_allow_l4(tmp_store: Store):
    with pytest.raises(AppError):
        tmp_store.save_connection(_profile(allow_l4=True), created_by="alice")


def test_plaintext_requires_reason(tmp_store: Store):
    tls = TlsConfig(enabled=False, allow_plaintext=True, plaintext_reason="short")
    with pytest.raises(AppError):
        tmp_store.save_connection(
            _profile(id="c-plain", name="plain", env="dev", protection="confirm", tls=tls),
            created_by="alice",
        )


def test_plaintext_with_reason_ok(tmp_store: Store):
    tls = TlsConfig(enabled=False, allow_plaintext=True, plaintext_reason="local-dev-only")
    tmp_store.save_connection(
        _profile(id="c-plain", name="plain", env="dev", protection="confirm", tls=tls),
        created_by="alice",
    )
    saved = tmp_store.get_connection("c-plain")
    assert saved is not None
    assert saved.tls.allow_plaintext
    grants = tmp_store.list_grants("c-plain")
    assert any(g.role == "admin" for g in grants)
