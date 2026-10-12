"""M0 机器人压测：N 个客户端同时在线，互相发消息并统计到达率与延迟。

用法（项目根目录）：
    .venv\\Scripts\\python.exe bots\\run_bots.py --clients 10 --rounds 5
    .venv\\Scripts\\python.exe bots\\run_bots.py --host 127.0.0.1 --port 9011
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from client.net import Connection  # noqa: E402
from server.framing import encode_frame  # noqa: E402


def spawn_slow_client(host: str, port: int, nickname: str, room: str) -> threading.Thread:
    """慢客户端：连上、登录、进房间，然后**永不读取**，用于对照实验。"""

    def worker() -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
            sock.connect((host, port))
            sock.sendall(encode_frame({"type": "login", "data": {"nickname": nickname},
                                       "request_id": "slow-1"}))
            time.sleep(0.3)
            sock.sendall(encode_frame({"type": "join_room", "data": {"room": room},
                                       "request_id": "slow-2"}))
            time.sleep(3600)
        except Exception as exc:  # pragma: no cover
            print(f"[慢客户端] 异常: {exc}")

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


class Bot:
    def __init__(self, nick: str, args, stats: dict) -> None:
        self.nick = nick
        self.args = args
        self.stats = stats
        self.conn: Connection | None = None
        self.sent: dict[str, float] = {}
        self.task: asyncio.Task | None = None
        self.seen: set[str] = set()  # 每个机器人各自去重，不能共用

    async def start(self) -> bool:
        try:
            self.conn = await Connection.connect(self.args.host, self.args.port)
            await self.conn.login(self.nick)
            await self.conn.join_room(self.args.room)
        except Exception as exc:
            print(f"[{self.nick}] 连接/登录失败: {exc}")
            return False
        self.stats["online"] += 1
        self.task = asyncio.create_task(self._reader())
        return True

    async def _reader(self) -> None:
        assert self.conn is not None
        while True:
            try:
                frame = await self.conn.recv()
            except Exception:
                return
            if frame.get("type") != "room_msg":
                continue
            data = frame["data"]
            sender = data.get("from")
            if sender == self.nick:
                continue
            marker = _marker_of(data.get("text", ""))
            if marker is None or marker in self.seen:
                continue
            self.seen.add(marker)
            self.stats["received"] += 1
            sent_at = self.stats["sent_at"].get(marker)
            if sent_at is not None:
                self.stats["latencies"].append((time.perf_counter() - sent_at) * 1000)

    async def say(self, index: int, run_id: str) -> None:
        assert self.conn is not None
        marker = f"m-{self.nick}-{index}-{run_id}"
        self.stats["sent_at"][marker] = time.perf_counter()
        self.stats["sent"] += 1
        try:
            await self.conn.room_msg(f"{marker} " + "x" * self.args.payload)
        except Exception as exc:
            print(f"[{self.nick}] 发送失败: {type(exc).__name__}: {exc!r}")
            self.stats["errors"] = self.stats.get("errors", 0) + 1

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
        if self.conn:
            # 主动退出：服务端会立即释放昵称，不会进入重连保留期
            try:
                await asyncio.wait_for(self.conn.send("logout"), 1.0)
                await asyncio.sleep(0.05)
            except Exception:
                pass
            await self.conn.close()


def _marker_of(text: str) -> str | None:
    for token in text.split():
        if token.startswith("m-"):
            return token
    return None


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9011)
    parser.add_argument("--clients", type=int, default=10)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--room", default="聊天房")
    parser.add_argument("--interval", type=float, default=0.05)
    parser.add_argument("--drain", type=float, default=2.0)
    parser.add_argument("--prefix", default="bot")
    parser.add_argument("--payload", type=int, default=0, help="每条消息追加的填充字符数")
    parser.add_argument("--slow", type=int, default=0, help="注入几个只连不读的慢客户端")
    args = parser.parse_args()

    run_id = "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(4))
    stats = {"online": 0, "sent": 0, "received": 0,
             "sent_at": {}, "latencies": [], "errors": 0}

    bots = [Bot(f"{args.prefix}{i}", args, stats) for i in range(args.clients)]
    results = await asyncio.gather(*(bot.start() for bot in bots))
    online = sum(1 for ok in results if ok)
    print(f"同时在线: {online}/{args.clients}")
    if online < args.clients:
        print("提示：有客户端没连上，后面的到达率会偏低")

    for index in range(args.slow):
        # 带 run 后缀：异常断开的昵称会保留 60 秒，重复用同名会导致登录被拒
        spawn_slow_client(args.host, args.port, f"slow{index}-{run_id}", args.room)
    if args.slow:
        print(f"已注入 {args.slow} 个慢客户端（只连不读）")
        await asyncio.sleep(0.5)

    for index in range(args.rounds):
        await asyncio.gather(*(bot.say(index, run_id) for bot in bots if bot.conn))
        await asyncio.sleep(args.interval)
    await asyncio.sleep(args.drain)

    expected = args.rounds * online * max(online - 1, 0)
    rate = (stats["received"] / expected * 100) if expected else 0.0
    print(f"广播到达: {stats['received']}/{expected}（{rate:.1f}%）")
    if stats["errors"]:
        print(f"发送错误: {stats['errors']} 次")
    if stats["latencies"]:
        ordered = sorted(stats["latencies"])
        p50 = statistics.median(ordered)
        p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
        print(f"广播延迟: p50 {p50:.1f} ms  p95 {p95:.1f} ms  最大 {max(ordered):.1f} ms")
    print(f"结论: {'通过' if online == args.clients and rate >= 99.0 else '未通过'}")

    await asyncio.gather(*(bot.stop() for bot in bots))
    return 0 if online == args.clients and rate >= 99.0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

