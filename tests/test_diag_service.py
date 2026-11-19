"""Slowlog / CLIENT LIST parsers and capped collect_lists, no real Redis."""
from __future__ import annotations

import pytest

from redisdesk.app.services.diag_service import (
    CLIENT_SHOW_MAX,
    SLOWLOG_FETCH_COUNT,
    SLOWLOG_SHOW_ROWS,
    collect_lists,
    collect_snapshot,
    format_duration_us,
    parse_client_list,
    parse_slowlog_entries,
)
from redisdesk.app.ui.diagnostics_metrics import DASH, format_save_time
from redisdesk.core.errors import AppError, ErrorCode


class FakeGateway:
    def __init__(self, *, info=None, results=None, errors=None) -> None:
        self.info_data = info or {"redis_version": "7.2.0", "used_memory_human": "1.00M"}
        self.results = results or {}
        self.errors = errors or {}
        self.calls: list[list[str]] = []

    def info(self):
        if "INFO" in self.errors:
            raise self.errors["INFO"]
        return dict(self.info_data)

    def execute(self, argv, timeout_s=None):
        key = tuple(str(part) for part in argv)
        self.calls.append(list(key))
        if key in self.errors:
            raise self.errors[key]
        cmd = key[0].upper() if key else ""
        # also allow lookup by command prefix
        if key in self.results:
            return self.results[key]
        for stored, value in self.results.items():
            if tuple(part.upper() if i == 0 else part for i, part in enumerate(stored)) == tuple(part.upper() if i == 0 else part for i, part in enumerate(key)):
                return value
        raise AppError(ErrorCode.E08, "unexpected command {0}".format(" ".join(key)))


def _slowlog_entry(log_id: int, ts: int = 1700000000, us: int = 1500, argv=None):
    return [log_id, ts, us, argv or ["GET", "foo"]]


def test_parse_slowlog_supports_redis3_and_redis4_and_dict():
    rows, fetched = parse_slowlog_entries(
        [
            [1, 1700000000, 350, ["GET", "foo"]],
            [2, 1700000000, 2500, ["SET", "k", "v"], "127.0.0.1:1234", "worker"],
            {"id": 3, "start_time": 1700000000, "duration": 1_200_000, "command": ["HGETALL", "user:1"]},
        ]
    )
    assert fetched == 3
    assert [row.log_id for row in rows] == ["1", "2", "3"]
    assert rows[0].duration_text == "350 \u00b5s"
    assert rows[1].duration_text == "2.50 ms"
    assert rows[2].duration_text == "1200 ms"
    assert rows[0].time_text == format_save_time("1700000000")
    assert "GET" in rows[0].command and "foo" in rows[0].command
    assert "HGETALL" in rows[2].command


def test_parse_slowlog_caps_display_and_empty():
    raw = [_slowlog_entry(i) for i in range(1, 201)]
    rows, fetched = parse_slowlog_entries(raw, display_rows=SLOWLOG_SHOW_ROWS)
    assert fetched == 200
    assert len(rows) == SLOWLOG_SHOW_ROWS == 50
    assert rows[0].log_id == "1"
    assert rows[-1].log_id == "50"
    assert parse_slowlog_entries(None) == ([], 0)
    assert parse_slowlog_entries([]) == ([], 0)
    assert parse_slowlog_entries("bad") == ([], 0)


def test_parse_client_list_caps_and_parses_fields():
    lines = [
        "id=1 addr=127.0.0.1:1001 name=app db=0 idle=12 flags=N sub=0 qbuf=0 omem=0 cmd=get",
        "id=2 addr=127.0.0.1:1002 name= db=2 idle=3 flags=P cmd=info",
    ]
    lines += [f"id={i} addr=10.0.0.{i % 250}:6379 name=c{i} db=0 idle=1 flags=N cmd=ping" for i in range(3, 3003)]
    rows, total = parse_client_list("\n".join(lines), display_max=CLIENT_SHOW_MAX)
    assert total == 3002
    assert len(rows) == CLIENT_SHOW_MAX == 2000
    assert rows[0].client_id == "1"
    assert rows[0].addr == "127.0.0.1:1001"
    assert rows[0].name == "app"
    assert rows[0].idle == "12 \u79d2"
    assert rows[0].command == "get"
    assert rows[1].name == DASH
    assert rows[1].db == "2"
    assert parse_client_list("") == ([], 0)
    assert parse_client_list(None) == ([], 0)


def test_format_duration_us_edges():
    assert format_duration_us(-1) == DASH
    assert format_duration_us("x") == DASH
    assert format_duration_us(0) == "0 \u00b5s"
    assert format_duration_us(999) == "999 \u00b5s"
    assert format_duration_us(1000) == "1 ms"


def test_collect_lists_caps_commands_and_skips_writes():
    entries = [_slowlog_entry(i) for i in range(128)]
    clients = "\n".join(f"id={i} addr=127.0.0.1:{1000+i} name=n{i} db=0 idle=1 flags=N cmd=get" for i in range(3000))
    gw = FakeGateway(
        results={
            ("SLOWLOG", "LEN"): 400,
            ("SLOWLOG", "GET", str(SLOWLOG_FETCH_COUNT)): entries,
            ("CONFIG", "GET", "slowlog-log-slower-than"): ["slowlog-log-slower-than", "10000"],
            ("CLIENT", "LIST"): clients,
        }
    )
    result = collect_lists(gw)
    issued = [" ".join(call) for call in gw.calls]
    assert "SLOWLOG GET {0}".format(SLOWLOG_FETCH_COUNT) in issued
    assert "SLOWLOG LEN" in issued
    assert "CLIENT LIST" in issued
    assert all("RESET" not in call and "KILL" not in call for call in issued)
    assert result.slowlog_error is None
    assert result.client_error is None
    assert len(result.slowlog_rows) == SLOWLOG_SHOW_ROWS
    assert result.slowlog_fetched == 128
    assert result.slowlog_len == 400
    assert result.slowlog_truncated is True
    assert "50" in result.slowlog_note and "400" in result.slowlog_note
    assert len(result.client_rows) == CLIENT_SHOW_MAX
    assert result.client_total == 3000
    assert result.client_truncated is True
    assert "\u622a\u65ad" in result.client_note


def test_collect_lists_isolates_errors_and_empty_states():
    gw = FakeGateway(
        results={
            ("SLOWLOG", "LEN"): 0,
            ("SLOWLOG", "GET", str(SLOWLOG_FETCH_COUNT)): [],
            ("CONFIG", "GET", "slowlog-log-slower-than"): ["slowlog-log-slower-than", "-1"],
            ("CLIENT", "LIST"): "",
        }
    )
    result = collect_lists(gw)
    assert result.slowlog_rows == []
    assert "\u672a\u5f00\u542f" in result.slowlog_note
    assert result.client_rows == []
    assert "\u6682\u65e0\u5ba2\u6237\u7aef" in result.client_note

    gw2 = FakeGateway(
        results={("CLIENT", "LIST"): "id=9 addr=127.0.0.1:1 name=x db=0 idle=0 flags=N cmd=ping"},
        errors={
            ("SLOWLOG", "LEN"): AppError(ErrorCode.E08, "NOPERM SLOWLOG"),
        },
    )
    isolated = collect_lists(gw2)
    assert isolated.slowlog_error is not None and "NOPERM" in isolated.slowlog_error
    assert isolated.slowlog_rows == []
    assert isolated.client_error is None
    assert isolated.client_rows[0].client_id == "9"

    gw3 = FakeGateway(
        results={
            ("SLOWLOG", "LEN"): 1,
            ("SLOWLOG", "GET", str(SLOWLOG_FETCH_COUNT)): [_slowlog_entry(7, us=10, argv=["PING"])],
            ("CONFIG", "GET", "slowlog-log-slower-than"): ["slowlog-log-slower-than", "0"],
        },
        errors={("CLIENT", "LIST"): AppError(ErrorCode.E08, "unknown command CLIENT")},
    )
    isolated2 = collect_lists(gw3)
    assert isolated2.slowlog_rows[0].log_id == "7"
    assert "\u8bb0\u5f55\u6240\u6709\u547d\u4ee4" in isolated2.slowlog_note
    assert isolated2.client_error is not None
    assert isolated2.client_rows == []


def test_collect_snapshot_keeps_info_when_lists_fail():
    gw = FakeGateway(
        info={"redis_version": "7.2.0"},
        errors={
            ("SLOWLOG", "LEN"): AppError(ErrorCode.E08, "denied"),
            ("SLOWLOG", "GET", str(SLOWLOG_FETCH_COUNT)): AppError(ErrorCode.E08, "denied"),
            ("CONFIG", "GET", "slowlog-log-slower-than"): AppError(ErrorCode.E08, "denied"),
            ("CLIENT", "LIST"): AppError(ErrorCode.E08, "denied"),
        },
    )
    snap = collect_snapshot(gw)
    assert snap.info["redis_version"] == "7.2.0"
    assert snap.lists.slowlog_error is not None
    assert snap.lists.client_error is not None
