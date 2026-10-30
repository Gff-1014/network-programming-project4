"""SQLite persistence tests for groups and chat history."""

from __future__ import annotations

import asyncio

import pytest

from client.net import Connection, ServerError
from server.protocol import ErrCode


async def test_group_and_room_messages_survive_server_restart(server_factory, tmp_path):
    db_path = str(tmp_path / "persist.db")
    first = await server_factory(db_path=db_path)
    owner = await Connection.connect(first.host, first.port)
    member = await Connection.connect(first.host, first.port)
    await owner.login("persist-owner")
    await member.login("persist-member")
    await owner.create_group(["persist-member"])
    group_state = await owner.wait_for("group_state", timeout=3)
    group_key = group_state["data"]["room"]
    await owner.room_msg("这条消息要跨重启保留")

    await owner.close()
    await member.close()
    await first.hub.close_all("test restart")
    await asyncio.sleep(0.1)

    second = await server_factory(db_path=db_path)
    restarted = await Connection.connect(second.host, second.port)
    await restarted.login("persist-owner")
    rooms = await restarted.list_rooms()
    room = next(
        item for item in rooms["data"]["rooms"]
        if item.get("room_key") == group_key
    )
    assert room["kind"] == "group"

    await restarted.join_room(group_key)
    history = await restarted.wait_for("room_history", timeout=3)
    assert any(
        item["text"] == "这条消息要跨重启保留"
        for item in history["data"]["messages"]
    )
    await restarted.close()


async def test_room_message_history_is_paginated(server):
    writer = await Connection.connect(server.host, server.port)
    await writer.login("history-writer")
    await writer.join_room("聊天房")
    await writer.wait_for("room_history", timeout=3)
    for index in range(55):
        await writer.room_msg(f"分页消息-{index:02d}")

    reader = await Connection.connect(server.host, server.port)
    await reader.login("history-reader")
    await reader.join_room("聊天房")
    first_page = await reader.wait_for("room_history", timeout=3)
    assert len(first_page["data"]["messages"]) == 50
    assert first_page["data"]["has_more"] is True
    next_before = first_page["data"]["next_before"]
    assert next_before is not None

    older = await reader.request("room_history", {"before_id": next_before})
    assert older["type"] == "room_history"
    assert older["data"]["messages"]
    assert all(
        message["message_id"] < next_before
        for message in older["data"]["messages"]
    )

    await writer.close()
    await reader.close()


async def test_public_channels_are_available_to_logged_in_users(server):
    conn = await Connection.connect(server.host, server.port)
    await conn.login("channel-user")
    rooms = await conn.list_rooms()
    channels = [
        item for item in rooms["data"]["rooms"]
        if item.get("kind") == "channel"
    ]
    assert len(channels) == 3
    assert all(item.get("owner") == "wanjia1" for item in channels)

    first = channels[0]
    joined = await conn.join_room(first["room_key"])
    assert joined["data"]["room"] == first["room_key"]
    await conn.close()


async def test_only_wanjia1_can_create_public_channel(server):
    admin = await Connection.connect(server.host, server.port)
    member = await Connection.connect(server.host, server.port)
    await admin.login("wanjia1")
    await member.login("wanjia2")

    created = await admin.request("create_channel", {"name": "课程答疑"})
    assert created["type"] == "channel_created"
    assert created["data"]["owner"] == "wanjia1"
    rooms = await member.list_rooms()
    assert any(
        item.get("room_key") == created["data"]["room_key"]
        and item.get("kind") == "channel"
        for item in rooms["data"]["rooms"]
    )

    with pytest.raises(ServerError) as exc:
        await member.request("create_channel", {"name": "越权频道"})
    assert exc.value.code == ErrCode.NOT_CHANNEL_ADMIN

    await admin.close()
    await member.close()


async def test_created_channel_and_messages_survive_server_restart(
    server_factory,
    tmp_path,
):
    db_path = str(tmp_path / "channel-persist.db")
    first = await server_factory(db_path=db_path)
    admin = await Connection.connect(first.host, first.port)
    await admin.login("wanjia1")

    created = await admin.request("create_channel", {"name": "跨重启频道"})
    room_key = created["data"]["room_key"]
    await admin.join_room(room_key)
    await admin.wait_for("room_history", timeout=3)
    await admin.room_msg("频道消息也要跨重启保留")

    await admin.close()
    await first.hub.close_all("test restart")
    await asyncio.sleep(0.1)

    second = await server_factory(db_path=db_path)
    member = await Connection.connect(second.host, second.port)
    await member.login("wanjia2")
    rooms = await member.list_rooms()
    channel = next(
        item for item in rooms["data"]["rooms"]
        if item.get("room_key") == room_key
    )
    assert channel["name"] == "跨重启频道"
    assert channel["kind"] == "channel"
    assert channel["owner"] == "wanjia1"

    await member.join_room(room_key)
    history = await member.wait_for("room_history", timeout=3)
    assert any(
        item["text"] == "频道消息也要跨重启保留"
        for item in history["data"]["messages"]
    )
    await member.close()
