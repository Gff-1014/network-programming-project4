"""服务端入口：`python -m server`。

启动后会打印监听地址与房间列表，方便本机多开客户端时确认连哪里。
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import socket
import sys

if __package__ in (None, ""):  # 允许 `python server/main.py` 直接运行
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from config import settings  # noqa: E402
from server import handlers  # noqa: E402,F401  导入即注册处理器
from server.hub import Hub  # noqa: E402
from server.session import Session  # noqa: E402

logger = logging.getLogger("server")

_conn_counter = itertools.count(1)


async def handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                            hub: Hub) -> None:
    cfg = hub.cfg  # 用 Hub 的配置，便于测试起不同参数的实例
    conn_id = f"c{next(_conn_counter):04d}"
    if hub.pending_count() >= cfg.max_pending_connections:
        logger.warning("未登录连接过多，拒绝 %s", conn_id)
        writer.close()
        return
    session = Session(conn_id, reader, writer, hub, cfg)
    await session.run()


def local_addresses() -> list[str]:
    """列出本机可用 IPv4 地址，跨机测试时告诉对方该填哪个。"""
    addresses: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            addr = info[4][0]
            if addr not in addresses:
                addresses.append(addr)
    except OSError:  # pragma: no cover
        pass
    return addresses


async def serve() -> None:
    hub = Hub(settings)
    server = await asyncio.start_server(
        lambda r, w: handle_connection(r, w, hub), settings.host, settings.port
    )
    print(f"服务端已启动: {settings.host}:{settings.port}", flush=True)
    print(f"本机可用地址: {', '.join(local_addresses()) or '未检测到'}", flush=True)
    print(f"房间: {[room.name for room in hub.rooms.values()]}", flush=True)
    print(f"分帧: 4 字节长度前缀，单帧上限 {settings.max_frame_bytes} 字节", flush=True)
    async with server:
        await server.serve_forever()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        print("\n服务端已停止")


if __name__ == "__main__":
    main()
