"""M0 验收：10 个客户端同时在线，房间内互相发消息全部到达。"""

from __future__ import annotations

import asyncio
import statistics
import time

from client.net import Connection


async def test_ten_clients_chat_in_one_room(server):
    client_count = 10
    rounds = 5
    room = "聊天房"

    conns = [await Connection.connect(server.host, server.port) for _ in range(client_count)]
    welcomes = []
    for index, conn in enumerate(conns):
        welcomes.append(await conn.login(f"bot{index}"))
    assert welcomes[-1]["data"]["online"] == client_count, "服务端应看到 10 个在线用户"
    for conn in conns:
        await conn.join_room(room)

    received: dict[int, set[str]] = {i: set() for i in range(client_count)}
    latencies: list[float] = []
    sent_at: dict[str, float] = {}
    stop = asyncio.Event()

    async def collect(index: int) -> None:
        conn = conns[index]
        while not stop.is_set():
            try:
                frame = await conn.recv(0.05)
            except asyncio.TimeoutError:
                continue
            except Exception:
                return
            if frame.get("type") != "room_msg":
                continue
            data = frame["data"]
            marker = data["text"].split()[-1]
            if marker.startswith(f"m-{index}-"):
                continue
            if marker in received[index]:
                continue
            received[index].add(marker)
            start = sent_at.get(marker)
            if start is not None:
                latencies.append((time.perf_counter() - start) * 1000)

    collectors = [asyncio.create_task(collect(i)) for i in range(client_count)]

    for round_index in range(rounds):
        for index, conn in enumerate(conns):
            marker = f"m-{index}-{round_index}"
            sent_at[marker] = time.perf_counter()
            await conn.send("room_msg", {"text": f"并发测试 {marker}"})
        await asyncio.sleep(0.1)
    await asyncio.sleep(1.5)
    stop.set()

    expected = {f"m-{i}-{r}" for i in range(client_count) for r in range(rounds)}
    for index in range(client_count):
        others = {m for m in expected if not m.startswith(f"m-{index}-")}
        missing = others - received[index]
        assert not missing, f"客户端 {index} 漏收 {len(missing)} 条: {sorted(missing)[:5]}"

    total_expected = rounds * client_count * (client_count - 1)
    assert len(latencies) == total_expected
    ordered = sorted(latencies)
    p50 = statistics.median(ordered)
    p95 = ordered[int(round(0.95 * (len(ordered) - 1)))]
    print(f"\n10 客户端 / {rounds} 轮：到达 {len(latencies)}/{total_expected}"
          f"，p50 {p50:.1f} ms，p95 {p95:.1f} ms")

    for task in collectors:
        task.cancel()
    await asyncio.gather(*collectors, return_exceptions=True)
    for conn in conns:
        await conn.close()
