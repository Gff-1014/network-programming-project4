"""Three-browser table smoke test: login, join, ready, deal."""

from __future__ import annotations

import asyncio
import json

import aiohttp

from web_smoke import WS_URL, login, wait_for


async def main() -> int:
    accounts = ("wanjia1", "wanjia2", "wanjia3")
    async with aiohttp.ClientSession() as http:
        clients = [await http.ws_connect(WS_URL, heartbeat=10) for _ in accounts]
        try:
            for ws, account in zip(clients, accounts):
                await login(ws, account)

            for ws in clients:
                await ws.send_json({
                    "type": "join_room",
                    "data": {"room": "牌桌1"},
                })
                await wait_for(
                    ws,
                    lambda msg: msg.get("type") == "event"
                    and msg.get("kind") == "joined",
                )

            for ws in clients:
                await ws.send_json({"type": "ready", "data": {}})

            starts = []
            for ws in clients:
                starts.append(await wait_for(
                    ws,
                    lambda msg: msg.get("type") == "event"
                    and msg.get("kind") == "game_start",
                    timeout=8.0,
                ))

            print(json.dumps({
                "ok": True,
                "hands": [len(item["data"].get("hand", [])) for item in starts],
                "states": [item["data"].get("state") for item in starts],
            }, ensure_ascii=False))
            return 0
        finally:
            for ws in clients:
                await ws.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
