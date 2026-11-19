"""会话状态、空闲计时、Windows 锁屏监听。"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from redisdesk.core.limits import SESSION_ABSOLUTE_HOURS, SESSION_IDLE_LOCK_MINUTES
from redisdesk.core.model import SessionContext


class SessionManager:
    def __init__(
        self,
        *,
        idle_minutes: int = SESSION_IDLE_LOCK_MINUTES,
        absolute_hours: int = SESSION_ABSOLUTE_HOURS,
        on_lock: Optional[Callable[[], None]] = None,
    ) -> None:
        self.idle_minutes = idle_minutes
        self.absolute_hours = absolute_hours
        self.on_lock = on_lock
        self.current: Optional[SessionContext] = None

    def create(self, *, user_id: str, username: str, role: str) -> SessionContext:
        now = datetime.now(timezone.utc)
        ctx = SessionContext(
            user_id=user_id,
            username=username,
            role=role,  # type: ignore[arg-type]
            login_at=now,
            last_active_at=now,
            unlocked=True,
            session_id=uuid.uuid4().hex,
            absolute_expires_at=now + timedelta(hours=self.absolute_hours),
        )
        self.current = ctx
        return ctx

    def touch(self, now: datetime | None = None) -> SessionContext | None:
        if self.current is None or not self.current.unlocked:
            return self.current
        clock = now or datetime.now(timezone.utc)
        self.current = SessionContext(
            **{**_asdict(self.current), "last_active_at": clock}  # type: ignore[arg-type]
        )
        if self.should_lock(clock):
            self.lock()
        return self.current

    def should_lock(self, now: datetime | None = None) -> bool:
        ctx = self.current
        if ctx is None or not ctx.unlocked:
            return False
        clock = now or datetime.now(timezone.utc)
        if ctx.absolute_expires_at and clock >= ctx.absolute_expires_at:
            return True
        idle = clock - ctx.last_active_at
        return idle >= timedelta(minutes=self.idle_minutes)

    def lock(self) -> None:
        if self.current is None:
            return
        self.current = SessionContext(**{**_asdict(self.current), "unlocked": False})  # type: ignore[arg-type]
        if self.on_lock:
            self.on_lock()

    def unlock(self) -> None:
        if self.current is None:
            return
        now = datetime.now(timezone.utc)
        self.current = SessionContext(
            **{**_asdict(self.current), "unlocked": True, "last_active_at": now}  # type: ignore[arg-type]
        )

    def clear(self) -> None:
        self.current = None

    def is_absolute_expired(self, now: datetime | None = None) -> bool:
        ctx = self.current
        if ctx is None or ctx.absolute_expires_at is None:
            return False
        clock = now or datetime.now(timezone.utc)
        return clock >= ctx.absolute_expires_at


def _asdict(ctx: SessionContext) -> dict:
    return {
        "user_id": ctx.user_id,
        "username": ctx.username,
        "role": ctx.role,
        "login_at": ctx.login_at,
        "last_active_at": ctx.last_active_at,
        "unlocked": ctx.unlocked,
        "session_id": ctx.session_id,
        "absolute_expires_at": ctx.absolute_expires_at,
    }


# Windows 锁屏：WTSRegisterSessionNotification。UI 层在有 QWidget 后调用。
WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK = 0x7
NOTIFY_FOR_THIS_SESSION = 0


def register_session_notification(hwnd: int) -> bool:
    if os.name != "nt":
        return False
    try:
        user32 = __import__("ctypes").windll.user32  # type: ignore[attr-defined]
        return bool(user32.WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION))
    except Exception:
        return False
