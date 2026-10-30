"""集中配置（运行期零第三方依赖，直接读环境变量与默认值）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else int(raw)


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else raw


def _env_accounts(name: str) -> dict[str, str]:
    """解析 `DDZ_EXTRA_ACCOUNTS="bot0:pw,bot1:pw"` 这样的额外账号表。"""
    raw = os.environ.get(name, "")
    accounts: dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        account, _, password = item.partition(":")
        if account.strip():
            accounts[account.strip()] = password.strip() or "123456"
    return accounts


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

    # 账号体系（课程演示）：预置 5 个账号，密码统一 123456。
    # 默认**严格模式**：只有预置账号与额外账号能登录（拼错账号会被拒绝并给出提示）。
    # 需要更多身份（压测 10 客户端等）时用 DDZ_EXTRA_ACCOUNTS="bot0:pw,bot1:pw" 批量注入；
    # 也可以临时打开 DDZ_AUTO_REGISTER=1，让未预置账号首次登录即注册。
    extra_accounts: dict[str, str] = field(default_factory=lambda: _env_accounts("DDZ_EXTRA_ACCOUNTS"))
    account_passwords: dict[str, str] = field(default_factory=lambda: {
        "wanjia1": "123456",
        "wanjia2": "123456",
        "wanjia3": "123456",
        "wanjia4": "123456",
        "wanjia5": "123456",
    })
    auto_register: bool = _env_int("DDZ_AUTO_REGISTER", 0) == 1
    # SQLite 持久化文件；测试会覆盖成临时数据库
    db_path: str = _env_str("DDZ_DB_PATH", "data/app.db")
    history_page_size: int = _env_int("DDZ_HISTORY_PAGE_SIZE", 50)
    # 私信历史保留上限（仅保留最近消息）
    max_private_history: int = _env_int("DDZ_MAX_PRIVATE_HISTORY", 300)

    def __post_init__(self) -> None:
        # 额外账号并入账号表（不覆盖预置账号的密码）
        for account, password in self.extra_accounts.items():
            self.account_passwords.setdefault(account, password)

    # 默认房间：大厅是登录后自动进入的房间，不可退出
    lobby_room: str = _env_str("DDZ_LOBBY_ROOM", "大厅")
    chat_rooms: tuple[str, ...] = ("聊天房",)
    # 牌桌：三人一桌，其余人可作观战
    table_rooms: tuple[str, ...] = ("牌桌1", "牌桌2", "牌桌3")
    public_channels: tuple[str, ...] = (
        "公共频道·综合",
        "公共频道·技术",
        "公共频道·闲聊",
    )
    seats_per_table: int = _env_int("DDZ_SEATS_PER_TABLE", 3)
    max_spectators: int = _env_int("DDZ_MAX_SPECTATORS", 2)
    # 断线托管：轮到自己后多久自动出牌（毫秒）
    auto_play_delay_ms: int = _env_int("DDZ_AUTO_PLAY_DELAY_MS", 3000)


settings = Settings()
