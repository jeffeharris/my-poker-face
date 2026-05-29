"""Pure tests for field-wide standings, finishing positions, and conservation."""

import pytest

from tournament.field import TournamentField


def _field(n: int, stack: int = 1000) -> TournamentField:
    entries = {f"P{i:02d}": 'TAG' for i in range(n)}
    return TournamentField(starting_stack=stack, entries=entries)


def test_initial_state_conserves_chips():
    f = _field(18)
    assert f.active_count == 18
    assert f.chip_sum() == 18 * 1000
    f.assert_conservation()
    assert not f.is_complete()
    assert f.winner() is None


def test_conservation_after_chip_transfer():
    f = _field(3)
    # move chips around without creating/destroying any
    f.stacks['P00'] += 500
    f.stacks['P01'] -= 500
    f.assert_conservation()


def test_conservation_violation_is_caught():
    f = _field(3)
    f.stacks['P00'] += 1  # chips materialized from nowhere
    with pytest.raises(AssertionError):
        f.assert_conservation()


def test_single_elimination_gets_last_position():
    f = _field(6)
    f.stacks['P05'] = 0
    f.record_eliminations([('P05', 1000)], round_index=0)
    assert f.active_count == 5
    assert f.eliminations[-1].player_id == 'P05'
    assert f.eliminations[-1].finishing_position == 6


def test_simultaneous_bust_tiebreak_by_starting_stack():
    f = _field(10)
    # Two players bust the same round: P08 started the hand with MORE chips than
    # P09, so P08 should finish higher (9th) and P09 lower (10th).
    f.stacks['P08'] = 0
    f.stacks['P09'] = 0
    f.record_eliminations([('P08', 800), ('P09', 200)], round_index=3)
    by_player = {e.player_id: e.finishing_position for e in f.eliminations}
    assert by_player['P08'] == 9
    assert by_player['P09'] == 10
    assert f.active_count == 8


def test_positions_are_unique_and_contiguous_to_winner():
    f = _field(5)
    # eliminate one per round until a single winner remains
    order = ['P04', 'P03', 'P02', 'P01']
    for rnd, pid in enumerate(order):
        f.stacks[pid] = 0
        f.record_eliminations([(pid, 100 + rnd)], round_index=rnd)
    assert f.is_complete()
    assert f.winner() == 'P00'
    positions = sorted(e.finishing_position for e in f.eliminations)
    assert positions == [2, 3, 4, 5]


def test_winner_holds_all_chips_invariant_at_finish():
    f = _field(2, stack=1000)
    # heads up: P00 busts P01
    f.stacks['P00'] = 2000
    f.stacks['P01'] = 0
    f.record_eliminations([('P01', 1000)], round_index=0)
    assert f.is_complete()
    assert f.winner() == 'P00'
    f.assert_conservation()
