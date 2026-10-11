"""托管与机器人决策（本项目自研）。

v1 策略刻意保持简单、可解释（会写进"已知限制"）：

* 叫分：按手牌强度估算（王、2、炸弹数量），给出 0~3 分；
* 先手：能一次走完就走完，否则出最小单张；
* 跟牌：在能压过的组合里挑"张数最少、尽量不动炸弹"的那个；没有就过牌。

`act(match)` 让当前玩家做一个动作，机器人脚本、托管、单元测试共用它。
"""

from __future__ import annotations

from .api import check_card_type, list_greater_cards
from .cards import Card


def hand_strength(hand) -> int:
    """粗略的手牌强度：王和 2 各算分，炸弹额外加分。"""
    score = 0
    counts: dict[int, int] = {}
    for card in hand:
        rank = Card.get_rank_int(card)
        counts[rank] = counts.get(rank, 0) + 1
        if rank >= 13:  # 大小王
            score += 2
        elif rank == 12:  # 2
            score += 1
    score += sum(2 for rank, n in counts.items() if n == 4)
    return score


def choose_bid(hand, highest: int = 0) -> int:
    """叫分策略：强度越高叫得越高，但必须高于当前最高分，否则不叫。"""
    strength = hand_strength(hand)
    if strength >= 8:
        want = 3
    elif strength >= 5:
        want = 2
    elif strength >= 3:
        want = 1
    else:
        want = 0
    return want if want > highest else 0


def choose_play(hand, table) -> list[int] | None:
    """返回要出的牌；None 表示过牌。"""
    if not table:  # 先手
        ok, _ = check_card_type(hand)
        if ok:
            return list(hand)
        return [min(hand, key=Card.get_rank_int)]

    options = list_greater_cards(table, hand)
    if not options:
        return None
    # 炸弹和王炸留到必要时再用：先看普通牌型
    normal = {t: combos for t, combos in options.items() if t not in ("bomb", "rocket")}
    pool = normal or options
    best = min(
        (combo for combos in pool.values() for combo in combos),
        key=lambda combo: (len(combo), max(Card.get_rank_int(c) for c in combo)),
    )
    return list(best)


def act(match) -> object:
    """替当前玩家做一个动作（机器人 / 托管 / 自测共用）。"""
    from .engine import State

    player = match.current
    if player is None:
        return None
    if match.state is State.BIDDING:
        highest = max(match.bids.values(), default=0)
        return match.bid(player, choose_bid(match.hands[player], highest))
    if match.state is State.PLAYING:
        cards = choose_play(match.hands[player], match.table)
        if cards:
            return match.play(player, cards)
        return match.pass_turn(player)
    return None
