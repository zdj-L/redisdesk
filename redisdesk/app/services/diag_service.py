"""诊断快照：INFO 之外只读拉取慢查询与客户端列表，并施加上限。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from redisdesk.adapters.gateway import RedisGateway
from redisdesk.core.authz import Action, GrantResolver, check_or_raise
from redisdesk.core.errors import AppError
from redisdesk.core.limits import (
    CLIENT_LIST_DISPLAY_MAX,
    SOCKET_TIMEOUT_S,
    SLOWLOG_COUNT,
    SLOWLOG_COUNT_MAX,
    SLOWLOG_COUNT_MIN,
    SLOWLOG_DISPLAY_ROWS,
    SLOWLOG_DISPLAY_ROWS_MAX,
    SLOWLOG_DISPLAY_ROWS_MIN,
)
from redisdesk.core.model import ConnectionProfile, SessionContext
from redisdesk.core.redact import format_argv_masked
from redisdesk.app.ui.diagnostics_metrics import DASH, format_save_time


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


SLOWLOG_FETCH_COUNT = _clamp(SLOWLOG_COUNT, SLOWLOG_COUNT_MIN, SLOWLOG_COUNT_MAX)
SLOWLOG_SHOW_ROWS = _clamp(SLOWLOG_DISPLAY_ROWS, SLOWLOG_DISPLAY_ROWS_MIN, SLOWLOG_DISPLAY_ROWS_MAX)
CLIENT_SHOW_MAX = _clamp(CLIENT_LIST_DISPLAY_MAX, 500, 10_000)
COMMAND_DISPLAY_CHARS = 160


@dataclass
class SlowlogRow:
    log_id: str
    time_text: str
    duration_text: str
    command: str


@dataclass
class ClientRow:
    client_id: str
    addr: str
    name: str
    db: str
    idle: str
    flags: str
    command: str


@dataclass
class DiagnosticLists:
    slowlog_rows: list[SlowlogRow] = field(default_factory=list)
    slowlog_len: int | None = None
    slowlog_fetched: int = 0
    slowlog_truncated: bool = False
    slowlog_note: str = ""
    slowlog_error: str | None = None
    client_rows: list[ClientRow] = field(default_factory=list)
    client_total: int = 0
    client_truncated: bool = False
    client_note: str = ""
    client_error: str | None = None


@dataclass
class DiagnosticsSnapshot:
    info: dict[str, str]
    lists: DiagnosticLists


def _to_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="surrogateescape")
    return str(value)


def _to_int(value: object) -> int | None:
    text = _to_str(value).strip()
    if text == "":
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def format_duration_us(raw: object) -> str:
    micros = _to_int(raw)
    if micros is None or micros < 0:
        return DASH
    if micros < 1000:
        return f"{micros} µs"
    ms = micros / 1000
    if ms >= 100 or ms.is_integer():
        return f"{int(round(ms))} ms"
    if ms >= 10:
        return f"{ms:.1f} ms"
    return f"{ms:.2f} ms"


def _extract_slowlog_entry(item: object) -> tuple[str, object, object, list[object]] | None:
    if isinstance(item, dict):
        argv = item.get("command") or item.get("argv") or item.get("args") or []
        if isinstance(argv, (str, bytes)):
            argv = [argv]
        return (
            _to_str(item.get("id", item.get("uid", ""))),
            item.get("start_time", item.get("timestamp", item.get("time"))),
            item.get("duration", item.get("duration_us", item.get("micros"))),
            list(argv) if isinstance(argv, (list, tuple)) else [],
        )
    if isinstance(item, (list, tuple)) and len(item) >= 4:
        argv = item[3]
        if not isinstance(argv, (list, tuple)):
            argv = [argv]
        return _to_str(item[0]), item[1], item[2], list(argv)
    if hasattr(item, "id") and hasattr(item, "command"):
        argv = getattr(item, "command")
        if not isinstance(argv, (list, tuple)):
            argv = [argv]
        return (
            _to_str(getattr(item, "id")),
            getattr(item, "start_time", getattr(item, "timestamp", None)),
            getattr(item, "duration", None),
            list(argv),
        )
    return None


def parse_slowlog_entries(raw: object, *, display_rows: int = SLOWLOG_SHOW_ROWS) -> tuple[list[SlowlogRow], int]:
    if raw is None:
        return [], 0
    if not isinstance(raw, (list, tuple)):
        return [], 0
    fetched = 0
    rows: list[SlowlogRow] = []
    limit = max(0, display_rows)
    for item in raw:
        parsed = _extract_slowlog_entry(item)
        if parsed is None:
            continue
        fetched += 1
        if len(rows) >= limit:
            continue
        log_id, ts, duration, argv = parsed
        command = format_argv_masked([_to_str(part) for part in argv], limit=COMMAND_DISPLAY_CHARS)
        rows.append(
            SlowlogRow(
                log_id=log_id or DASH,
                time_text=format_save_time(ts),
                duration_text=format_duration_us(duration),
                command=command or DASH,
            )
        )
    return rows, fetched


def parse_client_list(raw: object, *, display_max: int = CLIENT_SHOW_MAX) -> tuple[list[ClientRow], int]:
    if raw is None:
        return [], 0
    if isinstance(raw, (list, tuple)):
        text = "\n".join(_to_str(item) for item in raw)
    else:
        text = _to_str(raw)
    rows: list[ClientRow] = []
    total = 0
    limit = max(0, display_max)
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        total += 1
        if len(rows) >= limit:
            continue
        fields: dict[str, str] = {}
        for part in line.split(" "):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            fields[key] = value
        idle = fields.get("idle", "")
        idle_text = f"{idle} 秒" if idle != "" and idle.isdigit() else (idle or DASH)
        rows.append(
            ClientRow(
                client_id=fields.get("id") or DASH,
                addr=fields.get("addr") or DASH,
                name=fields.get("name") or DASH,
                db=fields.get("db") or DASH,
                idle=idle_text,
                flags=fields.get("flags") or DASH,
                command=fields.get("cmd") or DASH,
            )
        )
    return rows, total


def _config_map(raw: object) -> dict[str, str]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {_to_str(k): _to_str(v) for k, v in raw.items()}
    if isinstance(raw, (list, tuple)):
        out: dict[str, str] = {}
        seq = list(raw)
        for i in range(0, len(seq) - 1, 2):
            out[_to_str(seq[i])] = _to_str(seq[i + 1])
        return out
    return {}


def _error_text(exc: BaseException) -> str:
    return exc.message if isinstance(exc, AppError) else (str(exc) or type(exc).__name__)


def _slowlog_threshold_note(values: dict[str, str]) -> str:
    slower = values.get("slowlog-log-slower-than", values.get("slowlog_log_slower_than", "")).strip()
    if slower == "-1":
        return "慢查询未开启（阈值为 -1）。"
    if slower == "0":
        return "当前记录所有命令，开销较大。"
    return ""


def _exec(gw: RedisGateway, argv: list[str]) -> Any:
    return gw.execute(argv, timeout_s=SOCKET_TIMEOUT_S)


def collect_lists(gw: RedisGateway) -> DiagnosticLists:
    """Best-effort list pulls; a failed section does not abort INFO."""
    result = DiagnosticLists()

    try:
        length = _exec(gw, ["SLOWLOG", "LEN"])
        result.slowlog_len = _to_int(length)
    except Exception as exc:  # noqa: BLE001
        result.slowlog_error = _error_text(exc)

    if result.slowlog_error is None:
        try:
            raw = _exec(gw, ["SLOWLOG", "GET", str(SLOWLOG_FETCH_COUNT)])
            rows, fetched = parse_slowlog_entries(raw, display_rows=SLOWLOG_SHOW_ROWS)
            result.slowlog_rows = rows
            result.slowlog_fetched = fetched
            total = result.slowlog_len if result.slowlog_len is not None else fetched
            result.slowlog_truncated = fetched >= SLOWLOG_FETCH_COUNT or (result.slowlog_len is not None and result.slowlog_len > len(rows))
            shown = len(rows)
            if total:
                result.slowlog_note = f"已显示 {shown} 条，共 {total} 条（拉取上限 {SLOWLOG_FETCH_COUNT}，展示上限 {SLOWLOG_SHOW_ROWS}）。"
            elif shown == 0:
                result.slowlog_note = "暂无慢查询记录。"
            else:
                result.slowlog_note = f"已显示 {shown} 条（展示上限 {SLOWLOG_SHOW_ROWS}）。"
        except Exception as exc:  # noqa: BLE001
            result.slowlog_error = _error_text(exc)
            result.slowlog_note = f"无法拉取慢查询：{result.slowlog_error}"

    try:
        cfg = _config_map(_exec(gw, ["CONFIG", "GET", "slowlog-log-slower-than"]))
        hint = _slowlog_threshold_note(cfg)
        if hint:
            result.slowlog_note = (hint + " " + result.slowlog_note).strip() if result.slowlog_note else hint
    except Exception:
        pass

    try:
        raw = _exec(gw, ["CLIENT", "LIST"])
        rows, total = parse_client_list(raw, display_max=CLIENT_SHOW_MAX)
        result.client_rows = rows
        result.client_total = total
        result.client_truncated = total > len(rows)
        if total == 0:
            result.client_note = "暂无客户端。"
        elif result.client_truncated:
            result.client_note = f"客户端过多，已截断。已显示 {len(rows)} / 共 {total} 个（上限 {CLIENT_SHOW_MAX}）。"
        else:
            result.client_note = f"共 {total} 个客户端。"
    except Exception as exc:  # noqa: BLE001
        result.client_error = _error_text(exc)
        result.client_note = f"无法拉取客户端列表：{result.client_error}"

    return result


def collect_snapshot(gw: RedisGateway) -> DiagnosticsSnapshot:
    info = gw.info()
    data = {str(k): v if isinstance(v, str) else str(v) for k, v in (info or {}).items()}
    return DiagnosticsSnapshot(info=data, lists=collect_lists(gw))


class DiagService:
    def __init__(self, grants: GrantResolver) -> None:
        self.grants = grants

    def overview(self, session: SessionContext, profile: ConnectionProfile, gateway: RedisGateway) -> dict[str, str]:
        check_or_raise(session, Action.CONNECT, profile, grants=self.grants)
        info = gateway.info()
        keys = (
            "redis_version",
            "os",
            "uptime_in_seconds",
            "connected_clients",
            "used_memory_human",
            "used_memory_peak_human",
            "role",
            "db0",
        )
        return {k: info[k] for k in keys if k in info}

    def snapshot(self, session: SessionContext, profile: ConnectionProfile, gateway: RedisGateway) -> DiagnosticsSnapshot:
        check_or_raise(session, Action.EXEC_L1, profile, grants=self.grants)
        return collect_snapshot(gateway)
