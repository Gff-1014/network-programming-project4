"""AI 陪打：占住牌桌的几个座位，自动出牌，直到你停止脚本。

用法（先启动服务端，再开你的窗口客户端进同一张牌桌）：

    .venv\\Scripts\\python.exe bots\\fill_seats.py --room 牌桌2 --ai 2

它会打印每个 AI 的行动，方便你对照牌局。按 Ctrl+C 结束。
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


async def printer(bots: list[DoudizhuBot], stop: asyncio.Event) -> None:
    """把每个 AI 的日志增量打印出来。"""
    printed = {bot.nickname: 0 for bot in bots}
    while not stop.is_set():
        for bot in bots:
            lines = bot.log[printed[bot.nickname]:]
            for line in lines:
                print(f"  [{bot.nickname}] {line}")
            printed[bot.nickname] = len(bot.log)
            if bot.result:
                result = bot.result
                print(f"  === 本局结束：赢家 {result.get('winner')}，"
                      f"倍数 ×{result.get('multiplier')}，分数 {result.get('scores')} ===")
                bot.result = None
        await asyncio.sleep(0.3)


async def main() -> int:
    """AI 陪打入口：占住座位并一直自动出牌。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9011)
    parser.add_argument("--room", default="牌桌2")
    parser.add_argument("--ai", type=int, default=2, help="占几个座位（最多 3；留给你自己的那 1 个不用填）")
    parser.add_argument("--accounts", default="wanjia4,wanjia5",
                        help="AI 使用哪些账号（默认取预置账号 wanjia4/wanjia5；严格模式下必须是已存在的账号）")
    parser.add_argument("--password", default="123456")
    args = parser.parse_args()

    accounts = [a.strip() for a in args.accounts.split(",") if a.strip()]
    bots: list[DoudizhuBot] = []
    conns: list[Connection] = []
    tasks: list[asyncio.Task] = []
    stop = asyncio.Event()

    for index in range(args.ai):
        conn = await Connection.connect(args.host, args.port)
        nickname = accounts[index % len(accounts)] if accounts else f"ai{index + 1}"
        await conn.login(nickname, args.password)
        bot = DoudizhuBot(nickname, conn, args.room)
        conns.append(conn)
        bots.append(bot)
        tasks.append(asyncio.create_task(bot.run_forever()))
        print(f"AI 就位：{nickname} → {args.room}")
        await asyncio.sleep(0.4)

    print(f"\n现在用窗口客户端进 {args.room} 坐下并点“准备”，凑满 {3} 人就会开局。")
    print("（AI 会自动叫分与出牌；你想接手某一手时，可以在界面里点“托管/取回控制”）\n")

    printing = asyncio.create_task(printer(bots, stop))
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        stop.set()
        printing.cancel()
        for conn in conns:
            try:
                await conn.send("logout")
                await asyncio.sleep(0.02)
            except Exception:
                pass
            await conn.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n已停止 AI 陪打")
