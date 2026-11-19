"""诊断页 INFO 展示层：格式化、派生指标与卡片字段组装。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Mapping

DASH = "\u2014"
TZ_SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")

CARD_SERVER = "\u670d\u52a1\u5668"
CARD_MEMORY = "\u5185\u5b58"
CARD_STATS = "\u7edf\u8ba1"
CARD_CLIENTS = "\u5ba2\u6237\u7aef"
CARD_PERSIST = "\u6301\u4e45\u5316"
CARD_REPLICATION = "\u590d\u5236"
CARD_KEYSPACE = "\u952e\u7a7a\u95f4"

# Single-column page order. A two-column row grid left a hole under shorter
# sections; masonry is denser but less even. Suitability beats density.
SECTION_ORDER: tuple[str, ...] = (
    CARD_SERVER,
    CARD_MEMORY,
    CARD_STATS,
    CARD_CLIENTS,
    CARD_PERSIST,
    CARD_REPLICATION,
    CARD_KEYSPACE,
)

_MODE_LABELS = {
    "standalone": "\u5355\u673a\uff08standalone\uff09",
    "sentinel": "\u54e8\u5175\uff08sentinel\uff09",
    "cluster": "\u96c6\u7fa4\uff08cluster\uff09",
}

_ROLE_LABELS = {
    "master": "\u4e3b\u8282\u70b9",
    "slave": "\u4ece\u8282\u70b9",
    "replica": "\u4ece\u8282\u70b9",
    "sentinel": "\u54e8\u5175",
}

_POLICY_LABELS = {
    "noeviction": "\u4e0d\u6dd8\u6c70\uff08noeviction\uff09",
    "allkeys-lru": "\u6240\u6709\u952e LRU\uff08allkeys-lru\uff09",
    "volatile-lru": "\u8fc7\u671f\u952e LRU\uff08volatile-lru\uff09",
    "allkeys-lfu": "\u6240\u6709\u952e LFU\uff08allkeys-lfu\uff09",
    "volatile-lfu": "\u8fc7\u671f\u952e LFU\uff08volatile-lfu\uff09",
    "allkeys-random": "\u6240\u6709\u952e\u968f\u673a\uff08allkeys-random\uff09",
    "volatile-random": "\u8fc7\u671f\u952e\u968f\u673a\uff08volatile-random\uff09",
    "volatile-ttl": "\u6700\u65e9\u8fc7\u671f\u4f18\u5148\uff08volatile-ttl\uff09",
}

_KEYSPACE_RE = re.compile(r"^db(\d+)$", re.IGNORECASE)


def _text(info: Mapping[str, object], key: str) -> str:
    raw = info.get(key)
    if raw is None:
        return ""
    text = str(raw).strip()
    return "" if text in {"", "None", "none"} else text


def _or_dash(value: str) -> str:
    return value if value else DASH


def _as_int(value: str) -> int | None:
    if value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: str) -> float | None:
    if value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_uptime(raw: object) -> str:
    seconds = _as_int("" if raw is None else str(raw))
    if seconds is None or seconds < 0:
        return DASH
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} \u5929")
        parts.append(f"{hours} \u5c0f\u65f6")
        return " ".join(parts)
    if hours:
        parts.append(f"{hours} \u5c0f\u65f6")
        if minutes:
            parts.append(f"{minutes} \u5206\u949f")
        return " ".join(parts)
    if minutes:
        return f"{minutes} \u5206\u949f"
    return f"{secs} \u79d2"


def format_mode(raw: object) -> str:
    text = "" if raw is None else str(raw).strip()
    if not text:
        return DASH
    return _MODE_LABELS.get(text.lower(), text)


def format_role(raw: object) -> str:
    text = "" if raw is None else str(raw).strip()
    if not text:
        return DASH
    return _ROLE_LABELS.get(text.lower(), text)


def format_policy(raw: object) -> str:
    text = "" if raw is None else str(raw).strip()
    if not text:
        return DASH
    return _POLICY_LABELS.get(text.lower(), text)


def format_ops_per_sec(raw: object) -> str:
    text = "" if raw is None else str(raw).strip()
    if text == "":
        return DASH
    number = _as_float(text)
    if number is None:
        return text
    if number.is_integer():
        return f"{int(number)} \u6b21/\u79d2"
    return f"{number:.1f} \u6b21/\u79d2"


def format_hit_rate(hits: object, misses: object) -> str:
    hit_n = _as_float("" if hits is None else str(hits))
    miss_n = _as_float("" if misses is None else str(misses))
    if hit_n is None or miss_n is None:
        return DASH
    total = hit_n + miss_n
    if total <= 0:
        return DASH
    return f"{(hit_n / total) * 100:.1f}%"


def format_aof(raw: object) -> str:
    text = "" if raw is None else str(raw).strip().lower()
    if text in {"1", "yes", "on", "true"}:
        return "\u5f00\u542f"
    if text in {"0", "no", "off", "false"}:
        return "\u5173\u95ed"
    return DASH


def format_save_time(raw: object, *, tz: timezone = TZ_SHANGHAI) -> str:
    ts = _as_int("" if raw is None else str(raw))
    if ts is None or ts <= 0:
        return DASH
    try:
        moment = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(tz)
    except (OSError, OverflowError, ValueError):
        return DASH
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def format_link_status(role: object, status: object) -> str:
    role_text = "" if role is None else str(role).strip().lower()
    if role_text in {"master", ""}:
        return "\u672c\u8282\u70b9\u4e3a\u4e3b" if role_text == "master" else DASH
    text = "" if status is None else str(status).strip().lower()
    if text in {"up", "ok", "connected"}:
        return "\u5df2\u8fde\u63a5"
    if text in {"down", "disconnected"}:
        return "\u65ad\u5f00"
    return DASH if text == "" else text


def format_master_lag(role: object, raw: object) -> str:
    role_text = "" if role is None else str(role).strip().lower()
    if role_text in {"master", ""}:
        return DASH
    seconds = _as_int("" if raw is None else str(raw))
    if seconds is None or seconds < 0:
        return DASH
    if seconds == 0:
        return "\u521a\u521a"
    return f"{seconds} \u79d2\u524d"


def _parse_keyspace_entry(raw: object) -> tuple[int, int] | None:
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        keys = _as_int(str(raw.get("keys", 0) or 0)) or 0
        expires = _as_int(str(raw.get("expires", 0) or 0)) or 0
        return keys, expires
    text = str(raw).strip()
    if not text:
        return None
    parts: dict[str, str] = {}
    cleaned = text.replace("{", "").replace("}", "").replace("'", "").replace('"', "")
    for item in cleaned.split(","):
        if "=" not in item and ":" not in item:
            continue
        sep = "=" if "=" in item else ":"
        name, value = item.split(sep, 1)
        parts[name.strip()] = value.strip()
    if "keys" not in parts and "expires" not in parts:
        return None
    keys = _as_int(parts.get("keys", "0")) or 0
    expires = _as_int(parts.get("expires", "0")) or 0
    return keys, expires


def parse_keyspace(info: Mapping[str, object]) -> list[tuple[str, str]]:
    rows: list[tuple[int, str, str]] = []
    for key, raw in info.items():
        match = _KEYSPACE_RE.match(str(key).strip())
        if not match:
            continue
        parsed = _parse_keyspace_entry(raw)
        if parsed is None:
            continue
        keys, expires = parsed
        if keys <= 0 and expires <= 0:
            continue
        index = int(match.group(1))
        label = f"DB {index}"
        value = f"{keys} \u4e2a\u952e\uff0c{expires} \u4e2a\u5e26\u8fc7\u671f"
        rows.append((index, label, value))
    rows.sort(key=lambda item: item[0])
    if not rows:
        return [(CARD_KEYSPACE, DASH)]
    return [(label, value) for _index, label, value in rows]


def _plain(info: Mapping[str, object], key: str) -> str:
    return _or_dash(_text(info, key))


def build_card_pairs(info: Mapping[str, object] | None) -> dict[str, list[tuple[str, str]]]:
    data: Mapping[str, object] = info or {}
    role = _text(data, "role")
    return {
        CARD_SERVER: [
            ("\u7248\u672c", _plain(data, "redis_version")),
            ("\u64cd\u4f5c\u7cfb\u7edf", _plain(data, "os")),
            ("\u8fd0\u884c\u65f6\u95f4", format_uptime(_text(data, "uptime_in_seconds"))),
            ("\u8fd0\u884c\u6a21\u5f0f", format_mode(_text(data, "redis_mode"))),
        ],
        CARD_MEMORY: [
            ("\u5df2\u7528\u5185\u5b58", _plain(data, "used_memory_human")),
            ("\u5185\u5b58\u5cf0\u503c", _plain(data, "used_memory_peak_human")),
            ("RSS", _plain(data, "used_memory_rss_human")),
            ("\u788e\u7247\u7387", _plain(data, "mem_fragmentation_ratio")),
            ("\u5185\u5b58\u4e0a\u9650", _plain(data, "maxmemory_human")),
            ("\u6dd8\u6c70\u7b56\u7565", format_policy(_text(data, "maxmemory_policy"))),
        ],
        CARD_STATS: [
            ("\u77ac\u65f6\u6307\u4ee4\u6570", format_ops_per_sec(_text(data, "instantaneous_ops_per_sec"))),
            ("\u547d\u4e2d\u7387", format_hit_rate(_text(data, "keyspace_hits"), _text(data, "keyspace_misses"))),
            ("\u8fc7\u671f\u952e", _plain(data, "expired_keys")),
            ("\u9a71\u9010\u952e", _plain(data, "evicted_keys")),
            ("\u7d2f\u8ba1\u547d\u4ee4", _plain(data, "total_commands_processed")),
        ],
        CARD_CLIENTS: [
            ("\u5df2\u8fde\u63a5\u5ba2\u6237\u7aef", _plain(data, "connected_clients")),
            ("\u963b\u585e\u5ba2\u6237\u7aef", _plain(data, "blocked_clients")),
            ("\u62d2\u7edd\u8fde\u63a5", _plain(data, "rejected_connections")),
            ("\u7d2f\u8ba1\u8fde\u63a5", _plain(data, "total_connections_received")),
        ],
        CARD_PERSIST: [
            ("\u6700\u8fd1 RDB", _plain(data, "rdb_last_bgsave_status")),
            ("\u8ddd\u4e0a\u6b21\u4fdd\u5b58\u53d8\u66f4", _plain(data, "rdb_changes_since_last_save")),
            ("\u4e0a\u6b21\u4fdd\u5b58\u65f6\u95f4", format_save_time(_text(data, "rdb_last_save_time"))),
            ("AOF", format_aof(_text(data, "aof_enabled"))),
        ],
        CARD_REPLICATION: [
            ("\u89d2\u8272", format_role(role)),
            ("\u4e3b\u4ece\u8fde\u63a5", format_link_status(role, _text(data, "master_link_status"))),
            ("\u4ece\u8282\u70b9\u6570", _plain(data, "connected_slaves")),
            ("\u4e3b\u5e93\u5ef6\u8fdf", format_master_lag(role, _text(data, "master_last_io_seconds_ago"))),
        ],
        CARD_KEYSPACE: parse_keyspace(data),
    }
