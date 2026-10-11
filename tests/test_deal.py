"""本项目新增：发牌的可复现性与完整性（上游没有固定 seed，这是我们改的部分）。"""

from __future__ import annotations

import time

import game
from game import Card


def test_deal_shape():
    hands = game.new_game(seed=1)
    assert [len(h) for h in hands] == [17, 17, 17, 3]
    all_cards = [c for h in hands for c in h]
    assert len(all_cards) == 54
    assert len(set(all_cards)) == 54, "同一副牌出现重复"
    assert sorted(all_cards) == game.ALL_CARDS, "发出去的牌与整副牌不一致"


def test_deal_is_reproducible():
    a = game.new_game(seed=20261011)
    b = game.new_game(seed=20261011)
    assert a == b, "同一 seed 必须发出同一副牌"
    c = game.new_game(seed=20261012)
    assert a != c, "不同 seed 发出同一副牌的概率极低，说明 seed 没生效"


def test_hand_sorted_desc():
    hands = game.new_game(seed=7)
    for hand in hands:
        ranks = [Card.get_rank_int(c) for c in hand]
        assert ranks == sorted(ranks, reverse=True), "手牌应按点数降序"


def test_facade_matches_engine_on_random_plays():
    """门面接口与上游引擎在随机对局中给出一致结论。"""
    from game.engine import Doudizhu

    rng_hands = game.new_game(seed=99)
    hand = rng_hands[0]
    # 单张：门面与引擎判定一致
    for card in hand[:5]:
        assert game.check_card_type([card])[0] is True
        assert Doudizhu.check_card_type(Card.cards_without_suit([card]))[0] is True

    # 出一对：从手牌里挑两张同点数的牌比较
    ranks: dict[int, list[int]] = {}
    for card in hand:
        ranks.setdefault(Card.get_rank_int(card), []).append(card)
    pairs = [v for v in ranks.values() if len(v) >= 2]
    if pairs:
        a, b = pairs[0][:2], pairs[-1][:2]
        assert game.check_card_type(a)[0] is True
        assert game.check_card_type(b)[0] is True
        if Card.get_rank_int(a[0]) != Card.get_rank_int(b[0]):
            greater, _ = game.cards_greater(a, b)
            expected = Card.get_rank_int(a[0]) > Card.get_rank_int(b[0])
            assert greater is expected


def test_engine_init_cost():
    """记录牌型字典的构建耗时（只用于观察，不设阈值）。"""
    start = time.perf_counter()
    game.Doudizhu.init_doudizhu_dict()
    elapsed = time.perf_counter() - start
    assert game.Doudizhu.TOTAL == 34152
    print(f"\n牌型字典: {game.Doudizhu.TOTAL} 条，构建耗时 {elapsed * 1000:.1f} ms")
