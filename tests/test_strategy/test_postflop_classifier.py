"""Tests for postflop classifier -- build_postflop_node()."""

from types import SimpleNamespace

import pytest

from poker.strategy.postflop_classifier import (
    _determine_facing_action,
    _determine_position,
    _determine_spr_bucket,
    build_postflop_node,
)

# ---------------------------------------------------------------------------
# Helpers to build minimal mock game states
# ---------------------------------------------------------------------------


def _player(name, stack=1000, bet=0, is_folded=False, is_all_in=False):
    return SimpleNamespace(
        name=name,
        stack=stack,
        bet=bet,
        is_folded=is_folded,
        is_all_in=is_all_in,
        is_human=False,
        has_acted=False,
        last_action=None,
        hand=(),
    )


def _game_state(
    players=None,
    dealer_idx=0,
    current_idx=0,
    raises=0,
    pot_total=100,
    ante=10,
    community_cards=(),
):
    """Build a minimal mock game state."""
    if players is None:
        players = (
            _player('Alice'),
            _player('Bob'),
            _player('Carol'),
            _player('Dave'),
            _player('Eve'),
            _player('Frank'),
        )
    players = tuple(players)

    # Build table_positions based on 6-max seating from dealer
    n = len(players)
    if n == 6:
        keys = [
            'button',
            'small_blind_player',
            'big_blind_player',
            'under_the_gun',
            'middle_position_1',
            'cutoff',
        ]
    elif n == 5:
        keys = ['button', 'small_blind_player', 'big_blind_player', 'under_the_gun', 'cutoff']
    elif n == 4:
        keys = ['button', 'small_blind_player', 'big_blind_player', 'cutoff']
    elif n == 3:
        keys = ['button', 'small_blind_player', 'big_blind_player']
    else:  # heads up
        keys = ['button', 'big_blind_player']

    positions = {}
    for i, key in enumerate(keys):
        idx = (dealer_idx + i) % n
        positions[key] = players[idx].name
    # HU: button is also SB
    if n == 2:
        positions['small_blind_player'] = players[dealer_idx].name

    return SimpleNamespace(
        players=players,
        current_player_idx=current_idx,
        current_dealer_idx=dealer_idx,
        raises_this_round=raises,
        pot={'total': pot_total},
        current_ante=ante,
        community_cards=community_cards,
        table_positions=positions,
        highest_bet=0,
    )


# ---------------------------------------------------------------------------
# Street detection
# ---------------------------------------------------------------------------


class TestStreetDetection:
    def test_flop(self):
        gs = _game_state()
        node = build_postflop_node(gs, 0, ['Ah', 'Kd'], ['Qs', '7c', '2h'])
        assert node.street == 'flop'

    def test_turn(self):
        gs = _game_state()
        node = build_postflop_node(gs, 0, ['Ah', 'Kd'], ['Qs', '7c', '2h', '5d'])
        assert node.street == 'turn'

    def test_river(self):
        gs = _game_state()
        node = build_postflop_node(gs, 0, ['Ah', 'Kd'], ['Qs', '7c', '2h', '5d', '3s'])
        assert node.street == 'river'


# ---------------------------------------------------------------------------
# Position (IP / OOP)
# ---------------------------------------------------------------------------


class TestPositionDetection:
    def test_button_is_ip(self):
        # Alice is BTN (dealer_idx=0), current_idx=0
        gs = _game_state(dealer_idx=0, current_idx=0)
        assert _determine_position(gs, 0) == 'IP'

    def test_bb_is_oop(self):
        # BB is at index 2 (dealer=0 → SB=1, BB=2)
        gs = _game_state(dealer_idx=0, current_idx=2)
        assert _determine_position(gs, 2) == 'OOP'

    def test_co_vs_btn_is_oop(self):
        # CO is index 5 (dealer=0), BTN is index 0
        gs = _game_state(dealer_idx=0, current_idx=5)
        # CO vs BTN (and others) — BTN is more IP
        assert _determine_position(gs, 5) == 'OOP'


# ---------------------------------------------------------------------------
# Facing action
# ---------------------------------------------------------------------------


class TestFacingAction:
    def test_unopened(self):
        gs = _game_state(raises=0)
        assert _determine_facing_action(gs) == 'unopened'

    def test_facing_bet(self):
        gs = _game_state(raises=1)
        assert _determine_facing_action(gs) == 'facing_bet'

    def test_facing_raise(self):
        gs = _game_state(raises=2)
        assert _determine_facing_action(gs) == 'facing_raise'

    def test_facing_multiple_raises(self):
        gs = _game_state(raises=3)
        assert _determine_facing_action(gs) == 'facing_raise'


# ---------------------------------------------------------------------------
# SPR bucket
# ---------------------------------------------------------------------------


class TestSPRBucket:
    def test_high_spr(self):
        gs = _game_state(
            players=[_player('Alice', stack=1000)],
            pot_total=100,
        )
        assert _determine_spr_bucket(gs, 0) == 'high'  # 1000/100 = 10

    def test_medium_spr(self):
        gs = _game_state(
            players=[_player('Alice', stack=400)],
            pot_total=100,
        )
        assert _determine_spr_bucket(gs, 0) == 'medium'  # 400/100 = 4

    def test_low_spr(self):
        gs = _game_state(
            players=[_player('Alice', stack=150)],
            pot_total=100,
        )
        assert _determine_spr_bucket(gs, 0) == 'low'  # 150/100 = 1.5

    def test_zero_pot_returns_high(self):
        gs = _game_state(
            players=[_player('Alice', stack=500)],
            pot_total=0,
        )
        assert _determine_spr_bucket(gs, 0) == 'high'


# ---------------------------------------------------------------------------
# Full build_postflop_node integration
# ---------------------------------------------------------------------------


class TestBuildPostflopNode:
    def test_basic_flop_node(self):
        gs = _game_state(dealer_idx=0, current_idx=0, raises=0, pot_total=100)
        node = build_postflop_node(gs, 0, ['Ah', 'Kd'], ['Ks', '7c', '2h'])
        assert node.street == 'flop'
        assert node.position == 'IP'
        assert node.pot_type == 'SRP'
        assert node.made_tier == 'strong_made'  # TPTK
        assert node.facing_action == 'unopened'
        assert node.spr_bucket == 'high'

    def test_node_key_format(self):
        gs = _game_state(dealer_idx=0, current_idx=0, raises=1, pot_total=200)
        node = build_postflop_node(gs, 0, ['7h', '7d'], ['7s', '4c', '2h'])
        key = node.key
        assert key.startswith('flop|')
        assert '|nuts|' in key
        assert '|facing_bet|' in key

    def test_river_facing_raise_low_spr(self):
        gs = _game_state(
            dealer_idx=0,
            current_idx=2,  # BB is OOP
            raises=2,
            pot_total=800,
            players=[
                _player('Alice', stack=1000),
                _player('Bob', stack=1000),
                _player('Carol', stack=500),
                _player('Dave', stack=1000),
                _player('Eve', stack=1000),
                _player('Frank', stack=1000),
            ],
        )
        node = build_postflop_node(
            gs,
            2,
            ['Ah', 'Qd'],
            ['Ks', '8c', '3h', '5d', '2s'],
        )
        assert node.street == 'river'
        assert node.position == 'OOP'
        assert node.facing_action == 'facing_raise'
        assert node.spr_bucket == 'low'  # 500/800 < 2
