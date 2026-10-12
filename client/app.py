"""Tkinter 桌面客户端：连接页 → 大厅 → 聊天房 / 牌桌。

界面只做两件事：把用户操作交给 `ClientSession`，把会话事件刷到控件上。
所有网络动作都在后台事件循环里执行（见 `client/bridge.py`）。
"""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import messagebox, ttk

from game import ai
from game.text import parse_card

from .bridge import AsyncBridge
from .cards_view import COLOR_TABLE_BG, HandCanvas, TableCanvas
from .session import ClientSession

FONT_UI = ("Microsoft YaHei UI", 10)
FONT_TITLE = ("Microsoft YaHei UI", 12, "bold")
FONT_LOG = ("Microsoft YaHei UI", 9)


class ChatApp(tk.Tk):
    def __init__(self, bridge: AsyncBridge, host: str = "127.0.0.1", port: int = 9011,
                 nickname: str = "", auto_login: bool = False, auto_room: str = "",
                 auto_ready: bool = False) -> None:
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
        self.nick_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="未连接")
        self.chat_var = tk.StringVar(value="")
        self.table_status_var = tk.StringVar(value="")
        self.multiplier_var = tk.StringVar(value="倍数 ×1")
        self.current_var = tk.StringVar(value="")

        self.container = ttk.Frame(self)
        self.container.pack(fill="both", expand=True)
        self.frames: dict[str, ttk.Frame] = {
            "connect": ConnectFrame(self.container, self),
            "lobby": LobbyFrame(self.container, self),
            "room": RoomFrame(self.container, self),
            "table": TableFrame(self.container, self),
        }
        for frame in self.frames.values():
            frame.place(relx=0, rely=0, relwidth=1, relheight=1)

        ttk.Label(self, textvariable=self.status_var, anchor="w").pack(fill="x", side="bottom")
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
        self.events.put((kind, data))

    def _drain_events(self) -> None:
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
        elif kind == "disconnected":
            self.status_var.set("连接已断开")
            messagebox.showwarning("连接断开", "与服务器的连接已断开")
            self.show("connect")
        for name in ("lobby", "room", "table"):
            frame = self.frames.get(name)
            if frame is not None and frame.winfo_ismapped():
                handler = getattr(frame, "on_event", None)
                if handler:
                    handler(kind, data)
        self._refresh_status()

    def _refresh_status(self) -> None:
        session = self.session
        bits = []
        if session.nickname:
            bits.append(f"{session.nickname}")
        if session.room:
            bits.append(f"房间 {session.room}")
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
        host, port, nick = self.host_var.get().strip(), self.port_var.get().strip(), self.nick_var.get().strip()
        if not nick:
            messagebox.showinfo("提示", "请输入昵称")
            return

        async def run() -> None:
            await self.session.connect(host, int(port))
            await self.session.login(nick)

        self.status_var.set("正在连接…")
        self.call(run(), "连接失败")

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
        text = self.chat_var.get().strip()
        if not text or not to:
            messagebox.showinfo("提示", "先在右侧选择一位用户，再输入私信内容")
            return
        self.chat_var.set("")
        self.call(self.session.whisper(to, text), "私信失败")

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
    def __init__(self, parent, app: ChatApp) -> None:
        super().__init__(parent, padding=8)
        self.app = app

    def on_show(self) -> None:  # pragma: no cover - 子类覆盖
        return

    def on_event(self, kind: str, data: dict) -> None:  # pragma: no cover - 子类覆盖
        return


class ConnectFrame(BaseFrame):
    def __init__(self, parent, app: ChatApp) -> None:
        super().__init__(parent, app)
        box = ttk.LabelFrame(self, text="连接服务器", padding=16)
        box.place(relx=0.5, rely=0.45, anchor="center")

        ttk.Label(box, text="服务器地址", font=FONT_UI).grid(row=0, column=0, sticky="e", pady=6)
        ttk.Entry(box, textvariable=app.host_var, width=18).grid(row=0, column=1)
        ttk.Label(box, text="端口", font=FONT_UI).grid(row=0, column=2, padx=(12, 4))
        ttk.Entry(box, textvariable=app.port_var, width=8).grid(row=0, column=3)
        ttk.Label(box, text="昵称", font=FONT_UI).grid(row=1, column=0, sticky="e", pady=6)
        ttk.Entry(box, textvariable=app.nick_var, width=18).grid(row=1, column=1, columnspan=3, sticky="w")
        ttk.Button(box, text="连接", command=app.do_login).grid(row=2, column=0, columnspan=4, pady=(12, 0))
        ttk.Label(box, text="昵称重复、房间不存在等情况会在这里给出原因", font=FONT_LOG).grid(
            row=3, column=0, columnspan=4, pady=(10, 0)
        )


class LobbyFrame(BaseFrame):
    def __init__(self, parent, app: ChatApp) -> None:
        super().__init__(parent, app)
        ttk.Label(self, text="大厅", font=FONT_TITLE).pack(anchor="w")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, pady=8)

        left = ttk.LabelFrame(body, text="房间", padding=8)
        left.pack(side="left", fill="both", expand=True)
        self.room_list = tk.Listbox(left, font=FONT_UI, height=12)
        self.room_list.pack(fill="both", expand=True)
        ttk.Button(left, text="进入选中房间", command=self._join_selected).pack(fill="x", pady=(8, 0))
        ttk.Button(left, text="刷新", command=self._refresh).pack(fill="x", pady=4)

        right = ttk.LabelFrame(body, text="在线用户", padding=8)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.user_list = tk.Listbox(right, font=FONT_UI, height=12)
        self.user_list.pack(fill="both", expand=True)

    def on_show(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        self.app.call(self.app.session.refresh_rooms(), "刷新房间失败")

    def _join_selected(self) -> None:
        selection = self.room_list.curselection()
        if not selection:
            messagebox.showinfo("提示", "请先选择一个房间")
            return
        text = self.room_list.get(selection[0])
        self.app.do_join(text.split("（")[0])

    def on_event(self, kind: str, data: dict) -> None:
        if kind == "room_list":
            self.room_list.delete(0, tk.END)
            for room in data.get("rooms", []):
                kind_label = {"lobby": "大厅", "chat": "聊天", "doudizhu": "牌桌"}.get(
                    room.get("kind"), room.get("kind")
                )
                self.room_list.insert(tk.END, f"{room['name']}（{kind_label}・{room['count']} 人）")
        elif kind == "room_users":
            if data.get("room") == self.app.session.room:
                self.user_list.delete(0, tk.END)
                for user in data.get("users", []):
                    self.user_list.insert(tk.END, user)


class RoomFrame(BaseFrame):
    def __init__(self, parent, app: ChatApp) -> None:
        super().__init__(parent, app)
        header = ttk.Frame(self)
        header.pack(fill="x")
        self.title_var = tk.StringVar(value="聊天房")
        ttk.Label(header, textvariable=self.title_var, font=FONT_TITLE).pack(side="left")
        ttk.Button(header, text="离开房间", command=app.do_leave).pack(side="right")

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
        self.title_var.set(f"聊天房 - {self.app.session.room}")
        self.refresh_users()

    def refresh_users(self) -> None:
        self.user_list.delete(0, tk.END)
        for user in self.app.session.room_users:
            self.user_list.insert(tk.END, user)

    def on_event(self, kind: str, data: dict) -> None:
        if kind == "room_users" and data.get("room") == self.app.session.room:
            self.refresh_users()
        self.append_log(data.get("_line", ""))

    def append_log(self, text: str) -> None:
        if not text:
            return
        self.log.configure(state="normal")
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.log.configure(state="disabled")


class TableFrame(BaseFrame):
    def __init__(self, parent, app: ChatApp) -> None:
        super().__init__(parent, app)
        header = ttk.Frame(self)
        header.pack(fill="x")
        self.title_var = tk.StringVar(value="牌桌")
        ttk.Label(header, textvariable=self.title_var, font=FONT_TITLE).pack(side="left")
        ttk.Label(header, textvariable=app.multiplier_var, font=FONT_UI).pack(side="left", padx=12)
        ttk.Label(header, textvariable=app.current_var, font=FONT_UI).pack(side="left")
        ttk.Button(header, text="离开牌桌", command=app.do_leave).pack(side="right")

        # 对手信息
        seats = ttk.Frame(self)
        seats.pack(fill="x", pady=6)
        self.seat_vars: list[tk.StringVar] = []
        for index in range(3):
            var = tk.StringVar(value=f"座位 {index + 1}：等待玩家…")
            ttk.Label(seats, textvariable=var, font=FONT_UI, relief="groove", padding=6,
                      anchor="center", justify="center").pack(
                side="left", fill="x", expand=True, padx=4
            )
            self.seat_vars.append(var)

        middle = ttk.Frame(self)
        middle.pack(fill="both", expand=True)
        self.table_canvas = TableCanvas(middle)
        self.table_canvas.pack(fill="x", pady=(0, 6))
        self.log_box = tk.Text(middle, font=FONT_LOG, height=12, state="disabled", wrap="word")
        self.log_box.pack(fill="both", expand=True, pady=6)
        self.log_box.tag_configure("chat", foreground="#1A1A1A")
        self.log_box.tag_configure("me", foreground="#2F6FED")
        self.log_box.tag_configure("system", foreground="#808080")
        self.log_box.tag_configure("alert", foreground="#C0392B")

        # 牌局内聊天：默认发到房间，也可以只发给某个人
        chat_row = ttk.Frame(self)
        chat_row.pack(fill="x", pady=(6, 0))
        self.chat_target = ttk.Combobox(chat_row, values=["（发到房间）"], width=14, state="readonly")
        self.chat_target.current(0)
        self.chat_target.pack(side="left")
        chat_entry = ttk.Entry(chat_row, textvariable=app.chat_var, font=FONT_UI)
        chat_entry.pack(side="left", fill="x", expand=True, padx=6)
        chat_entry.bind("<Return>", lambda _event: self.send_chat())
        ttk.Button(chat_row, text="发送", command=self.send_chat).pack(side="left")
        self.chat_entry = chat_entry

        hand_box = ttk.LabelFrame(self, text="我的手牌（点击选牌）", padding=6)
        hand_box.pack(fill="x")
        self.hand_canvas = HandCanvas(hand_box, on_toggle=self._toggle)
        self.hand_canvas.pack(fill="x")

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=8)
        self.buttons: dict[str, ttk.Button] = {}
        for key, text, command in (
            ("ready", "准备", app.do_ready),
            ("bid0", "不叫", lambda: app.do_bid(0)),
            ("bid1", "1 分", lambda: app.do_bid(1)),
            ("bid2", "2 分", lambda: app.do_bid(2)),
            ("bid3", "3 分", lambda: app.do_bid(3)),
            ("play", "出牌", app.do_play),
            ("pass", "过牌", app.do_pass),
            ("hint", "提示", app.do_hint),
            ("auto", "托管", app.toggle_auto),
        ):
            button = ttk.Button(actions, text=text, command=command)
            button.pack(side="left", padx=4)
            self.buttons[key] = button

    # ------------------------------------------------------------ 渲染 ----

    def on_show(self) -> None:
        self.title_var.set(f"牌桌 - {self.app.session.room}（{self.app.session.role}）")
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
        self.buttons["ready"].state(["!disabled"] if not in_match and session.role == "player" else ["disabled"])
        auto_button = self.buttons["auto"]
        auto_button.configure(text="取回控制" if session.auto_play else "托管")
        auto_button.state(
            ["!disabled"] if session.role == "player" and in_match else ["disabled"]
        )
        auto_names = state.get("auto_players") or []
        if session.nickname in auto_names:
            self.app.current_var.set(f"{self.app.current_var.get()}　（我托管中）")
        for key in ("bid0", "bid1", "bid2", "bid3"):
            enabled = state_name == "BIDDING" and my_turn and session.role == "player"
            self.buttons[key].state(["!disabled"] if enabled else ["disabled"])
        for key in ("play", "pass", "hint"):
            enabled = state_name == "PLAYING" and my_turn and session.role == "player"
            self.buttons[key].state(["!disabled"] if enabled else ["disabled"])

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
