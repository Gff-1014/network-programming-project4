"""在后台线程里跑 asyncio 事件循环，让 Tkinter 主线程可以安全地发起网络动作。

Tk 的 mainloop 会独占主线程，所以网络协程统一交给这里的循环执行，
结果通过 `concurrent.futures.Future` 回传；界面侧只用 `after()` 轮询事件队列。
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from typing import Any, Coroutine


class AsyncBridge:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="async-bridge", daemon=True)
        self._thread.start()
        self._ready.wait(3)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.call_soon(self._ready.set)
        self.loop.run_forever()

    def submit(self, coro: Coroutine[Any, Any, Any]) -> Future:
        """把协程丢到后台循环执行，返回可等待的 Future。"""
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
