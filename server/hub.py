"""房间、在线状态与重连预约。

约定：

* 大厅是登录后自动进入的房间，不允许退出；
* 房间广播只发给本房间成员，房间之间互相隔离；
* 成员变化会推送房间成员列表，客户端据此显示在线列表；
* 掉线后昵称不会立刻释放：进入 `reconnect_grace_ms` 的"预约"状态，
  凭 token 可以恢复昵称与房间，超时才正式注销并广播离开。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .protocol import ErrCode, error, make, now_ms
from .table import Table

if TYPE_CHECKING:  # pragma: no cover
    from .session import Session


@dataclass
class Reservation:
    """断线后为昵称与房间保留的预约。"""

    nickname: str
    room: str
    token: str
    expires_at_ms: int
    expired_reason: str = "断开超时"


class Room:
    def __init__(self, name: str, kind: str = "chat") -> None:
        self.name = name
        self.kind = kind
        self.members: dict[str, "Session"] = {}
        self.table: Table | None = None

    def add(self, session: "Session") -> None:
        self.members[session.conn_id] = session

    def remove(self, session: "Session") -> None:
        self.members.pop(session.conn_id, None)

    def nicknames(self) -> list[str]:
        return sorted(s.nickname for s in self.members.values() if s.nickname)

    def broadcast(self, payload: dict, droppable: bool = False,
                  exclude: "Session | None" = None) -> int:
        """向全体成员投递，返回实际投递人数（丢弃的会话不计入）。"""
        sent = 0
        for member in list(self.members.values()):
            if member is exclude:
                continue
            if member.send(payload, droppable=droppable):
                sent += 1
        return sent

    def snapshot(self) -> dict:
        return {"name": self.name, "kind": self.kind, "count": len(self.members)}


class Hub:
    """全局状态：房间表、连接表、昵称表、重连预约表。"""

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.rooms: dict[str, Room] = {}
        self.sessions: dict[str, "Session"] = {}
        self.by_nickname: dict[str, "Session"] = {}
        self.reservations: dict[str, Reservation] = {}   # token -> 预约
        self.reserved_names: dict[str, str] = {}         # 昵称 -> token
        self.rooms[cfg.lobby_room] = Room(cfg.lobby_room, kind="lobby")
        for name in cfg.chat_rooms:
            self.rooms[name] = Room(name, kind="chat")
        for name in cfg.table_rooms:
            room = Room(name, kind="doudizhu")
            room.table = Table(room, cfg)
            self.rooms[name] = room
        self.dropped_total = 0
        self.slow_consumer_disconnects = 0
        self.idle_timeouts = 0

    # ------------------------------------------------------------- 连接 ----

    def add_session(self, session: "Session") -> None:
        self.sessions[session.conn_id] = session

    def pending_count(self) -> int:
        return sum(1 for s in self.sessions.values() if not s.nickname)

    # ------------------------------------------------------------- 登录 ----

    def _new_token(self) -> str:
        return secrets.token_hex(16)

    def login(self, session: "Session", nickname: str) -> tuple[bool, str | None, str | None]:
        """首次登录：校验昵称、签发 token、进入大厅。"""
        nickname = nickname.strip()
        if not nickname or len(nickname) > self.cfg.max_nickname_chars:
            return False, ErrCode.INVALID_NICKNAME, (
                f"昵称需为 1~{self.cfg.max_nickname_chars} 个字符"
            )
        if any(ch.isspace() for ch in nickname):
            return False, ErrCode.INVALID_NICKNAME, "昵称不能包含空白字符"
        if nickname in self.by_nickname:
            return False, ErrCode.NICKNAME_TAKEN, "该昵称已在线"
        if nickname in self.reserved_names:
            return False, ErrCode.NICKNAME_TAKEN, "该昵称的会话正在重连中，请稍候或换一个"

        session.nickname = nickname
        session.token = self._new_token()
        self.by_nickname[nickname] = session
        self._enter_room(session, self.cfg.lobby_room)
        return True, None, None

    def resume(self, session: "Session", token: str) -> tuple[bool, str | None, str | None]:
        """凭 token 恢复会话：昵称、房间一并恢复，并换发新 token。"""
        reservation = self.reservations.get(token)
        if reservation is None:
            return False, ErrCode.SESSION_EXPIRED, "会话不存在或已失效，请重新登录"
        if reservation.expires_at_ms <= now_ms():
            self._expire_reservation(reservation)
            return False, ErrCode.SESSION_EXPIRED, "重连宽限期已过，请重新登录"
        if reservation.nickname in self.by_nickname:
            return False, ErrCode.NICKNAME_TAKEN, "该昵称已被其他连接占用"

        del self.reservations[token]
        self.reserved_names.pop(reservation.nickname, None)

        session.nickname = reservation.nickname
        session.token = self._new_token()
        self.by_nickname[reservation.nickname] = session
        room_name = reservation.room if reservation.room in self.rooms else self.cfg.lobby_room
        room = self._enter_room(session, room_name, resumed=True)
        if room.table is not None:
            room.table.on_resume(session)
        return True, None, None

    def _enter_room(self, session: "Session", room_name: str, resumed: bool = False) -> Room:
        room = self.rooms[room_name]
        room.add(session)
        session.room = room.name
        room.broadcast(
            make(
                "user_joined",
                {"nickname": session.nickname, "room": room.name, "resumed": resumed},
            )
        )
        self.broadcast_room_users(room)
        self.broadcast_room_list()
        return room

    def logout(self, session: "Session", keep_reservation: bool = True) -> None:
        """断线清理。

        keep_reservation=True 且已登录：昵称与房间进入宽限期（可凭 token 重连），
        从在线名单与房间成员中移除，并通知房间内其他人"正在重连"。
        """
        nickname = session.nickname
        room = self.rooms.get(session.room) if session.room else None

        if nickname and room is not None:
            room.remove(session)
            if room.table is not None:
                room.table.on_disconnect(session)
        if nickname and self.by_nickname.get(nickname) is session:
            del self.by_nickname[nickname]

        if nickname and keep_reservation and session.token and room is not None:
            reservation = Reservation(
                nickname=nickname,
                room=room.name,
                token=session.token,
                expires_at_ms=now_ms() + self.cfg.reconnect_grace_ms,
            )
            self.reservations[session.token] = reservation
            self.reserved_names[nickname] = session.token
            room.broadcast(
                make("user_reconnecting", {"nickname": nickname, "room": room.name,
                                           "grace_ms": self.cfg.reconnect_grace_ms}),
                droppable=True,
            )
            self.broadcast_room_users(room)
        elif nickname and room is not None:
            room.broadcast(make("user_left", {"nickname": nickname, "room": room.name}))
            self.broadcast_room_users(room)

        self.sessions.pop(session.conn_id, None)
        session.nickname = None
        session.room = None
        self.broadcast_room_list()

    # ------------------------------------------------------------- 预约 ----

    def _expire_reservation(self, reservation: Reservation) -> None:
        self.reservations.pop(reservation.token, None)
        self.reserved_names.pop(reservation.nickname, None)
        room = self.rooms.get(reservation.room)
        if room is not None:
            room.broadcast(
                make("user_left", {"nickname": reservation.nickname, "room": room.name})
            )
            self.broadcast_room_users(room)
        self.broadcast_room_list()

    def sweep_reservations(self) -> int:
        """清理过期的重连预约，返回清理数量。"""
        now = now_ms()
        expired = [r for r in self.reservations.values() if r.expires_at_ms <= now]
        for reservation in expired:
            self._expire_reservation(reservation)
        return len(expired)

    def reserved_count(self) -> int:
        return len(self.reservations)

    # ------------------------------------------------------------- 房间 ----

    def join(self, session: "Session", room_name: str) -> tuple[bool, str | None, str | None]:
        room = self.rooms.get(room_name)
        if room is None:
            return False, ErrCode.ROOM_NOT_FOUND, f"房间 {room_name} 不存在"
        if session.room == room_name:
            return False, ErrCode.ALREADY_IN_ROOM, f"你已经在 {room_name}"

        previous = self.rooms.get(session.room) if session.room else None
        if previous is not None:
            previous.remove(session)
            previous.broadcast(
                make("user_left", {"nickname": session.nickname, "room": previous.name})
            )
            self.broadcast_room_users(previous)

        self._enter_room(session, room_name)
        return True, None, None

    def leave(self, session: "Session") -> tuple[bool, str | None, str | None]:
        if session.room == self.cfg.lobby_room:
            return False, ErrCode.NOT_IN_ROOM, "大厅是默认房间，不能退出"
        return self.join(session, self.cfg.lobby_room)

    def room_of(self, session: "Session") -> Room | None:
        return self.rooms.get(session.room) if session.room else None

    def find_by_nickname(self, nickname: str) -> "Session | None":
        return self.by_nickname.get(nickname)

    # ------------------------------------------------------------- 推送 ----

    def room_list(self) -> list[dict]:
        return [room.snapshot() for room in self.rooms.values()]

    def broadcast_room_list(self) -> None:
        payload = make("room_list", {"rooms": self.room_list()})
        for session in list(self.sessions.values()):
            if session.nickname:
                session.send(payload)

    def broadcast_room_users(self, room: Room) -> None:
        room.broadcast(
            make("room_users", {"room": room.name, "users": room.nicknames()}),
            droppable=True,
        )

    def online_count(self) -> int:
        return len(self.by_nickname)

    async def close_all(self, reason: str = "server shutdown") -> int:
        """关闭所有会话（服务端退出或测试收尾时用）。"""
        sessions = list(self.sessions.values())
        for session in sessions:
            await session.close(reason, keep_reservation=False)
        return len(sessions)

    def error_for(self, session: "Session", code: str, message: str,
                  request_id: str | None = None) -> None:
        session.send(error(code, message, request_id))
