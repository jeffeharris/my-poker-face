#!/usr/bin/env python3
"""Unit tests for the chart-graded preflop leak finder (pure core — no DB,
no chart load; the reference resolver is injected)."""

from flask_app.services.coach_chart_leaks import (
    ChartLeak,
    bucket_action,
    compute_chart_leaks,
    format_chart_leaks_for_prompt,
)


def _decisions(hand, position, scenario, action, n, *, eff_bb=100, nplayers=6, opener=None):
    return [
        {
            'hand': hand,
            'position': position,
            'scenario': scenario,
            'opener': opener,
            'effective_stack_bb': eff_bb,
            'num_players': nplayers,
            'action': action,
        }
        for _ in range(n)
    ]


def _ref(table):
    """Build a resolver that returns a fixed bucketed freq per hand, else None."""

    def resolve(hand, position, scenario, opener, eff_bb, num_players):
        return table.get(hand)

    return resolve


class TestBucketAction:
    def test_fold_call_raise(self):
        assert bucket_action('fold') == 'fold'
        assert bucket_action('call') == 'call'
        assert bucket_action('raise') == 'raise'

    def test_check_is_call(self):
        # SB completing / limping is a voluntary continue.
        assert bucket_action('check') == 'call'

    def test_aggressive_variants_are_raise(self):
        for a in ('bet', 'jam', 'all_in', 'all-in', 'shove', 'reraise'):
            assert bucket_action(a) == 'raise'

    def test_unknown_and_empty(self):
        assert bucket_action('') is None
        assert bucket_action(None) is None
        assert bucket_action('mystery') is None


class TestClassification:
    def test_too_loose(self):
        # Chart folds 72o 90%; you play it every time.
        ref = _ref({'72o': {'fold': 0.9, 'call': 0.05, 'raise': 0.05}})
        rep = compute_chart_leaks(_decisions('72o', 'UTG', 'rfi', 'call', 6), ref)
        assert len(rep.leaks) == 1
        assert rep.leaks[0].kind == 'too_loose'
        assert rep.leaks[0].hand == '72o'

    def test_over_fold(self):
        # Chart continues KJs 90% (fold 10%); you fold it every time.
        ref = _ref({'KJs': {'fold': 0.1, 'call': 0.2, 'raise': 0.7}})
        rep = compute_chart_leaks(_decisions('KJs', 'BB', 'vs_open', 'fold', 6), ref)
        assert len(rep.leaks) == 1
        assert rep.leaks[0].kind == 'over_fold'

    def test_too_passive(self):
        # Chart 3-bets AQs 80%; you flat-call every time.
        ref = _ref({'AQs': {'fold': 0.05, 'call': 0.15, 'raise': 0.8}})
        rep = compute_chart_leaks(_decisions('AQs', 'CO', 'vs_open', 'call', 6), ref)
        assert len(rep.leaks) == 1
        assert rep.leaks[0].kind == 'too_passive'

    def test_on_chart_play_is_clean(self):
        # Chart folds 72o 90%; you fold it — no leak.
        ref = _ref({'72o': {'fold': 0.9, 'call': 0.05, 'raise': 0.05}})
        rep = compute_chart_leaks(_decisions('72o', 'UTG', 'rfi', 'fold', 6), ref)
        assert rep.leaks == []

    def test_small_deviation_is_not_a_leak(self):
        # Chart calls 60% / raises 40%; you call every time. VPIP matches
        # (you continue), and neither fold-gap clears the threshold.
        ref = _ref({'A5s': {'fold': 0.0, 'call': 0.6, 'raise': 0.4}})
        rep = compute_chart_leaks(_decisions('A5s', 'BTN', 'vs_open', 'call', 6), ref)
        # over_fold gap = 0 - 0 = 0; too_passive needs raise>=.55. Clean.
        assert rep.leaks == []


class TestLimpAndAggregate:
    def test_open_limp_is_flagged(self):
        # Open spot, chart raises-or-folds a playable hand (fold 50% / raise
        # 50%, never calls), but you flat-call → limp leak.
        ref = _ref({'A9o': {'fold': 0.5, 'call': 0.0, 'raise': 0.5}})
        rep = compute_chart_leaks(_decisions('A9o', 'SB', 'rfi', 'call', 6), ref)
        assert len(rep.leaks) == 1
        assert rep.leaks[0].kind == 'limp'

    def test_limping_trash_is_too_loose_not_limp(self):
        # Chart folds it (trash) — the error is playing at all, not how.
        ref = _ref({'72o': {'fold': 0.9, 'call': 0.0, 'raise': 0.1}})
        rep = compute_chart_leaks(_decisions('72o', 'SB', 'rfi', 'call', 6), ref)
        assert rep.leaks[0].kind == 'too_loose'

    def test_position_aggregate_groups_across_hands(self):
        # Different hands, same (scenario, position): exact-hand grouping would
        # see all n=1 (nothing); position grouping aggregates to a real read.
        def resolve(hand, position, scenario, opener, eff_bb, num_players):
            return {'fold': 0.5, 'call': 0.0, 'raise': 0.5}  # raise-or-fold

        decisions = (
            _decisions('A9o', 'SB', 'rfi', 'call', 1)
            + _decisions('K8o', 'SB', 'rfi', 'call', 1)
            + _decisions('Q7o', 'SB', 'rfi', 'call', 1)
            + _decisions('J6o', 'SB', 'rfi', 'call', 1)
            + _decisions('T6o', 'SB', 'rfi', 'call', 1)
        )
        by_hand = compute_chart_leaks(decisions, resolve, group_by='hand')
        assert by_hand.leaks == []  # every hand n=1, below gate
        by_pos = compute_chart_leaks(decisions, resolve, group_by='position')
        assert len(by_pos.leaks) == 1
        assert by_pos.leaks[0].kind == 'limp'
        assert by_pos.leaks[0].hand == ''  # an aggregate, not one hand
        assert by_pos.leaks[0].n == 5


class TestGatesAndTiers:
    def test_min_sample_gate(self):
        ref = _ref({'72o': {'fold': 0.9, 'call': 0.05, 'raise': 0.05}})
        rep = compute_chart_leaks(_decisions('72o', 'UTG', 'rfi', 'call', 1), ref)
        assert rep.leaks == []  # single play isn't a pattern

    def test_watching_vs_confirmed(self):
        ref = _ref({'72o': {'fold': 0.9, 'call': 0.05, 'raise': 0.05}})
        small = compute_chart_leaks(_decisions('72o', 'UTG', 'rfi', 'call', 3), ref)
        assert small.leaks[0].status == 'watching'
        big = compute_chart_leaks(_decisions('72o', 'UTG', 'rfi', 'call', 8), ref)
        assert big.leaks[0].status == 'confirmed'

    def test_ranked_worst_first(self):
        ref = _ref({
            '72o': {'fold': 0.95, 'call': 0.03, 'raise': 0.02},
            'J5o': {'fold': 0.95, 'call': 0.03, 'raise': 0.02},
        })
        decisions = (
            _decisions('72o', 'UTG', 'rfi', 'call', 8)
            + _decisions('J5o', 'CO', 'rfi', 'call', 4)
        )
        rep = compute_chart_leaks(decisions, ref)
        assert [lk.hand for lk in rep.leaks] == ['72o', 'J5o']  # higher severity first


class TestCoverageAndSkips:
    def test_short_multiway_is_skipped(self):
        ref = _ref({'72o': {'fold': 0.9, 'call': 0.05, 'raise': 0.05}})
        rep = compute_chart_leaks(
            _decisions('72o', 'UTG', 'rfi', 'call', 6, eff_bb=10, nplayers=5), ref
        )
        assert rep.graded == 0
        assert rep.skipped.get('short_multiway') == 6
        assert rep.leaks == []

    def test_short_headsup_is_not_skipped(self):
        # HU short stacks are in scope (push/fold), so they're graded here.
        ref = _ref({'72o': {'fold': 0.9, 'call': 0.05, 'raise': 0.05}})
        rep = compute_chart_leaks(
            _decisions('72o', 'SB', 'rfi', 'call', 6, eff_bb=10, nplayers=2), ref
        )
        assert rep.graded == 6

    def test_no_reference_is_skipped(self):
        ref = _ref({})  # resolver returns None for everything
        rep = compute_chart_leaks(_decisions('72o', 'UTG', 'rfi', 'call', 6), ref)
        assert rep.graded == 0
        assert rep.skipped.get('no_reference') == 6

    def test_unparsed_action_is_skipped(self):
        ref = _ref({'72o': {'fold': 0.9, 'call': 0.05, 'raise': 0.05}})
        decisions = _decisions('72o', 'UTG', 'rfi', 'mystery', 4)
        rep = compute_chart_leaks(decisions, ref)
        assert rep.skipped.get('unparsed') == 4


class TestRecencySliceAndDiff:
    def _dec(self, hand, action, n, *, scenario='rfi', position='SB', created=1, eff_bb=100, nplayers=6):
        return [
            {
                'hand': hand, 'position': position, 'scenario': scenario, 'opener': None,
                'effective_stack_bb': eff_bb, 'num_players': nplayers, 'action': action,
                'created_at': f'2026-01-{created:02d} 00:00:00', 'hand_number': i,
            }
            for i in range(n)
        ]

    def test_recent_slice_takes_newest_by_time(self):
        from flask_app.services.coach_chart_leaks import recent_slice

        old = self._dec('72o', 'fold', 3, created=1)
        new = self._dec('AA', 'raise', 2, created=9)
        got = recent_slice(old + new, n_hands=2)
        assert len(got) == 2
        assert all(d['hand'] == 'AA' for d in got)  # newest by created_at

    def test_shrinking_when_recent_gap_smaller(self):
        from flask_app.services.coach_chart_leaks import compute_slice_diff

        ref = lambda *a: {'fold': 0.9, 'call': 0.05, 'raise': 0.05}  # chart folds 72o
        # all-time: plays 72o constantly (gap ~0.7). recent: still a leak but
        # smaller (plays half → gap ~0.4) → shrinking, not cleared.
        all_d = self._dec('72o', 'call', 12, created=1)
        recent = self._dec('72o', 'call', 4, created=9) + self._dec('72o', 'fold', 4, created=9)
        trends, _ = compute_slice_diff(all_d + recent, recent, ref)
        assert trends[('rfi', 'SB')]['trend'] == 'shrinking'

    def test_insufficient_when_no_recent_volume(self):
        from flask_app.services.coach_chart_leaks import compute_slice_diff

        ref = lambda *a: {'fold': 0.9, 'call': 0.05, 'raise': 0.05}
        all_d = self._dec('72o', 'call', 10, created=1)
        recent = self._dec('72o', 'call', 2, created=9)  # < min_sample (5)
        trends, _ = compute_slice_diff(all_d + recent, recent, ref)
        assert trends[('rfi', 'SB')]['trend'] == 'insufficient'

    def test_emerging_recent_only_leak(self):
        from flask_app.services.coach_chart_leaks import compute_slice_diff

        ref = lambda *a: {'fold': 0.9, 'call': 0.05, 'raise': 0.05}
        # Lots of clean old folds dilute all-time below threshold; the recent
        # slice plays it (a new leak that all-time doesn't yet show).
        all_d = self._dec('72o', 'fold', 50, created=1)
        recent = self._dec('72o', 'call', 6, created=9)
        trends, emerging = compute_slice_diff(all_d + recent, recent, ref)
        assert ('rfi', 'SB') not in trends  # not an all-time leak
        assert len(emerging) == 1 and emerging[0]['position'] == 'SB'


class TestTrendAndDepth:
    def _dec(self, action, n, *, hand='72o', position='SB', eff_bb=100, start=0):
        return [
            {
                'hand': hand, 'position': position, 'scenario': 'rfi', 'opener': None,
                'effective_stack_bb': eff_bb, 'num_players': 6, 'action': action,
                'created_at': '2026-01-01 00:00:00', 'hand_number': start + i,
            }
            for i in range(n)
        ]

    def test_trend_series_per_block(self):
        from flask_app.services.coach_chart_leaks import compute_leak_trend

        ref = lambda *a: {'fold': 0.9, 'call': 0.05, 'raise': 0.05}  # chart folds 72o
        d = self._dec('call', 60)  # always limps it → a leak in every block
        tr = compute_leak_trend(d, ref, blocks=6)
        series = tr[('rfi', 'SB')]
        assert len(series) == 6
        assert all(s is not None and s > 0.5 for s in series)  # persistent leak

    def test_trend_thin_block_is_none(self):
        from flask_app.services.coach_chart_leaks import compute_leak_trend

        ref = lambda *a: {'fold': 0.9, 'call': 0.05, 'raise': 0.05}
        d = self._dec('call', 6)  # 6 hands / 6 blocks = 1 each, below min_sample
        tr = compute_leak_trend(d, ref, blocks=6)
        # No eligible block → no series for the spot.
        assert ('rfi', 'SB') not in tr

    def test_depth_slice_bands(self):
        from flask_app.services.coach_chart_leaks import depth_slice

        deep = self._dec('call', 3, eff_bb=100)
        short = self._dec('call', 3, eff_bb=10)
        pool = deep + short
        assert len(depth_slice(pool, 'deep')) == 3
        assert len(depth_slice(pool, 'short')) == 3
        assert len(depth_slice(pool, 'all')) == 6


class TestPromptText:
    def test_empty_when_nothing_graded(self):
        rep = compute_chart_leaks([], _ref({}))
        assert 'No chart-gradeable' in format_chart_leaks_for_prompt(rep)

    def test_describes_leak_and_tier(self):
        ref = _ref({'KJs': {'fold': 0.1, 'call': 0.2, 'raise': 0.7}})
        rep = compute_chart_leaks(_decisions('KJs', 'BB', 'vs_open', 'fold', 8), ref)
        txt = format_chart_leaks_for_prompt(rep)
        assert 'CHART PROFILE' in txt
        assert 'KJs' in txt
        assert 'facing a raise' in txt
        assert 'CONFIRMED LEAKS' in txt

    def test_clean_profile_when_enough_volume(self):
        # Eligible group, no leak → honest "tracks the charts" (not "discipline"
        # claimed on no data).
        ref = _ref({'AKs': {'fold': 0.0, 'call': 0.1, 'raise': 0.9}})
        rep = compute_chart_leaks(_decisions('AKs', 'BTN', 'rfi', 'raise', 6), ref)
        assert rep.eligible_groups >= 1
        assert 'tracks the charts' in format_chart_leaks_for_prompt(rep)

    def test_not_enough_volume_does_not_claim_clean(self):
        # All singletons → no eligible group → must NOT claim discipline.
        ref = _ref({'AKs': {'fold': 0.0, 'call': 0.1, 'raise': 0.9}})
        decisions = _decisions('AKs', 'BTN', 'rfi', 'raise', 1)
        rep = compute_chart_leaks(decisions, ref)
        assert rep.eligible_groups == 0
        txt = format_chart_leaks_for_prompt(rep)
        assert 'Not enough repeated spots' in txt
        assert 'tracks the charts' not in txt


class TestEffectivenessBaseline:
    def test_followed_solver_line_rules(self):
        from flask_app.services.coach_chart_leaks import followed_solver_line as f
        assert f('limp', 'raise') and f('limp', 'fold') and not f('limp', 'call')
        assert f('too_loose', 'fold') and not f('too_loose', 'call')
        assert f('over_fold', 'call') and not f('over_fold', 'fold')
        assert f('too_passive', 'raise') and not f('too_passive', 'call')

    def test_baseline_only_counts_leak_spots(self):
        from flask_app.services.coach_chart_leaks import compute_baseline_follow_rates
        decs = (
            [{'scenario': 'rfi', 'position': 'SB', 'action': 'call'} for _ in range(4)]
            + [{'scenario': 'rfi', 'position': 'SB', 'action': 'raise'} for _ in range(6)]
            + [{'scenario': 'rfi', 'position': 'BTN', 'action': 'call'} for _ in range(5)]  # not a leak spot
        )
        ls = {'by_spot': {('rfi', 'SB'): {'kind': 'limp'}}}
        base = compute_baseline_follow_rates(decs, ls)
        assert base['by_kind']['limp']['n'] == 10  # BTN excluded
        assert base['by_kind']['limp']['followed'] == 6  # the 6 raises
        assert base['overall']['rate'] == 0.6

    def test_merge_computes_lift(self):
        from flask_app.services.coach_chart_leaks import merge_effectiveness
        nudged = {
            'by_kind': {'limp': {'nudges': 5, 'followed': 4, 'follow_rate': 0.8}},
            'overall': {'nudges': 5, 'followed': 4, 'follow_rate': 0.8},
        }
        baseline = {
            'by_kind': {'limp': {'n': 10, 'followed': 6, 'rate': 0.6}},
            'overall': {'n': 10, 'followed': 6, 'rate': 0.6},
        }
        m = merge_effectiveness(nudged, baseline)
        assert m['by_kind']['limp']['nudged'] == {'n': 5, 'rate': 0.8}
        assert m['by_kind']['limp']['baseline'] == {'n': 10, 'rate': 0.6}
        assert m['by_kind']['limp']['lift'] == 0.2
        assert m['overall']['lift'] == 0.2

    def test_merge_lift_none_when_missing(self):
        from flask_app.services.coach_chart_leaks import merge_effectiveness
        nudged = {'by_kind': {}, 'overall': {'nudges': 0, 'follow_rate': None}}
        baseline = {'by_kind': {}, 'overall': {'n': 0, 'rate': None}}
        assert merge_effectiveness(nudged, baseline)['overall']['lift'] is None
