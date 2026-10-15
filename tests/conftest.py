"""测试夹具：在随机端口起一个真实服务端，供集成测试使用。"""

from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import dataclass

import pytest_asyncio

from config import settings
from server.hub import Hub
from server.main import handle_connection, heartbeat_sweeper


@dataclass
class ServerHandle:
    host: str
    port: int
    hub: Hub
    cfg: object

    @property
    def online(self) -> int:
        return self.hub.online_count()


@pytest_asyncio.fixture
async def server_factory():
    """启动服务端工厂：`await server_factory(handshake_timeout_ms=200)`。"""
    started: list[asyncio.AbstractServer] = []
    handles: list[ServerHandle] = []
    sweepers: list[asyncio.Task] = []

    async def start(**overrides) -> ServerHandle:
        # 测试里用到的账号名很多，默认打开"首次登录即注册"，
        # 生产/演示默认是严格模式（只认预置账号 + DDZ_EXTRA_ACCOUNTS）。
        overrides.setdefault("auto_register", True)
        cfg = dataclasses.replace(settings, **overrides)
        hub = Hub(cfg)
        srv = await asyncio.start_server(
            lambda r, w: handle_connection(r, w, hub), "127.0.0.1", 0
        )
        started.append(srv)
        # 与真实服务端一致：同时启动心跳/清理任务
        sweepers.append(asyncio.create_task(heartbeat_sweeper(hub)))
        port = srv.sockets[0].getsockname()[1]
        handle = ServerHandle("127.0.0.1", port, hub, cfg)
        handles.append(handle)
        return handle

    try:
        yield start
    finally:
        # 先关掉残留会话，否则连接处理协程不结束，wait_closed() 会一直等
        for handle in handles:
            await handle.hub.close_all("test teardown")
        for task in sweepers:
            task.cancel()
        for srv in started:
            srv.close()
        await asyncio.gather(
            *(asyncio.wait_for(srv.wait_closed(), 3) for srv in started),
            return_exceptions=True,
        )


@pytest_asyncio.fixture
async def server(server_factory) -> ServerHandle:
    return await server_factory()
