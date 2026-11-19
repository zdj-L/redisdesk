"""本地数据目录约定：只写 %LOCALAPPDATA%\\RedisDesk。"""

from __future__ import annotations

import os
from pathlib import Path

from redisdesk.core.limits import APPDATA_DIRNAME, DB_FILENAME, LOG_FILENAME, UI_SETTINGS_FILENAME


def app_data_dir() -> Path:
    base = os.environ.get("REDISDESK_APPDATA")
    if base:
        return Path(base)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / APPDATA_DIRNAME
    return Path.home() / f".{APPDATA_DIRNAME.lower()}"


def db_path() -> Path:
    return app_data_dir() / DB_FILENAME


def log_dir() -> Path:
    return app_data_dir() / "logs"


def log_path() -> Path:
    return log_dir() / LOG_FILENAME


def ui_settings_path() -> Path:
    return app_data_dir() / UI_SETTINGS_FILENAME


KNOWN_HOSTS_FILENAME = "known_hosts_redisdesk"
_SYSTEM_KNOWN_HOSTS_ENV = "REDISDESK_SYSTEM_KNOWN_HOSTS"


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

