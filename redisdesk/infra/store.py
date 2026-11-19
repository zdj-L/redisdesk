"""SQLite 本地存储：账号、连接、授权、凭据、设置、历史、敏感规则。不引入 ORM。"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.limits import (
    AUDIT_CHAIN_GENESIS,
    DPAPI_ENTROPY,
    SCHEMA_VERSION,
    SECRET_DEFAULT_MODE,
)
from redisdesk.core.model import (
    ClusterConfig,
    ConnectionGrant,
    ConnectionProfile,
    Endpoint,
    LocalUser,
    SecretRef,
    SensitiveRule,
    SentinelConfig,
    SshConfig,
    TlsConfig,
)
from redisdesk.core.redact import builtin_sensitive_rules
from redisdesk.infra.paths import db_path

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_user (
    id                   TEXT PRIMARY KEY,
    username             TEXT NOT NULL UNIQUE,
    display_name         TEXT NOT NULL,
    role                 TEXT NOT NULL CHECK (role IN ('viewer','operator','admin')),
    password_hash        TEXT NOT NULL,
    failed_count         INTEGER NOT NULL DEFAULT 0,
    locked_until         TEXT,
    daily_fail_count     INTEGER NOT NULL DEFAULT 0,
    daily_fail_window    TEXT,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    disabled             INTEGER NOT NULL DEFAULT 0,
    password_updated_at  TEXT NOT NULL,
    last_login_at        TEXT,
    recovery_code_hash   TEXT,
    created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS password_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS connection (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    topology      TEXT NOT NULL CHECK (topology IN ('direct','sentinel','cluster')),
    transport     TEXT NOT NULL CHECK (transport IN ('tcp','ssh')),
    host          TEXT,
    port          INTEGER NOT NULL DEFAULT 6379,
    db            INTEGER NOT NULL DEFAULT 0,
    username      TEXT,
    secret_id     TEXT,
    secret_mode   TEXT NOT NULL DEFAULT 'dpapi'
                  CHECK (secret_mode IN ('dpapi','dpapi_master','not_stored')),
    tls_json      TEXT NOT NULL,
    allow_plaintext INTEGER NOT NULL DEFAULT 0,
    plaintext_reason TEXT,
    approved_by   TEXT,
    approved_at   TEXT,
    ssh_json      TEXT,
    sentinel_json TEXT,
    cluster_json  TEXT,
    env           TEXT NOT NULL DEFAULT 'dev' CHECK (env IN ('prod','test','dev')),
    protection    TEXT NOT NULL DEFAULT 'confirm'
                  CHECK (protection IN ('readonly','confirm','unrestricted')),
    allow_l4      INTEGER NOT NULL DEFAULT 0,
    tags          TEXT,
    note          TEXT,
    created_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    CHECK (env <> 'prod' OR (protection = 'readonly' AND allow_l4 = 0)),
    CHECK (allow_plaintext = 0 OR (plaintext_reason IS NOT NULL AND length(plaintext_reason) >= 10))
);

CREATE TABLE IF NOT EXISTS connection_grant (
    connection_id TEXT NOT NULL REFERENCES connection(id) ON DELETE CASCADE,
    role          TEXT NOT NULL CHECK (role IN ('viewer','operator','admin')),
    granted_by    TEXT NOT NULL,
    granted_at    TEXT NOT NULL,
    PRIMARY KEY (connection_id, role)
);

CREATE TABLE IF NOT EXISTS secret (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    storage    TEXT NOT NULL,
    cipher     BLOB,
    entropy    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    seq            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT    NOT NULL,
    actor_user_id  TEXT    NOT NULL,
    actor_username TEXT    NOT NULL,
    actor_role     TEXT    NOT NULL,
    client_host    TEXT    NOT NULL,
    client_ip      TEXT,
    session_id     TEXT    NOT NULL,
    connection_id  TEXT,
    connection_name TEXT,
    env            TEXT,
    host           TEXT,
    port           INTEGER,
    event          TEXT    NOT NULL,
    command_name   TEXT,
    argv_masked    TEXT,
    argv_digest    TEXT,
    risk_level     TEXT,
    decision       TEXT    NOT NULL,
    deny_reason    TEXT,
    result         TEXT,
    duration_ms    INTEGER,
    impact_estimate TEXT,
    error_code     TEXT,
    hash_algo      TEXT    NOT NULL DEFAULT 'SHA-256',
    prev_hash      TEXT    NOT NULL,
    record_hash    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(actor_username, ts);
CREATE INDEX IF NOT EXISTS idx_audit_conn ON audit_log(connection_id, ts);
CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_log(event, ts);

CREATE TABLE IF NOT EXISTS audit_chain_state (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    last_seq      INTEGER NOT NULL,
    last_hash     TEXT    NOT NULL,
    broken_at_seq INTEGER,
    broken_detected_at TEXT,
    updated_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS console_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id TEXT NOT NULL,
    actor         TEXT NOT NULL,
    raw           TEXT NOT NULL,
    argv_digest   TEXT,
    risk_level    TEXT,
    decision      TEXT,
    result        TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_conn ON console_history(connection_id, created_at);

CREATE TABLE IF NOT EXISTS sensitive_rule (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    target     TEXT NOT NULL CHECK (target IN ('key_name','field_name','value')),
    match_type TEXT NOT NULL CHECK (match_type IN ('prefix','regex','contains')),
    pattern    TEXT NOT NULL,
    action     TEXT NOT NULL CHECK (action IN ('mask_full','mask_partial','hash')),
    enabled    INTEGER NOT NULL DEFAULT 1,
    builtin    INTEGER NOT NULL DEFAULT 0,
    created_by TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str = "") -> str:
    return (prefix + uuid.uuid4().hex) if prefix else uuid.uuid4().hex


class Store:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            try:
                return self._conn.execute(sql, params)
            except sqlite3.Error as exc:
                raise AppError(ErrorCode.E11, "本地存储失败", hint=str(exc)) from exc

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            row = self._conn.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO schema_meta(key, value) VALUES('version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            state = self._conn.execute("SELECT last_seq FROM audit_chain_state WHERE id=1").fetchone()
            if state is None:
                now = utc_now()
                self._conn.execute(
                    """INSERT INTO audit_chain_state(id, last_seq, last_hash, broken_at_seq, broken_detected_at, updated_at)
                       VALUES (1, 0, ?, NULL, NULL, ?)""",
                    (AUDIT_CHAIN_GENESIS, now),
                )
            existing = self._conn.execute("SELECT COUNT(*) AS c FROM sensitive_rule").fetchone()["c"]
            if existing == 0:
                now = utc_now()
                for rule in builtin_sensitive_rules():
                    self._conn.execute(
                        """INSERT INTO sensitive_rule(id, name, target, match_type, pattern, action, enabled, builtin, created_by, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (
                            rule.id,
                            rule.name,
                            rule.target,
                            rule.match_type,
                            rule.pattern,
                            rule.action,
                            1 if rule.enabled else 0,
                            1 if rule.builtin else 0,
                            "system",
                            now,
                        ),
                    )

    # ------------------------------------------------------------------ users
    def count_admins(self) -> int:
        row = self.execute(
            "SELECT COUNT(*) AS c FROM app_user WHERE role='admin' AND disabled=0"
        ).fetchone()
        return int(row["c"])

    def get_user_by_username(self, username: str) -> Optional[sqlite3.Row]:
        return self.execute("SELECT * FROM app_user WHERE username=?", (username,)).fetchone()

    def get_user(self, user_id: str) -> Optional[sqlite3.Row]:
        return self.execute("SELECT * FROM app_user WHERE id=?", (user_id,)).fetchone()

    def list_users(self) -> list[sqlite3.Row]:
        return list(self.execute("SELECT * FROM app_user ORDER BY username").fetchall())

    def insert_user(
        self,
        *,
        username: str,
        display_name: str,
        role: str,
        password_hash: str,
        recovery_code_hash: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        uid = user_id or new_id("u")
        now = utc_now()
        try:
            with self.tx() as conn:
                conn.execute(
                    """INSERT INTO app_user(id, username, display_name, role, password_hash,
                       failed_count, locked_until, daily_fail_count, daily_fail_window,
                       must_change_password, disabled, password_updated_at, last_login_at,
                       recovery_code_hash, created_at)
                       VALUES (?,?,?,?,?,0,NULL,0,NULL,0,0,?,NULL,?,?)""",
                    (uid, username, display_name, role, password_hash, now, recovery_code_hash, now),
                )
                conn.execute(
                    "INSERT INTO password_history(user_id, password_hash, created_at) VALUES (?,?,?)",
                    (uid, password_hash, now),
                )
        except sqlite3.IntegrityError as exc:
            raise AppError(ErrorCode.E11, "用户名已存在或数据无效") from exc
        return uid

    def update_user(self, user_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "display_name",
            "role",
            "password_hash",
            "failed_count",
            "locked_until",
            "daily_fail_count",
            "daily_fail_window",
            "must_change_password",
            "disabled",
            "password_updated_at",
            "last_login_at",
            "recovery_code_hash",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise AppError(ErrorCode.E11, f"不支持的字段: {', '.join(unknown)}")
        assignments = ", ".join(f"{k}=?" for k in fields)
        values = tuple(fields.values()) + (user_id,)
        self.execute(f"UPDATE app_user SET {assignments} WHERE id=?", values)

    def password_history_hashes(self, user_id: str, limit: int) -> list[str]:
        rows = self.execute(
            "SELECT password_hash FROM password_history WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [r["password_hash"] for r in rows]

    def add_password_history(self, user_id: str, password_hash: str) -> None:
        self.execute(
            "INSERT INTO password_history(user_id, password_hash, created_at) VALUES (?,?,?)",
            (user_id, password_hash, utc_now()),
        )

    # ------------------------------------------------------------------ connections
    def list_connections(self) -> list[ConnectionProfile]:
        rows = self.execute("SELECT * FROM connection ORDER BY name").fetchall()
        return [row_to_profile(r) for r in rows]

    def get_connection(self, connection_id: str) -> Optional[ConnectionProfile]:
        row = self.execute("SELECT * FROM connection WHERE id=?", (connection_id,)).fetchone()
        return row_to_profile(row) if row else None

    def save_connection(self, profile: ConnectionProfile, *, created_by: str, secret_mode: str = SECRET_DEFAULT_MODE) -> None:
        now = utc_now()
        tls = profile.tls
        allow_plaintext = 1 if tls.allow_plaintext else 0
        tags = json.dumps(list(profile.tags), ensure_ascii=False)
        tls_json = json.dumps(_tls_to_dict(tls), ensure_ascii=False)
        ssh_json = json.dumps(_ssh_to_dict(profile.ssh), ensure_ascii=False) if profile.ssh else None
        sentinel_json = json.dumps(_sentinel_to_dict(profile.sentinel), ensure_ascii=False) if profile.sentinel else None
        cluster_json = json.dumps(_cluster_to_dict(profile.cluster), ensure_ascii=False) if profile.cluster else None
        try:
            with self.tx() as conn:
                exists = conn.execute("SELECT 1 FROM connection WHERE id=?", (profile.id,)).fetchone()
                values = (
                    profile.name,
                    profile.topology,
                    profile.transport,
                    profile.host,
                    profile.port,
                    profile.db,
                    profile.username,
                    profile.secret_ref,
                    secret_mode,
                    tls_json,
                    allow_plaintext,
                    tls.plaintext_reason,
                    tls.approved_by,
                    tls.approved_at,
                    ssh_json,
                    sentinel_json,
                    cluster_json,
                    profile.env,
                    profile.protection,
                    1 if profile.allow_l4 else 0,
                    tags,
                    profile.note,
                    now,
                    profile.id,
                )
                if exists:
                    conn.execute(
                        """UPDATE connection SET name=?, topology=?, transport=?, host=?, port=?, db=?,
                           username=?, secret_id=?, secret_mode=?, tls_json=?, allow_plaintext=?,
                           plaintext_reason=?, approved_by=?, approved_at=?, ssh_json=?, sentinel_json=?,
                           cluster_json=?, env=?, protection=?, allow_l4=?, tags=?, note=?, updated_at=?
                           WHERE id=?""",
                        values,
                    )
                else:
                    conn.execute(
                        """INSERT INTO connection(name, topology, transport, host, port, db, username,
                           secret_id, secret_mode, tls_json, allow_plaintext, plaintext_reason, approved_by,
                           approved_at, ssh_json, sentinel_json, cluster_json, env, protection, allow_l4,
                           tags, note, updated_at, id, created_by, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        values + (created_by, now),
                    )
                    conn.execute(
                        """INSERT OR IGNORE INTO connection_grant(connection_id, role, granted_by, granted_at)
                           VALUES (?,?,?,?)""",
                        (profile.id, "admin", created_by, now),
                    )
        except sqlite3.IntegrityError as exc:
            raise AppError(ErrorCode.E11, "连接保存失败：违反完整性约束（例如生产连接必须只读，或明文例外理由不足）") from exc

    def delete_connection(self, connection_id: str) -> None:
        with self.tx() as conn:
            row = conn.execute("SELECT secret_id FROM connection WHERE id=?", (connection_id,)).fetchone()
            conn.execute("DELETE FROM connection_grant WHERE connection_id=?", (connection_id,))
            conn.execute("DELETE FROM connection WHERE id=?", (connection_id,))
            if row and row["secret_id"]:
                conn.execute("DELETE FROM secret WHERE id=?", (row["secret_id"],))

    def list_grants(self, connection_id: Optional[str] = None) -> list[ConnectionGrant]:
        if connection_id:
            rows = self.execute(
                "SELECT * FROM connection_grant WHERE connection_id=?", (connection_id,)
            ).fetchall()
        else:
            rows = self.execute("SELECT * FROM connection_grant").fetchall()
        return [
            ConnectionGrant(
                connection_id=r["connection_id"],
                role=r["role"],
                granted_by=r["granted_by"],
                granted_at=r["granted_at"],
            )
            for r in rows
        ]

    def grant(self, connection_id: str, role: str, granted_by: str) -> None:
        now = utc_now()
        self.execute(
            """INSERT OR REPLACE INTO connection_grant(connection_id, role, granted_by, granted_at)
               VALUES (?,?,?,?)""",
            (connection_id, role, granted_by, now),
        )

    def revoke_grant(self, connection_id: str, role: str) -> None:
        self.execute(
            "DELETE FROM connection_grant WHERE connection_id=? AND role=?",
            (connection_id, role),
        )

    # ------------------------------------------------------------------ secrets / settings / rules
    def put_secret(self, ref: SecretRef) -> None:
        now = utc_now()
        self.execute(
            """INSERT OR REPLACE INTO secret(id, kind, storage, cipher, entropy, created_at)
               VALUES (?,?,?,?,?,?)""",
            (ref.id, ref.kind, ref.storage, ref.cipher, ref.entropy_version or DPAPI_ENTROPY, now),
        )

    def get_secret(self, secret_id: str) -> Optional[SecretRef]:
        row = self.execute("SELECT * FROM secret WHERE id=?", (secret_id,)).fetchone()
        if not row:
            return None
        return SecretRef(
            id=row["id"],
            kind=row["kind"],
            storage=row["storage"],
            cipher=row["cipher"],
            entropy_version=row["entropy"],
        )

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT OR REPLACE INTO app_settings(key, value) VALUES (?,?)",
            (key, value),
        )

    def list_sensitive_rules(self) -> list[SensitiveRule]:
        rows = self.execute("SELECT * FROM sensitive_rule").fetchall()
        return [
            SensitiveRule(
                id=r["id"],
                name=r["name"],
                target=r["target"],
                match_type=r["match_type"],
                pattern=r["pattern"],
                action=r["action"],
                enabled=bool(r["enabled"]),
                builtin=bool(r["builtin"]),
            )
            for r in rows
        ]

    def add_history(
        self,
        *,
        connection_id: str,
        actor: str,
        raw: str,
        argv_digest: Optional[str],
        risk_level: Optional[str],
        decision: Optional[str],
        result: Optional[str],
        keep: int,
    ) -> None:
        now = utc_now()
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO console_history(connection_id, actor, raw, argv_digest, risk_level, decision, result, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (connection_id, actor, raw, argv_digest, risk_level, decision, result, now),
            )
            conn.execute(
                """DELETE FROM console_history WHERE connection_id=? AND id NOT IN (
                     SELECT id FROM console_history WHERE connection_id=? ORDER BY id DESC LIMIT ?
                   )""",
                (connection_id, connection_id, keep),
            )

    def list_history(self, connection_id: str, actor: str, limit: int = 200) -> list[sqlite3.Row]:
        return list(
            self.execute(
                """SELECT * FROM console_history WHERE connection_id=? AND actor=?
                   ORDER BY id DESC LIMIT ?""",
                (connection_id, actor, limit),
            ).fetchall()
        )


class StoreGrantResolver:
    def __init__(self, store: Store) -> None:
        self._store = store

    def has_grant(self, connection_id: str, role: str) -> bool:
        row = self._store.execute(
            "SELECT 1 FROM connection_grant WHERE connection_id=? AND role=?",
            (connection_id, role),
        ).fetchone()
        return row is not None


def row_to_user(row: sqlite3.Row) -> LocalUser:
    return LocalUser(
        id=row["id"],
        username=row["username"],
        display_name=row["display_name"],
        role=row["role"],
        password_hash=row["password_hash"],
        failed_count=int(row["failed_count"]),
        locked_until=row["locked_until"],
        must_change_password=bool(row["must_change_password"]),
        disabled=bool(row["disabled"]),
        password_updated_at=row["password_updated_at"],
        last_login_at=row["last_login_at"],
        created_at=row["created_at"],
    )


def row_to_profile(row: sqlite3.Row) -> ConnectionProfile:
    tls_data = json.loads(row["tls_json"]) if row["tls_json"] else {}
    ssh = _ssh_from_dict(json.loads(row["ssh_json"])) if row["ssh_json"] else None
    sentinel = _sentinel_from_dict(json.loads(row["sentinel_json"])) if row["sentinel_json"] else None
    cluster = _cluster_from_dict(json.loads(row["cluster_json"])) if row["cluster_json"] else None
    tags = tuple(json.loads(row["tags"])) if row["tags"] else ()
    host = row["host"] or ""
    port = int(row["port"] or 6379)
    return ConnectionProfile(
        id=row["id"],
        name=row["name"],
        topology=row["topology"],
        transport=row["transport"],
        endpoint=Endpoint(host, port) if host else None,
        db=int(row["db"] or 0),
        username=row["username"],
        secret_ref=row["secret_id"],
        tls=_tls_from_dict(tls_data, row),
        ssh=ssh,
        sentinel=sentinel,
        cluster=cluster,
        env=row["env"],
        protection=row["protection"],
        allow_l4=bool(row["allow_l4"]),
        tags=tags,
        note=row["note"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _tls_to_dict(tls: TlsConfig) -> dict[str, Any]:
    return {
        "enabled": tls.enabled,
        "version_min": tls.version_min,
        "ca_path": tls.ca_path,
        "client_cert_path": tls.client_cert_path,
        "client_key_path": tls.client_key_path,
        "verify_hostname": tls.verify_hostname,
        "allow_plaintext": tls.allow_plaintext,
        "plaintext_reason": tls.plaintext_reason,
        "approved_by": tls.approved_by,
        "approved_at": tls.approved_at,
    }


def _tls_from_dict(data: dict[str, Any], row: sqlite3.Row) -> TlsConfig:
    return TlsConfig(
        enabled=bool(data.get("enabled", True)) and not bool(row["allow_plaintext"]),
        version_min=data.get("version_min") or "TLSv1.2",
        ca_path=data.get("ca_path"),
        client_cert_path=data.get("client_cert_path"),
        client_key_path=data.get("client_key_path"),
        verify_hostname=bool(data.get("verify_hostname", True)),
        allow_plaintext=bool(row["allow_plaintext"]),
        plaintext_reason=row["plaintext_reason"] or data.get("plaintext_reason"),
        approved_by=row["approved_by"] or data.get("approved_by"),
        approved_at=row["approved_at"] or data.get("approved_at"),
    )


def _ssh_to_dict(ssh: SshConfig) -> dict[str, Any]:
    return {
        "host": ssh.host,
        "port": ssh.port,
        "username": ssh.username,
        "auth_mode": ssh.auth_mode,
        "private_key_path": ssh.private_key_path,
        "passphrase_ref": ssh.passphrase_ref,
        "known_hosts_policy": ssh.known_hosts_policy,
        "local_port": ssh.local_port,
        "password_auth_flagged": ssh.password_auth_flagged,
        "host_key_sha256": ssh.host_key_sha256,
        "password_ref": ssh.password_ref,
    }


def _ssh_from_dict(data: dict[str, Any]) -> SshConfig:
    return SshConfig(
        host=data.get("host", ""),
        port=int(data.get("port") or 22),
        username=data.get("username") or "",
        auth_mode=data.get("auth_mode") or "key",
        private_key_path=data.get("private_key_path"),
        passphrase_ref=data.get("passphrase_ref"),
        known_hosts_policy=data.get("known_hosts_policy") or "strict",
        local_port=int(data.get("local_port") or 0),
        password_auth_flagged=bool(data.get("password_auth_flagged")),
        host_key_sha256=data.get("host_key_sha256"),
        password_ref=data.get("password_ref"),
    )


def _sentinel_to_dict(cfg: SentinelConfig) -> dict[str, Any]:
    return {
        "master_name": cfg.master_name,
        "sentinels": [{"host": e.host, "port": e.port} for e in cfg.sentinels],
        "sentinel_username": cfg.sentinel_username,
        "sentinel_secret_ref": cfg.sentinel_secret_ref,
        "require_master_role": cfg.require_master_role,
    }


def _sentinel_from_dict(data: dict[str, Any]) -> SentinelConfig:
    sentinels = tuple(Endpoint(s["host"], int(s.get("port") or 26379)) for s in data.get("sentinels") or [])
    return SentinelConfig(
        master_name=data.get("master_name") or "",
        sentinels=sentinels,
        sentinel_username=data.get("sentinel_username"),
        sentinel_secret_ref=data.get("sentinel_secret_ref"),
        require_master_role=bool(data.get("require_master_role", True)),
    )


def _cluster_to_dict(cfg: ClusterConfig) -> dict[str, Any]:
    return {
        "seeds": [{"host": e.host, "port": e.port} for e in cfg.seeds],
        "slot_cache_ttl_s": cfg.slot_cache_ttl_s,
    }


def _cluster_from_dict(data: dict[str, Any]) -> ClusterConfig:
    seeds = tuple(Endpoint(s["host"], int(s.get("port") or 6379)) for s in data.get("seeds") or [])
    return ClusterConfig(seeds=seeds, slot_cache_ttl_s=int(data.get("slot_cache_ttl_s") or 300))
