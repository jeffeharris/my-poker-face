"""Tests for the live-table bridge decision logic (Phase 2c brain).

Pure: a real TournamentSession driven with simulated human-table results, no
Flask/engine. Verifies the boundary outcome classification and the seat-spec
contract the game builder/sync consume.
"""

import pytest

from flask_app.handlers.tournament_handler import (
    COMPLETE,
    CONTINUE,
    HUMAN_OUT,
    RELOCATED,
    coordinate_after_human_hand,
    human_table_seat_specs,
)
from tournament.config import TournamentConfig
from tournament.director import FakeHandResolver
from tournament.session import TournamentSession


def _session(field_size: int = 18, table_size: int = 6, seed: int = 0) -> TournamentSession:
    config = TournamentConfig(
        field_size=field_size, table_size=table_size, starting_stack=10_000, seed=seed,
        rounds_per_level=3,
    )
    return TournamentSession(config, ai_resolver=FakeHandResolver())


def _human_result(session: TournamentSession) -> dict:
    table = session.human_table
    seat_order = table.players
    stacks = {pid: session.field.stacks[pid] for pid in seat_order}
    return FakeHandResolver().resolve(
        seat_order=seat_order,
        stacks=stacks,
        level=session.current_level(),
        button=table.dealer_index_in_occupied(),
        seed=session.rounds * 13 + 1,
    )


def test_seat_specs_describe_the_human_table():
    session = _session(18)
    specs = human_table_seat_specs(session)
    assert len(specs) == session.human_table.size
    humans = [s for s in specs if s.is_human]
    assert len(humans) == 1
    assert humans[0].player_id == session.human_id
    assert sum(1 for s in specs if s.is_button) == 1
    for s in specs:
        assert s.stack == session.field.stacks[s.player_id]
        assert s.archetype == session.entries[s.player_id]


def test_continue_outcome_while_human_in_and_table_stable():
    session = _session(18)
    prev = session.human_table.table_id
    out = coordinate_after_human_hand(session, _human_result(session), prev)
    # one round in, 18 players won't have collapsed the human's table yet
    assert out.kind in (CONTINUE, RELOCATED)  # relocation only if a break happened
    assert out.standings['players_remaining'] <= 18


def test_full_run_reaches_complete_or_human_out():
    session = _session(18, seed=2)
    prev = session.human_table.table_id
    last = None
    guard = 0
    while True:
        last = coordinate_after_human_hand(session, _human_result(session), prev)
        guard += 1
        assert guard < 100_000
        if last.kind in (HUMAN_OUT, COMPLETE):
            break
        prev = last.table_id  # follow relocations
        assert session.field.chip_sum() == session.config.total_chips
    assert last.kind in (HUMAN_OUT, COMPLETE)


def test_relocation_is_detected_when_table_id_changes():
    # 24 entrants over 4 tables collapses; following prev_table_id, a relocation
    # outcome must surface at least once (unless the human busts first).
    session = _session(24, seed=5)
    prev = session.human_table.table_id
    saw_relocation = False
    while not session.is_complete() and not session.human_out:
        out = coordinate_after_human_hand(session, _human_result(session), prev)
        if out.kind == RELOCATED:
            saw_relocation = True
            assert out.table_id != prev
        if out.kind in (HUMAN_OUT, COMPLETE):
            break
        prev = out.table_id
    if not session.human_out:
        assert saw_relocation


def test_seat_specs_raise_when_human_out():
    for seed in range(12):
        session = _session(18, seed=seed)
        prev = session.human_table.table_id
        while not session.is_complete() and not session.human_out:
            out = coordinate_after_human_hand(session, _human_result(session), prev)
            if out.kind in (HUMAN_OUT, COMPLETE):
                break
            prev = out.table_id
        if session.human_out:
            with pytest.raises(RuntimeError):
                human_table_seat_specs(session)
            return
    pytest.skip("human won every sampled seed")
