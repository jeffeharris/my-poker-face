"""Phase 7.5 Step 0 tests for opportunity-normalized stats.

Covers the new postflop-only counters and derived properties on
OpponentTendencies:
  - aggression_factor_postflop (postflop-only, with raw-count cap from day one)
  - all_in_per_facing_bet (response-aggression axis)
  - postflop_jam_open_rate (open-aggression axis)

Also verifies:
  - Legacy aggression_factor formula UNCHANGED in Step 0
  - Preflop all-ins are excluded from postflop counters
  - Per-opponent isolation (counters don't leak between opponents)
  - Missing-field tolerance for old serialized records
  - The raw-count cap on aggression_factor_postflop fires at the
    MEDIUM_AF_THRESHOLD value from config

See docs/plans/PHASE_7_5_ADJUSTMENT_LAYER_WIDENING.md §Tests.
"""

import pytest

from poker.memory.opponent_model import OpponentTendencies, OpponentModel
from poker.strategy.phase_7_5_config import CONFIG


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def t() -> OpponentTendencies:
    """Fresh OpponentTendencies for each test."""
    return OpponentTendencies()


# ── Postflop counters: response axis ─────────────────────────────────────

class TestFacingBetOpportunities:
    def test_facing_bet_call_increments_opportunity_only(self, t):
        t.update_from_action('call', 'FLOP', was_facing_bet=True)
        assert t._facing_bet_opportunities == 1
        assert t._all_ins_facing_bet == 0

    def test_facing_bet_fold_increments_opportunity_only(self, t):
        t.update_from_action('fold', 'FLOP', was_facing_bet=True)
        assert t._facing_bet_opportunities == 1
        assert t._all_ins_facing_bet == 0

    def test_facing_bet_raise_increments_opportunity_only(self, t):
        t.update_from_action('raise', 'FLOP', was_facing_bet=True)
        assert t._facing_bet_opportunities == 1
        assert t._all_ins_facing_bet == 0

    def test_facing_bet_all_in_increments_both(self, t):
        t.update_from_action('all_in', 'FLOP', was_facing_bet=True)
        assert t._facing_bet_opportunities == 1
        assert t._all_ins_facing_bet == 1

    def test_derived_rate_zero_when_no_opportunities(self, t):
        assert t.all_in_per_facing_bet == 0.0

    def test_derived_rate_correct(self, t):
        t.update_from_action('all_in', 'FLOP', was_facing_bet=True)
        t.update_from_action('fold', 'FLOP', was_facing_bet=True)
        t.update_from_action('fold', 'TURN', was_facing_bet=True)
        t.update_from_action('fold', 'RIVER', was_facing_bet=True)
        # 1 jam out of 4 facing-bet opportunities
        assert t.all_in_per_facing_bet == pytest.approx(0.25)


# ── Postflop counters: open axis ─────────────────────────────────────────

class TestPostflopOpenOpportunities:
    def test_check_into_no_bet_increments_opportunity(self, t):
        t.update_from_action('check', 'FLOP', was_facing_bet=False)
        assert t._postflop_open_opportunities == 1
        assert t._postflop_jam_opens == 0

    def test_bet_into_no_bet_increments_opportunity_only(self, t):
        t.update_from_action('bet', 'FLOP', was_facing_bet=False)
        assert t._postflop_open_opportunities == 1
        assert t._postflop_jam_opens == 0

    def test_open_jam_increments_both(self, t):
        t.update_from_action('all_in', 'FLOP', was_facing_bet=False)
        assert t._postflop_open_opportunities == 1
        assert t._postflop_jam_opens == 1

    def test_check_behind_counts_as_open_opportunity(self, t):
        """Per plan: not literal 'first to act' — check-behinds count."""
        # Simulate: BB checked, BTN checks behind.
        # The BTN action is 'check' with was_facing_bet=False.
        t.update_from_action('check', 'FLOP', was_facing_bet=False)
        assert t._postflop_open_opportunities == 1

    def test_derived_rate_correct(self, t):
        # 3 opens, 1 jam → 33%
        t.update_from_action('all_in', 'FLOP', was_facing_bet=False)
        t.update_from_action('check', 'TURN', was_facing_bet=False)
        t.update_from_action('bet', 'RIVER', was_facing_bet=False)
        assert t._postflop_open_opportunities == 3
        assert t._postflop_jam_opens == 1
        assert t.postflop_jam_open_rate == pytest.approx(1 / 3)


# ── Preflop exclusion ────────────────────────────────────────────────────

class TestPreflopExclusion:
    def test_preflop_jam_does_not_increment_postflop_counters(self, t):
        # Opponent jams preflop (e.g. short-stack 3-bet jam).
        t.update_from_action(
            'all_in', 'PRE_FLOP',
            was_facing_bet=True,  # caller may pass either; phase gates the update
        )
        assert t._postflop_jam_opens == 0
        assert t._postflop_open_opportunities == 0
        assert t._all_ins_facing_bet == 0
        assert t._facing_bet_opportunities == 0
        # Legacy preflop stat still updates (all_in counts toward AF).
        assert t._all_in_count == 1

    def test_preflop_bet_does_not_increment_postflop_af(self, t):
        t.update_from_action('raise', 'PRE_FLOP', was_facing_bet=False)
        assert t._postflop_bet_raise_count == 0
        assert t._postflop_call_count == 0


# ── was_facing_bet=None semantics ────────────────────────────────────────

class TestUnknownContext:
    def test_was_facing_bet_none_skips_postflop_counters(self, t):
        """When caller can't determine context, postflop counters are
        skipped entirely (no opportunity or jam recorded)."""
        t.update_from_action('call', 'FLOP', was_facing_bet=None)
        assert t._facing_bet_opportunities == 0
        assert t._postflop_open_opportunities == 0
        assert t._postflop_bet_raise_count == 0
        assert t._postflop_call_count == 0
        # Legacy counters still update.
        assert t._call_count == 1


# ── Postflop AF + raw-count cap from day one ─────────────────────────────

class TestAggressionFactorPostflop:
    def test_zero_actions_returns_neutral(self, t):
        assert t.aggression_factor_postflop == 1.0

    def test_ratio_when_both_counters_nonzero(self, t):
        # 6 postflop bets/raises, 2 postflop calls → AF = 3.0
        for _ in range(6):
            t.update_from_action('bet', 'FLOP', was_facing_bet=False)
        for _ in range(2):
            t.update_from_action('call', 'TURN', was_facing_bet=True)
        assert t.aggression_factor_postflop == pytest.approx(3.0)

    def test_raw_count_capped_at_medium_threshold(self, t):
        """Zero postflop calls + N raises → AF capped at MEDIUM threshold.

        Phase 7.5 plan: the new postflop AF has the raw-count cap from
        day one (no legacy consumer to protect). Suppresses noisy
        extreme classification from zero-call samples.
        """
        cap = CONFIG.signal_thresholds.medium_af_postflop  # 4.0
        # 10 bets, 0 calls
        for _ in range(10):
            t.update_from_action('bet', 'FLOP', was_facing_bet=False)
        # Pre-cap raw would be 10.0; cap pulls it down to MEDIUM threshold.
        assert t.aggression_factor_postflop == cap
        assert t.aggression_factor_postflop < 10.0

    def test_raw_count_below_cap_unchanged(self, t):
        """When raw count < cap, no capping occurs."""
        cap = CONFIG.signal_thresholds.medium_af_postflop  # 4.0
        # 2 bets, 0 calls → raw 2.0, below cap.
        for _ in range(2):
            t.update_from_action('bet', 'FLOP', was_facing_bet=False)
        assert t.aggression_factor_postflop == 2.0
        assert t.aggression_factor_postflop < cap


# ── Legacy aggression_factor UNCHANGED in Step 0 ─────────────────────────

class TestLegacyAfUnchanged:
    def test_legacy_af_uses_raw_count_fallback_in_step_0(self, t):
        """Step 0 must not change the legacy formula. The cap on legacy
        aggression_factor lands with Item 2, not Step 0."""
        # 6 raises, 0 calls — preflop AND postflop. Legacy aggression_factor
        # uses raw count fallback per existing logic.
        for _ in range(6):
            t.update_from_action('raise', 'FLOP', was_facing_bet=False)
        assert t.aggression_factor == 6.0  # raw count, NOT capped


# ── Per-opponent isolation ───────────────────────────────────────────────

class TestPerOpponentIsolation:
    def test_counters_do_not_leak_between_opponents(self):
        """Each OpponentTendencies has independent counter state."""
        t1 = OpponentTendencies()
        t2 = OpponentTendencies()

        t1.update_from_action('all_in', 'FLOP', was_facing_bet=False)
        t1.update_from_action('all_in', 'TURN', was_facing_bet=False)
        # t1 is a first-in jammer; t2 is fresh.

        assert t1._postflop_jam_opens == 2
        assert t2._postflop_jam_opens == 0
        assert t1.postflop_jam_open_rate == 1.0
        assert t2.postflop_jam_open_rate == 0.0


# ── Missing-field tolerance for old records ──────────────────────────────

class TestMissingFieldTolerance:
    def test_from_dict_with_legacy_record_defaults_new_fields_to_zero(self):
        """Records serialized before Phase 7.5 lack the new counter
        fields. from_dict() must default them to 0 / 0.0, not crash."""
        legacy_data = {
            'hands_observed': 50,
            'hands_dealt': 60,
            'vpip': 0.4,
            'pfr': 0.2,
            'aggression_factor': 2.0,
            'fold_to_cbet': 0.6,
            'bluff_frequency': 0.3,
            'showdown_win_rate': 0.5,
            'all_in_frequency': 0.05,
            'recent_trend': 'stable',
            '_vpip_count': 20,
            '_pfr_count': 10,
            '_bet_raise_count': 30,
            '_call_count': 15,
            '_all_in_count': 3,
            '_fold_to_cbet_count': 6,
            '_cbet_faced_count': 10,
            '_showdowns': 8,
            '_showdowns_won': 4,
            # NO Phase 7.5 fields — simulating an old record.
        }
        t = OpponentTendencies.from_dict(legacy_data)
        # Legacy fields preserved
        assert t.hands_observed == 50
        assert t.aggression_factor == 2.0
        # New fields default to neutral / zero
        assert t._postflop_bet_raise_count == 0
        assert t._postflop_call_count == 0
        assert t._facing_bet_opportunities == 0
        assert t._all_ins_facing_bet == 0
        assert t._postflop_open_opportunities == 0
        assert t._postflop_jam_opens == 0
        assert t.aggression_factor_postflop == 1.0  # derived neutral
        assert t.all_in_per_facing_bet == 0.0
        assert t.postflop_jam_open_rate == 0.0

    def test_round_trip_preserves_new_fields(self):
        """to_dict / from_dict should round-trip the new fields."""
        t1 = OpponentTendencies()
        t1.update_from_action('all_in', 'FLOP', was_facing_bet=False)
        t1.update_from_action('call', 'TURN', was_facing_bet=True)
        t1.update_from_action('bet', 'RIVER', was_facing_bet=False)

        snapshot = t1.to_dict()
        t2 = OpponentTendencies.from_dict(snapshot)

        assert t2._postflop_jam_opens == t1._postflop_jam_opens
        assert t2._postflop_open_opportunities == t1._postflop_open_opportunities
        assert t2._facing_bet_opportunities == t1._facing_bet_opportunities
        assert t2._postflop_bet_raise_count == t1._postflop_bet_raise_count
        assert t2._postflop_call_count == t1._postflop_call_count
        assert t2.postflop_jam_open_rate == t1.postflop_jam_open_rate
