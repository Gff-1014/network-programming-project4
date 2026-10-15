"""两套皮肤共用的小对话框：私聊输入、邀请建群多选。

用纯 tkinter 实现（Toplevel + Listbox），因此在经典皮肤和 CustomTkinter 皮肤下都能用，
不需要为两种皮肤各写一遍。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import ui_ctk  # 只为取配色；未装 customtkinter 时该模块也能导入


def _style_of(use_ctk: bool) -> dict:
    if use_ctk and ui_ctk.available():
        return {"bg": ui_ctk.CARD_BG, "fg": ui_ctk.TEXT,
                "accent": ui_ctk.ACCENT, "accent_fg": "#0B0B0B"}
    return {"bg": None, "fg": None, "accent": None, "accent_fg": None}


def ask_text(parent, title: str, prompt: str, initial: str = "",
             use_ctk: bool = True) -> str | None:
    """弹出一个单行输入框，返回输入内容；取消返回 None。"""
    palette = _style_of(use_ctk)
    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent)
    win.resizable(False, False)
    if palette["bg"]:
        win.configure(bg=palette["bg"])

    body = tk.Frame(win, padx=14, pady=12,
                    bg=palette["bg"] or win.cget("bg"))
    body.pack(fill="both", expand=True)
    label_kw = {"bg": palette["bg"], "fg": palette["fg"]} if palette["bg"] else {}
    tk.Label(body, text=prompt, **label_kw).pack(anchor="w")
    var = tk.StringVar(value=initial)
    entry = tk.Entry(body, textvariable=var, width=36)
    entry.pack(fill="x", pady=(6, 10))
    entry.focus_set()

    result: dict[str, str | None] = {"value": None}

    def ok() -> None:
        result["value"] = var.get().strip() or None
        win.destroy()

    def cancel() -> None:
        win.destroy()

    row = tk.Frame(body, bg=palette["bg"] or win.cget("bg"))
    row.pack(anchor="e")
    tk.Button(row, text="取消", width=8, command=cancel).pack(side="right", padx=(6, 0))
    tk.Button(row, text="发送", width=8, command=ok).pack(side="right")
    entry.bind("<Return>", lambda _event: ok())
    win.bind("<Escape>", lambda _event: cancel())
    win.grab_set()
    parent.wait_window(win)
    return result["value"]


def choose_members(parent, candidates: list[str], title: str = "邀请用户创建群聊",
                   use_ctk: bool = True) -> list[str] | None:
    """多选对话框：从在线用户里挑人，返回账号列表；取消返回 None。"""
    palette = _style_of(use_ctk)
    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent)
    if palette["bg"]:
        win.configure(bg=palette["bg"])

    body = tk.Frame(win, padx=14, pady=12, bg=palette["bg"] or win.cget("bg"))
    body.pack(fill="both", expand=True)
    label_kw = {"bg": palette["bg"], "fg": palette["fg"]} if palette["bg"] else {}
    tk.Label(body, text="选择要邀请的在线用户（可多选，Ctrl/Shift 连选）", **label_kw).pack(anchor="w")

    listbox = tk.Listbox(body, selectmode="extended", height=min(12, max(4, len(candidates))),
                         width=32, exportselection=False)
    listbox.pack(fill="both", expand=True, pady=(6, 10))
    for name in candidates:
        listbox.insert(tk.END, name)

    result: dict[str, list[str] | None] = {"value": None}

    def ok() -> None:
        result["value"] = [candidates[i] for i in listbox.curselection()]
        win.destroy()

    row = tk.Frame(body, bg=palette["bg"] or win.cget("bg"))
    row.pack(anchor="e")
    tk.Button(row, text="取消", width=8, command=win.destroy).pack(side="right", padx=(6, 0))
    tk.Button(row, text="创建群聊", width=10, command=ok).pack(side="right")
    win.bind("<Escape>", lambda _event: win.destroy())
    win.grab_set()
    parent.wait_window(win)
    return result["value"]
