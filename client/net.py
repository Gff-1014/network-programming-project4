"""客户端网络层：长度前缀分帧 + 单读者多路复用 + 请求/响应 + 等待推送。

机器人脚本、自动化测试与后面的 Tkinter 客户端都复用本模块。

设计要点（都是踩过坑之后定下来的）：

1. **单读者**：整条连接只有一个协程读流，其他代码不直接碰 `StreamReader`，
   避免"两个协程同时 readexactly"的运行时错误。
2. **按 request_id 分流**：带 `request_id` 且有人在等的回包直接交给等待者，
   其余一律进收件箱。这样"请求响应"和"服务端推送"不会互相抢包。
3. **等待循环里不空转**：取不到消息就 await 收件箱（带超时），
   绝不做"取出→不匹配→放回"的死循环，避免把事件循环饿死。
"""

from __future__ import annotations

import asyncio
import itertools
import time

from server.framing import encode_frame, read_frame


class ServerError(Exception):
    """服务端返回的 error 帧。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class Connection:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 max_frame_bytes: int = 65536) -> None:
        self.reader = reader
        self.writer = writer
        self.max_frame_bytes = max_frame_bytes
        self._ids = itertools.count(1)
        self._buffer: list[dict] = []                         # 收到但暂时用不上的推送
        self._inbox: asyncio.Queue[dict] = asyncio.Queue()    # 未被请求认领的推送
        self._waiters: dict[str, asyncio.Future] = {}         # request_id -> 等待回包的 future
        self._eof = False
        self.closed = False
        self._pump_task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        try:
            while True:
                frame = await read_frame(self.reader, self.max_frame_bytes)
                request_id = frame.get("request_id")
                waiter = self._waiters.pop(request_id, None) if request_id else None
                if waiter is not None and not waiter.done():
                    waiter.set_result(frame)
                else:
                    self._inbox.put_nowait(frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._mark_closed()

    def _mark_closed(self) -> None:
        self._eof = True
        for waiter in list(self._waiters.values()):
            if not waiter.done():
                waiter.set_exception(ConnectionError("连接已关闭"))
        self._waiters.clear()
        self._inbox.put_nowait({"type": "_closed", "data": {}})

    # ------------------------------------------------------------- 连接 ----

    @classmethod
    async def connect(cls, host: str = "127.0.0.1", port: int = 9011,
                      max_frame_bytes: int = 65536) -> "Connection":
        reader, writer = await asyncio.open_connection(host, port)
        return cls(reader, writer, max_frame_bytes)

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._pump_task.cancel()
        self._mark_closed()
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass

    # ------------------------------------------------------------- 收发 ----

    async def send(self, msg_type: str, data: dict | None = None,
                   request_id: str | None = None) -> str:
        rid = request_id or f"c{next(self._ids)}"
        payload = {"type": msg_type, "data": data or {}, "request_id": rid}
        self.writer.write(encode_frame(payload))
        await self.writer.drain()
        return rid

    async def recv(self, timeout: float | None = None) -> dict:
        """收一条推送；优先返回之前缓存的。"""
        if self._buffer:
            return self._buffer.pop(0)
        return await self._take_from_inbox(timeout)

    async def _take_from_inbox(self, timeout: float | None = None) -> dict:
        """只从收件箱取新推送，不碰 `_buffer`。

        等待循环里必须用它：如果调用 `recv()`，会把上一轮刚放进 `_buffer`
        的帧又取出来，形成"取出→放回"的空转。
        """
        if self._eof and self._inbox.empty():
            raise ConnectionError("连接已关闭")
        getter = self._inbox.get()
        frame = await (asyncio.wait_for(getter, timeout) if timeout else getter)
        if frame.get("type") == "_closed":
            raise ConnectionError("连接已关闭")
        return frame

    async def request(self, msg_type: str, data: dict | None = None,
                      timeout: float = 5.0) -> dict:
        """发一条请求并等待同一 request_id 的响应（不影响其他推送）。"""
        rid = f"c{next(self._ids)}"
        waiter: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters[rid] = waiter
        await self.send(msg_type, data, request_id=rid)
        try:
            frame = await asyncio.wait_for(waiter, timeout)
        except asyncio.TimeoutError:
            self._waiters.pop(rid, None)
            raise asyncio.TimeoutError(f"{msg_type} 等待响应超时") from None
        return self._as_response(frame)

    async def wait_for(self, msg_type: str, timeout: float = 3.0,
                       match: dict | None = None) -> dict:
        """等待某类推送；期间的其他消息缓存起来。"""
        deadline = time.monotonic() + timeout
        while True:
            for index, frame in enumerate(self._buffer):
                if frame.get("type") == msg_type and _match(frame, match):
                    return self._buffer.pop(index)
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise asyncio.TimeoutError(f"等待 {msg_type} 超时")
            frame = await self._take_from_inbox(remain)
            if frame.get("type") == msg_type and _match(frame, match):
                return frame
            self._buffer.append(frame)

    @staticmethod
    def _as_response(frame: dict) -> dict:
        if frame.get("type") == "error":
            raise ServerError(frame["data"]["code"], frame["data"]["message"])
        return frame

    # ------------------------------------------------------------- 语义封装 ----

    async def login(self, nickname: str, timeout: float = 5.0) -> dict:
        return await self.request("login", {"nickname": nickname}, timeout)

    async def join_room(self, room: str, timeout: float = 5.0) -> dict:
        return await self.request("join_room", {"room": room}, timeout)

    async def leave_room(self, timeout: float = 5.0) -> dict:
        return await self.request("leave_room", {}, timeout)

    async def list_rooms(self, timeout: float = 5.0) -> dict:
        return await self.request("list_rooms", {}, timeout)

    async def room_msg(self, text: str, timeout: float = 5.0) -> dict:
        return await self.request("room_msg", {"text": text}, timeout)

    async def private_msg(self, to: str, text: str, timeout: float = 5.0) -> dict:
        return await self.request("private_msg", {"to": to, "text": text}, timeout)

    async def ping(self, timeout: float = 5.0) -> dict:
        return await self.request("ping", {}, timeout)


def _match(frame: dict, match: dict | None) -> bool:
    if not match:
        return True
    data = frame.get("data") or {}
    return all(data.get(key) == value for key, value in match.items())
