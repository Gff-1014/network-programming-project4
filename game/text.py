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


def card_code(card_int: int) -> str:
    """协议里的牌面编码，如 '3s'、'BJ'、'CJ'（可被 Card.new 还原）。"""
    if Card.get_rank_int(card_int) >= 13:  # 大小王没有花色，int_to_str 会带空格
        return Card.rank_int_to_str(card_int)
    return Card.int_to_str(card_int)


def parse_card(code: str) -> int:
    """协议牌面编码 → 整数牌。"""
    return Card.new(code)
