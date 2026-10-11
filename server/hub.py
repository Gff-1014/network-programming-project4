"""房间与在线状态（大厅 + 聊天房）。

约定：

* 大厅是登录后自动进入的房间，不允许退出；
* 房间广播只发给本房间成员，房间之间互相隔离；
* 所有状态变更都会推送房间成员列表，客户端据此显示在线列表。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .protocol import ErrCode, error, make

if TYPE_CHECKING:  # pragma: no cover
    from .session import Session


class Room:
    def __init__(self, name: str, kind: str = "chat") -> None:
        self.name = name
        self.kind = kind
        self.members: dict[str, "Session"] = {}

    def add(self, session: "Session") -> None:
        self.members[session.conn_id] = session

    def remove(self, session: "Session") -> None:
        self.members.pop(session.conn_id, None)

    def nicknames(self) -> list[str]:
        return sorted(s.nickname for s in self.members.values() if s.nickname)

    def broadcast(self, payload: dict, droppable: bool = False,
                  exclude: "Session | None" = None) -> int:
        """向全体成员投递，返回实际投递人数。"""
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
    """全局状态：房间表、连接表、昵称表。"""

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.rooms: dict[str, Room] = {}
        self.sessions: dict[str, "Session"] = {}
        self.by_nickname: dict[str, "Session"] = {}
        self.rooms[cfg.lobby_room] = Room(cfg.lobby_room, kind="lobby")
        for name in cfg.chat_rooms:
            self.rooms[name] = Room(name, kind="chat")
        self.dropped_total = 0

    # ------------------------------------------------------------- 连接 ----

    def add_session(self, session: "Session") -> None:
        self.sessions[session.conn_id] = session
        self.broadcast_room_list()

    def pending_count(self) -> int:
        return sum(1 for s in self.sessions.values() if not s.nickname)

    # ------------------------------------------------------------- 登录 ----

    def login(self, session: "Session", nickname: str) -> tuple[bool, str | None, str | None]:
        """登录：校验昵称并加入大厅。返回 (是否成功, 错误码, 错误说明)。"""
        nickname = nickname.strip()
        if not nickname or len(nickname) > self.cfg.max_nickname_chars:
            return False, ErrCode.INVALID_NICKNAME, (
                f"昵称需为 1~{self.cfg.max_nickname_chars} 个字符"
            )
        if any(ch.isspace() for ch in nickname):
            return False, ErrCode.INVALID_NICKNAME, "昵称不能包含空白字符"
        if nickname in self.by_nickname:
            return False, ErrCode.NICKNAME_TAKEN, "该昵称已在线"

        session.nickname = nickname
        self.by_nickname[nickname] = session
        lobby = self.rooms[self.cfg.lobby_room]
        lobby.add(session)
        session.room = lobby.name
        return True, None, None

    def logout(self, session: "Session") -> None:
        """断线清理：把会话从所有表中摘掉（可重复调用）。"""
        if session.room and session.room in self.rooms:
            room = self.rooms[session.room]
            room.remove(session)
            if session.nickname:
                room.broadcast(
                    make("user_left", {"nickname": session.nickname, "room": room.name})
                )
                self.broadcast_room_users(room)
        if session.nickname and self.by_nickname.get(session.nickname) is session:
            del self.by_nickname[session.nickname]
            session.nickname = None
        self.sessions.pop(session.conn_id, None)
        session.room = None
        self.broadcast_room_list()

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

        room.add(session)
        session.room = room.name
        room.broadcast(
            make("user_joined", {"nickname": session.nickname, "room": room.name})
        )
        self.broadcast_room_users(room)
        self.broadcast_room_list()
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
            await session.close(reason)
        return len(sessions)

    def error_for(self, session: "Session", code: str, message: str,
                  request_id: str | None = None) -> None:
        session.send(error(code, message, request_id))
