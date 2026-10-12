"""角标牌画布：左上/右下标点数与花色，中间留白（参考欢乐斗地主的牌面结构）。

只依赖 tkinter.Canvas，不用图片资源，也不需要 Pillow：

* 牌底 = 圆角矩形（`create_polygon(..., smooth=True)` 近似圆角）；
* 左上角 = 点数在上、花色在下；右下角做镜像排版（花色在上、点数在下）。
  Tk 的 Canvas 文字不能旋转，用镜像排版来等价表现"倒过来的角标"；
* 大小王没有花色，用"大王/小王"两字竖排在左侧；
* 选中的牌整体上移，并换成强调色描边 —— 就是常说的"提牌"。
"""

from __future__ import annotations

import tkinter as tk

from game.cards import Card
from game.text import SUITS, is_red

FONT_NUM = ("Segoe UI", 12, "bold")
FONT_SUIT = ("Segoe UI Symbol", 11)
FONT_NUM_SMALL = ("Segoe UI", 9, "bold")
FONT_SUIT_SMALL = ("Segoe UI Symbol", 8)
FONT_JOKER = ("Microsoft YaHei UI", 10, "bold")

CARD_W, CARD_H = 60, 84
SMALL_W, SMALL_H = 44, 62
FAN_STEP = 26            # 手牌叠放步长（张数多时自动压缩）
LIFT = 10                # 选中时上移的像素

COLOR_BG = "#FFFFFF"
COLOR_BORDER = "#CFCFCF"
COLOR_SELECTED_BG = "#FFF8E1"
COLOR_ACCENT = "#2F6FED"
COLOR_RED = "#D64545"
COLOR_BLACK = "#1A1A1A"
COLOR_TABLE_BG = "#1F7A4D"


def round_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
               radius: float = 6, **kwargs) -> int:
    """在 Canvas 上画圆角矩形（用多边形 + 平滑近似，零依赖）。"""
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def _rank_text(card: int) -> str:
    return Card.STR_RANKS[Card.get_rank_int(card)]


def _suit_icon(card: int) -> str:
    return SUITS.get(Card.get_suit_int(card), "")


def _color(card: int) -> str:
    return COLOR_RED if is_red(card) else COLOR_BLACK


def is_joker(card: int) -> bool:
    return Card.get_rank_int(card) >= 13


def draw_card(canvas: tk.Canvas, x: float, y: float, card: int, *,
              selected: bool = False, small: bool = False, dim: bool = False,
              tag: str | None = None) -> str:
    """画一张角标牌，返回这张牌的 tag（用于点击命中）。"""
    w, h = (SMALL_W, SMALL_H) if small else (CARD_W, CARD_H)
    font_num = FONT_NUM_SMALL if small else FONT_NUM
    font_suit = FONT_SUIT_SMALL if small else FONT_SUIT
    dy = -LIFT if selected else 0
    tags = ("card", tag) if tag else ("card",)

    round_rect(
        canvas, x, y + dy, x + w, y + h + dy, radius=6,
        fill=COLOR_SELECTED_BG if selected else COLOR_BG,
        outline=COLOR_ACCENT if selected else COLOR_BORDER,
        width=2 if selected else 1,
        tags=tags,
    )

    if is_joker(card):
        label = "大王" if _rank_text(card) == "CJ" else "小王"
        canvas.create_text(x + 8, y + h / 2 + dy, text=label, anchor="w",
                           font=FONT_JOKER, fill=_color(card), tags=tags)
        canvas.create_text(x + w - 6, y + 6 + dy, text="王", anchor="ne",
                           font=font_num, fill=_color(card), tags=tags)
        canvas.create_text(x + w - 6, y + h - 6 + dy, text="王", anchor="se",
                           font=font_num, fill=_color(card), tags=tags)
        return tags[1] if tag else ""

    rank, suit = _rank_text(card), _suit_icon(card)
    # 左上角：点数在上、花色在下
    canvas.create_text(x + 6, y + 5 + dy, text=rank, anchor="nw",
                       font=font_num, fill=_color(card), tags=tags)
    canvas.create_text(x + 6, y + 23 + dy, text=suit, anchor="nw",
                       font=font_suit, fill=_color(card), tags=tags)
    # 右下角：镜像排版（花色在上、点数在下），等价于旋转 180°
    canvas.create_text(x + w - 6, y + h - 23 + dy, text=suit, anchor="se",
                       font=font_suit, fill=_color(card), tags=tags)
    canvas.create_text(x + w - 6, y + h - 5 + dy, text=rank, anchor="se",
                       font=font_num, fill=_color(card), tags=tags)

    if dim:  # 不可用（例如不在轮次）时压暗
        canvas.create_rectangle(x, y + dy, x + w, y + h + dy,
                                fill="#F0F0F0", outline="", stipple="gray25", tags=tags)
    return tags[1] if tag else ""


class HandCanvas(tk.Canvas):
    """手牌区：叠放显示，点击选牌。"""

    def __init__(self, parent, on_toggle=None, **kwargs) -> None:
        kwargs.setdefault("height", CARD_H + LIFT + 8)
        kwargs.setdefault("bg", COLOR_TABLE_BG)
        kwargs.setdefault("highlightthickness", 0)
        super().__init__(parent, **kwargs)
        self.on_toggle = on_toggle
        self.cards: list[int] = []
        self.selected: set[int] = set()
        self.bind("<Configure>", lambda _event: self.render())
        self.bind("<Button-1>", self._on_click)

    def set_cards(self, cards, selected=None) -> None:
        self.cards = list(cards)
        self.selected = set(selected or ())
        self.render()

    def render(self) -> None:
        self.delete("all")
        if not self.cards:
            return
        width = max(self.winfo_width(), 200)
        count = len(self.cards)
        step = FAN_STEP
        if count > 1:
            step = min(FAN_STEP, max(12, (width - CARD_W - 12) // (count - 1)))
        for index, card in enumerate(self.cards):
            x = 6 + index * step
            draw_card(self, x, LIFT + 4, card, selected=card in self.selected,
                      tag=f"card-{index}")

    def _on_click(self, event) -> None:
        index = self._hit(event.x)
        if index is None or self.on_toggle is None:
            return
        self.on_toggle(self.cards[index])

    def _hit(self, x_pos: float) -> int | None:
        if not self.cards:
            return None
        width = max(self.winfo_width(), 200)
        step = FAN_STEP if len(self.cards) == 1 else min(
            FAN_STEP, max(12, (width - CARD_W - 12) // (len(self.cards) - 1))
        )
        # 从右往左找：靠右的牌在上层
        for index in range(len(self.cards) - 1, -1, -1):
            x1 = 6 + index * step
            if x1 <= x_pos <= x1 + CARD_W:
                return index
        return None


class TableCanvas(tk.Canvas):
    """出牌区：中央显示本轮桌面牌，右上角显示三张底牌。"""

    def __init__(self, parent, **kwargs) -> None:
        kwargs.setdefault("height", SMALL_H + 12)
        kwargs.setdefault("bg", COLOR_TABLE_BG)
        kwargs.setdefault("highlightthickness", 0)
        super().__init__(parent, **kwargs)
        self.bind("<Configure>", lambda _event: self.render())
        self.table_cards: list[int] = []
        self.bottom_cards: list[int] = []
        self.owner: str | None = None

    def set_state(self, table_cards, bottom_cards, owner=None) -> None:
        self.table_cards = list(table_cards)
        self.bottom_cards = list(bottom_cards)
        self.owner = owner
        self.render()

    def render(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 200)
        if self.bottom_cards:
            label_x = width - 6 - len(self.bottom_cards) * (SMALL_W - 22) - 46
            self.create_text(label_x, SMALL_H / 2 + 6, text="底牌", anchor="e",
                             font=("Microsoft YaHei UI", 9), fill="#DFF3E6")
            for index, card in enumerate(self.bottom_cards):
                draw_card(self, label_x + 8 + index * (SMALL_W - 22), 6, card, small=True)
        if not self.table_cards:
            self.create_text(width / 2, SMALL_H / 2 + 6, text="（等待出牌）",
                             font=("Microsoft YaHei UI", 9), fill="#BFE6CE")
            return
        total = len(self.table_cards)
        step = min(SMALL_W - 20, max(14, (width - SMALL_W - 20) // max(total, 1)))
        start = max(6, (width - (total - 1) * step - SMALL_W) / 2)
        for index, card in enumerate(self.table_cards):
            draw_card(self, start + index * step, 6, card, small=True)
