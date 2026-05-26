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
