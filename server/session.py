"""会话：一条 TCP 连接的读写、发送队列与背压、握手保护、心跳与重连凭据。

要点：

* 每个连接一个**有界**发送队列（条数 + 字节数双阈值）和一个写协程；
* 消息分三类：可丢（房间广播）/ 必达（私信、系统、结算）/ 控制（心跳，不进队列直接写）；
* 队列持续积压到上限即判定慢客户端：清空积压、回一条 error、随后断开；
* 读循环只有一条退出路径（`finally` 统一清理），任何异常都不重试；
* 时间统一用服务端单调时钟。
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque

from . import protocol
from .framing import FrameError, encode_frame, read_frame
from .protocol import ErrCode, error, make

logger = logging.getLogger(__name__)


class Session:
    def __init__(self, conn_id: str, reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter, hub, cfg) -> None:
        """连接级状态：发送队列、最后活跃时间、丢弃计数与重连 token。"""
        self.conn_id = conn_id
        self.reader = reader
        self.writer = writer
        self.hub = hub
        self.cfg = cfg
        self.nickname: str | None = None
        self.token: str | None = None
        self.room: str | None = None

        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=cfg.send_queue_max)
        self._queue_bytes = 0
        self.last_seen_ms = protocol.now_ms()
        self.last_ping_ms = 0
        self.bad_frames = 0
        self.unknown_types = 0
        self.dropped = 0
        self.queue_high_water = 0
        self.drop_times: deque[int] = deque()
        self.slow_consumer = False
        self.close_reason = ""
        self.closed = False
        self._writer_task: asyncio.Task | None = None

    # ------------------------------------------------------------- 生命周期 ----

    async def run(self) -> None:
        """会话主循环：握手→逐帧分发；任何异常都只有 finally 一个出口。"""
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
            self._reject_bad_frame(exc)
        except asyncio.TimeoutError:
            self.send(error(ErrCode.HANDSHAKE_TIMEOUT, "未在限时内完成登录"))
            self.close_reason = "handshake timeout"
            logger.info("[%s] 握手超时", self.conn_id)
        except asyncio.IncompleteReadError:
            pass  # 对端正常或异常关闭
        except (ConnectionError, OSError) as exc:
            logger.info("[%s] 连接中断: %s", self.conn_id, exc)
        except Exception:  # pragma: no cover - 兜底，绝不让异常逃逸出去
            logger.exception("[%s] 会话异常", self.conn_id)
        finally:
            await self.close(self.close_reason or "session end")

    async def _handshake(self) -> dict | None:
        """首个消息必须在限时内到达，且是 login 或 resume。"""
        timeout = self.cfg.handshake_timeout_ms / 1000
        payload = await asyncio.wait_for(
            read_frame(self.reader, self.cfg.max_frame_bytes), timeout
        )
        self.last_seen_ms = protocol.now_ms()
        if payload.get("type") not in ("login", "resume", "register"):
            self.send(error(ErrCode.NOT_LOGGED_IN, "首个消息必须是 login / resume / register",
                            payload.get("request_id")))
            await self.close("handshake rejected")
            return None
        return payload

    async def _dispatch(self, payload: dict) -> None:
        """按 type 查注册表；未知类型回错误，连续多次则断开。"""
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
                error(ErrCode.UNKNOWN_TYPE, f"未知消息类型 {msg_type}", request_id,
                      supported=protocol.SUPPORTED_TYPES)
            )
            if self.unknown_types >= self.cfg.bad_frame_limit:
                await self.close("too many unknown types")
            return

        data = payload.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        try:
            await handler(self, data, request_id)
        except Exception:
            # 单个处理器的 bug 不该拖垮整条连接
            logger.exception("[%s] 处理 %s 时异常", self.conn_id, msg_type)
            self.send(error(ErrCode.INTERNAL, f"处理 {msg_type} 时内部错误", request_id))

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

    async def _writer_loop(self) -> None:
        """唯一的写协程：从有界队列取字节写入 socket，背压在这里体现。"""
        try:
            while True:
                data = await self.queue.get()
                self._queue_bytes = max(0, self._queue_bytes - len(data))
                try:
                    # drain() 只阻塞本连接的写协程：慢客户端会卡住自己的队列，
                    # 不会拖住其他连接（这正是 M1 慢客户端对照实验验证的点）。
                    self.writer.write(data)
                    await self.writer.drain()
                finally:
                    self.queue.task_done()
        except (asyncio.CancelledError, ConnectionError, OSError):
            pass

    async def close(self, reason: str = "", keep_reservation: bool = True) -> None:
        """统一的退出路径：摘除状态、发完残余消息、关连接、取消写协程。可重复调用。"""
        if self.closed:
            return
        self.closed = True
        self.close_reason = reason
        nickname = self.nickname  # logout 会清空，先留一份用于日志
        self.hub.logout(self, keep_reservation=keep_reservation)
        if self._writer_task is not None and not self._writer_task.done():
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
            "[%s] 关闭(%s) 昵称=%s 丢弃=%d 非法帧=%d 队列峰值=%d",
            self.conn_id, reason or "-", nickname, self.dropped,
            self.bad_frames, self.queue_high_water,
        )

    # ------------------------------------------------------------- 发送 ----

    def send(self, payload: dict, droppable: bool = False) -> bool:
        """入队；队列满（条数或字节数）时按类型处理。"""
        if self.closed or self.slow_consumer:
            return False
        data = encode_frame(payload)
        if self.queue.full() or self._queue_bytes + len(data) > self.cfg.send_queue_max_bytes:
            self._on_drop(droppable)
            return False
        self._queue_bytes += len(data)
        self.queue.put_nowait(data)
        self.queue_high_water = max(self.queue_high_water, self.queue.qsize())
        return True

    def send_control(self, payload: dict) -> bool:
        """控制类消息（心跳）：不进业务队列，直接写传输层，避免被积压挡住。"""
        if self.closed:
            return False
        try:
            self.writer.write(encode_frame(payload))
            return True
        except Exception:  # pragma: no cover
            return False

    def ping(self) -> bool:
        """发心跳：控制帧直接写传输层，绕过业务队列，避免被积压拖慢。"""
        self.last_ping_ms = protocol.now_ms()
        return self.send_control(make("ping", {"interval_ms": self.cfg.ping_interval_ms}))

    def _on_drop(self, droppable: bool) -> None:
        """队列满时的丢弃处理：按时间窗口统计，达到阈值判定慢客户端。"""
        self.dropped += 1
        self.hub.dropped_total += 1
        now = protocol.now_ms()
        self.drop_times.append(now)
        while self.drop_times and now - self.drop_times[0] > self.cfg.slow_consumer_window_ms:
            self.drop_times.popleft()
        if not droppable:
            logger.warning("[%s] 队列已满，丢弃必达消息（累计 %d）", self.conn_id, self.dropped)
        if len(self.drop_times) >= self.cfg.slow_consumer_drop_limit:
            self._mark_slow_consumer()

    def _mark_slow_consumer(self) -> None:
        """判定慢客户端：清空积压、回一条 error、随后断开。"""
        if self.slow_consumer or self.closed:
            return
        self.slow_consumer = True
        self.hub.slow_consumer_disconnects += 1
        logger.warning(
            "[%s] 判定慢客户端：窗口内丢弃 %d 条，断开连接",
            self.conn_id, len(self.drop_times),
        )
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:  # pragma: no cover
                break
        self._queue_bytes = 0
        try:
            self.queue.put_nowait(
                encode_frame(error(ErrCode.SLOW_CONSUMER, "发送队列持续积压，连接将被断开"))
            )
        except asyncio.QueueFull:  # pragma: no cover
            pass
        asyncio.get_running_loop().create_task(self._close_later(0.3))

    async def _close_later(self, delay: float) -> None:
        """延迟关闭：先让错误帧发出去，再断开连接。"""
        await asyncio.sleep(delay)
        await self.close("slow consumer", keep_reservation=False)

    async def close_for_idle(self) -> None:
        """心跳判活失败：静默超时，清理连接（不保留重连预约）。"""
        self.hub.idle_timeouts += 1
        self.send_control(error(ErrCode.SESSION_EXPIRED, "长时间无响应，连接已断开"))
        await self.close("idle timeout", keep_reservation=True)

    # ------------------------------------------------------------- 视图 ----

    def room_snapshot(self) -> dict:
        room = self.hub.room_of(self)
        return room.snapshot() if room else {}

    def welcome_payload(self, request_id: str | None = None) -> dict:
        return make(
            "welcome",
            {
                "conn_id": self.conn_id,
                "nickname": self.nickname,
                "account": self.nickname,
                "token": self.token,
                "room": self.room,
                "rooms": self.hub.room_list(),
                "online": self.hub.online_count(),
                "reconnect_grace_ms": self.cfg.reconnect_grace_ms,
            },
            request_id,
        )

    def resumed_payload(self, request_id: str | None = None) -> dict:
        room = self.hub.room_of(self)
        return make(
            "resumed",
            {
                "conn_id": self.conn_id,
                "nickname": self.nickname,
                "account": self.nickname,
                "token": self.token,
                "room": self.room,
                "users": room.nicknames() if room else [],
                "online": self.hub.online_count(),
            },
            request_id,
        )
