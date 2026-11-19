"""ScanIterator / ScanSession 引擎。只走 SCAN，禁止 KEYS。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from threading import Event
from typing import Callable, Iterable, Optional

from redisdesk.adapters.gateway import RedisGateway
from redisdesk.core.limits import (
    KEY_PAGE_SIZE,
    META_PIPELINE_BATCH,
    SCAN_BUDGET_CURSOR_STEPS,
    SCAN_BUFFER_MAX_ROWS,
    SCAN_COUNT,
    SCAN_DEDUPE_WINDOW,
    SCAN_TIME_BUDGET_S,
    UI_EMIT_BATCH_ROWS,
)
from redisdesk.core.model import KeyMeta, ScanQuery, ScanSession, ScanState

# 禁止出现在本文件的命令名（测试会静态检查）。
_FORBIDDEN = ("KEYS", "HGETALL", "SMEMBERS", "HKEYS", "HVALS")


class CancellationToken:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def throw_if_cancelled(self) -> bool:
        return self.cancelled


EmitFn = Callable[[list[KeyMeta], ScanSession], None]


@dataclass
class ScanEngineState:
    session: ScanSession
    buffer: list[KeyMeta] = field(default_factory=list)
    seen: list[bytes] = field(default_factory=list)
    seen_set: set[bytes] = field(default_factory=set)


class ScanEngine:
    """单机/哨兵：一条扫描游标。填充一页、预算、去重、空批继续。"""

    def __init__(
        self,
        gateway: RedisGateway,
        query: ScanQuery,
        *,
        node_id: str | None = "default",
        page_size: int = KEY_PAGE_SIZE,
        scan_count: int = SCAN_COUNT,
        fetch_ttl: bool = True,
        time_budget_s: int = SCAN_TIME_BUDGET_S,
        cursor_budget: int = SCAN_BUDGET_CURSOR_STEPS,
        token: CancellationToken | None = None,
    ) -> None:
        self.gateway = gateway
        self.query = query
        self.node_id = node_id or "default"
        self.page_size = page_size
        self.scan_count = min(max(scan_count, 50), 10_000)
        self.fetch_ttl = fetch_ttl
        self.time_budget_s = time_budget_s
        self.cursor_budget = cursor_budget
        self.token = token or CancellationToken()
        self._state = ScanEngineState(
            session=ScanSession(
                session_id=uuid.uuid4().hex,
                query=query,
                cursors={self.node_id: 0},
                state="idle",
            )
        )

    @property
    def session(self) -> ScanSession:
        return self._state.session

    @property
    def buffer(self) -> list[KeyMeta]:
        return list(self._state.buffer)

    def start(self) -> ScanSession:
        st = self._state.session
        self._state.session = ScanSession(
            session_id=st.session_id,
            query=self.query,
            cursors={self.node_id: 0},
            scanned=0,
            matched=0,
            buffered=0,
            state="running",
            started_at=_now_iso(),
            elapsed_ms=0,
            budget_exhausted=False,
        )
        self._state.buffer.clear()
        self._state.seen.clear()
        self._state.seen_set.clear()
        return self._state.session

    def pause(self) -> ScanSession:
        return self._replace(state="paused")

    def resume(self) -> ScanSession:
        if self._state.session.state in {"finished", "cancelled", "error"}:
            return self._state.session
        return self._replace(state="running")

    def cancel(self) -> ScanSession:
        self.token.cancel()
        return self._replace(state="cancelled")

    def fill_page(self, *, emit: EmitFn | None = None) -> list[KeyMeta]:
        """推进游标直到凑满一页、结束、预算耗尽或取消。空批必须继续。"""
        if self._state.session.state == "idle":
            self.start()
        if self._state.session.state in {"finished", "cancelled", "error"}:
            return []
        self._replace(state="running")

        started = time.perf_counter()
        emitted: list[KeyMeta] = []
        pending_emit: list[KeyMeta] = []
        client_filter = self._needs_client_filter()

        while True:
            if self.token.cancelled:
                self._replace(state="cancelled", elapsed_ms=_elapsed_ms(started))
                break
            if len(self._unconsumed_new(emitted)) >= self.page_size:
                break
            if len(self._state.buffer) >= SCAN_BUFFER_MAX_ROWS:
                self._replace(state="paused", budget_exhausted=False, elapsed_ms=_elapsed_ms(started))
                break

            cursor = self._state.session.cursors.get(self.node_id, 0)
            key_type = self.query.key_type if self.gateway.capabilities.supports_scan_type else None
            # 类型过滤在 <6.0 时不能传 TYPE，需客户端 pipeline TYPE
            try:
                next_cursor, batch, _err = self.gateway.scan(
                    self.node_id if self.node_id != "default" else None,
                    cursor,
                    match=self.query.pattern,
                    count=self.query.count or self.scan_count,
                    key_type=key_type,
                )
            except Exception as exc:  # noqa: BLE001
                self._replace(state="error", error=str(exc), elapsed_ms=_elapsed_ms(started))
                break

            batch = self._dedupe(batch)
            scanned = self._state.session.scanned + len(batch)
            metas = self._to_metas(batch)
            if self.fetch_ttl or (not self.gateway.capabilities.supports_scan_type and self.query.key_type):
                metas = self._attach_meta(metas)
            metas = self._apply_filters(metas)

            self._state.buffer.extend(metas)
            # 缓冲超限时丢弃由调用方处理；此处先截断提示暂停
            pending_emit.extend(metas)
            emitted.extend(metas)
            new_state: ScanState = "running"
            budget_hit = False
            if next_cursor == 0:
                new_state = "finished"
            steps = scanned
            elapsed = time.perf_counter() - started + (self._state.session.elapsed_ms / 1000.0)
            if client_filter and (steps >= self.cursor_budget or elapsed >= self.time_budget_s):
                new_state = "paused"
                budget_hit = True
            self._replace(
                cursors={**self._state.session.cursors, self.node_id: next_cursor},
                scanned=scanned,
                matched=len(self._state.buffer),
                buffered=len(self._state.buffer),
                state=new_state,
                elapsed_ms=int(elapsed * 1000),
                budget_exhausted=budget_hit,
            )
            if emit and len(pending_emit) >= UI_EMIT_BATCH_ROWS:
                emit(pending_emit, self._state.session)
                pending_emit = []
            if new_state in {"finished", "paused", "cancelled", "error"}:
                break
            # 空批：继续循环，不得当作结束（next_cursor==0 才结束）

        if emit and pending_emit:
            emit(pending_emit, self._state.session)
        return emitted[: self.page_size] if False else self._last_page(emitted)

    def _last_page(self, emitted: list[KeyMeta]) -> list[KeyMeta]:
        if len(emitted) <= self.page_size:
            return emitted
        return emitted[: self.page_size]

    def _unconsumed_new(self, emitted: list[KeyMeta]) -> list[KeyMeta]:
        return emitted

    def _needs_client_filter(self) -> bool:
        if self.query.ttl_min is not None or self.query.ttl_max is not None:
            return True
        if self.query.size_min is not None or self.query.size_max is not None:
            return True
        if self.query.key_type and not self.gateway.capabilities.supports_scan_type:
            return True
        return False

    def _dedupe(self, batch: Iterable[bytes]) -> list[bytes]:
        unique: list[bytes] = []
        for key in batch:
            if key in self._state.seen_set:
                continue
            self._state.seen_set.add(key)
            self._state.seen.append(key)
            unique.append(key)
        overflow = len(self._state.seen) - SCAN_DEDUPE_WINDOW
        if overflow > 0:
            drop = self._state.seen[:overflow]
            self._state.seen = self._state.seen[overflow:]
            for k in drop:
                self._state.seen_set.discard(k)
        return unique

    def _to_metas(self, batch: list[bytes]) -> list[KeyMeta]:
        rows: list[KeyMeta] = []
        for raw in batch:
            try:
                name = raw.decode("utf-8")
            except UnicodeDecodeError:
                name = raw.decode("utf-8", errors="surrogateescape")
            rows.append(KeyMeta(key=name, node_id=None if self.node_id == "default" else self.node_id))
        return rows

    def _attach_meta(self, rows: list[KeyMeta]) -> list[KeyMeta]:
        if not rows:
            return rows
        commands: list[list[str]] = []
        need_type = bool(self.query.key_type and not self.gateway.capabilities.supports_scan_type)
        for row in rows:
            if self.fetch_ttl:
                commands.append(["TTL", row.key])
            if need_type:
                commands.append(["TYPE", row.key])
        if not commands:
            return rows
        try:
            results = self.gateway.pipeline(commands, batch=META_PIPELINE_BATCH)
        except Exception:
            return rows
        idx = 0
        updated: list[KeyMeta] = []
        for row in rows:
            ttl = row.ttl_s
            typ = row.key_type
            if self.fetch_ttl:
                ttl = _as_int(results[idx], default=-1)
                idx += 1
            if need_type:
                typ = _as_str(results[idx])
                idx += 1
            updated.append(
                KeyMeta(
                    key=row.key,
                    key_type=typ,
                    ttl_s=ttl,
                    size_bytes=row.size_bytes,
                    encoding=row.encoding,
                    node_id=row.node_id,
                    slot=row.slot,
                )
            )
        return updated

    def _apply_filters(self, rows: list[KeyMeta]) -> list[KeyMeta]:
        out: list[KeyMeta] = []
        want_type = self.query.key_type
        client_type = want_type and not self.gateway.capabilities.supports_scan_type
        for row in rows:
            if client_type and row.key_type and row.key_type.lower() != want_type.lower():
                continue
            if self.query.ttl_min is not None and (row.ttl_s < 0 or row.ttl_s < self.query.ttl_min):
                continue
            if self.query.ttl_max is not None and (row.ttl_s < 0 or row.ttl_s > self.query.ttl_max):
                continue
            if self.query.size_min is not None and (row.size_bytes is None or row.size_bytes < self.query.size_min):
                continue
            if self.query.size_max is not None and (row.size_bytes is None or row.size_bytes > self.query.size_max):
                continue
            out.append(row)
        return out

    def _replace(self, **kwargs: object) -> ScanSession:
        cur = self._state.session
        data = {
            "session_id": cur.session_id,
            "query": cur.query,
            "cursors": cur.cursors,
            "scanned": cur.scanned,
            "matched": cur.matched,
            "buffered": cur.buffered,
            "state": cur.state,
            "started_at": cur.started_at,
            "elapsed_ms": cur.elapsed_ms,
            "error": cur.error,
            "budget_exhausted": cur.budget_exhausted,
        }
        data.update(kwargs)
        self._state.session = ScanSession(**data)  # type: ignore[arg-type]
        return self._state.session


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def _as_int(value: object, default: int = -1) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_str(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="surrogateescape")
    return str(value)
