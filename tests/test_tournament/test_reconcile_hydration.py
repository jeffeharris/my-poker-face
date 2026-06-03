"""T3-77 — a persona balanced ONTO the human's table mid-tournament hydrates
its mood from the cash world, just like the initial builder.

Drives `reconcile_live_table` directly with a fake state machine + a seeded
emotional_state_json blob, asserting the genuinely-new real-persona seat is
hydrated (and that the gate holds: no sandbox, or not a real persona, => no
hydrate).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from flask_app.handlers.tournament_handler import SeatSpec, reconcile_live_table
from poker.player_psychology import PlayerPsychology
from poker.poker_game import Player, PokerGameState

SANDBOX = "sb-1"
NEWCOMER = "napoleon"


class _FakeBankrollRepo:
    def __init__(self, blobs):
        self._blobs = blobs

    def load_emotional_state_json(self, pid, sandbox_id=None):
        return self._blobs.get((pid, sandbox_id))


def _state_machine():
    # Human + one incumbent AI; the newcomer arrives via seat_specs.
    players = (
        Player(name="human:me", stack=10_000, is_human=True),
        Player(name="incumbent", stack=10_000, is_human=False),
    )
    gs = PokerGameState(
        players=players, deck=(), current_ante=100, last_raise_amount=100, current_dealer_idx=0
    )
    return SimpleNamespace(game_state=gs)


def _specs():
    return [
        SeatSpec(
            player_id="human:me", stack=10_000, archetype="human", is_human=True, is_button=True
        ),
        SeatSpec(
            player_id="incumbent", stack=10_000, archetype="x", is_human=False, is_button=False
        ),
        SeatSpec(player_id=NEWCOMER, stack=10_000, archetype="x", is_human=False, is_button=False),
    ]


def _make_controller_factory():
    """make_controller stub: every new seat gets a baseline psychology
    (hand_count=0) so a successful hydrate is observable as a non-zero value."""

    def _make(name, sm):
        psych = PlayerPsychology.from_personality_config(name, {})
        psych.hand_count = 0
        # Real tournament controllers chain into AIPokerPlayer, so `ai_player`
        # exists — the hydrate hook reads `ai_player.personality_config`.
        return SimpleNamespace(
            psychology=psych, state_machine=sm, ai_player=SimpleNamespace(personality_config={})
        )

    return _make


def _seed_blob(hand_count):
    world = PlayerPsychology.from_personality_config(NEWCOMER, {})
    world.hand_count = hand_count
    return json.dumps(world.to_dict())


def test_balanced_in_persona_is_hydrated(monkeypatch):
    import flask_app.extensions as ext

    monkeypatch.setattr(
        ext,
        "bankroll_repo",
        _FakeBankrollRepo({(NEWCOMER, SANDBOX): _seed_blob(42)}),
        raising=False,
    )
    monkeypatch.setattr(ext, "personality_repo", None, raising=False)

    sm = _state_machine()
    ai_controllers = {"incumbent": SimpleNamespace(psychology=None, state_machine=sm)}
    added, removed = reconcile_live_table(
        sm,
        ai_controllers,
        None,  # memory_manager
        _specs(),
        big_blind=100,
        make_controller=_make_controller_factory(),
        real_persona_ids={NEWCOMER, "incumbent"},
        sandbox_id=SANDBOX,
    )

    assert NEWCOMER in added
    assert ai_controllers[NEWCOMER].psychology.hand_count == 42  # hydrated from the world


def test_no_hydrate_without_sandbox(monkeypatch):
    import flask_app.extensions as ext

    monkeypatch.setattr(
        ext,
        "bankroll_repo",
        _FakeBankrollRepo({(NEWCOMER, SANDBOX): _seed_blob(42)}),
        raising=False,
    )
    monkeypatch.setattr(ext, "personality_repo", None, raising=False)

    sm = _state_machine()
    ai_controllers = {"incumbent": SimpleNamespace(psychology=None, state_machine=sm)}
    reconcile_live_table(
        sm,
        ai_controllers,
        None,
        _specs(),
        big_blind=100,
        make_controller=_make_controller_factory(),
        real_persona_ids={NEWCOMER},
        sandbox_id=None,  # non-cash field => no hydrate
    )

    assert ai_controllers[NEWCOMER].psychology.hand_count == 0  # baseline


def test_no_hydrate_for_synthetic_seat(monkeypatch):
    import flask_app.extensions as ext

    monkeypatch.setattr(
        ext,
        "bankroll_repo",
        _FakeBankrollRepo({(NEWCOMER, SANDBOX): _seed_blob(42)}),
        raising=False,
    )
    monkeypatch.setattr(ext, "personality_repo", None, raising=False)

    sm = _state_machine()
    ai_controllers = {"incumbent": SimpleNamespace(psychology=None, state_machine=sm)}
    reconcile_live_table(
        sm,
        ai_controllers,
        None,
        _specs(),
        big_blind=100,
        make_controller=_make_controller_factory(),
        real_persona_ids=frozenset(),  # newcomer not a real persona
        sandbox_id=SANDBOX,
    )

    assert ai_controllers[NEWCOMER].psychology.hand_count == 0  # baseline
