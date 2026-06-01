"""Tests for trait-aware emotion families.

The quadrant (confidence x composure) fixes the internal feeling; the
persona's emotion family (from anchors) chooses the surface emotion. A
low-ego fun-lover reads 'gleeful'/'giddy'/'sheepish' where a high-ego
competitor reads 'angry'/'shocked'. See poker/psychology_model.py and
PlayerPsychology._get_true_emotion.
"""

import pytest

from poker.player_psychology import EmotionalAxes, PlayerPsychology
from poker.psychology_model import (
    EmotionFamily,
    PersonalityAnchors,
    get_emotion_family,
)


def _anchors(ego, expressiveness, energy=0.7):
    return PersonalityAnchors(
        baseline_aggression=0.3,
        baseline_looseness=0.6,
        ego=ego,
        poise=0.5,
        expressiveness=expressiveness,
        risk_identity=0.4,
        adaptation_bias=0.3,
        baseline_energy=energy,
        recovery_rate=0.15,
    )


class TestGetEmotionFamily:
    def test_stoic_when_low_expressiveness(self):
        # Low expressiveness wins regardless of ego.
        assert get_emotion_family(_anchors(ego=0.9, expressiveness=0.2)) == EmotionFamily.STOIC

    def test_fun_lover_when_low_ego(self):
        assert get_emotion_family(_anchors(ego=0.2, expressiveness=0.8)) == EmotionFamily.FUN_LOVER

    def test_competitor_when_high_ego(self):
        assert get_emotion_family(_anchors(ego=0.85, expressiveness=0.7)) == EmotionFamily.COMPETITOR

    def test_anxious_in_the_middle(self):
        assert get_emotion_family(_anchors(ego=0.5, expressiveness=0.6)) == EmotionFamily.ANXIOUS

    def test_boundaries(self):
        # expressiveness exactly 0.40 is NOT stoic (strict <)
        assert get_emotion_family(_anchors(ego=0.2, expressiveness=0.40)) == EmotionFamily.FUN_LOVER
        # ego exactly 0.40 is NOT fun_lover (strict <) -> anxious
        assert get_emotion_family(_anchors(ego=0.40, expressiveness=0.6)) == EmotionFamily.ANXIOUS
        # ego exactly 0.55 is NOT competitor (strict >) -> anxious
        assert get_emotion_family(_anchors(ego=0.55, expressiveness=0.6)) == EmotionFamily.ANXIOUS


def _psych(ego, expressiveness, confidence, composure, energy):
    """Build a PlayerPsychology with the given anchors and forced axes."""
    config = {'anchors': _anchors(ego, expressiveness, energy).to_dict()}
    psych = PlayerPsychology.from_personality_config('Tester', config)
    psych.axes = EmotionalAxes(confidence=confidence, composure=composure, energy=energy)
    return psych


def _emotion(**kw):
    # use_expression_filter=False -> the raw family/quadrant label (no zone/dampening)
    return _psych(**kw).get_display_emotion(use_expression_filter=False)


# (ego, expressiveness) for each family
FUN_LOVER = dict(ego=0.2, expressiveness=0.85)
COMPETITOR = dict(ego=0.85, expressiveness=0.7)
STOIC = dict(ego=0.5, expressiveness=0.2)


class TestFamilyDisplayMatrix:
    """The fish complaint: a low-ego tourist should read cheerful, not angry."""

    @pytest.mark.parametrize(
        "confidence,composure,energy,expected",
        [
            (0.6, 0.3, 0.8, 'giddy'),  # OVERHEATED, high energy
            (0.6, 0.3, 0.4, 'gleeful'),  # OVERHEATED, low energy
            (0.3, 0.3, 0.8, 'sheepish'),  # SHAKEN
            (0.7, 0.7, 0.8, 'elated'),  # COMMANDING, high energy
            (0.3, 0.7, 0.4, 'happy'),  # GUARDED
        ],
    )
    def test_fun_lover_never_angry(self, confidence, composure, energy, expected):
        emo = _emotion(**FUN_LOVER, confidence=confidence, composure=composure, energy=energy)
        assert emo == expected
        assert emo != 'angry'

    @pytest.mark.parametrize(
        "confidence,composure,energy,expected",
        [
            (0.6, 0.3, 0.8, 'angry'),  # OVERHEATED, high energy
            (0.6, 0.3, 0.4, 'frustrated'),  # OVERHEATED, low energy
            (0.3, 0.3, 0.8, 'shocked'),  # SHAKEN, high energy
            (0.7, 0.7, 0.8, 'smug'),  # COMMANDING, high energy
        ],
    )
    def test_competitor_keeps_its_edge(self, confidence, composure, energy, expected):
        assert _emotion(**COMPETITOR, confidence=confidence, composure=composure, energy=energy) == expected

    def test_stoic_compresses_toward_poker_face(self):
        # Low-energy stoic in a calm-ish quadrant reads muted.
        assert _emotion(**STOIC, confidence=0.3, composure=0.7, energy=0.3) == 'poker_face'
