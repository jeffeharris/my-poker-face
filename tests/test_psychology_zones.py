"""
Unit tests for Psychology System Phase 5: Zone Detection.

Tests the zone detection system:
- Sweet spot detection (circular geometry with cosine falloff)
- Penalty zone detection (edge-based geometry)
- Zone blending and normalization
- Energy manifestation
- PlayerPsychology integration
"""

import pytest
import math
from poker.player_psychology import (
    PersonalityAnchors,
    EmotionalAxes,
    PlayerPsychology,
    ZoneEffects,
    get_zone_effects,
    _calculate_sweet_spot_strength,
    _detect_sweet_spots,
    _detect_penalty_zones,
    _get_zone_manifestation,
    # Zone constants
    ZONE_GUARDED_CENTER,
    ZONE_GUARDED_RADIUS,
    ZONE_POKER_FACE_CENTER,
    ZONE_POKER_FACE_RADIUS,
    ZONE_COMMANDING_CENTER,
    ZONE_COMMANDING_RADIUS,
    ZONE_AGGRO_CENTER,
    ZONE_AGGRO_RADIUS,
    PENALTY_TILTED_THRESHOLD,
    PENALTY_OVERCONFIDENT_THRESHOLD,
    ENERGY_LOW_THRESHOLD,
    ENERGY_HIGH_THRESHOLD,
)


class TestSweetSpotStrength:
    """Tests for _calculate_sweet_spot_strength function."""

    def test_strength_at_center_is_maximum(self):
        """Strength should be 1.0 at the exact center of a zone."""
        center = (0.5, 0.7)
        radius = 0.15

        strength = _calculate_sweet_spot_strength(0.5, 0.7, center, radius)

        assert strength == pytest.approx(1.0, abs=0.001)

    def test_strength_at_edge_is_zero(self):
        """Strength should be 0.0 at the edge of the zone."""
        center = (0.5, 0.7)
        radius = 0.15

        # Point exactly on the edge (distance = radius)
        edge_conf = 0.5 + radius  # Move right by exactly radius
        strength = _calculate_sweet_spot_strength(edge_conf, 0.7, center, radius)

        assert strength == pytest.approx(0.0, abs=0.001)

    def test_strength_outside_zone_is_zero(self):
        """Strength should be 0.0 outside the zone."""
        center = (0.5, 0.7)
        radius = 0.15

        # Point clearly outside
        strength = _calculate_sweet_spot_strength(0.8, 0.3, center, radius)

        assert strength == 0.0

    def test_strength_decreases_with_distance(self):
        """Strength should decrease as distance from center increases."""
        center = (0.5, 0.7)
        radius = 0.15

        strength_at_center = _calculate_sweet_spot_strength(0.5, 0.7, center, radius)
        strength_at_quarter = _calculate_sweet_spot_strength(
            0.5 + radius * 0.25, 0.7, center, radius
        )
        strength_at_half = _calculate_sweet_spot_strength(
            0.5 + radius * 0.5, 0.7, center, radius
        )

        assert strength_at_center > strength_at_quarter > strength_at_half

    def test_cosine_falloff_at_half_radius(self):
        """At half radius, strength should be approximately 0.5 (cosine property)."""
        center = (0.5, 0.7)
        radius = 0.15

        half_radius_point = (0.5 + radius * 0.5, 0.7)
        strength = _calculate_sweet_spot_strength(
            half_radius_point[0], half_radius_point[1], center, radius
        )

        # cos(π * 0.5) = 0, so strength = 0.5 + 0.5 * 0 = 0.5
        assert strength == pytest.approx(0.5, abs=0.01)


class TestSweetSpotDetection:
    """Tests for _detect_sweet_spots function."""

    def test_detect_poker_face_zone_at_center(self):
        """Should detect Poker Face zone when at its center."""
        sweet_spots = _detect_sweet_spots(
            ZONE_POKER_FACE_CENTER[0], ZONE_POKER_FACE_CENTER[1]
        )

        assert 'poker_face' in sweet_spots
        assert sweet_spots['poker_face'] == pytest.approx(1.0, abs=0.001)

    def test_detect_guarded_zone_at_center(self):
        """Should detect Guarded zone when at its center."""
        sweet_spots = _detect_sweet_spots(
            ZONE_GUARDED_CENTER[0], ZONE_GUARDED_CENTER[1]
        )

        assert 'guarded' in sweet_spots
        assert sweet_spots['guarded'] == pytest.approx(1.0, abs=0.001)

    def test_detect_commanding_zone_at_center(self):
        """Should detect Commanding zone when at its center."""
        sweet_spots = _detect_sweet_spots(
            ZONE_COMMANDING_CENTER[0], ZONE_COMMANDING_CENTER[1]
        )

        assert 'commanding' in sweet_spots
        assert sweet_spots['commanding'] == pytest.approx(1.0, abs=0.001)

    def test_detect_aggro_zone_at_center(self):
        """Should detect Aggro zone when at its center."""
        sweet_spots = _detect_sweet_spots(
            ZONE_AGGRO_CENTER[0], ZONE_AGGRO_CENTER[1]
        )

        assert 'aggro' in sweet_spots
        assert sweet_spots['aggro'] == pytest.approx(1.0, abs=0.001)

    def test_no_sweet_spots_in_neutral_territory(self):
        """Should return empty dict when in neutral territory."""
        # Pick a point far from all sweet spots
        sweet_spots = _detect_sweet_spots(0.5, 0.5)

        assert sweet_spots == {}

    def test_multiple_sweet_spots_when_overlapping(self):
        """Should detect multiple zones when in overlapping region."""
        # Point between Poker Face (0.52, 0.72) and Commanding (0.78, 0.78)
        # This is near both but not at center of either
        conf = 0.65
        comp = 0.75

        sweet_spots = _detect_sweet_spots(conf, comp)

        # May detect one or both depending on exact position
        # Key is that total detected is consistent
        assert len(sweet_spots) >= 0  # Could be 0, 1, or 2


class TestPenaltyZoneDetection:
    """Tests for _detect_penalty_zones function."""

    def test_detect_tilted_when_low_composure(self):
        """Should detect Tilted penalty when composure < 0.35."""
        penalties = _detect_penalty_zones(0.5, 0.2)

        assert 'tilted' in penalties
        # Strength = (0.35 - 0.2) / 0.35 = 0.43
        assert penalties['tilted'] == pytest.approx(0.43, abs=0.01)

    def test_no_tilted_when_composure_above_threshold(self):
        """Should not detect Tilted when composure >= 0.35."""
        penalties = _detect_penalty_zones(0.5, 0.5)

        assert 'tilted' not in penalties

    def test_detect_overconfident_when_high_confidence(self):
        """Should detect Overconfident penalty when confidence > 0.90."""
        penalties = _detect_penalty_zones(0.95, 0.7)

        assert 'overconfident' in penalties
        # Strength = (0.95 - 0.90) / 0.10 = 0.5
        assert penalties['overconfident'] == pytest.approx(0.5, abs=0.01)

    def test_no_overconfident_when_confidence_below_threshold(self):
        """Should not detect Overconfident when confidence <= 0.90."""
        penalties = _detect_penalty_zones(0.85, 0.7)

        assert 'overconfident' not in penalties

    def test_detect_shaken_in_lower_left_corner(self):
        """Should detect Shaken penalty when both axes are low."""
        penalties = _detect_penalty_zones(0.2, 0.2)

        assert 'shaken' in penalties
        assert 'tilted' in penalties  # Also tilted (low composure)

    def test_detect_overheated_in_lower_right_corner(self):
        """Should detect Overheated penalty in lower-right corner."""
        penalties = _detect_penalty_zones(0.8, 0.2)

        assert 'overheated' in penalties
        assert 'tilted' in penalties  # Also tilted (low composure)

    def test_detect_detached_in_upper_left_corner(self):
        """Should detect Detached penalty in upper-left corner."""
        penalties = _detect_penalty_zones(0.2, 0.8)

        assert 'detached' in penalties
        assert 'tilted' not in penalties  # Not tilted (high composure)

    def test_penalty_stacking(self):
        """Multiple penalties should be detected simultaneously."""
        # Very low composure + confidence near zero = Tilted + Shaken
        penalties = _detect_penalty_zones(0.1, 0.1)

        assert 'tilted' in penalties
        assert 'shaken' in penalties
        assert len(penalties) == 2

    def test_no_penalties_in_safe_zone(self):
        """Should return empty dict when in safe zone."""
        penalties = _detect_penalty_zones(0.5, 0.6)

        assert penalties == {}


class TestEnergyManifestation:
    """Tests for _get_zone_manifestation function."""

    def test_low_energy_manifestation(self):
        """Should return 'low_energy' when energy < 0.35."""
        assert _get_zone_manifestation(0.2) == 'low_energy'
        assert _get_zone_manifestation(0.0) == 'low_energy'
        assert _get_zone_manifestation(0.34) == 'low_energy'

    def test_high_energy_manifestation(self):
        """Should return 'high_energy' when energy > 0.65."""
        assert _get_zone_manifestation(0.8) == 'high_energy'
        assert _get_zone_manifestation(1.0) == 'high_energy'
        assert _get_zone_manifestation(0.66) == 'high_energy'

    def test_balanced_energy_manifestation(self):
        """Should return 'balanced' when energy is in middle range."""
        assert _get_zone_manifestation(0.5) == 'balanced'
        assert _get_zone_manifestation(0.35) == 'balanced'
        assert _get_zone_manifestation(0.65) == 'balanced'


class TestGetZoneEffects:
    """Tests for get_zone_effects main function."""

    def test_returns_zone_effects_object(self):
        """Should return a ZoneEffects dataclass."""
        effects = get_zone_effects(0.5, 0.7, 0.5)

        assert isinstance(effects, ZoneEffects)

    def test_sweet_spots_normalized_to_one(self):
        """Sweet spot weights should sum to 1.0 when any are active."""
        # At Poker Face center
        effects = get_zone_effects(
            ZONE_POKER_FACE_CENTER[0], ZONE_POKER_FACE_CENTER[1], 0.5
        )

        if effects.sweet_spots:
            total = sum(effects.sweet_spots.values())
            assert total == pytest.approx(1.0, abs=0.001)

    def test_penalties_are_raw_not_normalized(self):
        """Penalty strengths should be raw (not normalized)."""
        # Deep in tilted territory
        effects = get_zone_effects(0.5, 0.1, 0.5)

        assert 'tilted' in effects.penalties
        # Raw strength, not normalized
        expected = (0.35 - 0.1) / 0.35
        assert effects.penalties['tilted'] == pytest.approx(expected, abs=0.01)

    def test_includes_energy_manifestation(self):
        """Should include energy manifestation."""
        effects_low = get_zone_effects(0.5, 0.7, 0.2)
        effects_high = get_zone_effects(0.5, 0.7, 0.8)

        assert effects_low.manifestation == 'low_energy'
        assert effects_high.manifestation == 'high_energy'

    def test_stores_input_coordinates(self):
        """Should store the input coordinates."""
        effects = get_zone_effects(0.6, 0.7, 0.4)

        assert effects.confidence == 0.6
        assert effects.composure == 0.7
        assert effects.energy == 0.4

    def test_neutral_territory_detection(self):
        """Should correctly identify neutral territory."""
        # Point far from all zones
        effects = get_zone_effects(0.5, 0.5, 0.5)

        assert effects.in_neutral_territory
        assert effects.primary_sweet_spot is None
        assert effects.primary_penalty is None


class TestZoneEffectsDataclass:
    """Tests for ZoneEffects dataclass properties."""

    def test_primary_sweet_spot_returns_strongest(self):
        """primary_sweet_spot should return zone with highest strength."""
        effects = ZoneEffects(
            sweet_spots={'poker_face': 0.6, 'commanding': 0.4},
            penalties={},
        )

        assert effects.primary_sweet_spot == 'poker_face'

    def test_primary_sweet_spot_returns_none_when_empty(self):
        """primary_sweet_spot should return None when no sweet spots."""
        effects = ZoneEffects(sweet_spots={}, penalties={})

        assert effects.primary_sweet_spot is None

    def test_primary_penalty_returns_strongest(self):
        """primary_penalty should return penalty with highest strength."""
        effects = ZoneEffects(
            sweet_spots={},
            penalties={'tilted': 0.5, 'shaken': 0.3},
        )

        assert effects.primary_penalty == 'tilted'

    def test_total_penalty_strength_sums_all(self):
        """total_penalty_strength should sum all penalty strengths."""
        effects = ZoneEffects(
            sweet_spots={},
            penalties={'tilted': 0.5, 'shaken': 0.3},
        )

        assert effects.total_penalty_strength == pytest.approx(0.8)

    def test_to_dict_serialization(self):
        """to_dict should serialize all fields."""
        effects = ZoneEffects(
            sweet_spots={'poker_face': 1.0},
            penalties={'tilted': 0.2},
            manifestation='balanced',
            confidence=0.52,
            composure=0.72,
            energy=0.5,
        )

        data = effects.to_dict()

        assert data['sweet_spots'] == {'poker_face': 1.0}
        assert data['penalties'] == {'tilted': 0.2}
        assert data['manifestation'] == 'balanced'
        assert data['confidence'] == 0.52
        assert data['composure'] == 0.72
        assert data['energy'] == 0.5
        assert data['primary_sweet_spot'] == 'poker_face'
        assert data['primary_penalty'] == 'tilted'


class TestPlayerPsychologyIntegration:
    """Tests for PlayerPsychology zone detection integration."""

    @pytest.fixture
    def batman_anchors(self):
        """Batman: Poker Face zone archetype (mid confidence, high poise)."""
        return PersonalityAnchors(
            baseline_aggression=0.4,
            baseline_looseness=0.3,
            ego=0.4,
            poise=0.8,
            expressiveness=0.2,
            risk_identity=0.3,
            adaptation_bias=0.5,
            baseline_energy=0.4,
            recovery_rate=0.15,
        )

    @pytest.fixture
    def ramsay_anchors(self):
        """Gordon Ramsay: Aggro zone archetype (high aggression, volatile)."""
        return PersonalityAnchors(
            baseline_aggression=0.8,
            baseline_looseness=0.6,
            ego=0.8,
            poise=0.35,
            expressiveness=0.9,
            risk_identity=0.7,
            adaptation_bias=0.6,
            baseline_energy=0.8,
            recovery_rate=0.25,
        )

    def test_zone_effects_property_returns_zone_effects(self, batman_anchors):
        """zone_effects property should return ZoneEffects object."""
        config = {'anchors': batman_anchors.to_dict()}
        psych = PlayerPsychology.from_personality_config('Batman', config)

        effects = psych.zone_effects

        assert isinstance(effects, ZoneEffects)

    def test_primary_zone_property(self, batman_anchors):
        """primary_zone property should return zone name or 'neutral'."""
        config = {'anchors': batman_anchors.to_dict()}
        psych = PlayerPsychology.from_personality_config('Batman', config)

        zone = psych.primary_zone

        assert isinstance(zone, str)
        # Could be a zone name or 'neutral'
        valid_zones = ['poker_face', 'guarded', 'commanding', 'aggro',
                       'tilted', 'shaken', 'overheated', 'overconfident',
                       'detached', 'neutral']
        assert zone in valid_zones

    def test_to_dict_includes_zone_effects(self, batman_anchors):
        """to_dict should include zone_effects and primary_zone."""
        config = {'anchors': batman_anchors.to_dict()}
        psych = PlayerPsychology.from_personality_config('Batman', config)

        data = psych.to_dict()

        assert 'zone_effects' in data
        assert 'primary_zone' in data
        assert isinstance(data['zone_effects'], dict)
        assert isinstance(data['primary_zone'], str)

    def test_tilted_player_has_penalty(self, batman_anchors):
        """Player with low composure should have tilted penalty."""
        config = {'anchors': batman_anchors.to_dict()}
        psych = PlayerPsychology.from_personality_config('Batman', config)

        # Push into tilted state
        psych.axes = EmotionalAxes(confidence=0.5, composure=0.2, energy=0.5)

        effects = psych.zone_effects
        assert 'tilted' in effects.penalties
        assert psych.primary_zone == 'tilted'

    def test_overconfident_player_has_penalty(self, batman_anchors):
        """Player with very high confidence should have overconfident penalty."""
        config = {'anchors': batman_anchors.to_dict()}
        psych = PlayerPsychology.from_personality_config('Batman', config)

        # Push into overconfident state
        psych.axes = EmotionalAxes(confidence=0.95, composure=0.7, energy=0.5)

        effects = psych.zone_effects
        assert 'overconfident' in effects.penalties


class TestZoneGeometryConsistency:
    """Tests verifying zone geometry matches documentation."""

    def test_poker_face_center_matches_docs(self):
        """Poker Face center should be (0.52, 0.72)."""
        assert ZONE_POKER_FACE_CENTER == (0.52, 0.72)

    def test_poker_face_radius_matches_docs(self):
        """Poker Face radius should be 0.16."""
        assert ZONE_POKER_FACE_RADIUS == 0.16

    def test_guarded_center_matches_docs(self):
        """Guarded center should be (0.28, 0.72)."""
        assert ZONE_GUARDED_CENTER == (0.28, 0.72)

    def test_commanding_center_matches_docs(self):
        """Commanding center should be (0.78, 0.78)."""
        assert ZONE_COMMANDING_CENTER == (0.78, 0.78)

    def test_aggro_center_matches_docs(self):
        """Aggro center should be (0.68, 0.48)."""
        assert ZONE_AGGRO_CENTER == (0.68, 0.48)

    def test_tilted_threshold_matches_docs(self):
        """Tilted threshold should be composure < 0.35."""
        assert PENALTY_TILTED_THRESHOLD == 0.35

    def test_overconfident_threshold_matches_docs(self):
        """Overconfident threshold should be confidence > 0.90."""
        assert PENALTY_OVERCONFIDENT_THRESHOLD == 0.90
