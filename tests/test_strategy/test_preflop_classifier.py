"""Tests for poker.strategy.preflop_classifier."""

from types import SimpleNamespace

import pytest

from poker.strategy.preflop_classifier import (
    build_preflop_node,
    classify_preflop_scenario,
    get_6max_position,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _player(name, bet=0):
    """Lightweight player stub."""
    return SimpleNamespace(name=name, bet=bet, is_folded=False, stack=1000)


def _game(players, dealer_idx=0, current_idx=0, raises=0, ante=50, table_positions=None,
          opener_idx=-1):
    """Build a minimal game-state namespace for testing."""
    gs = SimpleNamespace(
        players=tuple(players),
        current_dealer_idx=dealer_idx,
        current_player_idx=current_idx,
        raises_this_round=raises,
        current_ante=ante,
        preflop_opener_idx=opener_idx,
    )
    # Allow caller to supply explicit positions; otherwise derive them
    # using the same logic as PokerGameState.table_positions.
    if table_positions is not None:
        gs.table_positions = table_positions
    else:
        gs.table_positions = _derive_positions(gs)
    return gs


def _derive_positions(gs):
    """Reproduce the position-building logic from PokerGameState."""
    num = len(gs.players)
    dealer = gs.current_dealer_idx

    if num == 2:
        return {
            "button": gs.players[dealer].name,
            "small_blind_player": gs.players[dealer].name,
            "big_blind_player": gs.players[(dealer + 1) % num].name,
        }

    base = ["button", "small_blind_player", "big_blind_player"]
    configs = {
        3: base,
        4: base + ["under_the_gun"],
        5: base + ["under_the_gun", "cutoff"],
        6: base + ["under_the_gun", "middle_position_1", "cutoff"],
    }
    all_pos = configs.get(num, base)
    return {pos: gs.players[(dealer + i) % num].name for i, pos in enumerate(all_pos) if i < num}


# ── Position mapping: 6 players ───────────────────────────────────────


class TestGet6maxPosition:
    """Verify that each seat maps to the correct 6-max label."""

    @pytest.fixture()
    def six_player_game(self):
        players = [_player(f"P{i}") for i in range(6)]
        return _game(players, dealer_idx=0)

    @pytest.mark.parametrize(
        "idx,expected",
        [
            (0, "BTN"),
            (1, "SB"),
            (2, "BB"),
            (3, "UTG"),
            (4, "HJ"),
            (5, "CO"),
        ],
    )
    def test_six_player_positions(self, six_player_game, idx, expected):
        assert get_6max_position(six_player_game, idx) == expected


# ── Position collapse: fewer players ──────────────────────────────────


class TestPositionCollapse:
    def test_five_players_no_hj(self):
        players = [_player(f"P{i}") for i in range(5)]
        gs = _game(players, dealer_idx=0)
        labels = [get_6max_position(gs, i) for i in range(5)]
        assert labels == ["BTN", "SB", "BB", "UTG", "CO"]

    def test_four_players(self):
        players = [_player(f"P{i}") for i in range(4)]
        gs = _game(players, dealer_idx=0)
        labels = [get_6max_position(gs, i) for i in range(4)]
        assert labels == ["BTN", "SB", "BB", "UTG"]

    def test_three_players(self):
        players = [_player(f"P{i}") for i in range(3)]
        gs = _game(players, dealer_idx=0)
        labels = [get_6max_position(gs, i) for i in range(3)]
        assert labels == ["BTN", "SB", "BB"]

    def test_two_players_heads_up(self):
        players = [_player("Hero"), _player("Villain")]
        gs = _game(players, dealer_idx=0)
        # In heads-up, button IS small blind
        assert get_6max_position(gs, 0) == "SB"
        assert get_6max_position(gs, 1) == "BB"


# ── Scenario classification ───────────────────────────────────────────


class TestClassifyPreflopScenario:
    def test_rfi_utg(self):
        """Unopened pot, UTG to act → rfi."""
        players = [_player(f"P{i}") for i in range(6)]
        gs = _game(players, dealer_idx=0, current_idx=3, raises=0)
        scenario, pos, opener = classify_preflop_scenario(gs)
        assert scenario == "rfi"
        assert pos == "UTG"
        assert opener == ""

    def test_vs_open_btn_facing_co_raise(self):
        """CO opens (1 raise), BTN to act → vs_open, opener=CO."""
        players = [
            _player("P0"),  # BTN
            _player("P1"),  # SB
            _player("P2", bet=50),  # BB (posted blind)
            _player("P3"),  # UTG
            _player("P4"),  # HJ
            _player("P5", bet=150),  # CO (opened to 150)
        ]
        gs = _game(players, dealer_idx=0, current_idx=0, raises=1)
        scenario, pos, opener = classify_preflop_scenario(gs)
        assert scenario == "vs_open"
        assert pos == "BTN"
        assert opener == "CO"

    def test_bb_defend_vs_btn_open(self):
        """BTN opens, action to BB → vs_open, opener=BTN."""
        players = [
            _player("P0", bet=150),  # BTN (opened)
            _player("P1"),  # SB (folded)
            _player("P2", bet=50),  # BB (to act)
            _player("P3"),  # UTG
            _player("P4"),  # HJ
            _player("P5"),  # CO
        ]
        gs = _game(players, dealer_idx=0, current_idx=2, raises=1)
        scenario, pos, opener = classify_preflop_scenario(gs)
        assert scenario == "vs_open"
        assert pos == "BB"
        assert opener == "BTN"

    def test_vs_3bet(self):
        """CO opens, BTN 3-bets, CO (the opener) to act → vs_3bet, opener=BTN (3-bettor)."""
        players = [
            _player("P0", bet=450),  # BTN (3-bet to 450)
            _player("P1"),  # SB
            _player("P2", bet=50),  # BB
            _player("P3"),  # UTG
            _player("P4"),  # HJ
            _player("P5", bet=150),  # CO (original open to 150)
        ]
        gs = _game(players, dealer_idx=0, current_idx=5, raises=2, opener_idx=5)
        scenario, pos, opener = classify_preflop_scenario(gs)
        assert scenario == "vs_3bet"
        assert pos == "CO"
        assert opener == "BTN"

    def test_vs_squeeze_cold_caller(self):
        """CO opens, BTN cold-calls, SB squeezes (3-bet), BTN (the caller) to act →
        vs_squeeze, opener=SB (the squeezer/aggressor)."""
        players = [
            _player("P0", bet=450),  # BTN cold-called the open, now faces the squeeze
            _player("P1", bet=900),  # SB squeezed to 900
            _player("P2", bet=50),  # BB
            _player("P3"),  # UTG
            _player("P4"),  # HJ
            _player("P5", bet=450),  # CO (original opener, also faces the squeeze)
        ]
        # CO (P5) opened; BTN (P0) is to act and is NOT the opener → squeeze.
        gs = _game(players, dealer_idx=0, current_idx=0, raises=2, opener_idx=5)
        scenario, pos, opener = classify_preflop_scenario(gs)
        assert scenario == "vs_squeeze"
        assert pos == "BTN"
        assert opener == "SB"

    def test_opener_faces_squeeze_is_vs_3bet(self):
        """Same squeeze, but now the ORIGINAL opener (CO) is to act → vs_3bet,
        not vs_squeeze: the opener's range is uncapped."""
        players = [
            _player("P0", bet=450),  # BTN cold-caller
            _player("P1", bet=900),  # SB squeezer
            _player("P2", bet=50),  # BB
            _player("P3"),  # UTG
            _player("P4"),  # HJ
            _player("P5", bet=450),  # CO opener, to act
        ]
        gs = _game(players, dealer_idx=0, current_idx=5, raises=2, opener_idx=5)
        scenario, pos, opener = classify_preflop_scenario(gs)
        assert scenario == "vs_3bet"
        assert pos == "CO"
        assert opener == "SB"

    def test_unknown_opener_falls_back_to_vs_3bet(self):
        """Defensive: opener_idx == -1 at two raises (shouldn't happen) →
        vs_3bet, preserving pre-split behavior."""
        players = [
            _player("P0", bet=450),
            _player("P1", bet=900),
            _player("P2", bet=50),
            _player("P3"),
            _player("P4"),
            _player("P5", bet=450),
        ]
        gs = _game(players, dealer_idx=0, current_idx=0, raises=2, opener_idx=-1)
        scenario, _, _ = classify_preflop_scenario(gs)
        assert scenario == "vs_3bet"

    def test_vs_4bet(self):
        """3+ raises → vs_4bet."""
        players = [
            _player("P0", bet=450),  # BTN (3-bet)
            _player("P1"),  # SB
            _player("P2", bet=50),  # BB
            _player("P3"),  # UTG
            _player("P4"),  # HJ
            _player("P5", bet=1200),  # CO (4-bet to 1200)
        ]
        gs = _game(players, dealer_idx=0, current_idx=0, raises=3)
        scenario, pos, opener = classify_preflop_scenario(gs)
        assert scenario == "vs_4bet"
        assert pos == "BTN"
        assert opener == "CO"


# ── build_preflop_node integration ─────────────────────────────────────


class TestBuildPreflopNode:
    def test_rfi_node(self):
        players = [_player(f"P{i}") for i in range(6)]
        gs = _game(players, dealer_idx=0, current_idx=3, raises=0)
        node = build_preflop_node(gs, 3, "AKs")
        assert node.hand == "AKs"
        assert node.position == "UTG"
        assert node.scenario == "rfi"
        assert node.opener_position == ""
        assert node.key == "rfi|UTG||AKs"

    def test_vs_open_node(self):
        players = [
            _player("P0"),  # BTN
            _player("P1"),  # SB
            _player("P2", bet=50),  # BB
            _player("P3"),  # UTG
            _player("P4"),  # HJ
            _player("P5", bet=150),  # CO (opened)
        ]
        gs = _game(players, dealer_idx=0, current_idx=0, raises=1)
        node = build_preflop_node(gs, 0, "TT")
        assert node.hand == "TT"
        assert node.position == "BTN"
        assert node.scenario == "vs_open"
        assert node.opener_position == "CO"
        assert node.key == "vs_open|BTN|CO|TT"

    def test_heads_up_node(self):
        """Heads-up: button/SB opens, BB to act."""
        players = [
            _player("Hero", bet=150),  # BTN/SB (opened)
            _player("Villain", bet=50),  # BB
        ]
        gs = _game(players, dealer_idx=0, current_idx=1, raises=1)
        node = build_preflop_node(gs, 1, "72o")
        assert node.position == "BB"
        assert node.scenario == "vs_open"
        assert node.opener_position == "SB"
