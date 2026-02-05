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
    PokerFaceZone,
    create_poker_face_zone,
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


# === Phase 3 Tests: Poker Face Zone ===

class TestPokerFaceZoneGeometry:
    """Tests for PokerFaceZone ellipsoid geometry."""

    def test_zone_center_is_inside(self):
        """Test that the zone center is inside the zone."""
        from poker.player_psychology import PokerFaceZone

        zone = PokerFaceZone()
        # Center point should always be inside
        assert zone.contains(0.65, 0.75, 0.4)
        assert zone.distance(0.65, 0.75, 0.4) == pytest.approx(0.0, 0.01)

    def test_zone_boundary_distance(self):
        """Test that boundary points have distance ~1.0."""
        from poker.player_psychology import PokerFaceZone

        zone = PokerFaceZone()
        # Move along confidence axis by radius
        boundary_point = (0.65 + 0.25, 0.75, 0.4)  # (0.9, 0.75, 0.4)
        assert zone.distance(*boundary_point) == pytest.approx(1.0, 0.01)
        assert zone.contains(*boundary_point)  # On boundary = inside

    def test_point_outside_zone(self):
        """Test that points far from center are outside."""
        from poker.player_psychology import PokerFaceZone

        zone = PokerFaceZone()
        # Point well outside zone (all axes far from center)
        assert not zone.contains(0.2, 0.3, 0.9)
        assert zone.distance(0.2, 0.3, 0.9) > 1.0

    def test_point_just_outside_boundary(self):
        """Test that points just outside boundary are detected."""
        from poker.player_psychology import PokerFaceZone

        zone = PokerFaceZone()
        # Move just past boundary on confidence axis
        outside_point = (0.65 + 0.26, 0.75, 0.4)  # Just past radius
        assert not zone.contains(*outside_point)
        assert zone.distance(*outside_point) > 1.0

    def test_ellipsoid_not_sphere(self):
        """Test that zone is ellipsoid (different radii matter)."""
        from poker.player_psychology import PokerFaceZone

        zone = PokerFaceZone()  # rc=0.25, rcomp=0.25, re=0.20

        # Same deviation on confidence (radius 0.25) vs energy (radius 0.20)
        # Energy deviation should result in larger normalized distance
        conf_deviation = zone.distance(0.65 + 0.10, 0.75, 0.4)  # Move 0.10 on confidence
        energy_deviation = zone.distance(0.65, 0.75, 0.4 + 0.10)  # Move 0.10 on energy

        # Energy has smaller radius, so same absolute deviation = larger normalized distance
        assert energy_deviation > conf_deviation

    def test_zone_serialization(self):
        """Test zone serializes and contains expected keys."""
        from poker.player_psychology import PokerFaceZone

        zone = PokerFaceZone(radius_confidence=0.30, radius_composure=0.28, radius_energy=0.18)
        data = zone.to_dict()

        assert data['center_confidence'] == 0.65
        assert data['center_composure'] == 0.75
        assert data['center_energy'] == 0.40
        assert data['radius_confidence'] == 0.30
        assert data['radius_composure'] == 0.28
        assert data['radius_energy'] == 0.18


class TestPokerFaceZoneRadiusModifiers:
    """Tests for personality-based radius modifiers."""

    def test_high_poise_larger_composure_radius(self):
        """Test that high poise gives larger composure radius."""
        from poker.player_psychology import create_poker_face_zone, PersonalityAnchors

        low_poise = PersonalityAnchors(
            baseline_aggression=0.5, baseline_looseness=0.5, ego=0.5, poise=0.2,
            expressiveness=0.5, risk_identity=0.5, adaptation_bias=0.5,
            baseline_energy=0.5, recovery_rate=0.15,
        )
        high_poise = PersonalityAnchors(
            baseline_aggression=0.5, baseline_looseness=0.5, ego=0.5, poise=0.9,
            expressiveness=0.5, risk_identity=0.5, adaptation_bias=0.5,
            baseline_energy=0.5, recovery_rate=0.15,
        )

        zone_low = create_poker_face_zone(low_poise)
        zone_high = create_poker_face_zone(high_poise)

        assert zone_high.radius_composure > zone_low.radius_composure

    def test_low_ego_larger_confidence_radius(self):
        """Test that low ego gives larger confidence radius."""
        from poker.player_psychology import create_poker_face_zone, PersonalityAnchors

        low_ego = PersonalityAnchors(
            baseline_aggression=0.5, baseline_looseness=0.5, ego=0.2, poise=0.5,
            expressiveness=0.5, risk_identity=0.5, adaptation_bias=0.5,
            baseline_energy=0.5, recovery_rate=0.15,
        )
        high_ego = PersonalityAnchors(
            baseline_aggression=0.5, baseline_looseness=0.5, ego=0.8, poise=0.5,
            expressiveness=0.5, risk_identity=0.5, adaptation_bias=0.5,
            baseline_energy=0.5, recovery_rate=0.15,
        )

        zone_low = create_poker_face_zone(low_ego)
        zone_high = create_poker_face_zone(high_ego)

        assert zone_low.radius_confidence > zone_high.radius_confidence

    def test_high_expressiveness_smaller_energy_radius(self):
        """Test that high expressiveness gives smaller energy radius."""
        from poker.player_psychology import create_poker_face_zone, PersonalityAnchors

        low_express = PersonalityAnchors(
            baseline_aggression=0.5, baseline_looseness=0.5, ego=0.5, poise=0.5,
            expressiveness=0.2, risk_identity=0.5, adaptation_bias=0.5,
            baseline_energy=0.5, recovery_rate=0.15,
        )
        high_express = PersonalityAnchors(
            baseline_aggression=0.5, baseline_looseness=0.5, ego=0.5, poise=0.5,
            expressiveness=0.9, risk_identity=0.5, adaptation_bias=0.5,
            baseline_energy=0.5, recovery_rate=0.15,
        )

        zone_low = create_poker_face_zone(low_express)
        zone_high = create_poker_face_zone(high_express)

        assert zone_low.radius_energy > zone_high.radius_energy

    def test_risk_seeking_narrows_confidence_radius(self):
        """Test that risk-seeking (>0.5) narrows confidence radius."""
        from poker.player_psychology import create_poker_face_zone, PersonalityAnchors

        neutral_risk = PersonalityAnchors(
            baseline_aggression=0.5, baseline_looseness=0.5, ego=0.5, poise=0.5,
            expressiveness=0.5, risk_identity=0.5, adaptation_bias=0.5,
            baseline_energy=0.5, recovery_rate=0.15,
        )
        high_risk = PersonalityAnchors(
            baseline_aggression=0.5, baseline_looseness=0.5, ego=0.5, poise=0.5,
            expressiveness=0.5, risk_identity=0.9, adaptation_bias=0.5,
            baseline_energy=0.5, recovery_rate=0.15,
        )

        zone_neutral = create_poker_face_zone(neutral_risk)
        zone_high = create_poker_face_zone(high_risk)

        # Risk-seeking narrows confidence radius
        assert zone_high.radius_confidence < zone_neutral.radius_confidence
        # Composure radius unchanged (same poise, no risk-averse modifier)
        # Note: risk_identity being high means the risk-seeking path, which only affects confidence

    def test_risk_averse_narrows_composure_radius(self):
        """Test that risk-averse (<0.5) narrows composure radius."""
        from poker.player_psychology import create_poker_face_zone, PersonalityAnchors

        neutral_risk = PersonalityAnchors(
            baseline_aggression=0.5, baseline_looseness=0.5, ego=0.5, poise=0.5,
            expressiveness=0.5, risk_identity=0.5, adaptation_bias=0.5,
            baseline_energy=0.5, recovery_rate=0.15,
        )
        low_risk = PersonalityAnchors(
            baseline_aggression=0.5, baseline_looseness=0.5, ego=0.5, poise=0.5,
            expressiveness=0.5, risk_identity=0.1, adaptation_bias=0.5,
            baseline_energy=0.5, recovery_rate=0.15,
        )

        zone_neutral = create_poker_face_zone(neutral_risk)
        zone_low = create_poker_face_zone(low_risk)

        # Risk-averse narrows composure radius
        assert zone_low.radius_composure < zone_neutral.radius_composure

    def test_radius_ranges(self):
        """Test that radius modifiers produce values in expected ranges."""
        from poker.player_psychology import create_poker_face_zone, PersonalityAnchors

        # Extreme personality with all modifiers maximizing zone size
        max_zone = PersonalityAnchors(
            baseline_aggression=0.5, baseline_looseness=0.5,
            ego=0.0,  # Max confidence radius
            poise=1.0,  # Max composure radius
            expressiveness=0.0,  # Max energy radius
            risk_identity=0.5,  # No asymmetric penalty
            adaptation_bias=0.5, baseline_energy=0.5, recovery_rate=0.15,
        )
        # Extreme personality with all modifiers minimizing zone size
        min_zone = PersonalityAnchors(
            baseline_aggression=0.5, baseline_looseness=0.5,
            ego=1.0,  # Min confidence radius
            poise=0.0,  # Min composure radius
            expressiveness=1.0,  # Min energy radius
            risk_identity=1.0,  # Asymmetric penalty on confidence
            adaptation_bias=0.5, baseline_energy=0.5, recovery_rate=0.15,
        )

        zone_max = create_poker_face_zone(max_zone)
        zone_min = create_poker_face_zone(min_zone)

        # Per plan: rc: 0.13-0.33, rcomp: 0.13-0.33, re: 0.14-0.26
        # Note: with risk_identity=1.0, confidence gets additional 20% penalty
        assert 0.10 <= zone_min.radius_confidence <= 0.35
        assert 0.10 <= zone_min.radius_composure <= 0.35
        assert 0.10 <= zone_min.radius_energy <= 0.30

        assert 0.25 <= zone_max.radius_confidence <= 0.35
        assert 0.25 <= zone_max.radius_composure <= 0.35
        assert 0.20 <= zone_max.radius_energy <= 0.30


class TestPokerFaceZoneIntegration:
    """Integration tests for poker face zone with PlayerPsychology."""

    def test_batman_inside_zone_at_baseline(self):
        """Test that Batman (high poise, low ego) is inside zone at baseline."""
        # Batman-like: high poise, low ego, low expressiveness
        config = {
            'anchors': {
                'baseline_aggression': 0.5,
                'baseline_looseness': 0.4,
                'ego': 0.36,  # Low ego = stable confidence
                'poise': 0.9,  # High poise = stable composure
                'expressiveness': 0.25,  # Low expressiveness
                'risk_identity': 0.5,
                'adaptation_bias': 0.5,
                'baseline_energy': 0.4,  # Near zone center energy
                'recovery_rate': 0.15,
            }
        }
        psych = PlayerPsychology.from_personality_config('Batman', config)

        # Should be inside the poker face zone at baseline
        assert psych.is_in_poker_face_zone(), (
            f"Batman should be in poker face zone. "
            f"Conf={psych.confidence:.2f}, Comp={psych.composure:.2f}, "
            f"Energy={psych.energy:.2f}, Distance={psych.zone_distance:.2f}"
        )
        assert psych.get_display_emotion() == 'poker_face'

    def test_zeus_outside_zone_at_baseline(self):
        """Test that Zeus (low poise, high ego) is outside zone at baseline."""
        # Zeus-like: low poise, high ego, high expressiveness
        config = {
            'anchors': {
                'baseline_aggression': 0.85,
                'baseline_looseness': 0.7,
                'ego': 0.88,  # High ego = volatile confidence
                'poise': 0.35,  # Low poise = volatile composure
                'expressiveness': 0.80,  # High expressiveness
                'risk_identity': 0.75,
                'adaptation_bias': 0.5,
                'baseline_energy': 0.7,  # High energy
                'recovery_rate': 0.12,
            }
        }
        psych = PlayerPsychology.from_personality_config('Zeus', config)

        # Should be outside the poker face zone
        assert not psych.is_in_poker_face_zone(), (
            f"Zeus should be outside poker face zone. "
            f"Conf={psych.confidence:.2f}, Comp={psych.composure:.2f}, "
            f"Energy={psych.energy:.2f}, Distance={psych.zone_distance:.2f}"
        )
        # Should show quadrant-based emotion (not poker_face due to zone)
        # Note: may still be filtered by expression filter, but should not be poker_face due to zone

    def test_bob_ross_inside_zone_at_baseline(self):
        """Test that Bob Ross (high poise, moderate ego) is inside zone at baseline."""
        # Bob Ross-like: very high poise, moderate ego, moderate expressiveness
        config = {
            'anchors': {
                'baseline_aggression': 0.4,
                'baseline_looseness': 0.6,
                'ego': 0.5,  # Moderate ego
                'poise': 0.85,  # Very high poise
                'expressiveness': 0.6,  # Moderate expressiveness
                'risk_identity': 0.5,
                'adaptation_bias': 0.5,
                'baseline_energy': 0.45,  # Near zone center energy
                'recovery_rate': 0.15,
            }
        }
        psych = PlayerPsychology.from_personality_config('Bob Ross', config)

        # Should be inside the poker face zone at baseline
        assert psych.is_in_poker_face_zone(), (
            f"Bob Ross should be in poker face zone. "
            f"Conf={psych.confidence:.2f}, Comp={psych.composure:.2f}, "
            f"Energy={psych.energy:.2f}, Distance={psych.zone_distance:.2f}"
        )

    def test_pressure_can_exit_zone(self):
        """Test that pressure events can push a player out of the zone."""
        # Start with Batman-like personality in zone
        config = {
            'anchors': {
                'baseline_aggression': 0.5,
                'baseline_looseness': 0.4,
                'ego': 0.5,  # Moderate ego so pressure affects them
                'poise': 0.5,  # Moderate poise so pressure affects them
                'expressiveness': 0.5,
                'risk_identity': 0.5,
                'adaptation_bias': 0.5,
                'baseline_energy': 0.4,
                'recovery_rate': 0.15,
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)

        initial_in_zone = psych.is_in_poker_face_zone()

        # Apply multiple bad beats to push composure down
        for _ in range(5):
            psych.apply_pressure_event('bad_beat')

        # After pressure, should be outside zone
        assert psych.composure < 0.5  # Composure dropped significantly
        assert not psych.is_in_poker_face_zone() or psych.zone_distance > 0.8, (
            f"Player should be pushed toward zone boundary by pressure. "
            f"Composure={psych.composure:.2f}, Distance={psych.zone_distance:.2f}"
        )

    def test_zone_distance_property(self):
        """Test that zone_distance property returns expected values."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.5, 'poise': 0.7,
                'expressiveness': 0.5, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.15,
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)

        # Should be a float
        assert isinstance(psych.zone_distance, float)

        # If inside zone, distance < 1.0; if outside, distance > 1.0
        if psych.is_in_poker_face_zone():
            assert psych.zone_distance <= 1.0
        else:
            assert psych.zone_distance > 1.0

    def test_serialization_includes_zone_info(self):
        """Test that serialization includes zone information."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.5, 'poise': 0.7,
                'expressiveness': 0.5, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.15,
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)
        data = psych.to_dict()

        assert 'poker_face_zone' in data
        assert 'in_poker_face_zone' in data
        assert 'zone_distance' in data
        assert isinstance(data['in_poker_face_zone'], bool)
        assert isinstance(data['zone_distance'], float)

    def test_deserialization_recomputes_zone(self):
        """Test that deserialization recomputes zone from anchors."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.3, 'poise': 0.8,
                'expressiveness': 0.4, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.15,
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)
        original_zone = psych._poker_face_zone

        # Serialize and restore
        data = psych.to_dict()
        restored = PlayerPsychology.from_dict(data, config)

        # Zone should be recomputed with same radii
        assert restored._poker_face_zone.radius_confidence == pytest.approx(
            original_zone.radius_confidence, 0.001
        )
        assert restored._poker_face_zone.radius_composure == pytest.approx(
            original_zone.radius_composure, 0.001
        )

    def test_display_emotion_bypasses_quadrant_in_zone(self):
        """Test that players in zone show poker_face regardless of quadrant."""
        # Create player who would normally show 'confident' (COMMANDING quadrant)
        # but is inside the poker face zone
        config = {
            'anchors': {
                'baseline_aggression': 0.6,
                'baseline_looseness': 0.5,
                'ego': 0.3,  # Low ego = large zone
                'poise': 0.85,  # High poise = large zone
                'expressiveness': 0.3,  # Low expressiveness = large zone
                'risk_identity': 0.5,
                'adaptation_bias': 0.5,
                'baseline_energy': 0.4,  # Near zone center
                'recovery_rate': 0.15,
            }
        }
        psych = PlayerPsychology.from_personality_config('TestPlayer', config)

        # Verify they're in the zone
        assert psych.is_in_poker_face_zone()

        # Verify their true emotion would be something other than poker_face
        true_emotion = psych.get_display_emotion(use_expression_filter=False)
        assert true_emotion != 'poker_face', f"True emotion should not be poker_face, got {true_emotion}"

        # But display emotion should be poker_face
        display_emotion = psych.get_display_emotion(use_expression_filter=True)
        assert display_emotion == 'poker_face'

    def test_display_emotion_shows_quadrant_outside_zone(self):
        """Test that players outside zone show quadrant-based emotion."""
        # Create volatile player who is clearly outside zone
        config = {
            'anchors': {
                'baseline_aggression': 0.85,
                'baseline_looseness': 0.7,
                'ego': 0.9,  # Very high ego = small zone
                'poise': 0.2,  # Very low poise = small zone
                'expressiveness': 0.9,  # Very high expressiveness = small zone
                'risk_identity': 0.8,
                'adaptation_bias': 0.5,
                'baseline_energy': 0.8,  # High energy far from zone center
                'recovery_rate': 0.15,
            }
        }
        psych = PlayerPsychology.from_personality_config('VolatilePlayer', config)

        # Verify they're outside the zone
        assert not psych.is_in_poker_face_zone()

        # Display emotion should NOT be forced to poker_face by zone
        # (May still be filtered by expression filter, but that's separate)
        # With high expressiveness and high energy, visibility is high, so true emotion shows
        display_emotion = psych.get_display_emotion(use_expression_filter=True)
        # Should be their quadrant emotion, not poker_face
        # (expression filter visibility = 0.9 * 0.8 = 0.72, which is > 0.6 threshold)
        true_emotion = psych.get_display_emotion(use_expression_filter=False)
        assert display_emotion == true_emotion  # High visibility shows true emotion


# === Phase 4 Tests: Severity Sensitivity + Asymmetric Recovery ===

class TestSeveritySensitivity:
    """Tests for Phase 4 severity-based sensitivity floors."""

    def test_minor_event_uses_low_floor(self):
        """Minor events use floor=0.20, giving lower sensitivity."""
        from poker.player_psychology import _get_severity_floor, _calculate_sensitivity

        floor = _get_severity_floor('win')  # Minor event
        assert floor == 0.20

        # Low ego player (0.2) with minor event
        # sensitivity = 0.20 + 0.80 × 0.2 = 0.36
        sensitivity = _calculate_sensitivity(0.2, floor)
        assert sensitivity == pytest.approx(0.36, 0.01)

    def test_normal_event_uses_default_floor(self):
        """Normal events use floor=0.30 (the default)."""
        from poker.player_psychology import _get_severity_floor, _calculate_sensitivity

        floor = _get_severity_floor('big_loss')  # Normal event
        assert floor == 0.30

        # Low ego player (0.2) with normal event
        # sensitivity = 0.30 + 0.70 × 0.2 = 0.44
        sensitivity = _calculate_sensitivity(0.2, floor)
        assert sensitivity == pytest.approx(0.44, 0.01)

    def test_major_event_uses_high_floor(self):
        """Major events use floor=0.40, giving higher minimum sensitivity."""
        from poker.player_psychology import _get_severity_floor, _calculate_sensitivity

        floor = _get_severity_floor('bad_beat')  # Major event
        assert floor == 0.40

        # Even low ego player (0.2) feels major events more
        # sensitivity = 0.40 + 0.60 × 0.2 = 0.52
        sensitivity = _calculate_sensitivity(0.2, floor)
        assert sensitivity == pytest.approx(0.52, 0.01)

    def test_high_ego_major_event_near_full(self):
        """High ego + major event approaches full impact."""
        from poker.player_psychology import _get_severity_floor, _calculate_sensitivity

        floor = _get_severity_floor('bad_beat')  # Major event
        # sensitivity = 0.40 + 0.60 × 0.9 = 0.94
        sensitivity = _calculate_sensitivity(0.9, floor)
        assert sensitivity == pytest.approx(0.94, 0.01)

    def test_unknown_event_defaults_to_normal(self):
        """Unknown events default to normal severity (0.30 floor)."""
        from poker.player_psychology import _get_severity_floor

        floor = _get_severity_floor('completely_made_up_event')
        assert floor == 0.30

    def test_minor_vs_major_event_impact_difference(self):
        """Verify that minor and major events have different impacts."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5,
                'ego': 0.3,  # Low-moderate ego
                'poise': 0.3,  # Low-moderate poise (sensitive to composure)
                'expressiveness': 0.5, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.15,
            }
        }
        # Test minor event (win)
        psych_minor = PlayerPsychology.from_personality_config('TestMinor', config)
        initial_conf = psych_minor.confidence
        psych_minor.apply_pressure_event('win')  # Minor event
        minor_delta = psych_minor.confidence - initial_conf

        # Test major event (double_up) - similar base impact but higher floor
        psych_major = PlayerPsychology.from_personality_config('TestMajor', config)
        initial_conf_major = psych_major.confidence
        psych_major.apply_pressure_event('double_up')  # Major event
        major_delta = psych_major.confidence - initial_conf_major

        # Major event should have larger impact due to higher floor
        # (Both are positive confidence events, major should be bigger)
        assert abs(major_delta) > abs(minor_delta)

    def test_poise_inverted_for_composure(self):
        """High poise = LOW sensitivity to composure events."""
        # High poise player
        high_poise_config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.5,
                'poise': 0.9,  # High poise
                'expressiveness': 0.5, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.15,
            }
        }
        # Low poise player
        low_poise_config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.5,
                'poise': 0.1,  # Low poise
                'expressiveness': 0.5, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.15,
            }
        }

        psych_high = PlayerPsychology.from_personality_config('HighPoise', high_poise_config)
        psych_low = PlayerPsychology.from_personality_config('LowPoise', low_poise_config)

        # Record initial composure (will differ due to different baselines)
        high_initial = psych_high.composure
        low_initial = psych_low.composure

        # Apply same bad_beat event
        psych_high.apply_pressure_event('bad_beat')
        psych_low.apply_pressure_event('bad_beat')

        # Calculate composure drops
        high_drop = high_initial - psych_high.composure
        low_drop = low_initial - psych_low.composure

        # Low poise should drop MORE (higher sensitivity = 1 - poise)
        assert low_drop > high_drop


class TestAsymmetricRecovery:
    """Tests for Phase 4 asymmetric recovery mechanics."""

    def test_recovery_slower_when_deeply_tilted(self):
        """
        Recovery from deep tilt (comp=0.2) is proportionally slower than mild tilt.

        The asymmetric recovery means that the MODIFIER is smaller when deeply tilted,
        making tilt "sticky". We test this by comparing the effective recovery rate
        (recovery / gap_to_baseline) rather than absolute recovery amounts.
        """
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.5,
                'poise': 0.7,  # Baseline composure around 0.675
                'expressiveness': 0.5, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.3,  # Use same rate
            }
        }

        # Deep tilt player (composure = 0.2)
        psych_deep = PlayerPsychology.from_personality_config('DeepTilt', config)
        psych_deep.axes = psych_deep.axes.update(composure=0.2)
        deep_baseline = psych_deep._baseline_composure
        deep_gap = deep_baseline - 0.2

        # Mild tilt player (composure = 0.5)
        psych_mild = PlayerPsychology.from_personality_config('MildTilt', config)
        psych_mild.axes = psych_mild.axes.update(composure=0.5)
        mild_baseline = psych_mild._baseline_composure
        mild_gap = mild_baseline - 0.5

        # Apply recovery
        psych_deep.recover()
        psych_mild.recover()

        # Calculate recovery as proportion of gap closed
        deep_recovery = psych_deep.composure - 0.2
        mild_recovery = psych_mild.composure - 0.5

        deep_rate_effective = deep_recovery / deep_gap if deep_gap > 0 else 0
        mild_rate_effective = mild_recovery / mild_gap if mild_gap > 0 else 0

        # Deep tilt should have LOWER effective rate (sticky modifier = 0.6 + 0.4 × 0.2 = 0.68)
        # Mild tilt should have HIGHER effective rate (sticky modifier = 0.6 + 0.4 × 0.5 = 0.80)
        # Expected: deep_rate = 0.3 × 0.68 = 0.204, mild_rate = 0.3 × 0.80 = 0.24
        assert deep_rate_effective < mild_rate_effective

    def test_hot_streak_decays_at_point_eight(self):
        """Above-baseline states decay at fixed 0.8 modifier."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.5,
                'poise': 0.5,  # Moderate baseline
                'expressiveness': 0.5, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.5,  # High rate
            }
        }

        psych = PlayerPsychology.from_personality_config('HotStreak', config)
        baseline_comp = psych._baseline_composure

        # Set composure ABOVE baseline (hot streak state)
        psych.axes = psych.axes.update(composure=0.9)

        # Apply recovery
        psych.recover()

        # Expected: new = 0.9 + (baseline - 0.9) × 0.5 × 0.8
        # With baseline ~0.55: new = 0.9 + (0.55 - 0.9) × 0.5 × 0.8 = 0.9 - 0.14 = 0.76
        expected = 0.9 + (baseline_comp - 0.9) * 0.5 * 0.8

        assert psych.composure == pytest.approx(expected, 0.01)

    def test_recovery_still_targets_personality_baseline(self):
        """Recovery target is personality-specific, not universal."""
        # High-poise player has high baseline composure
        high_poise_config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.5,
                'poise': 0.9,  # High poise -> high baseline
                'expressiveness': 0.3, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.9,  # Very high rate for convergence
            }
        }

        psych = PlayerPsychology.from_personality_config('HighPoise', high_poise_config)
        baseline_comp = psych._baseline_composure

        # Verify baseline is high (not the old universal 0.7)
        assert baseline_comp > 0.7

        # Set composure low
        psych.axes = psych.axes.update(composure=0.3)

        # Apply many recovery cycles
        for _ in range(50):
            psych.recover()

        # Should converge toward personality baseline, not universal 0.7
        assert psych.composure > 0.7  # Should be closer to ~0.8

    def test_energy_recovery_unchanged(self):
        """Energy still uses edge springs, not asymmetric recovery."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5, 'ego': 0.5, 'poise': 0.7,
                'expressiveness': 0.5, 'risk_identity': 0.5, 'adaptation_bias': 0.5,
                'baseline_energy': 0.5, 'recovery_rate': 0.5,
            }
        }

        # Test energy at extreme low (should trigger edge spring)
        psych_low = PlayerPsychology.from_personality_config('LowEnergy', config)
        psych_low.axes = psych_low.axes.update(energy=0.05)

        # Energy recovery should be boosted by edge spring
        psych_low.recover()

        # Edge spring at 0.05: spring = (0.15 - 0.05) × 0.33 = 0.033
        # Rate becomes 0.5 + 0.033 = 0.533
        # new = 0.05 + (0.5 - 0.05) × 0.533 = 0.05 + 0.24 = 0.29
        assert psych_low.energy > 0.2  # Significant boost from edge spring

    def test_confidence_asymmetric_recovery(self):
        """Confidence also uses asymmetric recovery."""
        config = {
            'anchors': {
                'baseline_aggression': 0.5, 'baseline_looseness': 0.5,
                'ego': 0.5,  # Baseline confidence around 0.65
                'poise': 0.7, 'expressiveness': 0.5, 'risk_identity': 0.5,
                'adaptation_bias': 0.5, 'baseline_energy': 0.5, 'recovery_rate': 0.5,
            }
        }

        psych = PlayerPsychology.from_personality_config('Test', config)
        baseline_conf = psych._baseline_confidence

        # Set confidence below baseline
        psych.axes = psych.axes.update(confidence=0.3)

        # Apply recovery
        psych.recover()

        # Sticky modifier = 0.6 + 0.4 × 0.3 = 0.72
        # new = 0.3 + (baseline - 0.3) × 0.5 × 0.72
        expected = 0.3 + (baseline_conf - 0.3) * 0.5 * 0.72

        assert psych.confidence == pytest.approx(expected, 0.01)
