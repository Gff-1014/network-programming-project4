"""会话：一条 TCP 连接的读写、发送队列、握手保护与分发。

要点：

* 每个连接一个发送队列 + 一个写协程，避免"边读边写"互相阻塞；
* 读循环只有一条退出路径（`finally` 统一清理），**任何异常都不重试**，
  避免出现"连接已死却原地空转"把事件循环拖死的情况；
* 时间统一用服务端单调时钟。
"""

from __future__ import annotations

import asyncio
import logging

from . import protocol
from .framing import FrameError, encode_frame, read_frame
from .protocol import ErrCode, error, make

logger = logging.getLogger(__name__)


class Session:
    def __init__(self, conn_id: str, reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter, hub, cfg) -> None:
        self.conn_id = conn_id
        self.reader = reader
        self.writer = writer
        self.hub = hub
        self.cfg = cfg
        self.nickname: str | None = None
        self.room: str | None = None
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=cfg.send_queue_max)
        self.last_seen_ms = protocol.now_ms()
        self.bad_frames = 0
        self.unknown_types = 0
        self.dropped = 0
        self.closed = False
        self._writer_task: asyncio.Task | None = None

    # ------------------------------------------------------------- 生命周期 ----

    async def run(self) -> None:
        self._writer_task = asyncio.create_task(self._writer_loop())
        self.hub.add_session(self)
        try:
            first = await self._handshake()
            if first is None:
                return
            await self._dispatch(first)
            while not self.closed:
                try:
                    payload = await read_frame(self.reader, self.cfg.max_frame_bytes)
                except FrameError as exc:
                    # 只有确认字节流仍然对齐（消息体已读满）才可以继续；否则必须断开
                    if not self._reject_bad_frame(exc):
                        break
                    continue
                self.last_seen_ms = protocol.now_ms()
                await self._dispatch(payload)
        except FrameError as exc:
            # 握手阶段就收到非法帧
            self._reject_bad_frame(exc)
        except asyncio.TimeoutError:
            self.send(error(ErrCode.HANDSHAKE_TIMEOUT, "未在限时内完成登录"))
            logger.info("[%s] 握手超时", self.conn_id)
        except asyncio.IncompleteReadError:
            pass  # 对端正常或异常关闭
        except (ConnectionError, OSError) as exc:
            logger.info("[%s] 连接中断: %s", self.conn_id, exc)
        except Exception:  # pragma: no cover - 兜底，绝不让异常逃逸出去
            logger.exception("[%s] 会话异常", self.conn_id)
        finally:
            await self.close("session end")

    def _reject_bad_frame(self, exc: FrameError) -> bool:
        """记录非法帧并回错误；返回是否还能继续读（字节流是否仍然对齐）。"""
        self.bad_frames += 1
        self.send(error(exc.code, exc.message))
        logger.warning("[%s] 非法帧(%d/%d): %s", self.conn_id, self.bad_frames,
                       self.cfg.bad_frame_limit, exc)
        if exc.fatal:
            return False
        if self.bad_frames >= self.cfg.bad_frame_limit:
            self.send(error(ErrCode.TOO_MANY_BAD_FRAMES, "非法帧过多"))
            return False
        return True

    async def _handshake(self) -> dict | None:
        """首个消息必须在限时内到达且是 login；返回这条消息交给分发器处理。"""
        timeout = self.cfg.handshake_timeout_ms / 1000
        payload = await asyncio.wait_for(
            read_frame(self.reader, self.cfg.max_frame_bytes), timeout
        )
        self.last_seen_ms = protocol.now_ms()
        if payload.get("type") != "login":
            self.send(error(ErrCode.NOT_LOGGED_IN, "首个消息必须是 login", payload.get("request_id")))
            await self.close("handshake rejected")
            return None
        return payload

    async def _dispatch(self, payload: dict) -> None:
        msg_type = payload.get("type")
        request_id = payload.get("request_id")
        if not isinstance(msg_type, str):
            self.bad_frames += 1
            self.send(error(ErrCode.BAD_MESSAGE, "缺少 type 字段", request_id))
            return

        handler = protocol.get_handler(msg_type)
        if handler is None:
            self.unknown_types += 1
            self.send(
                error(
                    ErrCode.UNKNOWN_TYPE,
                    f"未知消息类型 {msg_type}",
                    request_id,
                    supported=protocol.SUPPORTED_TYPES,
                )
            )
            if self.unknown_types >= self.cfg.bad_frame_limit:
                await self.close("too many unknown types")
            return

        data = payload.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        await handler(self, data, request_id)

    async def _writer_loop(self) -> None:
        try:
            while True:
                payload = await self.queue.get()
                try:
                    self.writer.write(encode_frame(payload))
                    await self.writer.drain()
                finally:
                    self.queue.task_done()
        except (asyncio.CancelledError, ConnectionError, OSError):
            pass

    async def close(self, reason: str = "") -> None:
        """统一的退出路径：摘除状态、发完残余消息、关连接、取消写协程。可重复调用。"""
        if self.closed:
            return
        self.closed = True
        self.hub.logout(self)
        # 把已入队的消息尽量发完（例如握手被拒/超时的 error 提示），最多等 0.5 秒
        if self._writer_task is not None:
            if not self._writer_task.done():
                try:
                    await asyncio.wait_for(self.queue.join(), timeout=0.5)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
            self._writer_task.cancel()
        try:
            self.writer.close()
        except Exception:  # pragma: no cover
            pass
        logger.info(
            "[%s] 关闭(%s) 昵称=%s 丢弃=%d 非法帧=%d",
            self.conn_id, reason or "-", self.nickname, self.dropped, self.bad_frames,
        )

    # ------------------------------------------------------------- 发送 ----

    def send(self, payload: dict, droppable: bool = False) -> bool:
        """入队；队列满时按类型处理：可丢消息直接丢弃并计数。"""
        if self.closed:
            return False
        try:
            self.queue.put_nowait(payload)
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            self.hub.dropped_total += 1
            if not droppable:
                logger.warning("[%s] 发送队列已满，丢弃非广播消息 %s", self.conn_id, payload.get("type"))
            return False

    def room_snapshot(self) -> dict:
        room = self.hub.room_of(self)
        return room.snapshot() if room else {}

    def welcome_payload(self, request_id: str | None = None) -> dict:
        return make(
            "welcome",
            {
                "conn_id": self.conn_id,
                "nickname": self.nickname,
                "room": self.room,
                "rooms": self.hub.room_list(),
                "online": self.hub.online_count(),
            },
            request_id,
        )
