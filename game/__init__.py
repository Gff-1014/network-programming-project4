"""斗地主游戏侧：牌表示、牌型规则、对局状态机与托管 AI。

模块分工：

    cards.py   牌的表示（移植自 onestraw/doudizhu，MIT）
    rules.py   37 种牌型的枚举、识别与比较（同上，原样移植）
    api.py     门面：发牌、牌型判定、可压牌枚举等纯函数
    engine.py  对局状态机（本项目自研）
    ai.py      托管与机器人决策（本项目自研）

网络层只使用 api.py 与 engine.py 暴露的接口，不直接依赖移植代码。
"""

from __future__ import annotations

from .api import (
    ALL_CARDS,
    Card,
    Doudizhu,
    cards_greater,
    check_card_type,
    hand_str,
    list_greater_cards,
    new_game,
)
from .engine import Action, Err, Match, MatchConfig, State

__all__ = [
    "ALL_CARDS",
    "Card",
    "Doudizhu",
    "Action",
    "Err",
    "Match",
    "MatchConfig",
    "State",
    "cards_greater",
    "check_card_type",
    "hand_str",
    "list_greater_cards",
    "new_game",
]
