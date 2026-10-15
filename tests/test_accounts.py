"""账号体系与群聊：预置账号、唯一登录、登出、邀请建群与解散。"""

from __future__ import annotations

import asyncio

import pytest

from client.net import Connection, ServerError
from server.protocol import ErrCode


PRESET = [f"wanjia{i}" for i in range(1, 6)]
PASSWORD = "123456"


# --------------------------------------------------------------- 账号 ----


@pytest.mark.parametrize("account", PRESET)
async def test_preset_accounts_can_login(server, account):
    conn = await Connection.connect(server.host, server.port)
    welcome = await conn.login(account, PASSWORD)
    assert welcome["data"]["account"] == account
    assert welcome["data"]["room"] == "大厅"
    await conn.close()


async def test_wrong_password_rejected(server):
    conn = await Connection.connect(server.host, server.port)
    with pytest.raises(ServerError) as exc:
        await conn.login("wanjia1", "wrong-password")
    assert exc.value.code == ErrCode.BAD_CREDENTIALS
    await conn.close()


async def test_same_account_cannot_login_twice(server):
    first = await Connection.connect(server.host, server.port)
    await first.login("wanjia2", PASSWORD)

    second = await Connection.connect(server.host, server.port)
    with pytest.raises(ServerError) as exc:
        await second.login("wanjia2", PASSWORD)
    assert exc.value.code == ErrCode.ACCOUNT_LOGGED_IN
    assert "其它客户端" in exc.value.message

    # 第一个连接仍然可用（没有被顶掉）
    await first.request("ping", {})
    await first.close()
    await second.close()


async def test_logout_frees_account(server):
    first = await Connection.connect(server.host, server.port)
    await first.login("wanjia3", PASSWORD)
    await first.request("logout", {})
    await asyncio.sleep(0.2)

    second = await Connection.connect(server.host, server.port)
    welcome = await second.login("wanjia3", PASSWORD)
    assert welcome["data"]["account"] == "wanjia3"
    await second.close()


async def test_auto_registered_account_keeps_its_password(server):
    """未预置账号首次登录即注册；之后密码必须一致。"""
    conn = await Connection.connect(server.host, server.port)
    await conn.login("newbie", "mypass")
    await conn.request("logout", {})
    await asyncio.sleep(0.2)

    again = await Connection.connect(server.host, server.port)
    with pytest.raises(ServerError) as exc:
        await again.login("newbie", "another-pass")
    assert exc.value.code == ErrCode.BAD_CREDENTIALS
    welcome = await again.login("newbie", "mypass")
    assert welcome["data"]["account"] == "newbie"
    await again.close()


async def test_strict_mode_rejects_unknown_account(server_factory):
    server = await server_factory(auto_register=False)
    conn = await Connection.connect(server.host, server.port)
    with pytest.raises(ServerError) as exc:
        await conn.login("not-invited", PASSWORD)
    assert exc.value.code == ErrCode.UNKNOWN_ACCOUNT
    # 预置账号仍然可用
    welcome = await conn.login("wanjia5", PASSWORD)
    assert welcome["data"]["account"] == "wanjia5"
    await conn.close()


# --------------------------------------------------------------- 群聊 ----


async def test_create_group_invites_members(server):
    conns = []
    for account in ("wanjia1", "wanjia2", "wanjia3"):
        conn = await Connection.connect(server.host, server.port)
        await conn.login(account, PASSWORD)
        await conn.join_room("聊天房")
        conns.append(conn)

    reply = await conns[0].request("create_group", {"members": ["wanjia2", "wanjia3"]})
    room = reply["data"]["room"]
    assert room.startswith("群聊")

    # 被邀请者也收到了 group_created，并且已经在群里
    for conn in conns:
        frame = await conn.wait_for("group_created", timeout=3.0)
        assert frame["data"]["room"] == room
        assert frame["data"]["owner"] == "wanjia1"
        assert set(frame["data"]["members"]) == {"wanjia1", "wanjia2", "wanjia3"}

    rooms = {r["name"]: r for r in server.hub.room_list()}
    assert rooms[room]["kind"] == "group"
    assert rooms[room]["owner"] == "wanjia1"
    assert rooms[room]["count"] == 3
    for conn in conns:
        await conn.close()


async def test_group_member_must_be_online(server):
    conn = await Connection.connect(server.host, server.port)
    await conn.login("wanjia4", PASSWORD)
    with pytest.raises(ServerError) as exc:
        await conn.request("create_group", {"members": ["ghost-account"]})
    assert exc.value.code == ErrCode.GROUP_MEMBER_OFFLINE
    await conn.close()


async def test_only_owner_can_dismiss_group(server):
    conns = []
    for account in ("wanjia1", "wanjia2"):
        conn = await Connection.connect(server.host, server.port)
        await conn.login(account, PASSWORD)
        await conn.join_room("聊天房")
        conns.append(conn)

    reply = await conns[0].request("create_group", {"members": ["wanjia2"]})
    room = reply["data"]["room"]
    await asyncio.sleep(0.3)

    with pytest.raises(ServerError) as exc:
        await conns[1].request("delete_group", {"room": room})
    assert exc.value.code == ErrCode.NOT_GROUP_OWNER

    await conns[0].request("delete_group", {"room": room})
    await asyncio.sleep(0.3)
    names = [r["name"] for r in server.hub.room_list()]
    assert room not in names, "解散后房间应从列表里消失"
    assert server.hub.by_nickname["wanjia2"].room == "大厅", "成员应回到大厅"
    for conn in conns:
        await conn.close()
