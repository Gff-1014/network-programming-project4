"""HTTP/WebSocket 网关：把浏览器接入现有 TCP 聊天室服务端。

浏览器不能直接复用 TCP 长度前缀协议，所以每个 WebSocket 会话在网关内部
创建一个 ``ClientSession``，由它连接原来的 TCP 服务端。这样网页版和桌面
客户端仍然使用同一个服务端、同一批账号、房间和牌桌。

启动示例：

    .venv\\Scripts\\python.exe web_gateway.py

默认监听 ``0.0.0.0:8080``，后端 TCP 服务端为 ``127.0.0.1:9011``。
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import mimetypes
import os
import secrets
import socket
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web
import qrcode
from qrcode.image.svg import SvgPathImage

from client.net import ServerError
from client.session import ClientSession
from game.text import card_code, parse_card


WEB_DIR = Path(__file__).resolve().parent / "web"
UPLOAD_DIR = Path(__file__).resolve().parent / "data" / "uploads"
LOGGER = logging.getLogger("web_gateway")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
BLOCKED_MIME = {"text/html", "image/svg+xml", "application/x-msdownload"}

# WebSocket 标签页的短期文件令牌 -> 对应会话
FILE_BRIDGES: dict[str, "BrowserBridge"] = {}
# 文件 ID -> 磁盘路径和群 ID
FILE_STORE: dict[str, dict] = {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def local_ipv4_addresses() -> list[str]:
    """返回适合手机访问的本机 IPv4，优先使用默认路由地址。"""
    addresses: list[str] = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        addresses.append(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            address = info[4][0]
            if address not in addresses and not address.startswith("127."):
                addresses.append(address)
    except OSError:
        pass
    return [item for item in addresses if not item.startswith("169.254.")]


class BrowserBridge:
    """一条浏览器 WebSocket 对应的 TCP 客户端。"""

    def __init__(self, backend_host: str, backend_port: int, web_port: int) -> None:
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.web_port = web_port
        self.events: asyncio.Queue[dict] = asyncio.Queue()
        self.session = ClientSession(on_event=self._on_event)
        self.closed = False
        self.file_token = secrets.token_urlsafe(24)
        FILE_BRIDGES[self.file_token] = self

    def _on_event(self, kind: str, data: dict) -> None:
        """把 ClientSession 事件排入 WebSocket 发送队列。"""
        if not self.closed:
            self.events.put_nowait({
                "type": "event",
                "kind": kind,
                "data": data,
            })

    async def _send_loop(self, ws: web.WebSocketResponse) -> None:
        while not self.closed:
            message = await self.events.get()
            try:
                await ws.send_json(message)
            except (ConnectionError, RuntimeError):
                return

    async def send(self, message: dict) -> None:
        if not self.closed:
            await self.events.put(message)

    async def ensure_connected(self) -> None:
        conn = self.session.conn
        if conn is None or self.session._closed or conn.closed:
            await self.session.connect(self.backend_host, self.backend_port)

    def snapshot(self) -> dict:
        room = next(
            (
                item for item in self.session.rooms
                if item.get("room_key", item.get("name")) == self.session.room
            ),
            None,
        )
        peer = self.session.current_peer
        return {
            "account": self.session.account,
            "nickname": self.session.nickname,
            "room": self.session.room,
            "room_key": self.session.room,
            "room_name": self.session.room_name or self.session.room,
            "room_kind": room.get("kind") if room else None,
            "role": self.session.role,
            "rooms": self.session.rooms,
            "room_users": self.session.room_users,
            "online_users": self.session.online_users,
            "conversations": self.session.conversations,
            "group_state": self.session.group_state,
            "lan_urls": [
                f"http://{address}:{self.web_port}"
                for address in local_ipv4_addresses()
            ],
            "current_peer": peer,
            "private_history": self.session.private_history.get(peer, []) if peer else [],
            "hand": [card_code(card) for card in self.session.hand],
            "table": self.session.table,
            "auto_play": self.session.auto_play,
            "file_token": self.file_token,
            "room_history": self.session.room_history,
            "room_history_has_more": self.session.room_history_has_more,
            "room_history_next_before": self.session.room_history_next_before,
        }

    async def send_snapshot(self) -> None:
        await self.send({"type": "snapshot", "data": self.snapshot()})

    async def refresh_all(self) -> None:
        if self.session.nickname is None:
            return
        await self.session.refresh_rooms()
        await self.session.refresh_users()
        await self.session.refresh_conversations()
        room = next(
            (
                item for item in self.session.rooms
                if item.get("room_key", item.get("name")) == self.session.room
            ),
            None,
        )
        if room is not None and room.get("kind") == "group":
            await self.session.refresh_group_state()

    async def handle(self, message: dict) -> None:
        command = message.get("type")
        data = message.get("data") or {}
        if not isinstance(data, dict):
            data = {}

        try:
            if command in ("login", "register"):
                await self._handle_auth(command, data)
                return

            if command == "logout":
                await self.session.logout()
                await self.send_snapshot()
                return

            if command == "hello":
                await self.send_snapshot()
                return

            if command == "refresh_all":
                await self.refresh_all()
                await self.send_snapshot()
                return

            if self.session.nickname is None:
                await self.send_error("NOT_LOGGED_IN", "请先登录")
                return

            if command == "refresh_rooms":
                await self.session.refresh_rooms()
            elif command == "refresh_users":
                await self.session.refresh_users()
            elif command == "join_invite":
                token = _text(data.get("token"))
                if not token:
                    raise ValueError("缺少群邀请 token")
                await self.session.join_invite(token)
                await self.refresh_all()
            elif command == "group_state":
                await self.session.refresh_group_state()
            elif command == "join_room":
                room = _text(data.get("room"))
                if not room:
                    raise ValueError("缺少房间名")
                await self.session.join_room(room)
                await self.refresh_all()
            elif command == "leave_room":
                await self.session.leave_room()
                await self.refresh_all()
            elif command == "room_msg":
                text = _text(data.get("text"))
                if not text:
                    raise ValueError("消息不能为空")
                await self.session.say(text)
            elif command == "load_room_history":
                await self.session.load_room_history(
                    data.get("before_id") if isinstance(data.get("before_id"), int) else None
                )
            elif command == "load_private_history":
                peer = _text(data.get("peer"))
                if not peer:
                    raise ValueError("缺少私聊对象")
                await self.session.load_private_history(
                    peer,
                    data.get("before_id") if isinstance(data.get("before_id"), int) else None,
                )
            elif command == "private_open":
                peer = _text(data.get("peer"))
                if not peer:
                    raise ValueError("缺少私聊对象")
                await self.session.open_private(peer)
            elif command == "private_msg":
                text = _text(data.get("text"))
                if not text:
                    raise ValueError("消息不能为空")
                await self.session.send_private(text)
            elif command == "create_group":
                members = data.get("members")
                if not isinstance(members, list):
                    raise ValueError("members 必须是账号数组")
                await self.session.create_group([
                    item for item in members if isinstance(item, str) and item
                ])
                await self.refresh_all()
            elif command == "create_channel":
                name = _text(data.get("name"))
                if not name:
                    raise ValueError("频道名不能为空")
                await self.session.create_channel(name)
                await self.refresh_all()
            elif command == "delete_group":
                room = _text(data.get("room")) or self.session.room or ""
                if not room:
                    raise ValueError("缺少群聊名")
                await self.session.delete_group(room)
                await self.refresh_all()
            elif command == "rename_group":
                name = _text(data.get("name"))
                if not name:
                    raise ValueError("群名不能为空")
                await self.session.rename_group(name)
            elif command == "invite_group_member":
                members = data.get("members")
                if not isinstance(members, list):
                    raise ValueError("members 必须是账号数组")
                await self.session.invite_group_members([
                    item for item in members if isinstance(item, str) and item
                ])
            elif command == "approve_group_invite":
                account = _text(data.get("account"))
                if not account:
                    raise ValueError("缺少申请账号")
                await self.session.approve_group_invite(account)
            elif command == "reject_group_invite":
                account = _text(data.get("account"))
                if not account:
                    raise ValueError("缺少申请账号")
                await self.session.reject_group_invite(account)
            elif command == "set_announcement":
                text = _text(data.get("text"))
                await self.session.set_announcement(text)
            elif command == "poll_create":
                title = _text(data.get("title"))
                options = data.get("options")
                if not isinstance(options, list):
                    raise ValueError("投票选项必须是数组")
                deadline = data.get("deadline_minutes")
                if deadline is not None:
                    deadline = int(deadline)
                await self.session.create_poll(
                    title,
                    [item for item in options if isinstance(item, str)],
                    deadline,
                    bool(data.get("anonymous", False)),
                )
            elif command == "poll_list":
                await self.session.refresh_group_state()
            elif command == "poll_vote":
                await self.session.vote_poll(
                    _text(data.get("poll_id")),
                    _text(data.get("option_id")),
                )
            elif command == "poll_close":
                await self.session.close_poll(_text(data.get("poll_id")))
            elif command == "group_file_delete":
                await self.session.delete_group_file(_text(data.get("file_id")))
            elif command == "ready":
                await self.session.ready()
            elif command == "bid":
                score = data.get("score")
                if not isinstance(score, int) or score not in (0, 1, 2, 3):
                    raise ValueError("叫分必须是 0/1/2/3")
                await self.session.bid(score)
            elif command == "play":
                cards = data.get("cards")
                if not isinstance(cards, list) or not cards:
                    raise ValueError("请选择要出的牌")
                await self.session.play([parse_card(item) for item in cards])
            elif command == "pass":
                await self.session.pass_turn()
            elif command == "spectate":
                room = _text(data.get("room"))
                if not room:
                    raise ValueError("缺少房间名")
                await self.session.spectate(room)
                await self.refresh_all()
            elif command == "set_auto":
                await self.session.set_auto(bool(data.get("enabled", True)))
            elif command == "request_state":
                await self.session.request_state()
            else:
                await self.send_error("UNKNOWN_COMMAND", f"未知网页命令 {command}")
                return

            await self.send({"type": "ack", "command": command})
            await self.send_snapshot()
        except ServerError as exc:
            await self.send_error(exc.code, exc.message)
        except asyncio.TimeoutError:
            await self.send_error("TIMEOUT", "服务端响应超时")
        except (ConnectionError, OSError) as exc:
            await self.send_error("CONNECTION_ERROR", f"TCP 连接不可用: {exc}")
        except (ValueError, KeyError, TypeError) as exc:
            await self.send_error("BAD_COMMAND", str(exc) or "参数不合法")
        except Exception:
            LOGGER.exception("处理网页命令失败: %s", command)
            await self.send_error("INTERNAL", "网关处理命令失败")

    async def _handle_auth(self, command: str, data: dict) -> None:
        account = _text(data.get("account"))
        password = data.get("password", "")
        if not account:
            await self.send_error("INVALID_ACCOUNT", "请输入账号")
            return
        if not isinstance(password, str):
            await self.send_error("BAD_CREDENTIALS", "密码格式不正确")
            return
        if self.session.nickname:
            await self.send_error("ALREADY_LOGGED_IN", "当前网页已经登录")
            return

        await self.ensure_connected()
        try:
            if command == "login":
                await self.session.login(account, password)
            else:
                await self.session.register(account, password)
            await self.refresh_all()
            await self.send({"type": "ack", "command": command})
            await self.send_snapshot()
        except (ServerError, asyncio.TimeoutError, ConnectionError, OSError) as exc:
            if isinstance(exc, ServerError):
                await self.send_error(exc.code, exc.message)
            elif isinstance(exc, asyncio.TimeoutError):
                await self.send_error("TIMEOUT", "登录超时")
            else:
                await self.send_error("CONNECTION_ERROR", f"无法连接服务端: {exc}")

    async def send_error(self, code: str, message: str) -> None:
        await self.send({
            "type": "error",
            "code": code,
            "message": message,
        })

    async def run(self, ws: web.WebSocketResponse) -> None:
        send_task = asyncio.create_task(self._send_loop(ws))
        try:
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    try:
                        payload = json.loads(message.data)
                    except json.JSONDecodeError:
                        await self.send_error("BAD_JSON", "网页消息不是合法 JSON")
                        continue
                    if not isinstance(payload, dict):
                        await self.send_error("BAD_JSON", "网页消息必须是对象")
                        continue
                    await self.handle(payload)
                elif message.type == WSMsgType.ERROR:
                    LOGGER.info("WebSocket 错误: %s", ws.exception())
                    break
        except asyncio.CancelledError:
            raise
        finally:
            self.closed = True
            FILE_BRIDGES.pop(self.file_token, None)
            send_task.cancel()
            await asyncio.gather(send_task, return_exceptions=True)
            await self.session.close()


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    bridge = BrowserBridge(
        request.app["backend_host"],
        request.app["backend_port"],
        request.app["web_port"],
    )
    ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=64 * 1024)
    await ws.prepare(request)
    await bridge.send({"type": "hello", "data": {"websocket": True}})
    await bridge.run(ws)
    return ws


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB_DIR / "index.html")


async def health(request: web.Request) -> web.Response:
    return web.json_response({
        "ok": True,
        "backend": f'{request.app["backend_host"]}:{request.app["backend_port"]}',
    })


async def favicon(request: web.Request) -> web.Response:
    return web.Response(status=204)


def _file_bridge(request: web.Request) -> BrowserBridge | None:
    token = request.query.get("token") or request.headers.get("X-File-Token", "")
    return FILE_BRIDGES.get(token)


async def upload_group_file(request: web.Request) -> web.Response:
    bridge = _file_bridge(request)
    if bridge is None or bridge.session.nickname is None:
        return web.json_response({"error": "请先登录"}, status=401)

    group_id = request.match_info["group_id"]
    if bridge.session.group_state.get("group_id") != group_id:
        return web.json_response({"error": "当前不在目标群聊"}, status=403)

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path: Path | None = None
    try:
        reader = await request.multipart()
        part = await reader.next()
        while part is not None and part.name != "file":
            await part.release()
            part = await reader.next()
        if part is None or not part.filename:
            return web.json_response({"error": "没有收到文件"}, status=400)

        name = Path(part.filename).name[:255]
        suffix = Path(name).suffix.lower()[:10]
        file_id = secrets.token_hex(16)
        path = UPLOAD_DIR / f"{file_id}{suffix}"
        size = 0
        with path.open("wb") as target:
            while True:
                chunk = await part.read_chunk(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise ValueError("文件超过 20 MB 限制")
                target.write(chunk)

        mime = part.headers.get("Content-Type") or (
            mimetypes.guess_type(name)[0] or "application/octet-stream"
        )
        if mime in BLOCKED_MIME:
            raise ValueError("不允许上传 HTML、SVG 或可执行文件")
        kind = "image" if mime.startswith("image/") else "file"
        await bridge.session.register_group_file(
            group_id, file_id, name, mime, size, kind,
        )
        FILE_STORE[file_id] = {
            "path": path,
            "group_id": group_id,
            "name": name,
            "mime": mime,
            "kind": kind,
        }
        return web.json_response({
            "file_id": file_id,
            "name": name,
            "mime": mime,
            "size": size,
            "kind": kind,
            "url": f"/api/attachments/{file_id}",
        })
    except ServerError as exc:
        if path is not None:
            path.unlink(missing_ok=True)
        return web.json_response({"error": exc.message}, status=403)
    except (ValueError, OSError) as exc:
        if path is not None:
            path.unlink(missing_ok=True)
        return web.json_response({"error": str(exc)}, status=400)


async def download_attachment(request: web.Request) -> web.StreamResponse:
    bridge = _file_bridge(request)
    if bridge is None or bridge.session.nickname is None:
        return web.json_response({"error": "请先登录"}, status=401)
    file_id = request.match_info["file_id"]
    item = FILE_STORE.get(file_id)
    if item is None or not item["path"].is_file():
        return web.json_response({"error": "文件不存在"}, status=404)
    if bridge.session.group_state.get("group_id") != item["group_id"]:
        return web.json_response({"error": "无权访问该文件"}, status=403)
    disposition = "inline" if item["kind"] == "image" else "attachment"
    return web.FileResponse(
        item["path"],
        headers={
            "Content-Type": item["mime"],
            "Content-Disposition": f'{disposition}; filename="{item["name"]}"',
        },
    )


async def delete_attachment(request: web.Request) -> web.Response:
    bridge = _file_bridge(request)
    if bridge is None or bridge.session.nickname is None:
        return web.json_response({"error": "请先登录"}, status=401)
    file_id = request.match_info["file_id"]
    item = FILE_STORE.get(file_id)
    if item is None:
        return web.json_response({"error": "文件不存在"}, status=404)
    try:
        await bridge.session.delete_group_file(file_id)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=403)
    item["path"].unlink(missing_ok=True)
    FILE_STORE.pop(file_id, None)
    return web.json_response({"ok": True})


async def qr_image(request: web.Request) -> web.Response:
    text = request.query.get("data", "")
    if not text or len(text) > 2048:
        return web.json_response({"error": "二维码内容不合法"}, status=400)
    image = qrcode.make(text, image_factory=SvgPathImage, box_size=10, border=2)
    buffer = io.BytesIO()
    image.save(buffer)
    return web.Response(body=buffer.getvalue(), content_type="image/svg+xml")


def create_app(backend_host: str, backend_port: int, web_port: int = 8080) -> web.Application:
    app = web.Application(client_max_size=MAX_UPLOAD_BYTES + 1024 * 1024)
    app["backend_host"] = backend_host
    app["backend_port"] = backend_port
    app["web_port"] = web_port
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    app.router.add_get("/", index)
    app.router.add_get("/join/{token}", index)
    app.router.add_get("/api/health", health)
    app.router.add_get("/favicon.ico", favicon)
    app.router.add_get("/api/qr", qr_image)
    app.router.add_post("/api/groups/{group_id}/files", upload_group_file)
    app.router.add_get("/api/attachments/{file_id}", download_attachment)
    app.router.add_delete("/api/attachments/{file_id}", delete_attachment)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/static/", WEB_DIR / "static", show_index=False)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="斗地主聊天室网页版网关")
    parser.add_argument("--host", default=os.environ.get("WEB_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("WEB_PORT", "8080")))
    parser.add_argument(
        "--backend-host",
        default=os.environ.get("WEB_BACKEND_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--backend-port",
        type=int,
        default=int(os.environ.get("WEB_BACKEND_PORT", "9011")),
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    print(f"网页版监听: http://{args.host}:{args.port}", flush=True)
    print(
        f"后端 TCP: {args.backend_host}:{args.backend_port}",
        flush=True,
    )
    web.run_app(
        create_app(args.backend_host, args.backend_port, args.port),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
