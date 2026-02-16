"""Tests for strategy node data types."""

import pytest
from poker.strategy.nodes import PreflopNode, PostflopNode


class TestPreflopNode:
    def test_immutable(self):
        node = PreflopNode(hand='AA', position='UTG', scenario='rfi', opener_position='')
        with pytest.raises(AttributeError):
            node.hand = 'KK'

    def test_key_rfi(self):
        node = PreflopNode(hand='AKs', position='CO', scenario='rfi', opener_position='')
        assert node.key == 'rfi|CO||AKs'

    def test_key_vs_open(self):
        node = PreflopNode(hand='QQ', position='BTN', scenario='vs_open', opener_position='UTG')
        assert node.key == 'vs_open|BTN|UTG|QQ'

    def test_key_vs_3bet(self):
        node = PreflopNode(hand='AKo', position='UTG', scenario='vs_3bet', opener_position='HJ')
        assert node.key == 'vs_3bet|UTG|HJ|AKo'

    def test_key_vs_4bet(self):
        node = PreflopNode(hand='KK', position='HJ', scenario='vs_4bet', opener_position='UTG')
        assert node.key == 'vs_4bet|HJ|UTG|KK'

    def test_equality(self):
        n1 = PreflopNode(hand='AA', position='UTG', scenario='rfi', opener_position='')
        n2 = PreflopNode(hand='AA', position='UTG', scenario='rfi', opener_position='')
        assert n1 == n2

    def test_hashable(self):
        node = PreflopNode(hand='AA', position='UTG', scenario='rfi', opener_position='')
        s = {node}
        assert node in s


class TestPostflopNode:
    def test_immutable(self):
        node = PostflopNode(
            street='flop', position='IP', pot_type='SRP',
            board_texture='dry_high', made_tier='strong_made',
            draw_modifier='no_draw', facing_action='unopened', spr_bucket='high',
        )
        with pytest.raises(AttributeError):
            node.street = 'turn'

    def test_key(self):
        node = PostflopNode(
            street='flop', position='OOP', pot_type='SRP',
            board_texture='monotone', made_tier='air',
            draw_modifier='strong_draw', facing_action='facing_bet', spr_bucket='medium',
        )
        assert node.key == 'flop|OOP|SRP|monotone|air|strong_draw|facing_bet|medium'

    def test_equality(self):
        kwargs = dict(
            street='flop', position='IP', pot_type='SRP',
            board_texture='dry_high', made_tier='nuts',
            draw_modifier='no_draw', facing_action='unopened', spr_bucket='high',
        )
        assert PostflopNode(**kwargs) == PostflopNode(**kwargs)
