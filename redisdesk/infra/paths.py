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
