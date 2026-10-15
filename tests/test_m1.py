"""M1：发送队列背压、慢客户端隔离、心跳判活与 token 重连。"""

from __future__ import annotations

import asyncio
import dataclasses
import socket
import struct
import time

import pytest

from client.net import Connection, ServerError, encode_frame
from config import settings
from server.hub import Hub
from server.protocol import ErrCode, make
from server.session import Session


class FakeWriter:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        return None


def make_session(cfg, hub=None) -> Session:
    hub = hub or Hub(cfg)
    session = Session("c0001", None, FakeWriter(), hub, cfg)  # type: ignore[arg-type]
    session.nickname = "queue-user"
    return session


async def wait_until(predicate, timeout: float = 3.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


# --------------------------------------------------------- 队列与背压 ----


async def test_queue_drops_broadcasts_when_full():
    cfg = dataclasses.replace(settings, send_queue_max=4, slow_consumer_drop_limit=100)
    session = make_session(cfg)

    accepted = sum(1 for _ in range(4) if session.send(make("room_msg", {"text": "x"}),
                                                     droppable=True))
    assert accepted == 4, "前 4 条应入队"
    assert session.send(make("room_msg", {"text": "y"}), droppable=True) is False
    assert session.dropped == 1
    assert session.hub.dropped_total == 1
    assert session.slow_consumer is False, "偶尔丢一条不该判定慢客户端"
    await session.close("test", keep_reservation=False)


async def test_queue_byte_limit_also_drops():
    cfg = dataclasses.replace(settings, send_queue_max=1000, send_queue_max_bytes=200,
                              slow_consumer_drop_limit=100)
    session = make_session(cfg)
    text = "x" * 100
    for _ in range(10):
        session.send(make("room_msg", {"text": text}), droppable=True)
    assert session.dropped > 0, "字节数超限也要能挡住"
    await session.close("test", keep_reservation=False)


async def test_slow_consumer_is_marked_and_disconnected():
    cfg = dataclasses.replace(settings, send_queue_max=2, slow_consumer_drop_limit=3,
                              slow_consumer_window_ms=60_000)
    session = make_session(cfg)
    for _ in range(10):
        session.send(make("room_msg", {"text": "flood"}), droppable=True)
    assert session.slow_consumer is True
    assert session.hub.slow_consumer_disconnects == 1
    assert session.queue.qsize() <= 1, "判定后应清空积压，只留一条提示"
    assert await wait_until(lambda: session.closed, timeout=2.0), "判定后应断开连接"


async def test_must_deliver_message_dropped_when_full():
    cfg = dataclasses.replace(settings, send_queue_max=1, slow_consumer_drop_limit=100)
    session = make_session(cfg)
    session.send(make("room_msg", {"text": "a"}), droppable=True)
    assert session.send(make("private_msg", {"text": "b"}), droppable=False) is False
    assert session.dropped == 1
    await session.close("test", keep_reservation=False)


# ------------------------------------------------------------ 心跳 ----


async def test_heartbeat_ping_and_idle_cleanup(server_factory):
    server = await server_factory(ping_interval_ms=200, idle_timeout_ms=700,
                                  sweep_interval_ms=100, reconnect_grace_ms=200)
    conn = await Connection.connect(server.host, server.port, auto_pong=False)
    await conn.login("sleepy")

    ping = await conn.wait_for("ping", timeout=2.0)
    assert "interval_ms" in ping["data"]

    assert await wait_until(lambda: server.hub.idle_timeouts == 1, timeout=3.0), \
        "不回应心跳应被判活失败"
    assert await wait_until(lambda: server.online == 0, timeout=2.0)
    assert server.hub.reserved_count() == 1, "超时后应保留重连预约"

    # 宽限期过后预约被回收
    assert await wait_until(lambda: server.hub.reserved_count() == 0, timeout=3.0)
    await conn.close()


async def test_auto_pong_keeps_connection_alive(server_factory):
    server = await server_factory(ping_interval_ms=150, idle_timeout_ms=500,
                                  sweep_interval_ms=80)
    conn = await Connection.connect(server.host, server.port)  # auto_pong=True
    await conn.login("awake")
    await asyncio.sleep(1.2)
    assert server.hub.idle_timeouts == 0, "自动回应心跳的客户端不该被清理"
    assert server.online == 1
    await conn.close()


# ------------------------------------------------------------ 重连 ----


async def test_resume_restores_nickname_and_room(server):
    first = await Connection.connect(server.host, server.port)
    welcome = await first.login("rejoin")
    token = welcome["data"]["token"]
    await first.join_room("聊天房")
    await first.close()

    assert await wait_until(lambda: server.hub.reserved_count() == 1, timeout=2.0)

    second = await Connection.connect(server.host, server.port)
    resumed = await second.resume(token)
    data = resumed["data"]
    assert data["nickname"] == "rejoin"
    assert data["room"] == "聊天房", "重连应回到原房间"
    assert data["token"] != token, "重连后应换发新 token"
    assert server.online == 1

    # 旧 token 不能再用
    third = await Connection.connect(server.host, server.port)
    with pytest.raises(ServerError) as exc:
        await third.resume(token)
    assert exc.value.code == ErrCode.SESSION_EXPIRED
    await second.close()
    await third.close()


async def test_nickname_reserved_during_grace(server):
    first = await Connection.connect(server.host, server.port)
    await first.login("holder")
    await first.close()
    assert await wait_until(lambda: server.hub.reserved_count() == 1, timeout=2.0)

    other = await Connection.connect(server.host, server.port)
    with pytest.raises(ServerError) as exc:
        await other.login("holder")
    assert exc.value.code == ErrCode.ACCOUNT_LOGGED_IN
    assert "重连" in exc.value.message
    await other.close()


async def test_resume_after_grace_expires(server_factory):
    server = await server_factory(reconnect_grace_ms=150, sweep_interval_ms=50)
    first = await Connection.connect(server.host, server.port)
    token = (await first.login("late"))["data"]["token"]
    await first.close()

    assert await wait_until(lambda: server.hub.reserved_count() == 1, timeout=2.0)
    assert await wait_until(lambda: server.hub.reserved_count() == 0, timeout=3.0)
    late = await Connection.connect(server.host, server.port)
    with pytest.raises(ServerError) as exc:
        await late.resume(token)
    assert exc.value.code == ErrCode.SESSION_EXPIRED

    # 预约过期后昵称应被释放
    other = await Connection.connect(server.host, server.port)
    welcome = await other.login("late")
    assert welcome["data"]["nickname"] == "late"
    await late.close()
    await other.close()


# ------------------------------------------- 慢客户端不影响其他人 ----


def raw_slow_client(host: str, port: int, nickname: str, room: str) -> socket.socket:
    """连上、登录、进房间，然后**永不读取**（制造真实背压）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
    sock.connect((host, port))
    sock.sendall(encode_frame({"type": "login", "data": {"nickname": nickname},
                               "request_id": "r1"}))
    time.sleep(0.2)
    sock.sendall(encode_frame({"type": "join_room", "data": {"room": room},
                               "request_id": "r2"}))
    return sock


async def test_slow_client_does_not_block_others(server_factory):
    """对照实验：慢客户端把服务端发送队列堵满时，正常客户端仍要快。"""
    server = await server_factory(send_queue_max=4, slow_consumer_drop_limit=5,
                                  slow_consumer_window_ms=60_000)
    room = "聊天房"

    slow = raw_slow_client(server.host, server.port, "slowpoke", room)
    await asyncio.sleep(0.3)

    fast = await Connection.connect(server.host, server.port)
    await fast.login("speedy")
    await fast.join_room(room)

    payload = "z" * 480
    latencies: list[float] = []
    for index in range(200):
        start = time.perf_counter()
        await fast.send("room_msg", {"text": f"{payload}-{index}"})
        latencies.append((time.perf_counter() - start) * 1000)
        await asyncio.sleep(0.002)

    # 正常客户端自己的消息应当正常收到（说明广播链路没有被慢客户端堵死）
    got = await fast.wait_for("room_msg", timeout=3.0,
                              match={"from": "speedy", "text": f"{payload}-199"})
    assert got["data"]["from"] == "speedy"
    assert max(latencies) < 200, f"发送不应被阻塞: max={max(latencies):.1f} ms"

    # 慢客户端应当被丢弃计数，最终被判定并断开
    assert await wait_until(
        lambda: server.hub.dropped_total > 0 or server.hub.slow_consumer_disconnects > 0,
        timeout=5.0,
    ), "慢客户端应触发丢弃或断开"
    assert await wait_until(lambda: server.hub.slow_consumer_disconnects >= 1, timeout=8.0), \
        "持续积压应最终判定为慢客户端"

    slow.close()
    await fast.close()
