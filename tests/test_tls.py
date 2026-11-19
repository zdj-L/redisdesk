import ssl

import pytest

from redisdesk.adapters.tls import build_ssl_context, require_tls_or_exception
from redisdesk.core.errors import AppError
from redisdesk.core.model import TlsConfig


def test_plaintext_without_exception_rejected():
    with pytest.raises(AppError) as exc:
        require_tls_or_exception(TlsConfig(enabled=False, allow_plaintext=False))
    assert exc.value.code == "E01"


def test_plaintext_exception_ok():
    require_tls_or_exception(
        TlsConfig(enabled=False, allow_plaintext=True, plaintext_reason="local-dev-only")
    )
    assert build_ssl_context(TlsConfig(enabled=False, allow_plaintext=True, plaintext_reason="local-dev-only")) is None


def test_tls_context_minimum_and_verify():
    ctx = build_ssl_context(TlsConfig(enabled=True))
    assert ctx is not None
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert ctx.check_hostname is True
    assert ctx.verify_mode == ssl.CERT_REQUIRED
