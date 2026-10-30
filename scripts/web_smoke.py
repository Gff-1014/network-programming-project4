"""Two-browser WebSocket smoke test for the web gateway.

Prerequisite:

    .venv\\Scripts\\python.exe -m server
    .venv\\Scripts\\python.exe web_gateway.py

Then run:

    .venv\\Scripts\\python.exe scripts\\web_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import aiohttp


WS_URL = os.environ.get("WEB_WS_URL", "ws://127.0.0.1:8080/ws")


async def wait_for(ws, predicate, timeout: float = 6.0):
    """Read messages until predicate(message) is true."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError("等待 WebSocket 消息超时")
        message = await asyncio.wait_for(ws.receive_json(), remaining)
        if predicate(message):
            return message


async def login(ws, account: str) -> dict:
    await ws.send_json({
        "type": "login",
        "data": {"account": account, "password": "123456"},
    })
    await wait_for(
        ws,
        lambda msg: msg.get("type") == "event"
        and msg.get("kind") == "logged_in",
    )
    return await wait_for(
        ws,
        lambda msg: msg.get("type") == "snapshot"
        and msg.get("data", {}).get("nickname") == account,
    )


async def main() -> int:
    async with aiohttp.ClientSession() as http:
        first = await http.ws_connect(WS_URL, heartbeat=10)
        second = await http.ws_connect(WS_URL, heartbeat=10)
        try:
            await login(first, "wanjia1")
            await login(second, "wanjia2")

            await first.send_json({
                "type": "join_room",
                "data": {"room": "聊天房"},
            })
            await wait_for(
                first,
                lambda msg: msg.get("type") == "event"
                and msg.get("kind") == "joined",
            )
            await second.send_json({
                "type": "join_room",
                "data": {"room": "聊天房"},
            })
            await wait_for(
                second,
                lambda msg: msg.get("type") == "event"
                and msg.get("kind") == "joined",
            )

            marker = "web-smoke-room"
            await first.send_json({
                "type": "room_msg",
                "data": {"text": marker},
            })
            room_event = await wait_for(
                second,
                lambda msg: msg.get("type") == "event"
                and msg.get("kind") == "room_msg"
                and marker in msg.get("data", {}).get("text", ""),
            )

            await first.send_json({
                "type": "private_open",
                "data": {"peer": "wanjia2"},
            })
            await wait_for(
                first,
                lambda msg: msg.get("type") == "event"
                and msg.get("kind") == "private_opened",
            )
            private_marker = "web-smoke-private"
            await first.send_json({
                "type": "private_msg",
                "data": {"text": private_marker},
            })
            private_event = await wait_for(
                second,
                lambda msg: msg.get("type") == "event"
                and msg.get("kind") == "private_msg"
                and private_marker in msg.get("data", {}).get("text", ""),
            )

            print(json.dumps({
                "ok": True,
                "room": room_event["data"].get("text"),
                "private": private_event["data"].get("text"),
            }, ensure_ascii=False))
            return 0
        finally:
            await first.close()
            await second.close()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:
        print(f"web smoke failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
