"""GitHub Releases update channel: check, verify, and launch the Windows setup.

This is an unsigned internal-preview channel. Downloads are never treated as
signed. Installation requires an explicit confirmation and a SHA-256 digest.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from redisdesk import __version__
from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.infra.paths import app_data_dir

DEFAULT_REPO = "zdj-L/redisdesk"
DEFAULT_API_URL = f"https://api.github.com/repos/{DEFAULT_REPO}/releases/latest"
MANIFEST_NAMES = ("latest.json", "update-manifest.json")
SUMS_NAMES = ("SHA256SUMS.txt", "SHA256SUMS", "SHA256SUMS.sha256")
CHECK_INTERVAL = timedelta(hours=24)
SNOOZE_INTERVAL = timedelta(hours=24)
STARTUP_DELAY_MS = 12_000
DOWNLOAD_TIMEOUT_S = 60
CHECK_TIMEOUT_S = 20
MAX_MANIFEST_BYTES = 1_000_000
MAX_ASSET_BYTES = 400 * 1024 * 1024
USER_AGENT = f"RedisDesk/{__version__} (update-check; +https://github.com/{DEFAULT_REPO})"
PREF_AUTO_CHECK = "update_auto_check"
PREF_SKIPPED = "update_skipped_version"
PREF_SNOOZE_UNTIL = "update_snooze_until"
PREF_LAST_CHECK = "update_last_check_at"
_ALLOWED_HOSTS = {
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}


HttpOpener = Callable[[str, float], tuple[bytes, Mapping[str, str]]]


@dataclass(frozen=True, slots=True)
class UpdateAsset:
    name: str
    url: str
    size: int | None
    sha256: str | None


@dataclass(frozen=True, slots=True)
class UpdateOffer:
    version: str
    current_version: str
    notes: str
    asset: UpdateAsset
    unsigned: bool = True
    published_at: str = ""
    source: str = "github-releases"


def parse_version(value: str) -> tuple[int, ...]:
    text = (value or "").strip()
    if text.lower().startswith("v") and len(text) > 1 and text[1].isdigit():
        text = text[1:]
    numeric = re.split(r"[^0-9.]", text, maxsplit=1)[0]
    if not numeric or not re.search(r"\d", numeric):
        raise ValueError(f"invalid version: {value!r}")
    parts = [int(part) if part else 0 for part in numeric.split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def looks_like_setup(name: str) -> bool:
    lower = (name or "").lower().rsplit("/", 1)[-1]
    if not lower.endswith("setup.exe"):
        return False
    if lower.endswith("uninstall.exe") or "uninstall" in lower:
        return False
    if "portable" in lower or lower.endswith(".zip"):
        return False
    return True


def updates_dir() -> Path:
    path = app_data_dir() / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: object) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def auto_check_enabled(settings: Mapping[str, Any] | Any) -> bool:
    getter = settings.get if hasattr(settings, "get") else lambda key, default=None: default
    value = getter(PREF_AUTO_CHECK, True)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "off", "no"}
    return bool(value) if value is not None else True


def should_auto_check(settings: Any, *, now: datetime | None = None, frozen: bool | None = None) -> bool:
    if os.environ.get("REDISDESK_SKIP_UPDATE_CHECK", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    if os.environ.get("REDISDESK_FORCE_UPDATE_CHECK", "").strip().lower() in {"1", "true", "yes", "on"}:
        frozen = True
    if frozen is None:
        from redisdesk.infra.integrity import running_frozen
        frozen = running_frozen()
    if not frozen:
        return False
    if not auto_check_enabled(settings):
        return False
    moment = now or _now()
    last = _parse_iso(settings.get(PREF_LAST_CHECK) if hasattr(settings, "get") else None)
    if last is not None and moment - last < CHECK_INTERVAL:
        return False
    snooze = _parse_iso(settings.get(PREF_SNOOZE_UNTIL) if hasattr(settings, "get") else None)
    if snooze is not None and moment < snooze:
        return False
    return True


def should_prompt(offer: UpdateOffer, settings: Any, *, now: datetime | None = None) -> bool:
    skipped = ""
    if hasattr(settings, "get"):
        skipped = str(settings.get(PREF_SKIPPED) or "")
    if skipped and skipped == offer.version:
        return False
    snooze = _parse_iso(settings.get(PREF_SNOOZE_UNTIL) if hasattr(settings, "get") else None)
    if snooze is not None and (now or _now()) < snooze:
        return False
    return True


def record_last_check(settings: Any, *, now: datetime | None = None) -> None:
    if hasattr(settings, "set"):
        settings.set(PREF_LAST_CHECK, (now or _now()).isoformat())


def record_skip(settings: Any, version: str) -> None:
    if hasattr(settings, "set"):
        settings.set(PREF_SKIPPED, version)


def record_snooze(settings: Any, *, now: datetime | None = None) -> None:
    if hasattr(settings, "set"):
        settings.set(PREF_SNOOZE_UNTIL, ((now or _now()) + SNOOZE_INTERVAL).isoformat())


def manifest_url_override() -> str | None:
    text = os.environ.get("REDISDESK_UPDATE_MANIFEST_URL", "").strip()
    return text or None


def api_url() -> str:
    return os.environ.get("REDISDESK_UPDATE_API_URL", "").strip() or DEFAULT_API_URL


def default_http_get(url: str, timeout: float) -> tuple[bytes, Mapping[str, str]]:
    _assert_https_url(url)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json, application/json, text/plain, */*",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_MANIFEST_BYTES + 1)
            headers = {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        raise AppError(ErrorCode.E01, _http_error_message(exc)) from exc
    except urllib.error.URLError as exc:
        raise AppError(ErrorCode.E01, "无法连接到更新源，请检查网络后再试。") from exc
    except TimeoutError as exc:
        raise AppError(ErrorCode.E03, "检查更新超时，请稍后重试。") from exc
    if len(data) > MAX_MANIFEST_BYTES:
        raise AppError(ErrorCode.E15, "更新清单过大，已拒绝读取。")
    return data, headers


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    if exc.code == 404:
        return "更新源尚未发布任何版本。"
    if exc.code in {403, 429}:
        return "更新源暂时限制访问，请稍后再试。"
    return f"检查更新失败（HTTP {exc.code}）。"


def _assert_https_url(url: str, *, allow_test_hosts: bool = True) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise AppError(ErrorCode.E15, "更新地址必须使用 HTTPS。")
    host = (parsed.hostname or "").lower()
    if host in _ALLOWED_HOSTS:
        return
    if allow_test_hosts and host in {"127.0.0.1", "localhost"}:
        return
    if os.environ.get("REDISDESK_UPDATE_ALLOW_HOST", "").strip().lower() == host:
        return
    raise AppError(ErrorCode.E15, f"拒绝来自未允许主机的更新地址（{host}）。")


def parse_sha256sums(text: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+)$", line)
        if not match:
            continue
        mapping[Path(match.group(2).strip()).name] = match.group(1).lower()
    return mapping


def parse_manifest(raw: object, *, current_version: str) -> UpdateOffer:
    if not isinstance(raw, dict):
        raise AppError(ErrorCode.E15, "更新清单格式无效。")
    version = str(raw.get("version") or raw.get("tag") or "").strip()
    if not version:
        raise AppError(ErrorCode.E15, "更新清单缺少版本号。")
    asset_raw = raw.get("asset") or raw.get("installer") or {}
    if not isinstance(asset_raw, dict):
        raise AppError(ErrorCode.E15, "更新清单缺少安装包信息。")
    name = str(asset_raw.get("name") or "").strip()
    url = str(asset_raw.get("url") or asset_raw.get("browser_download_url") or "").strip()
    if not name or not url:
        raise AppError(ErrorCode.E08, "更新清单未提供 Windows 安装包（setup.exe）。")
    if not looks_like_setup(name):
        raise AppError(ErrorCode.E08, "更新通道只接受 Windows 安装包（setup.exe），不使用便携版 ZIP。")
    size_val = asset_raw.get("size")
    try:
        size = int(size_val) if size_val is not None and str(size_val) != "" else None
    except (TypeError, ValueError) as exc:
        raise AppError(ErrorCode.E15, "更新清单中的安装包大小无效。") from exc
    sha = _normalize_sha256(asset_raw.get("sha256") or asset_raw.get("sha256sum"))
    notes = str(raw.get("notes") or raw.get("body") or raw.get("changelog") or "").strip()
    return UpdateOffer(
        version=version,
        current_version=current_version,
        notes=notes,
        asset=UpdateAsset(name=name, url=url, size=size, sha256=sha),
        unsigned=bool(raw.get("unsigned", True)),
        published_at=str(raw.get("published_at") or ""),
        source="manifest",
    )


def _normalize_sha256(value: object) -> str | None:
    if not value:
        return None
    text = str(value).strip().lower()
    if text.startswith("sha256:"):
        text = text[7:]
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise AppError(ErrorCode.E15, "更新清单中的 SHA-256 无效。")
    return text


def offer_from_release(payload: dict[str, Any], *, current_version: str, sums: Mapping[str, str] | None = None) -> UpdateOffer:
    tag = str(payload.get("tag_name") or payload.get("name") or "").strip()
    version = tag[1:] if tag.lower().startswith("v") and len(tag) > 1 else tag
    if not version:
        raise AppError(ErrorCode.E15, "Release 缺少版本号。")
    assets = payload.get("assets") or []
    setup = None
    if isinstance(assets, list):
        for item in assets:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            if looks_like_setup(name):
                setup = item
                break
    if setup is None:
        raise AppError(ErrorCode.E08, "该版本没有提供 Windows 安装包（setup.exe）。")
    name = str(setup.get("name") or "")
    url = str(setup.get("browser_download_url") or setup.get("url") or "")
    size_val = setup.get("size")
    try:
        size = int(size_val) if size_val is not None else None
    except (TypeError, ValueError):
        size = None
    sha = None
    if sums:
        sha = sums.get(name) or sums.get(Path(name).name)
        if sha:
            sha = _normalize_sha256(sha)
    digest = setup.get("digest")
    if sha is None and isinstance(digest, str) and digest.lower().startswith("sha256:"):
        sha = _normalize_sha256(digest)
    return UpdateOffer(
        version=version,
        current_version=current_version,
        notes=str(payload.get("body") or "").strip(),
        asset=UpdateAsset(name=name, url=url, size=size, sha256=sha),
        unsigned=True,
        published_at=str(payload.get("published_at") or ""),
        source="github-releases",
    )


def check_for_update(
    *,
    current_version: str | None = None,
    http_get: HttpOpener | None = None,
) -> UpdateOffer | None:
    current = current_version or __version__
    getter = http_get or default_http_get
    override = manifest_url_override()
    if override:
        offer = parse_manifest(_load_json(getter, override), current_version=current)
    else:
        offer = _check_github(getter, current)
    if not is_newer(offer.version, current):
        return None
    _assert_https_url(offer.asset.url)
    return offer


def _load_json(http_get: HttpOpener, url: str) -> Any:
    data, _headers = http_get(url, CHECK_TIMEOUT_S)
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppError(ErrorCode.E15, "无法解析更新清单。") from exc


def _check_github(http_get: HttpOpener, current: str) -> UpdateOffer:
    payload = _load_json(http_get, api_url())
    if not isinstance(payload, dict):
        raise AppError(ErrorCode.E15, "GitHub Release 响应无效。")
    assets = payload.get("assets") if isinstance(payload.get("assets"), list) else []
    manifest_asset = next(
        (item for item in assets if isinstance(item, dict) and str(item.get("name") or "") in MANIFEST_NAMES),
        None,
    )
    if manifest_asset:
        url = str(manifest_asset.get("browser_download_url") or manifest_asset.get("url") or "")
        offer = parse_manifest(_load_json(http_get, url), current_version=current)
        return offer
    sums = None
    sums_asset = next(
        (item for item in assets if isinstance(item, dict) and str(item.get("name") or "") in SUMS_NAMES),
        None,
    )
    if sums_asset:
        url = str(sums_asset.get("browser_download_url") or sums_asset.get("url") or "")
        data, _ = http_get(url, CHECK_TIMEOUT_S)
        sums = parse_sha256sums(data.decode("utf-8", errors="replace"))
    return offer_from_release(payload, current_version=current, sums=sums)


def verify_file(path: Path, *, sha256: str | None, size: int | None) -> str:
    if not path.is_file():
        raise AppError(ErrorCode.E15, "下载的安装包不存在，已中止安装。")
    actual_size = path.stat().st_size
    if size is not None and actual_size != int(size):
        _safe_unlink(path)
        raise AppError(
            ErrorCode.E15,
            f"安装包大小不匹配（期望 {size} 字节，实际 {actual_size} 字节），已删除下载文件并拒绝安装。",
        )
    digest = sha256_file(path)
    if sha256:
        if digest != sha256.lower():
            _safe_unlink(path)
            raise AppError(
                ErrorCode.E15,
                "安装包 SHA-256 与清单不一致，已删除下载文件并拒绝安装。",
            )
    else:
        _safe_unlink(path)
        raise AppError(
            ErrorCode.E15,
            "该版本未提供 SHA-256 摘要，未签名预览通道拒绝安装未经校验的安装包。",
        )
    return digest


def require_digest(offer: UpdateOffer) -> None:
    if not offer.asset.sha256:
        raise AppError(
            ErrorCode.E15,
            "该版本未提供 SHA-256 摘要，未签名预览通道拒绝下载未经校验的安装包。请等待发布方上传校验清单后再试。",
        )
    if not looks_like_setup(offer.asset.name):
        raise AppError(ErrorCode.E08, "更新通道只接受 Windows 安装包（setup.exe）。")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def download_and_verify(
    offer: UpdateOffer,
    dest_dir: Path | None = None,
    *,
    http_get: HttpOpener | None = None,
) -> Path:
    require_digest(offer)
    _assert_https_url(offer.asset.url)
    folder = dest_dir or updates_dir()
    folder.mkdir(parents=True, exist_ok=True)
    safe_name = Path(offer.asset.name).name
    if not looks_like_setup(safe_name):
        raise AppError(ErrorCode.E08, "更新通道只接受 Windows 安装包（setup.exe）。")
    dest = folder / safe_name
    tmp = dest.with_suffix(dest.suffix + ".part")
    _safe_unlink(tmp)
    try:
        if http_get is not None:
            data, headers = http_get(offer.asset.url, DOWNLOAD_TIMEOUT_S)
            content_len = headers.get("content-length") if headers else None
            expected = offer.asset.size
            if content_len:
                try:
                    expected = expected or int(content_len)
                except ValueError:
                    pass
            if expected is not None and len(data) != int(expected):
                raise AppError(
                    ErrorCode.E15,
                    f"下载大小不匹配（期望 {expected} 字节，实际 {len(data)} 字节），已拒绝安装。",
                )
            if len(data) > MAX_ASSET_BYTES:
                raise AppError(ErrorCode.E15, "安装包超过允许的大小上限，已拒绝保存。")
            tmp.write_bytes(data)
            verify_file(tmp, sha256=offer.asset.sha256, size=offer.asset.size or expected)
        else:
            expected = _stream_download(offer.asset.url, tmp, expected_size=offer.asset.size)
            verify_file(tmp, sha256=offer.asset.sha256, size=offer.asset.size or expected)
        tmp.replace(dest)
    except Exception:
        _safe_unlink(tmp)
        raise
    return dest


def _stream_download(url: str, dest: Path, *, expected_size: int | None) -> int | None:
    _assert_https_url(url)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
        method="GET",
    )
    try:
        response = urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S)
    except urllib.error.HTTPError as exc:
        raise AppError(ErrorCode.E01, _http_error_message(exc)) from exc
    except urllib.error.URLError as exc:
        raise AppError(ErrorCode.E01, "无法下载更新包，请检查网络后再试。") from exc
    except TimeoutError as exc:
        raise AppError(ErrorCode.E03, "下载更新包超时，请稍后重试。") from exc
    written = 0
    try:
        header_len = response.headers.get("Content-Length")
        if header_len and expected_size is None:
            try:
                expected_size = int(header_len)
            except ValueError:
                expected_size = None
        if expected_size is not None and expected_size > MAX_ASSET_BYTES:
            raise AppError(ErrorCode.E15, "安装包超过允许的大小上限，已拒绝保存。")
        with dest.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_ASSET_BYTES:
                    raise AppError(ErrorCode.E15, "安装包超过允许的大小上限，已拒绝保存。")
                handle.write(chunk)
    finally:
        try:
            response.close()
        except Exception:
            pass
    if expected_size is not None and written != int(expected_size):
        raise AppError(
            ErrorCode.E15,
            f"下载大小不匹配（期望 {expected_size} 字节，实际 {written} 字节），已拒绝安装。",
        )
    return expected_size


def launch_installer(path: Path) -> None:
    installer = Path(path)
    if not installer.is_file():
        raise AppError(ErrorCode.E01, "找不到已校验的安装包。")
    try:
        os.startfile(str(installer))  # type: ignore[attr-defined]
    except AttributeError:
        import subprocess
        subprocess.Popen([str(installer)], close_fds=True)
    except OSError as exc:
        raise AppError(ErrorCode.E01, f"无法启动安装程序：{exc}") from exc


def write_manifest(
    setup_path: Path,
    *,
    version: str,
    notes: str = "",
    dest_dir: Path | None = None,
) -> tuple[Path, Path]:
    setup = Path(setup_path)
    if not setup.is_file() or not looks_like_setup(setup.name):
        raise AppError(ErrorCode.E08, "请提供 Windows 安装包（setup.exe）。")
    folder = dest_dir or setup.parent
    digest = sha256_file(setup)
    size = setup.stat().st_size
    manifest = {
        "name": "RedisDesk",
        "version": version,
        "channel": "internal-preview",
        "unsigned": True,
        "notes": notes,
        "published_at": _now().isoformat(),
        "asset": {
            "name": setup.name,
            "url": f"https://github.com/{DEFAULT_REPO}/releases/download/v{version}/{setup.name}",
            "size": size,
            "sha256": digest,
        },
    }
    latest = folder / "latest.json"
    sums = folder / "SHA256SUMS.txt"
    latest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sums.write_text(f"{digest}  {setup.name}\n", encoding="utf-8")
    return latest, sums


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
