"""统一脱敏：命令参数脱敏 + 敏感值遮罩规则。

命令参数脱敏（AUTH / CONFIG SET requirepass 等）与数据内容遮罩分开，
供日志、审计、导出、历史共用，禁止各模块各自实现一份。
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from redisdesk.core.limits import (
    ARGV_MASKED_MAX_CHARS,
    SENSITIVE_MATCH_TIMEOUT_MS,
    SENSITIVE_RULES_BUILTIN,
    VALUE_PREVIEW_CHARS,
)
from redisdesk.core.model import SensitiveRule

MASK_TOKEN = "***"
MASK_DISPLAY_TEMPLATE = "●●●●●● (已遮罩 · {n} 字节)"


# key 名内置通配（按 contains，大小写不敏感）
_BUILTIN_KEY_CONTAINS = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "privatekey",
    "private_key",
)

# 字段名内置精确（大小写不敏感）
_BUILTIN_FIELD_NAMES = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "token",
        "secret",
        "authorization",
        "cookie",
        "idcard",
        "id_card",
        "phone",
        "mobile",
        "email",
        "bankcard",
    }
)

_AUTH_COMMANDS = frozenset({"AUTH"})
_PASSWORD_CONFIG_KEYS = frozenset({"REQUIREPASS", "MASTERAUTH"})


def redact_argv(argv: Sequence[str]) -> list[str]:
    """对命令参数做脱敏，返回新列表（不修改入参）。"""
    if not argv:
        return []
    out = list(argv)
    name = out[0].upper()

    if name == "AUTH":
        if len(out) >= 2:
            out[-1] = MASK_TOKEN
        return out

    if name == "HELLO":
        _redact_after_keyword(out, "AUTH", password_is_last=True)
        return out

    if name == "CONFIG" and len(out) >= 4 and out[1].upper() == "SET":
        key = out[2].upper()
        if key in _PASSWORD_CONFIG_KEYS or _name_looks_secret(out[2]):
            out[3] = MASK_TOKEN
        return out

    if name == "ACL" and len(out) >= 2 and out[1].upper() == "SETUSER":
        for i in range(2, len(out)):
            if out[i].startswith(">") and len(out[i]) > 1:
                out[i] = ">" + MASK_TOKEN
            elif out[i].upper().startswith("#") or out[i].startswith("<"):
                continue
        return out

    if name == "MIGRATE":
        _redact_after_keyword(out, "AUTH", password_is_last=True)
        _redact_after_keyword(out, "AUTH2", password_is_last=True)
        return out

    return out


def format_argv_masked(argv: Sequence[str], limit: int = ARGV_MASKED_MAX_CHARS) -> str:
    redacted = redact_argv(argv)
    text = " ".join(_quote_if_needed(a) for a in redacted)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def argv_digest(argv: Sequence[str], hex_chars: int = 16) -> str:
    """原文 SHA-256 的前 N 位十六进制（对未脱敏 argv）。"""
    payload = "\0".join(argv).encode("utf-8", errors="surrogateescape")
    return hashlib.sha256(payload).hexdigest()[:hex_chars]


def _redact_after_keyword(out: list[str], keyword: str, *, password_is_last: bool) -> None:
    upper = [a.upper() for a in out]
    if keyword not in upper:
        return
    i = upper.index(keyword)
    # AUTH <user> <password> 或 AUTH <password>
    if keyword in {"AUTH", "AUTH2"}:
        if i + 2 < len(out):
            out[i + 2] = MASK_TOKEN
        elif i + 1 < len(out):
            out[i + 1] = MASK_TOKEN


def _name_looks_secret(name: str) -> bool:
    lowered = name.lower()
    return any(s in lowered for s in ("password", "secret", "token", "auth"))


def _quote_if_needed(token: str) -> str:
    if not token or any(c.isspace() for c in token):
        return '"' + token.replace('"', '\\"') + '"'
    return token


def builtin_sensitive_rules() -> list[SensitiveRule]:
    rules: list[SensitiveRule] = []
    for i, pat in enumerate(_BUILTIN_KEY_CONTAINS, 1):
        rules.append(
            SensitiveRule(
                id=f"builtin-key-{i}",
                name=f"key contains {pat}",
                target="key_name",
                match_type="contains",
                pattern=pat,
                action="mask_full",
                enabled=True,
                builtin=True,
            )
        )
    for i, pat in enumerate(sorted(_BUILTIN_FIELD_NAMES), 1):
        rules.append(
            SensitiveRule(
                id=f"builtin-field-{i}",
                name=f"field {pat}",
                target="field_name",
                match_type="contains",
                pattern=pat,
                action="mask_full",
                enabled=True,
                builtin=True,
            )
        )
    return rules


@dataclass(frozen=True, slots=True)
class MaskResult:
    masked: bool
    text: str
    reason: Optional[str]
    action: Optional[str] = None


class SensitiveMatcher:
    """编译缓存规则；单次匹配超时按「命中」处理（保守）。"""

    def __init__(self, rules: Iterable[SensitiveRule] | None = None, *, include_builtin: bool = SENSITIVE_RULES_BUILTIN) -> None:
        collected: list[SensitiveRule] = []
        if include_builtin:
            collected.extend(builtin_sensitive_rules())
        if rules:
            collected.extend(rules)
        self.rules = tuple(r for r in collected if r.enabled)
        self._compiled: list[tuple[SensitiveRule, Optional[re.Pattern[str]]]] = []
        for rule in self.rules:
            cre: Optional[re.Pattern[str]] = None
            if rule.match_type == "regex":
                try:
                    cre = re.compile(rule.pattern, re.IGNORECASE)
                except re.error:
                    cre = None
            self._compiled.append((rule, cre))

    def match(
        self,
        *,
        key: str | None = None,
        field: str | None = None,
        value: str | None = None,
        value_len: int | None = None,
        apply_to_key_name: bool = False,
    ) -> MaskResult:
        """默认只遮罩值；key 名仅在 apply_to_key_name 且命中 target=key_name 时遮罩。"""
        deadline = time.perf_counter() + (SENSITIVE_MATCH_TIMEOUT_MS / 1000.0)
        hit: Optional[SensitiveRule] = None
        for rule, cre in self._compiled:
            if time.perf_counter() > deadline:
                # 超时按命中处理
                hit = rule
                break
            subject = None
            if rule.target == "key_name":
                subject = key
            elif rule.target == "field_name":
                subject = field
            else:
                subject = value
            if subject is None:
                continue
            if self._matches(rule, cre, subject):
                hit = rule
                break
        if hit is None:
            return MaskResult(False, value if value is not None else (key or ""), None)
        # 浏览列表的 key 名默认不遮罩
        if hit.target == "key_name" and not apply_to_key_name and value is not None:
            # key 名规则用于判定「该 key 的值需要遮罩」
            n = value_len if value_len is not None else len(value.encode("utf-8", errors="surrogateescape"))
            return MaskResult(True, format_mask(n), hit.name, hit.action)
        if hit.target in {"field_name", "value"} or apply_to_key_name:
            raw = value if value is not None else (field or key or "")
            n = value_len if value_len is not None else len(raw.encode("utf-8", errors="surrogateescape"))
            return MaskResult(True, self._apply_action(hit, raw, n), hit.name, hit.action)
        return MaskResult(False, value or "", None)

    def _matches(self, rule: SensitiveRule, cre: Optional[re.Pattern[str]], subject: str) -> bool:
        if rule.match_type == "regex":
            if cre is None:
                return False
            return cre.search(subject) is not None
        lowered = subject.lower()
        pat = rule.pattern.lower()
        if rule.match_type == "prefix":
            return lowered.startswith(pat)
        # contains（通配 *token* 也按 contains）
        pat = pat.replace("*", "")
        return pat in lowered

    def _apply_action(self, rule: SensitiveRule, raw: str, n: int) -> str:
        if rule.action == "mask_partial" and n > 8:
            return raw[:2] + "…" + raw[-2:] + f" (部分遮罩 · {n} 字节)"
        if rule.action == "hash":
            digest = hashlib.sha256(raw.encode("utf-8", errors="surrogateescape")).hexdigest()[:12]
            return f"sha256:{digest} (已哈希 · {n} 字节)"
        return format_mask(n)


def format_mask(n: int) -> str:
    return MASK_DISPLAY_TEMPLATE.format(n=n)


def preview(text: str, limit: int = VALUE_PREVIEW_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"
