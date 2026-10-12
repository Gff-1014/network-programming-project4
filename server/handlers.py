"""M0 消息处理器：登录、房间、广播、私信、心跳。

新增一种消息类型 = 在 protocol.C2S_TYPES 里加名字 + 这里加一个 `@register` 函数。
"""

from __future__ import annotations

from .protocol import ErrCode, error, make, register


def _require_login(session) -> bool:
    if session.nickname is None:
        session.send(error(ErrCode.NOT_LOGGED_IN, "请先登录"))
        return False
    return True


def _text(data: dict, key: str = "text") -> str:
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


@register("login")
async def on_login(session, data: dict, request_id: str | None) -> None:
    if session.nickname is not None:
        session.send(error(ErrCode.BAD_MESSAGE, "你已经登录", request_id))
        return
    nickname = data.get("nickname")
    if not isinstance(nickname, str):
        session.send(error(ErrCode.INVALID_NICKNAME, "缺少 nickname", request_id))
        return

    ok, code, message = session.hub.login(session, nickname)
    if not ok:
        session.send(error(code or ErrCode.INVALID_NICKNAME, message or "登录失败", request_id))
        return

    session.send(session.welcome_payload(request_id))
    room = session.hub.room_of(session)
    if room is not None:
        session.hub.broadcast_room_users(room)
        room.broadcast(
            make("user_joined", {"nickname": session.nickname, "room": room.name}),
            exclude=session,
        )
    session.hub.broadcast_room_list()


@register("logout")
async def on_logout(session, data: dict, request_id: str | None) -> None:
    session.send(make("left", {"reason": "logout"}, request_id))
    await session.close("logout", keep_reservation=False)


@register("resume")
async def on_resume(session, data: dict, request_id: str | None) -> None:
    """凭 token 恢复会话（握手阶段的第二个允许类型）。"""
    if session.nickname is not None:
        session.send(error(ErrCode.ALREADY_LOGGED_IN, "当前连接已登录", request_id))
        return
    token = data.get("token")
    if not isinstance(token, str) or not token:
        session.send(error(ErrCode.SESSION_EXPIRED, "缺少 token", request_id))
        await session.close("resume without token", keep_reservation=False)
        return

    ok, code, message = session.hub.resume(session, token)
    if not ok:
        session.send(error(code or ErrCode.SESSION_EXPIRED, message or "恢复失败", request_id))
        await session.close("resume failed", keep_reservation=False)
        return

    session.send(session.resumed_payload(request_id))
    session.hub.broadcast_room_list()


@register("pong")
async def on_pong(session, data: dict, request_id: str | None) -> None:
    """客户端对服务端 ping 的回应；last_seen 已在读循环里刷新，这里无需额外处理。"""
    return


@register("ping")
async def on_ping(session, data: dict, request_id: str | None) -> None:
    session.send(make("pong", {"echo": data.get("echo")}, request_id))


@register("list_rooms")
async def on_list_rooms(session, data: dict, request_id: str | None) -> None:
    session.send(make("room_list", {"rooms": session.hub.room_list()}, request_id))


@register("join_room")
async def on_join_room(session, data: dict, request_id: str | None) -> None:
    if not _require_login(session):
        return
    room_name = data.get("room")
    if not isinstance(room_name, str):
        session.send(error(ErrCode.ROOM_NOT_FOUND, "缺少 room", request_id))
        return
    ok, code, message = session.hub.join(session, room_name)
    if not ok:
        session.send(error(code or ErrCode.ROOM_NOT_FOUND, message or "加入失败", request_id))
        return
    room = session.hub.room_of(session)
    session.send(make("joined", {"room": room_name, "users": room.nicknames() if room else []},
                      request_id))


@register("leave_room")
async def on_leave_room(session, data: dict, request_id: str | None) -> None:
    if not _require_login(session):
        return
    previous = session.room
    ok, code, message = session.hub.leave(session)
    if not ok:
        session.send(error(code or ErrCode.NOT_IN_ROOM, message or "退出失败", request_id))
        return
    session.send(make("left", {"room": previous, "to": session.room}, request_id))


@register("room_msg")
async def on_room_msg(session, data: dict, request_id: str | None) -> None:
    if not _require_login(session):
        return
    text = _text(data)
    if not text:
        session.send(error(ErrCode.BAD_MESSAGE, "消息不能为空", request_id))
        return
    if len(text) > session.cfg.max_text_chars:
        session.send(
            error(
                ErrCode.MESSAGE_TOO_LONG,
                f"消息超过 {session.cfg.max_text_chars} 字符",
                request_id,
            )
        )
        return
    room = session.hub.room_of(session)
    if room is None:
        session.send(error(ErrCode.NOT_IN_ROOM, "你不在任何房间", request_id))
        return

    payload = make(
        "room_msg",
        {
            "room": room.name,
            "from": session.nickname,
            "text": text,
        },
    )
    delivered = room.broadcast(payload, droppable=True)
    session.send(make("room_msg_ack", {"delivered": delivered, "room": room.name}, request_id))


@register("private_msg")
async def on_private_msg(session, data: dict, request_id: str | None) -> None:
    if not _require_login(session):
        return
    target = data.get("to")
    text = _text(data)
    if not isinstance(target, str) or not target:
        session.send(error(ErrCode.USER_NOT_FOUND, "缺少收件人", request_id))
        return
    if not text:
        session.send(error(ErrCode.BAD_MESSAGE, "消息不能为空", request_id))
        return
    if len(text) > session.cfg.max_text_chars:
        session.send(
            error(ErrCode.MESSAGE_TOO_LONG, f"消息超过 {session.cfg.max_text_chars} 字符", request_id)
        )
        return

    receiver = session.hub.find_by_nickname(target)
    if receiver is None:
        session.send(error(ErrCode.USER_NOT_FOUND, f"{target} 不在线", request_id))
        return

    payload = make("private_msg", {"from": session.nickname, "to": target, "text": text})
    receiver.send(payload)
    session.send(make("private_msg", {"from": session.nickname, "to": target, "text": text,
                                     "self": True}, request_id))
