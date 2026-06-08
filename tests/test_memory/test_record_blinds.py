"""record_blinds posts SB/BB into the hand record.

Regression guard for the key mismatch where record_blinds read 'SB'/'BB'
from game_state.table_positions, but PokerGameState.table_positions uses
'small_blind_player'/'big_blind_player' — so the lookups silently no-op'd
and blinds were never recorded.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _player(name: str):
    return SimpleNamespace(name=name, stack=1000, is_human=False, hand=[])


@pytest.fixture
def mgr():
    from poker.memory.memory_manager import AIMemoryManager

    return AIMemoryManager(game_id="test_game", db_path=None)


@pytest.fixture
def game_state():
    # Mirrors PokerGameState.table_positions key names (3-handed).
    return SimpleNamespace(
        players=[_player("Alice"), _player("Bob"), _player("Cara")],
        table_positions={
            "button": "Alice",
            "small_blind_player": "Bob",
            "big_blind_player": "Cara",
        },
        current_ante=100,  # BB; SB is half
    )


def test_blinds_are_recorded(mgr, game_state):
    mgr.hand_recorder.start_hand(game_state, hand_number=1)
    mgr.record_blinds(game_state)

    actions = mgr.hand_recorder.current_hand.actions
    posts = [a for a in actions if a.action == "post_blind"]
    assert len(posts) == 2

    by_name = {a.player_name: a for a in posts}
    assert by_name["Bob"].amount == 50  # SB = BB // 2
    assert by_name["Cara"].amount == 100  # BB
    # Pot runs SB then BB.
    assert by_name["Bob"].pot_after == 50
    assert by_name["Cara"].pot_after == 150


def test_heads_up_blinds(mgr):
    # In HU, table_positions sets small_blind_player == button.
    gs = SimpleNamespace(
        players=[_player("Alice"), _player("Bob")],
        table_positions={
            "button": "Alice",
            "small_blind_player": "Alice",
            "big_blind_player": "Bob",
        },
        current_ante=40,
    )
    mgr.hand_recorder.start_hand(gs, hand_number=1)
    mgr.record_blinds(gs)

    posts = [a for a in mgr.hand_recorder.current_hand.actions if a.action == "post_blind"]
    by_name = {a.player_name: a for a in posts}
    assert by_name["Alice"].amount == 20
    assert by_name["Bob"].amount == 40


def test_no_hand_in_progress_is_safe(mgr, game_state):
    # No start_hand → record_blinds logs a warning and returns without raising.
    mgr.record_blinds(game_state)
    assert mgr.hand_recorder.current_hand is None
