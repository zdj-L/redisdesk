"""RotatingFileHandler + 脱敏过滤器。日志只允许命令名与参数个数，不写参数值。"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from redisdesk.core.limits import LOG_BACKUP_COUNT, LOG_ROTATE_MB
from redisdesk.core.redact import redact_argv
from redisdesk.infra.paths import log_path


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        # 约定：如需记录命令，使用 record.argv / record.command
        argv = getattr(record, "argv", None)
        if argv:
            name = str(argv[0]).upper() if argv else ""
            if name == "AUTH":
                record.msg = f"{record.getMessage()} command=AUTH args=redacted"
            else:
                record.msg = f"{record.getMessage()} command={name} argc={len(argv)}"
            record.args = ()
            if hasattr(record, "argv"):
                record.argv = redact_argv(list(argv))
        return True


def setup_logging(level: str = "INFO", path: Path | None = None) -> logging.Logger:
    logger = logging.getLogger("redisdesk")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    target = path or log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        target,
        maxBytes=LOG_ROTATE_MB * 1024 * 1024,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactFilter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger
