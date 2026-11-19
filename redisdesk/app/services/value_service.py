"""值分页与解码 + 敏感遮罩。UI 拿不到未遮罩内容。"""

from __future__ import annotations

from redisdesk.adapters.codec import as_bytes, decode_text, try_pretty_json
from redisdesk.adapters.gateway import RedisGateway
from redisdesk.core.authz import Action, GrantResolver, check_or_raise
from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.limits import (
    BIG_COLLECTION_ITEMS,
    BIG_STRING_BYTES,
    STRING_CHUNK_KB,
    STRING_INLINE_LIMIT_KB,
    VALUE_PAGE_SIZE,
    VALUE_SCAN_COUNT,
)
from redisdesk.core.model import ConnectionProfile, SessionContext, ValueItem, ValuePage
from redisdesk.core.redact import SensitiveMatcher, format_mask


class ValueService:
    def __init__(self, grants: GrantResolver, matcher: SensitiveMatcher | None = None) -> None:
        self.grants = grants
        self.matcher = matcher or SensitiveMatcher()

    def load_string(
        self,
        session: SessionContext,
        profile: ConnectionProfile,
        gateway: RedisGateway,
        key: str,
        *,
        offset: int = 0,
    ) -> ValuePage:
        check_or_raise(session, Action.VIEW_VALUE, profile, grants=self.grants)
        raw_len = gateway.execute(["STRLEN", key], timeout_s=5)
        length = int(raw_len or 0)
        note = ""
        if length >= BIG_STRING_BYTES:
            note = f"该 String {length} 字节，将分块加载（{STRING_CHUNK_KB} KB/段）"
        inline_limit = STRING_INLINE_LIMIT_KB * 1024
        chunk = STRING_CHUNK_KB * 1024
        if length <= inline_limit and offset == 0:
            data = as_bytes(gateway.execute(["GET", key], timeout_s=5) or b"")
        else:
            end = min(length, offset + chunk) - 1
            if end < offset:
                data = b""
            else:
                data = as_bytes(gateway.execute(["GETRANGE", key, str(offset), str(end)], timeout_s=5) or b"")
            note = (note + "；" if note else "") + f"已加载 {offset + len(data)} / 共 {length} 字节"
        text, is_binary = decode_text(data)
        mask = self.matcher.match(key=key, value=text, value_len=len(data))
        item = ValueItem(
            field=None,
            value_text=mask.text if mask.masked else text,
            value_bytes_len=len(data),
            is_binary=is_binary,
            masked=mask.masked,
            mask_reason=mask.reason,
        )
        return ValuePage(
            key=key,
            key_type="string",
            page_index=offset // chunk,
            offset_or_cursor=str(offset),
            items=(item,),
            total=length,
            has_more=offset + len(data) < length,
            loaded_bytes=len(data),
            note=note,
        )

    def load_hash_page(
        self,
        session: SessionContext,
        profile: ConnectionProfile,
        gateway: RedisGateway,
        key: str,
        cursor: int = 0,
        *,
        match: str | None = None,
        page_index: int = 0,
    ) -> ValuePage:
        check_or_raise(session, Action.VIEW_VALUE, profile, grants=self.grants)
        total = int(gateway.execute(["HLEN", key], timeout_s=5) or 0)
        note = f"该 Hash 有 {total} 个 field，按页加载" if total > BIG_COLLECTION_ITEMS else ""
        argv = ["HSCAN", key, str(cursor), "COUNT", str(VALUE_SCAN_COUNT)]
        if match:
            argv.extend(["MATCH", match])
        raw = gateway.execute(argv, timeout_s=5)
        next_cursor, pairs = _scan_pairs(raw)
        items: list[ValueItem] = []
        # 成对 field/value
        it = iter(pairs)
        for field in it:
            value = next(it, b"")
            field_s = _to_str(field)
            data = as_bytes(value)
            text, is_binary = decode_text(data)
            mask = self.matcher.match(key=key, field=field_s, value=text, value_len=len(data))
            items.append(
                ValueItem(
                    field=field_s,
                    value_text=mask.text if mask.masked else text,
                    value_bytes_len=len(data),
                    is_binary=is_binary,
                    masked=mask.masked,
                    mask_reason=mask.reason,
                )
            )
            if len(items) >= VALUE_PAGE_SIZE:
                break
        return ValuePage(
            key=key,
            key_type="hash",
            page_index=page_index,
            offset_or_cursor=str(next_cursor),
            items=tuple(items),
            total=total,
            has_more=int(next_cursor) != 0,
            note=note,
        )


def _scan_pairs(raw: object) -> tuple[int, list[object]]:
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return 0, []
    cursor = raw[0]
    payload = raw[1] if isinstance(raw[1], (list, tuple)) else []
    try:
        next_cursor = int(cursor)
    except (TypeError, ValueError):
        next_cursor = int(_to_str(cursor) or "0")
    return next_cursor, list(payload)


def _to_str(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="surrogateescape")
    return str(value)
