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
    return TournamentSession(cfg, ai_resolver=FakeHandResolver())


def test_paid_places_scales_with_field_min_two():
    assert paid_places_for(18) == 3  # round(2.7)
    assert paid_places_for(45) == 7  # round(6.75)
    assert paid_places_for(2) == 2  # floor of min 2
    assert paid_places_for(3) == 2  # round(0.45)=0 -> min 2


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
    s = _session(field_size=18)  # paid_places = 3
    assert s.payout_view() == {
        'paid_places': 3, 'players_to_money': 15, 'on_bubble': False, 'in_money': False
    }
    # Bust down to 4 remaining -> on the bubble.
    for pid in [f'P{i:02d}' for i in range(5, 19)]:  # bust 14, leaving P01..P04
        s.field.record_eliminations([(pid, s.field.stacks[pid])], 0, {})
    assert s.field.active_count == 4
    pv = s.payout_view()
    assert pv['on_bubble'] is True and pv['players_to_money'] == 1 and pv['in_money'] is False
    # One more bust -> bubble bursts, everyone left is ITM.
    s.field.record_eliminations([('P04', s.field.stacks['P04'])], 0, {})
    pv = s.payout_view()
    assert pv['in_money'] is True and pv['players_to_money'] == 0 and pv['on_bubble'] is False


def test_human_in_money_when_field_collapses_to_paid():
    s = _session(field_size=4)  # paid_places = 2
    # bust two non-humans -> 2 remain (incl human) -> human ITM
    s.field.record_eliminations([('P04', s.field.stacks['P04'])], 0, {})
    s.field.record_eliminations([('P03', s.field.stacks['P03'])], 0, {})
    sv = s.standings_view()
    assert sv['payout']['in_money'] is True
    assert sv['human']['in_money'] is True


def test_human_in_money_reflects_cashed_finish_when_out():
    s = _session(field_size=4)  # paid_places = 2
    # Human busts in 2nd (a paid place) -> cashed even though out.
    s.field.eliminations.append(
        Elimination(player_id=s.human_id, finishing_position=2, round_index=0)
    )
    s.field.stacks.pop(s.human_id, None)
    assert s.human_out
    assert s._human_in_money(2) is True
    # ...but a 3rd-place bust in a 2-paid field did NOT cash.
    s2 = _session(field_size=4)
    s2.field.eliminations.append(
        Elimination(player_id=s2.human_id, finishing_position=3, round_index=0)
    )
    s2.field.stacks.pop(s2.human_id, None)
    assert s2._human_in_money(2) is False


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
