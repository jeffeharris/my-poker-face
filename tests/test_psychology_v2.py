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
        """Test that quadrant property works correctly."""
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
        # Initial axes are 0.5 confidence, 0.7 composure -> GUARDED
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
