"""CustomTkinter 皮肤 —— 本次界面美化新增的**全部**代码都在这里，可整块回滚。

回滚方式（详见 docs/界面美化-可回滚说明.md）：

1. 启动时加 `--ui classic`（或设环境变量 `DDZ_UI=classic`）→ 立刻回到经典界面；
2. 直接删除本文件 → `client/app.py` 里的开关会检测不到它，自动退回经典界面；
3. 卸载依赖：`pip uninstall customtkinter`（同时删掉 requirements.txt 里那一行）。

设计约束：

* 只做"皮肤"，不碰协议、会话层与业务逻辑；
* 牌桌沿用 `client/app.py::TableFrame` 的布局与渲染，仅覆写控件创建钩子换外观；
* 牌面仍是自绘的 Canvas 角标牌（client/cards_view.py），与本文件无关。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

try:  # 没装依赖时整个模块优雅降级
    import customtkinter as ctk

    HAS_CTK = True
except Exception:  # pragma: no cover - 未安装依赖的分支
    ctk = None
    HAS_CTK = False

# ------------------------------------------------------------------ 配色 ----

APP_BG = "#181818"
CARD_BG = "#242424"
CARD_BG_2 = "#2E2E2E"
FELT = "#1F7A4D"
ACCENT = "#2CC985"
ACCENT_HOVER = "#249E6B"
DANGER = "#E06C75"
TEXT = "#EAEAEA"
TEXT_MUTED = "#9A9A9A"
MY_BUBBLE = "#2F6FED"
OTHER_BUBBLE = "#3A3A3A"

FONT_UI = ("Microsoft YaHei UI", 12)
FONT_TITLE = ("Microsoft YaHei UI", 18, "bold")
FONT_SUB = ("Microsoft YaHei UI", 11)
FONT_LOG = ("Microsoft YaHei UI", 11)
FONT_MONO_BUBBLE = ("Microsoft YaHei UI", 12)


def available() -> bool:
    """依赖是否可用（app.py 用它决定走哪套界面）。"""
    return HAS_CTK


def root_class():
    """返回根窗口类：装了依赖就用 CTk（外观模式/缩放由它统管）。"""
    return ctk.CTk if HAS_CTK else tk.Tk


def apply_theme(appearance: str = "dark", theme: str = "green") -> None:
    """全局外观：深色 + 绿色主题（与牌桌绿毡呼应）。"""
    if not HAS_CTK:
        return
    try:
        ctk.set_appearance_mode(appearance)
        ctk.set_default_color_theme(theme)
    except Exception:  # pragma: no cover - 主题名变化时不影响启动
        pass


def style_ttk(style) -> None:
    """把牌桌里保留的 ttk 控件调成同一套深色，避免出现浅灰色块。

    关键点：Windows 上 ttk 默认用 `vista` 主题，它**会忽略 background 之类的配置**，
    所以必须先切到 `clam`，深色才会真正生效；同时要把边框的亮/暗色一起配掉，
    否则 clam 会画出浅色的 3D 边框。
    """
    try:
        style.theme_use("clam")
    except Exception:  # pragma: no cover - 主题名异常时保持原样
        pass

    flat = {"borderwidth": 0, "relief": "flat"}
    edge = {"bordercolor": CARD_BG_2, "lightcolor": CARD_BG_2, "darkcolor": CARD_BG_2}

    style.configure("TFrame", background=APP_BG, **flat)
    style.configure("Ctk.TFrame", background=APP_BG, **flat)
    style.configure("CtkCard.TFrame", background=CARD_BG, **flat)
    style.configure("TLabel", background=APP_BG, foreground=TEXT, **flat)
    style.configure("Ctk.TLabel", background=APP_BG, foreground=TEXT_MUTED, **flat)
    style.configure("CtkCard.TLabel", background=CARD_BG, foreground=TEXT, **flat)
    style.configure("TLabelframe", background=APP_BG, **edge)
    style.configure("TLabelframe.Label", background=APP_BG, foreground=TEXT_MUTED)
    style.configure("Ctk.TLabelframe", background=APP_BG, **edge)
    style.configure("Ctk.TLabelframe.Label", background=APP_BG, foreground=TEXT_MUTED)
    # 下面几类当前皮肤用不到，一并配好：以后混用不会再冒出浅色块
    style.configure("TButton", background=CARD_BG_2, foreground=TEXT, focuscolor=ACCENT, **edge)
    style.map("TButton", background=[("active", ACCENT_HOVER), ("disabled", CARD_BG)],
              foreground=[("disabled", TEXT_MUTED)])
    style.configure("TEntry", fieldbackground=CARD_BG_2, foreground=TEXT, insertcolor=TEXT, **edge)
    style.configure("TCombobox", fieldbackground=CARD_BG_2, background=CARD_BG_2,
                    foreground=TEXT, arrowcolor=TEXT, **edge)
    style.map("TCombobox", fieldbackground=[("readonly", CARD_BG_2)],
              foreground=[("readonly", TEXT)])
    style.configure("Vertical.TScrollbar", background=CARD_BG_2, troughcolor=APP_BG,
                    bordercolor=APP_BG, arrowcolor=TEXT)


# ------------------------------------------------------------ 登录页 ----

class ConnectFrame(ctk.CTkFrame):
    def __init__(self, parent, app) -> None:
        super().__init__(parent, fg_color=APP_BG, corner_radius=0)
        self.app = app

        card = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=18)
        card.place(relx=0.5, rely=0.45, anchor="center")

        ctk.CTkLabel(card, text="斗地主聊天室", font=ctk.CTkFont(*FONT_TITLE)).grid(
            row=0, column=0, columnspan=4, pady=(24, 2), padx=28
        )
        ctk.CTkLabel(card, text="大厅聊天 + 三人斗地主（服务端统一裁决）",
                     font=ctk.CTkFont(*FONT_SUB), text_color=TEXT_MUTED).grid(
            row=1, column=0, columnspan=4, pady=(0, 16), padx=28
        )

        ctk.CTkLabel(card, text="账号", font=ctk.CTkFont(*FONT_UI)).grid(row=2, column=0, sticky="e", pady=6)
        account_entry = ctk.CTkEntry(card, textvariable=app.account_var, width=170,
                                     fg_color=CARD_BG_2, border_width=0)
        account_entry.grid(row=2, column=1, columnspan=2, sticky="w")
        ctk.CTkLabel(card, text="密码", font=ctk.CTkFont(*FONT_UI)).grid(row=3, column=0, sticky="e", pady=6)
        pwd_entry = ctk.CTkEntry(card, textvariable=app.password_var, width=170, show="*",
                                 fg_color=CARD_BG_2, border_width=0)
        pwd_entry.grid(row=3, column=1, columnspan=2, sticky="w")
        account_entry.bind("<Return>", lambda _event: pwd_entry.focus_set())
        pwd_entry.bind("<Return>", lambda _event: app.do_login())
        account_entry.focus_set()

        ctk.CTkButton(card, text="登录", height=38, corner_radius=10,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0B0B0B",
                      font=ctk.CTkFont("Microsoft YaHei UI", 13, "bold"),
                      command=app.do_login).grid(row=4, column=0, columnspan=3,
                                                 pady=(18, 8), padx=28, sticky="ew")
        ctk.CTkButton(card, text="创建账号", height=32, corner_radius=10,
                      fg_color=CARD_BG_2, hover_color="#3A3A3A", text_color=TEXT,
                      command=app.do_register).grid(row=5, column=0, columnspan=3,
                                                    pady=(0, 10), padx=28, sticky="ew")
        self.hint = ctk.CTkLabel(card, text="预置账号 wanjia1 ~ wanjia5，初始密码 123456",
                                 font=ctk.CTkFont("Microsoft YaHei UI", 10), text_color=TEXT_MUTED)
        self.hint.grid(row=6, column=0, columnspan=3, pady=(0, 20), padx=28)

    def on_show(self) -> None:
        return

    def on_event(self, kind: str, data: dict) -> None:
        if kind == "error":
            self.hint.configure(text=f"{data.get('message')}（{data.get('code')}）", text_color=DANGER)
        elif kind == "logged_out":
            self.hint.configure(text="已登出，可换账号登录", text_color=ACCENT)
            self.app.account_var.set("")
            self.app.password_var.set("")


# -------------------------------------------------------------- 大厅 ----

class LobbyFrame(ctk.CTkFrame):
    def __init__(self, parent, app) -> None:
        super().__init__(parent, fg_color=APP_BG, corner_radius=0)
        self.app = app
        self._room_names: list[str] = []

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(12, 6))
        ctk.CTkLabel(head, text="大厅", font=ctk.CTkFont(*FONT_TITLE)).pack(side="left")
        self.account_label = ctk.CTkLabel(head, text="当前账号：-", font=ctk.CTkFont(*FONT_UI),
                                          text_color=TEXT_MUTED)
        self.account_label.pack(side="left", padx=16)
        ctk.CTkButton(head, text="登出账号", width=100, fg_color=CARD_BG_2, hover_color="#3A3A3A",
                      command=self.app.do_logout).pack(side="right")
        ctk.CTkButton(head, text="邀请建群", width=100, fg_color=ACCENT, text_color="#0B0B0B",
                      hover_color=ACCENT_HOVER,
                      command=self.app.do_create_group).pack(side="right", padx=(0, 8))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        left = ctk.CTkFrame(body, fg_color=CARD_BG, corner_radius=14)
        left.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(left, text="房间（点一下直接进入）", font=ctk.CTkFont(*FONT_SUB),
                     text_color=TEXT_MUTED).pack(anchor="w", padx=12, pady=(10, 4))
        self.rooms_box = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self.rooms_box.configure(scrollbar_button_color=CARD_BG_2,
                                 scrollbar_button_hover_color=ACCENT_HOVER)
        self.rooms_box.pack(fill="both", expand=True, padx=6, pady=(0, 8))
        ctk.CTkButton(left, text="刷新房间", width=110, fg_color=CARD_BG_2, hover_color="#3A3A3A",
                      command=self._refresh).pack(anchor="w", padx=12, pady=(0, 12))

        right = ctk.CTkFrame(body, fg_color=CARD_BG, corner_radius=14)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))
        ctk.CTkLabel(right, text="在线用户（点一下即可私聊）", font=ctk.CTkFont(*FONT_SUB),
                     text_color=TEXT_MUTED).pack(anchor="w", padx=12, pady=(10, 4))
        self.users_box = ctk.CTkScrollableFrame(right, fg_color="transparent")
        self.users_box.configure(scrollbar_button_color=CARD_BG_2,
                                 scrollbar_button_hover_color=ACCENT_HOVER)
        self.users_box.pack(fill="both", expand=True, padx=6, pady=(0, 12))
        ctk.CTkButton(right, text="刷新在线玩家", width=110, fg_color=CARD_BG_2,
                      hover_color="#3A3A3A", command=self.app.do_refresh_users).pack(
            anchor="w", padx=12, pady=(0, 12))

        conv = ctk.CTkFrame(body, fg_color=CARD_BG, corner_radius=14, width=200)
        conv.pack(side="left", fill="both", padx=(12, 0))
        ctk.CTkLabel(conv, text="私聊会话（点一下打开）", font=ctk.CTkFont("Microsoft YaHei UI", 10),
                     text_color=TEXT_MUTED).pack(anchor="w", padx=12, pady=(10, 4))
        self.conv_box = ctk.CTkScrollableFrame(conv, fg_color="transparent", width=180)
        self.conv_box.configure(scrollbar_button_color=CARD_BG_2,
                                scrollbar_button_hover_color=ACCENT_HOVER)
        self.conv_box.pack(fill="both", expand=True, padx=6, pady=(0, 12))

    # ---------- 事件 ----------

    def on_show(self) -> None:
        self._refresh()
        self._render_users()
        self._render_conversations()
        self.account_label.configure(text=f"当前账号：{self.app.session.account or '-'}")
        self.app.call(self.app.session.refresh_users(), "刷新在线玩家失败")

    def _render_conversations(self) -> None:
        for child in self.conv_box.winfo_children():
            child.destroy()
        for item in self.app.session.conversations:
            preview = item.get("last_text") or ""
            preview = preview if len(preview) <= 12 else preview[:12] + "…"
            ctk.CTkButton(self.conv_box, text=f"{item['peer']}：{preview}", anchor="w",
                          height=30, corner_radius=8, fg_color=CARD_BG_2,
                          hover_color=ACCENT_HOVER, text_color=TEXT,
                          command=lambda p=item["peer"]: self.app.do_open_private(p)).pack(
                fill="x", pady=2, padx=4)

    def _refresh(self) -> None:
        self.app.call(self.app.session.refresh_rooms(), "刷新房间失败")

    def _render_rooms(self, rooms: list[dict]) -> None:
        for child in self.rooms_box.winfo_children():
            child.destroy()
        kind_label = {"lobby": "大厅", "chat": "聊天", "doudizhu": "牌桌"}
        self._room_names = []
        for room in rooms:
            name = room.get("name", "")
            self._room_names.append(name)
            text = f"{name}　{kind_label.get(room.get('kind'), room.get('kind'))}・{room.get('count')} 人"
            ctk.CTkButton(self.rooms_box, text=text, anchor="w", height=34, corner_radius=8,
                          fg_color=CARD_BG_2, hover_color=ACCENT_HOVER,
                          command=lambda r=name: self.app.do_join(r)).pack(fill="x", pady=3, padx=4)

    def _render_users(self) -> None:
        for child in self.users_box.winfo_children():
            child.destroy()
        names = (self.app.session.online_users or self.app.session.room_users
                 or [self.app.session.nickname or "-"])
        for name in names:
            if name == self.app.session.nickname:
                ctk.CTkLabel(self.users_box, text=f"　{name}（我）", anchor="w",
                             text_color=TEXT_MUTED).pack(fill="x", pady=2, padx=4)
                continue
            ctk.CTkButton(self.users_box, text=f"　{name}", anchor="w", height=28, corner_radius=8,
                          fg_color=CARD_BG_2, hover_color=ACCENT_HOVER, text_color=TEXT,
                          command=lambda n=name: self.app.do_whisper(n)).pack(fill="x", pady=2, padx=4)

    def on_event(self, kind: str, data: dict) -> None:
        if kind == "room_list":
            self._render_rooms(data.get("rooms", []))
            self.account_label.configure(text=f"当前账号：{self.app.session.account or '-'}")
        elif kind == "room_users":
            self._render_users()
        elif kind == "user_list":
            self._render_users()
        elif kind in ("conversations", "private_msg", "private_opened"):
            self._render_conversations()


# ------------------------------------------------------------ 聊天室 ----

class PrivateChatFrame(ctk.CTkFrame):
    """私聊界面（CustomTkinter 皮肤）：气泡显示历史，底部输入。"""

    def __init__(self, parent, app) -> None:
        super().__init__(parent, fg_color=APP_BG, corner_radius=0)
        self.app = app

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(12, 6))
        self.title_var = tk.StringVar(value="私聊")
        ctk.CTkLabel(header, textvariable=self.title_var, font=ctk.CTkFont(*FONT_TITLE)).pack(side="left")
        ctk.CTkButton(header, text="返回大厅", width=100, fg_color=CARD_BG_2, hover_color="#3A3A3A",
                      command=lambda: app.show("lobby")).pack(side="right")
        ctk.CTkButton(header, text="刷新记录", width=100, fg_color=CARD_BG_2, hover_color="#3A3A3A",
                      command=app.do_refresh_private).pack(side="right", padx=(0, 8))

        self.messages = ctk.CTkScrollableFrame(self, fg_color=CARD_BG, corner_radius=14)
        self.messages.configure(scrollbar_button_color=CARD_BG_2,
                                scrollbar_button_hover_color=ACCENT_HOVER)
        self.messages.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=16, pady=(0, 14))
        entry = ctk.CTkEntry(bottom, textvariable=app.chat_var, height=34, corner_radius=10,
                             fg_color=CARD_BG_2, border_width=0)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _event: app.do_send_private())
        self.chat_entry = entry
        ctk.CTkButton(bottom, text="发送", width=80, height=34, corner_radius=10,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0B0B0B",
                      command=app.do_send_private).pack(side="left", padx=(8, 0))

    def on_show(self) -> None:
        self.render()

    def render(self) -> None:
        session = self.app.session
        peer = session.current_peer or "-"
        self.title_var.set(f"与 {peer} 的私聊")
        for child in self.messages.winfo_children():
            child.destroy()
        for message in session.private_history.get(peer, []):
            mine = message.get("from") == session.nickname
            who = "我" if mine else message.get("from")
            ctk.CTkLabel(
                self.messages, text=f"{who}: {message.get('text')}",
                font=ctk.CTkFont(*FONT_MONO_BUBBLE), justify="left", wraplength=520,
                fg_color=MY_BUBBLE if mine else OTHER_BUBBLE, corner_radius=10,
                text_color=TEXT, padx=10, pady=6,
            ).pack(anchor="e" if mine else "w", padx=10, pady=4)
        try:
            self.messages._parent_canvas.yview_moveto(1.0)
        except Exception:  # pragma: no cover
            pass

    def on_event(self, kind: str, data: dict) -> None:
        if kind in ("private_opened", "history", "private_msg"):
            if (data.get("peer") or self.app.session.current_peer) == self.app.session.current_peer:
                self.render()


class RoomFrame(ctk.CTkFrame):
    def __init__(self, parent, app) -> None:
        super().__init__(parent, fg_color=APP_BG, corner_radius=0)
        self.app = app
        self._bubbles = 0

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(12, 6))
        self.title_var = tk.StringVar(value="聊天房")
        ctk.CTkLabel(header, textvariable=self.title_var, font=ctk.CTkFont(*FONT_TITLE)).pack(side="left")
        self.group_var = tk.StringVar(value="")
        ctk.CTkLabel(header, textvariable=self.group_var, font=ctk.CTkFont(*FONT_UI),
                     text_color=TEXT_MUTED).pack(side="left", padx=12)
        self.btn_dismiss = ctk.CTkButton(header, text="解散群聊", width=100, fg_color="#7A2E2E",
                                         hover_color=DANGER, command=app.do_delete_group)
        ctk.CTkButton(header, text="离开房间", width=100, fg_color=CARD_BG_2, hover_color="#3A3A3A",
                      command=app.do_leave).pack(side="right")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        self.messages = ctk.CTkScrollableFrame(body, fg_color=CARD_BG, corner_radius=14)
        self.messages.pack(side="left", fill="both", expand=True)
        self.messages.configure(scrollbar_button_color=CARD_BG_2,
                                scrollbar_button_hover_color=ACCENT_HOVER)

        side = ctk.CTkFrame(body, fg_color=CARD_BG, corner_radius=14, width=180)
        side.pack(side="left", fill="y", padx=(12, 0))
        ctk.CTkLabel(side, text="房间里的人（选中可私信）", font=ctk.CTkFont("Microsoft YaHei UI", 10),
                     text_color=TEXT_MUTED).pack(anchor="w", padx=10, pady=(10, 4))
        self.users_box = ctk.CTkScrollableFrame(side, fg_color="transparent", width=150)
        self.users_box.configure(scrollbar_button_color=CARD_BG_2,
                                 scrollbar_button_hover_color=ACCENT_HOVER)
        self.users_box.pack(fill="y", expand=True, padx=6, pady=(0, 8))
        self._selected_user: str | None = None
        self.whisper_hint = ctk.CTkLabel(side, text="未选中用户", font=ctk.CTkFont("Microsoft YaHei UI", 10),
                                         text_color=TEXT_MUTED)
        self.whisper_hint.pack(anchor="w", padx=10, pady=(0, 10))

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=16, pady=(0, 14))
        entry = ctk.CTkEntry(bottom, textvariable=app.chat_var, height=34, corner_radius=10,
                             fg_color=CARD_BG_2, border_width=0)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _event: self.send())
        self.chat_entry = entry
        ctk.CTkButton(bottom, text="发送", width=80, height=34, corner_radius=10,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0B0B0B",
                      command=self.send).pack(side="left", padx=(8, 0))

    # ---------- 交互 ----------

    def send(self) -> None:
        text = self.app.chat_var.get().strip()
        if not text:
            return
        self.app.chat_var.set("")
        if self._selected_user:
            self.app.call(self.app.session.whisper(self._selected_user, text), "私信失败")
        else:
            self.app.call(self.app.session.say(text), "发送失败")

    def append_log(self, text: str) -> None:
        self.append_message(text)

    def append_message(self, text: str, kind: str = "room_msg") -> None:
        if not text:
            return
        mine = text.startswith("我:") or text.startswith("（私信）我")
        color = MY_BUBBLE if mine else OTHER_BUBBLE
        if kind in ("user_joined", "user_left", "user_reconnecting", "timeout"):
            color = "transparent"
        bubble = ctk.CTkLabel(
            self.messages, text=text, font=ctk.CTkFont(*FONT_MONO_BUBBLE),
            fg_color=color, corner_radius=10, justify="left", wraplength=460,
            text_color=TEXT if color != "transparent" else TEXT_MUTED,
            padx=10, pady=6,
        )
        bubble.pack(anchor="e" if mine else "w", padx=10, pady=4)
        self._bubbles += 1
        try:  # 滚到底（CTkScrollableFrame 的内部画布）
            self.messages._parent_canvas.yview_moveto(1.0)
        except Exception:  # pragma: no cover
            pass

    def refresh_users(self) -> None:
        for child in self.users_box.winfo_children():
            child.destroy()
        self._selected_user = None
        self.whisper_hint.configure(text="未选中用户")
        for name in self.app.session.room_users:
            ctk.CTkButton(self.users_box, text=name, anchor="w", height=28, corner_radius=8,
                          fg_color=CARD_BG_2, hover_color=ACCENT_HOVER,
                          command=lambda n=name: self._select(n)).pack(fill="x", pady=2, padx=4)

    def _select(self, name: str) -> None:
        if name == self.app.session.nickname:
            self._selected_user = None
            self.whisper_hint.configure(text="未选中用户（点别人可私信）")
        else:
            self._selected_user = name
            self.whisper_hint.configure(text=f"私信对象：{name}")

    # ---------- 事件 ----------

    def on_show(self) -> None:
        self.title_var.set(f"聊天房 - {self.app.session.room}")
        self._sync_group_controls()
        self.refresh_users()

    def _sync_group_controls(self) -> None:
        """群聊：显示群主，只有群主能看到"解散群聊"。"""
        session = self.app.session
        room = next((r for r in session.rooms if r.get("name") == session.room), None)
        if room and room.get("kind") == "group":
            owner = room.get("owner") or "-"
            self.group_var.set(f"群聊（群主：{owner}）")
            if owner == session.nickname:
                self.btn_dismiss.pack(side="right", padx=(0, 8))
            else:
                self.btn_dismiss.pack_forget()
        else:
            self.group_var.set("")
            self.btn_dismiss.pack_forget()

    def on_event(self, kind: str, data: dict) -> None:
        if kind == "room_users" and data.get("room") == self.app.session.room:
            self.refresh_users()
        if kind in ("room_list", "joined", "group_created"):
            self._sync_group_controls()
        if data.get("_line") and kind in ("room_msg", "private_msg", "user_joined",
                                          "user_left", "user_reconnecting", "error"):
            self.append_message(data["_line"], kind)


# ------------------------------------------------------------ 牌桌皮肤 ----

def make_table_frame():
    """延迟导入，避免 app.py 与本文件循环依赖。"""
    from . import app as app_module

    class TableFrameCTK(app_module.TableFrame):
        """牌桌：沿用原布局与渲染逻辑，只把控件换成 CustomTkinter 外观。"""

        def _build_header(self) -> None:
            header = ctk.CTkFrame(self, fg_color="transparent")
            header.pack(fill="x")
            self.header_frame = header
            self.title_var = tk.StringVar(value="牌桌")
            ctk.CTkLabel(header, textvariable=self.title_var,
                         font=ctk.CTkFont("Microsoft YaHei UI", 16, "bold")).pack(side="left")
            ctk.CTkLabel(header, textvariable=self.app.multiplier_var,
                         font=ctk.CTkFont(*FONT_UI), text_color=ACCENT).pack(side="left", padx=14)
            ctk.CTkLabel(header, textvariable=self.app.current_var,
                         font=ctk.CTkFont(*FONT_UI), text_color=TEXT_MUTED).pack(side="left")
            ctk.CTkButton(header, text="离开牌桌", width=100, fg_color=CARD_BG_2,
                          hover_color="#3A3A3A", command=self.app.do_leave).pack(side="right")

        def _build_seats(self) -> None:
            seats = ctk.CTkFrame(self, fg_color="transparent")
            seats.pack(fill="x", pady=8)
            self.seats_frame = seats
            self.seat_vars: list[tk.StringVar] = []
            self.seat_labels: list[ctk.CTkLabel] = []
            for index in range(3):
                var = tk.StringVar(value=f"座位 {index + 1}：等待玩家…")
                label = ctk.CTkLabel(seats, textvariable=var, font=ctk.CTkFont(*FONT_UI),
                                     fg_color=CARD_BG, corner_radius=12, padx=10, pady=8)
                label.pack(side="left", fill="x", expand=True, padx=5)
                self.seat_vars.append(var)
                self.seat_labels.append(label)

        def _build_actions(self) -> None:
            actions = ctk.CTkFrame(self, fg_color="transparent")
            actions.pack(fill="x", pady=10)
            self.actions_frame = actions
            self.buttons: dict[str, ctk.CTkButton] = {}
            spec = (
                ("ready", "准备", self.app.do_ready, ACCENT, "#0B0B0B"),
                ("bid0", "不叫", lambda: self.app.do_bid(0), CARD_BG_2, TEXT),
                ("bid1", "1 分", lambda: self.app.do_bid(1), CARD_BG_2, TEXT),
                ("bid2", "2 分", lambda: self.app.do_bid(2), CARD_BG_2, TEXT),
                ("bid3", "3 分", lambda: self.app.do_bid(3), CARD_BG_2, TEXT),
                ("play", "出牌", self.app.do_play, ACCENT, "#0B0B0B"),
                ("pass", "过牌", self.app.do_pass, CARD_BG_2, TEXT),
                ("hint", "提示", self.app.do_hint, CARD_BG_2, TEXT),
                ("auto", "托管", self.app.toggle_auto, CARD_BG_2, TEXT),
            )
            for key, text, command, color, text_color in spec:
                button = ctk.CTkButton(actions, text=text, width=76, height=34, corner_radius=10,
                                       fg_color=color, hover_color=ACCENT_HOVER,
                                       text_color=text_color, command=command)
                button.pack(side="left", padx=4)
                self.buttons[key] = button

        def _build_chat(self) -> None:
            chat_row = ctk.CTkFrame(self, fg_color="transparent")
            chat_row.pack(fill="x", pady=(8, 0))
            self.chat_row = chat_row
            self.chat_target = ctk.CTkComboBox(chat_row, values=["（发到房间）"], width=140,
                                               fg_color=CARD_BG_2, border_width=0,
                                               button_color=CARD_BG_2, button_hover_color=ACCENT_HOVER,
                                               dropdown_fg_color=CARD_BG_2,
                                               dropdown_hover_color=ACCENT_HOVER,
                                               dropdown_text_color=TEXT,
                                               text_color=TEXT,
                                               command=lambda _v: None)
            self.chat_target.set("（发到房间）")
            self.chat_target.pack(side="left")
            entry = ctk.CTkEntry(chat_row, textvariable=self.app.chat_var, height=32,
                                 corner_radius=10, fg_color=CARD_BG_2, border_width=0)
            entry.pack(side="left", fill="x", expand=True, padx=8)
            entry.bind("<Return>", lambda _event: self.send_chat())
            ctk.CTkButton(chat_row, text="发送", width=70, height=32, corner_radius=10,
                          fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0B0B0B",
                          command=self.send_chat).pack(side="left")
            self.chat_entry = entry

        def _configure_log_tags(self) -> None:
            self.log_box.configure(bg=CARD_BG, fg=TEXT, insertbackground=TEXT,
                                   relief="flat", borderwidth=0, highlightthickness=0)
            self.log_box.tag_configure("chat", foreground=TEXT)
            self.log_box.tag_configure("me", foreground="#7FB2FF")
            self.log_box.tag_configure("system", foreground=TEXT_MUTED)
            self.log_box.tag_configure("alert", foreground=DANGER)

        def _apply_theme(self) -> None:
            style = ttk.Style()
            style_ttk(style)
            self.configure(style="Ctk.TFrame")
            middle = getattr(self, "middle_frame", None)
            if middle is not None:
                middle.configure(style="CtkCard.TFrame")
            hand_box = getattr(self, "hand_box", None)
            if hand_box is not None:
                hand_box.configure(style="Ctk.TLabelframe")
            for canvas in (getattr(self, "table_canvas", None), getattr(self, "hand_canvas", None)):
                if canvas is not None:
                    canvas.configure(bg=FELT, highlightthickness=0)

        def _set_button_enabled(self, key: str, enabled: bool) -> None:
            self.buttons[key].configure(state="normal" if enabled else "disabled")

        def _set_button_text(self, key: str, text: str) -> None:
            self.buttons[key].configure(text=text)

        def refresh_chat_targets(self) -> None:
            names = list(self.app.session.table.get("players") or [])
            others = [name for name in names if name != self.app.session.nickname]
            values = ["（发到房间）"] + others
            self.chat_target.configure(values=values)
            if self.chat_target.get() not in values:
                self.chat_target.set("（发到房间）")

    return TableFrameCTK
