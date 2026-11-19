"""界面展示文案与枚举中文标签。代码标识保持英文。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

ENV_LABELS = {"dev": "开发", "test": "测试", "prod": "生产"}
PROTECTION_LABELS = {"readonly": "只读", "confirm": "需确认", "unrestricted": "不限制"}
TOPOLOGY_LABELS = {"direct": "直连", "sentinel": "哨兵", "cluster": "集群"}
TRANSPORT_LABELS = {"tcp": "TCP", "ssh": "SSH"}
ROLE_LABELS = {"admin": "管理员", "operator": "操作员", "viewer": "只读员"}
KEY_TYPE_LABELS = {
    "string": "字符串",
    "hash": "哈希",
    "list": "列表",
    "set": "集合",
    "zset": "有序集合",
    "stream": "流",
    "ReJSON-RL": "JSON",
    "json": "JSON",
}

EVENT_LABELS = {
    "auth_login": "登录",
    "auth_bootstrap": "初始化管理员",
    "auth_local_bootstrap": "创建本机会话",
    "auth_local_session": "打开本机会话",
    "auth_lockout": "账号锁定",
    "auth_recover": "恢复账号",
    "user_change": "账号变更",
    "profile_change": "连接变更",
    "grant_access": "授权变更",
    "high_risk_plaintext": "明文例外",
    "connect": "建立连接",
    "connect_plaintext": "明文连接",
    "acl_warning": "ACL 警告",
}

DECISION_LABELS = {
    "allow": "允许",
    "deny": "拒绝",
    "confirm": "待确认",
    "typed_confirm": "需键入确认",
}

RESULT_LABELS = {
    "ok": "成功",
    "err": "失败",
    "blocked": "已阻断",
    "pending": "处理中",
}


def label(mapping: dict[str, str], key: Optional[str], fallback: str = "—") -> str:
    if key is None or key == "":
        return fallback
    return mapping.get(str(key), str(key))


def env_label(value: Optional[str]) -> str:
    return label(ENV_LABELS, value)


def protection_label(value: Optional[str]) -> str:
    return label(PROTECTION_LABELS, value)


def topology_label(value: Optional[str]) -> str:
    return label(TOPOLOGY_LABELS, value)


def role_label(value: Optional[str]) -> str:
    return label(ROLE_LABELS, value)


def key_type_label(value: Optional[str]) -> str:
    return label(KEY_TYPE_LABELS, value)


def event_label(value: Optional[str]) -> str:
    return label(EVENT_LABELS, value)


def decision_label(value: Optional[str]) -> str:
    return label(DECISION_LABELS, value)


def result_label(value: Optional[str]) -> str:
    return label(RESULT_LABELS, value)


def format_ts(value: Optional[str], *, fallback: str = "—") -> str:
    """将 ISO 时间转为本地时区显示（系统本地时区，本机一般为北京时间）。"""
    if not value:
        return fallback
    text = str(value).strip()
    if not text:
        return fallback
    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone()
        return local.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text


def format_ttl(ttl_s: Optional[int]) -> str:
    if ttl_s is None:
        return "—"
    if ttl_s < 0:
        return "永久"
    if ttl_s < 60:
        return f"{ttl_s} 秒"
    if ttl_s < 3600:
        return f"{ttl_s // 60} 分 {ttl_s % 60} 秒"
    hours, rem = divmod(ttl_s, 3600)
    minutes = rem // 60
    return f"{hours} 小时 {minutes} 分"
