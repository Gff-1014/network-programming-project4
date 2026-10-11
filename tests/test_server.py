"""服务端集成测试：登录、房间、广播、私信与异常输入。"""

from __future__ import annotations

import asyncio

import pytest

from client.net import Connection, ServerError
from server.framing import read_frame
from server.protocol import ErrCode


async def test_login_and_welcome(server):
    conn = await Connection.connect(server.host, server.port)
    welcome = await conn.login("alice")
    data = welcome["data"]
    assert data["nickname"] == "alice"
    assert data["room"] == "大厅"
    assert any(room["name"] == "聊天房" for room in data["rooms"])
    assert data["online"] == 1
    await conn.close()


async def test_duplicate_nickname_rejected(server):
    first = await Connection.connect(server.host, server.port)
    await first.login("dup")
    second = await Connection.connect(server.host, server.port)
    with pytest.raises(ServerError) as exc:
        await second.login("dup")
    assert exc.value.code == ErrCode.NICKNAME_TAKEN
    await first.close()
    await second.close()


@pytest.mark.parametrize("nickname", ["", "   ", "a" * 40, "with space"])
async def test_invalid_nickname(server, nickname):
    conn = await Connection.connect(server.host, server.port)
    with pytest.raises(ServerError) as exc:
        await conn.login(nickname)
    assert exc.value.code == ErrCode.INVALID_NICKNAME
    await conn.close()


async def test_first_message_must_be_login(server):
    conn = await Connection.connect(server.host, server.port)
    await conn.send("room_msg", {"text": "hi"})
    first = await conn.recv(2.0)
    assert first["type"] == "error"
    assert first["data"]["code"] == ErrCode.NOT_LOGGED_IN


async def test_ping_pong(server):
    conn = await Connection.connect(server.host, server.port)
    await conn.login("pinger")
    pong = await conn.ping()
    assert pong["type"] == "pong"
    await conn.close()


async def test_list_rooms(server):
    conn = await Connection.connect(server.host, server.port)
    await conn.login("lister")
    reply = await conn.list_rooms()
    names = [room["name"] for room in reply["data"]["rooms"]]
    assert "大厅" in names and "聊天房" in names
    await conn.close()


async def test_room_broadcast_and_isolation(server):
    a = await Connection.connect(server.host, server.port)
    b = await Connection.connect(server.host, server.port)
    await a.login("roomA")
    await b.login("roomB")
    await a.join_room("聊天房")  # b 留在大厅

    await a.room_msg("只在聊天房可见")
    got = await a.wait_for("room_msg", match={"text": "只在聊天房可见"})
    assert got["data"]["from"] == "roomA"

    with pytest.raises(asyncio.TimeoutError):
        await b.wait_for("room_msg", timeout=0.3)
    await a.close()
    await b.close()


async def test_private_message_only_to_target(server):
    a = await Connection.connect(server.host, server.port)
    b = await Connection.connect(server.host, server.port)
    c = await Connection.connect(server.host, server.port)
    for conn, nick in ((a, "pmA"), (b, "pmB"), (c, "pmC")):
        await conn.login(nick)

    await a.private_msg("pmB", "悄悄话")
    got = await b.wait_for("private_msg", match={"text": "悄悄话"})
    assert got["data"]["from"] == "pmA"
    with pytest.raises(asyncio.TimeoutError):
        await c.wait_for("private_msg", timeout=0.3)
    for conn in (a, b, c):
        await conn.close()


async def test_private_message_to_offline_user(server):
    a = await Connection.connect(server.host, server.port)
    await a.login("lonely")
    with pytest.raises(ServerError) as exc:
        await a.private_msg("nobody", "在吗")
    assert exc.value.code == ErrCode.USER_NOT_FOUND
    await a.close()


async def test_leave_room_returns_to_lobby(server):
    conn = await Connection.connect(server.host, server.port)
    await conn.login("returner")
    await conn.join_room("聊天房")
    reply = await conn.leave_room()
    assert reply["data"]["to"] == "大厅"
    with pytest.raises(ServerError) as exc:
        await conn.leave_room()  # 大厅不能退出
    assert exc.value.code == ErrCode.NOT_IN_ROOM
    await conn.close()


async def test_join_unknown_room(server):
    conn = await Connection.connect(server.host, server.port)
    await conn.login("explorer")
    with pytest.raises(ServerError) as exc:
        await conn.join_room("不存在的房间")
    assert exc.value.code == ErrCode.ROOM_NOT_FOUND
    await conn.close()


async def test_room_users_updates(server):
    a = await Connection.connect(server.host, server.port)
    await a.login("listA")
    await a.join_room("聊天房")
    b = await Connection.connect(server.host, server.port)
    await b.login("listB")
    await b.join_room("聊天房")

    # 房间成员列表会随加入/退出多次推送，这里等到出现"两人都在"的那一条
    users: set[str] = set()
    deadline = asyncio.get_running_loop().time() + 3
    while asyncio.get_running_loop().time() < deadline:
        frame = await a.wait_for("room_users", timeout=1.0, match={"room": "聊天房"})
        users = set(frame["data"]["users"])
        if users == {"listA", "listB"}:
            break
    assert users == {"listA", "listB"}
    await a.close()
    await b.close()


async def test_message_too_long(server):
    conn = await Connection.connect(server.host, server.port)
    await conn.login("longy")
    with pytest.raises(ServerError) as exc:
        await conn.room_msg("x" * (server.cfg.max_text_chars + 1))
    assert exc.value.code == ErrCode.MESSAGE_TOO_LONG
    await conn.close()


async def test_unknown_type_then_disconnect(server):
    conn = await Connection.connect(server.host, server.port)
    await conn.login("unknown")
    for _ in range(server.cfg.bad_frame_limit):
        await conn.send("nope_type", {})
    errors = []
    closed = False
    for _ in range(10):
        try:
            frame = await conn.recv(1.0)
        except asyncio.TimeoutError:
            break
        except (asyncio.IncompleteReadError, ConnectionError):
            closed = True
            break
        if frame.get("type") == "error":
            errors.append(frame)
    assert errors, "服务端应对未知类型回 error"
    assert errors[0]["data"]["code"] == ErrCode.UNKNOWN_TYPE
    assert "supported" in errors[0]["data"]
    assert closed, "连续未知类型后服务端应断开连接"
    await conn.close()


async def test_oversize_frame_rejected(server):
    """长度超限：服务端回 error 帧后必须断开（字节流已错位）。"""
    reader, writer = await asyncio.open_connection(server.host, server.port)
    writer.write((server.cfg.max_frame_bytes + 1).to_bytes(4, "big") + b"{")
    await writer.drain()
    payload = await asyncio.wait_for(read_frame(reader, 1 << 20), 2.0)
    assert payload["type"] == "error"
    assert payload["data"]["code"] == ErrCode.BAD_FRAME
    assert await asyncio.wait_for(reader.read(1), 2.0) == b"", "服务端应关闭连接"
    writer.close()


async def test_handshake_timeout(server_factory):
    server = await server_factory(handshake_timeout_ms=200)
    conn = await Connection.connect(server.host, server.port)
    frame = await conn.recv(2.0)  # 不登录，等服务端超时
    assert frame["type"] == "error"
    assert frame["data"]["code"] == ErrCode.HANDSHAKE_TIMEOUT
    await conn.close()


async def test_prelogin_abrupt_disconnect_keeps_server_healthy(server):
    """回归：未登录连接断掉不能让服务端卡死（参照 Azuronis 的实测缺陷）。"""
    for _ in range(5):
        reader, writer = await asyncio.open_connection(server.host, server.port)
        writer.close()  # 连上就断，什么都不发
    await asyncio.sleep(0.2)

    conn = await Connection.connect(server.host, server.port)
    welcome = await asyncio.wait_for(conn.login("healthy"), 2.0)
    assert welcome["data"]["nickname"] == "healthy"
    await conn.close()


async def test_disconnect_cleans_online_list(server):
    a = await Connection.connect(server.host, server.port)
    await a.login("stay")
    b = await Connection.connect(server.host, server.port)
    await b.login("leaver")
    await asyncio.sleep(0.1)
    await b.close()
    await asyncio.sleep(0.2)
    assert server.online == 1
    assert "leaver" not in server.hub.by_nickname
    await a.close()
