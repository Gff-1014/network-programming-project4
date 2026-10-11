# -*- coding: utf-8 -*-
"""移植自 onestraw/doudizhu 的 tests/test_card.py（MIT）。

仅修改导入路径：doudizhu -> game.cards
"""

import unittest

from game.cards import Card


class TestCard(unittest.TestCase):
    def test_cards_without_suit(self):
        test_cards = [
            Card.new('3s'),
            Card.new('3h'),
            Card.new('3d'),
            Card.new('3c'),
            Card.new('10c'),
            Card.new('10d'),
            Card.new('10h'),
            Card.new('10s'),
            Card.new('BJ'),
            Card.new('CJ'),
        ]

        ret = Card.cards_without_suit(test_cards)
        self.assertEqual(ret, '3-3-3-3-10-10-10-10-BJ-CJ')


if __name__ == '__main__':
    unittest.main()
