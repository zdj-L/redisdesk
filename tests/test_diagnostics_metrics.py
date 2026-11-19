"""Presentation-layer INFO formatters used by the diagnostics page."""
from __future__ import annotations

from redisdesk.app.ui.diagnostics_metrics import (
    CARD_CLIENTS,
    CARD_KEYSPACE,
    CARD_MEMORY,
    CARD_PERSIST,
    CARD_REPLICATION,
    CARD_SERVER,
    CARD_STATS,
    DASH,
    build_card_pairs,
    format_aof,
    format_hit_rate,
    format_link_status,
    format_master_lag,
    format_mode,
    format_ops_per_sec,
    format_policy,
    format_role,
    format_save_time,
    format_uptime,
    parse_keyspace,
)


def test_format_uptime_units():
    assert format_uptime(91289) == "1 \u5929 1 \u5c0f\u65f6"
    assert format_uptime("26390") == "7 \u5c0f\u65f6 19 \u5206\u949f"
    assert format_uptime(12345) == "3 \u5c0f\u65f6 25 \u5206\u949f"
    assert format_uptime(90) == "1 \u5206\u949f"
    assert format_uptime(45) == "45 \u79d2"
    assert format_uptime(0) == "0 \u79d2"
    assert format_uptime("") == DASH
    assert format_uptime("abc") == DASH
    assert format_uptime(None) == DASH


def test_format_hit_rate():
    assert format_hit_rate("90", "10") == "90.0%"
    assert format_hit_rate(1, 1) == "50.0%"
    assert format_hit_rate("10", "0") == "100.0%"
    assert format_hit_rate("0", "0") == DASH
    assert format_hit_rate("", "3") == DASH
    assert format_hit_rate(None, None) == DASH


def test_format_mode_policy_role_ops_aof():
    assert format_mode("standalone").startswith("\u5355\u673a")
    assert format_mode("") == DASH
    assert format_mode(None) == DASH
    assert "allkeys-lru" in format_policy("allkeys-lru")
    assert format_policy("allkeys-lru").startswith("\u6240\u6709\u952e LRU")
    assert format_policy("") == DASH
    assert format_role("master") == "\u4e3b\u8282\u70b9"
    assert format_role("slave") == "\u4ece\u8282\u70b9"
    assert format_ops_per_sec("42") == "42 \u6b21/\u79d2"
    assert format_ops_per_sec("") == DASH
    assert format_aof("0") == "\u5173\u95ed"
    assert format_aof("1") == "\u5f00\u542f"
    assert format_aof("") == DASH


def test_format_save_time_shanghai():
    assert format_save_time("1700000000") == "2023-11-15 06:13:20"
    assert format_save_time("0") == DASH
    assert format_save_time("") == DASH
    assert format_save_time("not-a-time") == DASH


def test_replication_master_and_slave():
    assert format_link_status("master", "up") == "\u672c\u8282\u70b9\u4e3a\u4e3b"
    assert format_link_status("master", "") == "\u672c\u8282\u70b9\u4e3a\u4e3b"
    assert format_link_status("slave", "up") == "\u5df2\u8fde\u63a5"
    assert format_link_status("slave", "down") == "\u65ad\u5f00"
    assert format_link_status("slave", "") == DASH
    assert format_link_status("", "") == DASH
    assert format_master_lag("master", "3") == DASH
    assert format_master_lag("slave", "8") == "8 \u79d2\u524d"
    assert format_master_lag("slave", "0") == "\u521a\u521a"
    assert format_master_lag("slave", "") == DASH


def test_parse_keyspace_raw_and_dict_strings():
    rows = parse_keyspace(
        {
            "db0": "keys=14,expires=0,avg_ttl=0",
            "db1": "keys=0,expires=0,avg_ttl=0",
            "db2": "{'keys': 3, 'expires': 1, 'avg_ttl': 100}",
            "redis_version": "7.2.0",
        }
    )
    assert rows == [
        ("DB 0", "14 \u4e2a\u952e\uff0c0 \u4e2a\u5e26\u8fc7\u671f"),
        ("DB 2", "3 \u4e2a\u952e\uff0c1 \u4e2a\u5e26\u8fc7\u671f"),
    ]
    assert parse_keyspace({}) == [(CARD_KEYSPACE, DASH)]
    assert parse_keyspace({"db0": "keys=0,expires=0,avg_ttl=0"}) == [(CARD_KEYSPACE, DASH)]


def _labels(sections, title):
    return [label for label, _value in sections[title]]


def _values(sections, title):
    return [value for _label, value in sections[title]]


def test_build_card_pairs_full_info():
    sections = build_card_pairs(
        {
            "redis_version": "7.2.0",
            "os": "Windows",
            "uptime_in_seconds": "12345",
            "redis_mode": "standalone",
            "used_memory_human": "1.00M",
            "used_memory_peak_human": "2.00M",
            "used_memory_rss_human": "1.20M",
            "mem_fragmentation_ratio": "1.15",
            "maxmemory_human": "0B",
            "maxmemory_policy": "allkeys-lru",
            "instantaneous_ops_per_sec": "42",
            "keyspace_hits": "90",
            "keyspace_misses": "10",
            "expired_keys": "5",
            "evicted_keys": "1",
            "total_commands_processed": "1000",
            "connected_clients": "3",
            "blocked_clients": "0",
            "rejected_connections": "0",
            "total_connections_received": "9",
            "rdb_last_bgsave_status": "ok",
            "rdb_changes_since_last_save": "7",
            "rdb_last_save_time": "1700000000",
            "aof_enabled": "0",
            "role": "master",
            "connected_slaves": "0",
            "db0": "keys=14,expires=0,avg_ttl=0",
            "db2": "keys=3,expires=1,avg_ttl=100",
        }
    )
    assert "\u89d2\u8272" not in _labels(sections, CARD_SERVER)
    assert sections[CARD_SERVER][2][1] == "3 \u5c0f\u65f6 25 \u5206\u949f"
    assert sections[CARD_SERVER][3][1].startswith("\u5355\u673a")
    assert sections[CARD_MEMORY][2] == ("RSS", "1.20M")
    assert "allkeys-lru" in sections[CARD_MEMORY][5][1]
    assert sections[CARD_STATS][0][1] == "42 \u6b21/\u79d2"
    assert sections[CARD_STATS][1][1] == "90.0%"
    assert sections[CARD_CLIENTS][0][1] == "3"
    persist_labels = _labels(sections, CARD_PERSIST)
    assert "DB0" not in persist_labels
    assert "\u89d2\u8272" not in persist_labels
    assert all("keys=" not in value for value in _values(sections, CARD_PERSIST))
    assert sections[CARD_PERSIST][2][1] == "2023-11-15 06:13:20"
    assert sections[CARD_PERSIST][3][1] == "\u5173\u95ed"
    assert sections[CARD_REPLICATION][0][1] == "\u4e3b\u8282\u70b9"
    assert sections[CARD_REPLICATION][1][1] == "\u672c\u8282\u70b9\u4e3a\u4e3b"
    assert sections[CARD_REPLICATION][2][1] == "0"
    assert sections[CARD_REPLICATION][3][1] == DASH
    assert _labels(sections, CARD_KEYSPACE) == ["DB 0", "DB 2"]


def test_build_card_pairs_missing_fields_are_dashes():
    sections = build_card_pairs({"redis_version": "3.2.100", "os": "Linux", "uptime_in_seconds": "60"})
    assert sections[CARD_SERVER][0][1] == "3.2.100"
    assert sections[CARD_SERVER][2][1] == "1 \u5206\u949f"
    assert sections[CARD_SERVER][3][1] == DASH
    assert sections[CARD_MEMORY][2][1] == DASH
    assert sections[CARD_MEMORY][5][1] == DASH
    assert sections[CARD_STATS][0][1] == DASH
    assert sections[CARD_STATS][1][1] == DASH
    assert sections[CARD_PERSIST][2][1] == DASH
    assert sections[CARD_PERSIST][3][1] == DASH
    assert sections[CARD_REPLICATION][0][1] == DASH
    assert sections[CARD_REPLICATION][1][1] == DASH
    assert sections[CARD_KEYSPACE] == [(CARD_KEYSPACE, DASH)]
