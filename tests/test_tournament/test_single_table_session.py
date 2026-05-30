"""Single-table TournamentSession: the unification of the legacy single-table
game (step 3B). The session is a passive field/standings/completion observer fed
by `fold_live_hand`; the live engine owns play + blinds."""

import pytest

from tournament.session import TournamentSession


def _session(stack=10000):
    entries = {'Jeff': 'human', 'Gordon': 'TAG', 'Bob': 'LAG'}
    return TournamentSession.for_single_table(
        entries=entries, human_id='Jeff', starting_stack=stack, seed=1
    )


def test_for_single_table_one_table_real_names():
    s = _session()
    assert len(s.seating.tables) == 1
    assert s.field.field_size == 3
    assert s.human_id == 'Jeff'
    assert set(s.field.stacks) == {'Jeff', 'Gordon', 'Bob'}
    assert all(v == 10000 for v in s.field.stacks.values())


def test_fold_live_hand_records_elimination_with_eliminator():
    s = _session()
    events = s.fold_live_hand({'Jeff': 10000, 'Gordon': 20000, 'Bob': 0}, eliminator='Gordon')
    assert len(events) == 1
    assert events[0].player_id == 'Bob'
    assert events[0].finishing_position == 3
    assert events[0].eliminator == 'Gordon'
    assert not s.is_complete() and not s.human_out
    assert 'Bob' not in s.field.stacks


def test_fold_live_hand_to_completion():
    s = _session()
    s.fold_live_hand({'Jeff': 10000, 'Gordon': 20000, 'Bob': 0}, eliminator='Gordon')
    s.fold_live_hand({'Jeff': 0, 'Gordon': 30000}, eliminator='Gordon')
    assert s.is_complete()
    assert s.winner() == 'Gordon'
    assert s.human_out
    assert s.human_rank() == 2  # Jeff finished 2nd


def test_fold_live_hand_human_wins():
    s = _session()
    s.fold_live_hand({'Jeff': 20000, 'Gordon': 10000, 'Bob': 0}, eliminator='Jeff')
    s.fold_live_hand({'Jeff': 30000, 'Gordon': 0}, eliminator='Jeff')
    assert s.is_complete()
    assert s.winner() == 'Jeff'
    assert not s.human_out
    assert s.human_rank() == 1


def test_fold_live_hand_rejects_chip_leak():
    s = _session()
    with pytest.raises(AssertionError):
        # 10000 + 15000 + 0 = 25000 != 30000
        s.fold_live_hand({'Jeff': 10000, 'Gordon': 15000, 'Bob': 0}, eliminator='Gordon')


def test_session_from_legacy_tracker_preserves_history():
    """Cold-load migration: a legacy TournamentTracker blob + live stacks rebuild
    an equivalent session (active stacks + elimination history)."""
    from flask_app.handlers.single_table_tournament import session_from_legacy_tracker

    class P:
        def __init__(self, name, stack, is_human=False):
            self.name, self.stack, self.is_human = name, stack, is_human

    tracker_data = {
        'starting_players': [
            {'name': 'Jeff', 'is_human': True},
            {'name': 'Gordon', 'is_human': False},
            {'name': 'Bob', 'is_human': False},
        ],
        'eliminations': [
            {'eliminated_player': 'Bob', 'eliminator': 'Gordon',
             'finishing_position': 3, 'hand_number': 4},
        ],
        'hand_count': 7,
    }
    # Live table mid-game: Bob busted (0), Jeff + Gordon active, total conserved.
    players = (P('Jeff', 12000, True), P('Gordon', 18000), P('Bob', 0))
    s = session_from_legacy_tracker(tracker_data, players)

    assert s is not None
    assert not s.is_complete() and not s.human_out
    assert s.field.stacks == {'Jeff': 12000, 'Gordon': 18000}
    assert [(e.player_id, e.finishing_position, e.eliminator) for e in s.field.eliminations] == [
        ('Bob', 3, 'Gordon')
    ]
    s.field.assert_conservation()


def test_session_from_legacy_tracker_bails_on_unconserved():
    from flask_app.handlers.single_table_tournament import session_from_legacy_tracker

    class P:
        def __init__(self, name, stack, is_human=False):
            self.name, self.stack, self.is_human = name, stack, is_human

    tracker_data = {'starting_players': [{'name': 'A', 'is_human': True}, {'name': 'B'}]}
    # Total 9001 not divisible by 2 -> can't cleanly reconstruct -> None.
    assert session_from_legacy_tracker(tracker_data, (P('A', 9001, True), P('B', 0))) is None


def test_build_session_for_new_game_uses_buyin_not_live_stacks():
    """Blinds are posted by the time the session is built, so the buy-in must be
    passed explicitly — not read off a (reduced) live stack."""
    from flask_app.handlers.single_table_tournament import build_session_for_new_game

    class P:
        def __init__(self, name, stack, is_human):
            self.name, self.stack, self.is_human = name, stack, is_human

    # Bob posted the big blind -> his live stack is below the buy-in.
    players = (P('Jeff', 10000, True), P('Gordon', 9950, False), P('Bob', 9900, False))
    s = build_session_for_new_game(players, starting_stack=10000, seed=7)
    assert s.field.field_size == 3
    assert all(v == 10000 for v in s.field.stacks.values())  # genesis = buy-in
    assert s.human_id == 'Jeff'
    s.field.assert_conservation()
