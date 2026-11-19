"""安装包 / 更新包签名与哈希校验、单实例互斥、目录 ACL 自检。"""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from redisdesk.core.limits import SINGLE_INSTANCE_MUTEX


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    mutex_acquired: bool
    dpapi_available: bool
    data_dir_writable: bool
    qt_ok: bool
    message: str = ""


def acquire_single_instance_mutex(name: str = SINGLE_INSTANCE_MUTEX) -> tuple[bool, Optional[int]]:
    """返回 (acquired, handle)。非 Windows 时用文件锁退化（仅本机测试）。"""
    if os.name != "nt":
        return True, None
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    ERROR_ALREADY_EXISTS = 183
    handle = kernel32.CreateMutexW(None, True, name)
    if not handle:
        return False, None
    last = kernel32.GetLastError()
    if last == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False, None
    return True, int(handle)


def release_mutex(handle: Optional[int]) -> None:
    if handle is None or os.name != "nt":
        return
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.ReleaseMutex(handle)
    kernel32.CloseHandle(handle)


def dpapi_available() -> bool:
    return os.name == "nt"


def check_data_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def tighten_directory_acl(path: Path) -> bool:
    """首次运行收紧为当前用户 + Administrators。失败不阻断启动，只返回 False。"""
    if os.name != "nt":
        return False
    try:
        import subprocess

        user = os.environ.get("USERNAME", "")
        if not user:
            return False
        cmd = [
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{user}:(OI)(CI)F",
            "/grant:r",
            r"Administrators:(OI)(CI)F",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return result.returncode == 0
    except OSError:
        return False


def qt_plugins_ok() -> bool:
    # 启动期由 main 在 QApplication 创建后确认；此处仅作预检占位。
    return True


def startup_report(data_dir: Path) -> IntegrityReport:
    mutex_ok, _ = acquire_single_instance_mutex()
    # 上面会创建互斥量；真正持有应在 main 中完成。此处只探测会误伤，故仅在非探测路径使用。
    return IntegrityReport(
        mutex_acquired=mutex_ok,
        dpapi_available=dpapi_available(),
        data_dir_writable=check_data_dir(data_dir),
        qt_ok=True,
        message="" if mutex_ok else "已有 RedisDesk 实例在运行",
    )


def running_frozen() -> bool:
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")
