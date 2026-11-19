"""DPAPI、scrypt 口令哈希、AES-256-GCM。不自己实现密码学原语。"""

from __future__ import annotations

import base64
import ctypes
import os
import re
import secrets
from typing import Iterable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.limits import (
    DPAPI_ENTROPY,
    EXPORT_NONCE_BYTES,
    EXPORT_TAG_BYTES,
    MASTER_PASSWORD_MIN_LENGTH,
    PASSWORD_COMPLEXITY_CLASSES,
    PASSWORD_MIN_LENGTH,
    SCRYPT_DKLEN,
    SCRYPT_N,
    SCRYPT_P,
    SCRYPT_R,
    SCRYPT_SALT_BYTES,
)

# 常见弱口令（内置表的精简子集；完整 10000 条可通过弱口令文件扩展）。
_COMMON_WEAK = frozenset(
    s.lower()
    for s in """
    password password123 password1234 password!123 qwerty qwerty123 12345678 123456789
    1234567890 11111111 abc123 abc123456 letmein welcome welcome123 admin admin123
    root root123 passw0rd p@ssw0rd p@ssword iloveyou monkey dragon master login
    sunshine princess football baseball michael shadow 00000000 66666666 88888888
    changeme default pass1234 1q2w3e4r 1qaz2wsx zaq12wsx qazwsx asdfgh !qaz2wsx
    redis redis123 redisdesk redisdesk123 test test1234 tester testing admin@123
    administrator p@ssw0rd123 Password1 Password123 Password123! Admin1234
    Winter2024 Winter2025 Spring2024 Summer2024 Autumn2024
    """.split()
)

_COMPLEXITY = [
    ("upper", re.compile(r"[A-Z]")),
    ("lower", re.compile(r"[a-z]")),
    ("digit", re.compile(r"[0-9]")),
    ("symbol", re.compile(r"[^A-Za-z0-9]")),
]


def validate_password_policy(
    password: str,
    *,
    username: str = "",
    min_length: int = PASSWORD_MIN_LENGTH,
    min_classes: int = PASSWORD_COMPLEXITY_CLASSES,
    extra_denied: Iterable[str] = (),
) -> list[str]:
    """返回未通过的原因列表；空列表表示通过。"""
    errors: list[str] = []
    if len(password) < min_length:
        errors.append(f"口令长度至少 {min_length} 位")
    classes = sum(1 for _, rx in _COMPLEXITY if rx.search(password))
    if classes < min_classes:
        errors.append(f"口令需包含至少 {min_classes} 类字符（大写/小写/数字/符号）")
    if username and username.lower() in password.lower():
        errors.append("口令不得包含用户名")
    lowered = password.lower()
    if lowered in _COMMON_WEAK or password in _COMMON_WEAK:
        errors.append("口令过于常见，请更换")
    for item in extra_denied:
        if item and secrets.compare_digest(item, password):
            errors.append("口令不得与最近使用过的口令重复")
            break
        if item and item.lower() == lowered:
            errors.append("口令不得与最近使用过的口令重复")
            break
    return errors


def hash_password(password: str) -> str:
    """格式 scrypt$N$r$p$<salt_b64>$<hash_b64>。"""
    salt = os.urandom(SCRYPT_SALT_BYTES)
    digest = _scrypt(password.encode("utf-8"), salt)
    return "scrypt${}${}${}${}${}".format(
        SCRYPT_N,
        SCRYPT_R,
        SCRYPT_P,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, n_s, r_s, p_s, salt_b64, hash_b64 = encoded.split("$")
        if algo != "scrypt":
            return False
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    actual = _scrypt(password.encode("utf-8"), salt, n=n, r=r, p=p, dklen=len(expected))
    return secrets.compare_digest(actual, expected)


def _scrypt(data: bytes, salt: bytes, *, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P, dklen: int = SCRYPT_DKLEN) -> bytes:
    kdf = Scrypt(salt=salt, length=dklen, n=n, r=r, p=p)
    return kdf.derive(data)


def generate_recovery_code(length: int) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def aesgcm_encrypt(key: bytes, plaintext: bytes, *, aad: bytes = b"") -> bytes:
    """返回 nonce || ciphertext+tag（cryptography 默认 tag 附在密文末尾）。"""
    if len(key) != 32:
        raise AppError(ErrorCode.E12, "AES-256-GCM 密钥长度必须为 32 字节")
    nonce = os.urandom(EXPORT_NONCE_BYTES)
    ct = AESGCM(key).encrypt(nonce, plaintext, aad)
    return nonce + ct


def aesgcm_decrypt(key: bytes, blob: bytes, *, aad: bytes = b"") -> bytes:
    if len(blob) < EXPORT_NONCE_BYTES + EXPORT_TAG_BYTES:
        raise AppError(ErrorCode.E02, "密文格式无效")
    nonce, ct = blob[:EXPORT_NONCE_BYTES], blob[EXPORT_NONCE_BYTES:]
    try:
        return AESGCM(key).decrypt(nonce, ct, aad)
    except InvalidTag as exc:
        raise AppError(ErrorCode.E02, "解密失败") from exc


def derive_kek(password: str, salt: bytes, *, n: int = SCRYPT_N) -> bytes:
    if len(password) < MASTER_PASSWORD_MIN_LENGTH:
        raise AppError(ErrorCode.E08, f"主密码长度至少 {MASTER_PASSWORD_MIN_LENGTH} 位")
    return _scrypt(password.encode("utf-8"), salt, n=n)


# ---------------------------------------------------------------------------
# DPAPI（Windows）
# ---------------------------------------------------------------------------

CRYPTPROTECT_UI_FORBIDDEN = 0x01


def _data_blob_type():
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    return DATA_BLOB


def _is_windows() -> bool:
    return os.name == "nt"


def dpapi_protect(plain: bytes, *, entropy: str = DPAPI_ENTROPY) -> bytes:
    if not _is_windows():
        raise AppError(ErrorCode.E12, "DPAPI 仅在 Windows 上可用", hint="请在 Windows 上运行 RedisDesk")
    return _crypt_protect(plain, entropy.encode("utf-8"), protect=True)


def dpapi_unprotect(blob: bytes, *, entropy: str = DPAPI_ENTROPY) -> bytes:
    if not _is_windows():
        raise AppError(ErrorCode.E12, "DPAPI 仅在 Windows 上可用")
    try:
        return _crypt_protect(blob, entropy.encode("utf-8"), protect=False)
    except OSError as exc:
        raise AppError(ErrorCode.E02, "凭据与 Windows 用户绑定，请重新输入") from exc


def _crypt_protect(data: bytes, entropy: bytes, *, protect: bool) -> bytes:
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    DATA_BLOB = _data_blob_type()

    def _blob(raw: bytes):
        buf = ctypes.create_string_buffer(raw, len(raw))
        return DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf

    in_blob, in_keep = _blob(data)
    ent_blob, ent_keep = _blob(entropy) if entropy else (DATA_BLOB(0, None), None)
    out_blob = DATA_BLOB()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    flags = CRYPTPROTECT_UI_FORBIDDEN
    ok = fn(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(ent_blob) if entropy else None,
        None,
        None,
        flags,
        ctypes.byref(out_blob),
    )
    # 保持缓冲生命周期
    _ = in_keep, ent_keep
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
