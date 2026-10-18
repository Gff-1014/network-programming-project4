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


def _edit_distance(a: str, b: str) -> int:
    """简易编辑距离，只用来判断"是否只差一个字符"。"""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > 1:
        return 2
    if len(a) > len(b):
        a, b = b, a
    if len(a) == len(b):
        return sum(1 for x, y in zip(a, b) if x != y)
    i = j = 0
    skipped = False
    while i < len(a) and j < len(b):
        if a[i] != b[j]:
            if skipped:
                return 2
            skipped = True
            j += 1
            continue
        i += 1
        j += 1
    return 1

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
        """初始化房间：kind 区分聊天室/牌桌/群聊，members 以 conn_id 为键。"""
        self.name = name
        self.kind = kind
        self.members: dict[str, "Session"] = {}
        self.owner: str | None = None      # 群聊的群主账号
        self.table: Table | None = None

    def add(self, session: "Session") -> None:
        """把在线会话加入成员表（conn_id 作为键，断线时可精确移除）。"""
        self.members[session.conn_id] = session

    def remove(self, session: "Session") -> None:
        """从成员表移除会话；重复调用是安全的。"""
        self.members.pop(session.conn_id, None)

    def nicknames(self) -> list[str]:
        """返回房间内成员的账号列表（用于在线列表推送）。"""
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
        """房间摘要：名字/类型/人数/群主，供房间列表使用。"""
        return {"name": self.name, "kind": self.kind, "count": len(self.members),
                "owner": self.owner}


class Hub:
    """全局状态：房间表、连接表、昵称表、重连预约表。"""

    def __init__(self, cfg) -> None:
        """全局状态：房间表、会话表、账号表、重连预约与私信历史。"""
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
        self._group_seq = 0
        self.private_log: dict[tuple[str, str], list[dict]] = {}

    # ------------------------------------------------------------- 连接 ----

    def add_session(self, session: "Session") -> None:
        """登记新连接（未登录也登记，用于统计待登录连接数）。"""
        self.sessions[session.conn_id] = session

    def pending_count(self) -> int:
        """尚未完成登录的连接数（用于限制半开连接）。"""
        return sum(1 for s in self.sessions.values() if not s.nickname)

    # ------------------------------------------------------------- 登录 ----

    def _new_token(self) -> str:
        """生成 32 字节十六进制重连令牌。"""
        return secrets.token_hex(16)

    def login(self, session: "Session", account: str,
              password: str = "") -> tuple[bool, str | None, str | None]:
        """登录：校验账号与密码、签发 token、进入大厅。

        * 预置账号（见 config.account_passwords）密码必须匹配；
        * 未预置的账号：`auto_register` 打开时首次登录即注册（密码随本次输入），
          关闭时报"账号不存在"，即严格模式。
        * 同一账号只能有一个在线连接，重复登录会被拒。
        """
        account = account.strip()
        if not account or len(account) > self.cfg.max_nickname_chars:
            return False, ErrCode.INVALID_NICKNAME, (
                f"账号需为 1~{self.cfg.max_nickname_chars} 个字符"
            )
        if any(ch.isspace() for ch in account):
            return False, ErrCode.INVALID_NICKNAME, "账号不能包含空白字符"

        expected = self.cfg.account_passwords.get(account)
        if expected is None:
            if not self.cfg.auto_register:
                # 拼错一两个字母是最常见的登录失败原因，直接给出候选
                near = self._near_account(account)
                hint = f"，是不是想输入 {near}？" if near else ""
                return False, ErrCode.UNKNOWN_ACCOUNT, f"账号不存在{hint}"
            self.cfg.account_passwords[account] = password   # 首次登录即注册
        elif password != expected:
            return False, ErrCode.BAD_CREDENTIALS, "账号或密码不正确"

        if account in self.by_nickname:
            return False, ErrCode.ACCOUNT_LOGGED_IN, "该账号已在其它客户端登录"
        if account in self.reserved_names:
            return False, ErrCode.ACCOUNT_LOGGED_IN, "该账号刚刚掉线，正在重连保护中（60 秒）"

        session.nickname = account
        session.token = self._new_token()
        self.by_nickname[account] = session
        self._enter_room(session, self.cfg.lobby_room)
        return True, None, None

    def _near_account(self, account: str) -> str | None:
        """在账号表里找一个"像打错了"的候选（大小写不同或差一个字符）。"""
        lowered = account.lower()
        for known in self.cfg.account_passwords:
            if known.lower() == lowered:
                return known
        for known in self.cfg.account_passwords:
            if _edit_distance(known.lower(), lowered) <= 1:
                return known
        return None

    def online_accounts(self) -> list[str]:
        """当前所有在线账号（不区分房间）。"""
        return sorted(self.by_nickname)

    def register(self, account: str, password: str) -> tuple[bool, str | None, str | None]:
        """创建账号：只校验规则与重名，不做邮箱/验证码之类。"""
        account = account.strip()
        if not account or len(account) > self.cfg.max_nickname_chars:
            return False, ErrCode.INVALID_NICKNAME, (
                f"账号需为 1~{self.cfg.max_nickname_chars} 个字符"
            )
        if any(ch.isspace() for ch in account):
            return False, ErrCode.INVALID_NICKNAME, "账号不能包含空白字符"
        if len(password) < 4:
            return False, ErrCode.WEAK_PASSWORD, "密码至少 4 位"
        lowered = account.lower()
        if any(known.lower() == lowered for known in self.cfg.account_passwords):
            return False, ErrCode.ACCOUNT_EXISTS, "该账号已存在，请直接登录"
        self.cfg.account_passwords[account] = password
        return True, None, None

    # ------------------------------------------------------------- 私信 ----

    def record_private(self, sender: str, target: str, text: str, ts_ms: int) -> None:
        """记录一条私信（按账号对归档，只保留最近若干条）。"""
        key = tuple(sorted((sender, target)))
        box = self.private_log.setdefault(key, [])
        box.append({"from": sender, "to": target, "text": text, "ts_ms": ts_ms})
        if len(box) > self.cfg.max_private_history:
            del box[: len(box) - self.cfg.max_private_history]

    def private_history(self, account: str, peer: str) -> list[dict]:
        """取两个账号之间的私信历史（按时间正序）。"""
        return list(self.private_log.get(tuple(sorted((account, peer))), []))

    def conversations_of(self, account: str) -> list[dict]:
        """会话摘要列表：和谁聊过、最后一条、条数与时间（按时间倒序）。"""
        items: list[dict] = []
        for (a, b), messages in self.private_log.items():
            if account not in (a, b) or not messages:
                continue
            peer = b if a == account else a
            last = messages[-1]
            items.append({
                "peer": peer,
                "count": len(messages),
                "last_text": last["text"],
                "last_from": last["from"],
                "last_ts_ms": last["ts_ms"],
            })
        items.sort(key=lambda item: item["last_ts_ms"], reverse=True)
        return items

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
        """把会话放进指定房间并广播成员变化（resumed 表示重连恢复）。"""
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
        """预约过期：正式注销账号并广播离开。"""
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
        """当前处于重连保护期的账号数。"""
        return len(self.reservations)

    # ------------------------------------------------------------- 房间 ----

    def join(self, session: "Session", room_name: str) -> tuple[bool, str | None, str | None]:
        """切换房间：离开原房间、加入新房间，并广播两边的成员变化。"""
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
        """回到大厅（大厅不可退出）。"""
        if session.room == self.cfg.lobby_room:
            return False, ErrCode.NOT_IN_ROOM, "大厅是默认房间，不能退出"
        return self.join(session, self.cfg.lobby_room)

    def room_of(self, session: "Session") -> Room | None:
        """取会话当前所在房间。"""
        return self.rooms.get(session.room) if session.room else None

    # ------------------------------------------------------------- 群聊 ----

    def create_group(self, owner: "Session",
                     members: list[str]) -> tuple[bool, str | None, str | None, str | None]:
        """邀请若干在线账号建群：所有被邀请者会被直接拉进新房间。"""
        picked: list[str] = []
        for raw in members:
            name = raw.strip() if isinstance(raw, str) else ""
            if not name or name == owner.nickname or name in picked:
                continue
            if name not in self.by_nickname:
                return False, ErrCode.GROUP_MEMBER_OFFLINE, f"{name} 当前不在线", None
            picked.append(name)
        if not picked:
            return False, ErrCode.BAD_MESSAGE, "请至少选择一位在线用户", None

        self._group_seq += 1
        room_name = f"群聊{self._group_seq}"
        room = Room(room_name, kind="group")
        room.owner = owner.nickname
        self.rooms[room_name] = room

        self.join(owner, room_name)
        for name in picked:
            member = self.by_nickname.get(name)
            if member is not None:
                self.join(member, room_name)

        room.broadcast(make("group_created", {
            "room": room_name, "owner": owner.nickname, "members": room.nicknames(),
        }))
        self.broadcast_room_list()
        return True, None, None, room_name

    def delete_group(self, session: "Session", room_name: str) -> tuple[bool, str | None, str | None]:
        """解散群聊（仅群主）：成员回到大厅，房间被移除。"""
        room = self.rooms.get(room_name)
        if room is None or room.kind != "group":
            return False, ErrCode.GROUP_NOT_FOUND, "群聊不存在"
        if room.owner != session.nickname:
            return False, ErrCode.NOT_GROUP_OWNER, "只有群主可以解散群聊"

        room.broadcast(make("group_deleted", {"room": room_name, "owner": session.nickname}),
                       droppable=True)
        for member in list(room.members.values()):
            self.join(member, self.cfg.lobby_room)
        self.rooms.pop(room_name, None)
        self.broadcast_room_list()
        return True, None, None

    def groups_of(self, session: "Session") -> list[dict]:
        """列出所有群聊摘要（统计/调试用）。"""
        return [room.snapshot() for room in self.rooms.values() if room.kind == "group"]

    def find_by_nickname(self, nickname: str) -> "Session | None":
        """按账号查找在线会话（私信投递用）。"""
        return self.by_nickname.get(nickname)

    # ------------------------------------------------------------- 推送 ----

    def room_list(self) -> list[dict]:
        """房间摘要列表，客户端大厅直接展示。"""
        return [room.snapshot() for room in self.rooms.values()]

    def broadcast_room_list(self) -> None:
        """把房间列表推给所有已登录会话。"""
        payload = make("room_list", {"rooms": self.room_list()})
        for session in list(self.sessions.values()):
            if session.nickname:
                session.send(payload)

    def broadcast_room_users(self, room: Room) -> None:
        """把某房间的成员列表推给该房间成员。"""
        room.broadcast(
            make("room_users", {"room": room.name, "users": room.nicknames()}),
            droppable=True,
        )

    def online_count(self) -> int:
        """在线账号数。"""
        return len(self.by_nickname)

    async def close_all(self, reason: str = "server shutdown") -> int:
        """关闭所有会话（服务端退出或测试收尾时用）。"""
        sessions = list(self.sessions.values())
        for session in sessions:
            await session.close(reason, keep_reservation=False)
        return len(sessions)

    def error_for(self, session: "Session", code: str, message: str,
                  request_id: str | None = None) -> None:
        """给指定会话发一个错误帧（便捷方法）。"""
        session.send(error(code, message, request_id))
