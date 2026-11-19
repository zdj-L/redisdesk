"""二进制检测、JSON 美化、HEX、超长截断。"""

from __future__ import annotations

import json
from typing import Optional

from redisdesk.core.limits import (
    BINARY_NONPRINT_RATIO,
    BINARY_SNIFF_BYTES,
    JSON_PRETTY_LIMIT_KB,
    VALUE_PREVIEW_CHARS,
)

_PRINTABLE_EXTRA = frozenset({0x09, 0x0A, 0x0D})  # \t \n \r


def is_binary(data: bytes, *, sniff: int = BINARY_SNIFF_BYTES, ratio: float = BINARY_NONPRINT_RATIO) -> bool:
    if not data:
        return False
    sample = data[:sniff]
    if b"\x00" in sample:
        return True
    nonprint = 0
    for b in sample:
        if b < 0x20 and b not in _PRINTABLE_EXTRA:
            nonprint += 1
        elif b == 0x7F:
            nonprint += 1
    return (nonprint / len(sample)) >= ratio


def to_hex_dump(data: bytes, *, width: int = 16, limit: Optional[int] = None) -> str:
    chunk = data if limit is None else data[:limit]
    lines: list[str] = []
    for offset in range(0, len(chunk), width):
        block = chunk[offset : offset + width]
        hexpart = " ".join(f"{b:02x}" for b in block)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in block)
        lines.append(f"{offset:08x}  {hexpart:<{width * 3}}  {ascii_part}")
    return "\n".join(lines)


def escape_controls(text: str) -> str:
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if ch in "\t\n\r":
            out.append(ch)
        elif code < 0x20 or code == 0x7F:
            out.append(f"\\x{code:02x}")
        else:
            out.append(ch)
    return "".join(out)


def try_pretty_json(data: bytes | str, *, limit_kb: int = JSON_PRETTY_LIMIT_KB) -> Optional[str]:
    if isinstance(data, bytes):
        if len(data) > limit_kb * 1024:
            return None
        if not data or data[0] not in (0x7B, 0x5B):  # { or [
            return None
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return None
    else:
        if len(data.encode("utf-8")) > limit_kb * 1024:
            return None
        stripped = data.lstrip()
        if not stripped or stripped[0] not in "{[":
            return None
        text = data
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    return json.dumps(obj, ensure_ascii=False, indent=2)


def decode_text(data: bytes) -> tuple[str, bool]:
    """返回 (text, is_binary)。二进制时 text 为转义后的拉丁可视形式。"""
    if is_binary(data):
        return to_hex_dump(data), True
    try:
        return data.decode("utf-8"), False
    except UnicodeDecodeError:
        return to_hex_dump(data), True


def preview(text: str, limit: int = VALUE_PREVIEW_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def as_bytes(value: bytes | str | memoryview) -> bytes:
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8", errors="surrogateescape")
