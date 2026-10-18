"""会打牌的机器人：通过协议连接牌桌，用 game.ai 决策，直到一局结束。

它同时承担两个用途：

* `scripts`/`bots` 下的演示与压测（三个机器人自动打完整局）；
* 自动化测试里的端到端客户​​端（验证"网络层 + 规则引擎"整条链路）。
"""

from __future__ import annotations

import asyncio
import traceback
from typing import Any

from game import ai
from game.text import card_code, cards_text, parse_card
from client.net import Connection


class DoudizhuBot:
    """走协议的斗地主机器人：维护自己的手牌与公开状态，用 game.ai 决策。"""
    def __init__(self, nickname: str, conn: Connection, room: str) -> None:
        """初始化机器人状态与收消息任务。"""
        self.nickname = nickname
        self.conn = conn
        self.room = room
        self.hand: list[int] = []
        self.state: str | None = None
        self.current: str | None = None
        self.landlord: str | None = None
        self.table: list[int] = []
        self.bids: dict[str, int] = {}
        self.hand_counts: dict[str, int] = {}
        self.multiplier = 1
        self.log: list[str] = []
        self.result: dict | None = None
        self.finished = asyncio.Event()
        self._action_key: tuple | None = None
        self.history_len = 0
        self._task: asyncio.Task | None = None
        self.errors: list[str] = []

    # ------------------------------------------------------------ 生命周期 ----

    async def join_and_ready(self) -> None:
        """进房并准备，同时启动收消息循环。"""
        await self.conn.join_room(self.room)
        self._task = asyncio.create_task(self._loop())
        await self.conn.request("room_ready", {})

    async def play_until_end(self, timeout: float = 120.0) -> dict:
        """等待一局结束并返回结算数据。"""
        await asyncio.wait_for(self.finished.wait(), timeout)
        assert self.result is not None
        return self.result

    async def run_forever(self, rounds: int | None = None) -> None:
        """一直坐在牌桌上：每局结束自动再准备，供"AI 陪打"使用。"""
        await self.join_and_ready()
        played = 0
        while True:
            await self.finished.wait()
            played += 1
            if rounds is not None and played >= rounds:
                return
            self.finished = asyncio.Event()
            self.result = None
            self._action_key = None
            await asyncio.sleep(0.3)
            await self.conn.request("room_ready", {})

    async def stop(self) -> None:
        """停止收消息任务。"""
        if self._task:
            self._task.cancel()

    # ------------------------------------------------------------ 收消息 ----

    async def _loop(self) -> None:
        """收消息主循环。"""
        try:
            while True:
                frame = await self.conn.recv()
                await self._handle(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - 演示脚本里直接打印
            self.errors.append(f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")

    async def _handle(self, frame: dict) -> None:
        """更新本地状态，并在轮到自己时行动。"""
        kind = frame.get("type")
        data: dict[str, Any] = frame.get("data") or {}

        if kind in ("game_start", "hand"):
            self.hand = [parse_card(code) for code in data.get("hand", [])]
            self.state = data.get("state")
            self.current = data.get("current")
            self.landlord = data.get("landlord")
            self.history_len = data.get("history_len", self.history_len)
            label = "发牌" if kind == "game_start" else "拿底牌"
            self.log.append(f"[{label}] {self.nickname}: {cards_text(self.hand)}")
        elif kind == "table_state":
            self._apply_public(data)
        elif kind == "landlord":
            self.landlord = data.get("landlord")
            self.hand_counts = dict(data.get("hand_counts") or {})
            self.log.append(
                f"[定地主] {self.landlord} 叫 {data.get('bid')} 分，"
                f"底牌 {data.get('bottom_text')}"
            )
            if self.landlord == self.nickname:
                self.hand.extend(parse_card(code) for code in data.get("bottom", []))
                self.hand.sort(key=lambda c: -_rank(c))
        elif kind == "bid":
            self.bids[data["player"]] = data["score"]
            score = data["score"]
            self.log.append(f"[叫分] {data['player']} → {'不叫' if score == 0 else str(score) + ' 分'}")
        elif kind == "play":
            cards = [parse_card(code) for code in data.get("cards", [])]
            self.table = cards
            if data.get("player") == self.nickname:
                for card in cards:
                    if card in self.hand:
                        self.hand.remove(card)
            self.hand_counts[data["player"]] = data.get("hand_count", 0)
            bomb = "（炸弹）" if data.get("bomb") else ""
            self.log.append(
                f"[出牌] {data['player']}: {data.get('cards_text')}{bomb} "
                f"剩 {data.get('hand_count')} 张，倍数 ×{data.get('multiplier')}"
            )
        elif kind == "pass":
            self.log.append(f"[过牌] {data['player']}")
        elif kind == "table_cleared":
            self.table = []
            self.log.append(f"[一轮结束] {data['leader']} 重新领出")
        elif kind == "timeout":
            self.log.append(f"[超时] {data['player']} {data['phase']} → {data['action']}")
        elif kind == "game_over":
            self.result = data
            self.log.append(f"[结束] 赢家 {data.get('winner')}，倍数 ×{data.get('multiplier')}")
            self.finished.set()
            return

        # 只在拿到权威轮次信息（table_state / 手牌）时才行动，
        # 避免用"别人动作事件里带的旧 current"去抢跑
        if kind in ("table_state", "game_start", "hand"):
            await self._maybe_act()

    def _apply_public(self, data: dict) -> None:
        """用 table_state 同步公开信息。"""
        self.state = data.get("state")
        self.current = data.get("current")
        self.landlord = data.get("landlord")
        self.table = [parse_card(code) for code in data.get("table", [])]
        self.bids = dict(data.get("bids") or {})
        self.hand_counts = dict(data.get("hand_counts") or {})
        self.multiplier = data.get("multiplier", self.multiplier)
        self.history_len = data.get("history_len", self.history_len)

    # ------------------------------------------------------------ 决策 ----

    async def _maybe_act(self) -> None:
        """只在拿到权威轮次信息（table_state/手牌）时行动。"""
        if self.finished.is_set() or self.state is None:
            return
        key = (self.state, self.current, self.history_len)
        if self.current != self.nickname or key == self._action_key:
            return

        self._action_key = key
        try:
            if self.state == "BIDDING":
                highest = max(self.bids.values(), default=0)
                score = ai.choose_bid(self.hand, highest)
                await self.conn.request("bid", {"score": score}, timeout=5)
            elif self.state == "PLAYING":
                cards = ai.choose_play(self.hand, self.table)
                if cards:
                    await self.conn.request(
                        "play", {"cards": [card_code(c) for c in cards]}, timeout=5
                    )
                else:
                    await self.conn.request("pass", {}, timeout=5)
        except Exception as exc:
            # 动作被拒（例如状态已过期）不能中断整个收消息循环，等下一帧再试
            self.errors.append(f"{type(exc).__name__}: {exc}")
            self._action_key = None


def _rank(card: int) -> int:
    """取牌的点数索引（用于排序）。"""
    from game.cards import Card

    return Card.get_rank_int(card)
