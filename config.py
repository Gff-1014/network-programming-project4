"""集中配置（运行期零第三方依赖，直接读环境变量与默认值）。"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else int(raw)


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else raw


@dataclass(frozen=True)
class Settings:
    # 服务端监听：本机实验用 127.0.0.1，跨机时改为 0.0.0.0 并放行防火墙
    host: str = _env_str("DDZ_HOST", "127.0.0.1")
    port: int = _env_int("DDZ_PORT", 9011)

    # 分帧：4 字节大端长度 + UTF-8 JSON
    max_frame_bytes: int = _env_int("DDZ_MAX_FRAME_BYTES", 65536)
    max_text_chars: int = _env_int("DDZ_MAX_TEXT_CHARS", 500)

    # 握手保护：未登录连接的存活时间与非法帧上限
    handshake_timeout_ms: int = _env_int("DDZ_HANDSHAKE_TIMEOUT_MS", 10_000)
    bad_frame_limit: int = _env_int("DDZ_BAD_FRAME_LIMIT", 3)
    max_pending_connections: int = _env_int("DDZ_MAX_PENDING_CONNECTIONS", 50)

    # 每连接发送队列（M0 先用固定上限，M1 补丢弃/断开策略与统计）
    send_queue_max: int = _env_int("DDZ_SEND_QUEUE_MAX", 128)
    send_queue_max_bytes: int = _env_int("DDZ_SEND_QUEUE_MAX_BYTES", 65536)
    slow_consumer_drop_limit: int = _env_int("DDZ_SLOW_CONSUMER_DROP_LIMIT", 50)
    slow_consumer_window_ms: int = _env_int("DDZ_SLOW_CONSUMER_WINDOW_MS", 10_000)

    # 心跳与判活：收到任意合法帧都刷新 last_seen；静默超过 idle 即清理
    ping_interval_ms: int = _env_int("DDZ_PING_INTERVAL_MS", 10_000)
    idle_timeout_ms: int = _env_int("DDZ_IDLE_TIMEOUT_MS", 30_000)
    sweep_interval_ms: int = _env_int("DDZ_SWEEP_INTERVAL_MS", 5_000)

    # 断线重连：宽限期内昵称保留，凭 token 恢复
    reconnect_grace_ms: int = _env_int("DDZ_RECONNECT_GRACE_MS", 60_000)

    # 昵称限制
    max_nickname_chars: int = _env_int("DDZ_MAX_NICKNAME_CHARS", 32)

    # 默认房间：大厅是登录后自动进入的房间，不可退出
    lobby_room: str = _env_str("DDZ_LOBBY_ROOM", "大厅")
    chat_rooms: tuple[str, ...] = ("聊天房",)
    # 牌桌：三人一桌，其余人可作观战
    table_rooms: tuple[str, ...] = ("牌桌1", "牌桌2", "牌桌3")
    seats_per_table: int = _env_int("DDZ_SEATS_PER_TABLE", 3)
    max_spectators: int = _env_int("DDZ_MAX_SPECTATORS", 2)
    # 断线托管：轮到自己后多久自动出牌（毫秒）
    auto_play_delay_ms: int = _env_int("DDZ_AUTO_PLAY_DELAY_MS", 1000)


settings = Settings()
