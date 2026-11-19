"""Application-owned SSH known_hosts: list, add, delete, and verify helpers.

The writable file lives under app data (`known_hosts_redisdesk`). RedisDesk
never writes the user's ~/.ssh/known_hosts; that path may be read, read-only,
when a system known_hosts path is configured.
"""

from __future__ import annotations

import base64
import hashlib
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.infra.paths import known_hosts_path, system_known_hosts_path


def canonical_host(host: str, port: int | None) -> str:
    name = (host or "").strip()
    if not name:
        return ""
    port_n = int(port or 22)
    if name.startswith("[") and "]:" in name:
        return name
    if port_n == 22:
        return name
    return f"[{name}]:{port_n}"


def split_host_port(name: str, default_port: int = 22) -> tuple[str, int]:
    text = (name or "").strip()
    if text.startswith("[") and "]:" in text:
        host, port_text = text[1:].split("]:", 1)
        try:
            return host, int(port_text)
        except ValueError:
            return host, default_port
    return text, default_port


def fingerprint_sha256(key: Any) -> str:
    blob = key.asbytes() if hasattr(key, "asbytes") else bytes(key)
    digest = hashlib.sha256(blob).digest()
    return "SHA256:" + base64.b64encode(digest).rstrip(b"=").decode("ascii")


def normalize_fingerprint(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if text.lower().startswith("sha256:"):
        text = "SHA256:" + text.split(":", 1)[1]
    return text


def fingerprints_match(left: str | None, right: str | None) -> bool:
    return normalize_fingerprint(left) == normalize_fingerprint(right) and bool(normalize_fingerprint(left))


def _key_type(key: Any) -> str:
    if hasattr(key, "get_name"):
        return str(key.get_name())
    return "ssh-unknown"


@dataclass(slots=True)
class KnownHostEntry:
    host: str
    port: int
    key_type: str
    fingerprint: str
    name: str = ""

    @property
    def display_host(self) -> str:
        return self.host or self.name


class UnknownHostKeyError(AppError):
    """Unknown host key. TOFU may prompt; strict rejects without an accept path."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        key: Any,
        policy: str,
    ) -> None:
        loc = f"{host}:{int(port or 22)}"
        key_type = _key_type(key)
        fingerprint = fingerprint_sha256(key)
        policy_name = (policy or "strict").lower()
        if policy_name == "tofu":
            message = f"\u9996\u6b21\u8fde\u63a5 SSH \u8df3\u677f {loc}\uff0c\u4e3b\u673a\u5bc6\u94a5\u5c1a\u672a\u767b\u8bb0\u3002\u8bf7\u6838\u5bf9\u6307\u7eb9\u540e\u518d\u5199\u5165\u672c\u5e94\u7528 known_hosts\u3002"
        else:
            message = f"SSH \u8df3\u677f {loc} \u7684\u4e3b\u673a\u5bc6\u94a5\u5c1a\u672a\u767b\u8bb0\uff0c\u5f53\u524d\u7b56\u7565\u4e3a\u4e25\u683c\u6821\u9a8c\uff0c\u5df2\u62d2\u7edd\u8fde\u63a5\u3002"
        hint = f"{key_type} {fingerprint}"
        AppError.__init__(self, ErrorCode.E07, message, hint=hint)
        self.host = host
        self.port = int(port or 22)
        self.key = key
        self.key_type = key_type
        self.fingerprint = fingerprint
        self.policy = policy_name


class HostKeyMismatchError(AppError):
    """Host key changed. Never offer ignore-and-continue."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        expected_fingerprint: str = "",
        actual_fingerprint: str = "",
        key_type: str = "",
    ) -> None:
        loc = f"{host}:{int(port or 22)}"
        message = (
            f"SSH \u8df3\u677f {loc} \u7684\u4e3b\u673a\u5bc6\u94a5\u4e0e\u5df2\u767b\u8bb0\u7684\u4e0d\u4e00\u81f4\uff0c\u5df2\u62d2\u7edd\u8fde\u63a5\u3002"
            "\u8bf7\u4eba\u5de5\u6838\u5bf9\u6307\u7eb9\uff1b\u786e\u8ba4\u5b89\u5168\u540e\u53ef\u5728\u300c\u8bbe\u7f6e \u2192 SSH \u5df2\u77e5\u4e3b\u673a\u300d\u4e2d\u5220\u9664\u8be5\u6761\u76ee\u5e76\u91cd\u65b0\u767b\u8bb0\u3002"
            "\u4e0d\u63d0\u4f9b\u300c\u5ffd\u7565\u5e76\u7ee7\u7eed\u300d\u3002"
        )
        parts = []
        if expected_fingerprint:
            parts.append(f"\u5df2\u767b\u8bb0\uff1a{expected_fingerprint}")
        if actual_fingerprint:
            parts.append(f"\u672c\u6b21\u63d0\u793a\uff1a{key_type + ' ' if key_type else ''}{actual_fingerprint}".rstrip())
        hint = "\uff1b".join(parts)
        AppError.__init__(self, ErrorCode.E07, message, hint=hint)
        self.host = host
        self.port = int(port or 22)
        self.expected_fingerprint = expected_fingerprint
        self.actual_fingerprint = actual_fingerprint
        self.key_type = key_type


class MissingHostKeyPolicy:
    """Paramiko-compatible policy: never auto-add; raise a structured AppError."""

    def __init__(self, *, port: int, policy: str) -> None:
        self.port = int(port or 22)
        self.policy = (policy or "strict").lower()

    def missing_host_key(self, client, hostname, key):  # noqa: ANN001
        host, parsed_port = split_host_port(hostname, self.port)
        port = parsed_port if hostname.startswith("[") else self.port
        raise UnknownHostKeyError(host=host, port=port, key=key, policy=self.policy)


class KnownHostsStore:
    """Atomic read/write for the app known_hosts file only."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else known_hosts_path()
        self._lock = threading.Lock()

    def list_entries(self) -> list[KnownHostEntry]:
        keys = self._load()
        entries: list[KnownHostEntry] = []
        if keys is None:
            return entries
        for name in keys.keys():
            host, port = split_host_port(str(name), 22)
            entries_for_host = keys.lookup(name)
            if not entries_for_host:
                continue
            for key_type, key in entries_for_host.items():
                entries.append(
                    KnownHostEntry(
                        host=host,
                        port=port,
                        key_type=str(key_type),
                        fingerprint=fingerprint_sha256(key),
                        name=str(name),
                    )
                )
        entries.sort(key=lambda item: (item.host, item.port, item.key_type, item.fingerprint))
        return entries

    def add(self, host: str, port: int, key: Any) -> KnownHostEntry:
        name = canonical_host(host, port)
        if not name:
            raise AppError(ErrorCode.E11, "\u65e0\u6cd5\u767b\u8bb0\u4e3b\u673a\u5bc6\u94a5\uff1a\u4e3b\u673a\u540d\u4e3a\u7a7a\u3002")
        if key is None:
            raise AppError(ErrorCode.E11, "\u65e0\u6cd5\u767b\u8bb0\u4e3b\u673a\u5bc6\u94a5\uff1a\u7f3a\u5c11\u5bc6\u94a5\u3002")
        with self._lock:
            keys = self._load(create=True)
            assert keys is not None
            keys.add(name, _key_type(key), key)
            self._save(keys)
        host_name, port_n = split_host_port(name, int(port or 22))
        return KnownHostEntry(
            host=host_name,
            port=port_n,
            key_type=_key_type(key),
            fingerprint=fingerprint_sha256(key),
            name=name,
        )

    def remove(self, host: str, port: int, key_type: str | None = None) -> int:
        name = canonical_host(host, port)
        if not name:
            return 0
        with self._lock:
            keys = self._load()
            if keys is None:
                return 0
            present = keys.lookup(name)
            if not present:
                # also try the raw host without port canonicalization
                present = keys.lookup(host)
                if present:
                    name = host
            if not present:
                return 0
            removed = 0
            if key_type:
                if key_type in present:
                    del present[key_type]
                    removed = 1
            else:
                removed = len(list(present.keys()))
                present.clear()
            # paramiko HostKeys keeps a dict; delete hostname if empty
            if not present and hasattr(keys, "pop"):
                try:
                    keys.pop(name, None)
                except Exception:
                    pass
            self._save(keys)
            return removed

    def load_into_client(self, client: Any, *, system_path: Path | None | object = Ellipsis) -> None:
        """Load system keys read-only, then the writable app store.

        `system_path is Ellipsis` means use system_known_hosts_path(). Pass None
        to skip the system file entirely.
        """
        if system_path is Ellipsis:
            system_path = system_known_hosts_path()
        if system_path is not None:
            sys_file = Path(system_path)
            if sys_file.is_file():
                try:
                    client.load_system_host_keys(str(sys_file))
                except Exception:
                    pass
        if self.path.is_file():
            try:
                client.load_host_keys(str(self.path))
            except Exception:
                pass
        # Prevent paramiko from later writing whatever file it last loaded.
        try:
            client._host_keys_filename = None
        except Exception:
            pass

    def _load(self, *, create: bool = False):
        try:
            import paramiko
        except ImportError as exc:  # pragma: no cover
            raise AppError(ErrorCode.E07, "\u672a\u5b89\u88c5 paramiko\uff0c\u65e0\u6cd5\u7ba1\u7406 SSH known_hosts\u3002") from exc
        if not self.path.exists():
            if not create:
                return None
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.touch()
        keys = paramiko.HostKeys()
        try:
            keys.load(str(self.path))
        except Exception:
            if create:
                return keys
            raise AppError(ErrorCode.E11, f"\u65e0\u6cd5\u8bfb\u53d6 known_hosts\uff1a{self.path}")
        return keys

    def _save(self, keys) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            keys.save(str(tmp))
            os.replace(tmp, self.path)
        except Exception as exc:  # noqa: BLE001
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
            raise AppError(ErrorCode.E11, f"\u65e0\u6cd5\u5199\u5165 known_hosts\uff1a{self.path}") from exc


def mismatch_from_paramiko(exc: BaseException, *, host: str, port: int) -> HostKeyMismatchError:
    expected = ""
    actual = ""
    key_type = ""
    expected_key = getattr(exc, "expected_key", None)
    got_key = getattr(exc, "key", None)
    if expected_key is not None:
        try:
            expected = fingerprint_sha256(expected_key)
        except Exception:
            expected = ""
    if got_key is not None:
        try:
            actual = fingerprint_sha256(got_key)
            key_type = _key_type(got_key)
        except Exception:
            actual = ""
    return HostKeyMismatchError(
        host=host,
        port=port,
        expected_fingerprint=expected,
        actual_fingerprint=actual,
        key_type=key_type,
    )


def assert_pinned_fingerprint(key: Any, pinned: str | None, *, host: str, port: int) -> None:
    expected = normalize_fingerprint(pinned)
    if not expected:
        return
    actual = fingerprint_sha256(key)
    if not fingerprints_match(expected, actual):
        raise HostKeyMismatchError(
            host=host,
            port=port,
            expected_fingerprint=expected,
            actual_fingerprint=actual,
            key_type=_key_type(key),
        )
