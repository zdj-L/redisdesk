"""附件 A 默认参数的唯一代码化表示。

其它模块只引用本文件中的常量，不得另写默认值。
标注 TUNABLE 的参数才允许出现在设置页；其余为硬编码上限。
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# A.1 浏览与值读取
# ---------------------------------------------------------------------------
SCAN_COUNT = 1000
SCAN_COUNT_MIN = 50
SCAN_COUNT_MAX = 10000

KEY_PAGE_SIZE = 500
KEY_PAGE_SIZE_MIN = 100
KEY_PAGE_SIZE_MAX = 2000

VALUE_PAGE_SIZE = 100
VALUE_PAGE_SIZE_MIN = 10
VALUE_PAGE_SIZE_MAX = 500

VALUE_SCAN_COUNT = 200
VALUE_SCAN_COUNT_MIN = 50
VALUE_SCAN_COUNT_MAX = 1000

META_PIPELINE_BATCH = 100
META_PIPELINE_BATCH_MIN = 10
META_PIPELINE_BATCH_MAX = 200

SCAN_BUFFER_MAX_ROWS = 3000
SCAN_BUFFER_MAX_ROWS_MIN = 500
SCAN_BUFFER_MAX_ROWS_MAX = 10000

UI_EMIT_BATCH_ROWS = 200
UI_EMIT_BATCH_ROWS_MIN = 50
UI_EMIT_BATCH_ROWS_MAX = 500

SCAN_TIME_BUDGET_S = 30
SCAN_TIME_BUDGET_S_MIN = 10
SCAN_TIME_BUDGET_S_MAX = 120

SCAN_BUDGET_CURSOR_STEPS = 2_000_000
SCAN_BUDGET_CURSOR_STEPS_MIN = 500_000
SCAN_BUDGET_CURSOR_STEPS_MAX = 5_000_000

SCAN_COUNT_BATCH_DELETE = 500
SCAN_COUNT_BATCH_DELETE_MIN = 100
SCAN_COUNT_BATCH_DELETE_MAX = 1000

STRING_INLINE_LIMIT_KB = 256
STRING_INLINE_LIMIT_KB_MIN = 16
STRING_INLINE_LIMIT_KB_MAX = 1024

STRING_CHUNK_KB = 64
STRING_CHUNK_KB_MIN = 16
STRING_CHUNK_KB_MAX = 256

JSON_PRETTY_LIMIT_KB = 1024
JSON_PRETTY_LIMIT_KB_MIN = 128
JSON_PRETTY_LIMIT_KB_MAX = 4096

BINARY_NONPRINT_RATIO = 0.30
BINARY_NONPRINT_RATIO_MIN = 0.10
BINARY_NONPRINT_RATIO_MAX = 0.80

BIG_STRING_BYTES = 10_485_760  # 10 MB
BIG_COLLECTION_ITEMS = 10_000
BIG_ELEMENT_BYTES = 1_048_576  # 1 MB

VALUE_PREVIEW_CHARS = 200
VALUE_PREVIEW_CHARS_MIN = 60
VALUE_PREVIEW_CHARS_MAX = 1000

SCAN_DEDUPE_WINDOW = 5000  # 最近 N 个 key 去重窗口（04 §3.2）
VALUE_PAGE_CACHE_PAGES = 20
BINARY_SNIFF_BYTES = 512
LRANGE_MAX_SPAN = 500  # 单次 LRANGE/ZRANGE 区间上限

# ---------------------------------------------------------------------------
# A.2 连接与网络
# ---------------------------------------------------------------------------
CONNECT_TIMEOUT_S = 5
CONNECT_TIMEOUT_S_MIN = 1
CONNECT_TIMEOUT_S_MAX = 30

SOCKET_TIMEOUT_S = 5
SOCKET_TIMEOUT_S_MIN = 1
SOCKET_TIMEOUT_S_MAX = 60

CONSOLE_TIMEOUT_S = 10
CONSOLE_TIMEOUT_S_MIN = 1
CONSOLE_TIMEOUT_S_MAX = 120

CONNECT_RETRY_TIMES = 3
CONNECT_RETRY_TIMES_MIN = 0
CONNECT_RETRY_TIMES_MAX = 5

CONNECT_RETRY_BACKOFF_MS = 1000

POOL_MAX_CONNECTIONS = 4
POOL_MAX_CONNECTIONS_MIN = 2
POOL_MAX_CONNECTIONS_MAX = 16

SCAN_POOL_MAX_CONNECTIONS = 2
SCAN_POOL_MAX_CONNECTIONS_MIN = 1
SCAN_POOL_MAX_CONNECTIONS_MAX = 8

HEALTH_CHECK_INTERVAL_S = 30
HEALTH_CHECK_INTERVAL_S_MIN = 0
HEALTH_CHECK_INTERVAL_S_MAX = 300

SENTINEL_PORT = 26379
SENTINEL_DISCOVER_RETRY = 3
SENTINEL_DISCOVER_BACKOFF_MS = 500
SENTINEL_ROLE_CHECK_ON_ACQUIRE = True

CLUSTER_SLOT_CACHE_TTL_S = 300
CLUSTER_SLOT_CACHE_TTL_S_MIN = 30
CLUSTER_SLOT_CACHE_TTL_S_MAX = 3600

CLUSTER_SCAN_CONCURRENCY = 2
CLUSTER_SCAN_CONCURRENCY_MIN = 1
CLUSTER_SCAN_CONCURRENCY_MAX = 4

CLUSTER_MAX_NODES = 32

SSH_PORT = 22
SSH_LOCAL_PORT = 0
SSH_KEEPALIVE_S = 30

TLS_REQUIRED = True
TLS_VERSION_MIN = "TLSv1.2"
TLS_VERIFY_HOSTNAME = True
TLS_CERT_EXPIRY_WARN_DAYS = 30
TLS_CERT_EXPIRY_WARN_DAYS_MIN = 7
TLS_CERT_EXPIRY_WARN_DAYS_MAX = 90
PLAINTEXT_REASON_MIN_CHARS = 10
PLAINTEXT_REASON_MAX_CHARS = 200
TLS_PIN_SHA256 = ""

DEFAULT_REDIS_PORT = 6379

# ---------------------------------------------------------------------------
# A.3 控制台与危险操作
# ---------------------------------------------------------------------------
CONSOLE_HISTORY_MAX = 1000
CONSOLE_HISTORY_MAX_MIN = 100
CONSOLE_HISTORY_MAX_MAX = 5000

CONSOLE_RESULT_MAX_ROWS = 10_000
CONSOLE_RESULT_MAX_ROWS_MIN = 1000
CONSOLE_RESULT_MAX_ROWS_MAX = 50_000

CONSOLE_RESULT_MAX_BYTES = 5_242_880  # 5 MB

BATCH_DELETE_MAX_KEYS = 500
BATCH_DELETE_MAX_KEYS_MIN = 50
BATCH_DELETE_MAX_KEYS_MAX = 500

BATCH_DELETE_TOTAL_MAX = 10_000

DELETE_PIPELINE_BATCH = 100
DELETE_PIPELINE_BATCH_MIN = 10
DELETE_PIPELINE_BATCH_MAX = 200

DELETE_PAUSE_MS = 20
DELETE_PAUSE_MS_MIN = 0
DELETE_PAUSE_MS_MAX = 200

MONITOR_MAX_DURATION_S = 10

TYPED_CONFIRM_REQUIRED = True
L4_UNLOCK_REQUIRES_REASON = True

DEL_L3_KEY_THRESHOLD = 50  # DEL/UNLINK 超过该 key 数升级为 L3

# ---------------------------------------------------------------------------
# A.4 诊断与审计
# ---------------------------------------------------------------------------
SLOWLOG_COUNT = 128
SLOWLOG_COUNT_MIN = 32
SLOWLOG_COUNT_MAX = 512

SLOWLOG_DISPLAY_ROWS = 50
SLOWLOG_DISPLAY_ROWS_MIN = 10
SLOWLOG_DISPLAY_ROWS_MAX = 200

CLIENT_LIST_DISPLAY_MAX = 2000
CLIENT_LIST_DISPLAY_MAX_MIN = 500
CLIENT_LIST_DISPLAY_MAX_MAX = 10_000

INFO_AUTO_REFRESH_S = 0

MEMORY_USAGE_SAMPLES_SINGLE = 0
MEMORY_USAGE_SAMPLES_BATCH = 5
MEMORY_USAGE_BATCH_KEYS = 200

BIGKEY_SCAN_SAMPLE_MAX = 50_000
BIGKEY_SCAN_SCAN_COUNT = 1000

AUDIT_RETENTION_DAYS = 180
AUDIT_RETENTION_DAYS_MIN = 30
AUDIT_RETENTION_DAYS_MAX = 365

AUDIT_MAX_ROWS = 200_000

# ---------------------------------------------------------------------------
# A.5 本地与日志
# ---------------------------------------------------------------------------
LOG_ROTATE_MB = 10
LOG_BACKUP_COUNT = 5
UI_SETTINGS_DEBOUNCE_MS = 500
CONFIRM_DIALOG_TIMEOUT_S = 0
HIDE_UNAUTHORIZED_CONNECTIONS = False
UI_WATERMARK_ENABLED = False
APP_INTEGRITY_CHECK = True

# ---------------------------------------------------------------------------
# A.6 身份与会话
# ---------------------------------------------------------------------------
PASSWORD_MIN_LENGTH = 12
PASSWORD_MIN_LENGTH_MIN = 12
PASSWORD_MIN_LENGTH_MAX = 32

PASSWORD_COMPLEXITY_CLASSES = 3
PASSWORD_COMPLEXITY_CLASSES_MIN = 3
PASSWORD_COMPLEXITY_CLASSES_MAX = 4

PASSWORD_HISTORY_COUNT = 5
PASSWORD_HISTORY_COUNT_MIN = 3
PASSWORD_HISTORY_COUNT_MAX = 10

PASSWORD_MAX_AGE_DAYS = 90

SCRYPT_N = 32_768
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_SALT_BYTES = 16
SCRYPT_DKLEN = 32

LOGIN_FAIL_LOCK_THRESHOLD = 5
LOGIN_FAIL_LOCK_MINUTES = 15
LOGIN_FAIL_DAILY_THRESHOLD = 10
LOGIN_FAIL_DAILY_LOCK_MINUTES = 60
LOGIN_DELAY_AFTER_FAIL = 4  # 第 N 次失败起每次延迟 1 s

SESSION_IDLE_LOCK_MINUTES = 10
SESSION_IDLE_LOCK_MINUTES_MIN = 5
SESSION_IDLE_LOCK_MINUTES_MAX = 30

SESSION_ABSOLUTE_HOURS = 8
SESSION_ABSOLUTE_HOURS_MIN = 4
SESSION_ABSOLUTE_HOURS_MAX = 12

RECOVERY_CODE_LENGTH = 24

# ---------------------------------------------------------------------------
# A.7 凭据与导出
# ---------------------------------------------------------------------------
SECRET_DEFAULT_MODE = "dpapi"
MASTER_PASSWORD_MIN_LENGTH = 12
MASTER_PASSWORD_FAIL_THRESHOLD = 5
MASTER_PASSWORD_LOCK_MINUTES = 15
DPAPI_ENTROPY = "redisdesk:v2:credential"

EXPORT_KDF = "scrypt"
EXPORT_SCRYPT_N = 32_768
EXPORT_CIPHER = "AES-256-GCM"
EXPORT_MIN_PASSWORD = 12
EXPORT_FAIL_UNLOCK_MINUTES = 15
EXPORT_NONCE_BYTES = 12
EXPORT_TAG_BYTES = 16

# ---------------------------------------------------------------------------
# A.8 数据保护与审计链
# ---------------------------------------------------------------------------
SENSITIVE_RULES_BUILTIN = True
SENSITIVE_REVEAL_TTL_S = 60
SENSITIVE_REVEAL_TTL_S_MIN = 30
SENSITIVE_REVEAL_TTL_S_MAX = 300
SENSITIVE_REVEAL_MAX_PER_MINUTE = 10
SENSITIVE_MATCH_TIMEOUT_MS = 5
EXPORT_DEFAULT_MASKED = True

AUDIT_HASH_ALGO = "SHA-256"
AUDIT_CHAIN_GENESIS = "0" * 64
AUDIT_FAIL_CLOSED = True
AUDIT_INTEGRITY_CHECK_ON_START = True
AUDIT_INTEGRITY_START_SAMPLE = 500
AUDIT_INTEGRITY_FULL_CHECK_MAX_ROWS = 200_000
AUDIT_ARCHIVE_ENABLED = False
AUDIT_EXPORT_FORMAT = "csv"
ARGV_MASKED_MAX_CHARS = 200
ARGV_DIGEST_HEX_CHARS = 16

UPDATE_MANIFEST_SIG = "RSA-3072-PSS-SHA256"
UPDATE_ALLOW_DOWNGRADE = False

# ---------------------------------------------------------------------------
# 其它固定约定
# ---------------------------------------------------------------------------
APP_NAME = "RedisDesk"
SINGLE_INSTANCE_MUTEX = r"Global\RedisDesk"
APPDATA_DIRNAME = "RedisDesk"
DB_FILENAME = "redisdesk.db"
LOG_FILENAME = "redisdesk.log"
UI_SETTINGS_FILENAME = "ui-settings.json"
DEFAULT_ENV = "dev"
DEFAULT_PROTECTION = "confirm"
SCHEMA_VERSION = 1


# 可出现在设置页的参数名（文档标注「可调」）。
TUNABLE_PARAMETERS: frozenset[str] = frozenset(
    {
        "scan_count",
        "key_page_size",
        "value_page_size",
        "scan_time_budget_s",
        "string_inline_limit_kb",
        "json_pretty_limit_kb",
        "value_preview_chars",
        "connect_timeout_s",
        "socket_timeout_s",
        "console_timeout_s",
        "sentinel_port",
        "sentinel_role_check_on_acquire",
        "cluster_slot_cache_ttl_s",
        "cluster_scan_concurrency",
        "ssh_port",
        "ssh_local_port",
        "tls_cert_expiry_warn_days",
        "tls_pin_sha256",
        "console_history_max",
        "typed_confirm_required",
        "slowlog_count",
        "slowlog_display_rows",
        "info_auto_refresh_s",
        "memory_usage_samples_single",
        "bigkey_scan_sample_max",
        "audit_retention_days",
        "hide_unauthorized_connections",
        "ui_watermark_enabled",
        "password_min_length",
        "password_max_age_days",
        "session_idle_lock_minutes",
        "secret_default_mode",
        "sensitive_reveal_ttl_s",
        "audit_archive_enabled",
        "audit_export_format",
    }
)


# 供测试与文档对照：参数名 → 默认值（与 02 附件 A 逐行一致）。
DEFAULTS: dict[str, Any] = {
    "scan_count": SCAN_COUNT,
    "key_page_size": KEY_PAGE_SIZE,
    "value_page_size": VALUE_PAGE_SIZE,
    "value_scan_count": VALUE_SCAN_COUNT,
    "meta_pipeline_batch": META_PIPELINE_BATCH,
    "scan_buffer_max_rows": SCAN_BUFFER_MAX_ROWS,
    "ui_emit_batch_rows": UI_EMIT_BATCH_ROWS,
    "scan_time_budget_s": SCAN_TIME_BUDGET_S,
    "scan_budget_cursor_steps": SCAN_BUDGET_CURSOR_STEPS,
    "scan_count_batch_delete": SCAN_COUNT_BATCH_DELETE,
    "string_inline_limit_kb": STRING_INLINE_LIMIT_KB,
    "string_chunk_kb": STRING_CHUNK_KB,
    "json_pretty_limit_kb": JSON_PRETTY_LIMIT_KB,
    "binary_nonprint_ratio": BINARY_NONPRINT_RATIO,
    "big_string_bytes": BIG_STRING_BYTES,
    "big_collection_items": BIG_COLLECTION_ITEMS,
    "big_element_bytes": BIG_ELEMENT_BYTES,
    "value_preview_chars": VALUE_PREVIEW_CHARS,
    "connect_timeout_s": CONNECT_TIMEOUT_S,
    "socket_timeout_s": SOCKET_TIMEOUT_S,
    "console_timeout_s": CONSOLE_TIMEOUT_S,
    "connect_retry_times": CONNECT_RETRY_TIMES,
    "connect_retry_backoff_ms": CONNECT_RETRY_BACKOFF_MS,
    "pool_max_connections": POOL_MAX_CONNECTIONS,
    "scan_pool_max_connections": SCAN_POOL_MAX_CONNECTIONS,
    "health_check_interval_s": HEALTH_CHECK_INTERVAL_S,
    "sentinel_port": SENTINEL_PORT,
    "sentinel_discover_retry": SENTINEL_DISCOVER_RETRY,
    "sentinel_discover_backoff_ms": SENTINEL_DISCOVER_BACKOFF_MS,
    "sentinel_role_check_on_acquire": SENTINEL_ROLE_CHECK_ON_ACQUIRE,
    "cluster_slot_cache_ttl_s": CLUSTER_SLOT_CACHE_TTL_S,
    "cluster_scan_concurrency": CLUSTER_SCAN_CONCURRENCY,
    "cluster_max_nodes": CLUSTER_MAX_NODES,
    "ssh_port": SSH_PORT,
    "ssh_local_port": SSH_LOCAL_PORT,
    "ssh_keepalive_s": SSH_KEEPALIVE_S,
    "tls_required": TLS_REQUIRED,
    "tls_version_min": TLS_VERSION_MIN,
    "tls_verify_hostname": TLS_VERIFY_HOSTNAME,
    "tls_cert_expiry_warn_days": TLS_CERT_EXPIRY_WARN_DAYS,
    "plaintext_reason_min_chars": PLAINTEXT_REASON_MIN_CHARS,
    "tls_pin_sha256": TLS_PIN_SHA256,
    "console_history_max": CONSOLE_HISTORY_MAX,
    "console_result_max_rows": CONSOLE_RESULT_MAX_ROWS,
    "console_result_max_bytes": CONSOLE_RESULT_MAX_BYTES,
    "batch_delete_max_keys": BATCH_DELETE_MAX_KEYS,
    "batch_delete_total_max": BATCH_DELETE_TOTAL_MAX,
    "delete_pipeline_batch": DELETE_PIPELINE_BATCH,
    "delete_pause_ms": DELETE_PAUSE_MS,
    "monitor_max_duration_s": MONITOR_MAX_DURATION_S,
    "typed_confirm_required": TYPED_CONFIRM_REQUIRED,
    "l4_unlock_requires_reason": L4_UNLOCK_REQUIRES_REASON,
    "slowlog_count": SLOWLOG_COUNT,
    "slowlog_display_rows": SLOWLOG_DISPLAY_ROWS,
    "client_list_display_max": CLIENT_LIST_DISPLAY_MAX,
    "info_auto_refresh_s": INFO_AUTO_REFRESH_S,
    "memory_usage_samples_single": MEMORY_USAGE_SAMPLES_SINGLE,
    "memory_usage_samples_batch": MEMORY_USAGE_SAMPLES_BATCH,
    "memory_usage_batch_keys": MEMORY_USAGE_BATCH_KEYS,
    "bigkey_scan_sample_max": BIGKEY_SCAN_SAMPLE_MAX,
    "bigkey_scan_scan_count": BIGKEY_SCAN_SCAN_COUNT,
    "audit_retention_days": AUDIT_RETENTION_DAYS,
    "audit_max_rows": AUDIT_MAX_ROWS,
    "log_rotate_mb": LOG_ROTATE_MB,
    "log_backup_count": LOG_BACKUP_COUNT,
    "ui_settings_debounce_ms": UI_SETTINGS_DEBOUNCE_MS,
    "confirm_dialog_timeout_s": CONFIRM_DIALOG_TIMEOUT_S,
    "hide_unauthorized_connections": HIDE_UNAUTHORIZED_CONNECTIONS,
    "ui_watermark_enabled": UI_WATERMARK_ENABLED,
    "app_integrity_check": APP_INTEGRITY_CHECK,
    "password_min_length": PASSWORD_MIN_LENGTH,
    "password_complexity_classes": PASSWORD_COMPLEXITY_CLASSES,
    "password_history_count": PASSWORD_HISTORY_COUNT,
    "password_max_age_days": PASSWORD_MAX_AGE_DAYS,
    "scrypt_n": SCRYPT_N,
    "scrypt_r": SCRYPT_R,
    "scrypt_p": SCRYPT_P,
    "scrypt_salt_bytes": SCRYPT_SALT_BYTES,
    "scrypt_dklen": SCRYPT_DKLEN,
    "login_fail_lock_threshold": LOGIN_FAIL_LOCK_THRESHOLD,
    "login_fail_lock_minutes": LOGIN_FAIL_LOCK_MINUTES,
    "login_fail_daily_threshold": LOGIN_FAIL_DAILY_THRESHOLD,
    "login_fail_daily_lock_minutes": LOGIN_FAIL_DAILY_LOCK_MINUTES,
    "login_delay_after_fail": LOGIN_DELAY_AFTER_FAIL,
    "session_idle_lock_minutes": SESSION_IDLE_LOCK_MINUTES,
    "session_absolute_hours": SESSION_ABSOLUTE_HOURS,
    "recovery_code_length": RECOVERY_CODE_LENGTH,
    "secret_default_mode": SECRET_DEFAULT_MODE,
    "master_password_min_length": MASTER_PASSWORD_MIN_LENGTH,
    "master_password_fail_threshold": MASTER_PASSWORD_FAIL_THRESHOLD,
    "master_password_lock_minutes": MASTER_PASSWORD_LOCK_MINUTES,
    "dpapi_entropy": DPAPI_ENTROPY,
    "export_kdf": EXPORT_KDF,
    "export_scrypt_n": EXPORT_SCRYPT_N,
    "export_cipher": EXPORT_CIPHER,
    "export_min_password": EXPORT_MIN_PASSWORD,
    "export_fail_unlock_minutes": EXPORT_FAIL_UNLOCK_MINUTES,
    "sensitive_rules_builtin": SENSITIVE_RULES_BUILTIN,
    "sensitive_reveal_ttl_s": SENSITIVE_REVEAL_TTL_S,
    "sensitive_reveal_max_per_minute": SENSITIVE_REVEAL_MAX_PER_MINUTE,
    "sensitive_match_timeout_ms": SENSITIVE_MATCH_TIMEOUT_MS,
    "export_default_masked": EXPORT_DEFAULT_MASKED,
    "audit_hash_algo": AUDIT_HASH_ALGO,
    "audit_chain_genesis": AUDIT_CHAIN_GENESIS,
    "audit_fail_closed": AUDIT_FAIL_CLOSED,
    "audit_integrity_check_on_start": AUDIT_INTEGRITY_CHECK_ON_START,
    "audit_integrity_full_check_max_rows": AUDIT_INTEGRITY_FULL_CHECK_MAX_ROWS,
    "audit_archive_enabled": AUDIT_ARCHIVE_ENABLED,
    "audit_export_format": AUDIT_EXPORT_FORMAT,
    "update_manifest_sig": UPDATE_MANIFEST_SIG,
    "update_allow_downgrade": UPDATE_ALLOW_DOWNGRADE,
}
