"""领域模型（跨线程只传这些 DTO，不传 redis 客户端对象）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional

Topology = Literal["direct", "sentinel", "cluster"]
Transport = Literal["tcp", "ssh"]
EnvName = Literal["prod", "test", "dev"]
Protection = Literal["readonly", "confirm", "unrestricted"]
RoleName = Literal["viewer", "operator", "admin"]
SecretStorage = Literal["dpapi", "dpapi_master", "not_stored"]
SecretKind = Literal[
    "redis_password",
    "sentinel_password",
    "ssh_password",
    "ssh_key_passphrase",
    "master_wrap",
]
ScanState = Literal["idle", "running", "paused", "finished", "cancelled", "error"]
RiskLevel = Literal["L1", "L2", "L3", "L4"]
DenyReason = Literal[
    "unauthenticated",
    "role",
    "grant",
    "protection",
    "risk",
    "locked",
    "audit_unavailable",
    "audit_broken",
]
SshAuthMode = Literal["password", "key"]
KnownHostsPolicy = Literal["strict", "tofu"]
SensitiveTarget = Literal["key_name", "field_name", "value"]
SensitiveMatchType = Literal["prefix", "regex", "contains"]
SensitiveAction = Literal["mask_full", "mask_partial", "hash"]


@dataclass(frozen=True, slots=True)
class Endpoint:
    host: str
    port: int = 6379


@dataclass(frozen=True, slots=True)
class TlsConfig:
    enabled: bool = True
    version_min: str = "TLSv1.2"
    ca_path: Optional[str] = None
    client_cert_path: Optional[str] = None
    client_key_path: Optional[str] = None
    verify_hostname: bool = True
    allow_plaintext: bool = False
    plaintext_reason: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None


@dataclass(frozen=True, slots=True)
class SshConfig:
    host: str
    port: int = 22
    username: str = ""
    auth_mode: SshAuthMode = "key"
    private_key_path: Optional[str] = None
    passphrase_ref: Optional[str] = None
    known_hosts_policy: KnownHostsPolicy = "strict"
    local_port: int = 0
    password_auth_flagged: bool = False
    host_key_sha256: Optional[str] = None
    password_ref: Optional[str] = None


@dataclass(frozen=True, slots=True)
class SentinelConfig:
    master_name: str
    sentinels: tuple[Endpoint, ...] = ()
    sentinel_username: Optional[str] = None
    sentinel_secret_ref: Optional[str] = None
    require_master_role: bool = True
    tls: Optional[TlsConfig] = None


@dataclass(frozen=True, slots=True)
class ClusterConfig:
    seeds: tuple[Endpoint, ...] = ()
    slot_cache_ttl_s: int = 300


@dataclass(frozen=True, slots=True)
class ConnectionProfile:
    id: str
    name: str
    topology: Topology = "direct"
    transport: Transport = "tcp"
    endpoint: Optional[Endpoint] = None
    db: int = 0
    username: Optional[str] = None
    secret_ref: Optional[str] = None
    tls: TlsConfig = field(default_factory=TlsConfig)
    ssh: Optional[SshConfig] = None
    sentinel: Optional[SentinelConfig] = None
    cluster: Optional[ClusterConfig] = None
    env: EnvName = "dev"
    protection: Protection = "confirm"
    allow_l4: bool = False
    tags: tuple[str, ...] = ()
    note: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def host(self) -> str:
        return self.endpoint.host if self.endpoint else ""

    @property
    def port(self) -> int:
        return self.endpoint.port if self.endpoint else 6379


@dataclass(frozen=True, slots=True)
class LocalUser:
    id: str
    username: str
    display_name: str
    role: RoleName
    password_hash: str
    failed_count: int = 0
    locked_until: Optional[str] = None
    must_change_password: bool = False
    disabled: bool = False
    password_updated_at: Optional[str] = None
    last_login_at: Optional[str] = None
    created_at: Optional[str] = None


@dataclass(frozen=True, slots=True)
class SessionContext:
    user_id: str
    username: str
    role: RoleName
    login_at: datetime
    last_active_at: datetime
    unlocked: bool
    session_id: str
    absolute_expires_at: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class AuthzDecision:
    allowed: bool
    reason: str
    required_role: Optional[RoleName] = None
    action: Optional[str] = None
    resource_id: Optional[str] = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class ConnectionGrant:
    connection_id: str
    role: RoleName
    granted_by: str
    granted_at: str


@dataclass(frozen=True, slots=True)
class SensitiveRule:
    id: str
    name: str
    target: SensitiveTarget
    match_type: SensitiveMatchType
    pattern: str
    action: SensitiveAction = "mask_full"
    enabled: bool = True
    builtin: bool = False


@dataclass(frozen=True, slots=True)
class SecretRef:
    id: str
    kind: SecretKind
    storage: SecretStorage
    cipher: Optional[bytes] = None
    entropy_version: str = "redisdesk:v2:credential"


@dataclass(frozen=True, slots=True)
class ScanQuery:
    pattern: Optional[str] = None
    key_type: Optional[str] = None
    ttl_min: Optional[int] = None
    ttl_max: Optional[int] = None
    size_min: Optional[int] = None
    size_max: Optional[int] = None
    node_id: Optional[str] = None
    count: int = 1000


@dataclass(frozen=True, slots=True)
class ScanSession:
    session_id: str
    query: ScanQuery
    cursors: dict[str, int] = field(default_factory=dict)
    scanned: int = 0
    matched: int = 0
    buffered: int = 0
    state: ScanState = "idle"
    started_at: Optional[str] = None
    elapsed_ms: int = 0
    error: Optional[str] = None
    budget_exhausted: bool = False


@dataclass(frozen=True, slots=True)
class KeyMeta:
    key: str
    key_type: Optional[str] = None
    ttl_s: int = -1
    size_bytes: Optional[int] = None
    encoding: Optional[str] = None
    node_id: Optional[str] = None
    slot: Optional[int] = None


@dataclass(frozen=True, slots=True)
class ValueItem:
    field: Optional[str]
    value_text: str
    value_bytes_len: int
    is_binary: bool = False
    score: Optional[str] = None
    item_id: Optional[str] = None
    masked: bool = False
    mask_reason: Optional[str] = None


@dataclass(frozen=True, slots=True)
class ValuePage:
    key: str
    key_type: str
    page_index: int
    offset_or_cursor: str
    items: tuple[ValueItem, ...]
    total: Optional[int] = None
    has_more: bool = False
    truncated: bool = False
    loaded_bytes: int = 0
    cost_ms: int = 0
    note: str = ""


@dataclass(frozen=True, slots=True)
class CommandRequest:
    raw: str
    argv: tuple[str, ...]
    timeout_s: float
    issued_at: datetime
    actor: str
    session_id: str


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    level: RiskLevel
    category: str
    reason: str
    alternative: str = ""
    typed_confirm_text: str = ""
    requires_reason: bool = False
    irreversible: bool = False
    hard_deny: bool = False
    l4_permanent: bool = False
    impact_estimate: str = ""


@dataclass(frozen=True, slots=True)
class CommandResult:
    ok: bool
    argv_name: str
    rows: tuple[tuple[str, ...], ...] = ()
    duration_ms: int = 0
    hit: Optional[bool] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    truncated: bool = False
    masked_cells: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class AuditEntry:
    seq: Optional[int]
    ts: str
    actor_user_id: str
    actor_username: str
    actor_role: str
    client_host: str
    client_ip: Optional[str]
    session_id: str
    connection_id: Optional[str]
    connection_name: Optional[str]
    env: Optional[str]
    host: Optional[str]
    port: Optional[int]
    event: str
    command_name: Optional[str]
    argv_masked: Optional[str]
    argv_digest: Optional[str]
    risk_level: Optional[str]
    decision: str
    deny_reason: Optional[str]
    result: Optional[str]
    duration_ms: Optional[int]
    impact_estimate: Optional[str]
    error_code: Optional[str]
    hash_algo: str
    prev_hash: str
    record_hash: str = ""


@dataclass(frozen=True, slots=True)
class AppSettings:
    theme: str = "light"
    log_level: str = "INFO"
    scan_count: int = 1000
    key_page_size: int = 500
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NodeInfo:
    node_id: str
    host: str
    port: int
    role: str = "master"
    slots: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeFacts:
    """风险判定时的运行时事实（元素数、是否批量任务等）。"""

    collection_size: Optional[int] = None
    from_batch_delete: bool = False
    monitor_allowed: bool = False
