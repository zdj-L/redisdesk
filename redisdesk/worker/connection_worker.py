"""每连接一条后台工作线程。P0 提供可测试的请求队列骨架；UI 接入时再挂 QThread。"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class WorkerRequest:
    name: str
    fn: Callable[[], Any]
    on_done: Optional[Callable[[Any], None]] = None
    on_error: Optional[Callable[[BaseException], None]] = None


class ConnectionWorker:
    def __init__(self) -> None:
        self._queue: queue.Queue[WorkerRequest | None] = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._closed = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="redisdesk-conn-worker", daemon=True)
        self._thread.start()

    def submit(self, request: WorkerRequest) -> None:
        if self._closed:
            raise RuntimeError("worker closed")
        self.start()
        self._queue.put(request)

    def close(self, timeout: float = 2.0) -> None:
        self._closed = True
        self._queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            try:
                result = item.fn()
            except BaseException as exc:  # noqa: BLE001
                if item.on_error:
                    item.on_error(exc)
            else:
                if item.on_done:
                    item.on_done(result)
