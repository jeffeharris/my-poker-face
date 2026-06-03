"""Tests for the quick-chat tone → RelationshipEvent mapping.

Locks in the calibration table from `poker/memory/chat_intent.py`:
mid-hand tones with intensity composition, post-round tones (no
intensity), and the documented no-op cases (bluff, unknown, None).
"""

from __future__ import annotations

import pytest

from poker.memory.chat_intent import ChatEventMapping, map_tone
from poker.memory.relationship_events import RelationshipEvent


class TestMidHandTones:
    @pytest.mark.parametrize(
        "tone, expected_event, base_mult",
        [
            ("tilt", RelationshipEvent.TRASH_TALK, 1.0),
            ("goad", RelationshipEvent.TRASH_TALK, 1.0),
            ("needle", RelationshipEvent.TRASH_TALK, 0.5),
            ("bait", RelationshipEvent.TRASH_TALK, 0.5),
            ("befriend", RelationshipEvent.FRIENDLY_BANTER, 1.0),
        ],
    )
    def test_spicy_intensity_applies_full_base_multiplier(
        self,
        tone,
        expected_event,
        base_mult,
    ):
        result = map_tone(tone, intensity="spicy")
        assert result is not None
        assert result.event is expected_event
        assert result.multiplier == pytest.approx(base_mult)

    @pytest.mark.parametrize(
        "tone, expected_event, base_mult",
        [
            ("tilt", RelationshipEvent.TRASH_TALK, 1.0),
            ("needle", RelationshipEvent.TRASH_TALK, 0.5),
            ("befriend", RelationshipEvent.FRIENDLY_BANTER, 1.0),
        ],
    )
    def test_chill_intensity_halves_multiplier(
        self,
        tone,
        expected_event,
        base_mult,
    ):
        result = map_tone(tone, intensity="chill")
        assert result is not None
        assert result.event is expected_event
        assert result.multiplier == pytest.approx(base_mult * 0.5)

    def test_missing_intensity_defaults_to_full(self):
        # No intensity passed → spicy-equivalent (1.0). Conservative
        # default — don't silently swallow axis movement.
        result = map_tone("goad", intensity=None)
        assert result == ChatEventMapping(RelationshipEvent.TRASH_TALK, 1.0)

    def test_unknown_intensity_defaults_to_full(self):
        result = map_tone("goad", intensity="medium")
        assert result == ChatEventMapping(RelationshipEvent.TRASH_TALK, 1.0)


class TestPostRoundTones:
    @pytest.mark.parametrize(
        "tone, expected_event",
        [
            ("gloat", RelationshipEvent.TAUNT_POST_WIN),
            ("humble", RelationshipEvent.FRIENDLY_BANTER),
            ("salty", RelationshipEvent.TRASH_TALK),
            ("gracious", RelationshipEvent.COMPLIMENT),
        ],
    )
    def test_post_round_tone_maps_at_full_multiplier(
        self,
        tone,
        expected_event,
    ):
        result = map_tone(tone)
        assert result is not None
        assert result.event is expected_event
        assert result.multiplier == pytest.approx(1.0)

    def test_post_round_ignores_intensity(self):
        # Post-round tones encode their own intensity. Passing
        # intensity should not change the multiplier.
        chill_gloat = map_tone("gloat", intensity="chill")
        spicy_gloat = map_tone("gloat", intensity="spicy")
        assert chill_gloat == spicy_gloat
        assert chill_gloat.multiplier == pytest.approx(1.0)


class TestNoEffectTones:
    def test_bluff_returns_none(self):
        # Verbal bluffing is about the speaker's own hand, not the
        # opponent — documented no-op.
        assert map_tone("bluff") is None
        assert map_tone("bluff", intensity="spicy") is None

    def test_none_tone_returns_none(self):
        assert map_tone(None) is None

    def test_unknown_tone_returns_none(self):
        assert map_tone("snarky") is None
        assert map_tone("") is None


class TestCanonicalTrashTalk:
    def test_trash_talk_maps_to_full_trash_talk(self):
        result = map_tone("trash_talk", intensity="spicy")
        assert result is not None
        assert result.event is RelationshipEvent.TRASH_TALK
        assert result.multiplier == pytest.approx(1.0)

    def test_trash_talk_takes_intensity(self):
        assert map_tone("trash_talk", intensity="chill").multiplier == pytest.approx(0.5)

    def test_emotional_tones_have_no_relationship_mapping(self):
        # intimidate/dare are dispatched to psychology, never to the repo.
        assert map_tone("intimidate", intensity="spicy") is None
        assert map_tone("dare", intensity="spicy") is None


class TestSarcasmMode:
    def test_warm_tones_sharpen(self):
        from poker.memory.chat_intent import sarcasm_mode_for_tone

        assert sarcasm_mode_for_tone("props") == "sharpen"
        assert sarcasm_mode_for_tone("gracious") == "sharpen"

    def test_hostile_softens(self):
        from poker.memory.chat_intent import sarcasm_mode_for_tone

        assert sarcasm_mode_for_tone("trash_talk") == "soften"

    def test_self_directed(self):
        from poker.memory.chat_intent import sarcasm_mode_for_tone

        assert sarcasm_mode_for_tone("humble") == "self"

    def test_emotional_and_unknown_have_no_mode(self):
        from poker.memory.chat_intent import sarcasm_mode_for_tone

        assert sarcasm_mode_for_tone("intimidate") is None
        assert sarcasm_mode_for_tone("dare") is None
        assert sarcasm_mode_for_tone("befriend") is None  # sincere-only for now
        assert sarcasm_mode_for_tone(None) is None
        assert sarcasm_mode_for_tone("nonsense") is None
