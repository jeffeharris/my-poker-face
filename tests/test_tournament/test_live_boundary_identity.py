"""Regression tests for the live multi-table tournament hand-boundary freeze.

Root cause (prod, 2026-06-06): the human's live tournament table froze on its
first hand boundary with `AssertionError: hand resolver changed the set of
players at the table` (`tournament/session.py:_guard_table_result`).

The field keys every seat by its slug pid (`winston_churchill`); the boundary
keyed the live result via `seat_key(player)`, which falls back to the display
`name` when the typed `seat_id`/`personality_id` are absent. `reset_game_state_
for_new_hand` rebuilt every Player bare on each deal (including the opening
deal), dropping that identity — so the live result came back keyed by
`"Winston Churchill"` while the session table was keyed by `winston_churchill`,
the sets diverged, and the guard wedged the game.

Two complementary fixes, one test each:
  1. the deal carries identity forward (`reset_game_state_for_new_hand`)
  2. the boundary recovers the pid from the display name via the session roster
     (`_field_key` / `_session_display_to_pid`) — covers cold loads too, where
     the live players are rebuilt with no identity at all.
"""

from types import SimpleNamespace

from flask_app.handlers.tournament_handler import (
    COMPLETE,
    HUMAN_OUT,
    advance_tournament_after_hand,
    human_table_seat_specs,
)
from poker.poker_game import (
    Player,
    PokerGameState,
    reset_game_state_for_new_hand,
)
from poker.table.seat import HumanSeat, PersonaSeat
from tournament.config import TournamentConfig
from tournament.director import FakeHandResolver
from tournament.session import TournamentSession


def test_reset_for_new_hand_preserves_seat_identity():
    """A new hand must carry seat_id / personality_id / nickname forward — they
    identify the seat and never change between hands. Dropping them is what froze
    the tournament bridge."""
    players = (
        Player(
            name="Winston Churchill",
            stack=10_000,
            is_human=False,
            nickname="Winston Churchill",
            personality_id="winston_churchill",
            seat_id=PersonaSeat("winston_churchill"),
        ),
        Player(
            name="Jeff Harris",
            stack=10_000,
            is_human=True,
            seat_id=HumanSeat("google_123"),
        ),
    )
    gs = PokerGameState(players=players, deck=(), current_ante=100, last_raise_amount=100)

    new_gs = reset_game_state_for_new_hand(gs, deck_seed=7)

    by_name = {p.name: p for p in new_gs.players}
    winston = by_name["Winston Churchill"]
    assert winston.personality_id == "winston_churchill"
    assert winston.seat_id == PersonaSeat("winston_churchill")
    assert winston.nickname == "Winston Churchill"
    human = by_name["Jeff Harris"]
    assert human.seat_id == HumanSeat("google_123")


class _StubPersonaRepo:
    """Resolves a pid to a DISTINCT display name, reproducing the prod divergence
    (slug pid `winston_churchill` ≠ display `Winston Churchill`)."""

    @staticmethod
    def load_personality_by_id(pid):
        return {"name": f"Display {pid}"}


def _session(field_size: int = 12, seed: int = 0) -> TournamentSession:
    config = TournamentConfig(
        field_size=field_size,
        table_size=6,
        starting_stack=10_000,
        seed=seed,
        rounds_per_level=3,
    )
    return TournamentSession(config, ai_resolver=FakeHandResolver(), human_id="P01")


def _live_state_with_display_names(session: TournamentSession):
    """Build the live game's players named by their DISPLAY name and stripped of
    typed identity — exactly the state a cold-loaded MTT table is in, and what the
    per-hand deal produced before the fix."""
    specs = human_table_seat_specs(session)
    players = tuple(
        Player(
            name=("Jeff" if s.is_human else f"Display {s.player_id}"),
            stack=s.stack,
            is_human=s.is_human,
        )
        for s in specs
    )
    bb = session.current_level().big_blind
    gs = PokerGameState(
        players=players, deck=(), current_ante=bb, last_raise_amount=bb, current_dealer_idx=0
    )
    return SimpleNamespace(game_state=gs)


def _play_live_hand(session: TournamentSession, sm) -> None:
    gs = sm.game_state
    seat_order = [p.name for p in gs.players]
    stacks = {p.name: p.stack for p in gs.players}
    result = FakeHandResolver().resolve(
        seat_order=seat_order,
        stacks=stacks,
        level=session.current_level(),
        button=gs.current_dealer_idx,
        seed=session.rounds * 13 + 1,
    )
    new_players = tuple(
        Player(name=p.name, stack=result[p.name], is_human=p.is_human) for p in gs.players
    )
    sm.game_state = gs.update(players=new_players)


def test_boundary_recovers_pid_from_display_name(monkeypatch):
    """The boundary must not freeze when the live seats carry only display names.
    Before the fix this raised `AssertionError: hand resolver changed the set of
    players at the table`; now `_field_key` recovers the pid from the session
    roster, the guard passes, and the field folds the result + conserves chips."""
    monkeypatch.setattr("flask_app.extensions.personality_repo", _StubPersonaRepo(), raising=False)
    session = _session()
    sm = _live_state_with_display_names(session)
    specs = human_table_seat_specs(session)
    game_data = {
        "tournament_session": session,
        "tournament_table_id": session.human_table.table_id,
        "tournament_human_id": session.human_id,
        "ai_controllers": {s.player_id: SimpleNamespace() for s in specs if not s.is_human},
        "memory_manager": None,
    }
    total = session.config.total_chips

    _play_live_hand(session, sm)
    # Must not raise — reaching here at all is the regression. Before the fix the
    # display-name-keyed result tripped the guard's set-equality check.
    outcome = advance_tournament_after_hand(
        game_data, sm, make_controller=lambda *_: SimpleNamespace()
    )

    # The result was folded into the field (chips conserved), proving the guard's
    # set + conservation checks both passed — i.e. the display names resolved to
    # the field's pids.
    assert session.field.chip_sum() == total
    assert outcome.kind in ("continue", "relocated", HUMAN_OUT, COMPLETE)
