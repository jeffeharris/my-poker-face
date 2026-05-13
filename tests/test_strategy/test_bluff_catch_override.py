"""Phase 7.5 Item 1 unit tests for bluff-catch override building blocks.

Covers:
  - _base_call_prob: pot-odds × hand-class matrix
  - _board_danger_dampener: street + texture + paired-board factors
  - _bluff_catch_call_probability: composed base × dampener
  - _clamp_to_envelope: L1-bounded interpolation toward proposed

See docs/plans/PHASE_7_5_ADJUSTMENT_LAYER_WIDENING.md §Item 1.
"""

import pytest

from poker.strategy import phase_7_5_config as cfg
from poker.strategy.strategy_profile import StrategyProfile
from poker.strategy.value_override import (
    BLUFF_CATCH_TRIGGER_CLASSES,
    HandStrengthClass,
    _base_call_prob,
    _board_danger_dampener,
    _bluff_catch_call_probability,
    _clamp_to_envelope,
)


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_config():
    cfg.reset_for_testing()
    yield
    cfg.reset_for_testing()


# ── Trigger class membership ─────────────────────────────────────────────

class TestTriggerClasses:
    def test_medium_and_weak_made_in_trigger_set(self):
        assert HandStrengthClass.MEDIUM_MADE.value in BLUFF_CATCH_TRIGGER_CLASSES
        assert HandStrengthClass.WEAK_MADE.value in BLUFF_CATCH_TRIGGER_CLASSES

    def test_strong_hands_not_in_bluff_catch_triggers(self):
        """Mutual exclusivity with strong-hand value override."""
        for s in ('nuts', 'strong_made', 'strong', 'not_strong'):
            assert s not in BLUFF_CATCH_TRIGGER_CLASSES


# ── _base_call_prob: medium_made matrix ──────────────────────────────────

class TestBaseCallProbMediumMade:
    @pytest.mark.parametrize("bet,expected", [
        (0.10, 0.95),    # tiny bet — bluff-catch wide
        (0.50, 0.95),    # boundary at 0.5 — still wide
        (0.51, 0.80),    # just over 0.5 — drops to pot-size band
        (1.00, 0.80),    # pot-size
        (1.50, 0.50),    # 1.5x pot — drops to over-pot
        (2.00, 0.50),    # boundary at 2.0
        (2.50, 0.20),    # 2.5x pot — drops to huge
        (3.50, 0.20),    # 3.5x pot — jam-ish
    ])
    def test_medium_made_band(self, bet, expected):
        assert _base_call_prob('medium_made', bet) == expected


# ── _base_call_prob: weak_made matrix ────────────────────────────────────

class TestBaseCallProbWeakMade:
    @pytest.mark.parametrize("bet,expected", [
        (0.10, 0.70),
        (0.33, 0.70),    # boundary
        (0.40, 0.40),    # over 0.33 → mid band
        (0.67, 0.40),    # boundary
        (0.80, 0.10),    # over 0.67 → fold-heavy
        (2.00, 0.10),    # large bet
    ])
    def test_weak_made_band(self, bet, expected):
        assert _base_call_prob('weak_made', bet) == expected


# ── _base_call_prob: out-of-class returns 0 ──────────────────────────────

class TestBaseCallProbOutOfClass:
    @pytest.mark.parametrize("hand_class", [
        'nuts', 'strong_made', 'strong', 'not_strong', 'air',
    ])
    def test_non_trigger_class_returns_zero(self, hand_class):
        """Hand classes outside BLUFF_CATCH_TRIGGER_CLASSES → 0 prob.
        The caller should never invoke this for those classes; this
        defensive 0.0 keeps misuse from accidentally producing a call."""
        assert _base_call_prob(hand_class, 0.5) == 0.0


# ── _board_danger_dampener ───────────────────────────────────────────────

class TestBoardDangerDampener:
    def test_flop_safe_texture_full_strength(self):
        """Flop + safe texture → no dampening."""
        assert _board_danger_dampener('flop', 'dry_high', 'medium_made') == 1.0
        assert _board_danger_dampener('flop', 'dry_low_static', 'medium_made') == 1.0

    def test_turn_safe_texture(self):
        """Turn applies street factor (0.9) only."""
        assert _board_danger_dampener('turn', 'dry_high', 'medium_made') == 0.9

    def test_river_safe_texture(self):
        """River alone applies 0.6 street factor."""
        assert _board_danger_dampener('river', 'dry_high', 'medium_made') == 0.6

    @pytest.mark.parametrize("texture", [
        'monotone', 'wet_rainbow', 'two_tone_broadway', 'two_tone_connected',
    ])
    def test_flop_dangerous_texture(self, texture):
        """Dangerous texture on flop → 0.5 multiplier, no street penalty."""
        result = _board_danger_dampener('flop', texture, 'medium_made')
        assert result == pytest.approx(0.5)

    def test_river_dangerous_texture_compounds(self):
        """River + monotone → 0.6 × 0.5 = 0.30."""
        result = _board_danger_dampener('river', 'monotone', 'medium_made')
        assert result == pytest.approx(0.30)

    def test_weak_made_on_paired_extra_dampener(self):
        """weak_made on a paired board takes an extra 0.5 factor."""
        # Safe texture except for the paired flag.
        result = _board_danger_dampener(
            'flop', 'dry_low_static', 'weak_made', is_paired_board=True,
        )
        assert result == pytest.approx(0.5)

    def test_medium_made_on_paired_no_extra_dampener(self):
        """The paired-board extra dampener is weak_made specific."""
        result = _board_danger_dampener(
            'flop', 'dry_low_static', 'medium_made', is_paired_board=True,
        )
        assert result == 1.0

    def test_weak_made_river_paired_compounds_three_factors(self):
        """River (0.6) × dangerous_texture (0.5) × paired (0.5) = 0.15.

        Note: 'dry_low_static' is NOT in _DANGEROUS_TEXTURES so texture
        factor doesn't fire. This test uses 'monotone' to get all three.
        """
        result = _board_danger_dampener(
            'river', 'monotone', 'weak_made', is_paired_board=True,
        )
        assert result == pytest.approx(0.6 * 0.5 * 0.5)

    def test_unknown_street_treated_as_flop(self):
        """Unrecognized street values default to flop-level (no penalty)."""
        result = _board_danger_dampener('preflop', 'dry_high', 'medium_made')
        assert result == 1.0


# ── _bluff_catch_call_probability composed ───────────────────────────────

class TestComposedCallProbability:
    def test_safe_spot_full_base_prob(self):
        """Flop + safe texture + medium_made + small bet → full 0.95."""
        result = _bluff_catch_call_probability(
            'medium_made', 0.5, 'flop', 'dry_high',
        )
        assert result == 0.95

    def test_dangerous_river_dampens_to_24_percent(self):
        """River + monotone + medium_made + pot-size bet → 0.80 × 0.30 = 0.24.

        This is the specific case Codex flagged as needing the dampener
        (80% call on a river four-flush board is reckless). The composed
        result drops to 24%, which is conservative.
        """
        result = _bluff_catch_call_probability(
            'medium_made', 1.0, 'river', 'monotone',
        )
        assert result == pytest.approx(0.24)

    def test_weak_made_river_paired_very_low(self):
        """River + dangerous texture + paired board + weak_made + large bet:
        0.10 × 0.6 × 0.5 × 0.5 = 0.015 — essentially folding."""
        result = _bluff_catch_call_probability(
            'weak_made', 1.0, 'river', 'monotone', is_paired_board=True,
        )
        assert result == pytest.approx(0.015)

    def test_out_of_class_returns_zero(self):
        """Hand class outside trigger set → 0 regardless of other args."""
        result = _bluff_catch_call_probability(
            'nuts', 0.5, 'flop', 'dry_high',
        )
        assert result == 0.0


# ── _clamp_to_envelope ───────────────────────────────────────────────────

class TestClampToEnvelope:
    def test_proposed_within_envelope_unchanged(self):
        """When proposed is already within max_total_shift L1 of
        baseline, return it as-is (with zero-prob actions filtered)."""
        baseline = StrategyProfile(action_probabilities={'fold': 0.6, 'call': 0.4})
        proposed = StrategyProfile(action_probabilities={'fold': 0.5, 'call': 0.5})
        # L1 distance = |0.6-0.5| + |0.4-0.5| = 0.2
        clamped = _clamp_to_envelope(proposed, baseline, max_total_shift=0.4)
        assert clamped.action_probabilities == pytest.approx({'fold': 0.5, 'call': 0.5})

    def test_proposed_outside_envelope_pulled_back(self):
        """100% fold baseline → 100% call proposed (L1=2.0). With cap
        0.4, the result is 80% fold / 20% call (each side moves 0.2)."""
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        proposed = StrategyProfile(action_probabilities={'call': 1.0})
        clamped = _clamp_to_envelope(proposed, baseline, max_total_shift=0.4)
        assert clamped.action_probabilities['fold'] == pytest.approx(0.8)
        assert clamped.action_probabilities['call'] == pytest.approx(0.2)

    def test_extreme_cap_fits_full_flip(self):
        """At max_total_shift=0.8, the same 100%→100% flip fits at
        60% fold / 40% call."""
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        proposed = StrategyProfile(action_probabilities={'call': 1.0})
        clamped = _clamp_to_envelope(proposed, baseline, max_total_shift=0.8)
        assert clamped.action_probabilities['fold'] == pytest.approx(0.6)
        assert clamped.action_probabilities['call'] == pytest.approx(0.4)

    def test_zero_distance_returns_baseline(self):
        """If proposed == baseline (L1=0), the early-return handles it
        without dividing by zero."""
        baseline = StrategyProfile(action_probabilities={'fold': 0.7, 'call': 0.3})
        proposed = StrategyProfile(action_probabilities={'fold': 0.7, 'call': 0.3})
        clamped = _clamp_to_envelope(proposed, baseline, max_total_shift=0.4)
        assert clamped.action_probabilities == pytest.approx({'fold': 0.7, 'call': 0.3})

    def test_union_of_action_keys(self):
        """When proposed introduces a new action (call) not in baseline
        ({'fold': 1.0}), the clamp still operates over the union of keys."""
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        proposed = StrategyProfile(action_probabilities={'call': 0.6, 'fold': 0.4})
        # L1 = |1-0.4| + |0-0.6| = 1.2. Cap 0.4 → scale 1/3.
        clamped = _clamp_to_envelope(proposed, baseline, max_total_shift=0.4)
        # baseline.fold=1.0, proposed.fold=0.4 → clamped.fold = 1.0 + (1/3)*(0.4-1.0) = 0.8
        # baseline.call=0, proposed.call=0.6 → clamped.call = 0 + (1/3)*0.6 = 0.2
        assert clamped.action_probabilities['fold'] == pytest.approx(0.8)
        assert clamped.action_probabilities['call'] == pytest.approx(0.2)

    def test_result_filters_zero_probabilities(self):
        """Final distribution should not include actions with 0 prob."""
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        proposed = StrategyProfile(action_probabilities={'fold': 1.0})
        clamped = _clamp_to_envelope(proposed, baseline, max_total_shift=0.4)
        assert 'call' not in clamped.action_probabilities
        assert clamped.action_probabilities['fold'] == pytest.approx(1.0)

    def test_normalized_sum(self):
        """The clamped distribution sums to 1.0."""
        baseline = StrategyProfile(action_probabilities={'fold': 1.0})
        proposed = StrategyProfile(action_probabilities={'call': 0.6, 'fold': 0.4})
        clamped = _clamp_to_envelope(proposed, baseline, max_total_shift=0.4)
        assert sum(clamped.action_probabilities.values()) == pytest.approx(1.0)
