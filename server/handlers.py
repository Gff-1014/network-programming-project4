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
    """拉取与某人的私信历史（SQLite，支持向前分页）。"""
    if not _require_login(session):
        return
    peer = data.get("peer")
    if not isinstance(peer, str) or not peer:
        session.send(error(ErrCode.BAD_MESSAGE, "缺少 peer", request_id))
        return
    before_id = data.get("before_id")
    if before_id is not None and not isinstance(before_id, int):
        session.send(error(ErrCode.BAD_MESSAGE, "before_id 必须是整数", request_id))
        return
    limit = data.get("limit")
    messages, has_more, next_before = session.hub.private_history_page(
        session.nickname,
        peer,
        before_id=before_id,
        limit=limit if isinstance(limit, int) else None,
    )
    session.send(make("history", {
        "peer": peer,
        "messages": messages,
        "has_more": has_more,
        "next_before": next_before,
    }, request_id))


@register("room_history")
async def on_room_history(session, data: dict, request_id: str | None) -> None:
    """分页拉取当前房间或频道的消息历史。"""
    if not _require_login(session):
        return
    room_key = data.get("room") or session.room
    room = session.hub.rooms.get(room_key) if isinstance(room_key, str) else None
    if room is None:
        session.send(error(ErrCode.ROOM_NOT_FOUND, "房间不存在", request_id))
        return
    if room.kind == "group" and not room.has_account(session.nickname):
        session.send(error(ErrCode.ROOM_NOT_FOUND, "房间不存在", request_id))
        return
    before_id = data.get("before_id")
    if before_id is not None and not isinstance(before_id, int):
        session.send(error(ErrCode.BAD_MESSAGE, "before_id 必须是整数", request_id))
        return
    messages, has_more, next_before = session.hub.room_history_page(
        room,
        before_id=before_id,
        limit=data.get("limit") if isinstance(data.get("limit"), int) else None,
    )
    session.send(make("room_history", {
        "room": room.name,
        "room_name": room.display_name,
        "messages": messages,
        "has_more": has_more,
        "next_before": next_before,
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
    session.send(make("room_list", {"rooms": session.hub.room_list_for(session)}, request_id))


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
    session.send(make("joined", {
        "room": room_name,
        "room_key": room_name,
        "room_name": room.display_name if room else room_name,
        "users": room.nicknames() if room else [],
        "role": role,
    }, request_id))
    if room is not None and room.kind == "group":
        session.hub.send_group_state(session)
    if room is not None:
        messages, has_more, next_before = session.hub.room_history_page(room)
        session.send(make("room_history", {
            "room": room.name,
            "room_name": room.display_name,
            "messages": messages,
            "has_more": has_more,
            "next_before": next_before,
        }))


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
    destination = session.hub.rooms.get(session.room) if session.room else None
    session.send(make("left", {
        "room": previous,
        "to": session.room,
        "to_name": destination.display_name if destination else session.room,
    }, request_id))


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
    session.send(make("joined", {
        "room": room_name,
        "room_key": room_name,
        "room_name": room.display_name,
        "users": room.nicknames(),
        "role": "player",
        "owner": session.nickname,
    }, request_id))
    session.hub.broadcast_group_state(room)


@register("create_channel")
async def on_create_channel(session, data: dict, request_id: str | None) -> None:
    """管理员创建公共频道。"""
    if not _require_login(session):
        return
    name = data.get("name")
    if not isinstance(name, str):
        session.send(error(ErrCode.BAD_MESSAGE, "缺少频道名", request_id))
        return
    ok, code, message, room_key = session.hub.create_channel(session, name)
    if not ok:
        session.send(error(code or ErrCode.BAD_MESSAGE, message or "创建频道失败", request_id))
        return
    room = session.hub.rooms[room_key]
    session.send(make("channel_created", {
        "room": room.name,
        "room_key": room.name,
        "room_name": room.display_name,
        "owner": room.owner,
    }, request_id))


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


@register("rename_group")
async def on_rename_group(session, data: dict, request_id: str | None) -> None:
    """群主修改群显示名，群名允许重复。"""
    if not _require_login(session):
        return
    display_name = data.get("name")
    if not isinstance(display_name, str):
        session.send(error(ErrCode.BAD_MESSAGE, "缺少群名", request_id))
        return
    ok, code, message = session.hub.rename_group(session, display_name)
    if not ok:
        session.send(error(code or ErrCode.BAD_MESSAGE, message or "修改群名失败", request_id))
        return
    session.send(make("ok", {"action": "rename_group"}, request_id))


@register("group_state")
async def on_group_state(session, data: dict, request_id: str | None) -> None:
    """获取当前群的公告、投票和文件快照。"""
    if not _require_login(session):
        return
    session.hub.send_group_state(session)


@register("join_invite")
async def on_join_invite(session, data: dict, request_id: str | None) -> None:
    """凭群二维码中的 token 加入群聊。"""
    if not _require_login(session):
        return
    token = data.get("token")
    if not isinstance(token, str) or not token:
        session.send(error(ErrCode.GROUP_NOT_FOUND, "缺少群邀请 token", request_id))
        return
    ok, code, message, room_name, pending = session.hub.request_join_by_invite(session, token)
    if not ok:
        session.send(error(code or ErrCode.GROUP_NOT_FOUND, message or "加群失败", request_id))
        return
    room = session.hub.rooms[room_name]
    if pending:
        item = room.pending_invites.get(session.nickname, {})
        session.send(make("group_invite_pending", {
            "room": room_name,
            "room_name": room.display_name,
            "group_id": room.group_id,
            **item,
        }, request_id))
        return
    session.send(make("joined", {
        "room": room_name,
        "room_key": room_name,
        "room_name": room.display_name,
        "users": room.nicknames(),
        "role": "player",
        "owner": room.owner,
        "group_id": room.group_id,
    }, request_id))
    session.hub.send_group_state(session)


@register("invite_group_member")
async def on_invite_group_member(session, data: dict, request_id: str | None) -> None:
    """群成员邀请在线非成员，申请等待群主审批。"""
    if not _require_login(session):
        return
    members = data.get("members")
    if not isinstance(members, list):
        session.send(error(ErrCode.BAD_MESSAGE, "members 必须是账号数组", request_id))
        return
    ok, code, message = session.hub.invite_group_members(session, members)
    if not ok:
        session.send(error(code or ErrCode.BAD_MESSAGE, message or "邀请失败", request_id))
        return
    session.send(make("ok", {"action": "invite_group_member"}, request_id))


@register("approve_group_invite")
async def on_approve_group_invite(session, data: dict, request_id: str | None) -> None:
    """群主批准入群申请。"""
    if not _require_login(session):
        return
    account = data.get("account")
    if not isinstance(account, str) or not account:
        session.send(error(ErrCode.BAD_MESSAGE, "缺少申请账号", request_id))
        return
    ok, code, message = session.hub.approve_group_invite(session, account)
    if not ok:
        session.send(error(code or ErrCode.BAD_MESSAGE, message or "审批失败", request_id))
        return
    session.send(make("ok", {"action": "approve_group_invite", "account": account}, request_id))


@register("reject_group_invite")
async def on_reject_group_invite(session, data: dict, request_id: str | None) -> None:
    """群主拒绝入群申请。"""
    if not _require_login(session):
        return
    account = data.get("account")
    if not isinstance(account, str) or not account:
        session.send(error(ErrCode.BAD_MESSAGE, "缺少申请账号", request_id))
        return
    ok, code, message = session.hub.reject_group_invite(session, account)
    if not ok:
        session.send(error(code or ErrCode.BAD_MESSAGE, message or "审批失败", request_id))
        return
    session.send(make("ok", {"action": "reject_group_invite", "account": account}, request_id))


@register("set_announcement")
async def on_set_announcement(session, data: dict, request_id: str | None) -> None:
    """群主更新群公告。"""
    if not _require_login(session):
        return
    text = data.get("text")
    if not isinstance(text, str):
        session.send(error(ErrCode.BAD_MESSAGE, "text 必须是字符串", request_id))
        return
    ok, code, message = session.hub.set_announcement(session, text)
    if not ok:
        session.send(error(code or ErrCode.BAD_MESSAGE, message or "公告更新失败", request_id))
        return
    session.send(make("ok", {"action": "set_announcement"}, request_id))


@register("poll_create")
async def on_poll_create(session, data: dict, request_id: str | None) -> None:
    """群主发起单选投票。"""
    if not _require_login(session):
        return
    title = data.get("title")
    options = data.get("options")
    if not isinstance(title, str) or not isinstance(options, list):
        session.send(error(ErrCode.BAD_MESSAGE, "缺少投票标题或选项", request_id))
        return
    deadline_ms = data.get("deadline_ms")
    if deadline_ms is None and isinstance(data.get("deadline_minutes"), (int, float)):
        deadline_ms = now_ms() + int(data["deadline_minutes"] * 60_000)
    if deadline_ms is not None and not isinstance(deadline_ms, int):
        session.send(error(ErrCode.BAD_MESSAGE, "deadline_ms 必须是整数", request_id))
        return
    ok, code, message, poll = session.hub.create_poll(
        session,
        title,
        options,
        deadline_ms,
        bool(data.get("anonymous", False)),
    )
    if not ok:
        session.send(error(code or ErrCode.BAD_MESSAGE, message or "创建投票失败", request_id))
        return
    session.send(make("ok", {
        "action": "poll_create",
        "poll": poll.view(session.nickname) if poll else None,
    }, request_id))


@register("poll_list")
async def on_poll_list(session, data: dict, request_id: str | None) -> None:
    """返回当前群投票列表。"""
    if not _require_login(session):
        return
    session.hub.send_group_state(session)


@register("poll_vote")
async def on_poll_vote(session, data: dict, request_id: str | None) -> None:
    """给当前群的投票投一票。"""
    if not _require_login(session):
        return
    poll_id = data.get("poll_id")
    option_id = data.get("option_id")
    if not isinstance(poll_id, str) or not isinstance(option_id, str):
        session.send(error(ErrCode.BAD_MESSAGE, "缺少 poll_id 或 option_id", request_id))
        return
    ok, code, message = session.hub.vote_poll(session, poll_id, option_id)
    if not ok:
        session.send(error(code or ErrCode.BAD_MESSAGE, message or "投票失败", request_id))
        return
    session.send(make("ok", {"action": "poll_vote"}, request_id))


@register("poll_close")
async def on_poll_close(session, data: dict, request_id: str | None) -> None:
    """群主结束投票。"""
    if not _require_login(session):
        return
    poll_id = data.get("poll_id")
    if not isinstance(poll_id, str):
        session.send(error(ErrCode.BAD_MESSAGE, "缺少 poll_id", request_id))
        return
    ok, code, message = session.hub.close_poll(session, poll_id)
    if not ok:
        session.send(error(code or ErrCode.BAD_MESSAGE, message or "结束投票失败", request_id))
        return
    session.send(make("ok", {"action": "poll_close"}, request_id))


@register("group_file_register")
async def on_group_file_register(session, data: dict, request_id: str | None) -> None:
    """登记网关已上传成功的群文件元数据。"""
    if not _require_login(session):
        return
    group_id = data.get("group_id")
    file_id = data.get("file_id")
    name = data.get("name")
    mime = data.get("mime", "application/octet-stream")
    size = data.get("size")
    kind = data.get("kind", "file")
    if not all(isinstance(item, str) and item for item in (group_id, file_id, name)):
        session.send(error(ErrCode.BAD_MESSAGE, "文件元数据字段不完整", request_id))
        return
    if not isinstance(size, int) or not isinstance(mime, str):
        session.send(error(ErrCode.BAD_MESSAGE, "文件大小或类型不合法", request_id))
        return
    ok, code, message = session.hub.register_group_file(
        session, group_id, file_id, name, mime, size, kind,
    )
    if not ok:
        session.send(error(code or ErrCode.BAD_MESSAGE, message or "文件登记失败", request_id))
        return
    session.send(make("ok", {"action": "group_file_register"}, request_id))


@register("group_file_delete")
async def on_group_file_delete(session, data: dict, request_id: str | None) -> None:
    """删除群文件元数据（文件内容由网页网关清理）。"""
    if not _require_login(session):
        return
    file_id = data.get("file_id")
    if not isinstance(file_id, str) or not file_id:
        session.send(error(ErrCode.BAD_MESSAGE, "缺少 file_id", request_id))
        return
    ok, code, message = session.hub.delete_group_file(session, file_id)
    if not ok:
        session.send(error(code or ErrCode.FILE_NOT_FOUND, message or "删除文件失败", request_id))
        return
    session.send(make("ok", {"action": "group_file_delete"}, request_id))


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

    stored = session.hub.record_room_message(
        room,
        session.nickname,
        text,
    )
    payload = make(
        "room_msg",
        {
            "room": room.name,
            "room_name": room.display_name,
            "from": session.nickname,
            "text": text,
            "message_id": stored["message_id"],
            "ts_ms": stored["created_at"],
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

    stored = session.hub.record_private(
        session.nickname,
        target,
        text,
        now_ms(),
    )
    payload = make("private_msg", {
        "from": session.nickname,
        "to": target,
        "text": text,
        "message_id": stored["message_id"],
        "ts_ms": stored["created_at"],
    })
    receiver.send(payload)
    session.send(make("private_msg", {
        "from": session.nickname,
        "to": target,
        "text": text,
        "message_id": stored["message_id"],
        "ts_ms": stored["created_at"],
        "self": True,
    }, request_id))


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
