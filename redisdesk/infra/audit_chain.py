"""审计哈希链：计算、校验、断链状态。纯函数 + 面向行的校验，不直接写库。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from redisdesk.core.limits import AUDIT_CHAIN_GENESIS, AUDIT_HASH_ALGO, AUDIT_INTEGRITY_START_SAMPLE


HASH_ALGO = AUDIT_HASH_ALGO
GENESIS = AUDIT_CHAIN_GENESIS

# 参与哈希的字段（除 record_hash 外全部，按字典序）。
CANONICAL_FIELDS: tuple[str, ...] = (
    "actor_role",
    "actor_user_id",
    "actor_username",
    "argv_digest",
    "argv_masked",
    "client_host",
    "client_ip",
    "command_name",
    "connection_id",
    "connection_name",
    "decision",
    "deny_reason",
    "duration_ms",
    "env",
    "error_code",
    "event",
    "hash_algo",
    "host",
    "impact_estimate",
    "port",
    "prev_hash",
    "result",
    "risk_level",
    "seq",
    "session_id",
    "ts",
)


def canonical_bytes(record: Mapping[str, Any]) -> bytes:
    """键按字典序、紧凑分隔符、UTF-8；时间保持原样字符串。"""
    payload = {k: _normalize(record.get(k)) for k in CANONICAL_FIELDS}
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return text.encode("utf-8")


def _normalize(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    return str(value)


def prev_hash_bytes(prev_hash: str) -> bytes:
    """prev_hash 为 64 位小写十六进制；解码失败则按 UTF-8 字节回退（不应发生）。"""
    try:
        raw = bytes.fromhex(prev_hash)
        if len(raw) == 32:
            return raw
    except ValueError:
        pass
    return prev_hash.encode("utf-8")


def compute_record_hash(record: Mapping[str, Any], prev_hash: str) -> str:
    digest = hashlib.sha256()
    digest.update(canonical_bytes(record))
    digest.update(prev_hash_bytes(prev_hash))
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class ChainStatus:
    ok: bool
    last_seq: int
    last_hash: str
    broken_at_seq: Optional[int] = None
    message: str = ""
    checked: int = 0


def verify_chain(
    rows: Sequence[Mapping[str, Any]],
    *,
    expected_last_seq: Optional[int] = None,
    expected_last_hash: Optional[str] = None,
    genesis: str = GENESIS,
) -> ChainStatus:
    """按 seq 升序校验。rows 应已按 seq 排序。空链视为完好。"""
    if not rows:
        if expected_last_seq not in (None, 0) or (expected_last_hash not in (None, genesis) and expected_last_hash):
            if expected_last_seq and expected_last_seq > 0:
                return ChainStatus(False, expected_last_seq or 0, expected_last_hash or genesis, expected_last_seq, "链状态与空表不一致")
        return ChainStatus(True, 0, genesis, None, "空链", 0)

    first_seq = int(rows[0]["seq"])
    # 全量校验从创世开始；抽验（首条 seq!=1）以该条 prev_hash 为起点。
    prev = genesis if first_seq == 1 else str(rows[0].get("prev_hash") or genesis)

    last_seq = 0
    last_hash = prev
    for index, row in enumerate(rows):
        seq = int(row["seq"])
        row_prev = str(row.get("prev_hash") or "")
        if row_prev != prev:
            return ChainStatus(False, last_seq, last_hash, seq, f"seq={seq} 的 prev_hash 与上一条不一致", index)
        expected = compute_record_hash({**dict(row), "prev_hash": row_prev}, row_prev)
        actual = str(row.get("record_hash") or "")
        if actual != expected:
            return ChainStatus(False, last_seq, last_hash, seq, f"seq={seq} 的 record_hash 不匹配", index)
        prev = actual
        last_seq = seq
        last_hash = actual

    if expected_last_seq is not None and expected_last_seq != last_seq:
        return ChainStatus(False, last_seq, last_hash, last_seq, "链状态 last_seq 与末条不一致", len(rows))
    if expected_last_hash is not None and expected_last_hash != last_hash:
        return ChainStatus(False, last_seq, last_hash, last_seq, "链状态 last_hash 与末条不一致", len(rows))
    return ChainStatus(True, last_seq, last_hash, None, "完整", len(rows))


def sample_tail(rows: Sequence[Mapping[str, Any]], n: int = AUDIT_INTEGRITY_START_SAMPLE) -> Sequence[Mapping[str, Any]]:
    if n <= 0 or len(rows) <= n:
        return rows
    return rows[-n:]
