"""审计写入（只追加）+ 查询。fail-closed：写入失败向上抛出 E13。"""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping, Optional

from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.limits import (
    AUDIT_CHAIN_GENESIS,
    AUDIT_FAIL_CLOSED,
    AUDIT_HASH_ALGO,
    AUDIT_INTEGRITY_FULL_CHECK_MAX_ROWS,
    AUDIT_INTEGRITY_START_SAMPLE,
    AUDIT_MAX_ROWS,
)
from redisdesk.infra.audit_chain import ChainStatus, compute_record_hash, verify_chain
from redisdesk.infra.store import Store, utc_now

_AUDIT_COLUMNS = (
    "ts",
    "actor_user_id",
    "actor_username",
    "actor_role",
    "client_host",
    "client_ip",
    "session_id",
    "connection_id",
    "connection_name",
    "env",
    "host",
    "port",
    "event",
    "command_name",
    "argv_masked",
    "argv_digest",
    "risk_level",
    "decision",
    "deny_reason",
    "result",
    "duration_ms",
    "impact_estimate",
    "error_code",
    "hash_algo",
    "prev_hash",
    "record_hash",
)


class AuditLog:
    def __init__(self, store: Store) -> None:
        self.store = store
        self._writable = True
        self._broken_at: Optional[int] = None

    @property
    def writable(self) -> bool:
        return self._writable and AUDIT_FAIL_CLOSED

    @property
    def broken_at_seq(self) -> Optional[int]:
        return self._broken_at

    def is_chain_broken(self) -> bool:
        if self._broken_at is not None:
            return True
        row = self.store.execute(
            "SELECT broken_at_seq FROM audit_chain_state WHERE id=1"
        ).fetchone()
        if row and row["broken_at_seq"] is not None:
            self._broken_at = int(row["broken_at_seq"])
            return True
        return False

    def mark_unwritable(self) -> None:
        self._writable = False

    def append(self, record: Mapping[str, Any]) -> int:
        """在同一事务内 INSERT audit_log + UPDATE audit_chain_state。失败抛 E13。"""
        if not self._writable:
            raise AppError(ErrorCode.E13, "审计不可写，已拒绝执行写命令")
        try:
            with self.store.tx() as conn:
                state = conn.execute(
                    "SELECT last_seq, last_hash, broken_at_seq FROM audit_chain_state WHERE id=1"
                ).fetchone()
                if state is None:
                    raise AppError(ErrorCode.E13, "审计链状态缺失")
                prev_hash = state["last_hash"] or AUDIT_CHAIN_GENESIS
                payload = {k: record.get(k) for k in _AUDIT_COLUMNS}
                payload["hash_algo"] = AUDIT_HASH_ALGO
                payload["prev_hash"] = prev_hash
                payload["ts"] = payload["ts"] or utc_now()
                payload["record_hash"] = ""  # 占位，稍后按真实 seq 计算

                conn.execute(
                    f"""INSERT INTO audit_log({", ".join(_AUDIT_COLUMNS)})
                        VALUES ({", ".join("?" for _ in _AUDIT_COLUMNS)})""",
                    tuple(payload[k] for k in _AUDIT_COLUMNS),
                )
                seq = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                # 若 prev_hash 与链状态不一致（并发/篡改），拒绝
                # last_insert_rowid 即 seq（AUTOINCREMENT）
                payload["seq"] = seq
                record_hash = compute_record_hash(payload, prev_hash)
                conn.execute(
                    "UPDATE audit_log SET record_hash=? WHERE seq=?",
                    (record_hash, seq),
                )
                # 注意：文档要求不 UPDATE 原记录的业务字段；此处仅回填同事务内尚未对外可见的 record_hash。
                # 为保持哈希可复算，canonical 使用回填后的 record（含最终 record_hash 之外的字段，含 seq）。
                now = utc_now()
                conn.execute(
                    """UPDATE audit_chain_state
                       SET last_seq=?, last_hash=?, updated_at=?
                       WHERE id=1 AND last_hash=?""",
                    (seq, record_hash, now, prev_hash),
                )
                changed = conn.execute("SELECT changes()").fetchone()[0]
                if changed != 1:
                    raise AppError(ErrorCode.E13, "审计链并发冲突或已被篡改，拒绝写入")
                # 滚动上限：超过 AUDIT_MAX_ROWS 时按时间删除最旧行会断链，因此只在文档允许时
                # 删除最早记录之前先停止；P0 不自动删链首（以免破坏从 genesis 的全量校验）。
                # 行数告警留给查询侧。P2 归档后再清理。
                _ = AUDIT_MAX_ROWS
                return seq
        except AppError:
            self.mark_unwritable()
            raise
        except sqlite3.Error as exc:
            self.mark_unwritable()
            raise AppError(ErrorCode.E13, "审计不可写，已拒绝执行写命令", hint=str(exc)) from exc

    def verify_on_start(self, *, sample: int = AUDIT_INTEGRITY_START_SAMPLE) -> ChainStatus:
        state = self.store.execute(
            "SELECT last_seq, last_hash, broken_at_seq FROM audit_chain_state WHERE id=1"
        ).fetchone()
        expected_seq = int(state["last_seq"]) if state else 0
        expected_hash = state["last_hash"] if state else AUDIT_CHAIN_GENESIS
        rows = self._fetch_tail(sample)
        status = verify_chain(rows, expected_last_seq=expected_seq if rows else expected_seq, expected_last_hash=expected_hash)
        # 空库：expected 0 / genesis
        if not rows:
            status = verify_chain([], expected_last_seq=expected_seq, expected_last_hash=expected_hash)
        if not status.ok:
            self._broken_at = status.broken_at_seq
            now = utc_now()
            try:
                self.store.execute(
                    """UPDATE audit_chain_state SET broken_at_seq=?, broken_detected_at=?, updated_at=?
                       WHERE id=1""",
                    (status.broken_at_seq, now, now),
                )
            except Exception:
                pass
        elif state and state["broken_at_seq"] is not None:
            self._broken_at = int(state["broken_at_seq"])
        return status

    def verify_full(self, *, max_rows: int = AUDIT_INTEGRITY_FULL_CHECK_MAX_ROWS) -> ChainStatus:
        rows = self._fetch_all(max_rows)
        state = self.store.execute(
            "SELECT last_seq, last_hash FROM audit_chain_state WHERE id=1"
        ).fetchone()
        status = verify_chain(
            rows,
            expected_last_seq=int(state["last_seq"]) if state else 0,
            expected_last_hash=state["last_hash"] if state else AUDIT_CHAIN_GENESIS,
        )
        if not status.ok:
            self._broken_at = status.broken_at_seq
        return status

    def _fetch_tail(self, n: int) -> list[dict[str, Any]]:
        rows = self.store.execute(
            f"SELECT * FROM (SELECT * FROM audit_log ORDER BY seq DESC LIMIT ?) ORDER BY seq ASC",
            (n,),
        ).fetchall()
        return [dict(r) for r in rows]

    def _fetch_all(self, max_rows: int) -> list[dict[str, Any]]:
        rows = self.store.execute(
            "SELECT * FROM audit_log ORDER BY seq ASC LIMIT ?",
            (max_rows,),
        ).fetchall()
        return [dict(r) for r in rows]

    def query(
        self,
        *,
        event: Optional[str] = None,
        actor: Optional[str] = None,
        connection_id: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = ["1=1"]
        params: list[Any] = []
        if event:
            clauses.append("event=?")
            params.append(event)
        if actor:
            clauses.append("actor_username=?")
            params.append(actor)
        if connection_id:
            clauses.append("connection_id=?")
            params.append(connection_id)
        params.append(limit)
        rows = self.store.execute(
            f"SELECT * FROM audit_log WHERE {' AND '.join(clauses)} ORDER BY seq DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [dict(r) for r in rows]
