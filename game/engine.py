"""对局状态机（本项目自研）。

一个 `Match` 实例就是"一局"：

    WAITING（等三人准备） → DEALING（发牌） → BIDDING（叫分）
        → PLAYING（出牌） → SETTLED（结算） → 可 reset 再来一局

设计约定：

1. **纯逻辑**：不涉及网络、asyncio、文件，只接收玩家的动作，返回结果与事件。
2. **时间由外部注入**：判定超时、生成截止时间都用调用方传入的时钟，
   保证"一切以服务端时间为准"，也方便测试用假时钟。
3. **事件流**：每次状态变更都追加事件，网络层的广播、观战推送、对局日志
   全部由事件生成，不需要另外维护一套逻辑。
4. **手牌私有**：只有 `view_for(玩家)` 才包含该玩家自己的手牌，
   `public_view()` 只给张数与公开信息（观战者用这个）。
"""

from __future__ import annotations

import random
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from enum import Enum

from .api import check_card_type, cards_greater, hand_str, new_game
from .cards import Card


class State(str, Enum):
    WAITING = "WAITING"
    DEALING = "DEALING"
    BIDDING = "BIDDING"
    PLAYING = "PLAYING"
    SETTLED = "SETTLED"


class Err(str, Enum):
    """错误码，网络层直接把它放进 error 帧。"""

    BAD_STATE = "BAD_STATE"
    NOT_YOUR_TURN = "NOT_YOUR_TURN"
    ILLEGAL_BID = "ILLEGAL_BID"
    INVALID_PLAY = "INVALID_PLAY"
    NOT_IN_HAND = "NOT_IN_HAND"
    CANNOT_PASS = "CANNOT_PASS"
    NOT_ENOUGH_PLAYERS = "NOT_ENOUGH_PLAYERS"


@dataclass
class MatchConfig:
    """一局的可调参数（默认值取自大纲 v7）。"""

    bid_timeout_ms: int = 10_000
    play_timeout_ms: int = 20_000
    max_redeal: int = 3          # 连续全不叫的次数上限，超过则本局作废
    base_score: int = 1
    request_cache_size: int = 256
    seat_count: int = 3


@dataclass
class Action:
    """动作的处理结果。"""

    ok: bool
    code: Err | None = None
    detail: str = ""
    duplicate: bool = False


@dataclass
class Match:
    players: list[str]
    seed: int | None = None
    config: MatchConfig = field(default_factory=MatchConfig)
    clock: object = None  # Callable[[], int]，毫秒

    def __post_init__(self) -> None:
        self.cfg = self.config
        if self.clock is None:
            self.clock = lambda: int(time.monotonic() * 1000)  # noqa: E731
        if len(self.players) != self.cfg.seat_count:
            raise ValueError("当前只支持三人一桌")
        self.state = State.WAITING
        self.ready_set: set[str] = set()
        self.hands: dict[str, list[int]] = {p: [] for p in self.players}
        self.bottom: list[int] = []
        self.bottom_revealed = False
        self.landlord: str | None = None
        self.current: str | None = None
        self.bids: dict[str, int] = {}
        self.bid_order: list[str] = []
        self.table: list[int] = []
        self.table_owner: str | None = None
        self.passes = 0
        self.multiplier = 1
        self.bombs = 0
        self.play_counts: dict[str, int] = {p: 0 for p in self.players}
        self.history: list[dict] = []
        self.result: dict | None = None
        self.deadline_ms: int | None = None
        self.events: list[dict] = []
        self.deal_round = 0
        self._requests: OrderedDict[str, Action] = OrderedDict()

    # ------------------------------------------------------------ 查询 ----

    @property
    def landlord_side(self) -> list[str]:
        return [p for p in self.players if p != self.landlord] if self.landlord else []

    def hand_counts(self) -> dict[str, int]:
        return {p: len(h) for p, h in self.hands.items()}

    def _emit(self, **event) -> None:
        event.setdefault("ts_ms", self.clock())
        event.setdefault("state", self.state.value)
        self.events.append(event)

    def drain_events(self) -> list[dict]:
        events, self.events = self.events, []
        return events

    # ------------------------------------------------------------ 生命周期 ----

    def ready(self, player: str) -> Action:
        """玩家准备；三人都准备好后自动发牌并进入叫分。"""
        if self.state is not State.WAITING:
            return Action(False, Err.BAD_STATE, f"当前状态 {self.state.value} 不能准备")
        if player not in self.players:
            return Action(False, Err.NOT_ENOUGH_PLAYERS, f"{player} 不在本桌")
        self.ready_set.add(player)
        self._emit(type="ready", player=player, ready=len(self.ready_set))
        if len(self.ready_set) == len(self.players):
            self._deal()
        return Action(True)

    def _deal(self) -> None:
        self.state = State.DEALING
        self.deal_round += 1
        deal_seed = None if self.seed is None else self.seed + self.deal_round - 1
        groups = new_game(deal_seed)
        self.hands = {p: groups[i] for i, p in enumerate(self.players)}
        self.bottom = groups[3]
        self.bottom_revealed = False
        self.table, self.table_owner, self.passes = [], None, 0
        self.bids, self.bid_order = {}, []
        self.multiplier = 1
        self.bombs = 0
        self.play_counts = {p: 0 for p in self.players}
        self.landlord = None
        self.result = None
        self._emit(type="dealt", deal_round=self.deal_round, hand_counts=self.hand_counts())

        first = random.Random(deal_seed if deal_seed is not None else self.clock()).randrange(
            len(self.players)
        )
        self.bid_order = self.players[first:] + self.players[:first]
        self.current = self.bid_order[0]
        self.state = State.BIDDING
        self.deadline_ms = self.clock() + self.cfg.bid_timeout_ms
        self._emit(type="bid_start", first=self.current, order=list(self.bid_order))

    def reset(self) -> None:
        """一局结束后复位，可直接再开一局。"""
        self.__post_init__()

    # ------------------------------------------------------------ 叫分 ----

    def bid(self, player: str, score: int, request_id: str | None = None) -> Action:
        """叫分：0 表示不叫，1/2/3 必须高于当前最高分。"""
        cached = self._cached(request_id)
        if cached is not None:
            return cached
        if self.state is not State.BIDDING:
            return Action(False, Err.BAD_STATE, f"当前状态 {self.state.value} 不能叫分")
        if player != self.current:
            return Action(False, Err.NOT_YOUR_TURN, f"现在轮到 {self.current}")
        if score not in (0, 1, 2, 3):
            return Action(False, Err.ILLEGAL_BID, "叫分只能是 0/1/2/3")
        highest = max(self.bids.values(), default=0)
        if score != 0 and score <= highest:
            return Action(False, Err.ILLEGAL_BID, f"叫分必须高于当前最高分 {highest}")

        self.bids[player] = score
        self._emit(type="bid", player=player, score=score)
        self._remember(request_id, Action(True))

        if score == 3 or len(self.bids) == len(self.players):
            self._decide_landlord()
            return Action(True)

        self._next_bidder()
        return Action(True)

    def _next_bidder(self) -> None:
        remaining = [p for p in self.bid_order if p not in self.bids]
        if not remaining:
            self._decide_landlord()
            return
        self.current = remaining[0]
        self.deadline_ms = self.clock() + self.cfg.bid_timeout_ms

    def _decide_landlord(self) -> None:
        best = max(self.bids.values(), default=0)
        if best == 0:
            if self.deal_round >= self.cfg.max_redeal:
                self._emit(type="match_aborted", reason="无人叫分", deal_round=self.deal_round)
                self.state = State.WAITING
                self.ready_set.clear()
                self.deadline_ms = None
                return
            self._emit(type="redeal", reason="三家都不叫")
            self._deal()
            return

        self.landlord = next(p for p in self.bid_order if self.bids.get(p) == best)
        self.multiplier = best
        self.bottom_revealed = True
        self.hands[self.landlord] = Card.sort_cards_by_rank_int(
            self.hands[self.landlord] + self.bottom
        )
        self.state = State.PLAYING
        self.current = self.landlord
        self.table, self.table_owner, self.passes = [], None, 0
        self.deadline_ms = self.clock() + self.cfg.play_timeout_ms
        self._emit(
            type="landlord_decided",
            landlord=self.landlord,
            bid=best,
            bottom=list(self.bottom),
            hand_counts=self.hand_counts(),
        )

    # ------------------------------------------------------------ 出牌 ----

    def play(self, player: str, cards: list[int], request_id: str | None = None) -> Action:
        cached = self._cached(request_id)
        if cached is not None:
            return cached
        if self.state is not State.PLAYING:
            return Action(False, Err.BAD_STATE, f"当前状态 {self.state.value} 不能出牌")
        if player != self.current:
            return Action(False, Err.NOT_YOUR_TURN, f"现在轮到 {self.current}")
        if not cards:
            return Action(False, Err.INVALID_PLAY, "没有出牌")
        if Counter(cards) - Counter(self.hands[player]):
            return Action(False, Err.NOT_IN_HAND, "出的牌不在手牌里")

        ok, info = check_card_type(cards)
        if not ok:
            return Action(False, Err.INVALID_PLAY, f"牌型不合法：{info}")
        if self.table:
            greater, _ = cards_greater(cards, self.table)
            if not greater:
                return Action(False, Err.INVALID_PLAY, f"压不过 {hand_str(self.table)}")

        self._apply_play(player, cards)
        self._remember(request_id, Action(True))
        return Action(True)

    def pass_turn(self, player: str, request_id: str | None = None) -> Action:
        """过牌；只有在别人出过牌（桌面非空）时才能过。"""
        cached = self._cached(request_id)
        if cached is not None:
            return cached
        if self.state is not State.PLAYING:
            return Action(False, Err.BAD_STATE, f"当前状态 {self.state.value} 不能过牌")
        if player != self.current:
            return Action(False, Err.NOT_YOUR_TURN, f"现在轮到 {self.current}")
        if not self.table:
            return Action(False, Err.CANNOT_PASS, "你是本轮先手，必须出牌")

        self._apply_pass(player)
        self._remember(request_id, Action(True))
        return Action(True)

    def _apply_play(self, player: str, cards: list[int]) -> None:
        hand = list(self.hands[player])
        for card in cards:
            hand.remove(card)
        self.hands[player] = hand
        self.play_counts[player] += 1
        self.table = Card.sort_cards_by_rank_int(cards)
        self.table_owner = player
        self.passes = 0
        self.history.append({"event": "play", "player": player, "cards": list(self.table)})

        is_bomb = len(cards) == 4 and len({Card.get_rank_int(c) for c in cards}) == 1
        is_rocket = len(cards) == 2 and all(Card.get_rank_int(c) >= 13 for c in cards)
        if is_bomb or is_rocket:
            self.bombs += 1
            self.multiplier *= 2
        self._emit(
            type="play",
            player=player,
            cards=list(self.table),
            cards_text=hand_str(self.table),
            hand_count=len(hand),
            bomb=is_bomb or is_rocket,
            multiplier=self.multiplier,
        )

        if not hand:
            self._settle(player)
            return
        self._advance(player)

    def _apply_pass(self, player: str) -> None:
        self.passes += 1
        self.history.append({"event": "pass", "player": player})
        self._emit(type="pass", player=player, passes=self.passes)
        if self.passes >= 2:
            self.table, self.table_owner, self.passes = [], None, 0
            self.current = self._last_player(player)
            self._emit(type="table_cleared", leader=self.current)
        else:
            self._advance(player)
        self.deadline_ms = self.clock() + self.cfg.play_timeout_ms

    def _advance(self, player: str) -> None:
        idx = self.players.index(player)
        self.current = self.players[(idx + 1) % len(self.players)]
        self.deadline_ms = self.clock() + self.cfg.play_timeout_ms

    def _last_player(self, fallback: str) -> str:
        """最近一次真正出牌的玩家（用于两度过牌后重新领出）。"""
        for item in reversed(self.history):
            if item["event"] == "play":
                return item["player"]
        return fallback

    # ------------------------------------------------------------ 超时 ----

    def tick(self, now_ms: int | None = None) -> Action | None:
        """由服务端定时调用：到点就替当前玩家做动作。"""
        if self.deadline_ms is None or self.state not in (State.BIDDING, State.PLAYING):
            return None
        now = self.clock() if now_ms is None else now_ms
        if now < self.deadline_ms:
            return None
        return self.timeout()

    def timeout(self) -> Action:
        """当前玩家超时：叫分视为不叫；跟牌视为过牌；先手自动出最小单张。"""
        player = self.current
        if player is None:
            return Action(False, Err.BAD_STATE, "没有当前玩家")
        if self.state is State.BIDDING:
            self._emit(type="timeout", player=player, phase="BIDDING", action="不叫")
            return self.bid(player, 0)
        if self.state is State.PLAYING:
            if self.table:
                self._emit(type="timeout", player=player, phase="PLAYING", action="过牌")
                return self.pass_turn(player)
            smallest = min(self.hands[player], key=Card.get_rank_int)
            self._emit(type="timeout", player=player, phase="PLAYING", action="自动出最小单张")
            return self.play(player, [smallest])
        return Action(False, Err.BAD_STATE, f"当前状态 {self.state.value} 无超时处理")

    # ------------------------------------------------------------ 结算 ----

    def _settle(self, winner: str) -> None:
        landlord_win = winner == self.landlord
        spring = landlord_win and all(
            self.play_counts[p] == 0 for p in self.landlord_side
        )
        if spring:
            self.multiplier *= 2
        total = self.cfg.base_score * self.multiplier
        scores = {p: -total for p in self.players}
        scores[self.landlord] = -2 * total
        if landlord_win:
            scores[self.landlord] = 2 * total
            for p in self.landlord_side:
                scores[p] = -total
        else:
            for p in self.landlord_side:
                scores[p] = total

        self.state = State.SETTLED
        self.deadline_ms = None
        self.result = {
            "winner": winner,
            "landlord": self.landlord,
            "landlord_win": landlord_win,
            "spring": spring,
            "bombs": self.bombs,
            "multiplier": self.multiplier,
            "scores": scores,
        }
        self._emit(
            type="game_over",
            winner=winner,
            landlord_win=landlord_win,
            spring=spring,
            bombs=self.bombs,
            multiplier=self.multiplier,
            scores=scores,
        )

    # ------------------------------------------------------------ 视图 ----

    def view_for(self, player: str | None) -> dict:
        """给某个玩家的视图；player 为 None 时返回观战视图（不含任何手牌）。"""
        view = self.public_view()
        if player in self.players:
            view["you"] = player
            view["hand"] = list(self.hands[player])
            view["hand_text"] = hand_str(self.hands[player])
        return view

    def public_view(self) -> dict:
        return {
            "state": self.state.value,
            "players": list(self.players),
            "landlord": self.landlord,
            "current": self.current,
            "hand_counts": self.hand_counts(),
            "bottom": list(self.bottom) if self.bottom_revealed else [],
            "bottom_revealed": self.bottom_revealed,
            "table": list(self.table),
            "table_owner": self.table_owner,
            "bids": dict(self.bids),
            "multiplier": self.multiplier,
            "bombs": self.bombs,
            "deadline_ms": self.deadline_ms,
            "result": self.result,
            "deal_round": self.deal_round,
        }

    # ------------------------------------------------------------ 幂等 ----

    def _cached(self, request_id: str | None) -> Action | None:
        if not request_id:
            return None
        hit = self._requests.get(request_id)
        if hit is None:
            return None
        self._requests.move_to_end(request_id)
        return Action(ok=hit.ok, code=hit.code, detail=hit.detail, duplicate=True)

    def _remember(self, request_id: str | None, action: Action) -> None:
        if not request_id:
            return
        self._requests[request_id] = action
        self._requests.move_to_end(request_id)
        while len(self._requests) > self.cfg.request_cache_size:
            self._requests.popitem(last=False)
