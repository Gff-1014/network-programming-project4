"""牌面绘制测试：角标牌的形状、角标数量与点击命中（无显示环境自动跳过）。"""

from __future__ import annotations

import pytest

from game.text import parse_card


@pytest.fixture()
def tk_root():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover
        pytest.skip(f"没有可用的显示环境: {exc}")
    root.geometry("900x300")
    yield root
    root.destroy()


def test_hand_canvas_draws_corner_indices(tk_root):
    from client.cards_view import HandCanvas

    canvas = HandCanvas(tk_root, width=700, height=120)
    canvas.pack()
    codes = ("3s", "10h", "Jd", "2c", "5s")
    cards = [parse_card(code) for code in codes]
    canvas.set_cards(cards, selected={cards[0]})
    tk_root.update_idletasks()

    # 每张普通牌 = 1 个圆角底 + 4 处角标文字
    assert len(canvas.find_all()) == len(cards) * 5
    assert canvas.selected == {cards[0]}


def test_joker_has_rank_mark_and_no_suit(tk_root):
    from client.cards_view import HandCanvas

    canvas = HandCanvas(tk_root, width=400, height=120)
    big = parse_card("CJ")
    canvas.set_cards([big])
    tk_root.update_idletasks()
    # 王：1 个底 + "大王" 文字 + 两处"王"角标
    assert len(canvas.find_all()) == 4


def test_click_hit_maps_to_card_index(tk_root):
    from client.cards_view import HandCanvas

    pressed: list[int] = []
    canvas = HandCanvas(tk_root, width=700, height=120, on_toggle=pressed.append)
    canvas.pack()
    cards = [parse_card(code) for code in ("3s", "4h", "5d")]
    canvas.set_cards(cards)
    tk_root.update_idletasks()

    assert canvas._hit(8) == 0, "最左边的横坐标应命中第一张牌"
    # 叠放时重叠区域应命中最上层（也就是靠右、后画的那张）
    assert canvas._hit(70) == 2, "重叠处应命中盖在上面的牌"

    event = type("E", (), {"x": 8, "y": 40})()
    canvas._on_click(event)
    assert pressed == [cards[0]]


def test_table_canvas_draws_play_and_bottom(tk_root):
    from client.cards_view import TableCanvas

    canvas = TableCanvas(tk_root, width=500, height=80)
    canvas.pack()
    canvas.set_state([parse_card("3s"), parse_card("3h")], [parse_card("BJ")])
    tk_root.update_idletasks()
    assert canvas.find_all(), "出牌区应画出桌面牌与底牌"


def test_round_rect_helper_returns_polygon(tk_root):
    import tkinter as tk

    from client.cards_view import round_rect

    canvas = tk.Canvas(tk_root)
    item = round_rect(canvas, 0, 0, 60, 84, radius=6, fill="white")
    assert canvas.type(item) == "polygon"
