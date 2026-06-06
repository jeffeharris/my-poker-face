"""Standings read-model: leaders, ITM/bubble payout, and next-blinds — all pure
reads derived from the session field (the "cheap bundle")."""

from tournament.config import TournamentConfig
from tournament.director import FakeHandResolver
from tournament.field import Elimination
from tournament.session import TournamentSession, paid_places_for


def _session(field_size=18, table_size=6, seed=2):
    cfg = TournamentConfig(
        field_size=field_size, table_size=table_size, starting_stack=10000, seed=seed
    )
    # P01 is the human seat — F1 made the no-human_id default None.
    return TournamentSession(cfg, ai_resolver=FakeHandResolver(), human_id='P01')


def test_paid_places_matches_payout_structure():
    # Delegates to the economy's actual 0.30 payout structure (was a separate
    # display-only 0.15 ITM fraction, so the bubble fired at the wrong place).
    assert paid_places_for(18) == 5  # round(5.4)
    assert paid_places_for(45) == 14  # round(13.5)
    assert paid_places_for(6) == 2  # round(1.8)
    assert paid_places_for(2) == 1  # at least the winner is always paid


def test_leaderboard_orders_by_stack_and_flags_human():
    s = _session()
    s.field.stacks['P03'] = 25000
    s.field.stacks['P01'] = 5000  # human
    board = s.leaderboard(top=3)
    assert [b['player_id'] for b in board][:1] == ['P03']
    assert board[0]['rank'] == 1 and board[0]['stack'] == 25000
    assert len(board) == 3
    human_rows = [b for b in board if b['is_human']]
    # human at 5000 isn't top-3 here, so may be absent — but flag is correct when present
    assert all(b['is_human'] == (b['player_id'] == 'P01') for b in board)
    assert human_rows == []  # 5000 is below the top 3


def test_payout_bubble_and_in_money_transitions():
    s = _session(field_size=18)  # paid_places = 5 (round(18*0.30))
    assert s.payout_view() == {
        'paid_places': 5,
        'players_to_money': 13,
        'on_bubble': False,
        'in_money': False,
    }
    # Bust down to 6 remaining -> on the bubble (paid_places + 1).
    for pid in [f'P{i:02d}' for i in range(7, 19)]:  # bust 12, leaving P01..P06
        s.field.record_eliminations([(pid, s.field.stacks[pid])], 0, {})
    assert s.field.active_count == 6
    pv = s.payout_view()
    assert pv['on_bubble'] is True and pv['players_to_money'] == 1 and pv['in_money'] is False
    # One more bust -> bubble bursts, everyone left is ITM.
    s.field.record_eliminations([('P06', s.field.stacks['P06'])], 0, {})
    pv = s.payout_view()
    assert pv['in_money'] is True and pv['players_to_money'] == 0 and pv['on_bubble'] is False


def test_human_in_money_when_field_collapses_to_paid():
    s = _session(field_size=6)  # paid_places = 2 (round(6*0.30))
    # bust four non-humans -> 2 remain (incl human) -> human ITM
    for pid in ('P06', 'P05', 'P04', 'P03'):
        s.field.record_eliminations([(pid, s.field.stacks[pid])], 0, {})
    sv = s.standings_view()
    assert sv['payout']['in_money'] is True
    assert sv['human']['in_money'] is True


def test_human_in_money_reflects_cashed_finish_when_out():
    s = _session(field_size=6)  # paid_places = 2
    # Human busts in 2nd (a paid place) -> cashed even though out.
    s.field.eliminations.append(
        Elimination(player_id=s.human_id, finishing_position=2, round_index=0)
    )
    s.field.stacks.pop(s.human_id, None)
    assert s.human_out
    assert s._human_in_money(2) is True
    # ...but a 3rd-place bust in a 2-paid field did NOT cash.
    s2 = _session(field_size=6)
    s2.field.eliminations.append(
        Elimination(player_id=s2.human_id, finishing_position=3, round_index=0)
    )
    s2.field.stacks.pop(s2.human_id, None)
    assert s2._human_in_money(2) is False  # finished 3rd, only 2 paid


def test_next_level_counts_down_and_caps_at_top():
    s = _session()
    nl = s.next_level_view()
    assert nl['level'] == 2 and nl['hands_until'] == s.schedule.rounds_per_level
    # Advance to the final level -> next_level is None.
    s.rounds = s.schedule.rounds_per_level * (len(s.schedule.levels) - 1)
    assert s.next_level_view() is None


def test_standings_view_includes_new_keys():
    sv = _session().standings_view()
    for key in ('leaders', 'payout', 'next_level'):
        assert key in sv
    assert 'in_money' in sv['human']


def test_autonomous_session_has_no_human():
    """T3-80 F1: an AI-only tournament carries human_id=None (no nominated AI as
    'the human'). The read model must degrade gracefully — human_out is trivially
    True, no seat is is_human, and the standings 'human' block is all-null."""
    cfg = TournamentConfig(field_size=18, table_size=6, starting_stack=10000, seed=2)
    s = TournamentSession(cfg, ai_resolver=FakeHandResolver(), human_id=None)

    assert s.human_id is None
    assert s.human_out is True
    assert s.human_table is None
    assert s.human_rank() is None
    # No field seat is flagged as the human.
    assert all(not b['is_human'] for b in s.leaderboard(top=cfg.field_size))

    sv = s.standings_view()
    assert sv['human']['player_id'] is None
    assert sv['human']['out'] is True
    assert sv['human']['in_money'] is False
    assert all(not seat['is_human'] for t in sv['tables'] for seat in t['seats'])

    # And it actually plays: advance_round runs the AI field with no human.
    s.advance_round()
    assert s.rounds == 1
