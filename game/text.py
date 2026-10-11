"""牌面文本化，供脚本、日志与客户端复用（保证各处显示一致）。"""

from __future__ import annotations

from .cards import Card

SUITS = {1: "♠", 2: "♥", 4: "♦", 8: "♣", 0: ""}
RED_SUITS = {2, 4}


def card_text(card_int: int) -> str:
    """单张牌的可读文本，如 '♥A'、'CJ'。"""
    rank = Card.STR_RANKS[Card.get_rank_int(card_int)]
    return f"{SUITS.get(Card.get_suit_int(card_int), '')}{rank}"


def cards_text(cards) -> str:
    """一手牌的文本，如 '♠3 ♥4 ♦5'。"""
    return " ".join(card_text(c) for c in cards)


def is_red(card_int: int) -> bool:
    """是否红花色（客户端渲染用）。"""
    return Card.get_suit_int(card_int) in RED_SUITS
