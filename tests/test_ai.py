"""托管 / 机器人策略测试：三个机器人必须能自动打完一局。"""

from __future__ import annotations

from game import Match, State, ai


def test_three_bots_finish_a_game():
    match = Match(["bot0", "bot1", "bot2"], seed=20261011)
    for player in match.players:
        assert match.ready(player).ok

    steps = 0
    while match.state is not State.SETTLED:
        assert steps < 1000, "机器人对局没有收敛"
        action = ai.act(match)
        assert action is not None and action.ok, f"第 {steps} 步动作失败: {action}"
        steps += 1

    result = match.result
    assert result["landlord_win"] in (True, False)
    assert sum(result["scores"].values()) == 0
    assert result["multiplier"] >= 1


def test_bots_finish_games_with_many_seeds():
    """多组 seed 回归：任何一种发牌都要能打完，不能卡住。"""
    for seed in range(1, 21):
        match = Match(["b0", "b1", "b2"], seed=seed)
        for player in match.players:
            match.ready(player)
        steps = 0
        while match.state is not State.SETTLED:
            assert steps < 1000, f"seed={seed} 未收敛"
            assert ai.act(match).ok
            steps += 1
        assert sum(match.result["scores"].values()) == 0
