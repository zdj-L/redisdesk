from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from redisdesk.core.model import ConnectionProfile, Endpoint, SessionContext, TlsConfig
from redisdesk.infra.audit import AuditLog
from redisdesk.infra.store import Store


@pytest.fixture
def tmp_store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "redisdesk.db")
    yield store
    store.close()


@pytest.fixture
def audit(tmp_store: Store) -> AuditLog:
    return AuditLog(tmp_store)


def make_session(role: str = "admin", unlocked: bool = True) -> SessionContext:
    now = datetime.now(timezone.utc)
    return SessionContext(
        user_id="u1",
        username="alice",
        role=role,  # type: ignore[arg-type]
        login_at=now,
        last_active_at=now,
        unlocked=unlocked,
        session_id="s1",
    )


def make_profile(
    *,
    env: str = "dev",
    protection: str = "confirm",
    allow_l4: bool = False,
    tls: bool = True,
    name: str = "orders-dev",
    conn_id: str = "c1",
) -> ConnectionProfile:
    return ConnectionProfile(
        id=conn_id,
        name=name,
        topology="direct",
        transport="tcp",
        endpoint=Endpoint("127.0.0.1", 6379),
        env=env,  # type: ignore[arg-type]
        protection=protection,  # type: ignore[arg-type]
        allow_l4=allow_l4,
        tls=TlsConfig(enabled=tls, allow_plaintext=not tls, plaintext_reason="local-lab-only" if not tls else None),
    )
