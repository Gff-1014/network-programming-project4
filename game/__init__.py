"""斗地主规则引擎门面。

网络层只依赖本模块暴露的纯函数接口，不直接依赖具体实现，
以后替换或裁剪牌型规则时不需要改动服务器代码。

牌型算法移植自 onestraw/doudizhu（MIT），见 game/LICENSE-onestraw.txt。
本模块相对于上游的改动：发牌改为可传入 seed 的本地随机源，
因此同一 seed 可以复现同一副牌，便于测试与排错。
"""

from __future__ import annotations

import random

from .cards import Card
from .engine import Doudizhu

# 上游在导入时构建 34152 条牌型字典，约 0.3~0.6 秒，只在进程启动时付一次成本。
Doudizhu.init_doudizhu_dict()

__all__ = [
    "Card",
    "Doudizhu",
    "ALL_CARDS",
    "new_game",
    "check_card_type",
    "cards_greater",
    "list_greater_cards",
    "hand_str",
]


def _build_deck() -> list[int]:
    """构造一副 54 张牌（整数表示）。"""
    deck: list[int] = []
    for rank in Card.STR_RANKS:
        deck.extend(Card.new(card_str) for card_str in Card.card_rank_to_real_card(rank))
    return deck


ALL_CARDS: list[int] = sorted(_build_deck())


def new_game(seed: int | None = None) -> list[list[int]]:
    """发牌，返回 [手牌A, 手牌B, 手牌C, 底牌]。

    每组手牌 17 张，底牌 3 张，合计 54 张且互不重复；每组的牌按点数降序排列。

    seed 为 None 时每局不同；给出 seed 时同一 seed 必然发出同一副牌，
    用于复现问题、回归测试与对局回放。
    """
    deck = list(ALL_CARDS)
    random.Random(seed).shuffle(deck)
    hands = [deck[0:17], deck[17:34], deck[34:51]]
    bottom = deck[51:54]
    return [
        Card.sort_cards_by_rank_int(hand) for hand in hands
    ] + [Card.sort_cards_by_rank_int(bottom)]


def check_card_type(cards) -> tuple[bool, object]:
    """判断这手牌是否是合法牌型。

    cards: Card 整数列表。
    返回 (是否合法, 牌型信息)；合法时第二项是 [(牌型名, 权重), ...]，
    不合法时第二项是异常对象（沿用上游行为，网络层据此回 INVALID_PLAY）。
    """
    return Doudizhu.check_card_type(Card.cards_without_suit(cards))


def cards_greater(cards_x, cards_y) -> tuple[bool, str]:
    """判断 cards_x 能否压过 cards_y，返回 (能否压过, cards_x 的牌型名)。"""
    return Doudizhu.cards_greater(
        Card.cards_without_suit(cards_x),
        Card.cards_without_suit(cards_y),
    )


def list_greater_cards(cards_target, cards_candidate) -> dict:
    """从 cards_candidate 中找出所有能压过 cards_target 的组合。

    返回 {牌型名: [[牌, ...], ...]}。托管 AI 与"出牌提示"都基于它实现。
    """
    target_no_suit = Card.cards_without_suit(cards_target)
    candidate_no_suit = Card.cards_without_suit(cards_candidate)
    cards_gt = Doudizhu.list_greater_cards(target_no_suit, candidate_no_suit)

    def render_suit(cards: str, candidate) -> list[int]:
        pool = set(candidate)
        picked: set[int] = set()
        for rank in cards.split("-"):
            for card_int in (Card.new(cs) for cs in Card.card_rank_to_real_card(rank)):
                if card_int in pool and card_int not in picked:
                    picked.add(card_int)
                    break
        return Card.sort_cards_by_rank_int(list(picked))

    return {
        card_type: [render_suit(cards, cards_candidate) for cards in cards_list]
        for card_type, cards_list in cards_gt.items()
    }


def hand_str(cards) -> str:
    """把一手牌转成 '3-4-5' 这种便于日志阅读的字符串。"""
    return Card.cards_without_suit(cards)
