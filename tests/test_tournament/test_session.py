"""Headless tests for the live-human seam (TournamentSession).

The human is simulated with the deterministic FakeHandResolver so the whole
session — pacing, world-pause, relocation, standings views — is exercised without
the poker engine or any UI.
"""

import pytest

from tournament.config import TournamentConfig
from tournament.director import FakeHandResolver
from tournament.session import TournamentSession


def _session(field_size: int = 18, table_size: int = 6, seed: int = 0) -> TournamentSession:
    config = TournamentConfig(
        field_size=field_size,
        table_size=table_size,
        starting_stack=10_000,
        seed=seed,
        rounds_per_level=3,
    )
    return TournamentSession(config, ai_resolver=FakeHandResolver())


def _run_to_completion(session: TournamentSession) -> dict:
    """Play the human's hands (fake) until they're out or win, then fast-forward
    the rest of the field. Returns the final standings view."""
    human = FakeHandResolver().resolve
    guard = 0
    while not session.is_complete() and not session.human_out:
        session.play_round(human)
        guard += 1
        assert guard < 100_000
    if not session.is_complete():
        session.play_out()
    return session.standings_view()


# ── world-pause (player-gated time) ──────────────────────────────────────────


def test_no_advance_without_play_round():
    session = _session()
    before = session.standings_view()
    # Reading standings repeatedly must not move the world.
    for _ in range(5):
        session.standings_view()
    after = session.standings_view()
    assert before == after
    assert session.rounds == 0
    assert session.field.chip_sum() == session.config.total_chips
    assert all(
        seat['stack'] in (None, session.config.starting_stack)
        for t in after['tables']
        for seat in t['seats']
    )


# ── full run + conservation ──────────────────────────────────────────────────


def test_session_runs_to_completion():
    session = _session(18)
    view = _run_to_completion(session)
    assert view['complete']
    assert view['winner'] is not None
    assert view['players_remaining'] == 1
    # Conservation held the whole way (asserted each round internally too).
    assert session.field.chip_sum() == session.config.total_chips


def test_human_always_seated_while_in():
    session = _session(24)
    human = FakeHandResolver().resolve
    while not session.is_complete() and not session.human_out:
        # While the human is in, they must always have exactly one table.
        assert session.human_table is not None
        assert session.human_id in session.human_table.players
        session.play_round(human)


def test_human_relocates_across_table_breaks():
    # 24 entrants over 4 tables collapses to 1 — the human must end up moved at
    # least once (their starting table cannot survive the whole way).
    session = _session(24, seed=3)
    human = FakeHandResolver().resolve
    start_table = session.human_table.table_id
    moved = False
    while not session.is_complete() and not session.human_out:
        session.play_round(human)
        ht = session.human_table
        if ht is not None and ht.table_id != start_table:
            moved = True
    # Either the human moved tables, or they busted before any break — both are
    # valid; if the field collapsed while they survived, they must have moved.
    if not session.human_out:
        assert moved


def test_human_out_then_play_out_completes():
    # Find a seed where the human busts (most do), then confirm play_out finishes
    # the field and the human's finishing position is recorded.
    for seed in range(12):
        session = _session(18, seed=seed)
        human = FakeHandResolver().resolve
        while not session.is_complete() and not session.human_out:
            session.play_round(human)
        if session.human_out:
            rank = session.human_rank()
            assert rank is not None and 2 <= rank <= session.config.field_size
            session.play_out()
            assert session.is_complete()
            assert session.field.chip_sum() == session.config.total_chips
            return
    pytest.skip("human won every sampled seed; bust path not exercised")


# ── pacing ───────────────────────────────────────────────────────────────────


def test_ai_tables_pace_with_the_human():
    # Over R human rounds with several other tables, the AI tables must add hands
    # beyond the human's one-per-round (mean ~1 each), so the total hand count
    # exceeds the number of rounds but stays bounded.
    session = _session(24, seed=1)
    human = FakeHandResolver().resolve
    rounds = 0
    while not session.is_complete() and not session.human_out and rounds < 10:
        session.play_round(human)
        rounds += 1
    # human contributes `rounds` hands; AI tables add more (0/1/2 each).
    assert session._hand_counter > rounds


# ── views ────────────────────────────────────────────────────────────────────


def test_standings_view_is_wellformed_and_conserves():
    session = _session(18)
    human = FakeHandResolver().resolve
    session.play_round(human)
    view = session.standings_view()
    for key in ('field_size', 'players_remaining', 'level', 'human', 'tables'):
        assert key in view
    # Every chip is on some seat.
    total = sum(
        seat['stack'] or 0 for t in view['tables'] for seat in t['seats'] if seat['player_id']
    )
    assert total == session.config.total_chips
    # Exactly one human seat exists while the human is in.
    human_seats = [seat for t in view['tables'] for seat in t['seats'] if seat['is_human']]
    assert len(human_seats) == 1


# ── live bridge entry point ──────────────────────────────────────────────────


def _live_human_result(session: TournamentSession) -> dict:
    """Simulate the live game engine playing one hand at the human's table:
    resolve it with the fake resolver and return {pid: stack}."""
    table = session.human_table
    seat_order = table.players
    stacks = {pid: session.field.stacks[pid] for pid in seat_order}
    return FakeHandResolver().resolve(
        seat_order=seat_order,
        stacks=stacks,
        level=session.current_level(),
        button=table.dealer_index_in_occupied(),
        seed=session.rounds * 31 + 7,
    )


def test_apply_live_round_advances_and_conserves():
    session = _session(18)
    before = session.rounds
    result = _live_human_result(session)
    session.apply_live_round(result)
    assert session.rounds == before + 1
    assert session.field.chip_sum() == session.config.total_chips
    # the AI tables advanced too — more hands than just the human's one
    assert session._hand_counter >= 1


def test_apply_live_round_runs_to_completion():
    session = _session(18)
    guard = 0
    while not session.is_complete() and not session.human_out:
        session.apply_live_round(_live_human_result(session))
        guard += 1
        assert guard < 100_000
        assert session.field.chip_sum() == session.config.total_chips
    if not session.is_complete():
        session.play_out()
    assert session.is_complete()


def test_apply_live_round_rejects_when_human_out():
    # drive to human-out, then apply_live_round must refuse.
    for seed in range(12):
        session = _session(18, seed=seed)
        while not session.is_complete() and not session.human_out:
            session.apply_live_round(_live_human_result(session))
        if session.human_out:
            with pytest.raises(RuntimeError):
                session.apply_live_round({})
            return
    pytest.skip("human won every sampled seed")


def test_reproducible_from_seed():
    s1 = _session(18, seed=9)
    s2 = _session(18, seed=9)
    v1 = _run_to_completion(s1)
    v2 = _run_to_completion(s2)
    assert v1['winner'] == v2['winner']
    assert v1['players_remaining'] == v2['players_remaining']
