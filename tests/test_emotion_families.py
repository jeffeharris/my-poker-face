"""Tests for trait-aware emotion families.

The quadrant (confidence x composure) fixes the internal feeling; the
persona's emotion family (from anchors) chooses the surface emotion. A
low-ego fun-lover reads 'gleeful'/'giddy'/'sheepish' where a high-ego
competitor reads 'angry'/'shocked'. See poker/psychology_model.py and
PlayerPsychology._get_true_emotion.
"""

import json
from pathlib import Path

import pytest

from poker.player_psychology import EmotionalAxes, PlayerPsychology
from poker.psychology_model import (
    EmotionFamily,
    PersonalityAnchors,
    compute_baseline_confidence,
    get_emotion_family,
)

_PERSONALITIES = json.loads(
    (Path(__file__).resolve().parents[1] / 'poker' / 'personalities.json').read_text()
)['personalities']


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
        assert (
            get_emotion_family(_anchors(ego=0.85, expressiveness=0.7)) == EmotionFamily.COMPETITOR
        )

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
        assert (
            _emotion(**COMPETITOR, confidence=confidence, composure=composure, energy=energy)
            == expected
        )

    def test_stoic_compresses_toward_poker_face(self):
        # Low-energy stoic in a calm-ish quadrant reads muted.
        assert _emotion(**STOIC, confidence=0.3, composure=0.7, energy=0.3) == 'poker_face'


class TestSelfBelief:
    """self_belief is the bravado dial — raises confidence without touching ego."""

    BASE = dict(
        baseline_aggression=0.15,
        baseline_looseness=0.85,
        ego=0.2,
        poise=0.6,
        expressiveness=0.8,
        risk_identity=0.4,
        adaptation_bias=0.0,
        baseline_energy=0.7,
        recovery_rate=0.3,
    )

    def test_raises_confidence(self):
        low = compute_baseline_confidence(
            PersonalityAnchors.from_dict({**self.BASE, 'self_belief': 0.5})
        )
        high = compute_baseline_confidence(
            PersonalityAnchors.from_dict({**self.BASE, 'self_belief': 0.85})
        )
        assert high > low

    def test_absent_defaults_neutral(self):
        # Legacy personas (no self_belief key) get the neutral 0.5 default -> no offset.
        anc = PersonalityAnchors.from_dict(self.BASE)  # no self_belief key
        assert anc.self_belief == 0.5

    def test_clamp_keeps_out_of_overconfident_zone(self):
        maxed = compute_baseline_confidence(
            PersonalityAnchors.from_dict(
                {**self.BASE, 'ego': 0.9, 'risk_identity': 0.9, 'self_belief': 1.0}
            )
        )
        assert maxed <= 0.80  # below the OVERCONFIDENT penalty threshold


class TestTouristFamilyAssignments:
    """Regression guard for the curated tourist trait assignments."""

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("Vacation Greg", "fun_lover"),
            ("Lucky Mona", "fun_lover"),
            ("Slots Linda", "fun_lover"),
            ("Birthday Bobby", "fun_lover"),  # ego lowered from 0.50 -> carefree, not anxious
            ("Golf Trip Brad", "fun_lover"),  # ego lowered from 0.45
            ("Bachelorette Brenda", "fun_lover"),  # ego lowered from 0.40
            ("Cruise Carl", "competitor"),
            ("After Hours Trent", "competitor"),
            ("Freddie Fratboy", "competitor"),
        ],
    )
    def test_tourist_family(self, name, expected):
        anc = PersonalityAnchors.from_dict(_PERSONALITIES[name]['anchors'])
        assert get_emotion_family(anc).value == expected

    def test_fun_lover_tourists_are_not_timid(self):
        # The cheerful tourists should rest in COMMANDING (confident), not GUARDED.
        from poker.psychology_model import (
            EmotionalQuadrant,
            compute_baseline_composure,
            get_quadrant,
        )

        for name in ("Vacation Greg", "Lucky Mona", "Birthday Bobby"):
            anc = PersonalityAnchors.from_dict(_PERSONALITIES[name]['anchors'])
            conf = compute_baseline_confidence(anc)
            comp = compute_baseline_composure(anc)
            assert get_quadrant(conf, comp) == EmotionalQuadrant.COMMANDING, name


def _emotion_fish(is_fish, ego, expressiveness, confidence, composure, energy):
    config = {'anchors': _anchors(ego, expressiveness, energy).to_dict()}
    if is_fish:
        config['archetype'] = 'fish'
    psych = PlayerPsychology.from_personality_config('Tester', config)
    psych.axes = EmotionalAxes(confidence=confidence, composure=composure, energy=energy)
    return psych.get_display_emotion(use_expression_filter=False)


class TestFishCheerfulLoss:
    """Canon: a fish never figures out he's the mark — even losing, he's happy
    ('Aw, ya got me! Deal again, deal again.'). So a SHAKEN fish reads cheerful,
    not sheepish. Ordinary fun-lovers still feel the oops."""

    SHAKEN = dict(ego=0.2, expressiveness=0.85, confidence=0.3, composure=0.3)

    def test_ordinary_fun_lover_is_sheepish_when_shaken(self):
        assert _emotion_fish(False, energy=0.8, **self.SHAKEN) == 'sheepish'

    def test_fish_stays_cheerful_when_shaken(self):
        assert _emotion_fish(True, energy=0.8, **self.SHAKEN) == 'gleeful'  # high energy
        assert _emotion_fish(True, energy=0.4, **self.SHAKEN) == 'happy'  # low energy

    def test_fish_never_sheepish(self):
        for e in (0.2, 0.5, 0.9):
            assert _emotion_fish(True, energy=e, **self.SHAKEN) != 'sheepish'
