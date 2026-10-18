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
        """牌桌状态：座位表（账号→会话，None 表示断线托管）、观战者与对局实例。"""
        self.room = room
        self.cfg = cfg
        self.seats: dict[str, "Session | None"] = {}      # 昵称 -> 会话（None 表示断线托管中）
        self.spectators: dict[str, "Session"] = {}        # conn_id -> 会话
        self.match: Match | None = None
        self.ready_set: set[str] = set()
        self.auto_players: set[str] = set()   # 在线托管：没掉线但交给服务端自动打
        self._last_turn: str | None = None
        self._turn_started_ms = now_ms()

    # ------------------------------------------------------------ 座位 ----

    def seat_names(self) -> list[str]:
        """按座位顺序返回账号列表。"""
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
        """根据连接找到对应座位账号；观战者返回 None。"""
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
        """离开牌桌：清座位/观战位并广播最新状态。"""
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
            self.auto_players.add(nickname)
            self.room.broadcast(
                make("table_state", self.public_state() | {"auto": nickname}), droppable=True
            )
        self.spectators.pop(session.conn_id, None)

    def on_resume(self, session: "Session") -> bool:
        """玩家重连：把会话重新绑定到座位，并补发私有对局快照。"""
        nickname = session.nickname
        if nickname not in self.seats:
            return False
        self.seats[nickname] = session
        self.auto_players.discard(nickname)   # 回来即取回控制
        if self.match is not None:
            session.send(make("game_state", self.view_for(nickname)))
        return True

    # ------------------------------------------------------------ 对局 ----

    def _ensure_match(self) -> None:
        """座位坐满时才创建对局；开局由 ready() 在三人全部准备后触发。"""
        if self.match is None and len(self.seats) == self.cfg.seats_per_table:
            # 引擎层不设超时：真人出牌不限时，AI/托管由本类的 tick() 按 auto_play_delay_ms 驱动
            self.match = Match(
                list(self.seats),
                seed=None,
                config=MatchConfig(bid_timeout_ms=0, play_timeout_ms=0),
            )
            self._last_turn = None

    def ready(self, session: "Session") -> Action:
        """准备：三人齐且都准备时开局；上一局已结算则清空后重开。"""
        nickname = self.session_of(session)
        if nickname is None:
            return Action(False, Err.BAD_STATE, "你不在牌桌座位上")
        # 上一局已结算：清掉旧对局，等三家重新准备再开新一局
        if self.match is not None and self.match.state is State.SETTLED:
            self.match = None
            self.ready_set = set()
            self._last_turn = None
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

    def set_auto(self, session: "Session", enabled: bool) -> Action:
        """在线托管开关：托管期间服务端替这位玩家决策，随时可取回。"""
        nickname = self.session_of(session)
        if nickname is None:
            return Action(False, Err.BAD_STATE, "你不在牌桌座位上")
        if enabled:
            self.auto_players.add(nickname)
        else:
            self.auto_players.discard(nickname)
        self.broadcast(make("table_state", self.public_state()), droppable=True)
        return Action(True)

    def bid(self, session: "Session", score: int, request_id: str | None) -> Action:
        """叫分：交给引擎判定，并把产生的事件翻译广播。"""
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
        """出牌：把协议里的 '3s' 编码还原成整数牌，交给引擎校验。"""
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
        """过牌：交给引擎判定并广播结果。"""
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
            auto = seated is None or current in self.auto_players
            # 只有"托管/掉线"才由服务端代打；真人自由出牌，不设时限
            if auto and now - self._turn_started_ms >= self.cfg.auto_play_delay_ms:
                self.match.timeout()
                self._flush_events()
                self.broadcast(make("table_state", self.public_state()), droppable=True)

    # ------------------------------------------------------------ 视图 ----

    def public_state(self) -> dict:
        """公开视图：状态、轮次、各家张数、桌面牌、倍数与剩余时间（观战用）。"""
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
        # 客户端不共享服务端时钟：托管席位给出"还有多久自动出牌"，真人席位不限时
        current = view.get("current")
        auto = current in self.auto_players or (
            current is not None and self.seats.get(current) is None
        )
        if auto and self.match.state in (State.BIDDING, State.PLAYING):
            elapsed = now_ms() - self._turn_started_ms
            view["deadline_in_ms"] = max(0, self.cfg.auto_play_delay_ms - elapsed)
        else:
            view["deadline_in_ms"] = None
        view["room"] = self.room.name
        view["seats"] = [{"nickname": n, "online": self.seats.get(n) is not None}
                         for n in self.seats]
        view["auto_players"] = sorted(self.auto_players)
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
        """把一帧发给本桌所有玩家与观战者。"""
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
        """给某个玩家发私有手牌（game_start=发牌，hand=拿底牌）。"""
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
