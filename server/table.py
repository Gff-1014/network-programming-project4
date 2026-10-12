"""牌桌：把斗地主对局引擎（game.engine.Match）接到网络层。

设计要点：

* 牌桌是"房间"的一种（`room.kind == "doudizhu"`），坐在座位上的是玩家，其余是观战者；
* 对局引擎完全不知道网络的存在：这里只做三件事——把玩家动作转成引擎调用、
  把引擎事件翻译成协议消息、按需下发私有视图（手牌只发给本人）；
* 断线不立刻放弃座位：标记为托管，轮到他时由服务端自动决策（默认延迟 1 秒）；
  凭 token 重连即可拿回手牌继续打。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from game.cards import Card
from game.engine import Action, Err, Match, MatchConfig, State
from game.text import card_code, cards_text, parse_card

from .protocol import ErrCode, error, make, now_ms

if TYPE_CHECKING:  # pragma: no cover
    from .session import Session


class Table:
    kind = "doudizhu"

    def __init__(self, room, cfg) -> None:
        self.room = room
        self.cfg = cfg
        self.seats: dict[str, "Session | None"] = {}      # 昵称 -> 会话（None 表示断线托管中）
        self.spectators: dict[str, "Session"] = {}        # conn_id -> 会话
        self.match: Match | None = None
        self.ready_set: set[str] = set()
        self._last_turn: str | None = None
        self._turn_started_ms = now_ms()

    # ------------------------------------------------------------ 座位 ----

    def seat_names(self) -> list[str]:
        return list(self.seats)

    def can_join(self, session: "Session") -> tuple[bool, str | None, str | None]:
        """只做容量判断，不改变状态（供加入房间前检查）。"""
        nickname = session.nickname
        if nickname in self.seats:
            return True, None, None
        if len(self.seats) < self.cfg.seats_per_table:
            return True, None, None
        if len(self.spectators) < self.cfg.max_spectators:
            return True, None, None
        return False, ErrCode.ROOM_FULL, "牌桌已满（3 人 + 2 观战）"

    def session_of(self, session: "Session") -> str | None:
        for nickname, seat in self.seats.items():
            if seat is session:
                return nickname
        return None

    def join(self, session: "Session") -> tuple[bool, str | None, str | None]:
        """就座或观战。返回 (是否成功, 错误码, 说明)。"""
        nickname = session.nickname
        ok, code, message = self.can_join(session)
        if not ok:
            return False, code, message
        if nickname in self.seats:  # 重连后重新入座
            self.seats[nickname] = session
            return True, None, None
        if len(self.seats) < self.cfg.seats_per_table:
            self.seats[nickname] = session
            self._ensure_match()
            self.broadcast(make("table_state", self.public_state()), droppable=True)
            return True, None, None
        if len(self.spectators) < self.cfg.max_spectators:
            self.spectators[session.conn_id] = session
            session.send(make("spectating", {"room": self.room.name,
                                             "state": self.public_state()}))
            if self.match is not None:
                session.send(make("table_state", self.public_state()))
            return True, None, None
        return False, ErrCode.ROOM_FULL, "牌桌已满（3 人 + 2 观战）"

    def leave(self, session: "Session") -> None:
        nickname = self.session_of(session)
        if nickname is not None:
            self.seats.pop(nickname, None)
            self.ready_set.discard(nickname)
            if self.match is not None:
                self.broadcast(make("table_state", self.public_state()), droppable=True)
        self.spectators.pop(session.conn_id, None)

    def on_disconnect(self, session: "Session") -> None:
        """掉线：保留座位，标记为托管中。"""
        nickname = self.session_of(session)
        if nickname is not None:
            self.seats[nickname] = None
            self.room.broadcast(
                make("table_state", self.public_state() | {"auto": nickname}), droppable=True
            )
        self.spectators.pop(session.conn_id, None)

    def on_resume(self, session: "Session") -> bool:
        nickname = session.nickname
        if nickname not in self.seats:
            return False
        self.seats[nickname] = session
        if self.match is not None:
            session.send(make("game_state", self.view_for(nickname)))
        return True

    # ------------------------------------------------------------ 对局 ----

    def _ensure_match(self) -> None:
        """座位坐满时才创建对局；开局由 ready() 在三人全部准备后触发。"""
        if self.match is None and len(self.seats) == self.cfg.seats_per_table:
            self.match = Match(list(self.seats), seed=None, config=MatchConfig())
            self._last_turn = None

    def ready(self, session: "Session") -> Action:
        nickname = self.session_of(session)
        if nickname is None:
            return Action(False, Err.BAD_STATE, "你不在牌桌座位上")
        if self.match is not None and self.match.state is not State.WAITING:
            return Action(True)  # 已经开局，重复准备视为成功
        self.ready_set.add(nickname)
        self._ensure_match()
        if self.match is None:
            # 人数不足：先记下准备状态，等第三位入座并准备后自动开局
            self.broadcast(
                make("table_state", self.public_state() | {"ready": sorted(self.ready_set)}),
                droppable=True,
            )
            return Action(True)
        if len(self.ready_set) < self.cfg.seats_per_table:
            self.broadcast(
                make("table_state", self.public_state() | {"ready": sorted(self.ready_set)}),
                droppable=True,
            )
            return Action(True)

        # 三家都准备：同时置位，Match 内部随即发牌
        for seat_nickname in self.seats:
            action = self.match.ready(seat_nickname)
            if not action.ok:
                return action
        self._flush_events()
        return Action(True)

    def bid(self, session: "Session", score: int, request_id: str | None) -> Action:
        nickname = self.session_of(session)
        if nickname is None or self.match is None:
            return Action(False, Err.BAD_STATE, "对局尚未开始")
        action = self.match.bid(nickname, score, request_id)
        if action.ok:
            self._flush_events()
            self.broadcast(make("table_state", self.public_state()), droppable=True)
        return action

    def play(self, session: "Session", card_texts: list[str],
             request_id: str | None) -> Action:
        nickname = self.session_of(session)
        if nickname is None or self.match is None:
            return Action(False, Err.BAD_STATE, "对局尚未开始")
        try:
            cards = [parse_card(text) for text in card_texts]
        except (KeyError, IndexError):
            return Action(False, Err.INVALID_PLAY, f"无法识别的牌: {card_texts}")
        action = self.match.play(nickname, cards, request_id)
        if action.ok:
            self._flush_events()
            self.broadcast(make("table_state", self.public_state()), droppable=True)
        return action

    def pass_turn(self, session: "Session", request_id: str | None) -> Action:
        nickname = self.session_of(session)
        if nickname is None or self.match is None:
            return Action(False, Err.BAD_STATE, "对局尚未开始")
        action = self.match.pass_turn(nickname, request_id)
        if action.ok:
            self._flush_events()
            self.broadcast(make("table_state", self.public_state()), droppable=True)
        return action

    # ------------------------------------------------------------ 定时 ----

    async def tick(self, now: int | None = None) -> None:
        """由服务端扫描任务驱动：超时与断线托管都在这里发生。"""
        now = now or now_ms()
        if self.match is None:
            return
        current = self.match.current
        if current != self._last_turn:
            self._last_turn = current
            self._turn_started_ms = now

        if self.match.state in (State.BIDDING, State.PLAYING):
            seated = self.seats.get(current) if current else None
            if seated is None:  # 断线托管：轮到自己 1 秒后自动决策
                if now - self._turn_started_ms >= self.cfg.auto_play_delay_ms:
                    self.match.timeout()
                    self._flush_events()
                    self.broadcast(make("table_state", self.public_state()), droppable=True)
                    return
            elif self.match.tick(now):
                self._flush_events()
                self.broadcast(make("table_state", self.public_state()), droppable=True)

    # ------------------------------------------------------------ 视图 ----

    def public_state(self) -> dict:
        if self.match is None:
            # 未开局也给出完整的公开字段，客户端不必做防御式解析
            return {
                "room": self.room.name,
                "state": "WAITING",
                "players": list(self.seats),
                "landlord": None,
                "current": None,
                "hand_counts": {nickname: 0 for nickname in self.seats},
                "bottom": [],
                "bottom_revealed": False,
                "table": [],
                "table_owner": None,
                "bids": {},
                "multiplier": 1,
                "bombs": 0,
                "deadline_ms": None,
                "result": None,
                "deal_round": 0,
                "history_len": 0,
                "seats": [{"nickname": n, "online": self.seats[n] is not None}
                          for n in self.seats],
                "spectators": len(self.spectators),
            }
        view = self.match.public_view()
        # 牌统一用协议编码（'3s'），整数表示只在服务端内部使用
        view["table"] = [card_code(c) for c in view.get("table", [])]
        view["table_text"] = cards_text(self.match.table)
        view["bottom"] = [card_code(c) for c in view.get("bottom", [])]
        view["bottom_text"] = cards_text(self.match.bottom) if view.get("bottom_revealed") else ""
        view["room"] = self.room.name
        view["seats"] = [{"nickname": n, "online": self.seats.get(n) is not None}
                         for n in self.seats]
        view["spectators"] = len(self.spectators)
        return view

    def view_for(self, nickname: str) -> dict:
        """协议视图：手牌用 '3s' 这类字符串编码（整数表示只在服务端内部使用）。"""
        assert self.match is not None
        raw = self.match.view_for(nickname)
        hand = list(raw.pop("hand", []))
        view = dict(raw)
        view["room"] = self.room.name
        view["hand"] = [card_code(c) for c in hand]
        view["hand_text"] = cards_text(hand)
        view["seats"] = [{"nickname": n, "online": self.seats.get(n) is not None}
                         for n in self.seats]
        return view

    # ------------------------------------------------------------ 推送 ----

    def broadcast(self, payload: dict, droppable: bool = False) -> None:
        for seat in list(self.seats.values()):
            if seat is not None:
                seat.send(payload, droppable=droppable)
        for spectator in list(self.spectators.values()):
            spectator.send(payload, droppable=droppable)

    def _flush_events(self) -> None:
        """把引擎事件翻译成协议消息广播出去；手牌只发给本人。"""
        assert self.match is not None
        for event in self.match.drain_events():
            kind = event["type"]
            if kind == "bid_start":
                continue
            if kind == "dealt":
                # 发牌完成：把 17 张手牌分别发给本人
                for nickname, seat in self.seats.items():
                    if seat is not None:
                        self._send_hand(nickname, "game_start")
                self.broadcast(make("table_state", self.public_state()), droppable=True)
                continue
            if kind == "bid":
                self.broadcast(make("bid", {"player": event["player"], "score": event["score"]}))
            elif kind == "redeal":
                self.broadcast(make("rule", {"event": "redeal"}))
            elif kind == "match_aborted":
                self.broadcast(make("game_over", {"aborted": True, "reason": event["reason"]}))
                self.ready_set.clear()
            elif kind == "landlord_decided":
                self.broadcast(make("landlord", {
                    "landlord": event["landlord"],
                    "bid": event["bid"],
                    "bottom": [card_code(c) for c in event["bottom"]],
                    "bottom_text": cards_text(event["bottom"]),
                    "hand_counts": event["hand_counts"],
                }))
                landlord = event["landlord"]
                self._send_hand(landlord)
            elif kind == "play":
                self.broadcast(make("play", {
                    "player": event["player"],
                    "cards": [card_code(c) for c in event["cards"]],
                    "cards_text": event["cards_text"],
                    "hand_count": event["hand_count"],
                    "bomb": event["bomb"],
                    "multiplier": event["multiplier"],
                }))
            elif kind == "pass":
                self.broadcast(make("pass", {"player": event["player"]}))
            elif kind == "table_cleared":
                self.broadcast(make("table_cleared", {"leader": event["leader"]}))
            elif kind == "timeout":
                self.broadcast(make("timeout", {
                    "player": event["player"], "phase": event["phase"],
                    "action": event["action"],
                }))
            elif kind == "game_over":
                self.broadcast(make("game_over", {
                    "winner": event["winner"],
                    "landlord": event["landlord"],
                    "landlord_win": event["landlord_win"],
                    "spring": event["spring"],
                    "bombs": event["bombs"],
                    "multiplier": event["multiplier"],
                    "scores": event["scores"],
                }))
                self.ready_set.clear()

    def _send_hand(self, nickname: str, kind: str = "hand") -> None:
        seat = self.seats.get(nickname)
        if seat is None or self.match is None:
            return
        view = self.view_for(nickname)
        seat.send(make(kind, {
            "room": self.room.name,
            "hand": view.get("hand", []),
            "hand_text": view.get("hand_text", ""),
            "landlord": view.get("landlord"),
            "current": view.get("current"),
            "state": view.get("state"),
            "history_len": view.get("history_len"),
        }))
