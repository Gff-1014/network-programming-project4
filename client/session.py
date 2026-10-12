"""客户端会话：把协议消息变成界面能直接用的状态与事件。

刻意不依赖 Tkinter，方便脱离界面做自动化测试：
界面只负责订阅 `on_event(kind, data)` 并刷新控件。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from game.text import card_text, cards_text, parse_card

from .net import Connection, ServerError


class ClientSession:
    def __init__(self, on_event: Callable[[str, dict], None] | None = None) -> None:
        self.conn: Connection | None = None
        self.on_event = on_event or (lambda kind, data: None)
        self.nickname: str | None = None
        self.token: str | None = None
        self.room: str | None = None
        self.role: str | None = None
        self.rooms: list[dict] = []
        self.room_users: list[str] = []
        self.hand: list[int] = []
        self.table: dict[str, Any] = {}
        self.auto_play = False
        self.log: list[str] = []
        self.error: str | None = None
        self._pump_task: asyncio.Task | None = None
        self._closed = False

    # ------------------------------------------------------------ 连接 ----

    async def connect(self, host: str, port: int) -> None:
        self.conn = await Connection.connect(host, port)
        self._pump_task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        assert self.conn is not None
        while not self._closed:
            try:
                frame = await self.conn.recv()
            except Exception:
                if not self._closed:
                    self.on_event("disconnected", {})
                return
            try:
                await self._handle(frame.get("type", ""), frame.get("data") or {})
            except Exception as exc:  # pragma: no cover - 界面层兜底
                self._note(f"处理 {frame.get('type')} 出错: {exc}")

    async def close(self) -> None:
        self._closed = True
        if self._pump_task:
            self._pump_task.cancel()
        if self.conn:
            try:
                await self.conn.send("logout")
                await asyncio.sleep(0.05)
            except Exception:
                pass
            await self.conn.close()

    # ------------------------------------------------------------ 动作 ----

    async def login(self, nickname: str) -> dict:
        assert self.conn is not None
        welcome = await self.conn.login(nickname)
        data = welcome["data"]
        self.nickname = data["nickname"]
        self.token = data.get("token")
        self.room = data.get("room")
        self.rooms = data.get("rooms", [])
        self.on_event("logged_in", data)
        return data

    async def refresh_rooms(self) -> None:
        assert self.conn is not None
        reply = await self.conn.list_rooms()
        self.rooms = reply["data"]["rooms"]
        self.on_event("room_list", {"rooms": self.rooms})

    async def join_room(self, room: str) -> dict:
        assert self.conn is not None
        reply = await self.conn.join_room(room)
        self.room = room
        self.role = reply["data"].get("role", "player")
        self.on_event("joined", {"room": room, "role": self.role})
        return reply["data"]

    async def spectate(self, room: str) -> dict:
        assert self.conn is not None
        reply = await self.conn.request("spectate", {"room": room})
        self.room = room
        self.role = "spectator"
        self.on_event("joined", {"room": room, "role": self.role})
        return reply["data"]

    async def leave_room(self) -> None:
        assert self.conn is not None
        reply = await self.conn.leave_room()
        self.room = reply["data"].get("to")   # 服务端会把人放回大厅
        self.role = "player"
        self.hand = []
        self.table = {}
        self.room_users = []
        self.on_event("left", {"room": self.room})

    async def say(self, text: str) -> None:
        assert self.conn is not None
        await self.conn.room_msg(text)

    async def whisper(self, to: str, text: str) -> None:
        assert self.conn is not None
        await self.conn.private_msg(to, text)

    async def ready(self) -> None:
        assert self.conn is not None
        await self.conn.request("room_ready", {})

    async def bid(self, score: int) -> None:
        assert self.conn is not None
        await self.conn.request("bid", {"score": score})

    async def play(self, cards: list[int]) -> None:
        assert self.conn is not None
        from game.text import card_code

        await self.conn.request("play", {"cards": [card_code(c) for c in cards]})

    async def pass_turn(self) -> None:
        assert self.conn is not None
        await self.conn.request("pass", {})

    async def request_state(self) -> None:
        assert self.conn is not None
        await self.conn.request("game_state", {})

    async def set_auto(self, enabled: bool) -> None:
        """在线托管开关。"""
        assert self.conn is not None
        await self.conn.request("auto_play", {"enabled": enabled})
        self.auto_play = enabled
        self._emit(
            "auto_play",
            {"enabled": enabled},
            "[托管] 已交给服务端自动出牌" if enabled else "[托管] 已取回控制",
        )

    # ------------------------------------------------------------ 收消息 ----

    async def _handle(self, kind: str, data: dict) -> None:
        line: str | None = None
        if kind == "room_list":
            self.rooms = data.get("rooms", [])
        elif kind == "room_users":
            if data.get("room") == self.room:
                self.room_users = list(data.get("users", []))
        elif kind == "joined":
            self.role = data.get("role", self.role)
        elif kind in ("game_start", "hand"):
            self.hand = [parse_card(code) for code in data.get("hand", [])]
            label = "发牌" if kind == "game_start" else "拿到底牌"
            line = f"[{label}] 我的手牌（{len(self.hand)} 张）: {cards_text(self.hand)}"
        elif kind == "table_state":
            self.table = data
            if self.nickname:
                self.auto_play = self.nickname in (data.get("auto_players") or [])
        elif kind == "landlord":
            self.table["landlord"] = data.get("landlord")
            line = f"[定地主] {data.get('landlord')} 叫 {data.get('bid')} 分，底牌 {data.get('bottom_text')}"
        elif kind == "bid":
            score = data.get("score")
            line = f"[叫分] {data.get('player')} {'不叫' if not score else f'叫 {score} 分'}"
        elif kind == "play":
            cards = [parse_card(code) for code in data.get("cards", [])]
            if data.get("player") == self.nickname:
                for card in cards:
                    if card in self.hand:
                        self.hand.remove(card)
            bomb = "（炸弹）" if data.get("bomb") else ""
            line = f"[出牌] {data.get('player')}: {data.get('cards_text')}{bomb}"
            # 只在对手快走完时提示剩余张数，避免每手都刷一遍
            remaining = data.get("hand_count")
            if isinstance(remaining, int) and remaining <= 3:
                line += f"　剩 {remaining} 张"
        elif kind == "pass":
            line = f"[过牌] {data.get('player')}"
        elif kind == "table_cleared":
            self.table["table"] = []
            self.table["table_owner"] = None
            line = f"[一轮结束] {data.get('leader')} 重新领出"
        elif kind == "timeout":
            line = f"[托管] {data.get('player')} 超时未出 → {data.get('action')}"
        elif kind == "game_over":
            scores = data.get("scores") or {}
            mine = scores.get(self.nickname, 0)
            line = f"[结算] 赢家 {data.get('winner')}，倍数 ×{data.get('multiplier')}，我 {mine:+d} 分"
            self.hand = []
        elif kind == "user_joined":
            line = f"[系统] {data.get('nickname')} 进入房间"
        elif kind == "user_left":
            line = f"[系统] {data.get('nickname')} 离开房间"
        elif kind == "user_reconnecting":
            line = f"[系统] {data.get('nickname')} 掉线，等待重连…"
        elif kind == "room_msg":
            line = f"[{data.get('from')}] {data.get('text')}"
        elif kind == "private_msg":
            line = f"（私信）[{data.get('from')} → {data.get('to')}] {data.get('text')}"
        elif kind == "error":
            self.error = f"{data.get('code')}: {data.get('message')}"
            line = f"[错误] {self.error}"
        self._emit(kind, data, line)

    def _note(self, text: str) -> None:
        self.log.append(text)
        self.log = self.log[-200:]

    def _emit(self, kind: str, data: dict, line: str | None = None) -> None:
        """把事件连同它自己的那行日志一起交给界面。

        不能让界面用 `log[-1]` 去取最近一行：多个事件在同一批里到达时，
        它们会取到同一行（既重复、又漏掉真正的记录）。
        """
        payload = dict(data)
        if line:
            self._note(line)
            payload["_line"] = line
        self.on_event(kind, payload)

    # ------------------------------------------------------------ 便捷 ----

    @property
    def my_turn(self) -> bool:
        return self.table.get("current") == self.nickname

    @property
    def table_cards_text(self) -> str:
        codes = self.table.get("table") or []
        return cards_text([parse_card(code) for code in codes]) if codes else "（空）"

    def hand_text(self) -> str:
        return cards_text(self.hand)

    def card_labels(self) -> list[str]:
        return [card_text(card) for card in self.hand]

    def dump_state(self) -> str:
        return json.dumps(
            {
                "nickname": self.nickname,
                "room": self.room,
                "role": self.role,
                "state": self.table.get("state"),
                "current": self.table.get("current"),
                "hand": len(self.hand),
                "rooms": [room["name"] for room in self.rooms],
            },
            ensure_ascii=False,
        )


async def quick_login(host: str, port: int, nickname: str) -> ClientSession:
    """便捷入口：连上并登录，返回可直接使用的会话。"""
    session = ClientSession()
    await session.connect(host, port)
    await session.login(nickname)
    return session
