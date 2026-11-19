"""把阻塞型 I/O 放到后台线程，完成后再用信号回到界面线程。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal


class _JobSignals(QObject):
    finished = Signal(object)
    failed = Signal(object)


class FunctionWorker(QThread):
    """在独立线程运行 fn()，结果或异常通过信号回到调用线程（通常是 GUI 线程）。"""

    def __init__(self, fn: Callable[[], Any], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._fn = fn
        self.signals = _JobSignals()

    def run(self) -> None:
        try:
            result = self._fn()
        except BaseException as exc:  # noqa: BLE001
            self.signals.failed.emit(exc)
            return
        self.signals.finished.emit(result)
