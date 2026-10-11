"""对局冒烟：三个机器人自动打完一局，逐手打印。
用法（在项目根目录）：.venv\\Scripts\\python.exe scripts\\smoke_match.py --seed 20261011
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:  # Windows 控制台按 UTF-8 输出中文
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from game import Match, State, ai  # noqa: E402
from game.text import cards_text  # noqa: E402


def describe_events(events: list[dict]) -> None:
    """把状态机事件翻译成人能读的过程。"""
    for event in events:
        kind = event["type"]
        if kind == "dealt":
            print(f"  [发牌] 第 {event['deal_round']} 次，各家 {event['hand_counts']} 张")
        elif kind == "bid_start":
            print(f"  [叫分] 从 {event['first']} 开始，顺序 {event['order']}")
        elif kind == "bid":
            label = "不叫" if event["score"] == 0 else f"{event['score']} 分"
            print(f"  [叫分] {event['player']} → {label}")
        elif kind == "redeal":
            print(f"  [重发] {event['reason']}")
        elif kind == "match_aborted":
            print(f"  [终止] {event['reason']}，本局作废")
        elif kind == "landlord_decided":
            print(
                f"  [定地主] {event['landlord']} 以 {event['bid']} 分当上地主，"
                f"底牌 {cards_text(event['bottom'])}，各家 {event['hand_counts']} 张"
            )
        elif kind == "play":
            bomb = "（炸弹！）" if event["bomb"] else ""
            print(
                f"  [出牌] {event['player']}: {cards_text(event['cards'])}{bomb} "
                f"剩 {event['hand_count']} 张，倍数 ×{event['multiplier']}"
            )
        elif kind == "pass":
            print(f"  [过牌] {event['player']}")
        elif kind == "table_cleared":
            print(f"  [一轮结束] {event['leader']} 重新领出")
        elif kind == "timeout":
            print(f"  [超时] {event['player']} {event['phase']} → {event['action']}")
        elif kind == "game_over":
            print(
                f"  [结束] 赢家 {event['winner']}，地主{'胜' if event['landlord_win'] else '负'}，"
                f"春天={event['spring']}，炸弹={event['bombs']}，倍数 ×{event['multiplier']}"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20261011)
    parser.add_argument("--quiet", action="store_true", help="只打印结果")
    args = parser.parse_args()

    players = ["bot0", "bot1", "bot2"]
    match = Match(players, seed=args.seed)
    for player in players:
        match.ready(player)

    if not args.quiet:
        print(f"== 开局 (seed={args.seed}) ==")
        describe_events(match.drain_events())
        for player in players:
            print(f"  {player} 手牌: {cards_text(match.view_for(player)['hand'])}")
        print()

    steps = 0
    while match.state is not State.SETTLED:
        assert steps < 1000, "对局没有收敛"
        action = ai.act(match)
        assert action is not None and action.ok, f"动作失败: {action}"
        if args.quiet:
            match.drain_events()
        else:
            describe_events(match.drain_events())
        steps += 1

    match.drain_events()
    result = match.result
    print("\n== 结算 ==")
    print(f"  赢家: {result['winner']}（地主 {result['landlord']}）")
    print(
        f"  地主{'获胜' if result['landlord_win'] else '落败'}，"
        f"春天: {result['spring']}，炸弹: {result['bombs']}"
    )
    print(f"  倍数: ×{result['multiplier']}")
    for player, score in result["scores"].items():
        sign = "+" if score > 0 else ""
        print(f"  {player}: {sign}{score}")
    print(f"  分数合计（应为 0）: {sum(result['scores'].values())}")
    print(f"  总动作数: {steps}")

    spectator = match.public_view()
    print("\n== 观战视图检查 ==")
    print(f"  含手牌字段: {'hand' in spectator}（应为 False）")
    print(f"  各家剩余张数: {spectator['hand_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
