"""分帧层单元测试：粘包、半包、非法长度与非法内容。"""

from __future__ import annotations

import asyncio
import json
import struct

import pytest

from server.framing import HEADER, FrameError, encode_frame, read_frame

MAX = 65536


def reader_with(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


async def test_round_trip():
    payload = {"type": "ping", "data": {"echo": "中文"}, "request_id": "r1"}
    frame = encode_frame(payload)
    body = json.loads(frame[4:].decode("utf-8"))
    assert body == payload
    assert struct.unpack(">I", frame[:4])[0] == len(frame) - 4
    assert await read_frame(reader_with(frame), MAX) == payload


async def test_half_packet_is_buffered():
    """半包：一次只到一半的字节，读帧必须等剩下的。"""
    frame = encode_frame({"type": "ping", "data": {}})
    reader = asyncio.StreamReader()
    reader.feed_data(frame[:10])
    task = asyncio.create_task(read_frame(reader, MAX))
    await asyncio.sleep(0.05)
    assert not task.done(), "不应在消息不完整时返回"
    reader.feed_data(frame[10:])
    assert await task == {"type": "ping", "data": {}}


async def test_merged_packets_are_split():
    """粘包：两条消息挤在一次投递里，必须分两次读出。"""
    blob = encode_frame({"type": "ping", "data": {"n": 1}}) + encode_frame(
        {"type": "ping", "data": {"n": 2}}
    )
    reader = reader_with(blob)
    first = await read_frame(reader, MAX)
    second = await read_frame(reader, MAX)
    assert first["data"]["n"] == 1
    assert second["data"]["n"] == 2


async def test_zero_length_frame():
    with pytest.raises(FrameError) as exc:
        await read_frame(reader_with(HEADER.pack(0)), MAX)
    assert exc.value.code == "BAD_FRAME"
    assert exc.value.fatal is False


async def test_oversize_frame_is_fatal():
    with pytest.raises(FrameError) as exc:
        await read_frame(reader_with(HEADER.pack(MAX + 1)), MAX)
    assert exc.value.fatal is True, "长度超限时消息体没读，字节流已错位"


async def test_invalid_json():
    body = "not json".encode()
    with pytest.raises(FrameError) as exc:
        await read_frame(reader_with(HEADER.pack(len(body)) + body), MAX)
    assert exc.value.code == "BAD_FRAME"
    assert exc.value.fatal is False


async def test_invalid_utf8():
    body = b"\xff\xfe\xfd"
    with pytest.raises(FrameError):
        await read_frame(reader_with(HEADER.pack(len(body)) + body), MAX)


async def test_non_object_payload():
    body = b"[1,2,3]"
    with pytest.raises(FrameError) as exc:
        await read_frame(reader_with(HEADER.pack(len(body)) + body), MAX)
    assert "JSON 对象" in exc.value.message
