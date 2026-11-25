"""本地数据目录约定。

解析顺序（从高到低）：
1. 环境变量 REDISDESK_APPDATA（测试与开发覆盖，进程内最高优先）
2. 应用根目录旁的指针文件 redisdesk-datadir.txt（打开存储之前即可读取）
3. 打包预览版登记的默认目录（%LOCALAPPDATA%\\RedisDesk-Internal\\1.0.0）
4. 系统默认 %LOCALAPPDATA%\\RedisDesk（若无 LOCALAPPDATA 则为 ~/.redisdesk）

运行中途若更改指针，已锁定的目录保持不变，必须完全退出后重新打开才会改用新路径。
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from redisdesk.core.errors import AppError, ErrorCode
from redisdesk.core.limits import APPDATA_DIRNAME, DB_FILENAME, LOG_FILENAME, UI_SETTINGS_FILENAME

POINTER_FILENAME = "redisdesk-datadir.txt"
KIND_SYSTEM = "system"
KIND_APP_ROOT = "app_root"
KIND_CUSTOM = "custom"

KNOWN_HOSTS_FILENAME = "known_hosts_redisdesk"
_SYSTEM_KNOWN_HOSTS_ENV = "REDISDESK_SYSTEM_KNOWN_HOSTS"
_DATA_FILES = (DB_FILENAME, UI_SETTINGS_FILENAME, KNOWN_HOSTS_FILENAME)
_DATA_DIRS = ("logs", "updates")
_SQLITE_SUFFIXES = ("", "-wal", "-shm", "-journal")

_app_root_override: Path | None = None
_packaged_default: Path | None = None
_locked_dir: Path | None = None


def set_app_root_override(path: Path | str | None) -> None:
    """测试用：把「应用根目录」指到临时目录，避免改到真实仓库。"""
    global _app_root_override
    _app_root_override = Path(path) if path else None


def set_packaged_default_data_dir(path: Path | str | None) -> None:
    """打包入口调用：登记预览版默认数据目录，不设置 REDISDESK_APPDATA。"""
    global _packaged_default
    _packaged_default = Path(path) if path else None


def lock_app_data_dir(path: Path | str | None = None) -> Path:
    """启动时锁定本次进程使用的数据目录，避免改指针后误写到新目录。"""
    global _locked_dir
    _locked_dir = Path(path).expanduser() if path is not None else resolve_app_data_dir()
    return _locked_dir


def unlock_app_data_dir() -> None:
    """测试用：解除进程内锁定。"""
    global _locked_dir
    _locked_dir = None


def app_root() -> Path:
    if _app_root_override is not None:
        return Path(_app_root_override).expanduser().resolve()
    if bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS"):
        return Path(sys.executable).resolve().parent
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "redisdesk").is_dir():
            return parent
    return Path.cwd().resolve()


def pointer_path() -> Path:
    return app_root() / POINTER_FILENAME


def default_system_data_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / APPDATA_DIRNAME
    return Path.home() / f".{APPDATA_DIRNAME.lower()}"


def edition_default_data_dir() -> Path:
    if _packaged_default is not None:
        return Path(_packaged_default)
    return default_system_data_dir()


def env_override_active() -> bool:
    value = os.environ.get("REDISDESK_APPDATA")
    return bool(value and value.strip())


def is_same_data_dir(left: Path | str | None, right: Path | str | None) -> bool:
    """Compare two data directory paths after expand/resolve/normalization."""
    if left is None or right is None:
        return False
    try:
        return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()
    except OSError:
        return os.path.normcase(os.path.abspath(os.path.expanduser(str(left)))) == os.path.normcase(
            os.path.abspath(os.path.expanduser(str(right)))
        )


def read_pointer() -> Path | None:
    path = pointer_path()
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        return Path(item).expanduser()
    return None


def resolve_app_data_dir() -> Path:
    if env_override_active():
        return Path(os.environ["REDISDESK_APPDATA"].strip()).expanduser()
    pointer = read_pointer()
    if pointer is not None:
        return pointer
    return edition_default_data_dir()


def app_data_dir() -> Path:
    if _locked_dir is not None:
        return _locked_dir
    return resolve_app_data_dir()


def db_path() -> Path:
    return app_data_dir() / DB_FILENAME


def log_dir() -> Path:
    return app_data_dir() / "logs"


def log_path() -> Path:
    return log_dir() / LOG_FILENAME


def ui_settings_path() -> Path:
    return app_data_dir() / UI_SETTINGS_FILENAME


def known_hosts_path() -> Path:
    """App-owned known_hosts; never the user's ~/.ssh/known_hosts."""
    return app_data_dir() / KNOWN_HOSTS_FILENAME


def system_known_hosts_path() -> Path | None:
    """Optional read-only OpenSSH known_hosts. Tests may disable or redirect this.

    An empty / off value means do not read any system file. The default is
    ~/.ssh/known_hosts, and this path is never opened for writing by RedisDesk.
    """
    override = os.environ.get(_SYSTEM_KNOWN_HOSTS_ENV)
    if override is not None:
        text = override.strip()
        if text == "" or text.lower() in {"0", "off", "none", "false"}:
            return None
        return Path(text)
    return Path.home() / ".ssh" / "known_hosts"


def target_data_dir(kind: str, custom: Path | str | None = None) -> Path:
    name = (kind or "").strip().lower()
    if name == KIND_SYSTEM:
        return edition_default_data_dir()
    if name == KIND_APP_ROOT:
        return app_root()
    if name == KIND_CUSTOM:
        if custom is None or not str(custom).strip():
            raise AppError(ErrorCode.E08, "请先选择自定义数据目录。")
        return Path(str(custom).strip()).expanduser()
    raise AppError(ErrorCode.E08, "未知的数据目录选项。")


def is_shared_or_insecure_location(path: Path | str) -> bool:
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        return False
    public = Path(os.environ.get("PUBLIC") or r"C:\Users\Public")
    try:
        public = public.resolve()
        if resolved == public or public in resolved.parents:
            return True
    except OSError:
        pass
    return resolved.parent == resolved


def validate_data_dir_candidate(path: Path | str) -> Path:
    raw = str(path).strip() if path is not None else ""
    if not raw:
        raise AppError(ErrorCode.E08, "数据目录不能为空。")
    candidate = Path(raw).expanduser()
    try:
        candidate = candidate.resolve()
    except OSError as exc:
        raise AppError(ErrorCode.E08, f"数据目录无效：{candidate}", hint=str(exc)) from exc
    from redisdesk.infra.integrity import check_data_dir

    if not check_data_dir(candidate):
        raise AppError(ErrorCode.E08, f"数据目录不可写：{candidate}。未更改当前数据目录。")
    return candidate


def write_pointer(target: Path | str) -> Path:
    destination = validate_data_dir_candidate(target)
    pointer = pointer_path()
    try:
        pointer.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            "# RedisDesk data directory pointer. Do not store secrets in this file.\n"
            f"{destination}\n"
        )
        tmp = pointer.with_suffix(pointer.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8", newline="\n")
        tmp.replace(pointer)
    except OSError as exc:
        raise AppError(ErrorCode.E08, f"无法写入数据目录指针：{pointer}", hint=str(exc)) from exc
    return destination


def clear_pointer() -> None:
    pointer = pointer_path()
    try:
        pointer.unlink(missing_ok=True)
        leftover = pointer.with_suffix(pointer.suffix + ".tmp")
        leftover.unlink(missing_ok=True)
    except OSError as exc:
        raise AppError(ErrorCode.E08, f"无法移除数据目录指针：{pointer}", hint=str(exc)) from exc


def data_dir_has_payload(path: Path | str) -> bool:
    folder = Path(path)
    if (folder / DB_FILENAME).is_file():
        return True
    if (folder / UI_SETTINGS_FILENAME).is_file():
        return True
    if (folder / KNOWN_HOSTS_FILENAME).is_file():
        return True
    for name in _DATA_DIRS:
        child = folder / name
        if child.is_dir() and any(child.iterdir()):
            return True
    return False


def migrate_data_dir(source: Path, destination: Path) -> list[str]:
    src = Path(source).expanduser().resolve()
    dest = Path(destination).expanduser().resolve()
    if src == dest:
        return []
    if not src.exists():
        return []
    dest_db = dest / DB_FILENAME
    src_db = src / DB_FILENAME
    if dest_db.is_file() and src_db.is_file() and dest_db.resolve() != src_db.resolve():
        raise AppError(
            ErrorCode.E08,
            f"目标目录已有数据库 {dest_db}，为避免覆盖未执行迁移。若要改用该目录中的现有数据，请确认后仅切换路径。",
        )
    copied: list[str] = []
    dest.mkdir(parents=True, exist_ok=True)
    for filename in _DATA_FILES:
        if filename == DB_FILENAME:
            for suffix in _SQLITE_SUFFIXES:
                item = Path(str(src / DB_FILENAME) + suffix) if suffix else src / DB_FILENAME
                if not item.is_file():
                    continue
                target = dest / item.name
                shutil.copy2(item, target)
                copied.append(item.name)
            continue
        item = src / filename
        if item.is_file():
            shutil.copy2(item, dest / filename)
            copied.append(filename)
    for name in _DATA_DIRS:
        origin = src / name
        if not origin.is_dir():
            continue
        shutil.copytree(origin, dest / name, dirs_exist_ok=True)
        copied.append(name + "/")
    return copied


@dataclass
class DataDirChangeResult:
    kind: str
    current: Path
    next_path: Path
    copied: list[str] = field(default_factory=list)
    pointer_written: bool = False
    pointer_cleared: bool = False
    needs_restart: bool = True
    env_override: bool = False
    switched_to_existing: bool = False
    insecure: bool = False


def apply_data_dir_change(
    kind: str,
    custom: Path | str | None = None,
    *,
    allow_insecure: bool = False,
    switch_to_existing: bool = False,
    source: Path | str | None = None,
) -> DataDirChangeResult:
    """校验并迁移，然后写入或清除指针。不改动当前进程已锁定的目录。"""
    current = app_data_dir()
    requested = target_data_dir(kind, custom)
    if is_same_data_dir(current, requested):
        return DataDirChangeResult(
            kind=kind,
            current=Path(current),
            next_path=Path(current),
            needs_restart=False,
        )
    insecure = is_shared_or_insecure_location(requested)
    if insecure and not allow_insecure:
        raise AppError(
            ErrorCode.E08,
            f"目标位置可能被多人共享或不适宜存放凭证：{requested}。请另选目录，或确认风险后再继续。",
        )
    destination = validate_data_dir_candidate(requested)
    origin = Path(source).expanduser() if source is not None else current
    try:
        origin = origin.resolve()
    except OSError:
        origin = Path(origin)
    copied: list[str] = []
    switched = False
    dest_has = data_dir_has_payload(destination)
    src_has = data_dir_has_payload(origin)
    if dest_has and src_has and origin.resolve() != destination.resolve():
        if not switch_to_existing:
            raise AppError(
                ErrorCode.E08,
                f"目标目录已有 RedisDesk 数据：{destination}。未覆盖、未更改指针。若要改用该目录中的现有数据，请确认后重试。",
            )
        switched = True
    elif src_has and origin.resolve() != destination.resolve():
        copied = migrate_data_dir(origin, destination)
        if not data_dir_has_payload(destination):
            raise AppError(ErrorCode.E08, f"迁移后未能在目标目录看到数据：{destination}。未更改指针。")
    if kind == KIND_SYSTEM and destination.resolve() == edition_default_data_dir().resolve():
        clear_pointer()
        pointer_written, pointer_cleared = False, True
    else:
        write_pointer(destination)
        pointer_written, pointer_cleared = True, False
    try:
        from redisdesk.infra.integrity import tighten_directory_acl

        tighten_directory_acl(destination)
    except Exception:
        pass
    return DataDirChangeResult(
        kind=kind,
        current=Path(current),
        next_path=destination,
        copied=copied,
        pointer_written=pointer_written,
        pointer_cleared=pointer_cleared,
        needs_restart=True,
        env_override=env_override_active(),
        switched_to_existing=switched,
        insecure=insecure,
    )


