"""三个机器人打完整局斗地主，逐手打印，并检查手牌隐私。

用法（先启动服务端）：
    .venv\\Scripts\\python.exe bots\\run_table.py --room 牌桌1
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from bots.doudizhu_bot import DoudizhuBot  # noqa: E402
from client.net import Connection  # noqa: E402


async def main() -> int:
    """三机器人走网络打完整局的演示入口。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9011)
    parser.add_argument("--room", default="牌桌1")
    parser.add_argument("--spectators", type=int, default=0)
    parser.add_argument("--password", default="123456")
    args = parser.parse_args()

    # 严格模式（默认）下只能用已存在的账号，这里取预置账号的后三个
    names = ["wanjia3", "wanjia4", "wanjia5"]
    password = args.password

    conns = [await Connection.connect(args.host, args.port) for _ in range(3)]
    for conn, name in zip(conns, names):
        await conn.login(name, password)
    bots = [DoudizhuBot(name, conn, args.room) for name, conn in zip(names, conns)]

    spectators = []
    for index in range(args.spectators):
        conn = await Connection.connect(args.host, args.port)
        await conn.login(f"wanjia{index + 1}", password)
        spectators.append(conn)

    try:
        for bot in bots:
            await bot.join_and_ready()
        for conn in spectators:
            await conn.request("spectate", {"room": args.room})
        print(f"牌桌 {args.room}: {', '.join(names)} 全部准备，观战 {len(spectators)} 人")

        results = await asyncio.gather(*(bot.play_until_end(timeout=180) for bot in bots))

        print("\n== 对局过程 ==")
        for line in bots[0].log:
            print("  " + line)

        result = results[0]
        print("\n== 结算 ==")
        print(f"  赢家: {result.get('winner')}（地主 {result.get('landlord')}）")
        print(f"  地主{'获胜' if result.get('landlord_win') else '落败'}，"
              f"春天={result.get('spring')}，炸弹={result.get('bombs')}，倍数 ×{result.get('multiplier')}")
        for player, score in (result.get("scores") or {}).items():
            print(f"  {player}: {'+' if score > 0 else ''}{score}")
        print(f"  分数合计: {sum((result.get('scores') or {}).values())}")

        # 手牌隐私：任何机器人都不该收到别人的手牌
        leaked = [b.nickname for b in bots if b.hand and len(b.hand) > 20]
        print(f"\n== 隐私检查 ==")
        print(f"  收到超过 20 张手牌的机器人: {leaked or '无'}")
        print(f"  三个机器人结束时手牌张数: {[len(b.hand) for b in bots]}")

        ok = not leaked and all(not b.errors for b in bots)
        print(f"  结论: {'通过' if ok else '未通过'}")
        return 0 if ok else 1
    finally:
        for bot in bots:
            await bot.stop()
        for conn in conns + spectators:
            try:
                await conn.send("logout")
                await asyncio.sleep(0.02)
            except Exception:
                pass
            await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
