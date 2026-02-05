"""
Unit tests for Psychology System v2.1.

Tests the new architecture:
- PersonalityAnchors (static identity layer)
- EmotionalAxes (dynamic state layer)
- EmotionalQuadrant (quadrant-based emotion model)
- Modifier functions (derived aggression/looseness)
- Position-clamped range guidance
"""

import pytest
from poker.player_psychology import (
    PersonalityAnchors,
    EmotionalAxes,
    EmotionalQuadrant,
    PlayerPsychology,
    ComposureState,
    get_quadrant,
    compute_modifiers,
    compute_baseline_confidence,
    compute_baseline_composure,
    _clamp,
)
from poker.range_guidance import (
    looseness_to_range_pct,
    get_range_percentage,
    POSITION_CLAMPS,
)


class TestPersonalityAnchors:
    """Tests for PersonalityAnchors dataclass."""

    def test_valid_anchors_creation(self):
        """Test creating anchors with valid values."""
        anchors = PersonalityAnchors(
            baseline_aggression=0.5,
            baseline_looseness=0.3,
            ego=0.4,
            poise=0.7,
            expressiveness=0.5,
            risk_identity=0.5,
            adaptation_bias=0.5,
            baseline_energy=0.5,
            recovery_rate=0.15,
        )
        assert anchors.baseline_aggression == 0.5
        assert anchors.baseline_looseness == 0.3
        assert anchors.poise == 0.7

    def test_anchor_rejects_negative_values(self):
        """Test that anchors reject values below 0."""
        with pytest.raises(ValueError, match="must be in"):
            PersonalityAnchors(
                baseline_aggression=-0.1,
                baseline_looseness=0.3,
                ego=0.4,
                poise=0.7,
                expressiveness=0.5,
                risk_identity=0.5,
                adaptation_bias=0.5,
                baseline_energy=0.5,
                recovery_rate=0.15,
            )

    def test_anchor_rejects_values_above_one(self):
        """Test that anchors reject values above 1."""
        with pytest.raises(ValueError, match="must be in"):
            PersonalityAnchors(
                baseline_aggression=0.5,
                baseline_looseness=1.5,
                ego=0.4,
                poise=0.7,
                expressiveness=0.5,
                risk_identity=0.5,
                adaptation_bias=0.5,
                baseline_energy=0.5,
                recovery_rate=0.15,
            )

    def test_anchor_rejects_non_numeric(self):
        """Test that anchors reject non-numeric values."""
        with pytest.raises(TypeError, match="must be numeric"):
            PersonalityAnchors(
                baseline_aggression="high",
                baseline_looseness=0.3,
                ego=0.4,
                poise=0.7,
                expressiveness=0.5,
                risk_identity=0.5,
                adaptation_bias=0.5,
                baseline_energy=0.5,
                recovery_rate=0.15,
            )

    def test_anchors_immutable(self):
        """Test that anchors are immutable (frozen dataclass)."""
        anchors = PersonalityAnchors(
            baseline_aggression=0.5,
            baseline_looseness=0.3,
            ego=0.4,
            poise=0.7,
            expressiveness=0.5,
            risk_identity=0.5,
            adaptation_bias=0.5,
            baseline_energy=0.5,
            recovery_rate=0.15,
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            anchors.baseline_aggression = 0.8

    def test_from_dict(self):
        """Test creating anchors from dictionary."""
        data = {
            'baseline_aggression': 0.6,
            'baseline_looseness': 0.4,
            'ego': 0.5,
            'poise': 0.8,
            'expressiveness': 0.3,
            'risk_identity': 0.6,
            'adaptation_bias': 0.5,
            'baseline_energy': 0.4,
            'recovery_rate': 0.2,
        }
        anchors = PersonalityAnchors.from_dict(data)
        assert anchors.baseline_aggression == 0.6
        assert anchors.poise == 0.8

    def test_from_legacy_traits(self):
        """Test converting legacy 5-trait format to anchors."""
        legacy_traits = {
            'tightness': 0.6,  # Should become looseness = 0.4
            'aggression': 0.7,
            'confidence': 0.8,
            'composure': 0.9,
            'table_talk': 0.3,
        }
        anchors = PersonalityAnchors.from_legacy_traits(legacy_traits)
        assert anchors.baseline_aggression == 0.7
        assert anchors.baseline_looseness == 0.4  # 1 - tightness
        assert anchors.poise == 0.9  # composure -> poise

    def test_to_dict(self):
        """Test serializing anchors to dictionary."""
        anchors = PersonalityAnchors(
            baseline_aggression=0.5,
            baseline_looseness=0.3,
            ego=0.4,
            poise=0.7,
            expressiveness=0.5,
            risk_identity=0.5,
            adaptation_bias=0.5,
            baseline_energy=0.5,
            recovery_rate=0.15,
        )
        data = anchors.to_dict()
        assert data['baseline_aggression'] == 0.5
        assert data['poise'] == 0.7
        assert len(data) == 9  # All 9 anchors


class TestEmotionalAxes:
    """Tests for EmotionalAxes dataclass."""

    def test_valid_axes_creation(self):
        """Test creating axes with valid values."""
        axes = EmotionalAxes(confidence=0.6, composure=0.8, energy=0.4)
        assert axes.confidence == 0.6
        assert axes.composure == 0.8
        assert axes.energy == 0.4

    def test_axes_auto_clamp_high(self):
        """Test that axes auto-clamp values above 1."""
        axes = EmotionalAxes(confidence=1.5, composure=2.0, energy=0.5)
        assert axes.confidence == 1.0
        assert axes.composure == 1.0

    def test_axes_auto_clamp_low(self):
        """Test that axes auto-clamp values below 0."""
        axes = EmotionalAxes(confidence=-0.5, composure=0.5, energy=-1.0)
        assert axes.confidence == 0.0
        assert axes.energy == 0.0

    def test_default_values(self):
        """Test default axis values."""
        axes = EmotionalAxes()
        assert axes.confidence == 0.5
        assert axes.composure == 0.7
        assert axes.energy == 0.5

    def test_update_returns_new_instance(self):
        """Test that update() returns a new instance."""
        axes = EmotionalAxes(confidence=0.5, composure=0.7, energy=0.5)
        new_axes = axes.update(confidence=0.8)
        assert new_axes.confidence == 0.8
        assert new_axes.composure == 0.7  # Unchanged
        assert axes.confidence == 0.5  # Original unchanged

    def test_from_dict(self):
        """Test creating axes from dictionary."""
        data = {'confidence': 0.6, 'composure': 0.8, 'energy': 0.3}
        axes = EmotionalAxes.from_dict(data)
        assert axes.confidence == 0.6
        assert axes.composure == 0.8

    def test_to_dict(self):
        """Test serializing axes to dictionary."""
        axes = EmotionalAxes(confidence=0.6, composure=0.8, energy=0.4)
        data = axes.to_dict()
        assert data['confidence'] == 0.6
        assert data['composure'] == 0.8
        assert data['energy'] == 0.4


class TestEmotionalQuadrant:
    """Tests for quadrant determination."""

    def test_commanding_quadrant(self):
        """Test COMMANDING: high confidence + high composure."""
        assert get_quadrant(0.7, 0.7) == EmotionalQuadrant.COMMANDING
        assert get_quadrant(0.9, 0.9) == EmotionalQuadrant.COMMANDING

    def test_overheated_quadrant(self):
        """Test OVERHEATED: high confidence + low composure."""
        assert get_quadrant(0.7, 0.3) == EmotionalQuadrant.OVERHEATED
        assert get_quadrant(0.9, 0.4) == EmotionalQuadrant.OVERHEATED

    def test_guarded_quadrant(self):
        """Test GUARDED: low confidence + high composure."""
        assert get_quadrant(0.3, 0.7) == EmotionalQuadrant.GUARDED
        assert get_quadrant(0.4, 0.9) == EmotionalQuadrant.GUARDED

    def test_shaken_quadrant_low_both(self):
        """Test SHAKEN: low confidence + low composure."""
        assert get_quadrant(0.3, 0.3) == EmotionalQuadrant.SHAKEN
        assert get_quadrant(0.2, 0.2) == EmotionalQuadrant.SHAKEN

    def test_shaken_gate_threshold(self):
        """Test SHAKEN gate at 0.35 threshold."""
        # Both below 0.35 = SHAKEN via gate
        assert get_quadrant(0.34, 0.34) == EmotionalQuadrant.SHAKEN
        # One at 0.36, other below = not triggered by gate
        # but still SHAKEN if both are below 0.5 (low/low quadrant)
        # The gate just adds extra SHAKEN behavior, the quadrant logic
        # also assigns SHAKEN for low confidence + low composure
        assert get_quadrant(0.4, 0.4) == EmotionalQuadrant.SHAKEN  # Low/low without gate

    def test_boundary_at_0_5(self):
        """Test quadrant boundary at 0.5."""
        # Just above 0.5 both = COMMANDING
        assert get_quadrant(0.51, 0.51) == EmotionalQuadrant.COMMANDING
        # Just below 0.5 confidence, above composure = GUARDED
        assert get_quadrant(0.49, 0.51) == EmotionalQuadrant.GUARDED


class TestComputeModifiers:
    """Tests for compute_modifiers function."""

    def test_neutral_state_zero_modifiers(self):
        """Test that neutral state (0.5, 0.5) gives ~zero modifiers."""
        agg_mod, loose_mod = compute_modifiers(0.5, 0.5, 0.5)
        assert abs(agg_mod) < 0.01
        assert abs(loose_mod) < 0.01

    def test_high_confidence_positive_modifiers(self):
        """Test that high confidence increases modifiers."""
        agg_mod, loose_mod = compute_modifiers(0.9, 0.5, 0.5)
        assert agg_mod > 0
        assert loose_mod > 0

    def test_low_composure_increases_aggression(self):
        """Test that low composure increases aggression modifier."""
        agg_mod, loose_mod = compute_modifiers(0.5, 0.3, 0.5)
        assert agg_mod > 0

    def test_modifiers_clamped_normal_state(self):
        """Test that normal state modifiers are clamped to +-0.20."""
        agg_mod, loose_mod = compute_modifiers(1.0, 0.0, 0.5)
        assert -0.20 <= agg_mod <= 0.20
        assert -0.20 <= loose_mod <= 0.20

    def test_shaken_gate_risk_seeking_positive(self):
        """Test SHAKEN gate: risk-seeking (>0.5) gives positive modifiers."""
        agg_mod, loose_mod = compute_modifiers(0.2, 0.2, 0.8)  # Risk-seeking
        # Should get bonus from shaken intensity
        assert agg_mod > 0  # Manic spew

    def test_shaken_gate_risk_averse_negative(self):
        """Test SHAKEN gate: risk-averse (<0.5) gives negative modifiers."""
        agg_mod, loose_mod = compute_modifiers(0.2, 0.2, 0.2)  # Risk-averse
        # Should get penalty from shaken intensity
        assert agg_mod < 0  # Passive collapse

    def test_shaken_wider_clamp_range(self):
        """Test that SHAKEN state allows +-0.30 clamp range."""
        # Extreme shaken state
        agg_mod, loose_mod = compute_modifiers(0.1, 0.1, 0.9)  # Very shaken, risk-seeking
        # Could be up to 0.30
        assert abs(agg_mod) <= 0.30
        assert abs(loose_mod) <= 0.30


class TestBaselineFormulas:
    """Tests for personality-specific baseline derivation formulas."""

    def test_baseline_confidence_formula(self):
        """Test baseline_confidence formula components."""
        anchors = PersonalityAnchors(
            baseline_aggression=0.5,
            baseline_looseness=0.5,
            ego=0.5,
            poise=0.7,
            expressiveness=0.5,
            risk_identity=0.5,
            adaptation_bias=0.5,
            baseline_energy=0.5,
            recovery_rate=0.15,
        )
        # Formula: 0.3 + aggression*0.25 + risk_identity*0.20 + (1-ego)*0.25
        # = 0.3 + 0.125 + 0.10 + 0.125 = 0.65
        baseline = compute_baseline_confidence(anchors)
        assert abs(baseline - 0.65) < 0.01

    def test_baseline_confidence_high_aggression(self):
        """Test that high aggression increases baseline confidence."""
        low_agg = PersonalityAnchors(
            baseline_aggression=0.2, baseline_looseness=0.5, ego=0.5, poise=0.7,
            expressiveness=0.5, risk_identity=0.5, adaptation_bias=0.5,
            baseline_energy=0.5, recovery_rate=0.15,
        )
        high_agg = PersonalityAnchors(
            baseline_aggression=0.8, baseline_looseness=0.5, ego=0.5, poise=0.7,
            expressiveness=0.5, risk_identity=0.5, adaptation_bias=0.5,
            baseline_energy=0.5, recovery_rate=0.15,
        )
        assert compute_baseline_confidence(high_agg) > compute_baseline_confidence(low_agg)

    def test_baseline_confidence_high_ego_raises(self):
        """Test that high ego RAISES baseline confidence (high self-regard)."""
        low_ego = PersonalityAnchors(
            baseline_aggression=0.5, baseline_looseness=0.5, ego=0.2, poise=0.7,
            expressiveness=0.5, risk_identity=0.5, adaptation_bias=0.5,
            baseline_energy=0.5, recovery_rate=0.15,
        )
        high_ego = PersonalityAnchors(
            baseline_aggression=0.5, baseline_looseness=0.5, ego=0.8, poise=0.7,
            expressiveness=0.5, risk_identity=0.5, adaptation_bias=0.5,
            baseline_energy=0.5, recovery_rate=0.15,
        )
        # High ego = higher baseline (thinks highly of themselves)
        # Note: brittleness (bigger drops when challenged) is in event impacts, not baseline
        assert compute_baseline_confidence(high_ego) > compute_baseline_confidence(low_ego)

    def test_baseline_composure_formula(self):
        """Test baseline_composure formula components."""
        anchors = PersonalityAnchors(
            baseline_aggression=0.5,
            baseline_looseness=0.5,
            ego=0.5,
            poise=0.7,
            expressiveness=0.5,
            risk_identity=0.5,
            adaptation_bias=0.5,
            baseline_energy=0.5,
            recovery_rate=0.15,
        )
        # Formula: 0.25 + poise*0.50 + (1-expressiveness)*0.15 + (risk_id-0.5)*0.3
        # = 0.25 + 0.35 + 0.075 + 0 = 0.675
        baseline = compute_baseline_composure(anchors)
        assert abs(baseline - 0.675) < 0.01

    def test_baseline_composure_high_poise(self):
        """Test that high poise increases baseline composure."""
        low_poise = PersonalityAnchors(
            baseline_aggression=0.5, baseline_looseness=0.5, ego=0.5, poise=0.3,
            expressiveness=0.5, risk_identity=0.5, adaptation_bias=0.5,
            baseline_energy=0.5, recovery_rate=0.15,
        )
        high_poise = PersonalityAnchors(
            baseline_aggression=0.5, baseline_looseness=0.5, ego=0.5, poise=0.85,
            expressiveness=0.5, risk_identity=0.5, adaptation_bias=0.5,
            baseline_energy=0.5, recovery_rate=0.15,
        )
        assert compute_baseline_composure(high_poise) > compute_baseline_composure(low_poise)

    def test_baseline_composure_floor(self):
        """Test that baseline_composure has a floor of 0.25."""
        # Create extreme low-composure personality
        extreme = PersonalityAnchors(
            baseline_aggression=0.5, baseline_looseness=0.5, ego=0.5, poise=0.0,
            expressiveness=1.0, risk_identity=0.0, adaptation_bias=0.5,
            baseline_energy=0.5, recovery_rate=0.15,
        )
        # Even with worst anchors, composure should be >= 0.25
        baseline = compute_baseline_composure(extreme)
        assert baseline >= 0.25

    def test_volatile_personality_overheated_baseline(self):
        """Test that volatile personality (low poise, high ego) rests in OVERHEATED."""
        # Gordon Ramsay type: very low poise, high ego, high aggression, high expressiveness
        anchors = PersonalityAnchors(
            baseline_aggression=0.85,
            baseline_looseness=0.7,
            ego=0.85,
            poise=0.20,  # Very low poise = volatile
            expressiveness=0.80,  # High expressiveness = less internal control
            risk_identity=0.75,
            adaptation_bias=0.5,
            baseline_energy=0.7,
            recovery_rate=0.12,
        )
        baseline_conf = compute_baseline_confidence(anchors)
        baseline_comp = compute_baseline_composure(anchors)
        # Should be OVERHEATED: high confidence (>0.5), low composure (<0.5)
        # Formula: 0.25 + 0.20*0.50 + (1-0.80)*0.15 + (0.75-0.5)*0.3 = 0.455
        assert baseline_conf > 0.5, f"Expected conf > 0.5, got {baseline_conf}"
        assert baseline_comp < 0.5, f"Expected comp < 0.5, got {baseline_comp}"

    def test_stoic_personality_poker_face_baseline(self):
        """Test that stoic personality (high poise, low ego) rests near poker face zone."""
        # Batman type: high poise, low ego, moderate aggression
        anchors = PersonalityAnchors(
            baseline_aggression=0.5,
            baseline_looseness=0.4,
            ego=0.30,  # Low ego = stable
            poise=0.85,  # High poise = composed
            expressiveness=0.25,
            risk_identity=0.5,
            adaptation_bias=0.5,
            baseline_energy=0.3,
            recovery_rate=0.15,
        )
        baseline_conf = compute_baseline_confidence(anchors)
        baseline_comp = compute_baseline_composure(anchors)
        # Should be near poker face zone: conf ~0.65, comp ~0.75
        assert 0.55 < baseline_conf < 0.80
        assert 0.65 < baseline_comp < 0.90

    def test_recovery_toward_personality_baselines(self):
        """Test that recover() drifts toward personality-specific baselines, not universal 0.5/0.7."""
        # Create a stoic personality (high poise, low ego) with HIGH baseline composure
        config = {
            'anchors': {
                'baseline_aggression': 0.5,
                'baseline_looseness': 0.5,
                'ego': 0.30,
                'poise': 0.85,  # High poise -> high baseline composure (~0.77)
                'expressiveness': 0.25,
                'risk_identity': 0.5,
                'adaptation_bias': 0.5,
                'baseline_energy': 0.5,
                'recovery_rate': 0.50,  # High rate to see effect quickly
            }
        }
        psych = PlayerPsychology.from_personality_config('StoicPlayer', config)

        # Verify baselines are personality-specific, not universal
        assert psych._baseline_confidence > 0.55  # Higher than universal 0.5
        assert psych._baseline_composure > 0.72   # Higher than universal 0.7

        # Manually set axes to low values (simulating being shaken)
        psych.axes = psych.axes.update(confidence=0.3, composure=0.4)

        # Apply recovery
        psych.recover()

        # After recovery, should drift TOWARD personality-specific baseline,
        # not the old universal values (0.5, 0.7)
        # With rate=0.5: new = old + (baseline - old) * 0.5
        # If baseline_conf ~0.65: new_conf = 0.3 + (0.65 - 0.3) * 0.5 = 0.475
        # If baseline_comp ~0.77: new_comp = 0.4 + (0.77 - 0.4) * 0.5 = 0.585
        assert psych.axes.confidence > 0.4  # Moved toward baseline
        assert psych.axes.composure > 0.5   # Moved toward baseline (higher than 0.55 which old 0.7 baseline would give)

    def test_volatile_personality_recovery_lower_baseline(self):
        """Test that volatile personality recovers to LOWER baseline composure."""
        # Volatile personality (low poise, high ego) with LOW baseline composure
        config = {
            'anchors': {
                'baseline_aggression': 0.8,
                'baseline_looseness': 0.7,
                'ego': 0.85,
                'poise': 0.20,  # Low poise -> low baseline composure
                'expressiveness': 0.80,
                'risk_identity': 0.75,
                'adaptation_bias': 0.5,
                'baseline_energy': 0.7,
                'recovery_rate': 0.50,  # High rate to see effect quickly
            }
        }
        psych = PlayerPsychology.from_personality_config('VolatilePlayer', config)

        # Verify this personality has LOW baseline composure (below 0.5)
        assert psych._baseline_composure < 0.5

        # Start with HIGH composure (unusually calm for this personality)
        psych.axes = psych.axes.update(composure=0.8)

        # Apply recovery
        psych.recover()

        # Volatile personality should recover DOWNWARD toward their low baseline
        # new_comp = 0.8 + (baseline - 0.8) * 0.5
        # If baseline ~0.45: new_comp = 0.8 + (0.45 - 0.8) * 0.5 = 0.625
        assert psych.axes.composure < 0.75  # Dropped toward volatile baseline


class TestPositionClamps:
    """Tests for position-clamped range guidance."""

    def test_early_position_clamps(self):
        """Test early position range clamps."""
        min_range, max_range = POSITION_CLAMPS['early']
        assert min_range == 0.08
        assert max_range == 0.35

    def test_button_position_clamps(self):
        """Test button position range clamps."""
        min_range, max_range = POSITION_CLAMPS['button']
        assert min_range == 0.15
        assert max_range == 0.65

    def test_looseness_to_range_respects_min_clamp(self):
        """Test that very tight player still plays minimum range."""
        # Very tight player (looseness = 0)
        range_pct = looseness_to_range_pct(0.0, 'early')
        assert range_pct >= 0.08  # Early position minimum

    def test_looseness_to_range_respects_max_clamp(self):
        """Test that very loose player is clamped to max range."""
        # Very loose player (looseness = 1.0)
        range_pct = looseness_to_range_pct(1.0, 'early')
        assert range_pct <= 0.35  # Early position maximum

    def test_looseness_linear_mapping(self):
        """Test that looseness maps linearly within clamps."""
        # Neutral looseness = 0.5 should be halfway between min and max
        range_pct = looseness_to_range_pct(0.5, 'button')
        min_r, max_r = POSITION_CLAMPS['button']
        expected = min_r + (max_r - min_r) * 0.5
        assert abs(range_pct - expected) < 0.01

    def test_backward_compat_get_range_percentage(self):
        """Test that get_range_percentage uses tightness (inverted)."""
        # Tightness 0.3 = looseness 0.7
        range_from_tightness = get_range_percentage(0.3, 'button')
        range_from_looseness = looseness_to_range_pct(0.7, 'button')
        assert abs(range_from_tightness - range_from_looseness) < 0.01


class TestPlayerPsychologyIntegration:
    """Integration tests for PlayerPsychology with new model."""

    def test_create_from_anchors_config(self):
        """Test creating PlayerPsychology from anchors config."""
        config = {
            'play_style': 'aggressive and bold',
            'anchors': {
                'baseline_aggression': 0.8,
                'baseline_looseness': 0.6,
                'ego': 0.7,
                'poise': 0.5,
                'expressiveness': 0.6,
                'risk_identity': 0.8,
                'adaptation_bias': 0.5,
                'baseline_energy': 0.7,
                'recovery_rate': 0.2,
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)
        assert psych.anchors.baseline_aggression == 0.8
        assert psych.anchors.baseline_looseness == 0.6

    def test_create_from_legacy_config(self):
        """Test creating PlayerPsychology from legacy traits config."""
        config = {
            'play_style': 'tight and cautious',
            'personality_traits': {
                'tightness': 0.7,
                'aggression': 0.3,
                'confidence': 0.6,
                'composure': 0.8,
                'table_talk': 0.4,
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)
        # Check conversion happened
        assert psych.anchors.baseline_aggression == 0.3
        assert psych.anchors.baseline_looseness == pytest.approx(0.3, 0.01)  # 1 - 0.7

    def test_quadrant_property(self):
        """Test that quadrant property works correctly with personality-specific baselines."""
        # Create a personality that will start in GUARDED quadrant
        # Need low baseline_confidence (low aggression, low risk_identity, low ego)
        # and high baseline_composure (high poise)
        config = {
            'anchors': {
                'baseline_aggression': 0.2,  # Low -> lower confidence
                'baseline_looseness': 0.5,
                'ego': 0.2,                   # Low ego -> lower confidence baseline
                'poise': 0.8,                 # High poise -> higher composure
                'expressiveness': 0.3,        # Low expressiveness -> higher composure
                'risk_identity': 0.3,         # Low -> lower confidence, lower composure
                'adaptation_bias': 0.5,
                'baseline_energy': 0.5,
                'recovery_rate': 0.15,
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)
        # With these anchors:
        # baseline_conf = 0.3 + 0.2*0.25 + 0.3*0.20 + 0.2*0.25 = 0.3 + 0.05 + 0.06 + 0.05 = 0.46
        # baseline_comp = 0.25 + 0.8*0.50 + 0.7*0.15 + (0.3-0.5)*0.3 = 0.25 + 0.40 + 0.105 - 0.06 = 0.695
        # So confidence ~0.46 (<0.5), composure ~0.70 (>0.5) -> GUARDED
        assert psych.quadrant == EmotionalQuadrant.GUARDED

    def test_effective_aggression_derived(self):
        """Test that effective_aggression is derived from anchors + modifiers."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5,
                'baseline_looseness': 0.5,
                'ego': 0.5,
                'poise': 0.7,
                'expressiveness': 0.5,
                'risk_identity': 0.5,
                'adaptation_bias': 0.5,
                'baseline_energy': 0.5,
                'recovery_rate': 0.15,
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)
        # With neutral axes, effective aggression should be close to baseline
        assert abs(psych.effective_aggression - 0.5) < 0.1

    def test_pressure_event_updates_axes(self):
        """Test that pressure events update emotional axes."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5,
                'baseline_looseness': 0.5,
                'ego': 0.8,  # High ego = more sensitive
                'poise': 0.3,  # Low poise = more sensitive
                'expressiveness': 0.5,
                'risk_identity': 0.5,
                'adaptation_bias': 0.5,
                'baseline_energy': 0.5,
                'recovery_rate': 0.15,
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)
        initial_conf = psych.confidence
        initial_comp = psych.composure

        # Apply bad beat (affects composure heavily)
        psych.apply_pressure_event('bad_beat')

        # Composure should drop (low poise = high sensitivity)
        assert psych.composure < initial_comp

    def test_recovery_drifts_toward_baselines(self):
        """Test that recovery drifts axes toward baselines."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5,
                'baseline_looseness': 0.5,
                'ego': 0.5,
                'poise': 0.7,
                'expressiveness': 0.5,
                'risk_identity': 0.5,
                'adaptation_bias': 0.5,
                'baseline_energy': 0.5,
                'recovery_rate': 0.5,  # Fast recovery
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)

        # Manually set axes to extreme values
        psych.axes = EmotionalAxes(confidence=0.2, composure=0.3, energy=0.5)

        # Apply recovery
        psych.recover()

        # Should drift toward 0.5 (confidence baseline) and 0.7 (composure baseline)
        assert psych.confidence > 0.2
        assert psych.composure > 0.3

    def test_backward_compat_tightness_property(self):
        """Test that tightness property returns inverted looseness."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5,
                'baseline_looseness': 0.7,
                'ego': 0.5,
                'poise': 0.7,
                'expressiveness': 0.5,
                'risk_identity': 0.5,
                'adaptation_bias': 0.5,
                'baseline_energy': 0.5,
                'recovery_rate': 0.15,
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)
        # tightness should be ~ 1 - effective_looseness
        assert psych.tightness == pytest.approx(1.0 - psych.effective_looseness, 0.01)

    def test_serialization_round_trip(self):
        """Test that to_dict/from_dict preserves state."""
        config = {
            'anchors': {
                'baseline_aggression': 0.6,
                'baseline_looseness': 0.4,
                'ego': 0.5,
                'poise': 0.8,
                'expressiveness': 0.3,
                'risk_identity': 0.6,
                'adaptation_bias': 0.5,
                'baseline_energy': 0.4,
                'recovery_rate': 0.2,
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)
        psych.axes = EmotionalAxes(confidence=0.7, composure=0.6, energy=0.4)

        # Serialize and deserialize
        data = psych.to_dict()
        restored = PlayerPsychology.from_dict(data, config)

        assert restored.anchors.baseline_aggression == 0.6
        assert restored.axes.confidence == 0.7
        assert restored.axes.composure == 0.6


# === Phase 2 Tests: Energy + Expression ===

class TestEnergyImpacts:
    """Tests for Phase 2 energy impacts in pressure events."""

    def test_energy_included_in_win_events(self):
        """Test that win events include energy impact."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.5, 'poise': 0.7,
                'expressiveness': 0.5, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.15,
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)
        initial_energy = psych.energy

        # Big win should increase energy
        psych.apply_pressure_event('big_win')

        assert psych.energy > initial_energy

    def test_energy_included_in_loss_events(self):
        """Test that loss events include energy impact."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.5, 'poise': 0.7,
                'expressiveness': 0.5, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.15,
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)
        initial_energy = psych.energy

        # Bad beat should decrease energy
        psych.apply_pressure_event('bad_beat')

        assert psych.energy < initial_energy

    def test_energy_only_events_dont_affect_confidence_composure(self):
        """Test that energy-only events don't change confidence/composure."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.5, 'poise': 0.7,
                'expressiveness': 0.5, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.15,
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)
        initial_conf = psych.confidence
        initial_comp = psych.composure
        initial_energy = psych.energy

        # All-in moment is energy-only
        psych.apply_pressure_event('all_in_moment')

        assert psych.confidence == pytest.approx(initial_conf, 0.001)
        assert psych.composure == pytest.approx(initial_comp, 0.001)
        assert psych.energy > initial_energy

    def test_energy_direct_application_no_sensitivity(self):
        """Test that energy changes are applied directly without sensitivity filter."""
        # Create two personalities with different poise/ego
        low_sens_config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.2, 'poise': 0.9,
                'expressiveness': 0.5, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.15,
            }
        }
        high_sens_config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.9, 'poise': 0.2,
                'expressiveness': 0.5, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.15,
            }
        }

        psych_low = PlayerPsychology.from_personality_config('LowSens', low_sens_config)
        psych_high = PlayerPsychology.from_personality_config('HighSens', high_sens_config)

        # Both should get the SAME energy change
        psych_low.apply_pressure_event('all_in_moment')
        psych_high.apply_pressure_event('all_in_moment')

        assert psych_low.energy == psych_high.energy


class TestEnergyRecovery:
    """Tests for Phase 2 energy recovery with edge springs."""

    def test_energy_recovers_toward_baseline(self):
        """Test that energy recovers toward baseline_energy anchor."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.5, 'poise': 0.7,
                'expressiveness': 0.5, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.6, 'recovery_rate': 0.5,  # High rate for faster recovery
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)

        # Set energy below baseline
        psych.axes = psych.axes.update(energy=0.3)

        # Apply recovery
        psych.recover()

        # Should have moved toward baseline_energy (0.6)
        assert psych.energy > 0.3
        assert psych.energy < 0.6  # Not fully recovered yet

    def test_edge_spring_at_low_extreme(self):
        """Test that edge spring pushes away from 0."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.5, 'poise': 0.7,
                'expressiveness': 0.5, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.1,  # Low base rate
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)

        # Set energy very low (triggers edge spring at < 0.15)
        psych.axes = psych.axes.update(energy=0.05)

        # Record recovery amount without edge spring
        normal_target_delta = (0.5 - 0.05) * 0.1  # Would be 0.045

        # Apply recovery
        psych.recover()

        # Edge spring should boost recovery rate, so actual recovery is MORE than normal
        actual_recovery = psych.energy - 0.05
        assert actual_recovery > normal_target_delta

    def test_edge_spring_at_high_extreme(self):
        """Test that edge spring pushes away from 1."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.5, 'poise': 0.7,
                'expressiveness': 0.5, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.1,  # Low base rate
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)

        # Set energy very high (triggers edge spring at > 0.85)
        psych.axes = psych.axes.update(energy=0.95)

        # Record recovery amount without edge spring
        normal_target_delta = abs((0.5 - 0.95) * 0.1)  # Would be 0.045

        # Apply recovery
        psych.recover()

        # Edge spring should boost recovery rate, so actual recovery is MORE than normal
        actual_recovery = abs(psych.energy - 0.95)
        assert actual_recovery > normal_target_delta


class TestConsecutiveFoldTracking:
    """Tests for Phase 2 consecutive fold tracking."""

    def test_fold_increments_counter(self):
        """Test that folding increments the consecutive_folds counter."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.5, 'poise': 0.7,
                'expressiveness': 0.5, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.15,
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)

        assert psych.consecutive_folds == 0

        psych.on_action_taken('fold')
        assert psych.consecutive_folds == 1

        psych.on_action_taken('fold')
        assert psych.consecutive_folds == 2

    def test_non_fold_resets_counter(self):
        """Test that non-fold actions reset the consecutive_folds counter."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.5, 'poise': 0.7,
                'expressiveness': 0.5, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.15,
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)

        psych.on_action_taken('fold')
        psych.on_action_taken('fold')
        assert psych.consecutive_folds == 2

        psych.on_action_taken('call')
        assert psych.consecutive_folds == 0

    def test_three_consecutive_folds_triggers_event(self):
        """Test that 3 consecutive folds triggers consecutive_folds_3 event."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.5, 'poise': 0.7,
                'expressiveness': 0.5, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.15,
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)
        initial_energy = psych.energy

        # Fold twice - no event
        events1 = psych.on_action_taken('fold')
        events2 = psych.on_action_taken('fold')
        assert events1 == []
        assert events2 == []
        assert psych.energy == initial_energy

        # Third fold triggers event
        events3 = psych.on_action_taken('fold')
        assert 'consecutive_folds_3' in events3
        assert psych.energy < initial_energy  # Energy decreased

    def test_five_consecutive_folds_triggers_card_dead(self):
        """Test that 5 consecutive folds triggers card_dead_5 event."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.5, 'poise': 0.7,
                'expressiveness': 0.5, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.15,
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)

        # Fold 4 times
        for _ in range(4):
            psych.on_action_taken('fold')

        energy_before_5th = psych.energy

        # Fifth fold triggers card_dead_5
        events = psych.on_action_taken('fold')
        assert 'card_dead_5' in events
        assert psych.energy < energy_before_5th

    def test_consecutive_folds_serialization(self):
        """Test that consecutive_folds persists through serialization."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.5, 'poise': 0.7,
                'expressiveness': 0.5, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.15,
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)

        psych.on_action_taken('fold')
        psych.on_action_taken('fold')
        assert psych.consecutive_folds == 2

        # Serialize and restore
        data = psych.to_dict()
        restored = PlayerPsychology.from_dict(data, config)

        assert restored.consecutive_folds == 2


class TestExpressionFiltering:
    """Tests for Phase 2 expression filtering."""

    def test_calculate_visibility(self):
        """Test visibility calculation from expressiveness × energy."""
        from poker.expression_filter import calculate_visibility

        # High expressiveness × high energy = high visibility
        assert calculate_visibility(0.8, 0.8) == pytest.approx(0.64, 0.01)

        # Low expressiveness × high energy = medium visibility
        assert calculate_visibility(0.3, 0.8) == pytest.approx(0.24, 0.01)

        # High expressiveness × low energy = medium visibility
        assert calculate_visibility(0.8, 0.3) == pytest.approx(0.24, 0.01)

        # Low expressiveness × low energy = low visibility
        assert calculate_visibility(0.3, 0.3) == pytest.approx(0.09, 0.01)

    def test_dampen_emotion_high_visibility(self):
        """Test that high visibility shows true emotion."""
        from poker.expression_filter import dampen_emotion

        # High visibility (>0.6) shows true emotion
        assert dampen_emotion('angry', 0.7) == 'angry'
        assert dampen_emotion('shocked', 0.8) == 'shocked'

    def test_dampen_emotion_medium_visibility(self):
        """Test that medium visibility shows dampened emotion."""
        from poker.expression_filter import dampen_emotion

        # Medium visibility (0.3-0.6) shows dampened emotion
        assert dampen_emotion('angry', 0.45) == 'frustrated'
        assert dampen_emotion('shocked', 0.5) == 'nervous'
        assert dampen_emotion('smug', 0.4) == 'confident'

    def test_dampen_emotion_low_visibility_deterministic(self):
        """Test that low visibility shows poker_face in deterministic mode."""
        from poker.expression_filter import dampen_emotion

        # Low visibility (<0.3) with deterministic mode always shows poker_face
        assert dampen_emotion('angry', 0.2, use_random=False) == 'poker_face'
        assert dampen_emotion('shocked', 0.1, use_random=False) == 'poker_face'

    def test_get_display_emotion_with_filtering(self):
        """Test that get_display_emotion applies expression filtering."""
        config = {
            'anchors': {
                'baseline_aggression': 0.7, 'baseline_looseness': 0.5, 'ego': 0.5, 'poise': 0.3,
                'expressiveness': 0.2,  # Low expressiveness
                'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.15,
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)

        # Set low energy (expressiveness 0.2 × energy 0.2 = visibility 0.04)
        psych.axes = psych.axes.update(energy=0.2)

        # Without filtering, would show true emotion based on quadrant
        true_emotion = psych.get_display_emotion(use_expression_filter=False)
        assert true_emotion != 'poker_face'  # Has some emotion

        # With filtering (deterministic), should show poker_face
        # Need to test multiple times since it might be random
        displayed = psych.get_display_emotion(use_expression_filter=True)
        # At visibility 0.04, we're in "low" territory and should see dampening
        # The result will be poker_face or the medium-dampened version
        assert displayed in ['poker_face', 'thinking', 'frustrated', 'nervous', 'confident']

    def test_get_display_emotion_high_expressiveness_shows_true(self):
        """Test that high expressiveness + high energy shows true emotion."""
        config = {
            'anchors': {
                'baseline_aggression': 0.7, 'baseline_looseness': 0.5, 'ego': 0.7, 'poise': 0.3,
                'expressiveness': 0.9,  # High expressiveness
                'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.8, 'recovery_rate': 0.15,  # High energy
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)

        # Visibility = 0.9 × 0.8 = 0.72 (high)
        true_emotion = psych.get_display_emotion(use_expression_filter=False)
        filtered_emotion = psych.get_display_emotion(use_expression_filter=True)

        # High visibility should preserve true emotion
        assert filtered_emotion == true_emotion

    def test_expression_guidance_high_visibility(self):
        """Test expression guidance for high visibility players."""
        from poker.expression_filter import get_expression_guidance

        guidance = get_expression_guidance(expressiveness=0.9, energy=0.8)

        assert 'animated' in guidance.lower() or 'full' in guidance.lower()
        assert 'unreadable' not in guidance.lower()

    def test_expression_guidance_low_visibility(self):
        """Test expression guidance for low visibility players."""
        from poker.expression_filter import get_expression_guidance

        guidance = get_expression_guidance(expressiveness=0.2, energy=0.3)

        assert 'unreadable' in guidance.lower() or 'minimal' in guidance.lower()

    def test_tempo_guidance_high_energy(self):
        """Test tempo guidance for high energy."""
        from poker.expression_filter import get_tempo_guidance

        guidance = get_tempo_guidance(energy=0.8)

        assert 'quick' in guidance.lower() or 'hot' in guidance.lower()

    def test_tempo_guidance_low_energy(self):
        """Test tempo guidance for low energy."""
        from poker.expression_filter import get_tempo_guidance

        guidance = get_tempo_guidance(energy=0.2)

        assert 'deliberate' in guidance.lower() or 'detailed' in guidance.lower()
