"""WebSocket/HTTP smoke test for group announcements, polls and files."""

from __future__ import annotations

import asyncio
import json

import aiohttp

from web_smoke import WS_URL, login, wait_for


async def main() -> int:
    http_base = "http://127.0.0.1:8080"
    async with aiohttp.ClientSession() as http:
        owner = await http.ws_connect(WS_URL, heartbeat=10)
        member = await http.ws_connect(WS_URL, heartbeat=10)
        guest = await http.ws_connect(WS_URL, heartbeat=10)
        try:
            owner_snapshot = await login(owner, "wanjia1")
            member_snapshot = await login(member, "wanjia2")
            await login(guest, "wanjia3")

            await owner.send_json({
                "type": "create_group",
                "data": {"members": ["wanjia2"]},
            })
            owner_state = await wait_for(
                owner,
                lambda msg: msg.get("type") == "event"
                and msg.get("kind") == "group_state",
            )
            member_state = await wait_for(
                member,
                lambda msg: msg.get("type") == "event"
                and msg.get("kind") == "group_state",
            )
            group_id = owner_state["data"]["group_id"]
            invite_token = owner_state["data"]["invite_token"]
            assert member_state["data"]["group_id"] == group_id

            await owner.send_json({
                "type": "rename_group",
                "data": {"name": "网页自定义群名"},
            })
            renamed = await wait_for(
                member,
                lambda msg: msg.get("type") == "event"
                and msg.get("kind") == "group_renamed",
            )
            assert renamed["data"]["group_id"] == group_id
            assert renamed["data"]["room_name"] == "网页自定义群名"

            await owner.send_json({
                "type": "set_announcement",
                "data": {"text": "群活动：周五晚八点"},
            })
            await wait_for(
                member,
                lambda msg: msg.get("type") == "event"
                and msg.get("kind") == "announcement_updated",
            )

            await owner.send_json({
                "type": "poll_create",
                "data": {
                    "title": "活动时间",
                    "options": ["周五", "周六"],
                },
            })
            poll_state = await wait_for(
                member,
                lambda msg: msg.get("type") == "event"
                and msg.get("kind") == "group_state"
                and msg.get("data", {}).get("polls"),
            )
            poll = poll_state["data"]["polls"][-1]
            await member.send_json({
                "type": "poll_vote",
                "data": {
                    "poll_id": poll["poll_id"],
                    "option_id": poll["options"][0]["id"],
                },
            })
            voted_state = await wait_for(
                member,
                lambda msg: msg.get("type") == "event"
                and msg.get("kind") == "group_state"
                and any(
                    item.get("total_votes") == 1
                    for item in msg.get("data", {}).get("polls", [])
                ),
            )
            assert voted_state["data"]["polls"][-1]["total_votes"] == 1

            form = aiohttp.FormData()
            form.add_field(
                "file",
                "群文件联调内容".encode("utf-8"),
                filename="group-smoke.txt",
                content_type="text/plain",
            )
            upload = await http.post(
                f"{http_base}/api/groups/{group_id}/files",
                params={"token": owner_snapshot["data"]["file_token"]},
                data=form,
            )
            uploaded = await upload.json()
            assert upload.status == 200, uploaded
            await wait_for(
                member,
                lambda msg: msg.get("type") == "event"
                and msg.get("kind") == "group_file_added",
            )

            download = await http.get(
                f"{http_base}/api/attachments/{uploaded['file_id']}",
                params={"token": member_snapshot["data"]["file_token"]},
            )
            content = await download.text()
            assert download.status == 200
            assert "群文件联调内容" in content

            delete = await http.delete(
                f"{http_base}/api/attachments/{uploaded['file_id']}",
                params={"token": owner_snapshot["data"]["file_token"]},
            )
            assert delete.status == 200
            await wait_for(
                member,
                lambda msg: msg.get("type") == "event"
                and msg.get("kind") == "group_file_deleted",
            )

            await guest.send_json({
                "type": "join_invite",
                "data": {"token": invite_token},
            })
            await wait_for(
                guest,
                lambda msg: msg.get("type") == "event"
                and msg.get("kind") == "group_invite_pending",
            )
            await wait_for(
                owner,
                lambda msg: msg.get("type") == "event"
                and msg.get("kind") == "group_invite_requested",
            )
            await owner.send_json({
                "type": "approve_group_invite",
                "data": {"account": "wanjia3"},
            })
            joined = await wait_for(
                guest,
                lambda msg: msg.get("type") == "event"
                and msg.get("kind") == "joined",
            )
            assert joined["data"]["group_id"] == group_id

            print(json.dumps({
                "ok": True,
                "group_id": group_id,
                "announcement": True,
                "poll_votes": 1,
                "file_id": uploaded["file_id"],
                "invite_join": True,
            }, ensure_ascii=False))
            return 0
        finally:
            await owner.close()
            await member.close()
            await guest.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
