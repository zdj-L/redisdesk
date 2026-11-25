"""入口：单实例检查、日志、Store，并直接打开主窗口。"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from redisdesk import __version__
from redisdesk.infra.integrity import acquire_single_instance_mutex, check_data_dir, release_mutex, tighten_directory_acl
from redisdesk.infra.log import setup_logging
from redisdesk.infra.paths import app_data_dir, lock_app_data_dir, log_dir


def _crash_log(exc: BaseException) -> None:
    try:
        log_dir().mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = log_dir() / f"crash-{ts}.log"
        path.write_text(
            f"RedisDesk {__version__}\n{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
            encoding="utf-8",
        )
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    data_dir = lock_app_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    tighten_directory_acl(data_dir)
    if not check_data_dir(data_dir):
        print("数据目录不可写：", data_dir, file=sys.stderr)
        return 2

    acquired, mutex = acquire_single_instance_mutex()
    if not acquired:
        print("RedisDesk 已在运行（单实例互斥）。", file=sys.stderr)
        return 1

    setup_logging()
    try:
        return _run_ui(data_dir)
    except Exception as exc:  # noqa: BLE001
        _crash_log(exc)
        raise
    finally:
        release_mutex(mutex)
        from redisdesk.app.restart import consume_restart_request, launch_replacement_process, notify_manual_reopen

        if consume_restart_request():
            if not launch_replacement_process():
                notify_manual_reopen()


def _run_ui(data_dir: Path, *, startup_hook=None) -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    from redisdesk.app.services.audit_service import AuditService
    from redisdesk.app.services.auth_service import AuthService
    from redisdesk.app.services.connection_service import ConnectionService
    from redisdesk.app.services.console_service import ConsoleService
    from redisdesk.app.ui.mainwindow import MainWindow
    from redisdesk.app.ui.theme import apply_theme, app_font, UiSettings
    from redisdesk.infra.audit import AuditLog
    from redisdesk.infra.session import SessionManager
    from redisdesk.infra.store import Store, StoreGrantResolver

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("RedisDesk")
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    app.setFont(app_font())
    settings = UiSettings()
    apply_theme(app, settings.get("theme", "light"))

    store = Store()
    audit = AuditLog(store)
    chain = audit.verify_on_start()
    sessions = SessionManager()
    grants = StoreGrantResolver(store)
    auth = AuthService(store, audit, sessions)
    connections = ConnectionService(store, audit, grants)
    console = ConsoleService(store, audit, grants)
    audits = AuditService(audit, grants)

    window = MainWindow(
        auth=auth,
        connections=connections,
        console=console,
        audits=audits,
        sessions=sessions,
    )
    session = auth.ensure_local_session()
    window.bind_session(session)
    if chain.ok:
        window.set_audit_ok(True, last_seq=getattr(chain, "last_seq", None))
    else:
        window.show_audit_banner(
            f"审计链完整性异常（序号={chain.broken_at_seq}），已进入只读应急模式。"
        )
        window.set_audit_ok(False)
    window.place_normal_on_work_area()
    window.showNormal()
    if startup_hook is not None:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(300, lambda: startup_hook(window, app, session, connections, console))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
