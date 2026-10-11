"""牌型引擎冒烟脚本：发牌 → 校验合法牌型 → 比较大小 → 枚举可压牌 → 模拟一轮出牌。

用法（在项目根目录）：
    .venv\\Scripts\\python.exe scripts\\smoke_cards.py
    .venv\\Scripts\\python.exe scripts\\smoke_cards.py --seed 20261011
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:  # Windows 控制台按 UTF-8 输出中文
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import game
from game import Card

SUITS = {1: "♠", 2: "♥", 4: "♦", 8: "♣", 0: ""}
REDS = {2, 4}


def card_text(card_int: int) -> str:
    rank = Card.STR_RANKS[Card.get_rank_int(card_int)]
    suit = SUITS.get(Card.get_suit_int(card_int), "")
    return f"{suit}{rank}"


def hand_text(cards) -> str:
    return " ".join(card_text(c) for c in cards)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20261011)
    args = parser.parse_args()

    t0 = time.perf_counter()
    hands = game.new_game(seed=args.seed)
    deal_ms = (time.perf_counter() - t0) * 1000

    print(f"== 发牌 (seed={args.seed}, 耗时 {deal_ms:.2f} ms) ==")
    for i, hand in enumerate(hands[:3]):
        print(f"  玩家{i}: {hand_text(hand)}")
    print(f"  底牌  : {hand_text(hands[3])}")

    unique = {c for hand in hands for c in hand}
    print(f"\n== 完整性 ==")
    print(f"  张数: {sum(len(h) for h in hands)}（应为 54）")
    print(f"  去重后: {len(unique)}（应为 54）")
    print(f"  与整副牌一致: {sorted(unique) == game.ALL_CARDS}")

    print("\n== 牌型识别（门面接口） ==")
    landlord = hands[0]
    samples = {
        "单张": landlord[:1],
        "对子/两张": landlord[:2],
        "整手17张": landlord,
    }
    for name, cards in samples.items():
        ok, info = game.check_card_type(cards)
        detail = info if not ok else [t[0] for t in info]
        print(f"  {name:8s} {hand_text(cards)[:40]:42s} -> {'合法' if ok else '非法'} {detail}")

    print("\n== 大小比较 ==")
    singles = sorted(landlord, key=Card.get_rank_int, reverse=True)[:2]
    greater, ctype = game.cards_greater([singles[0]], [singles[1]])
    print(
        f"  {card_text(singles[0])} 压 {card_text(singles[1])} -> {greater}（牌型 {ctype}）"
    )
    greater_rocket, _ = game.cards_greater(
        [Card.new("BJ"), Card.new("CJ")], [Card.new("2s")]
    )
    print(f"  王炸压单张2 -> {greater_rocket}")

    print("\n== 可压牌枚举（托管 AI 与出牌提示的基础） ==")
    target = sorted(landlord, key=Card.get_rank_int)[:1]
    candidates = hands[0] + hands[1]
    options = game.list_greater_cards(target, candidates)
    print(f"  目标: {hand_text(target)}")
    for ctype, combos in list(options.items())[:4]:
        shown = ", ".join(hand_text(c) for c in combos[:3])
        print(f"  {ctype}: {len(combos)} 种，例如 {shown}")

    print("\n== 模拟一轮（地主先出，其余两家尝试压过） ==")
    lead = max(landlord, key=Card.get_rank_int)
    print(f"  玩家0 出: {card_text(lead)}")
    table = [lead]
    for idx in (1, 2):
        picks = game.list_greater_cards(table, hands[idx])
        if not picks:
            print(f"  玩家{idx} 要不起")
            continue
        ctype = min(picks, key=lambda k: len(picks[k][0]))  # 取张数最少的压法
        play = picks[ctype][0]
        ok, _ = game.check_card_type(play)
        greater, _ = game.cards_greater(play, table)
        print(
            f"  玩家{idx} 出: {hand_text(play)}（牌型 {ctype}，合法={ok}，能压={greater}）"
        )
        table = play

    print("\n冒烟通过：发牌、识别、比较、可压牌枚举、模拟出牌全部可用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
