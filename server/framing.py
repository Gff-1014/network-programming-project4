"""分帧层：4 字节大端长度前缀 + UTF-8 JSON。

处理要求：

* 粘包 / 半包 —— 一律用 `readexactly` 读足长度再解析，不假设"一次 recv 一条消息"；
* 非法长度 —— 长度为 0、超过上限、读不满，都抛 `FrameError`；
* 非法内容 —— 非 UTF-8、非 JSON、非对象，同样抛 `FrameError`。

调用方（会话层）负责把 `FrameError` 转成 error 帧并决定是否断开。
"""

from __future__ import annotations

import asyncio
import json
import struct

HEADER = struct.Struct(">I")


class FrameError(Exception):
    """帧不合法。`code` 直接作为 error 帧的错误码。"""

    def __init__(self, code: str, message: str, fatal: bool = False) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        # fatal=True 表示字节流已经错位（例如长度超限时没有读取消息体），只能断开
        self.fatal = fatal


def encode_frame(payload: dict) -> bytes:
    """把消息字典编码成带长度前缀的一帧。"""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return HEADER.pack(len(body)) + body


async def read_frame(reader: asyncio.StreamReader, max_bytes: int) -> dict:
    """从流里读出一帧。"""
    header = await reader.readexactly(HEADER.size)
    (length,) = HEADER.unpack(header)
    if length == 0:
        raise FrameError("BAD_FRAME", "帧长度为 0")
    if length > max_bytes:
        raise FrameError("BAD_FRAME", f"帧长度 {length} 超过上限 {max_bytes}", fatal=True)

    body = await reader.readexactly(length)
    try:
        payload = json.loads(body.decode("utf-8"))
    except UnicodeDecodeError:
        raise FrameError("BAD_FRAME", "帧不是合法的 UTF-8") from None
    except json.JSONDecodeError:
        raise FrameError("BAD_FRAME", "帧不是合法的 JSON") from None

    if not isinstance(payload, dict):
        raise FrameError("BAD_FRAME", "消息必须是 JSON 对象")
    return payload
