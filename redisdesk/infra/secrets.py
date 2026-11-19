"""SecretLease：明文只以 bytearray / memoryview 短期持有，退出即清零。"""

from __future__ import annotations

from types import TracebackType
from typing import Callable, Optional

from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.limits import DPAPI_ENTROPY
from redisdesk.core.model import SecretRef
from redisdesk.infra import crypto


class SecretLease:
    """在 adapters 建池函数内打开，覆盖建连过程，不跨请求持有。"""

    def __init__(
        self,
        secret: SecretRef | None,
        *,
        unlock: Optional[Callable[[SecretRef], bytes]] = None,
        master_password: Optional[bytes] = None,
    ) -> None:
        self._secret = secret
        self._unlock = unlock or default_unlock
        self._master_password = master_password
        self._buf: Optional[bytearray] = None

    def __enter__(self) -> memoryview:
        if self._secret is None or self._secret.storage == "not_stored":
            raise AppError(ErrorCode.E02, "凭据未存储，请重新输入")
        try:
            plain = self._unlock(self._secret)
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AppError(ErrorCode.E02, "凭据解密失败") from exc
        if not isinstance(plain, (bytes, bytearray)):
            raise AppError(ErrorCode.E02, "凭据解密失败")
        self._buf = bytearray(plain)
        if isinstance(plain, bytearray):
            _wipe(plain)
        return memoryview(self._buf)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._buf is not None:
            _wipe(self._buf)
            self._buf = None

    def __repr__(self) -> str:  # pragma: no cover
        return f"SecretLease(id={self._secret.id if self._secret else None})"


def default_unlock(secret: SecretRef) -> bytes:
    if secret.cipher is None:
        raise AppError(ErrorCode.E02, "凭据不存在，请重新输入")
    entropy = secret.entropy_version or DPAPI_ENTROPY
    if secret.storage == "dpapi":
        return crypto.dpapi_unprotect(secret.cipher, entropy=entropy)
    if secret.storage == "dpapi_master":
        # cipher = salt(16) || aesgcm(dpapi_blob)；KEK 由调用方在上层解开后传入
        # 默认路径只解 DPAPI 层；主密码包裹在 store/secrets 的专用函数中处理。
        raise AppError(ErrorCode.E02, "主密码凭据需先解锁主密码")
    raise AppError(ErrorCode.E02, "不支持的凭据存储模式")


def protect_secret(plain: bytes, *, storage: str = "dpapi", entropy: str = DPAPI_ENTROPY) -> bytes:
    if storage == "not_stored":
        raise AppError(ErrorCode.E08, "not_stored 模式不得落盘")
    if storage == "dpapi":
        return crypto.dpapi_protect(plain, entropy=entropy)
    raise AppError(ErrorCode.E08, "不支持的凭据存储模式")


def _wipe(buf: bytearray) -> None:
    for i in range(len(buf)):
        buf[i] = 0
