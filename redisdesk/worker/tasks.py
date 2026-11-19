"""ScanTask / DiagTask。P0 提供可独立运行的扫描任务封装。"""

from __future__ import annotations

from typing import Callable, Optional

from redisdesk.adapters.scan import CancellationToken, EmitFn, ScanEngine
from redisdesk.core.model import KeyMeta, ScanSession


class ScanTask:
    def __init__(self, engine: ScanEngine, *, emit: EmitFn | None = None) -> None:
        self.engine = engine
        self.emit = emit
        self.token = engine.token

    def run(self) -> ScanSession:
        self.engine.fill_page(emit=self.emit)
        return self.engine.session

    def cancel(self) -> None:
        self.token.cancel()
        self.engine.cancel()


class DiagTask:
    def __init__(self, fn: Callable[[], object], on_done: Optional[Callable[[object], None]] = None) -> None:
        self.fn = fn
        self.on_done = on_done

    def run(self) -> object:
        result = self.fn()
        if self.on_done:
            self.on_done(result)
        return result
