"""对局状态机测试（自研部分的验收依据）。"""

from __future__ import annotations

import game
from game import Err, Match, MatchConfig, State
from game.cards import Card


class FakeClock:
    def __init__(self, now: int = 0) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += ms


def new_match(seed: int = 20261011, clock=None) -> Match:
    match = Match(["p0", "p1", "p2"], seed=seed, clock=clock)
    for player in match.players:
        assert match.ready(player).ok
    return match


def to_playing(match: Match, bidder: str = None, score: int = 3) -> Match:
    """把对局推进到出牌阶段，默认由当前叫分者直接叫 3 分当上地主。"""
    bidder = bidder or match.current
    assert match.bid(bidder, score).ok
    if match.state is State.PLAYING:
        return match
    # 叫 3 分必然立即定地主，这里不该走到
    raise AssertionError(f"未进入出牌阶段: {match.state}")


# --------------------------------------------------------------- 发牌 ----


def test_deal_and_bid_order():
    match = new_match()
    assert match.state is State.BIDDING
    assert match.hand_counts() == {"p0": 17, "p1": 17, "p2": 17}
    assert len(match.bottom) == 3
    assert sorted(match.bid_order) == match.players
    assert match.current == match.bid_order[0]


def test_same_seed_same_deal():
    a = new_match(seed=42)
    b = new_match(seed=42)
    assert a.hands == b.hands
    assert a.bottom == b.bottom


# --------------------------------------------------------------- 叫分 ----


def test_bid_three_makes_landlord_immediately():
    match = new_match()
    first = match.current
    assert match.bid(first, 3).ok
    assert match.state is State.PLAYING
    assert match.landlord == first
    assert match.multiplier == 3
    assert match.bottom_revealed is True
    assert match.hand_counts()[first] == 20, "地主应拿到三张底牌"
    assert match.current == first, "地主先出牌"


def test_bid_must_exceed_highest():
    match = new_match()
    assert match.bid(match.current, 2).ok
    second = match.current
    assert match.bid(second, 1).code is Err.ILLEGAL_BID
    assert match.bid(second, 2).code is Err.ILLEGAL_BID
    assert match.bid(second, 3).ok
    assert match.landlord == second


def test_all_pass_redeals_then_aborts():
    match = new_match()
    match.drain_events()
    for _ in range(3):
        while match.state is State.BIDDING:
            assert match.bid(match.current, 0).ok
    assert match.state is State.WAITING, "连续三次全不叫后本局作废"
    assert match.deal_round == 3
    events = match.drain_events()
    assert sum(1 for e in events if e["type"] == "redeal") == 2, "前两次全不叫应重发"
    assert any(e["type"] == "match_aborted" for e in events)


# --------------------------------------------------------------- 出牌 ----


def test_not_your_turn():
    match = to_playing(new_match())
    landlord = match.landlord
    other = next(p for p in match.players if p != landlord)
    card = match.hands[other][0]
    assert match.play(other, [card]).code is Err.NOT_YOUR_TURN


def test_play_card_not_in_hand():
    match = to_playing(new_match())
    landlord = match.landlord
    own = set(match.hands[landlord])
    foreign = next(c for c in game.ALL_CARDS if c not in own)
    assert match.play(landlord, [foreign]).code is Err.NOT_IN_HAND


def test_play_invalid_type():
    match = to_playing(new_match())
    landlord = match.landlord
    hand = match.hands[landlord]
    # 取两张点数不同的牌，凑不成任何牌型
    by_rank: dict[int, list[int]] = {}
    for card in hand:
        by_rank.setdefault(Card.get_rank_int(card), []).append(card)
    singles = [cards[0] for cards in by_rank.values()]
    assert match.play(landlord, singles[:2]).code is Err.INVALID_PLAY


def test_follow_must_beat():
    match = to_playing(new_match())
    landlord = match.landlord
    others = [p for p in match.players if p != landlord]
    smallest = min(match.hands[landlord], key=Card.get_rank_int)
    assert match.play(landlord, [smallest]).ok

    follower = match.current
    lower = [c for c in match.hands[follower] if Card.get_rank_int(c) < Card.get_rank_int(smallest)]
    if lower:
        assert match.play(follower, [lower[0]]).code is Err.INVALID_PLAY


def test_cannot_pass_when_leading():
    match = to_playing(new_match())
    assert match.pass_turn(match.current).code is Err.CANNOT_PASS


def test_two_passes_clear_table_and_leader_leads():
    match = to_playing(new_match())
    leader = match.landlord
    card = min(match.hands[leader], key=Card.get_rank_int)
    assert match.play(leader, [card]).ok
    for _ in range(2):
        assert match.pass_turn(match.current).ok
    assert match.table == []
    assert match.current == leader, "两度过牌后由最后出牌者重新领出"


# --------------------------------------------------------------- 结算 ----


def test_win_settles_and_scores_are_zero_sum():
    match = to_playing(new_match())
    landlord = match.landlord
    match.hands[landlord] = [Card.new("3s")]  # 直接构造必胜局面
    assert match.play(landlord, [Card.new("3s")]).ok
    assert match.state is State.SETTLED
    result = match.result
    assert result["landlord_win"] is True
    assert sum(result["scores"].values()) == 0
    total = result["multiplier"]
    assert result["scores"][landlord] == 2 * total
    for farmer in match.players:
        if farmer != landlord:
            assert result["scores"][farmer] == -total


def test_spring_doubles_multiplier():
    match = to_playing(new_match())
    landlord = match.landlord
    base_multiplier = match.multiplier
    match.hands[landlord] = [Card.new("3s")]
    assert match.play(landlord, [Card.new("3s")]).ok
    assert match.result["spring"] is True
    assert match.result["multiplier"] == base_multiplier * 2


def test_bomb_doubles_multiplier():
    match = to_playing(new_match())
    landlord = match.landlord
    match.hands[landlord] = [Card.new(f"3{s}") for s in "shdc"]
    before = match.multiplier
    assert match.play(landlord, list(match.hands[landlord])).ok
    assert match.result["bombs"] == 1
    # 炸弹 ×2，且地主一轮走完（农民没出过牌）还要再 ×2 春天
    assert match.result["multiplier"] == before * 2 * 2


# --------------------------------------------------------------- 超时 ----


def test_timeout_on_bidding_counts_as_pass():
    clock = FakeClock()
    match = new_match(clock=clock)
    pending = match.current
    clock.advance(MatchConfig().bid_timeout_ms + 1)
    action = match.tick()
    assert action is not None and action.ok
    assert match.bids[pending] == 0
    assert any(e["type"] == "timeout" and e["player"] == pending for e in match.drain_events())


def test_timeout_on_follow_passes():
    clock = FakeClock()
    match = to_playing(new_match(clock=clock))
    landlord = match.landlord
    card = min(match.hands[landlord], key=Card.get_rank_int)
    assert match.play(landlord, [card]).ok
    follower = match.current
    match.drain_events()
    clock.advance(MatchConfig().play_timeout_ms + 1)
    assert match.tick().ok
    assert any(e["type"] == "pass" and e["player"] == follower for e in match.drain_events() or [])


def test_timeout_when_leading_plays_smallest_single():
    clock = FakeClock()
    match = to_playing(new_match(clock=clock))
    landlord = match.landlord
    smallest = min(match.hands[landlord], key=Card.get_rank_int)
    match.drain_events()
    clock.advance(MatchConfig().play_timeout_ms + 1)
    assert match.tick().ok
    assert match.table == [smallest]
    assert match.play_counts[landlord] == 1


# --------------------------------------------------------------- 幂等 ----


def test_request_id_is_idempotent():
    match = new_match()
    first = match.current
    assert match.bid(first, 3, request_id="req-1").ok
    state_snapshot = (match.state, match.landlord, dict(match.bids))
    duplicate = match.bid(first, 3, request_id="req-1")
    assert duplicate.duplicate is True
    assert (match.state, match.landlord, dict(match.bids)) == state_snapshot


def test_request_id_is_scoped_per_player():
    """两个玩家用同一个 request_id 时，不能互相把对方的动作吞掉。"""
    match = new_match()
    first = match.current
    assert match.bid(first, 0, request_id="c4").ok
    second = match.current
    assert second != first
    action = match.bid(second, 2, request_id="c4")  # 同一个编号，但换了玩家
    assert action.ok and not action.duplicate
    assert match.bids[second] == 2, "第二个玩家的叫分必须真正生效"


# --------------------------------------------------------------- 视图 ----


def test_privacy_of_views():
    match = to_playing(new_match())
    landlord = match.landlord
    view = match.view_for(landlord)
    assert "hand" in view and len(view["hand"]) == 20
    public = match.public_view()
    assert "hand" not in public
    assert "hands" not in public
    assert set(public["hand_counts"]) == set(match.players)


def test_spectator_view_has_no_hand():
    match = to_playing(new_match())
    view = match.view_for(None)
    assert "hand" not in view
    assert view["landlord"] == match.landlord
