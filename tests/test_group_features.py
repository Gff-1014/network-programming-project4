"""群公告、群投票、群文件和扫码邀请的协议测试。"""

from __future__ import annotations

import asyncio

import pytest

from client.net import Connection, ServerError
from server.protocol import ErrCode


async def make_connections(server, names):
    conns = {}
    for name in names:
        conn = await Connection.connect(server.host, server.port)
        await conn.login(name)
        conns[name] = conn
    return conns


async def test_group_announcement_poll_file_and_invite(server):
    conns = await make_connections(server, ["owner", "member", "guest"])
    owner = conns["owner"]
    member = conns["member"]
    guest = conns["guest"]

    reply = await owner.create_group(["member"])
    owner_state = await owner.wait_for("group_state", timeout=3)
    member_state = await member.wait_for("group_state", timeout=3)
    token = owner_state["data"]["invite_token"]
    group_id = owner_state["data"]["group_id"]
    assert member_state["data"]["group_id"] == group_id
    guest_rooms = await guest.request("list_rooms", {})
    assert group_id not in {
        item.get("group_id") for item in guest_rooms["data"]["rooms"]
    }
    with pytest.raises(ServerError) as exc:
        await guest.request("join_room", {"room": owner_state["data"]["room"]})
    assert exc.value.code == ErrCode.ROOM_NOT_FOUND

    with pytest.raises(ServerError) as exc:
        await member.request("set_announcement", {"text": "非群主"})
    assert exc.value.code == ErrCode.NOT_GROUP_OWNER

    await owner.request("set_announcement", {"text": "周五晚八点活动"})
    announcement = await member.wait_for(
        "announcement_updated",
        timeout=3,
    )
    assert announcement["data"]["announcement"]["text"] == "周五晚八点活动"

    await owner.request("poll_create", {
        "title": "今晚吃什么",
        "options": ["火锅", "烧烤"],
    })
    pushed = await member.wait_for("group_state", timeout=3)
    polls = pushed["data"]["polls"]
    poll_id = polls[-1]["poll_id"]
    await member.request("poll_vote", {
        "poll_id": poll_id,
        "option_id": polls[-1]["options"][0]["id"],
    })
    updated = await member.wait_for("group_state", timeout=3)
    poll = next(item for item in updated["data"]["polls"] if item["poll_id"] == poll_id)
    assert poll["total_votes"] == 1

    await owner.request("group_file_register", {
        "group_id": group_id,
        "file_id": "file-test-1",
        "name": "课程资料.txt",
        "mime": "text/plain",
        "size": 12,
        "kind": "file",
    })
    file_event = await member.wait_for("group_file_added", timeout=3)
    assert file_event["data"]["file_id"] == "file-test-1"

    pending = await guest.request("join_invite", {"token": token})
    assert pending["type"] == "group_invite_pending"
    await owner.wait_for("group_invite_requested", timeout=3)
    await owner.request("approve_group_invite", {"account": "guest"})
    joined = await guest.wait_for("joined", timeout=3)
    assert joined["data"]["group_id"] == group_id
    joined_rooms = await guest.request("list_rooms", {})
    assert group_id in {
        item.get("group_id") for item in joined_rooms["data"]["rooms"]
    }

    await owner.request("group_file_delete", {"file_id": "file-test-1"})
    deleted = await member.wait_for("group_file_deleted", timeout=3)
    assert deleted["data"]["file_id"] == "file-test-1"

    for conn in conns.values():
        await conn.request("logout", {})
        await asyncio.sleep(0)


async def test_group_owner_can_use_duplicate_display_names(server):
    conns = await make_connections(server, ["owner", "member", "guest"])
    owner = conns["owner"]
    member = conns["member"]

    await owner.create_group(["member"])
    first = await owner.wait_for("group_state", timeout=3)
    first_key = first["data"]["room"]
    first_id = first["data"]["group_id"]

    with pytest.raises(ServerError) as exc:
        await member.request("rename_group", {"name": "重复群名"})
    assert exc.value.code == ErrCode.NOT_GROUP_OWNER

    await owner.request("rename_group", {"name": "重复群名"})
    renamed = await member.wait_for("group_renamed", timeout=3)
    assert renamed["data"]["room_name"] == "重复群名"
    assert renamed["data"]["room"] == first_key
    assert renamed["data"]["group_id"] == first_id

    await owner.create_group(["guest"])
    second = await owner.wait_for("group_state", timeout=3)
    second_key = second["data"]["room"]
    second_id = second["data"]["group_id"]
    assert second_key != first_key
    assert second_id != first_id
    await owner.request("rename_group", {"name": "重复群名"})

    owner_rooms = await owner.request("list_rooms", {})
    member_rooms = await member.request("list_rooms", {})
    owner_groups = [
        item for item in owner_rooms["data"]["rooms"]
        if item.get("name") == "重复群名"
    ]
    member_groups = [
        item for item in member_rooms["data"]["rooms"]
        if item.get("name") == "重复群名"
    ]
    assert len(owner_groups) == 2
    assert len(member_groups) == 1
    assert len({item["room_key"] for item in owner_groups}) == 2
    assert len({item["group_id"] for item in owner_groups}) == 2

    for conn in conns.values():
        await conn.request("logout", {})


async def test_group_member_invite_requires_owner_approval(server):
    conns = await make_connections(server, ["owner", "member", "guest"])
    owner = conns["owner"]
    member = conns["member"]
    guest = conns["guest"]

    await owner.create_group(["member"])
    await member.wait_for("group_state", timeout=3)
    await member.request("invite_group_member", {"members": ["guest"]})
    request = await owner.wait_for("group_invite_requested", timeout=3)
    assert request["data"]["account"] == "guest"

    await owner.request("approve_group_invite", {"account": "guest"})
    joined = await guest.wait_for("joined", timeout=3)
    assert joined["data"]["owner"] == "owner"

    for conn in conns.values():
        await conn.request("logout", {})
