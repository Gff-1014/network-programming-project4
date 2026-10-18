"""M0 消息处理器：登录、房间、广播、私信、心跳。

新增一种消息类型 = 在 protocol.C2S_TYPES 里加名字 + 这里加一个 `@register` 函数。
"""

from __future__ import annotations

from .protocol import ErrCode, error, make, now_ms, register


def _require_login(session) -> bool:
    """未登录时回错误并返回 False，供各处理器统一复用。"""
    if session.nickname is None:
        session.send(error(ErrCode.NOT_LOGGED_IN, "请先登录"))
        return False
    return True


def _text(data: dict, key: str = "text") -> str:
    """取并裁剪字符串字段，缺省返回空串。"""
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


def _action_error(session, action, request_id: str | None) -> None:
    """把引擎返回的 Action 翻译成 error 帧。"""
    code = getattr(action.code, "value", action.code) or ErrCode.BAD_MESSAGE
    session.send(error(code, action.detail or "操作被拒绝", request_id))


def _table_of(session):
    """取会话所在牌桌；不在牌桌返回 None。"""
    room = session.hub.room_of(session)
    return room.table if room is not None else None


@register("login")
async def on_login(session, data: dict, request_id: str | None) -> None:
    """登录：账号密码交给 Hub 校验，成功后下发 welcome 并广播在线变化。"""
    if session.nickname is not None:
        session.send(error(ErrCode.BAD_MESSAGE, "你已经登录", request_id))
        return
    account = data.get("account") or data.get("nickname")
    password = data.get("password", "")
    if not isinstance(account, str) or not isinstance(password, str):
        session.send(error(ErrCode.INVALID_NICKNAME, "缺少 account 或 password", request_id))
        return

    ok, code, message = session.hub.login(session, account, password)
    if not ok:
        session.send(error(code or ErrCode.BAD_CREDENTIALS, message or "登录失败", request_id))
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


@register("register")
async def on_register(session, data: dict, request_id: str | None) -> None:
    """创建账号：输入账号与密码即可，成功后直接登录进大厅。"""
    if session.nickname is not None:
        session.send(error(ErrCode.BAD_MESSAGE, "你已经登录", request_id))
        return
    account = data.get("account")
    password = data.get("password", "")
    if not isinstance(account, str) or not isinstance(password, str):
        session.send(error(ErrCode.INVALID_NICKNAME, "缺少 account 或 password", request_id))
        return
    ok, code, message = session.hub.register(account, password)
    if not ok:
        session.send(error(code or ErrCode.INVALID_NICKNAME, message or "创建失败", request_id))
        return
    # 创建成功直接走登录流程（welcome 会带同一个 request_id 返回，客户端按登录处理）
    await on_login(session, {"account": account, "password": password}, request_id)


@register("list_conversations")
async def on_list_conversations(session, data: dict, request_id: str | None) -> None:
    """返回当前账号的私信会话摘要。"""
    if not _require_login(session):
        return
    session.send(make("conversations",
                      {"items": session.hub.conversations_of(session.nickname)}, request_id))


@register("get_history")
async def on_get_history(session, data: dict, request_id: str | None) -> None:
    """拉取与某人的私信历史（仅内存，服务端重启即清空）。"""
    if not _require_login(session):
        return
    peer = data.get("peer")
    if not isinstance(peer, str) or not peer:
        session.send(error(ErrCode.BAD_MESSAGE, "缺少 peer", request_id))
        return
    session.send(make("history", {
        "peer": peer,
        "messages": session.hub.private_history(session.nickname, peer),
    }, request_id))


@register("logout")
async def on_logout(session, data: dict, request_id: str | None) -> None:
    """主动登出：先回执再断开（主动退出不保留重连预约）。"""
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
    """应用层心跳：回 pong 并带回 echo。"""
    session.send(make("pong", {"echo": data.get("echo")}, request_id))


@register("list_rooms")
async def on_list_rooms(session, data: dict, request_id: str | None) -> None:
    """返回房间列表。"""
    session.send(make("room_list", {"rooms": session.hub.room_list()}, request_id))


@register("list_users")
async def on_list_users(session, data: dict, request_id: str | None) -> None:
    """拉取在线玩家：既有本房间成员，也有全服在线账号（大厅的"刷新在线玩家"用）。"""
    room = session.hub.room_of(session)
    session.send(make("user_list", {
        "room": session.room,
        "users": room.nicknames() if room else [],
        "online": session.hub.online_accounts(),
    }, request_id))


@register("join_room")
async def on_join_room(session, data: dict, request_id: str | None) -> None:
    """加入房间：牌桌先做容量检查，再把角色（player/spectator）回给客户端。"""
    if not _require_login(session):
        return
    room_name = data.get("room")
    if not isinstance(room_name, str):
        session.send(error(ErrCode.ROOM_NOT_FOUND, "缺少 room", request_id))
        return
    target = session.hub.rooms.get(room_name)
    table = target.table if target is not None else None
    if table is not None:
        ok, code, message = table.can_join(session)
        if not ok:
            session.send(error(code or ErrCode.ROOM_FULL, message or "牌桌已满", request_id))
            return
    ok, code, message = session.hub.join(session, room_name)
    if not ok:
        session.send(error(code or ErrCode.ROOM_NOT_FOUND, message or "加入失败", request_id))
        return
    room = session.hub.room_of(session)
    role = "player"
    if table is not None:
        ok, code, message = table.join(session)
        if not ok:
            session.send(error(code or ErrCode.ROOM_FULL, message or "牌桌已满", request_id))
            return
        role = "player" if session.nickname in table.seats else "spectator"
        session.send(make("table_state", table.public_state()))
    session.send(make("joined", {"room": room_name, "users": room.nicknames() if room else [],
                                 "role": role}, request_id))


@register("leave_room")
async def on_leave_room(session, data: dict, request_id: str | None) -> None:
    """离开当前房间，回到大厅。"""
    if not _require_login(session):
        return
    previous = session.room
    table = _table_of(session)
    if table is not None:
        table.leave(session)
    ok, code, message = session.hub.leave(session)
    if not ok:
        session.send(error(code or ErrCode.NOT_IN_ROOM, message or "退出失败", request_id))
        return
    session.send(make("left", {"room": previous, "to": session.room}, request_id))


@register("create_group")
async def on_create_group(session, data: dict, request_id: str | None) -> None:
    """邀请在线用户创建群聊（被邀请者会被直接拉进群）。"""
    if not _require_login(session):
        return
    members = data.get("members")
    if not isinstance(members, list):
        session.send(error(ErrCode.BAD_MESSAGE, "members 必须是账号数组", request_id))
        return
    ok, code, message, room_name = session.hub.create_group(session, members)
    if not ok:
        session.send(error(code or ErrCode.BAD_MESSAGE, message or "建群失败", request_id))
        return
    room = session.hub.rooms[room_name]
    session.send(make("joined", {"room": room_name, "users": room.nicknames(),
                                 "role": "player", "owner": session.nickname}, request_id))


@register("delete_group")
async def on_delete_group(session, data: dict, request_id: str | None) -> None:
    """解散群聊（仅群主）。"""
    if not _require_login(session):
        return
    room_name = data.get("room") or session.room
    if not isinstance(room_name, str):
        session.send(error(ErrCode.GROUP_NOT_FOUND, "缺少 room", request_id))
        return
    ok, code, message = session.hub.delete_group(session, room_name)
    if not ok:
        session.send(error(code or ErrCode.GROUP_NOT_FOUND, message or "解散失败", request_id))
        return
    session.send(make("left", {"room": room_name, "to": session.room}, request_id))


@register("room_msg")
async def on_room_msg(session, data: dict, request_id: str | None) -> None:
    """房间聊天：广播给房间成员并给发送者回执。"""
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
    """私信：投递给目标并记录到会话历史。"""
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
    session.hub.record_private(session.nickname, target, text, now_ms())
    session.send(make("private_msg", {"from": session.nickname, "to": target, "text": text,
                                     "self": True}, request_id))


# ------------------------------------------------------------ 牌桌消息 ----


@register("room_ready")
async def on_room_ready(session, data: dict, request_id: str | None) -> None:
    """牌桌准备：三人齐且都准备时开局。"""
    table = _table_of(session)
    if table is None:
        session.send(error(ErrCode.BAD_STATE, "当前房间不是牌桌", request_id))
        return
    action = table.ready(session)
    if not action.ok:
        _action_error(session, action, request_id)
        return
    session.send(make("ok", {"action": "ready"}, request_id))


@register("bid")
async def on_bid(session, data: dict, request_id: str | None) -> None:
    """叫分：0 表示不叫，1~3 必须高于当前最高分。"""
    table = _table_of(session)
    if table is None:
        session.send(error(ErrCode.BAD_STATE, "当前房间不是牌桌", request_id))
        return
    score = data.get("score")
    if not isinstance(score, int) or score not in (0, 1, 2, 3):
        session.send(error(ErrCode.INVALID_BID, "叫分必须是 0/1/2/3", request_id))
        return
    action = table.bid(session, score, request_id)
    if not action.ok:
        _action_error(session, action, request_id)
        return
    session.send(make("ok", {"action": "bid", "score": score}, request_id))


@register("play")
async def on_play(session, data: dict, request_id: str | None) -> None:
    """出牌：cards 使用 '3s'/'BJ' 这类编码。"""
    table = _table_of(session)
    if table is None:
        session.send(error(ErrCode.BAD_STATE, "当前房间不是牌桌", request_id))
        return
    cards = data.get("cards")
    if not isinstance(cards, list) or not cards or not all(isinstance(c, str) for c in cards):
        session.send(error(ErrCode.BAD_MESSAGE, "cards 必须是非空字符串数组", request_id))
        return
    action = table.play(session, cards, request_id)
    if not action.ok:
        _action_error(session, action, request_id)
        return
    session.send(make("ok", {"action": "play", "cards": cards}, request_id))


@register("pass")
async def on_pass(session, data: dict, request_id: str | None) -> None:
    """过牌。"""
    table = _table_of(session)
    if table is None:
        session.send(error(ErrCode.BAD_STATE, "当前房间不是牌桌", request_id))
        return
    action = table.pass_turn(session, request_id)
    if not action.ok:
        _action_error(session, action, request_id)
        return
    session.send(make("ok", {"action": "pass"}, request_id))


@register("game_state")
async def on_game_state(session, data: dict, request_id: str | None) -> None:
    """请求牌桌快照：玩家拿自己的手牌，观战者只拿公开信息。"""
    table = _table_of(session)
    if table is None or table.match is None:
        session.send(error(ErrCode.MATCH_NOT_STARTED, "牌桌还没有开始对局", request_id))
        return
    if session.nickname in table.seats:
        session.send(make("game_state", table.view_for(session.nickname), request_id))
    else:
        session.send(make("table_state", table.public_state(), request_id))


@register("spectate")
async def on_spectate(session, data: dict, request_id: str | None) -> None:
    """观战：加入牌桌但不占座位（座位未满时会提示直接入座）。"""
    room_name = data.get("room")
    if not isinstance(room_name, str) or room_name not in session.hub.rooms:
        session.send(error(ErrCode.ROOM_NOT_FOUND, "房间不存在", request_id))
        return
    room = session.hub.rooms[room_name]
    if room.table is None:
        session.send(error(ErrCode.BAD_STATE, "该房间不是牌桌", request_id))
        return
    if len(room.table.seats) < room.table.cfg.seats_per_table:
        session.send(error(ErrCode.BAD_STATE, "牌桌还有空位，请直接加入房间入座", request_id))
        return
    await on_join_room(session, {"room": room_name}, request_id)


@register("auto_play")
async def on_auto_play(session, data: dict, request_id: str | None) -> None:
    """在线托管开关：不下线也能把这一局交给服务端自动打，随时可以取回。"""
    table = _table_of(session)
    if table is None:
        session.send(error(ErrCode.BAD_STATE, "当前房间不是牌桌", request_id))
        return
    enabled = bool(data.get("enabled", True))
    action = table.set_auto(session, enabled)
    if not action.ok:
        _action_error(session, action, request_id)
        return
    session.send(make("ok", {"action": "auto_play", "enabled": enabled}, request_id))
