"""Resilience regressions for the live tournament hand boundary.

These cover the failure modes that could leave a human's live tournament
permanently wedged at a hand boundary (the same class as the identity-freeze in
test_live_boundary_identity.py, but from chip/seat desyncs and re-entry):

- H4: the live-table guard reconciles (warns) instead of raising on a chip or
  seat-set mismatch — the live engine is the chip authority for the human's
  table, and a raised guard freezes the game forever.
- H3: human_table_seat_specs tolerates a seat missing from the field (`.get`)
  instead of KeyError-ing.
- H5: advance_tournament_after_hand short-circuits to the terminal outcome on an
  already-terminal session (a re-entered boundary) instead of raising out of
  apply_live_round.
"""

from types import SimpleNamespace

from flask_app.handlers.tournament_handler import (
    COMPLETE,
    HUMAN_OUT,
    advance_tournament_after_hand,
    human_table_seat_specs,
)
from tournament.config import TournamentConfig
from tournament.director import FakeHandResolver
from tournament.session import TournamentSession


def _session(field_size: int = 12, seed: int = 0) -> TournamentSession:
    config = TournamentConfig(
        field_size=field_size,
        table_size=6,
        starting_stack=10_000,
        seed=seed,
        rounds_per_level=3,
    )
    return TournamentSession(config, ai_resolver=FakeHandResolver(), human_id="P01")


def test_apply_result_reconciles_on_chip_mismatch():
    """A live result that doesn't conserve chips must reconcile to live, not raise."""
    session = _session()
    table = session.human_table
    pids = list(table.players)
    result = {pid: session.field.stacks[pid] for pid in pids}
    result[pids[0]] += 500  # break conservation

    session._apply_result(table, result)  # must not raise

    assert session.field.stacks[pids[0]] == result[pids[0]]  # reconciled to live


def test_apply_result_reconciles_on_set_mismatch():
    """A live result whose player set differs from the field must reconcile, not raise."""
    session = _session()
    table = session.human_table
    pids = list(table.players)
    result = {pid: session.field.stacks[pid] for pid in pids}
    result["ghost_seat"] = 0  # set now differs from the field's view

    session._apply_result(table, result)  # must not raise


def test_seat_specs_tolerate_missing_field_stack():
    """A seat present at the table but absent from the field must be treated as
    out (stack 0) rather than KeyError-ing and freezing the boundary."""
    session = _session()
    table = session.human_table
    victim = next(p for p in table.players if p != session.human_id)
    del session.field.stacks[victim]  # simulate a live/session desync

    specs = human_table_seat_specs(session)  # must not raise

    spec = next(s for s in specs if s.player_id == victim)
    assert spec.stack == 0


def test_advance_short_circuits_on_terminal_session():
    """On an already-terminal session (re-entered boundary) the boundary must
    return the terminal outcome, not raise out of apply_live_round."""
    session = _session()
    del session.field.stacks[session.human_id]  # human busted -> human_out
    assert session.human_out

    game_data = {
        "tournament_session": session,
        "tournament_human_id": session.human_id,
        "ai_controllers": {},
    }
    # game_state must NOT be touched — the short-circuit returns before reading it.
    sm = SimpleNamespace(game_state=SimpleNamespace(players=()))

    outcome = advance_tournament_after_hand(game_data, sm, make_controller=lambda *_: None)

    assert outcome.kind in (HUMAN_OUT, COMPLETE)
