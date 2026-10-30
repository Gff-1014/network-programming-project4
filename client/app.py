"""Tkinter 桌面客户端：连接页 → 大厅 → 聊天房 / 牌桌。

界面只做两件事：把用户操作交给 `ClientSession`，把会话事件刷到控件上。
所有网络动作都在后台事件循环里执行（见 `client/bridge.py`）。
"""

from __future__ import annotations

import queue
import os
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from game import ai
from game.text import parse_card

from .bridge import AsyncBridge
from .cards_view import COLOR_TABLE_BG, HandCanvas, TableCanvas
from .session import ClientSession

# ====================== [UI-CTK] 界面美化开关（整块可回滚） ======================
# 本次美化新增的全部代码在 client/ui_ctk.py；这里只做"选哪套界面"的判断。
# 回滚：启动加 --ui classic / 设 DDZ_UI=classic，或直接删除 ui_ctk.py。
try:
    from . import ui_ctk

    UI_CTK_ENABLED = ui_ctk.available() and os.environ.get("DDZ_UI", "ctk").lower() != "classic"
except Exception:  # pragma: no cover - 未装依赖或文件被删时退回经典界面
    ui_ctk = None
    UI_CTK_ENABLED = False
# ==============================================================================

FONT_UI = ("Microsoft YaHei UI", 10)
FONT_TITLE = ("Microsoft YaHei UI", 12, "bold")
FONT_LOG = ("Microsoft YaHei UI", 9)


class ChatApp(ui_ctk.root_class() if UI_CTK_ENABLED else tk.Tk):  # [UI-CTK]
    """主窗口：管理各页面、状态栏、动作封装与事件分发。"""
    def __init__(self, bridge: AsyncBridge, host: str = "127.0.0.1", port: int = 9011,
                 nickname: str = "", auto_login: bool = False, auto_room: str = "",
                 auto_ready: bool = False) -> None:
        """创建根窗口、页面容器与状态栏，并启动事件轮询。"""
        super().__init__()
        self.title("斗地主聊天室")
        self.geometry("1000x700")
        self.minsize(900, 620)
        self.bridge = bridge
        self.session = ClientSession(on_event=self._enqueue_event)
        self.events: queue.Queue[tuple[str, dict]] = queue.Queue()
        self.selected: set[int] = set()
        self.countdown_ms: int | None = None

        self.host_var = tk.StringVar(value=host)
        self.port_var = tk.StringVar(value=str(port))
        self.account_var = tk.StringVar(value="")
        self.password_var = tk.StringVar(value="123456")
        self.nick_var = self.account_var          # 兼容旧引用
        self.status_var = tk.StringVar(value="未连接")
        self.chat_var = tk.StringVar(value="")
        self.table_status_var = tk.StringVar(value="")
        self.multiplier_var = tk.StringVar(value="倍数 ×1")
        self.current_var = tk.StringVar(value="")

        self.container = ttk.Frame(self)
        self.container.pack(fill="both", expand=True)
        if UI_CTK_ENABLED:  # [UI-CTK] CustomTkinter 皮肤
            ui_ctk.apply_theme()
            self.configure(fg_color=ui_ctk.APP_BG)   # 根窗口底色与内容一致，避免留出浅色边
            ui_ctk.style_ttk(ttk.Style())
            self.container.configure(style="Ctk.TFrame")
            self.frames = {
                "connect": ui_ctk.ConnectFrame(self.container, self),
                "lobby": ui_ctk.LobbyFrame(self.container, self),
                "room": ui_ctk.RoomFrame(self.container, self),
                "private": ui_ctk.PrivateChatFrame(self.container, self),
                "table": ui_ctk.make_table_frame()(self.container, self),
            }
        else:  # 经典皮肤（本次美化之前的样子）
            self.frames = {
                "connect": ConnectFrame(self.container, self),
                "lobby": LobbyFrame(self.container, self),
                "room": RoomFrame(self.container, self),
                "private": PrivateFrame(self.container, self),
                "table": TableFrame(self.container, self),
            }
        for frame in self.frames.values():
            frame.place(relx=0, rely=0, relwidth=1, relheight=1)

        status = ttk.Label(self, textvariable=self.status_var, anchor="w")
        if UI_CTK_ENABLED:  # [UI-CTK]
            status.configure(style="Ctk.TLabel")
        status.pack(fill="x", side="bottom")
        self.show("connect")
        self.after(50, self._drain_events)
        self.after(200, self._tick_countdown)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        if auto_login and nickname:
            self.nick_var.set(nickname)
            self.after(300, self.do_login)
        if auto_room:
            self.auto_room = auto_room
        else:
            self.auto_room = ""
        self.auto_ready = auto_ready

    # ------------------------------------------------------------ 视图切换 ----

    def show(self, name: str) -> None:
        """把指定页面置顶并触发它的 on_show。"""
        self.frames[name].tkraise()
        self.frames[name].on_show()

    # ------------------------------------------------------------ 线程桥 ----

    def call(self, coro, on_error: str = "操作失败") -> None:
        """提交协程并在出错时提示（回调在主线程里通过事件队列处理）。"""
        future = self.bridge.submit(coro)

        def done(fut) -> None:
            exc = fut.exception()
            if exc is not None:
                self.events.put(("_error", {"message": f"{on_error}: {exc}"}))

        future.add_done_callback(done)

    def _enqueue_event(self, kind: str, data: dict) -> None:
        """后台线程把事件放进线程安全队列。"""
        self.events.put((kind, data))

    def _drain_events(self) -> None:
        """主线程每 50ms 消费一次事件队列。"""
        while True:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle_event(kind, data)
            except Exception as exc:  # pragma: no cover - 界面兜底
                self.status_var.set(f"界面处理 {kind} 出错: {exc}")
        self.after(50, self._drain_events)

    def _handle_event(self, kind: str, data: dict) -> None:
        # 事件由后台 asyncio 线程放进队列，这里在主线程消费：
        # Tk 控件只能在主线程操作，所以所有网络结果都必须经这条路径回到界面。
        if kind == "_error":
            self.status_var.set(data.get("message", "操作失败"))
            messagebox.showerror("操作失败", data.get("message", "操作失败"))
            return
        if kind == "error":
            # 按课程要求：重复昵称、房间不存在、出牌非法等都要给出原因
            code = data.get("code")
            text = f"{data.get('message')}（{code}）"
            self.status_var.set(text)
            if code in ("NICKNAME_TAKEN", "INVALID_NICKNAME", "HANDSHAKE_TIMEOUT",
                        "SESSION_EXPIRED", "NOT_LOGGED_IN"):
                messagebox.showerror("无法登录", text)
            return
        if kind == "logged_in":
            self.status_var.set(f"已登录：{data.get('nickname')}（在线 {data.get('online')}）")
            self.show("lobby")
            if self.auto_room:
                room, self.auto_room = self.auto_room, ""
                self.do_join(room)
        elif kind == "joined":
            room = data.get("room")
            self.status_var.set(f"进入 {room}（{data.get('role')}）")
            if room and room.startswith("牌桌"):
                self.show("table")
                if self.auto_ready and self.session.role == "player":
                    self.auto_ready = False
                    self.after(300, self.do_ready)
            else:
                self.show("room")
        elif kind == "left":
            self.show("lobby")
        elif kind == "logged_out":
            self.status_var.set("已登出")
            self.chat_var.set("")
            self.show("connect")
        elif kind == "group_created":
            self.status_var.set(f"已进入群聊 {data.get('room')}")
            self.show("room")
        elif kind == "group_deleted":
            self.status_var.set(f"群聊 {data.get('room')} 已解散")
            self.show("lobby")
        elif kind == "private_opened":
            self.status_var.set(f"与 {data.get('peer')} 的私聊")
            self.show("private")
        elif kind == "disconnected":
            self.status_var.set("连接已断开")
            messagebox.showwarning("连接断开", "与服务器的连接已断开")
            self.show("connect")
        for name in ("lobby", "room", "private", "table"):
            frame = self.frames.get(name)
            if frame is not None and frame.winfo_ismapped():
                handler = getattr(frame, "on_event", None)
                if handler:
                    handler(kind, data)
        self._refresh_status()

    def _refresh_status(self) -> None:
        """状态栏显示账号、房间、对局与手牌概要。"""
        session = self.session
        bits = []
        if session.nickname:
            bits.append(f"{session.nickname}")
        if session.room:
            bits.append(f"房间 {session.room_name or session.room}")
        if session.table.get("state"):
            bits.append(f"对局 {session.table['state']}")
        if session.hand:
            bits.append(f"手牌 {len(session.hand)}")
        if bits:
            self.status_var.set("　|　".join(bits))

    # ------------------------------------------------------------ 倒计时 ----

    def _tick_countdown(self) -> None:
        remaining = self.session.table.get("deadline_in_ms")
        if remaining is None:
            self.countdown_ms = None
        elif self.countdown_ms is None:
            self.countdown_ms = remaining
        else:
            self.countdown_ms = max(0, self.countdown_ms - 200)
        seconds = "-" if self.countdown_ms is None else f"{self.countdown_ms // 1000}s"
        current = self.session.table.get("current") or "-"
        if self.countdown_ms is None:
            self.current_var.set(f"轮到 {current}　不限时")
        else:
            self.current_var.set(f"轮到 {current}　{seconds}后自动出牌")
        self.after(200, self._tick_countdown)

    # ------------------------------------------------------------ 动作封装 ----

    def do_login(self) -> None:
        host, port = self.host_var.get().strip(), self.port_var.get().strip()
        account, password = self.account_var.get().strip(), self.password_var.get()
        if not account:
            messagebox.showinfo("提示", "请输入账号")
            return
        if not password:
            messagebox.showinfo("提示", "请输入密码")
            return

        async def run() -> None:
            await self.session.connect(host, int(port))
            await self.session.login(account, password)

        self.status_var.set("正在连接…")
        self.call(run(), "登录失败")

    def do_logout(self) -> None:
        """登出账号：服务端释放账号，客户端回到登录页。"""
        if not messagebox.askyesno("登出", f"确定要登出账号 {self.session.account or ''} 吗？"):
            return
        self.call(self.session.logout(), "登出失败")

    def do_register(self) -> None:
        """用登录页填的账号 + 密码创建账号，成功后直接进入大厅。"""
        host, port = self.host_var.get().strip(), self.port_var.get().strip()
        account, password = self.account_var.get().strip(), self.password_var.get()
        if not account or not password:
            messagebox.showinfo("提示", "创建账号请输入账号与密码（密码至少 4 位）")
            return

        async def run() -> None:
            await self.session.connect(host, int(port))
            await self.session.register(account, password)

        self.call(run(), "创建账号失败")

    def do_join(self, room: str) -> None:
        async def run() -> None:
            try:
                await self.session.join_room(room)
            except Exception as exc:
                if "ROOM_FULL" in str(exc):
                    await self.session.spectate(room)
                    return
                raise

        self.call(run(), f"进入 {room} 失败")

    def do_leave(self) -> None:
        self.call(self.session.leave_room(), "离开房间失败")

    def do_say(self) -> None:
        text = self.chat_var.get().strip()
        if not text:
            return
        self.chat_var.set("")
        self.call(self.session.say(text), "发送失败")

    def do_whisper(self, to: str) -> None:
        """点在线玩家 → 打开与他的私聊界面。"""
        self.do_open_private(to)

    def do_open_private(self, peer: str) -> None:
        """打开与某人的私聊界面（会拉取历史消息）。"""
        if not peer:
            messagebox.showinfo("提示", "请先选择一位用户")
            return
        self.call(self.session.open_private(peer), "打开私聊失败")

    def do_send_private(self) -> None:
        """在私聊界面里发送一条消息。"""
        text = self.chat_var.get().strip()
        if not text:
            return
        self.chat_var.set("")
        self.call(self.session.send_private(text), "私信失败")

    def do_refresh_private(self) -> None:
        """重新拉取当前私聊的历史消息。"""
        peer = self.session.current_peer
        if peer:
            self.call(self.session.open_private(peer), "刷新私聊记录失败")

    def do_create_group(self) -> None:
        """邀请在线用户创建群聊。"""
        from .dialogs import choose_members

        source = self.session.online_users or self.session.room_users
        candidates = [name for name in source
                      if name != self.session.nickname]
        if not candidates:
            messagebox.showinfo("提示", "当前没有其他在线玩家")
            return
        picked = choose_members(self, candidates, use_ctk=UI_CTK_ENABLED)
        if picked:
            self.call(self.session.create_group(picked), "创建群聊失败")

    def do_create_channel(self) -> None:
        """公共频道管理员创建频道；服务端会再次校验账号权限。"""
        if self.session.nickname != "wanjia1":
            messagebox.showinfo("提示", "只有 wanjia1 可以创建公共频道")
            return
        name = simpledialog.askstring(
            "创建公共频道",
            "输入频道名称（最多 32 个字符）：",
            parent=self,
        )
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showinfo("提示", "频道名称不能为空")
            return
        if len(name) > 32:
            messagebox.showinfo("提示", "频道名称不能超过 32 个字符")
            return
        self.call(self.session.create_channel(name), "创建频道失败")

    def do_refresh_users(self) -> None:
        """手动刷新在线玩家（广播偶尔会错过，留一个手动入口）。"""
        self.call(self.session.refresh_users(), "刷新在线玩家失败")

    def do_delete_group(self) -> None:
        """解散当前群聊（仅群主）。"""
        room = self.session.room or ""
        if not messagebox.askyesno("解散群聊", f"确定要解散 {room} 吗？所有成员会回到大厅。"):
            return
        self.call(self.session.delete_group(room), "解散群聊失败")

    def do_ready(self) -> None:
        if self.session.role != "player":
            messagebox.showinfo("提示", "观战者不需要准备")
            return
        self.call(self.session.ready(), "准备失败")

    def do_bid(self, score: int) -> None:
        self.call(self.session.bid(score), "叫分失败")

    def do_play(self) -> None:
        cards = sorted(self.selected)
        if not cards:
            messagebox.showinfo("提示", "请先点选要出的牌")
            return
        self.selected.clear()
        self.call(self.session.play(cards), "出牌失败")
        self.frames["table"].render_hand()

    def do_pass(self) -> None:
        self.call(self.session.pass_turn(), "过牌失败")

    def toggle_auto(self) -> None:
        self.call(self.session.set_auto(not self.session.auto_play), "切换托管失败")

    def do_hint(self) -> None:
        """出牌提示：本地用规则引擎算一手能出的牌（不依赖服务端）。"""
        from game.text import parse_card

        table_cards = [parse_card(code) for code in self.session.table.get("table") or []]
        suggestion = ai.choose_play(self.session.hand, table_cards)
        if not suggestion:
            messagebox.showinfo("提示", "这手牌压不过，建议过牌")
            return
        self.selected = set(suggestion)
        self.frames["table"].render_hand()

    def on_close(self) -> None:
        try:
            self.bridge.submit(self.session.close())
        finally:
            self.bridge.stop()
            self.destroy()


class BaseFrame(ttk.Frame):
    """页面基类：统一 on_show/on_event 接口。"""
    def __init__(self, parent, app: ChatApp) -> None:
        super().__init__(parent, padding=8)
        self.app = app

    def on_show(self) -> None:  # pragma: no cover - 子类覆盖
        return

    def on_event(self, kind: str, data: dict) -> None:  # pragma: no cover - 子类覆盖
        return


class ConnectFrame(BaseFrame):
    """登录页：账号密码登录、创建账号。"""
    def __init__(self, parent, app: ChatApp) -> None:
        super().__init__(parent, app)
        box = ttk.LabelFrame(self, text="登录", padding=16)
        box.place(relx=0.5, rely=0.45, anchor="center")

        ttk.Label(box, text="账号", font=FONT_UI).grid(row=0, column=0, sticky="e", pady=6)
        account_entry = ttk.Entry(box, textvariable=app.account_var, width=22)
        account_entry.grid(row=0, column=1, sticky="w")
        ttk.Label(box, text="密码", font=FONT_UI).grid(row=1, column=0, sticky="e", pady=6)
        pwd_entry = ttk.Entry(box, textvariable=app.password_var, width=22, show="*")
        pwd_entry.grid(row=1, column=1, sticky="w")
        ttk.Button(box, text="登录", command=app.do_login).grid(row=2, column=0, columnspan=2,
                                                               pady=(12, 0), sticky="ew")
        ttk.Button(box, text="创建账号", command=app.do_register).grid(row=3, column=0, columnspan=2,
                                                                     pady=(6, 0), sticky="ew")
        hint = ttk.Label(box, text="预置账号 wanjia1 ~ wanjia5，初始密码 123456", font=FONT_LOG)
        hint.grid(row=4, column=0, columnspan=2, pady=(10, 0))
        self.hint = hint
        app.password_entry = pwd_entry
        account_entry.focus_set()
        account_entry.bind("<Return>", lambda _event: pwd_entry.focus_set())
        pwd_entry.bind("<Return>", lambda _event: app.do_login())

    def on_event(self, kind: str, data: dict) -> None:
        if kind == "error":
            self.hint.configure(
                text=f"{data.get('message')}（{data.get('code')}）",
                foreground="#C0392B",
            )
        elif kind == "logged_out":
            self.hint.configure(text="已登出，可换账号登录", foreground="#2E7D32")
            self.app.account_var.set("")
            self.app.password_var.set("")


class LobbyFrame(BaseFrame):
    """大厅：房间、在线玩家、私聊会话、建群与登出。"""
    def __init__(self, parent, app: ChatApp) -> None:
        super().__init__(parent, app)
        header = ttk.Frame(self)
        header.pack(fill="x")
        ttk.Label(header, text="大厅", font=FONT_TITLE).pack(side="left")
        self.account_var_display = tk.StringVar(value="当前账号：-")
        ttk.Label(header, textvariable=self.account_var_display, font=FONT_UI).pack(side="left", padx=16)
        self.btn_create_channel = ttk.Button(
            header, text="+ 频道", command=app.do_create_channel
        )
        ttk.Button(header, text="登出账号", command=app.do_logout).pack(side="right")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, pady=8)

        left = ttk.LabelFrame(body, text="房间", padding=8)
        left.pack(side="left", fill="both", expand=True)
        self.room_list = tk.Listbox(left, font=FONT_UI, height=12)
        self._room_keys: list[str] = []
        self.room_list.pack(fill="both", expand=True)
        ttk.Button(left, text="进入选中房间", command=self._join_selected).pack(fill="x", pady=(8, 0))
        ttk.Button(left, text="刷新", command=self._refresh).pack(fill="x", pady=4)
        ttk.Label(left, text="私聊会话（点一下打开）", font=FONT_LOG).pack(anchor="w", pady=(8, 2))
        self.conv_list = tk.Listbox(left, font=FONT_UI, height=6, exportselection=False)
        self.conv_list.pack(fill="x")
        self.conv_list.bind("<Double-Button-1>", lambda _event: self._open_conversation())
        ttk.Button(left, text="打开选中会话", command=self._open_conversation).pack(fill="x", pady=4)

        right = ttk.LabelFrame(body, text="在线玩家（点一下即可私聊）", padding=8)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.user_list = tk.Listbox(right, font=FONT_UI, height=12)
        self.user_list.pack(fill="both", expand=True)
        self.user_list.bind("<Double-Button-1>", lambda _event: self._whisper_selected())
        ttk.Button(right, text="私聊选中用户", command=self._whisper_selected).pack(fill="x", pady=(8, 0))
        ttk.Button(right, text="邀请建群", command=app.do_create_group).pack(fill="x", pady=4)
        ttk.Button(right, text="刷新在线玩家", command=app.do_refresh_users).pack(fill="x", pady=4)

    def _whisper_selected(self) -> None:
        selection = self.user_list.curselection()
        if not selection:
            messagebox.showinfo("提示", "请先选中一位在线用户")
            return
        self.app.do_whisper(self.user_list.get(selection[0]))

    def _open_conversation(self) -> None:
        selection = self.conv_list.curselection()
        if not selection:
            messagebox.showinfo("提示", "请先选择一个私聊会话")
            return
        item = self.app.session.conversations[selection[0]]
        self.app.do_open_private(item["peer"])

    def on_show(self) -> None:
        self.account_var_display.set(f"当前账号：{self.app.session.account or '-'}")
        if self.app.session.nickname == "wanjia1":
            self.btn_create_channel.pack(side="right", padx=(0, 8))
        else:
            self.btn_create_channel.pack_forget()
        self._refresh()
        self._render_conversations()
        self.app.call(self.app.session.refresh_users(), "刷新在线玩家失败")

    def _render_conversations(self) -> None:
        """左侧显示"与谁聊过"的会话列表（私聊后自动出现）。"""
        self.conv_list.delete(0, tk.END)
        for item in self.app.session.conversations:
            preview = item.get("last_text") or ""
            preview = preview if len(preview) <= 14 else preview[:14] + "…"
            self.conv_list.insert(tk.END, f"{item['peer']}：{preview}")

    def _refresh(self) -> None:
        self.app.call(self.app.session.refresh_rooms(), "刷新房间失败")

    def _join_selected(self) -> None:
        selection = self.room_list.curselection()
        if not selection:
            messagebox.showinfo("提示", "请先选择一个房间")
            return
        self.app.do_join(self._room_keys[selection[0]])

    def on_event(self, kind: str, data: dict) -> None:
        if kind == "room_list":
            self.room_list.delete(0, tk.END)
            self._room_keys = []
            for room in data.get("rooms", []):
                kind_label = {"lobby": "大厅", "chat": "聊天", "doudizhu": "牌桌",
                              "group": "群聊", "channel": "公共频道"}.get(
                                  room.get("kind"), room.get("kind"))
                self._room_keys.append(room.get("room_key", room["name"]))
                self.room_list.insert(tk.END, f"{room['name']}（{kind_label}・{room['count']} 人）")
        elif kind == "room_users":
            if data.get("room") == self.app.session.room:
                self._render_users()
        elif kind == "user_list":
            self._render_users()
        elif kind in ("conversations", "private_msg", "private_opened"):
            self._render_conversations()

    def _render_users(self) -> None:
        """在线玩家 = 全服在线（拿不到时退回本房间成员）。"""
        names = self.app.session.online_users or self.app.session.room_users
        self.user_list.delete(0, tk.END)
        for name in names:
            label = f"{name}（我）" if name == self.app.session.nickname else name
            self.user_list.insert(tk.END, label)


class RoomFrame(BaseFrame):
    """聊天室/群聊页面。"""
    def __init__(self, parent, app: ChatApp) -> None:
        super().__init__(parent, app)
        header = ttk.Frame(self)
        header.pack(fill="x")
        self.title_var = tk.StringVar(value="聊天房")
        ttk.Label(header, textvariable=self.title_var, font=FONT_TITLE).pack(side="left")
        self.group_var = tk.StringVar(value="")
        ttk.Label(header, textvariable=self.group_var, font=FONT_UI).pack(side="left", padx=12)
        ttk.Button(header, text="离开房间", command=app.do_leave).pack(side="right")
        self.btn_rename = ttk.Button(header, text="修改群名", command=self._rename_group)
        self.btn_dismiss = ttk.Button(header, text="解散群聊", command=app.do_delete_group)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, pady=8)
        self.log = tk.Text(body, font=FONT_LOG, state="disabled", wrap="word")
        self.log.pack(side="left", fill="both", expand=True)
        users = ttk.LabelFrame(body, text="房间里的人", padding=6)
        users.pack(side="left", fill="y", padx=(8, 0))
        self.user_list = tk.Listbox(users, font=FONT_UI, width=16, height=18)
        self.user_list.pack(fill="y", expand=True)
        ttk.Button(users, text="私信选中用户", command=self._whisper).pack(fill="x", pady=(6, 0))

        bottom = ttk.Frame(self)
        bottom.pack(fill="x")
        entry = ttk.Entry(bottom, textvariable=app.chat_var, font=FONT_UI)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _event: app.do_say())
        ttk.Button(bottom, text="发送", command=app.do_say).pack(side="left", padx=6)

    def _whisper(self) -> None:
        selection = self.user_list.curselection()
        target = self.user_list.get(selection[0]) if selection else ""
        self.app.do_whisper(target)

    def on_show(self) -> None:
        self.title_var.set(f"聊天房 - {self.app.session.room_name or self.app.session.room}")
        self._sync_group_controls()
        self.refresh_users()

    def _sync_group_controls(self) -> None:
        """群聊：显示群主；只有群主能看到"解散群聊"。"""
        session = self.app.session
        room = next(
            (r for r in session.rooms
             if r.get("room_key", r.get("name")) == session.room),
            None,
        )
        is_group = bool(room and room.get("kind") == "group")
        if is_group:
            owner = room.get("owner") or "-"
            self.group_var.set(f"群聊（群主：{owner}）")
            if owner == session.nickname:
                self.btn_rename.pack(side="right", padx=(0, 8))
                self.btn_dismiss.pack(side="right", padx=(0, 8))
            else:
                self.btn_rename.pack_forget()
                self.btn_dismiss.pack_forget()
        else:
            self.group_var.set("")
            self.btn_rename.pack_forget()
            self.btn_dismiss.pack_forget()

    def _rename_group(self) -> None:
        """群主输入新的显示群名；显示名可以和其他群重复。"""
        name = simpledialog.askstring(
            "修改群名",
            "输入新的群名：",
            initialvalue=self.app.session.room_name or self.app.session.room or "",
            parent=self,
        )
        if name is not None:
            self.app.call(self.app.session.rename_group(name), "修改群名失败")

    def refresh_users(self) -> None:
        self.user_list.delete(0, tk.END)
        for user in self.app.session.room_users:
            self.user_list.insert(tk.END, user)

    def on_event(self, kind: str, data: dict) -> None:
        if kind == "room_users" and data.get("room") == self.app.session.room:
            self.refresh_users()
        if kind == "room_history":
            for message in data.get("messages") or []:
                speaker = "我" if message.get("from") == self.app.session.nickname else message.get("from")
                self.append_message(f"{speaker}: {message.get('text', '')}")
        if kind in ("room_list", "joined", "group_created", "group_renamed"):
            self._sync_group_controls()
        self.append_log(data.get("_line", ""))

    def append_log(self, text: str) -> None:
        if not text:
            return
        self.log.configure(state="normal")
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.log.configure(state="disabled")


class PrivateFrame(BaseFrame):
    """私聊界面：显示与某人的历史消息，底部输入发送。"""

    def __init__(self, parent, app: ChatApp) -> None:
        super().__init__(parent, app)
        header = ttk.Frame(self)
        header.pack(fill="x")
        self.title_var = tk.StringVar(value="私聊")
        ttk.Label(header, textvariable=self.title_var, font=FONT_TITLE).pack(side="left")
        ttk.Button(header, text="返回大厅", command=lambda: app.show("lobby")).pack(side="right")
        ttk.Button(header, text="刷新记录", command=app.do_refresh_private).pack(
            side="right", padx=(0, 8))

        self.log = tk.Text(self, font=FONT_LOG, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, pady=8)
        self.log.tag_configure("me", foreground="#2F6FED")
        self.log.tag_configure("peer", foreground="#1A1A1A")

        bottom = ttk.Frame(self)
        bottom.pack(fill="x")
        entry = ttk.Entry(bottom, textvariable=app.chat_var, font=FONT_UI)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _event: app.do_send_private())
        ttk.Button(bottom, text="发送", command=app.do_send_private).pack(side="left", padx=6)
        self.chat_entry = entry

    def on_show(self) -> None:
        self.render()

    def render(self) -> None:
        session = self.app.session
        peer = session.current_peer or "-"
        self.title_var.set(f"与 {peer} 的私聊")
        self.log.configure(state="normal")
        self.log.delete("1.0", tk.END)
        for message in session.private_history.get(peer, []):
            mine = message.get("from") == session.nickname
            who = "我" if mine else message.get("from")
            self.log.insert(tk.END, f"{who}: {message.get('text')}\n", "me" if mine else "peer")
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def on_event(self, kind: str, data: dict) -> None:
        if kind in ("private_opened", "history", "private_msg"):
            if (data.get("peer") or self.app.session.current_peer) == self.app.session.current_peer:
                self.render()


class TableFrame(BaseFrame):
    """牌桌视图：布局与渲染逻辑固定，控件创建集中在 `_build_*` 钩子里。

    这样换皮肤（见 client/ui_ctk.py）只需覆写钩子，不用改一行业务逻辑。
    """

    def __init__(self, parent, app: ChatApp) -> None:
        super().__init__(parent, app)
        self._build_header()
        self._build_seats()
        self._build_center()
        self._build_chat()
        self._build_hand()
        self._build_actions()
        self._apply_theme()

    # ------------------------------------------------- 控件创建（换皮肤处） ----

    def _build_header(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x")
        self.header_frame = header
        self.title_var = tk.StringVar(value="牌桌")
        ttk.Label(header, textvariable=self.title_var, font=FONT_TITLE).pack(side="left")
        ttk.Label(header, textvariable=self.app.multiplier_var, font=FONT_UI).pack(side="left", padx=12)
        ttk.Label(header, textvariable=self.app.current_var, font=FONT_UI).pack(side="left")
        self.btn_leave = ttk.Button(header, text="离开牌桌", command=self.app.do_leave)
        self.btn_leave.pack(side="right")

    def _build_seats(self) -> None:
        seats = ttk.Frame(self)
        seats.pack(fill="x", pady=6)
        self.seats_frame = seats
        self.seat_vars: list[tk.StringVar] = []
        self.seat_labels: list[ttk.Label] = []
        for index in range(3):
            var = tk.StringVar(value=f"座位 {index + 1}：等待玩家…")
            label = ttk.Label(seats, textvariable=var, font=FONT_UI, relief="groove", padding=6,
                              anchor="center", justify="center")
            label.pack(
                side="left", fill="x", expand=True, padx=4
            )
            self.seat_vars.append(var)
            self.seat_labels.append(label)

    def _build_center(self) -> None:
        middle = ttk.Frame(self)
        middle.pack(fill="both", expand=True)
        self.middle_frame = middle
        self.table_canvas = TableCanvas(middle)
        self.table_canvas.pack(fill="x", pady=(0, 6))
        self.log_box = tk.Text(middle, font=FONT_LOG, height=12, state="disabled", wrap="word")
        self.log_box.pack(fill="both", expand=True, pady=6)
        self._configure_log_tags()

    def _configure_log_tags(self) -> None:
        """牌局日志按来源着色（自己/系统/警报）。"""
        self.log_box.tag_configure("chat", foreground="#1A1A1A")
        self.log_box.tag_configure("me", foreground="#2F6FED")
        self.log_box.tag_configure("system", foreground="#808080")
        self.log_box.tag_configure("alert", foreground="#C0392B")

    def _build_chat(self) -> None:
        """牌局内聊天：默认发到房间，也可以只发给某个人。"""
        chat_row = ttk.Frame(self)
        chat_row.pack(fill="x", pady=(6, 0))
        self.chat_row = chat_row
        self.chat_target = ttk.Combobox(chat_row, values=["（发到房间）"], width=14, state="readonly")
        self.chat_target.current(0)
        self.chat_target.pack(side="left")
        chat_entry = ttk.Entry(chat_row, textvariable=self.app.chat_var, font=FONT_UI)
        chat_entry.pack(side="left", fill="x", expand=True, padx=6)
        chat_entry.bind("<Return>", lambda _event: self.send_chat())
        ttk.Button(chat_row, text="发送", command=self.send_chat).pack(side="left")
        self.chat_entry = chat_entry

    def _build_hand(self) -> None:
        hand_box = ttk.LabelFrame(self, text="我的手牌（点击选牌）", padding=6)
        hand_box.pack(fill="x")
        self.hand_box = hand_box
        self.hand_canvas = HandCanvas(hand_box, on_toggle=self._toggle)
        self.hand_canvas.pack(fill="x")

    def _build_actions(self) -> None:
        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=8)
        self.actions_frame = actions
        self.buttons: dict[str, ttk.Button] = {}
        for key, text, command in (
            ("ready", "准备", self.app.do_ready),
            ("bid0", "不叫", lambda: self.app.do_bid(0)),
            ("bid1", "1 分", lambda: self.app.do_bid(1)),
            ("bid2", "2 分", lambda: self.app.do_bid(2)),
            ("bid3", "3 分", lambda: self.app.do_bid(3)),
            ("play", "出牌", self.app.do_play),
            ("pass", "过牌", self.app.do_pass),
            ("hint", "提示", self.app.do_hint),
            ("auto", "托管", self.app.toggle_auto),
        ):
            button = ttk.Button(actions, text=text, command=command)
            button.pack(side="left", padx=4)
            self.buttons[key] = button

    def _apply_theme(self) -> None:
        """经典皮肤：不改配色（CustomTkinter 皮肤在 ui_ctk.py 里覆写）。"""
        return

    def _set_button_enabled(self, key: str, enabled: bool) -> None:
        self.buttons[key].state(["!disabled"] if enabled else ["disabled"])

    def _set_button_text(self, key: str, text: str) -> None:
        self.buttons[key].configure(text=text)

    # ------------------------------------------------------------ 渲染 ----

    def on_show(self) -> None:
        self.title_var.set(
            f"牌桌 - {self.app.session.room_name or self.app.session.room}"
            f"（{self.app.session.role}）"
        )
        self.render_hand()
        self.render_state()

    def render_hand(self) -> None:
        session = self.app.session
        self.hand_canvas.set_cards(
            [] if session.role == "spectator" else session.hand,
            self.app.selected,
        )

    def _toggle(self, card: int) -> None:
        if card in self.app.selected:
            self.app.selected.discard(card)
        else:
            self.app.selected.add(card)
        self.render_hand()

    def render_state(self) -> None:
        session = self.app.session
        state = session.table
        self.table_canvas.set_state(
            [parse_card(code) for code in state.get("table") or []],
            [parse_card(code) for code in state.get("bottom") or []],
            state.get("table_owner"),
        )
        self.app.multiplier_var.set(f"倍数 ×{state.get('multiplier', 1)}")

        # 三个座位都显示（含自己），并标出地主、我、托管中、离线、当前轮到谁
        players = list(state.get("players") or [])
        counts = state.get("hand_counts") or {}
        online_map = {seat["nickname"]: seat.get("online", True)
                      for seat in (state.get("seats") or [])}
        autos = set(state.get("auto_players") or [])
        ready_players = set(state.get("ready") or [])
        for index, var in enumerate(self.seat_vars):
            if index >= len(players):
                var.set(f"座位 {index + 1}：空座")
                continue
            name = players[index]
            marks = []
            if name == state.get("landlord"):
                marks.append("地主")
            if name == session.nickname:
                marks.append("我")
            if name in ready_players:
                marks.append("已准备")
            if name in autos:
                marks.append("托管中")
            if not online_map.get(name, True):
                marks.append("离线")
            if name == state.get("current"):
                marks.append("当前")
            suffix = f"（{'、'.join(marks)}）" if marks else ""
            var.set(f"{name}{suffix}\n剩 {counts.get(name, 0)} 张")

        state_name = state.get("state", "WAITING")
        my_turn = session.my_turn
        in_match = state_name in ("BIDDING", "PLAYING")
        my_ready = session.nickname in ready_players
        self._set_button_text("ready", "已准备" if my_ready else "准备")
        self._set_button_enabled(
            "ready",
            not in_match and session.role == "player" and not my_ready,
        )
        self._set_button_text("auto", "取回控制" if session.auto_play else "托管")
        self._set_button_enabled("auto", session.role == "player" and in_match)
        auto_names = state.get("auto_players") or []
        if session.nickname in auto_names:
            self.app.current_var.set(f"{self.app.current_var.get()}　（我托管中）")
        for key in ("bid0", "bid1", "bid2", "bid3"):
            enabled = state_name == "BIDDING" and my_turn and session.role == "player"
            self._set_button_enabled(key, enabled)
        for key in ("play", "pass", "hint"):
            enabled = state_name == "PLAYING" and my_turn and session.role == "player"
            self._set_button_enabled(key, enabled)

    def append_log(self, text: str) -> None:
        self.append_line(text)

    def append_line(self, text: str, kind: str = "") -> None:
        if not text:
            return
        if kind == "room_msg":
            tag = "me" if text.startswith("我:") else "chat"
        elif kind == "private_msg":
            tag = "chat"
        elif kind in ("game_over", "error"):
            tag = "alert"
        elif kind in ("timeout", "user_reconnecting", "auto_play", "user_left", "user_joined"):
            tag = "system"
        else:
            tag = "chat"
        self.log_box.configure(state="normal")
        self.log_box.insert(tk.END, text + "\n", tag)
        self.log_box.see(tk.END)
        self.log_box.configure(state="disabled")

    def send_chat(self) -> None:
        """把输入框内容发出去：下拉选"发到房间"走房间广播，否则发给选中的人。"""
        text = self.app.chat_var.get().strip()
        if not text:
            return
        target = self.chat_target.get()
        self.app.chat_var.set("")
        if target.startswith("（"):
            self.app.call(self.app.session.say(text), "发送失败")
        else:
            self.app.call(self.app.session.whisper(target, text), "私信失败")

    def refresh_chat_targets(self) -> None:
        names = list(self.app.session.table.get("players") or [])
        others = [name for name in names if name != self.app.session.nickname]
        self.chat_target.configure(values=["（发到房间）"] + others)
        if self.chat_target.get() not in self.chat_target["values"]:
            self.chat_target.current(0)

    def on_event(self, kind: str, data: dict) -> None:
        if kind in ("table_state", "landlord", "game_start", "hand", "play",
                    "pass", "table_cleared", "bid", "timeout", "game_over"):
            if kind in ("game_start", "play"):
                self.app.selected.clear()
            self.render_hand()
            self.render_state()
            if kind in ("game_over",) and data.get("scores"):
                scores = data["scores"]
                mine = scores.get(self.app.session.nickname)
                messagebox.showinfo(
                    "本局结束",
                    f"赢家：{data.get('winner')}\n我的得分：{mine:+d}" if mine is not None else f"赢家：{data.get('winner')}",
                )
        if kind in ("landlord", "bid", "play", "pass", "table_cleared", "timeout",
                    "game_over", "user_reconnecting", "auto_play",
                    "room_msg", "private_msg", "user_joined", "user_left", "error"):
            self.append_line(data.get("_line", ""), kind)
        if kind == "table_state":
            self.refresh_chat_targets()
