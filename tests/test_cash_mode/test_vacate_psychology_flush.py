"""T3-77 — an AI that leaves the human's cash table mid-session flushes its
evolved mood back to the cash world before it's dropped, so the persona carries
that mood onward (idle pool / re-seat / off-screen sim).

Drives `_remove_departed_ais_from_game` directly with a fake controller +
recording bankroll repo.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from flask_app.handlers.game_handler import _remove_departed_ais_from_game
from poker.player_psychology import PlayerPsychology
from poker.poker_game import Player, PokerGameState

pytestmark = pytest.mark.flask

SANDBOX = "sb-1"
NAME = "incumbent"
PID = "napoleon"


class _RecordingBankrollRepo:
    def __init__(self):
        self.saves = {}

    def save_emotional_state_json(self, pid, blob, sandbox_id=None):
        self.saves[(pid, sandbox_id)] = blob


def _controller(hand_count):
    psych = PlayerPsychology.from_personality_config(PID, {})
    psych.hand_count = hand_count
    return SimpleNamespace(psychology=psych, ai_player=SimpleNamespace(personality_config={}))


def _state_machine():
    players = (
        Player(name="human:me", stack=10_000, is_human=True),
        Player(name=NAME, stack=8_000, is_human=False),
    )
    gs = PokerGameState(
        players=players, deck=(), current_ante=100, last_raise_amount=100, current_dealer_idx=0
    )
    return SimpleNamespace(game_state=gs)


def _patch_env(monkeypatch, repo):
    import flask_app.extensions as ext
    from flask_app.services import game_state_service

    monkeypatch.setattr(ext, "bankroll_repo", repo, raising=False)
    monkeypatch.setattr(game_state_service, "set_game", lambda *a, **k: None, raising=False)


def test_departed_ai_psychology_is_flushed(monkeypatch):
    repo = _RecordingBankrollRepo()
    _patch_env(monkeypatch, repo)

    sm = _state_machine()
    game_data = {
        "ai_controllers": {NAME: _controller(9)},
        "cash_personality_ids": {NAME: PID},
        "sandbox_id": SANDBOX,
    }

    _remove_departed_ais_from_game("cash-x", game_data, sm, {PID})

    assert (PID, SANDBOX) in repo.saves
    assert json.loads(repo.saves[(PID, SANDBOX)])["hand_count"] == 9
    # And the seat is actually dropped.
    assert NAME not in game_data["ai_controllers"]
    assert all(p.name != NAME for p in sm.game_state.players)


def test_no_flush_without_sandbox(monkeypatch):
    repo = _RecordingBankrollRepo()
    _patch_env(monkeypatch, repo)

    sm = _state_machine()
    game_data = {
        "ai_controllers": {NAME: _controller(9)},
        "cash_personality_ids": {NAME: PID},
        # no sandbox_id
    }

    _remove_departed_ais_from_game("cash-x", game_data, sm, {PID})

    assert repo.saves == {}  # nothing flushed
    assert NAME not in game_data["ai_controllers"]  # still removed
