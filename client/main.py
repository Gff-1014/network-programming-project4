"""窗口版客户端入口：`python -m client`。"""

from __future__ import annotations

import argparse

from .app import ChatApp
from .bridge import AsyncBridge


def main() -> None:
    parser = argparse.ArgumentParser(description="斗地主聊天室 桌面客户端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9011)
    parser.add_argument("--nickname", default="", help="预填昵称")
    parser.add_argument("--auto-login", action="store_true", help="启动后自动连接")
    parser.add_argument("--room", default="", help="登录后自动进入的房间（如 牌桌1）")
    parser.add_argument("--auto-ready", action="store_true", help="进入牌桌后自动准备（演示用）")
    parser.add_argument("--ui", choices=["ctk", "classic"], default=None,
                        help="界面皮肤：ctk=CustomTkinter（默认），classic=美化前的界面")
    args = parser.parse_args()

    if args.ui:
        import os

        os.environ["DDZ_UI"] = args.ui   # 必须在导入 client.app 之前生效

    from . import app as app_module  # noqa: E402

    bridge = AsyncBridge()
    app = app_module.ChatApp(
        bridge,
        host=args.host,
        port=args.port,
        nickname=args.nickname,
        auto_login=args.auto_login,
        auto_room=args.room,
        auto_ready=args.auto_ready,
    )
    try:
        app.mainloop()
    finally:
        bridge.stop()


if __name__ == "__main__":
    main()
