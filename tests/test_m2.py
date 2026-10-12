"""M2：牌桌协议、状态裁决、观战、断线托管与重连恢复。"""

from __future__ import annotations

import asyncio

import pytest

from bots.doudizhu_bot import DoudizhuBot
from client.net import Connection, ServerError
from client.session import ClientSession
from server.protocol import ErrCode

ROOM = "牌桌1"


async def make_seated(server, names, room=ROOM):
    """登录并坐上牌桌，返回 (连接, 机器人) 列表与角色信息。"""
    conns, roles = [], []
    for name in names:
        conn = await Connection.connect(server.host, server.port)
        await conn.login(name)
        reply = await conn.join_room(room)
        conns.append(conn)
        roles.append(reply["data"].get("role"))
    return conns, roles


async def ready_all(conns):
    for conn in conns:
        await conn.request("room_ready", {})


async def test_seats_and_spectators(server):
    conns, roles = await make_seated(server, ["p0", "p1", "p2", "p3", "p4"])
    assert roles == ["player", "player", "player", "spectator", "spectator"]

    extra = await Connection.connect(server.host, server.port)
    await extra.login("p5")
    with pytest.raises(ServerError) as exc:
        await extra.join_room(ROOM)
    assert exc.value.code == ErrCode.ROOM_FULL
    for conn in conns + [extra]:
        await conn.close()


async def test_ready_deals_private_hands(server):
    conns, _ = await make_seated(server, ["d0", "d1", "d2"])
    await ready_all(conns)

    hands = []
    for conn in conns:
        frame = await conn.wait_for("game_start", timeout=3.0)
        assert len(frame["data"]["hand"]) == 17
        hands.append(set(frame["data"]["hand"]))

    # 三家手牌互不重叠，合计 51 张
    assert len(hands[0] | hands[1] | hands[2]) == 51
    # 公开视图不含任何手牌
    public = await conns[0].wait_for("table_state", timeout=3.0)
    assert "hand" not in public["data"]
    for conn in conns:
        await conn.close()


async def test_bid_three_makes_landlord_and_reveals_bottom(server):
    conns, _ = await make_seated(server, ["e0", "e1", "e2"])
    await ready_all(conns)
    for conn in conns:
        await conn.wait_for("game_start", timeout=3.0)

    state = await conns[0].wait_for("table_state", timeout=3.0, match={"state": "BIDDING"})
    first = state["data"]["current"]
    bidder = conns[["e0", "e1", "e2"].index(first)]
    await bidder.request("bid", {"score": 3})

    for conn in conns:
        landlord_event = await conn.wait_for("landlord", timeout=3.0)
        assert landlord_event["data"]["landlord"] == first
        assert len(landlord_event["data"]["bottom"]) == 3

    hand = await bidder.wait_for("hand", timeout=3.0)
    assert len(hand["data"]["hand"]) == 20, "地主应拿到三张底牌"
    for conn in conns:
        await conn.close()


async def test_play_rules_are_enforced(server):
    conns, _ = await make_seated(server, ["f0", "f1", "f2"])
    await ready_all(conns)
    for conn in conns:
        await conn.wait_for("game_start", timeout=3.0)

    state = await conns[0].wait_for("table_state", timeout=3.0, match={"state": "BIDDING"})
    first = state["data"]["current"]
    index = ["f0", "f1", "f2"].index(first)
    landlord_conn, landlord_bot = conns[index], None
    await landlord_conn.request("bid", {"score": 3})
    hand = await landlord_conn.wait_for("hand", timeout=3.0)
    cards = hand["data"]["hand"]

    # 不是自己回合
    other = conns[(index + 1) % 3]
    with pytest.raises(ServerError) as exc:
        await other.request("play", {"cards": [cards[0]]})
    assert exc.value.code == "NOT_YOUR_TURN"

    # 先手不能过牌
    with pytest.raises(ServerError) as exc:
        await landlord_conn.request("pass", {})
    assert exc.value.code == "CANNOT_PASS"

    # 手里没有的牌
    foreign = next(code for code in ("3s", "4h", "5d", "6c", "7s", "8h", "9d")
                   if code not in cards)
    with pytest.raises(ServerError) as exc:
        await landlord_conn.request("play", {"cards": [foreign]})
    assert exc.value.code == "NOT_IN_HAND"

    # 合法出牌
    ok = await landlord_conn.request("play", {"cards": [cards[-1]]})
    assert ok["data"]["action"] == "play"
    for conn in conns:
        await conn.close()


async def test_spectator_sees_only_public_state(server):
    conns, _ = await make_seated(server, ["g0", "g1", "g2"])
    await ready_all(conns)
    spectator = await Connection.connect(server.host, server.port)
    await spectator.login("watcher")
    reply = await spectator.join_room(ROOM)
    assert reply["data"]["role"] == "spectator"

    state = await spectator.wait_for("table_state", timeout=3.0)
    assert "hand" not in state["data"]
    assert "hand_counts" in state["data"]
    for conn in conns + [spectator]:
        await conn.close()


async def test_three_bots_finish_a_game(server):
    """端到端验收：三个机器人通过协议打完整局，手牌不泄露，分数为 0 和。"""
    conns = [await Connection.connect(server.host, server.port) for _ in range(3)]
    bots = []
    for index, conn in enumerate(conns):
        await conn.login(f"bot{index}")
        bots.append(DoudizhuBot(f"bot{index}", conn, ROOM))
    for bot in bots:
        await bot.join_and_ready()

    results = await asyncio.wait_for(
        asyncio.gather(*(bot.play_until_end(30) for bot in bots)), 40
    )
    result = results[0]
    assert result["winner"] in [b.nickname for b in bots]
    assert sum(result["scores"].values()) == 0
    assert all(not bot.errors for bot in bots)
    # 每个机器人结束时都不知道别人的手牌（只知道张数）
    for bot in bots:
        assert len(bot.hand) <= 20
        assert set(bot.hand_counts) == {b.nickname for b in bots}


async def test_disconnected_player_is_auto_played(server_factory):
    server = await server_factory(auto_play_delay_ms=150, sweep_interval_ms=100)
    conns, _ = await make_seated(server, ["h0", "h1", "h2"])
    await ready_all(conns)
    for conn in conns:
        await conn.wait_for("game_start", timeout=3.0)

    state = await conns[0].wait_for("table_state", timeout=3.0, match={"state": "BIDDING"})
    first = state["data"]["current"]
    index = ["h0", "h1", "h2"].index(first)
    await conns[index].request("bid", {"score": 3})
    for conn in conns:
        await conn.wait_for("landlord", timeout=3.0)

    table = server.hub.rooms[ROOM].table
    before = len(table.match.history)
    await conns[index].close()  # 地主掉线

    # 服务端应在托管延迟后替他自动出牌，牌局继续
    for _ in range(40):
        if len(table.match.history) > before:
            break
        await asyncio.sleep(0.1)
    assert len(table.match.history) > before, "掉线玩家应由服务端托管自动出牌"
    assert table.seats[first] is None, "掉线期间保留座位并标记离线"
    for conn in conns:
        await conn.close()


async def test_table_plays_a_second_game(server):
    """一局结束后，三家重新准备应能开新的一局。"""
    conns = [await Connection.connect(server.host, server.port) for _ in range(3)]
    bots = []
    for index, conn in enumerate(conns):
        await conn.login(f"again{index}")
        bots.append(DoudizhuBot(f"again{index}", conn, ROOM))
    for bot in bots:
        await bot.join_and_ready()

    await asyncio.wait_for(asyncio.gather(*(bot.play_until_end(30) for bot in bots)), 45)
    table = server.hub.rooms[ROOM].table
    assert table.match.state.value == "SETTLED"
    first_match = table.match

    for bot in bots:
        bot.finished = asyncio.Event()
        bot.result = None
        bot._action_key = None
        await bot.conn.request("room_ready", {})

    # 机器人打得很快，第二局可能瞬间就打完了；关键是"换了新的一局"且能正常结算
    results = await asyncio.wait_for(
        asyncio.gather(*(bot.play_until_end(30) for bot in bots)), 45
    )
    assert table.match is not first_match, "第二局应当是新的对局对象"
    assert sum(results[0]["scores"].values()) == 0


async def test_online_auto_play_takes_over_my_turn(server_factory):
    """在线托管：人还在线，但轮到他时由服务端自动出牌。"""
    server = await server_factory(auto_play_delay_ms=150, sweep_interval_ms=100)
    conns, _ = await make_seated(server, ["t0", "t1", "t2"])
    await ready_all(conns)
    for conn in conns:
        await conn.wait_for("game_start", timeout=3.0)

    state = await conns[0].wait_for("table_state", timeout=3.0, match={"state": "BIDDING"})
    first = state["data"]["current"]
    index = ["t0", "t1", "t2"].index(first)
    landlord_conn = conns[index]
    await landlord_conn.request("bid", {"score": 3})
    for conn in conns:
        await conn.wait_for("landlord", timeout=3.0)

    # 地主开启托管后不再自己出牌
    await landlord_conn.request("auto_play", {"enabled": True})
    table = server.hub.rooms[ROOM].table
    before = len(table.match.history)
    for _ in range(40):
        if len(table.match.history) > before:
            break
        await asyncio.sleep(0.1)
    assert len(table.match.history) > before, "托管后服务端应自动替他行动"
    assert first in table.auto_players
    assert table.seats[first] is not None, "在线托管不掉座（座位仍绑定着在线会话）"

    # 取回控制后不再自动打
    await landlord_conn.request("auto_play", {"enabled": False})
    assert first not in table.auto_players
    for conn in conns:
        await conn.close()


async def test_human_turn_has_no_time_limit(server_factory):
    """真人控制时不限时：放着不动也不会被服务端代打。"""
    server = await server_factory(auto_play_delay_ms=200, sweep_interval_ms=100)
    conns, _ = await make_seated(server, ["h0", "h1", "h2"])
    await ready_all(conns)
    for conn in conns:
        await conn.wait_for("game_start", timeout=3.0)

    state = await conns[0].wait_for("table_state", timeout=3.0, match={"state": "BIDDING"})
    first = state["data"]["current"]
    index = ["h0", "h1", "h2"].index(first)
    await conns[index].request("bid", {"score": 3})
    for conn in conns:
        await conn.wait_for("landlord", timeout=3.0)

    table = server.hub.rooms[ROOM].table
    before = len(table.match.history)
    await asyncio.sleep(1.0)   # 超过 auto_play_delay_ms，但地主由真人控制
    assert len(table.match.history) == before, "真人控制时不该被自动代打"
    assert table.public_state().get("deadline_in_ms") is None, "真人出牌不限时"
    for conn in conns:
        await conn.close()


async def test_log_line_comes_with_its_own_event():
    """每条事件自带它那一行日志，避免界面取“最近一行”造成重复与漏行。"""
    session = ClientSession()
    lines: list[str] = []
    session.on_event = lambda kind, data: lines.append(data["_line"]) if data.get("_line") else None

    await session._handle("bid", {"player": "a", "score": 1})
    await session._handle("bid", {"player": "b", "score": 0})
    await session._handle("play", {"player": "a", "cards_text": "3", "hand_count": 16})
    await session._handle("pass", {"player": "b"})

    assert lines == [
        "[叫分] a 叫 1 分",
        "[叫分] b 不叫",
        "[出牌] a: 3",
        "[过牌] b",
    ], "同一批事件必须各自带自己的那行日志"


async def test_log_shows_remaining_only_when_close_to_finish():
    """出牌记录平时不带剩余张数，只在剩 ≤3 张时提示。"""
    session = ClientSession()
    lines: list[str] = []
    session.on_event = lambda kind, data: lines.append(data["_line"]) if data.get("_line") else None

    await session._handle("play", {"player": "a", "cards_text": "3", "hand_count": 10})
    await session._handle("play", {"player": "a", "cards_text": "4", "hand_count": 3})
    await session._handle("play", {"player": "b", "cards_text": "5", "hand_count": 1})

    assert lines == ["[出牌] a: 3", "[出牌] a: 4　剩 3 张", "[出牌] b: 5　剩 1 张"]


async def test_chat_works_inside_a_table(server):
    """牌局内也能聊天：房间广播与私信都要能收到，并写进牌桌记录。"""
    session = ClientSession()
    await session.connect(server.host, server.port)
    await session.login("talker")
    await session.join_room(ROOM)

    other = await Connection.connect(server.host, server.port)
    await other.login("mate")
    await other.join_room(ROOM)

    await session.say("大家好")
    await asyncio.sleep(0.3)
    assert "我: 大家好" in session.log, "自己发的聊天应显示为“我: …”"

    await other.request("room_msg", {"text": "收到"})
    await asyncio.sleep(0.3)
    assert "mate: 收到" in session.log, "牌桌上应能收到别人的聊天"

    await session.whisper("mate", "只跟你说")
    await asyncio.sleep(0.3)
    assert "（私信）我 → mate: 只跟你说" in session.log

    # 对方也能收到，并且在对方视角里标注"→ 我"
    other_log = ClientSession()
    await other_log.connect(server.host, server.port)
    await other_log.login("mate2")
    await other_log.join_room(ROOM)
    await session.whisper("mate2", "给你一条")
    await asyncio.sleep(0.3)
    assert "（私信）talker → 我: 给你一条" in other_log.log
    await other_log.close()
    await session.close()
    await other.close()


def test_jokers_are_displayed_in_chinese():
    """牌局记录里大小王显示为中文，协议编码仍是 BJ/CJ。"""
    from game.text import card_code, card_text, cards_text, parse_card

    big = parse_card("CJ")
    small = parse_card("BJ")
    assert card_text(small) == "小王"
    assert card_text(big) == "大王"
    assert cards_text([small, big]) == "小王 大王"
    assert card_code(small) == "BJ" and card_code(big) == "CJ"


async def test_resume_into_table_restores_hand(server):
    """断线后用 token 回到牌桌：昵称、房间、手牌都要恢复。"""
    conns, tokens = [], []
    for name in ("k0", "k1", "k2"):
        conn = await Connection.connect(server.host, server.port)
        welcome = await conn.login(name)
        tokens.append(welcome["data"]["token"])
        await conn.join_room(ROOM)
        conns.append(conn)
    await ready_all(conns)
    for conn in conns:
        await conn.wait_for("game_start", timeout=3.0)

    await conns[0].close()  # 掉线，进入重连保留期
    for _ in range(40):
        if server.hub.reserved_count() == 1:
            break
        await asyncio.sleep(0.1)
    assert server.hub.reserved_count() == 1

    again = await Connection.connect(server.host, server.port)
    resumed = await again.resume(tokens[0])
    assert resumed["data"]["nickname"] == "k0"
    assert resumed["data"]["room"] == ROOM, "重连应回到原牌桌"
    assert resumed["data"]["token"] != tokens[0], "重连应换发新 token"

    snapshot = await again.wait_for("game_state", timeout=3.0)
    assert len(snapshot["data"]["hand"]) == 17, "重连后应拿回自己的手牌"
    for conn in conns[1:] + [again]:
        await conn.close()
