"""Pure beat builder: RoundReports -> typed activity beats for the ticker/feed."""

from tournament.beats import build_beats, level_transition_beats, level_up_beat
from tournament.blinds import BlindLevel, BlindSchedule
from tournament.director import RoundReport
from tournament.field import Elimination


def _level(n=1, sb=50, bb=100, ante=0):
    return BlindLevel(level=n, small_blind=sb, big_blind=bb, ante=ante)


def _report(round_index, *, eliminations=(), seat_moves=(), broken_tables=(), level=None):
    return RoundReport(
        round_index=round_index,
        level=level or _level(),
        eliminations=tuple(eliminations),
        seat_moves=tuple(seat_moves),
        broken_tables=tuple(broken_tables),
    )


def _elim(pid, pos, *, eliminator=None, rnd=0):
    return Elimination(player_id=pid, finishing_position=pos, round_index=rnd, eliminator=eliminator)


def test_no_reports_no_beats():
    assert build_beats([], paid_places=2, table_size=6, human_id='H', remaining_before=6) == []


def test_quiet_round_no_beats():
    r = _report(0)  # nobody busted, no breaks
    assert build_beats([r], paid_places=2, table_size=6, human_id='H', remaining_before=6) == []


def test_knockout_beat_with_eliminator_and_human_flag():
    r = _report(3, eliminations=[_elim('P9', 9, eliminator='P2'), _elim('H', 8)])
    beats = build_beats([r], paid_places=2, table_size=9, human_id='H', remaining_before=10)
    kos = [b for b in beats if b['type'] == 'knockout']
    assert kos[0] == {
        'type': 'knockout', 'round': 3, 'player_id': 'P9',
        'finishing_position': 9, 'eliminator': 'P2', 'is_human': False,
    }
    assert kos[1]['player_id'] == 'H' and kos[1]['is_human'] is True


def test_table_break_beat():
    r = _report(2, broken_tables=[4, 7])
    beats = build_beats([r], paid_places=2, table_size=6, human_id='H', remaining_before=8)
    breaks = [b for b in beats if b['type'] == 'table_break']
    assert {b['table_id'] for b in breaks} == {4, 7}


def test_bubble_beat_fires_on_the_bubble_boy():
    # paid_places=3 -> the 4th-place finisher is the bubble boy.
    r = _report(5, eliminations=[_elim('P4', 4, eliminator='P1')])
    beats = build_beats([r], paid_places=3, table_size=9, human_id='H', remaining_before=4)
    bubble = [b for b in beats if b['type'] == 'bubble']
    assert len(bubble) == 1
    assert bubble[0]['player_id'] == 'P4' and bubble[0]['paid_places'] == 3


def test_no_bubble_beat_away_from_the_bubble():
    r = _report(5, eliminations=[_elim('P5', 5)])
    beats = build_beats([r], paid_places=3, table_size=9, human_id='H', remaining_before=6)
    assert not any(b['type'] == 'bubble' for b in beats)


def test_final_table_milestone():
    # 10 -> 9 with a 9-handed table_size = final table forms.
    r = _report(7, eliminations=[_elim('P10', 10)])
    beats = build_beats([r], paid_places=2, table_size=9, human_id='H', remaining_before=10)
    miles = [b for b in beats if b['type'] == 'milestone']
    assert miles and miles[0]['kind'] == 'final_table' and miles[0]['remaining'] == 9


def test_heads_up_and_three_handed_milestones_in_one_big_round():
    # 5 players bust down to 1 winner: crosses 3 and 2 on the way.
    elims = [_elim(f'P{p}', p) for p in (5, 4, 3, 2)]
    r = _report(20, eliminations=elims)
    beats = build_beats([r], paid_places=2, table_size=6, human_id='H', remaining_before=5)
    kinds = [b['kind'] for b in beats if b['type'] == 'milestone']
    # Highest threshold first; table_size(6) not crossed (started at 5).
    assert kinds == ['down_to', 'heads_up']  # 3-handed, then heads-up


def test_final_table_milestone_when_table_size_is_three():
    # 4 -> 3 with a 3-max table_size is the final table forming, not "down to 3".
    r = _report(8, eliminations=[_elim('P4', 4)])
    beats = build_beats([r], paid_places=2, table_size=3, human_id='H', remaining_before=4)
    miles = [b for b in beats if b['type'] == 'milestone' and b['remaining'] == 3]
    assert miles and miles[0]['kind'] == 'final_table'


def test_level_up_beat_shape():
    beat = level_up_beat(_level(4, 200, 400, 50), round_index=12)
    assert beat == {
        'type': 'level_up', 'round': 12,
        'level': 4, 'small_blind': 200, 'big_blind': 400, 'ante': 50,
    }


def _schedule(rounds_per_level=2):
    # Three levels: 50/100, 100/200, 200/400.
    return BlindSchedule(
        levels=(_level(1, 50, 100), _level(2, 100, 200), _level(3, 200, 400)),
        rounds_per_level=rounds_per_level,
    )


def test_level_transition_announces_on_the_raise_hand():
    # rounds_per_level=2: round 2 is the first hand of level 2. At the boundary
    # after playing round 1 (level 1), rounds advances to 2 → announce.
    sch = _schedule(2)
    beats = level_transition_beats(sch, prev_level=1, rounds=2, round_index=1)
    assert len(beats) == 1
    assert beats[0]['type'] == 'level_up' and beats[0]['level'] == 2


def test_level_transition_pre_announces_one_hand_early():
    # At the boundary after playing round 0 (level 1), rounds advances to 1 — the
    # next hand (round 1) is the LAST at level 1, so pre-announce the level-2 bump.
    sch = _schedule(2)
    beats = level_transition_beats(sch, prev_level=1, rounds=1, round_index=0)
    assert len(beats) == 1
    assert beats[0]['type'] == 'level_up_next' and beats[0]['level'] == 2
    assert (beats[0]['small_blind'], beats[0]['big_blind']) == (100, 200)


def test_level_transition_silent_mid_level():
    # rounds_per_level=3: after round 0, rounds=1; next hand (1) is NOT the last
    # at level 1 (round 2 still level 1), so neither beat fires.
    sch = _schedule(3)
    assert level_transition_beats(sch, prev_level=1, rounds=1, round_index=0) == []


def test_level_transition_pre_announce_then_announce_are_consecutive():
    # The same crossing: pre-announce at the boundary into the last level-1 hand,
    # then announce at the next boundary into the first level-2 hand.
    sch = _schedule(2)
    pre = level_transition_beats(sch, prev_level=1, rounds=1, round_index=0)
    ann = level_transition_beats(sch, prev_level=1, rounds=2, round_index=1)
    assert pre[0]['type'] == 'level_up_next'
    assert ann[0]['type'] == 'level_up'
    assert pre[0]['level'] == ann[0]['level'] == 2  # both name the level it bumps to


def test_level_transition_silent_at_top_level():
    sch = _schedule(2)
    # Deep into the top level (level 3): no bump ahead, look-ahead clamps.
    assert level_transition_beats(sch, prev_level=3, rounds=10, round_index=9) == []


def test_beats_span_multiple_reports_chronologically():
    r0 = _report(0, eliminations=[_elim('P8', 8)])
    r1 = _report(1, broken_tables=[3])
    beats = build_beats([r0, r1], paid_places=2, table_size=6, human_id='H', remaining_before=8)
    rounds = [b['round'] for b in beats]
    assert rounds == sorted(rounds)  # oldest-first within the burst
    assert beats[0]['type'] == 'knockout' and beats[-1]['type'] == 'table_break'
