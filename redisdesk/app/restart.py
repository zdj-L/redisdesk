"""Force a clean process restart after a data-directory change."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

_pending_restart = False


def reset_restart_state() -> None:
    """Tests only."""
    global _pending_restart
    _pending_restart = False


def schedule_restart_after_exit() -> None:
    global _pending_restart
    _pending_restart = True


def restart_is_pending() -> bool:
    return _pending_restart


def consume_restart_request() -> bool:
    global _pending_restart
    flag = _pending_restart
    _pending_restart = False
    return flag


def build_restart_command(argv: list[str] | None = None) -> list[str]:
    """Rebuild the command used to start this process.

    Packaged builds relaunch the exe. A development run relaunches
    ``python -m redisdesk.app.main``. Pytest or unknown argv is not forwarded.
    """
    raw = list(sys.argv if argv is None else argv)
    exe = sys.executable
    if bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS"):
        rest = raw[1:] if raw else []
        return [exe, *rest]
    if len(raw) >= 3 and raw[1] == "-m" and str(raw[2]).startswith("redisdesk"):
        return [exe, *raw[1:]]
    if raw and Path(raw[0]).name in {"main.py", "portable_entry.py"}:
        return [exe, *raw]
    if len(raw) >= 2 and Path(raw[0]).name.lower().startswith("python") and Path(str(raw[1])).name in {"main.py", "portable_entry.py"}:
        return [exe, *raw[1:]]
    return [exe, "-m", "redisdesk.app.main"]


def launch_replacement_process(argv: list[str] | None = None) -> bool:
    command = build_restart_command(argv)
    try:
        kwargs: dict = {"close_fds": False}
        if os.name == "nt":
            flags = int(getattr(subprocess, "DETACHED_PROCESS", 0)) | int(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
            kwargs["creationflags"] = flags
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(command, **kwargs)
        return True
    except OSError:
        return False


def notify_manual_reopen() -> None:
    message = "无法自动重新打开 RedisDesk，请手动再打开一次。"
    try:
        if os.name == "nt":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "RedisDesk", 0x10)
            return
    except Exception:
        pass
    print(message, file=sys.stderr)


def clean_shutdown() -> None:
    """Flush open Redis connections and the local store. Do not change the locked data dir."""
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        return
    app = QApplication.instance()
    if app is None:
        return
    window = None
    for widget in app.topLevelWidgets():
        if widget.__class__.__name__ == "MainWindow":
            window = widget
            break
    if window is None:
        return
    page = getattr(window, "connections_page", None)
    if page is not None:
        try:
            page.close_workspaces()
        except Exception:
            pass
        resources = [workspace.io for workspace in getattr(page, "workspaces", {}).values()]
        deadline = time.time() + 3
        while time.time() < deadline:
            pending = any(
                getattr(getattr(io, "cleanup_future", None), "done", lambda: True)() is False
                for io in resources
            )
            pending_test = any(job.isRunning() for job in getattr(page, "_test_jobs", {}).values())
            if not pending and not pending_test:
                break
            app.processEvents()
            time.sleep(0.05)
    store = getattr(getattr(window, "connections", None), "store", None)
    if store is not None:
        try:
            store.close()
        except Exception:
            pass


def _prompt_immediate_restart(message: str, parent=None) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout

    dialog = QDialog(parent)
    dialog.setObjectName("forcedRestartDialog")
    dialog.setWindowTitle("必须重新启动")
    dialog.setModal(True)
    dialog.setWindowFlags(
        Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint
    )
    root = QVBoxLayout(dialog)
    label = QLabel(message)
    label.setWordWrap(True)
    label.setObjectName("forcedRestartMessage")
    root.addWidget(label)
    button = QPushButton("立即重启")
    button.setObjectName("forcedRestartButton")
    button.setDefault(True)
    button.clicked.connect(dialog.accept)
    root.addWidget(button, alignment=Qt.AlignmentFlag.AlignRight)

    def _still_accept(event=None) -> None:
        dialog.accept()
        if event is not None:
            event.accept()

    dialog.reject = lambda *args: dialog.accept()  # type: ignore[method-assign]
    dialog.closeEvent = lambda event: _still_accept(event)  # type: ignore[method-assign]
    dialog.exec()


def request_forced_restart(*, message: str, parent=None) -> None:
    """Show a non-cancellable prompt, shut down I/O, then quit so main() can relaunch."""
    try:
        _prompt_immediate_restart(message, parent)
    except Exception:
        pass
    clean_shutdown()
    schedule_restart_after_exit()
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.quit()
    except Exception:
        pass
