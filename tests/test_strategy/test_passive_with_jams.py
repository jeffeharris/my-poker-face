"""Tests for the Phase 8.1b passive-with-jams pattern (detector only).

The behavior change Phase 8.1b originally shipped (suppress
hyper_passive's fold-mass reduction when the aggregate matched
passive-with-jams) empirically REGRESSED bb/100 across a 5-seed
6-max sim. Stations including jam-prone ones don't bluff much, so
the original "call more marginals" behavior is correct even when
they occasionally jam. The behavior change was reverted; the
detector stays for diagnostic visibility only.

Behavior under test:
  Pattern detection
    - _is_passive_with_jams: hyper_passive AND all_in_frequency above
      threshold. Pure stations (all_in=0) are NOT included.
    - classify_detected_patterns surfaces 'passive_with_jams' as a
      detected sub-pattern alongside 'hyper_passive'.

  Offset behavior (no longer changes based on passive_with_jams)
    - compute_exploitation_offsets hyper_passive branch emits the
      same raise-push AND fold-mass reduction regardless of whether
      the aggregate matches passive_with_jams. Trace context still
      records the flag for offline analysis.
"""

import pytest

from poker.strategy.exploitation import (
    AggregatedOpponentStats,
    DecisionContext,
    MIN_HANDS_DEFAULT,
    OpponentSpot,
    PASSIVE_WITH_JAMS_ALL_IN_THRESHOLD,
    _is_passive_with_jams,
    classify_detected_patterns,
    compute_exploitation_offsets,
)


def _stats(*, hands_observed=50, vpip=0.85, aggression_factor=0.4,
            all_in_frequency=0.0, **kwargs) -> AggregatedOpponentStats:
    return AggregatedOpponentStats(
        hands_observed=hands_observed,
        vpip=vpip,
        aggression_factor=aggression_factor,
        all_in_frequency=all_in_frequency,
        **kwargs,
    )


def _spot(name='Opp', *, stats=None, is_active=True) -> OpponentSpot:
    return OpponentSpot(
        name=name,
        stats=stats if stats is not None else _stats(),
        is_active=is_active,
    )


# ── _is_passive_with_jams ──────────────────────────────────────────────

class TestIsPassiveWithJams:
    def test_pure_station_not_matched(self):
        # all_in_frequency=0 — classic calling station; the bare
        # hyper_passive rule is safe here.
        assert _is_passive_with_jams(
            _stats(vpip=0.85, aggression_factor=0.4, all_in_frequency=0.0)
        ) is False

    def test_casebot_like_matched(self):
        # CaseBot empirically sits at all_in_frequency 0.09-0.14.
        assert _is_passive_with_jams(
            _stats(vpip=0.89, aggression_factor=0.4, all_in_frequency=0.12)
        ) is True

    def test_above_threshold_matched(self):
        # Strictly above PASSIVE_WITH_JAMS_ALL_IN_THRESHOLD with
        # hyper_passive traits → matched.
        assert _is_passive_with_jams(
            _stats(all_in_frequency=PASSIVE_WITH_JAMS_ALL_IN_THRESHOLD + 0.001)
        ) is True

    def test_at_threshold_not_matched(self):
        # Strict inequality so equality doesn't match (matches existing
        # threshold semantics in this module).
        assert _is_passive_with_jams(
            _stats(all_in_frequency=PASSIVE_WITH_JAMS_ALL_IN_THRESHOLD)
        ) is False

    def test_aggressive_player_not_matched(self):
        # high AF means NOT hyper_passive — pattern shouldn't match
        # even with high all-in frequency (that's a maniac, not a
        # passive-with-jams station).
        assert _is_passive_with_jams(
            _stats(vpip=0.85, aggression_factor=3.0, all_in_frequency=0.20)
        ) is False

    def test_tight_player_not_matched(self):
        # low VPIP means NOT hyper_passive — even with jams, this is a
        # tight maniac, not a passive-with-jams station.
        assert _is_passive_with_jams(
            _stats(vpip=0.20, aggression_factor=0.4, all_in_frequency=0.15)
        ) is False


# ── classify_detected_patterns surfaces passive_with_jams ──────────────

class TestPatternClassification:
    def test_pure_station_pattern_list(self):
        # hyper_passive only; no passive_with_jams.
        patterns = classify_detected_patterns(
            _stats(vpip=0.85, aggression_factor=0.4, all_in_frequency=0.0)
        )
        assert 'hyper_passive' in patterns
        assert 'passive_with_jams' not in patterns

    def test_casebot_pattern_list(self):
        # Both patterns present — passive_with_jams is a sub-pattern.
        patterns = classify_detected_patterns(
            _stats(vpip=0.89, aggression_factor=0.4, all_in_frequency=0.12)
        )
        assert 'hyper_passive' in patterns
        assert 'passive_with_jams' in patterns


# ── compute_exploitation_offsets auto-suppression behavior ────────────

class TestFoldMassAutoSuppression:
    def _default_actions(self):
        return ['fold', 'call', 'bet_50', 'all_in']

    def test_pure_station_emits_full_hyper_passive(self):
        # all_in_frequency=0 → not passive_with_jams → fold-mass
        # reduction fires (legacy behavior preserved for non-jam
        # stations).
        offsets = compute_exploitation_offsets(
            stats=_stats(vpip=0.85, aggression_factor=0.4, all_in_frequency=0.0),
            adaptation_bias=0.9,
            decision_context=DecisionContext(is_preflop=False),
            available_actions=self._default_actions(),
        )
        assert offsets['bet_50'] > 0.0
        assert offsets['fold'] < 0.0

    def test_casebot_aggregate_auto_suppresses_fold_mass(self):
        # all_in_frequency above threshold → passive_with_jams → fold
        # reduction stripped, raise push preserved.
        offsets = compute_exploitation_offsets(
            stats=_stats(vpip=0.89, aggression_factor=0.4, all_in_frequency=0.12),
            adaptation_bias=0.9,
            decision_context=DecisionContext(is_preflop=False),
            available_actions=self._default_actions(),
        )
        assert offsets['bet_50'] > 0.0
        assert 'fold' not in offsets or offsets['fold'] == 0.0

    def test_threshold_boundary_no_suppression(self):
        # all_in_frequency exactly at threshold → strict inequality
        # means NOT passive_with_jams → fold reduction still fires.
        offsets = compute_exploitation_offsets(
            stats=_stats(
                vpip=0.85, aggression_factor=0.4,
                all_in_frequency=PASSIVE_WITH_JAMS_ALL_IN_THRESHOLD,
            ),
            adaptation_bias=0.9,
            decision_context=DecisionContext(is_preflop=False),
            available_actions=self._default_actions(),
        )
        assert offsets['fold'] < 0.0

    def test_tight_nit_unaffected_by_8_1b(self):
        # tight_nit emits raise_* positive in open spots regardless of
        # any hyper_passive considerations — Phase 8.1b is scoped to
        # the hyper_passive branch only.
        nit_stats = _stats(
            vpip=0.10, aggression_factor=1.5, all_in_frequency=0.0,
        )
        offsets = compute_exploitation_offsets(
            stats=nit_stats,
            adaptation_bias=0.9,
            decision_context=DecisionContext(
                is_preflop=True, facing_all_in=False, facing_big_bet=False,
            ),
            available_actions=['fold', 'call', 'raise_2.5bb'],
        )
        assert offsets.get('raise_2.5bb', 0.0) > 0.0
