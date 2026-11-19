"""TLS 上下文构建、证书校验与到期检查。无「忽略证书错误」开关。"""

from __future__ import annotations

import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.limits import TLS_CERT_EXPIRY_WARN_DAYS, TLS_VERSION_MIN
from redisdesk.core.model import TlsConfig

_MIN_VERSIONS = {
    "TLSv1.2": ssl.TLSVersion.TLSv1_2,
    "TLSv1.3": ssl.TLSVersion.TLSv1_3,
}


def require_tls_or_exception(tls: TlsConfig) -> None:
    if tls.enabled:
        return
    if tls.allow_plaintext and tls.plaintext_reason and len(tls.plaintext_reason) >= 10:
        return
    raise AppError(
        ErrorCode.E01,
        "未启用 TLS，且未走明文例外流程",
        hint="请启用 TLS，或由 Admin 填写不少于 10 字的明文例外理由",
    )


def build_ssl_context(tls: TlsConfig) -> Optional[ssl.SSLContext]:
    """按设计构建 SSLContext。明文例外时返回 None。"""
    require_tls_or_exception(tls)
    if not tls.enabled:
        return None
    if not tls.verify_hostname:
        # 文档：verify_hostname 固定 true，即使配置被篡改也不允许关闭
        pass
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    min_ver = _MIN_VERSIONS.get(tls.version_min or TLS_VERSION_MIN, ssl.TLSVersion.TLSv1_2)
    if min_ver < ssl.TLSVersion.TLSv1_2:
        min_ver = ssl.TLSVersion.TLSv1_2
    ctx.minimum_version = min_ver
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    if tls.ca_path:
        ca = Path(tls.ca_path)
        if not ca.is_file():
            raise AppError(ErrorCode.E01, f"CA 文件不存在：{tls.ca_path}")
        ctx.load_verify_locations(cafile=str(ca))
    else:
        ctx.load_default_certs()
    if tls.client_cert_path:
        try:
            ctx.load_cert_chain(tls.client_cert_path, tls.client_key_path)
        except OSError as exc:
            raise AppError(ErrorCode.E01, "客户端证书加载失败") from exc
    return ctx


def cert_expiry_warning(not_after: datetime, *, warn_days: int = TLS_CERT_EXPIRY_WARN_DAYS) -> Optional[str]:
    now = datetime.now(timezone.utc)
    if not_after.tzinfo is None:
        not_after = not_after.replace(tzinfo=timezone.utc)
    remain = (not_after - now).days
    if remain < 0:
        return "证书已过期"
    if remain <= warn_days:
        return f"证书将在 {remain} 天后过期"
    return None
