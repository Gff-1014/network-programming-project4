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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
import uuid

from .protocol import ErrCode, error, make, now_ms
from .store import Store
from .table import Table


PUBLIC_CHANNEL_ADMIN = "wanjia1"


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


@dataclass
class GroupPoll:
    """群内单选投票。"""

    poll_id: str
    title: str
    options: list[dict]
    created_by: str
    created_at_ms: int
    deadline_ms: int | None = None
    anonymous: bool = False
    votes: dict[str, str] = field(default_factory=dict)
    closed_at_ms: int | None = None

    def closed(self, now: int | None = None) -> bool:
        if self.closed_at_ms is not None:
            return True
        return self.deadline_ms is not None and (now or now_ms()) >= self.deadline_ms

    def view(self, account: str, now: int | None = None) -> dict:
        counts = {option["id"]: 0 for option in self.options}
        for option_id in self.votes.values():
            if option_id in counts:
                counts[option_id] += 1
        return {
            "poll_id": self.poll_id,
            "title": self.title,
            "options": [dict(option, count=counts[option["id"]]) for option in self.options],
            "created_by": self.created_by,
            "created_at_ms": self.created_at_ms,
            "deadline_ms": self.deadline_ms,
            "anonymous": self.anonymous,
            "closed": self.closed(now),
            "total_votes": len(self.votes),
            "my_vote": self.votes.get(account),
        }


class Room:
    def __init__(self, name: str, kind: str = "chat") -> None:
        """初始化房间：kind 区分聊天室/牌桌/群聊，members 以 conn_id 为键。"""
        self.name = name
        self.display_name = name
        self.kind = kind
        self.members: dict[str, "Session"] = {}
        self.member_accounts: set[str] = set()
        self.owner: str | None = None      # 群聊的群主账号
        self.table: Table | None = None
        self.group_id: str | None = None
        self.invite_token: str | None = None
        self.announcement: dict = {
            "text": "",
            "updated_by": "",
            "updated_at_ms": 0,
            "revision": 0,
        }
        self.polls: dict[str, GroupPoll] = {}
        self.files: dict[str, dict] = {}
        self.pending_invites: dict[str, dict] = {}

    def add(self, session: "Session") -> None:
        """把在线会话加入成员表（conn_id 作为键，断线时可精确移除）。"""
        self.members[session.conn_id] = session
        if session.nickname:
            self.member_accounts.add(session.nickname)

    def remove(self, session: "Session") -> None:
        """从成员表移除会话；重复调用是安全的。"""
        self.members.pop(session.conn_id, None)
        # 群成员关系由 group_members 持久化；退出在线会话不等于退群。
        if self.kind != "group" and session.nickname:
            self.member_accounts.discard(session.nickname)

    def nicknames(self) -> list[str]:
        """返回房间内成员的账号列表（用于在线列表推送）。"""
        return sorted(s.nickname for s in self.members.values() if s.nickname)

    def has_account(self, account: str | None) -> bool:
        """账号当前是否在房间中。"""
        return bool(account) and account in self.member_accounts

    def persistence_state(self) -> dict:
        """群状态的 SQLite 快照。"""
        return {
            "announcement": dict(self.announcement),
            "polls": [dict(poll.__dict__) for poll in self.polls.values()],
            "files": [dict(item) for item in self.files.values()],
            "pending_invites": [dict(item) for item in self.pending_invites.values()],
        }

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
        snapshot = {
            "name": self.display_name,
            "room_key": self.name,
            "kind": self.kind,
            "count": len(self.members),
            "owner": self.owner,
        }
        if self.kind == "group":
            snapshot["group_id"] = self.group_id
        return snapshot

    def group_state(self, account: str) -> dict:
        """群成员看到的群公告、投票和文件快照。"""
        now = now_ms()
        return {
            "room": self.name,
            "room_name": self.display_name,
            "group_id": self.group_id,
            "owner": self.owner,
            "invite_token": self.invite_token,
            "announcement": dict(self.announcement),
            "pending_invites": (
                [dict(item) for item in self.pending_invites.values()]
                if account == self.owner
                else []
            ),
            "polls": [
                poll.view(account, now)
                for poll in self.polls.values()
            ],
            "files": [dict(item) for item in self.files.values()],
        }


class Hub:
    """全局状态：房间表、连接表、昵称表、重连预约表。"""

    def __init__(self, cfg) -> None:
        """全局状态：房间表、会话表、账号表、重连预约与私信历史。"""
        self.cfg = cfg
        self.store = Store(cfg.db_path)
        self.store.seed_accounts(cfg.account_passwords)
        cfg.account_passwords.update(self.store.load_accounts())
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
        for name in cfg.public_channels:
            room = Room(name, kind="channel")
            room.owner = PUBLIC_CHANNEL_ADMIN
            self.rooms[name] = room
            self.store.save_room(
                room.name,
                room.display_name,
                room.kind,
                room.owner,
                None,
                None,
                {},
            )
        self.dropped_total = 0
        self.slow_consumer_disconnects = 0
        self.idle_timeouts = 0
        self._group_seq = 0
        self.private_log: dict[tuple[str, str], list[dict]] = {}
        self.invite_tokens: dict[str, str] = {}
        self._load_persisted_rooms()

    def _load_persisted_rooms(self) -> None:
        """恢复群、频道、成员和群状态。"""
        members = self.store.load_group_members()
        for row in self.store.load_rooms():
            key = row["room_key"]
            kind = row["kind"]
            if kind not in ("group", "channel"):
                continue
            room = self.rooms.get(key)
            if room is None:
                room = Room(key, kind=kind)
                self.rooms[key] = room
            room.display_name = row["display_name"] or key
            room.owner = row["owner"]
            room.group_id = row["group_id"]
            room.invite_token = row["invite_token"]
            state = row.get("state") or {}
            announcement = state.get("announcement")
            if isinstance(announcement, dict):
                room.announcement = announcement
            room.polls = {}
            for item in state.get("polls") or []:
                if isinstance(item, dict) and item.get("poll_id"):
                    room.polls[item["poll_id"]] = GroupPoll(**item)
            room.files = {
                item["file_id"]: dict(item)
                for item in state.get("files") or []
                if isinstance(item, dict) and item.get("file_id")
            }
            room.pending_invites = {
                item["account"]: dict(item)
                for item in state.get("pending_invites") or []
                if isinstance(item, dict) and item.get("account")
            }
            if kind == "group" and room.group_id:
                room.member_accounts = set(members.get(room.group_id, set()))
                if room.owner:
                    room.member_accounts.add(room.owner)
                if room.invite_token:
                    self.invite_tokens[room.invite_token] = key

    def _persist_room(self, room: Room) -> None:
        """保存群/频道的显示名和扩展状态。"""
        if room.kind not in ("group", "channel"):
            return
        self.store.save_room(
            room.name,
            room.display_name,
            room.kind,
            room.owner,
            room.group_id,
            room.invite_token,
            room.persistence_state() if room.kind == "group" else {},
        )

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
            self.store.save_account(account, password)
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
        self.store.save_account(account, password)
        return True, None, None

    # ------------------------------------------------------------- 私信 ----

    def record_private(self, sender: str, target: str, text: str, ts_ms: int) -> dict:
        """记录一条私信到 SQLite，并保留内存索引。"""
        stored = self.store.add_message(
            "private",
            sender,
            text,
            recipient=target,
        )
        key = tuple(sorted((sender, target)))
        box = self.private_log.setdefault(key, [])
        box.append({
            "message_id": stored["message_id"],
            "from": sender,
            "to": target,
            "text": text,
            "ts_ms": stored["created_at"],
        })
        if len(box) > self.cfg.max_private_history:
            del box[: len(box) - self.cfg.max_private_history]
        return stored

    def private_history(
        self,
        account: str,
        peer: str,
        before_id: int | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """取两个账号之间的私信历史（持久化、按时间正序）。"""
        rows, _, _ = self.store.private_messages(
            account,
            peer,
            before_id=before_id,
            limit=limit or self.cfg.history_page_size,
        )
        return [
            {
                "message_id": row["id"],
                "from": row["sender"],
                "to": row["recipient"],
                "text": row["text"],
                "ts_ms": row["created_at"],
            }
            for row in rows
        ]

    def private_history_page(
        self,
        account: str,
        peer: str,
        before_id: int | None = None,
        limit: int | None = None,
    ) -> tuple[list[dict], bool, int | None]:
        """返回私信分页结果。"""
        rows, has_more, next_before = self.store.private_messages(
            account,
            peer,
            before_id=before_id,
            limit=limit or self.cfg.history_page_size,
        )
        return (
            [
                {
                    "message_id": row["id"],
                    "from": row["sender"],
                    "to": row["recipient"],
                    "text": row["text"],
                    "ts_ms": row["created_at"],
                }
                for row in rows
            ],
            has_more,
            next_before,
        )

    def conversations_of(self, account: str) -> list[dict]:
        """会话摘要列表：和谁聊过、最后一条、条数与时间（按时间倒序）。"""
        return self.store.private_conversations(account)

    def record_room_message(self, room: Room, sender: str, text: str) -> dict:
        """记录房间消息并返回持久化后的元数据。"""
        return self.store.add_message(
            "room",
            sender,
            text,
            room_key=room.name,
        )

    def room_history_page(
        self,
        room: Room,
        before_id: int | None = None,
        limit: int | None = None,
    ) -> tuple[list[dict], bool, int | None]:
        """返回房间消息分页；消息体保持与协议字段一致。"""
        rows, has_more, next_before = self.store.room_messages(
            room.name,
            before_id=before_id,
            limit=limit or self.cfg.history_page_size,
        )
        return (
            [
                {
                    "message_id": row["id"],
                    "from": row["sender"],
                    "text": row["text"],
                    "ts_ms": row["created_at"],
                }
                for row in rows
            ],
            has_more,
            next_before,
        )

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
        if room.kind == "group" and room.group_id and session.nickname:
            self.store.add_group_member(room.group_id, session.nickname)
        room.broadcast(
            make(
                "user_joined",
                {
                    "nickname": session.nickname,
                    "room": room.name,
                    "room_name": room.display_name,
                    "resumed": resumed,
                },
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
                # 异常掉线保留座位等待 token 重连；主动 logout 则真正离开牌桌。
                if keep_reservation:
                    room.table.on_disconnect(session)
                else:
                    room.table.leave(session)
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
                                           "room_name": room.display_name,
                                           "grace_ms": self.cfg.reconnect_grace_ms}),
                droppable=True,
            )
            self.broadcast_room_users(room)
        elif nickname and room is not None:
            room.broadcast(make("user_left", {
                "nickname": nickname,
                "room": room.name,
                "room_name": room.display_name,
            }))
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
            if room.table is not None:
                room.table.drop_seat(reservation.nickname)
            room.broadcast(
                make("user_left", {
                    "nickname": reservation.nickname,
                    "room": room.name,
                    "room_name": room.display_name,
                })
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

    def join(
        self,
        session: "Session",
        room_name: str,
        allow_group: bool = False,
    ) -> tuple[bool, str | None, str | None]:
        """切换房间：离开原房间、加入新房间，并广播两边的成员变化。"""
        room = self.rooms.get(room_name)
        if room is None:
            return False, ErrCode.ROOM_NOT_FOUND, f"房间 {room_name} 不存在"
        if (
            room.kind == "group"
            and not allow_group
            and not room.has_account(session.nickname)
        ):
            # 不向非成员暴露群是否存在
            return False, ErrCode.ROOM_NOT_FOUND, "房间不存在"
        if session.room == room_name:
            return False, ErrCode.ALREADY_IN_ROOM, f"你已经在 {room_name}"

        previous = self.rooms.get(session.room) if session.room else None
        if previous is not None:
            previous.remove(session)
            previous.broadcast(
                make("user_left", {
                    "nickname": session.nickname,
                    "room": previous.name,
                    "room_name": previous.display_name,
                })
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

        while True:
            self._group_seq += 1
            room_name = f"群聊{self._group_seq}"
            if room_name not in self.rooms:
                break
        room = Room(room_name, kind="group")
        room.owner = owner.nickname
        room.display_name = room_name
        room.group_id = uuid.uuid4().hex
        room.invite_token = secrets.token_urlsafe(18)
        self.rooms[room_name] = room
        self.invite_tokens[room.invite_token] = room_name
        self._persist_room(room)

        self.join(owner, room_name, allow_group=True)
        for name in picked:
            member = self.by_nickname.get(name)
            if member is not None:
                self.join(member, room_name, allow_group=True)

        room.broadcast(make("group_created", {
            "room": room_name,
            "room_name": room.display_name,
            "group_id": room.group_id,
            "owner": owner.nickname,
            "members": room.nicknames(),
        }))
        self.broadcast_room_list()
        return True, None, None, room_name

    def rename_group(self, session: "Session", display_name: str) -> tuple[bool, str | None, str | None]:
        """群主修改群显示名；显示名允许重复，内部 name 不变。"""
        room = self.group_of(session)
        if room is None:
            return False, ErrCode.NOT_IN_ROOM, "请先进入一个群聊"
        if room.owner != session.nickname:
            return False, ErrCode.NOT_GROUP_OWNER, "只有群主可以修改群名"
        display_name = display_name.strip()
        if not display_name:
            return False, ErrCode.BAD_MESSAGE, "群名不能为空"
        if len(display_name) > 32:
            return False, ErrCode.BAD_MESSAGE, "群名不能超过 32 个字符"
        room.display_name = display_name
        room.broadcast(make("group_renamed", {
            "room": room.name,
            "room_name": room.display_name,
            "group_id": room.group_id,
        }))
        self._persist_room(room)
        self.broadcast_room_list()
        return True, None, None

    def create_channel(
        self,
        session: "Session",
        display_name: str,
    ) -> tuple[bool, str | None, str | None, str | None]:
        """管理员创建公共频道；频道名可以重复，内部使用独立 room_key。"""
        if session.nickname != PUBLIC_CHANNEL_ADMIN:
            return False, ErrCode.NOT_CHANNEL_ADMIN, "只有 wanjia1 可以创建公共频道", None
        display_name = display_name.strip()
        if not display_name:
            return False, ErrCode.BAD_MESSAGE, "频道名不能为空", None
        if len(display_name) > 32:
            return False, ErrCode.BAD_MESSAGE, "频道名不能超过 32 个字符", None
        room_key = f"channel:{uuid.uuid4().hex[:12]}"
        room = Room(room_key, kind="channel")
        room.display_name = display_name
        room.owner = PUBLIC_CHANNEL_ADMIN
        self.rooms[room_key] = room
        self._persist_room(room)
        self.broadcast_room_list()
        return True, None, None, room_key

    def request_join_by_invite(
        self, session: "Session", token: str
    ) -> tuple[bool, str | None, str | None, str | None, bool]:
        """扫码后申请加入群聊，等待群主审批。"""
        room_name = self.invite_tokens.get(token)
        if room_name is None:
            return False, ErrCode.GROUP_NOT_FOUND, "邀请二维码已失效", None, False
        room = self.rooms.get(room_name)
        if room is None or room.kind != "group":
            return False, ErrCode.GROUP_NOT_FOUND, "群聊不存在", None, False
        if room.has_account(session.nickname):
            return True, None, None, room_name, False
        self._queue_group_invite(
            room,
            session.nickname,
            requested_by="扫码申请",
            source="qr",
            notify_target=False,
        )
        return True, None, None, room_name, True

    def _queue_group_invite(
        self,
        room: Room,
        account: str,
        requested_by: str,
        source: str,
        notify_target: bool = True,
    ) -> None:
        """加入待群主审批的邀请队列并通知群主。"""
        item = {
            "account": account,
            "requested_by": requested_by,
            "source": source,
            "requested_at_ms": now_ms(),
        }
        room.pending_invites[account] = item
        self._persist_room(room)
        owner = self.by_nickname.get(room.owner)
        if owner is not None:
            owner.send(make("group_invite_requested", {
                "room": room.name,
                "room_name": room.display_name,
                "group_id": room.group_id,
                **item,
            }))
            self.send_group_state(owner)
        target = self.by_nickname.get(account)
        if notify_target and target is not None:
            target.send(make("group_invite_pending", {
                "room": room.name,
                "room_name": room.display_name,
                "group_id": room.group_id,
                **item,
            }))

    def invite_group_members(
        self,
        session: "Session",
        members: list[str],
    ) -> tuple[bool, str | None, str | None]:
        """群成员邀请非成员，申请由群主审批。"""
        room = self.group_of(session)
        if room is None:
            return False, ErrCode.NOT_IN_ROOM, "请先进入一个群聊"
        added = 0
        for name in members:
            if not isinstance(name, str) or not name:
                continue
            if room.has_account(name) or name == session.nickname:
                continue
            target = self.by_nickname.get(name)
            if target is None:
                return False, ErrCode.USER_NOT_FOUND, f"{name} 当前不在线"
            self._queue_group_invite(
                room,
                name,
                requested_by=session.nickname,
                source="member",
            )
            added += 1
        if added == 0:
            return False, ErrCode.BAD_MESSAGE, "请选择未入群的在线玩家"
        return True, None, None

    def approve_group_invite(
        self, session: "Session", account: str
    ) -> tuple[bool, str | None, str | None]:
        """群主批准邀请，将目标用户加入当前群。"""
        room = self.group_of(session)
        if room is None:
            return False, ErrCode.NOT_IN_ROOM, "请先进入一个群聊"
        if room.owner != session.nickname:
            return False, ErrCode.NOT_GROUP_OWNER, "只有群主可以审批入群申请"
        item = room.pending_invites.pop(account, None)
        if item is None:
            return False, ErrCode.USER_NOT_FOUND, "没有找到该入群申请"
        target = self.by_nickname.get(account)
        if target is None:
            return False, ErrCode.USER_NOT_FOUND, f"{account} 当前不在线"
        ok, code, message = self.join(target, room.name, allow_group=True)
        if not ok:
            return False, code, message
        target.send(make("joined", {
            "room": room.name,
            "room_key": room.name,
            "room_name": room.display_name,
            "users": room.nicknames(),
            "role": "player",
            "owner": room.owner,
            "group_id": room.group_id,
        }))
        messages, has_more, next_before = self.room_history_page(room)
        target.send(make("room_history", {
            "room": room.name,
            "room_name": room.display_name,
            "messages": messages,
            "has_more": has_more,
            "next_before": next_before,
        }))
        self._persist_room(room)
        self.send_group_state(target)
        owner = self.by_nickname.get(room.owner)
        if owner is not None:
            self.send_group_state(owner)
        self.broadcast_room_list()
        return True, None, None

    def reject_group_invite(
        self, session: "Session", account: str
    ) -> tuple[bool, str | None, str | None]:
        """群主拒绝入群申请。"""
        room = self.group_of(session)
        if room is None:
            return False, ErrCode.NOT_IN_ROOM, "请先进入一个群聊"
        if room.owner != session.nickname:
            return False, ErrCode.NOT_GROUP_OWNER, "只有群主可以审批入群申请"
        item = room.pending_invites.pop(account, None)
        if item is None:
            return False, ErrCode.USER_NOT_FOUND, "没有找到该入群申请"
        target = self.by_nickname.get(account)
        if target is not None:
            target.send(make("group_invite_rejected", {
                "room": room.name,
                "room_name": room.display_name,
                "group_id": room.group_id,
                "account": account,
            }))
        self._persist_room(room)
        self.send_group_state(session)
        return True, None, None

    def group_of(self, session: "Session") -> Room | None:
        room = self.room_of(session)
        return room if room is not None and room.kind == "group" else None

    def send_group_state(self, session: "Session") -> None:
        room = self.group_of(session)
        if room is not None and session.nickname:
            session.send(make("group_state", room.group_state(session.nickname)))

    def broadcast_group_state(self, room: Room) -> None:
        for member in list(room.members.values()):
            if member.nickname:
                member.send(make("group_state", room.group_state(member.nickname)))

    def delete_group(self, session: "Session", room_name: str) -> tuple[bool, str | None, str | None]:
        """解散群聊（仅群主）：成员回到大厅，房间被移除。"""
        room = self.rooms.get(room_name)
        if room is None or room.kind != "group":
            return False, ErrCode.GROUP_NOT_FOUND, "群聊不存在"
        if room.owner != session.nickname:
            return False, ErrCode.NOT_GROUP_OWNER, "只有群主可以解散群聊"

        room.broadcast(make("group_deleted", {
            "room": room_name,
            "room_name": room.display_name,
            "owner": session.nickname,
        }),
                       droppable=True)
        if room.invite_token:
            self.invite_tokens.pop(room.invite_token, None)
        for member in list(room.members.values()):
            self.join(member, self.cfg.lobby_room)
        self.rooms.pop(room_name, None)
        self.store.delete_room(room_name, room.group_id)
        self.broadcast_room_list()
        return True, None, None

    def set_announcement(self, session: "Session", text: str) -> tuple[bool, str | None, str | None]:
        room = self.group_of(session)
        if room is None:
            return False, ErrCode.NOT_IN_ROOM, "请先进入一个群聊"
        if room.owner != session.nickname:
            return False, ErrCode.NOT_GROUP_OWNER, "只有群主可以修改群公告"
        text = text.strip()
        if len(text) > 1000:
            return False, ErrCode.MESSAGE_TOO_LONG, "群公告不能超过 1000 个字符"
        room.announcement = {
            "text": text,
            "updated_by": session.nickname,
            "updated_at_ms": now_ms(),
            "revision": int(room.announcement.get("revision", 0)) + 1,
        }
        self._persist_room(room)
        room.broadcast(make("announcement_updated", {
            "room": room.name,
            "announcement": dict(room.announcement),
        }))
        return True, None, None

    def create_poll(
        self,
        session: "Session",
        title: str,
        options: list[str],
        deadline_ms: int | None = None,
        anonymous: bool = False,
    ) -> tuple[bool, str | None, str | None, GroupPoll | None]:
        room = self.group_of(session)
        if room is None:
            return False, ErrCode.NOT_IN_ROOM, "请先进入一个群聊", None
        if room.owner != session.nickname:
            return False, ErrCode.NOT_GROUP_OWNER, "只有群主可以发起群投票", None
        title = title.strip()
        cleaned = [item.strip() for item in options if isinstance(item, str) and item.strip()]
        if not title:
            return False, ErrCode.BAD_MESSAGE, "投票标题不能为空", None
        if not 2 <= len(cleaned) <= 10:
            return False, ErrCode.BAD_MESSAGE, "投票选项需要 2 到 10 个", None
        if deadline_ms is not None and deadline_ms <= now_ms():
            return False, ErrCode.BAD_MESSAGE, "投票截止时间必须晚于当前时间", None
        poll = GroupPoll(
            poll_id=secrets.token_hex(6),
            title=title,
            options=[
                {"id": f"o{index + 1}", "text": item}
                for index, item in enumerate(cleaned)
            ],
            created_by=session.nickname,
            created_at_ms=now_ms(),
            deadline_ms=deadline_ms,
            anonymous=anonymous,
        )
        room.polls[poll.poll_id] = poll
        self._persist_room(room)
        self.broadcast_group_state(room)
        return True, None, None, poll

    def vote_poll(
        self,
        session: "Session",
        poll_id: str,
        option_id: str,
    ) -> tuple[bool, str | None, str | None]:
        room = self.group_of(session)
        if room is None:
            return False, ErrCode.NOT_IN_ROOM, "请先进入一个群聊"
        poll = room.polls.get(poll_id)
        if poll is None:
            return False, ErrCode.POLL_NOT_FOUND, "投票不存在"
        if poll.closed():
            return False, ErrCode.POLL_CLOSED, "投票已经结束"
        if option_id not in {option["id"] for option in poll.options}:
            return False, ErrCode.BAD_MESSAGE, "投票选项不存在"
        poll.votes[session.nickname] = option_id
        self._persist_room(room)
        self.broadcast_group_state(room)
        return True, None, None

    def close_poll(self, session: "Session", poll_id: str) -> tuple[bool, str | None, str | None]:
        room = self.group_of(session)
        if room is None:
            return False, ErrCode.NOT_IN_ROOM, "请先进入一个群聊"
        if room.owner != session.nickname:
            return False, ErrCode.NOT_GROUP_OWNER, "只有群主可以结束投票"
        poll = room.polls.get(poll_id)
        if poll is None:
            return False, ErrCode.POLL_NOT_FOUND, "投票不存在"
        poll.closed_at_ms = now_ms()
        self._persist_room(room)
        self.broadcast_group_state(room)
        return True, None, None

    def sweep_group_polls(self) -> int:
        """把到达截止时间的投票标记为结束。"""
        now = now_ms()
        changed_rooms: list[Room] = []
        for room in self.rooms.values():
            if room.kind != "group":
                continue
            changed = False
            for poll in room.polls.values():
                if (
                    poll.closed_at_ms is None
                    and poll.deadline_ms is not None
                    and poll.deadline_ms <= now
                ):
                    poll.closed_at_ms = now
                    self._persist_room(room)
                    changed = True
            if changed:
                changed_rooms.append(room)
        for room in changed_rooms:
            self.broadcast_group_state(room)
        return len(changed_rooms)

    def register_group_file(
        self,
        session: "Session",
        group_id: str,
        file_id: str,
        name: str,
        mime: str,
        size: int,
        kind: str,
    ) -> tuple[bool, str | None, str | None]:
        room = self.group_of(session)
        if room is None or room.group_id != group_id:
            return False, ErrCode.NOT_IN_ROOM, "当前账号不在目标群聊"
        if not file_id or not name or size < 0:
            return False, ErrCode.BAD_MESSAGE, "文件元数据不合法"
        if file_id in room.files:
            return True, None, None
        room.files[file_id] = {
            "file_id": file_id,
            "name": name[:255],
            "mime": mime[:100],
            "size": size,
            "kind": "image" if kind == "image" else "file",
            "uploader": session.nickname,
            "created_at_ms": now_ms(),
        }
        self._persist_room(room)
        room.broadcast(make("group_file_added", {
            "group_id": group_id,
            **room.files[file_id],
        }))
        return True, None, None

    def delete_group_file(self, session: "Session", file_id: str) -> tuple[bool, str | None, str | None]:
        room = self.group_of(session)
        if room is None:
            return False, ErrCode.NOT_IN_ROOM, "请先进入一个群聊"
        item = room.files.get(file_id)
        if item is None:
            return False, ErrCode.FILE_NOT_FOUND, "文件不存在"
        if item["uploader"] != session.nickname and room.owner != session.nickname:
            return False, ErrCode.NOT_GROUP_OWNER, "只有上传者或群主可以删除文件"
        room.files.pop(file_id, None)
        self._persist_room(room)
        room.broadcast(make("group_file_deleted", {
            "group_id": room.group_id,
            "file_id": file_id,
        }))
        return True, None, None

    def groups_of(self, session: "Session") -> list[dict]:
        """列出所有群聊摘要（统计/调试用）。"""
        return [room.snapshot() for room in self.rooms.values() if room.kind == "group"]

    def find_by_nickname(self, nickname: str) -> "Session | None":
        """按账号查找在线会话（私信投递用）。"""
        return self.by_nickname.get(nickname)

    # ------------------------------------------------------------- 推送 ----

    def room_list(self) -> list[dict]:
        """全局房间摘要（测试和管理使用）。"""
        return [room.snapshot() for room in self.rooms.values()]

    def room_list_for(self, session: "Session") -> list[dict]:
        """按账号权限返回房间；群聊只对群成员可见。"""
        return [
            room.snapshot()
            for room in self.rooms.values()
            if room.kind != "group" or room.has_account(session.nickname)
        ]

    def broadcast_room_list(self) -> None:
        """按每个会话的群成员权限推送房间列表。"""
        for session in list(self.sessions.values()):
            if session.nickname:
                session.send(make("room_list", {
                    "rooms": self.room_list_for(session),
                }))

    def broadcast_room_users(self, room: Room) -> None:
        """把某房间的成员列表推给该房间成员。"""
        room.broadcast(
            make("room_users", {
                "room": room.name,
                "room_name": room.display_name,
                "users": room.nicknames(),
            }),
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
