"""
Tests for the Playstyle Selection System.

Tests cover:
- Gaussian affinity computation
- Primary playstyle derivation from baselines
- Identity bias computation
- Exploit scoring from opponent models
- Election interval computation
- Full playstyle selection algorithm (probabilistic election model)
- PlaystyleBriefing construction and engagement tiers
- Integration with PlayerPsychology
"""

import json
import math
import random
import pytest
from unittest.mock import MagicMock, patch

from poker.playstyle_selector import (
    PlaystyleState,
    PlaystyleBriefing,
    compute_playstyle_affinities,
    compute_raw_affinity,
    derive_primary_playstyle,
    compute_identity_bias,
    compute_exploit_scores,
    compute_election_interval,
    select_playstyle,
    build_playstyle_briefing,
    _select_biggest_threat,
    _determine_engagement,
    _detect_emotional_shock,
    _softmax,
    ZONE_CENTERS,
    AFFINITY_SIGMA,
    PRIMARY_STYLE_BONUS,
    ADJACENT_STYLE_BONUS,
    STYLE_ADJACENCY,
)
from poker.zone_detection import (
    ZONE_GUARDED_CENTER,
    ZONE_POKER_FACE_CENTER,
    ZONE_COMMANDING_CENTER,
    ZONE_AGGRO_CENTER,
    ZoneEffects,
    ZoneContext,
)
from poker.player_psychology import (
    PersonalityAnchors,
    PlayerPsychology,
    compute_baseline_confidence,
    compute_baseline_composure,
)

pytestmark = pytest.mark.slow


# === HELPERS ===

def make_anchors(**overrides):
    """Create PersonalityAnchors with defaults."""
    defaults = dict(
        baseline_aggression=0.5,
        baseline_looseness=0.3,
        ego=0.5,
        poise=0.7,
        expressiveness=0.5,
        risk_identity=0.5,
        adaptation_bias=0.5,
        baseline_energy=0.5,
        recovery_rate=0.15,
    )
    defaults.update(overrides)
    return PersonalityAnchors(**defaults)


def make_opponent_model(
    name='Villain',
    hands_observed=10,
    vpip=0.5,
    pfr=0.3,
    aggression_factor=1.0,
    fold_to_cbet=0.5,
):
    """Create a mock OpponentModel with given tendencies."""
    model = MagicMock()
    model.opponent = name
    model.tendencies = MagicMock()
    model.tendencies.hands_observed = hands_observed
    model.tendencies.vpip = vpip
    model.tendencies.pfr = pfr
    model.tendencies.aggression_factor = aggression_factor
    model.tendencies.fold_to_cbet = fold_to_cbet
    model.tendencies.get_summary.return_value = f"AF={aggression_factor:.1f}, VPIP={vpip:.0%}"
    return model


def make_psychology(**anchor_overrides):
    """Create a PlayerPsychology with default config."""
    anchors = make_anchors(**anchor_overrides)
    config = {'anchors': anchors.to_dict()}
    return PlayerPsychology.from_personality_config('TestPlayer', config)


# === TEST CLASSES ===

class TestPlaystyleAffinities:
    """Tests for compute_playstyle_affinities()."""

    def test_always_positive(self):
        """Gaussian affinity is always positive for all styles."""
        for conf in [0.0, 0.1, 0.5, 0.9, 1.0]:
            for comp in [0.0, 0.1, 0.5, 0.9, 1.0]:
                affinities = compute_playstyle_affinities(conf, comp)
                for style, affinity in affinities.items():
                    assert affinity > 0, f"Affinity for {style} at ({conf}, {comp}) should be positive"

    def test_normalized_sum(self):
        """Affinities should sum to 1.0."""
        for conf in [0.0, 0.3, 0.5, 0.7, 1.0]:
            for comp in [0.0, 0.3, 0.5, 0.7, 1.0]:
                affinities = compute_playstyle_affinities(conf, comp)
                total = sum(affinities.values())
                assert abs(total - 1.0) < 1e-10, f"Sum at ({conf}, {comp}) = {total}"

    def test_highest_at_zone_center(self):
        """Each style has highest affinity at its own zone center."""
        for style, center in ZONE_CENTERS.items():
            affinities = compute_playstyle_affinities(center[0], center[1])
            best = max(affinities, key=affinities.get)
            assert best == style, f"At {style} center {center}, best was {best}"

    def test_smooth_decay_with_distance(self):
        """Affinity decreases smoothly with distance from center."""
        center = ZONE_POKER_FACE_CENTER
        aff_at_center = compute_raw_affinity(center[0], center[1], 'poker_face')
        aff_slightly_off = compute_raw_affinity(center[0] + 0.05, center[1], 'poker_face')
        aff_far_off = compute_raw_affinity(center[0] + 0.30, center[1], 'poker_face')

        assert aff_at_center > aff_slightly_off > aff_far_off

    def test_no_dead_zones(self):
        """Even at extreme positions, all affinities are positive."""
        # Extreme corners
        corners = [(0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0)]
        for conf, comp in corners:
            affinities = compute_playstyle_affinities(conf, comp)
            for style, aff in affinities.items():
                assert aff > 0.001, f"{style} at ({conf}, {comp}) = {aff} (should not be dead)"

    def test_all_four_styles_returned(self):
        """All four styles should be in the result."""
        affinities = compute_playstyle_affinities(0.5, 0.5)
        assert set(affinities.keys()) == {'guarded', 'poker_face', 'commanding', 'aggro'}


class TestDerivePlaystyle:
    """Tests for derive_primary_playstyle()."""

    def test_guarded_center(self):
        """Baseline at guarded center -> primary=guarded."""
        result = derive_primary_playstyle(*ZONE_GUARDED_CENTER)
        assert result == 'guarded'

    def test_poker_face_center(self):
        """Baseline at poker_face center -> primary=poker_face."""
        result = derive_primary_playstyle(*ZONE_POKER_FACE_CENTER)
        assert result == 'poker_face'

    def test_commanding_center(self):
        """Baseline at commanding center -> primary=commanding."""
        result = derive_primary_playstyle(*ZONE_COMMANDING_CENTER)
        assert result == 'commanding'

    def test_aggro_center(self):
        """Baseline at aggro center -> primary=aggro."""
        result = derive_primary_playstyle(*ZONE_AGGRO_CENTER)
        assert result == 'aggro'

    def test_midpoint_picks_nearest(self):
        """A point between two zones picks the nearest one."""
        # Midpoint between poker_face (0.52, 0.72) and commanding (0.78, 0.78)
        mid_conf = (0.52 + 0.78) / 2  # 0.65
        mid_comp = (0.72 + 0.78) / 2  # 0.75
        result = derive_primary_playstyle(mid_conf, mid_comp)
        # Should pick one of the two nearest
        assert result in ('poker_face', 'commanding')

    def test_returns_valid_style(self):
        """Always returns a valid style name."""
        for conf in [0.0, 0.25, 0.5, 0.75, 1.0]:
            for comp in [0.0, 0.25, 0.5, 0.75, 1.0]:
                result = derive_primary_playstyle(conf, comp)
                assert result in ZONE_CENTERS


class TestIdentityBias:
    """Tests for compute_identity_bias()."""

    def test_primary_gets_strong_bonus(self):
        """Primary style gets +0.20 bonus."""
        biases = compute_identity_bias('commanding')
        assert biases['commanding'] == PRIMARY_STYLE_BONUS

    def test_adjacent_gets_small_bonus(self):
        """Adjacent styles get +0.05 bonus."""
        biases = compute_identity_bias('poker_face')
        # poker_face is adjacent to guarded and commanding
        assert biases['guarded'] == ADJACENT_STYLE_BONUS
        assert biases['commanding'] == ADJACENT_STYLE_BONUS

    def test_non_adjacent_gets_zero(self):
        """Non-adjacent styles get 0.0."""
        biases = compute_identity_bias('guarded')
        # guarded is adjacent to poker_face only; commanding and aggro are not adjacent
        assert biases['aggro'] == 0.0
        assert biases['commanding'] == 0.0

    def test_all_four_styles_present(self):
        """All four styles in the result."""
        for style in ZONE_CENTERS:
            biases = compute_identity_bias(style)
            assert set(biases.keys()) == set(ZONE_CENTERS.keys())

    def test_aggro_adjacency(self):
        """Aggro is only adjacent to commanding."""
        biases = compute_identity_bias('aggro')
        assert biases['aggro'] == PRIMARY_STYLE_BONUS
        assert biases['commanding'] == ADJACENT_STYLE_BONUS
        assert biases['poker_face'] == 0.0
        assert biases['guarded'] == 0.0


class TestExploitScores:
    """Tests for compute_exploit_scores()."""

    def test_no_opponents_returns_zeros(self):
        """No opponents -> all zeros."""
        scores = compute_exploit_scores(None)
        assert all(v == 0.0 for v in scores.values())

    def test_empty_dict_returns_zeros(self):
        scores = compute_exploit_scores({})
        assert all(v == 0.0 for v in scores.values())

    def test_passive_opponent(self):
        """Passive opponent (AF<0.8) -> commanding and aggro get bonus."""
        model = make_opponent_model(aggression_factor=0.5)
        scores = compute_exploit_scores({'Villain': model})
        assert scores['commanding'] > 0
        assert scores['aggro'] > 0

    def test_aggressive_opponent(self):
        """Aggressive opponent (AF>2.0) -> guarded and poker_face get bonus."""
        model = make_opponent_model(aggression_factor=3.0)
        scores = compute_exploit_scores({'Villain': model})
        assert scores['guarded'] > 0
        assert scores['poker_face'] > 0

    def test_loose_opponent(self):
        """Loose opponent (VPIP>0.45) -> poker_face and commanding get bonus."""
        model = make_opponent_model(vpip=0.60)
        scores = compute_exploit_scores({'Villain': model})
        assert scores['poker_face'] > 0
        assert scores['commanding'] > 0

    def test_tight_opponent(self):
        """Tight opponent (VPIP<0.20) -> aggro and commanding get bonus."""
        model = make_opponent_model(vpip=0.15)
        scores = compute_exploit_scores({'Villain': model})
        assert scores['aggro'] > 0
        assert scores['commanding'] > 0

    def test_nemesis_preferred(self):
        """Nemesis with enough hands is used as threat."""
        nemesis_model = make_opponent_model(name='Nemesis', aggression_factor=0.5)
        other_model = make_opponent_model(name='Other', aggression_factor=5.0)
        models = {'Nemesis': nemesis_model, 'Other': other_model}

        # Without nemesis, most aggressive is picked
        scores_no_nemesis = compute_exploit_scores(models, nemesis=None)

        # With nemesis, nemesis is preferred
        scores_with_nemesis = compute_exploit_scores(models, nemesis='Nemesis')

        # Nemesis is passive -> commanding gets bonus
        assert scores_with_nemesis['commanding'] >= 0.15

    def test_insufficient_hands_ignored(self):
        """Opponent with < 3 hands is ignored."""
        model = make_opponent_model(hands_observed=2, aggression_factor=0.1)
        scores = compute_exploit_scores({'Villain': model})
        assert all(v == 0.0 for v in scores.values())

    def test_scores_capped_at_030(self):
        """Scores are capped at 0.30."""
        # A very passive + tight + high fold-to-cbet opponent maximizes commanding
        model = make_opponent_model(aggression_factor=0.3, vpip=0.10, fold_to_cbet=0.80)
        scores = compute_exploit_scores({'Villain': model})
        assert scores['commanding'] <= 0.30

    def test_high_fold_to_cbet(self):
        """High fold-to-cbet -> commanding and aggro get bonus."""
        model = make_opponent_model(fold_to_cbet=0.75)
        scores = compute_exploit_scores({'Villain': model})
        assert scores['commanding'] > 0 or scores['aggro'] > 0

    def test_low_fold_to_cbet(self):
        """Low fold-to-cbet -> guarded and poker_face get bonus."""
        model = make_opponent_model(fold_to_cbet=0.20)
        scores = compute_exploit_scores({'Villain': model})
        assert scores['guarded'] > 0 or scores['poker_face'] > 0


class TestElectionInterval:
    """Tests for compute_election_interval()."""

    def test_low_adaptation_long_interval(self):
        """Low adaptation (0.0) -> 6 hands between elections."""
        assert compute_election_interval(0.0) == 6

    def test_high_adaptation_short_interval(self):
        """High adaptation (1.0) -> 2 hands between elections."""
        assert compute_election_interval(1.0) == 2

    def test_mid_adaptation(self):
        """Mid adaptation (0.5) -> 4 hands."""
        assert compute_election_interval(0.5) == 4

    def test_monotonically_decreasing(self):
        """Higher adaptation always gives shorter interval."""
        prev = compute_election_interval(0.0)
        for ab in [0.2, 0.5, 0.8, 1.0]:
            curr = compute_election_interval(ab)
            assert curr <= prev
            prev = curr


class TestEmotionalShock:
    """Tests for _detect_emotional_shock()."""

    def test_no_shock_small_change(self):
        """Small axis changes don't trigger shock."""
        assert not _detect_emotional_shock(0.50, 0.70, 0.52, 0.72)

    def test_shock_confidence_drop(self):
        """Large confidence drop triggers shock."""
        assert _detect_emotional_shock(0.35, 0.70, 0.55, 0.70)

    def test_shock_composure_drop(self):
        """Large composure drop triggers shock."""
        assert _detect_emotional_shock(0.50, 0.45, 0.50, 0.70)

    def test_shock_both_axes(self):
        """Both axes swinging triggers shock."""
        assert _detect_emotional_shock(0.30, 0.40, 0.50, 0.70)

    def test_shock_on_rise(self):
        """Shock also triggers on large positive swings."""
        assert _detect_emotional_shock(0.80, 0.70, 0.55, 0.70)


class TestSoftmax:
    """Tests for _softmax()."""

    def test_sums_to_one(self):
        """Probabilities sum to 1.0."""
        scores = {'a': 0.5, 'b': 0.3, 'c': 0.2}
        probs = _softmax(scores, 1.0)
        assert abs(sum(probs.values()) - 1.0) < 1e-10

    def test_low_temperature_sharp(self):
        """Low temperature concentrates probability on highest score."""
        scores = {'a': 0.8, 'b': 0.5, 'c': 0.2}
        probs = _softmax(scores, 0.1)
        assert probs['a'] > 0.95

    def test_high_temperature_flat(self):
        """High temperature makes distribution more uniform."""
        scores = {'a': 0.8, 'b': 0.5, 'c': 0.2}
        probs = _softmax(scores, 5.0)
        # Should be more balanced
        assert probs['c'] > 0.2

    def test_preserves_ordering(self):
        """Higher scores get higher probabilities."""
        scores = {'a': 0.8, 'b': 0.5, 'c': 0.2}
        probs = _softmax(scores, 1.0)
        assert probs['a'] > probs['b'] > probs['c']


class TestSelectPlaystyle:
    """Tests for select_playstyle() algorithm with election model."""

    def _make_rng(self, seed=42):
        """Create a seeded RNG for deterministic tests."""
        return random.Random(seed)

    def test_first_call_triggers_election(self):
        """First call (hands_until_election=0) triggers an election."""
        state = PlaystyleState(
            active_playstyle='poker_face',
            primary_playstyle='commanding',
            hands_until_election=0,
        )
        identity_biases = compute_identity_bias('commanding')

        result = select_playstyle(
            current_state=state,
            confidence=ZONE_COMMANDING_CENTER[0],
            composure=ZONE_COMMANDING_CENTER[1],
            energy=0.5,
            adaptation_bias=0.5,
            identity_biases=identity_biases,
            rng=self._make_rng(),
        )
        assert result.elected_this_hand is True
        assert result.hands_until_election > 0

    def test_locked_in_between_elections(self):
        """Between elections, style doesn't change even if scores shift."""
        # Set last axes near commanding center to avoid triggering shock
        state = PlaystyleState(
            active_playstyle='poker_face',
            primary_playstyle='poker_face',
            hands_until_election=3,
            last_confidence=ZONE_COMMANDING_CENTER[0],
            last_composure=ZONE_COMMANDING_CENTER[1],
        )
        identity_biases = compute_identity_bias('poker_face')

        # At commanding center — but election not due and no shock
        result = select_playstyle(
            current_state=state,
            confidence=ZONE_COMMANDING_CENTER[0],
            composure=ZONE_COMMANDING_CENTER[1],
            energy=0.5,
            adaptation_bias=0.5,
            identity_biases=identity_biases,
            rng=self._make_rng(),
        )
        assert result.active_playstyle == 'poker_face'
        assert result.elected_this_hand is False
        assert result.hands_until_election == 2

    def test_emotional_shock_triggers_emergency_election(self):
        """Large axis swing triggers an emergency election."""
        state = PlaystyleState(
            active_playstyle='poker_face',
            primary_playstyle='poker_face',
            hands_until_election=5,  # Not due yet
            last_confidence=0.70,
            last_composure=0.70,
        )
        identity_biases = compute_identity_bias('poker_face')

        # Composure crashes (shock!) — should trigger emergency election
        result = select_playstyle(
            current_state=state,
            confidence=0.70,
            composure=0.40,  # -0.30 drop > threshold
            energy=0.5,
            adaptation_bias=0.5,
            identity_biases=identity_biases,
            rng=self._make_rng(),
        )
        assert result.elected_this_hand is True

    def test_probabilistic_favors_high_score(self):
        """At high composure (sharp temperature), highest score usually wins."""
        identity_biases = compute_identity_bias('commanding')
        state = PlaystyleState(
            active_playstyle='poker_face',
            primary_playstyle='commanding',
            hands_until_election=0,
        )

        # Run many elections at commanding center with high composure
        wins = {s: 0 for s in ZONE_CENTERS}
        for seed in range(100):
            result = select_playstyle(
                current_state=state,
                confidence=ZONE_COMMANDING_CENTER[0],
                composure=0.90,  # High composure -> sharp temperature
                energy=0.5,
                adaptation_bias=0.5,
                identity_biases=identity_biases,
                rng=random.Random(seed),
            )
            wins[result.active_playstyle] += 1

        # Commanding should win most elections (>40%) — probabilistic, not deterministic
        assert wins['commanding'] > 40
        # And should be the most-elected style
        assert wins['commanding'] == max(wins.values())

    def test_low_composure_more_chaotic(self):
        """At low composure (flat temperature), selection is more spread out."""
        identity_biases = compute_identity_bias('commanding')
        state = PlaystyleState(
            active_playstyle='poker_face',
            primary_playstyle='commanding',
            hands_until_election=0,
        )

        # Run elections at commanding center with LOW composure
        wins = {s: 0 for s in ZONE_CENTERS}
        for seed in range(100):
            result = select_playstyle(
                current_state=state,
                confidence=ZONE_COMMANDING_CENTER[0],
                composure=0.10,  # Low composure -> flat temperature
                energy=0.5,
                adaptation_bias=0.5,
                identity_biases=identity_biases,
                rng=random.Random(seed),
            )
            wins[result.active_playstyle] += 1

        # Should be more spread — commanding wins less than the sharp case
        assert wins['commanding'] < 80  # More variance
        # At least 2 styles should get some wins
        styles_with_wins = sum(1 for v in wins.values() if v > 0)
        assert styles_with_wins >= 2

    def test_exploit_can_shift_probabilities(self):
        """Exploit scoring changes which style is most probable."""
        identity_biases = compute_identity_bias('poker_face')
        state = PlaystyleState(
            active_playstyle='poker_face',
            primary_playstyle='poker_face',
            hands_until_election=0,
        )

        # Very passive opponent -> commanding gets exploit boost
        model = make_opponent_model(aggression_factor=0.3, vpip=0.10, fold_to_cbet=0.80)

        result = select_playstyle(
            current_state=state,
            confidence=ZONE_COMMANDING_CENTER[0],
            composure=ZONE_COMMANDING_CENTER[1],
            energy=0.5,
            adaptation_bias=1.0,
            identity_biases=identity_biases,
            opponent_models={'Villain': model},
            rng=self._make_rng(),
        )

        # Commanding should score higher than poker_face
        assert result.style_scores['commanding'] > result.style_scores['poker_face']

    def test_low_composure_reduces_adaptation(self):
        """Low composure reduces effective_adaptation, limiting exploit influence."""
        identity_biases = compute_identity_bias('poker_face')
        state = PlaystyleState(active_playstyle='poker_face', primary_playstyle='poker_face')

        model = make_opponent_model(aggression_factor=0.3)

        result_high = select_playstyle(
            current_state=state,
            confidence=0.5, composure=0.9, energy=0.9,
            adaptation_bias=1.0,
            identity_biases=identity_biases,
            opponent_models={'V': model},
            rng=self._make_rng(),
        )
        result_low = select_playstyle(
            current_state=state,
            confidence=0.5, composure=0.1, energy=0.9,
            adaptation_bias=1.0,
            identity_biases=identity_biases,
            opponent_models={'V': model},
            rng=self._make_rng(),
        )

        assert result_low.last_effective_adaptation < result_high.last_effective_adaptation

    def test_functional_no_mutation(self):
        """select_playstyle returns a new state without mutating input."""
        state = PlaystyleState(
            active_playstyle='poker_face',
            primary_playstyle='poker_face',
            hands_in_current_style=5,
        )
        identity_biases = compute_identity_bias('poker_face')
        original_hands = state.hands_in_current_style

        result = select_playstyle(
            current_state=state,
            confidence=0.5, composure=0.7, energy=0.5,
            adaptation_bias=0.5,
            identity_biases=identity_biases,
            rng=self._make_rng(),
        )

        assert state.hands_in_current_style == original_hands
        assert result is not state

    def test_election_sets_interval(self):
        """After an election, hands_until_election is set from adaptation_bias."""
        state = PlaystyleState(
            active_playstyle='poker_face',
            primary_playstyle='poker_face',
            hands_until_election=0,
        )
        identity_biases = compute_identity_bias('poker_face')

        result = select_playstyle(
            current_state=state,
            confidence=0.5, composure=0.7, energy=0.5,
            adaptation_bias=0.5,  # -> interval = 4
            identity_biases=identity_biases,
            rng=self._make_rng(),
        )

        assert result.hands_until_election == compute_election_interval(0.5)

    def test_probabilities_stored(self):
        """Style probabilities are stored in state for tracking."""
        state = PlaystyleState(active_playstyle='poker_face', primary_playstyle='poker_face')
        identity_biases = compute_identity_bias('poker_face')

        result = select_playstyle(
            current_state=state,
            confidence=0.5, composure=0.7, energy=0.5,
            adaptation_bias=0.5,
            identity_biases=identity_biases,
            rng=self._make_rng(),
        )

        assert len(result.style_probabilities) == 4
        assert abs(sum(result.style_probabilities.values()) - 1.0) < 1e-10


class TestEngagementTiers:
    """Tests for _determine_engagement()."""

    def test_basic_tier(self):
        assert _determine_engagement(0.10) == 'basic'
        assert _determine_engagement(0.24) == 'basic'

    def test_medium_tier(self):
        assert _determine_engagement(0.25) == 'medium'
        assert _determine_engagement(0.40) == 'medium'
        assert _determine_engagement(0.54) == 'medium'

    def test_full_tier(self):
        assert _determine_engagement(0.55) == 'full'
        assert _determine_engagement(0.80) == 'full'
        assert _determine_engagement(1.0) == 'full'


class TestPlaystyleBriefing:
    """Tests for build_playstyle_briefing()."""

    def _make_zone_effects(self, **kwargs):
        return ZoneEffects(**kwargs)

    def _make_prompt_manager(self):
        pm = MagicMock()
        template = MagicMock()
        template.sections = {}
        pm.get_template.return_value = template
        return pm

    def test_basic_engagement_template_only(self):
        """Basic engagement returns zone template only, no framing."""
        zone_effects = self._make_zone_effects(
            sweet_spots={'poker_face': 0.8},
            manifestation='balanced',
            confidence=0.5,
            composure=0.7,
            energy=0.5,
        )
        pm = self._make_prompt_manager()

        briefing = build_playstyle_briefing(
            active_playstyle='poker_face',
            zone_effects=zone_effects,
            zone_context=ZoneContext(),
            prompt_manager=pm,
            engagement='basic',
            active_affinity=0.1,
        )

        assert briefing.engagement == 'basic'
        # No suppressions at basic
        assert not briefing.suppress_equity_verdict
        assert not briefing.suppress_pot_odds
        assert not briefing.suppress_opponent_emotion

    def test_medium_engagement_has_framing(self):
        """Medium engagement includes mindset frame and risk stance."""
        zone_effects = self._make_zone_effects(
            sweet_spots={},
            manifestation='balanced',
            confidence=0.5,
            composure=0.7,
            energy=0.5,
        )
        pm = self._make_prompt_manager()

        briefing = build_playstyle_briefing(
            active_playstyle='commanding',
            zone_effects=zone_effects,
            zone_context=ZoneContext(),
            prompt_manager=pm,
            engagement='medium',
            active_affinity=0.35,
        )

        assert briefing.engagement == 'medium'
        assert 'COMMANDING MODE' in briefing.guidance
        assert 'Active' in briefing.guidance
        assert 'Extract maximum value' in briefing.guidance
        assert 'maximum pressure' in briefing.guidance
        # No suppressions at medium
        assert not briefing.suppress_equity_verdict

    def test_full_engagement_has_stats_and_suppressions(self):
        """Full engagement includes curated stats and style-specific suppressions."""
        zone_effects = self._make_zone_effects(
            sweet_spots={},
            manifestation='balanced',
            confidence=0.5,
            composure=0.7,
            energy=0.5,
        )
        pm = self._make_prompt_manager()

        # Aggro at full engagement -> suppress equity_verdict and pot_odds
        briefing = build_playstyle_briefing(
            active_playstyle='aggro',
            zone_effects=zone_effects,
            zone_context=ZoneContext(),
            prompt_manager=pm,
            engagement='full',
            active_affinity=0.7,
            threat_name='FishPlayer',
            threat_summary='passive, folds to pressure',
        )

        assert briefing.engagement == 'full'
        assert 'AGGRO MODE' in briefing.guidance
        assert 'Dominant' in briefing.guidance
        assert 'FishPlayer' in briefing.guidance
        assert briefing.suppress_equity_verdict is True
        assert briefing.suppress_pot_odds is True
        assert briefing.suppress_opponent_emotion is False

    def test_poker_face_suppresses_opponent_emotion(self):
        """Poker Face at full engagement suppresses opponent emotion."""
        zone_effects = self._make_zone_effects(
            sweet_spots={},
            manifestation='balanced',
            confidence=0.5,
            composure=0.7,
            energy=0.5,
        )
        pm = self._make_prompt_manager()

        briefing = build_playstyle_briefing(
            active_playstyle='poker_face',
            zone_effects=zone_effects,
            zone_context=ZoneContext(),
            prompt_manager=pm,
            engagement='full',
            active_affinity=0.7,
        )

        assert briefing.suppress_opponent_emotion is True
        assert briefing.suppress_equity_verdict is False
        assert briefing.suppress_pot_odds is False

    def test_guarded_suppresses_pot_odds(self):
        """Guarded at full engagement suppresses pot odds."""
        zone_effects = self._make_zone_effects(
            sweet_spots={},
            manifestation='balanced',
            confidence=0.3,
            composure=0.7,
            energy=0.5,
        )
        pm = self._make_prompt_manager()

        briefing = build_playstyle_briefing(
            active_playstyle='guarded',
            zone_effects=zone_effects,
            zone_context=ZoneContext(),
            prompt_manager=pm,
            engagement='full',
            active_affinity=0.7,
            player_stack=5000,
            pot_total=1000,
        )

        assert briefing.suppress_pot_odds is True
        assert 'GUARDED MODE' in briefing.guidance
        assert '20%' in briefing.guidance  # pot as % of stack

    def test_commanding_no_suppressions(self):
        """Commanding at full engagement has no suppressions (full info)."""
        zone_effects = self._make_zone_effects(
            sweet_spots={},
            manifestation='balanced',
            confidence=0.8,
            composure=0.8,
            energy=0.5,
        )
        pm = self._make_prompt_manager()

        briefing = build_playstyle_briefing(
            active_playstyle='commanding',
            zone_effects=zone_effects,
            zone_context=ZoneContext(),
            prompt_manager=pm,
            engagement='full',
            active_affinity=0.7,
            player_stack=10000,
            avg_stack=5000,
            pot_total=1000,
            big_blind=100,
        )

        assert not briefing.suppress_equity_verdict
        assert not briefing.suppress_pot_odds
        assert not briefing.suppress_opponent_emotion
        assert '2.0x' in briefing.guidance  # stack leverage

    def test_graceful_degradation_missing_data(self):
        """Briefing degrades gracefully when game data is missing."""
        zone_effects = self._make_zone_effects(
            sweet_spots={},
            manifestation='balanced',
            confidence=0.5,
            composure=0.7,
            energy=0.5,
        )
        pm = self._make_prompt_manager()

        # Commanding with no stack/pot data
        briefing = build_playstyle_briefing(
            active_playstyle='commanding',
            zone_effects=zone_effects,
            zone_context=ZoneContext(),
            prompt_manager=pm,
            engagement='full',
            active_affinity=0.7,
            player_stack=0,
            avg_stack=0,
            pot_total=0,
        )

        # Should still produce valid guidance without stat lines (no "x table average")
        assert 'COMMANDING MODE' in briefing.guidance
        assert 'table average' not in briefing.guidance.lower()


class TestPlaystyleState:
    """Tests for PlaystyleState serialization."""

    def test_to_dict_roundtrip(self):
        """Serialization roundtrip preserves all fields."""
        state = PlaystyleState(
            active_playstyle='aggro',
            primary_playstyle='poker_face',
            style_scores={'aggro': 0.8, 'poker_face': 0.5, 'commanding': 0.3, 'guarded': 0.2},
            style_probabilities={'aggro': 0.5, 'poker_face': 0.25, 'commanding': 0.15, 'guarded': 0.10},
            last_switch_hand=5,
            hands_in_current_style=3,
            hands_until_election=2,
            last_effective_adaptation=0.45,
            active_affinity=0.65,
            engagement='full',
            last_confidence=0.60,
            last_composure=0.55,
        )

        d = state.to_dict()
        restored = PlaystyleState.from_dict(d)

        assert restored.active_playstyle == 'aggro'
        assert restored.primary_playstyle == 'poker_face'
        assert restored.last_switch_hand == 5
        assert restored.hands_in_current_style == 3
        assert restored.hands_until_election == 2
        assert abs(restored.last_effective_adaptation - 0.45) < 1e-10
        assert abs(restored.active_affinity - 0.65) < 1e-10
        assert restored.engagement == 'full'
        assert restored.style_scores == state.style_scores
        assert abs(restored.last_confidence - 0.60) < 1e-10
        assert abs(restored.last_composure - 0.55) < 1e-10

    def test_from_dict_defaults(self):
        """from_dict with empty dict gives safe defaults."""
        state = PlaystyleState.from_dict({})
        assert state.active_playstyle == 'poker_face'
        assert state.primary_playstyle == 'poker_face'
        assert state.engagement == 'basic'
        assert state.hands_until_election == 0


class TestSelectBiggestThreat:
    """Tests for _select_biggest_threat()."""

    def test_nemesis_preferred(self):
        """Nemesis is used if they have enough hands."""
        nemesis = make_opponent_model(name='Nemesis', hands_observed=5, aggression_factor=0.5)
        other = make_opponent_model(name='Other', hands_observed=10, aggression_factor=5.0)

        result = _select_biggest_threat({'Nemesis': nemesis, 'Other': other}, nemesis='Nemesis')
        assert result.opponent == 'Nemesis'

    def test_nemesis_not_enough_hands(self):
        """Nemesis with < 3 hands falls back to most aggressive."""
        nemesis = make_opponent_model(name='Nemesis', hands_observed=1, aggression_factor=0.5)
        other = make_opponent_model(name='Other', hands_observed=10, aggression_factor=5.0)

        result = _select_biggest_threat({'Nemesis': nemesis, 'Other': other}, nemesis='Nemesis')
        assert result.opponent == 'Other'

    def test_no_nemesis_picks_most_aggressive(self):
        """Without nemesis, picks most aggressive opponent with enough data."""
        a = make_opponent_model(name='A', aggression_factor=1.0)
        b = make_opponent_model(name='B', aggression_factor=3.0)

        result = _select_biggest_threat({'A': a, 'B': b})
        assert result.opponent == 'B'

    def test_no_valid_opponents(self):
        """Returns None if no opponents have enough hands."""
        model = make_opponent_model(hands_observed=1)
        result = _select_biggest_threat({'V': model})
        assert result is None


class TestPlaystyleIntegration:
    """Integration tests with PlayerPsychology."""

    def test_psychology_initializes_playstyle(self):
        """PlayerPsychology auto-initializes playstyle from anchors."""
        psych = make_psychology()
        assert psych.playstyle_state is not None
        assert psych.active_playstyle in ZONE_CENTERS
        assert psych.playstyle_state.primary_playstyle in ZONE_CENTERS

    def test_primary_matches_baseline(self):
        """Primary playstyle should match the zone closest to baseline axes."""
        psych = make_psychology()
        expected = derive_primary_playstyle(
            compute_baseline_confidence(psych.anchors),
            compute_baseline_composure(psych.anchors),
        )
        assert psych.playstyle_state.primary_playstyle == expected

    def test_update_playstyle_updates_state(self):
        """update_playstyle() updates internal state and triggers election."""
        psych = make_psychology()

        result = psych.update_playstyle(hand_number=1)
        assert result is psych.playstyle_state
        # First call triggers election (hands_until_election starts at 0)
        assert result.elected_this_hand is True
        assert result.hands_until_election > 0

    def test_serialization_roundtrip(self):
        """Playstyle state survives serialization/deserialization."""
        psych = make_psychology()
        psych.update_playstyle(hand_number=1)

        d = psych.to_dict()
        assert 'playstyle_state' in d

        restored = PlayerPsychology.from_dict(d, psych.personality_config)
        assert restored.active_playstyle == psych.active_playstyle
        assert restored.playstyle_state.primary_playstyle == psych.playstyle_state.primary_playstyle

    def test_backward_compat_missing_playstyle(self):
        """Deserialization without playstyle_state derives it from anchors."""
        psych = make_psychology()
        d = psych.to_dict()
        del d['playstyle_state']

        restored = PlayerPsychology.from_dict(d, psych.personality_config)
        # Should auto-derive from baselines in __post_init__
        assert restored.playstyle_state is not None
        assert restored.active_playstyle in ZONE_CENTERS

    def test_high_aggression_personality_favors_aggro(self):
        """A personality with high aggression + risk should derive toward aggro/commanding."""
        psych = make_psychology(
            baseline_aggression=0.9,
            risk_identity=0.9,
            ego=0.8,
        )
        # High aggression -> high baseline confidence -> commanding or aggro
        assert psych.playstyle_state.primary_playstyle in ('commanding', 'aggro')

    def test_cautious_personality_favors_guarded(self):
        """A personality with low aggression and high poise should derive toward guarded."""
        psych = make_psychology(
            baseline_aggression=0.1,
            risk_identity=0.2,
            ego=0.2,
            poise=0.9,
        )
        # Low confidence, high composure -> guarded
        assert psych.playstyle_state.primary_playstyle in ('guarded', 'poker_face')
