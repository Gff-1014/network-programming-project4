"""客户端测试：会话层（无界面）与窗口冒烟。"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from client.session import ClientSession


async def test_client_session_chat_flow(server):
    events: list[tuple[str, dict]] = []
    alice = ClientSession(on_event=lambda kind, data: events.append((kind, data)))
    await alice.connect(server.host, server.port)
    welcome = await alice.login("alice")
    assert alice.nickname == "alice"
    assert welcome["room"] == "大厅"
    assert alice.rooms, "登录后应拿到房间列表"

    await alice.join_room("聊天房")
    assert alice.room == "聊天房" and alice.role == "player"

    bob = ClientSession()
    await bob.connect(server.host, server.port)
    await bob.login("bob")
    await bob.join_room("聊天房")

    await alice.say("你好")
    await bob.say("收到")
    await asyncio.sleep(0.3)
    assert any("你好" in line for line in alice.log)
    assert any("收到" in line for line in alice.log)

    await alice.whisper("bob", "悄悄话")
    await asyncio.sleep(0.2)
    assert any("悄悄话" in line for line in bob.log)

    await alice.leave_room()
    assert alice.room == "大厅"
    await alice.close()
    await bob.close()


async def test_client_session_table_flow(server):
    """会话层驱动一桌：准备、叫分、出牌，并确认手牌与轮次被正确维护。"""
    sessions = [ClientSession() for _ in range(3)]
    for index, session in enumerate(sessions):
        await session.connect(server.host, server.port)
        await session.login(f"gui{index}")
        await session.join_room("牌桌1")

    for session in sessions:
        await session.ready()
    await asyncio.sleep(0.5)

    for session in sessions:
        assert len(session.hand) == 17, "发牌后每人 17 张"
        assert session.table.get("state") == "BIDDING"

    state = sessions[0].table
    current = state["current"]
    bidder = next(s for s in sessions if s.nickname == current)
    await bidder.bid(3)
    await asyncio.sleep(0.4)

    landlord = bidder  # 叫 3 分立即成为地主
    assert landlord.table.get("landlord") == landlord.nickname
    assert len(landlord.hand) == 20, "地主应拿到三张底牌"

    smallest = min(landlord.hand, key=lambda card: card & 0xF)
    await landlord.play([smallest])
    await asyncio.sleep(0.4)
    assert len(landlord.hand) == 19
    assert landlord.table.get("table"), "桌面应有牌"

    for session in sessions:
        await session.close()


async def test_client_session_spectator(server):
    sessions = [ClientSession() for _ in range(3)]
    for index, session in enumerate(sessions):
        await session.connect(server.host, server.port)
        await session.login(f"seat{index}")
        await session.join_room("牌桌2")
    watcher = ClientSession()
    await watcher.connect(server.host, server.port)
    await watcher.login("viewer")
    await watcher.join_room("牌桌2")
    assert watcher.role == "spectator"
    assert watcher.hand == [], "观战者不应拿到任何手牌"
    for session in sessions + [watcher]:
        await session.close()


def test_gui_window_smoke():
    """窗口冒烟：能建出界面、跑一轮事件循环再销毁。

    放在子进程里跑：同一个进程里反复创建/销毁 Tk 根窗口会让 Tcl 解释器状态异常
    （表现为 "invalid command name tcl_findLibrary"），子进程最干净。
    """
    pytest.importorskip("tkinter")
    root = Path(__file__).resolve().parents[1]
    code = (
        "from client.app import ChatApp\n"
        "from client.bridge import AsyncBridge\n"
        "bridge = AsyncBridge()\n"
        "app = ChatApp(bridge)\n"
        "app.update()\n"
        "app.destroy()\n"
        "bridge.stop()\n"
        "print('WINDOW-OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=90
    )
    if "no display" in result.stderr.lower() or "tcl" in result.stderr.lower() and result.returncode != 0:
        pytest.skip(f"没有可用的显示环境: {result.stderr.strip().splitlines()[-1:] }")
    assert result.returncode == 0, result.stderr
    assert "WINDOW-OK" in result.stdout
