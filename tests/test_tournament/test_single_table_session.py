"""Single-table TournamentSession: the unification of the legacy single-table
game (step 3B). The session is a passive field/standings/completion observer fed
by `fold_live_hand`; the live engine owns play + blinds."""

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


def test_fold_live_hand_reconciles_chip_leak_without_raising(caplog):
    """The live engine is the chip authority for the human's single table, so a
    chip-conservation mismatch must NOT raise — a raised guard 500s out of
    `progress_game` and every retry re-enters the same boundary, permanently
    bricking the end screen (observed on an all-in run-out bust). Instead it
    reconciles the field to the live stacks and warns."""
    s = _session()
    # 10000 + 15000 + 0 = 25000 != 30000 — a 5000-chip leak.
    with caplog.at_level('WARNING', logger='tournament.session'):
        events = s.fold_live_hand({'Jeff': 10000, 'Gordon': 15000, 'Bob': 0}, eliminator='Gordon')
    # Completion bookkeeping still works: Bob busts, the field reconciles to live.
    assert [e.player_id for e in events] == ['Bob']
    assert 'Bob' not in s.field.stacks
    assert s.field.stacks == {'Jeff': 10000, 'Gordon': 15000}
    assert any('conservation' in r.message for r in caplog.records)


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
