"""消息类型、错误码与处理器注册表。

消息信封：

    客户端 → 服务端  {"type": "login", "request_id": "...", "data": {...}}
    服务端 → 客户端  {"type": "welcome", "request_id": "...", "data": {...}, "ts_ms": 123}

处理器用装饰器注册，新增一种消息类型只需加一个函数（现场修改演示用）。
"""

from __future__ import annotations

import time
from typing import Awaitable, Callable

# ---------------------------------------------------------------- 消息类型 ----

# 客户端 → 服务端
C2S_TYPES = (
    "login",
    "register",
    "resume",
    "pong",
    "logout",
    "ping",
    "list_rooms",
    "list_users",
    "list_conversations",
    "get_history",
    "room_history",
    "group_state",
    "join_invite",
    "invite_group_member",
    "approve_group_invite",
    "reject_group_invite",
    "join_room",
    "leave_room",
    "create_group",
    "create_channel",
    "delete_group",
    "rename_group",
    "set_announcement",
    "poll_create",
    "poll_list",
    "poll_vote",
    "poll_close",
    "group_file_register",
    "group_file_delete",
    "room_ready",
    "bid",
    "play",
    "pass",
    "spectate",
    "game_state",
    "auto_play",
    "room_msg",
    "private_msg",
)

# 服务端 → 客户端推送
S2C_TYPES = (
    "welcome",
    "resumed",
    "error",
    "ping",
    "pong",
    "room_list",
    "user_list",
    "conversations",
    "history",
    "room_history",
    "joined",
    "left",
    "room_users",
    "room_msg_ack",
    "user_joined",
    "user_left",
    "user_reconnecting",
    "group_created",
    "channel_created",
    "group_deleted",
    "group_renamed",
    "group_state",
    "group_invite_requested",
    "group_invite_pending",
    "group_invite_rejected",
    "announcement_updated",
    "poll_created",
    "poll_updated",
    "poll_closed",
    "group_file_added",
    "group_file_deleted",
    "logged_out",
    "room_msg",
    "private_msg",
    "game_start",
    "game_state",
    "bid",
    "play",
    "pass",
    "game_over",
    "table_state",
    "spectating",
)

SUPPORTED_TYPES = tuple(dict.fromkeys(C2S_TYPES))  # 未知类型提示里列出的可发送类型


# ------------------------------------------------------------------ 错误码 ----

class ErrCode(str):
    BAD_FRAME = "BAD_FRAME"
    UNKNOWN_TYPE = "UNKNOWN_TYPE"
    NOT_LOGGED_IN = "NOT_LOGGED_IN"
    NICKNAME_TAKEN = "NICKNAME_TAKEN"
    INVALID_NICKNAME = "INVALID_NICKNAME"
    ROOM_NOT_FOUND = "ROOM_NOT_FOUND"
    ALREADY_IN_ROOM = "ALREADY_IN_ROOM"
    NOT_IN_ROOM = "NOT_IN_ROOM"
    USER_NOT_FOUND = "USER_NOT_FOUND"
    MESSAGE_TOO_LONG = "MESSAGE_TOO_LONG"
    HANDSHAKE_TIMEOUT = "HANDSHAKE_TIMEOUT"
    BAD_MESSAGE = "BAD_MESSAGE"
    TOO_MANY_BAD_FRAMES = "TOO_MANY_BAD_FRAMES"
    SLOW_CONSUMER = "SLOW_CONSUMER"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    ALREADY_LOGGED_IN = "ALREADY_LOGGED_IN"
    ROOM_FULL = "ROOM_FULL"
    NOT_SEATED = "NOT_SEATED"
    MATCH_NOT_STARTED = "MATCH_NOT_STARTED"
    INVALID_BID = "INVALID_BID"
    INTERNAL = "INTERNAL"
    UNKNOWN_ACCOUNT = "UNKNOWN_ACCOUNT"
    BAD_CREDENTIALS = "BAD_CREDENTIALS"
    ACCOUNT_LOGGED_IN = "ACCOUNT_LOGGED_IN"
    ACCOUNT_EXISTS = "ACCOUNT_EXISTS"
    WEAK_PASSWORD = "WEAK_PASSWORD"
    GROUP_NOT_FOUND = "GROUP_NOT_FOUND"
    NOT_GROUP_OWNER = "NOT_GROUP_OWNER"
    GROUP_MEMBER_OFFLINE = "GROUP_MEMBER_OFFLINE"
    NOT_CHANNEL_ADMIN = "NOT_CHANNEL_ADMIN"
    POLL_NOT_FOUND = "POLL_NOT_FOUND"
    POLL_CLOSED = "POLL_CLOSED"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"


# ------------------------------------------------------------------ 构造函数 ----

def now_ms() -> int:
    return int(time.monotonic() * 1000)


def make(msg_type: str, data: dict | None = None, request_id: str | None = None) -> dict:
    payload: dict = {"type": msg_type, "data": data or {}}
    if request_id:
        payload["request_id"] = request_id
    payload["ts_ms"] = now_ms()
    return payload


def error(code: str, message: str, request_id: str | None = None, **extra) -> dict:
    data = {"code": code, "message": message}
    data.update(extra)
    return make("error", data, request_id)


# ------------------------------------------------------------------ 注册表 ----

Handler = Callable[..., Awaitable[None]]
_HANDLERS: dict[str, Handler] = {}


def register(msg_type: str) -> Callable[[Handler], Handler]:
    """把处理器登记到注册表；重复注册会覆盖（便于热改）。"""

    def decorator(func: Handler) -> Handler:
        _HANDLERS[msg_type] = func
        return func

    return decorator


def get_handler(msg_type: str) -> Handler | None:
    return _HANDLERS.get(msg_type)


def registered_types() -> list[str]:
    return sorted(_HANDLERS)
