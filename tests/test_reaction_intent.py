"""Tests for the emoji-reaction weighted-pool roller.

Two invariants matter here:

  1. Every emoji a pool can produce maps to a `RelationshipEvent` of
     the correct polarity (positive → friendly/compliment family,
     negative → trash-talk/taunt family). A misrouted entry would
     silently invert the relationship signal.
  2. The roll is reproducible when a seeded `random.Random` is passed,
     so other tests (and future replay paths) can pin the rolled
     emoji without coupling to wall-clock RNG state.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from poker.memory.reaction_intent import (
    SENTIMENT_NEGATIVE,
    SENTIMENT_POSITIVE,
    is_valid_sentiment,
    pool_emojis,
    roll_reaction,
)
from poker.memory.relationship_events import RelationshipEvent


POSITIVE_EVENTS = {
    RelationshipEvent.COMPLIMENT,
    RelationshipEvent.FRIENDLY_BANTER,
}
NEGATIVE_EVENTS = {
    RelationshipEvent.TRASH_TALK,
    RelationshipEvent.TAUNT_POST_WIN,
}


class TestSentimentValidation:
    def test_positive_is_valid(self):
        assert is_valid_sentiment(SENTIMENT_POSITIVE)

    def test_negative_is_valid(self):
        assert is_valid_sentiment(SENTIMENT_NEGATIVE)

    def test_none_is_invalid(self):
        assert not is_valid_sentiment(None)

    def test_garbage_is_invalid(self):
        assert not is_valid_sentiment("neutral")

    def test_empty_string_is_invalid(self):
        assert not is_valid_sentiment("")


class TestRoll:
    def test_unknown_sentiment_returns_none(self):
        assert roll_reaction("neutral") is None

    def test_positive_rolls_positive_event(self):
        rng = random.Random(1)
        # Roll a handful — every result must belong to the positive
        # event family or there's a misrouted pool entry.
        for _ in range(50):
            roll = roll_reaction(SENTIMENT_POSITIVE, rng=rng)
            assert roll is not None
            assert roll.event in POSITIVE_EVENTS
            assert roll.emoji in pool_emojis(SENTIMENT_POSITIVE)
            assert 0.0 < roll.multiplier <= 1.0

    def test_negative_rolls_negative_event(self):
        rng = random.Random(2)
        for _ in range(50):
            roll = roll_reaction(SENTIMENT_NEGATIVE, rng=rng)
            assert roll is not None
            assert roll.event in NEGATIVE_EVENTS
            assert roll.emoji in pool_emojis(SENTIMENT_NEGATIVE)
            assert 0.0 < roll.multiplier <= 1.0

    def test_seeded_rng_is_deterministic(self):
        # Two independent rolls with the same seed produce the same
        # sequence — required so replay/tests can pin the rolled emoji.
        rng_a = random.Random(42)
        rng_b = random.Random(42)
        seq_a = [roll_reaction(SENTIMENT_POSITIVE, rng=rng_a).emoji for _ in range(10)]
        seq_b = [roll_reaction(SENTIMENT_POSITIVE, rng=rng_b).emoji for _ in range(10)]
        assert seq_a == seq_b

    def test_weighting_skews_to_modal_emoji(self):
        # The 😂 entry has weight 3 vs 2/2/2/1 for the others, so over
        # many rolls it should appear roughly 3/10 of the time. We
        # don't pin an exact frequency (RNG variance) — just check
        # it's the most common outcome by a comfortable margin.
        rng = random.Random(7)
        rolls = [roll_reaction(SENTIMENT_POSITIVE, rng=rng).emoji for _ in range(1000)]
        counts = Counter(rolls)
        most_common, _ = counts.most_common(1)[0]
        assert most_common == "😂"
        assert counts["😂"] > 200  # ≥20% — well above the 10% of the rarest entry

    def test_negative_modal_is_bored(self):
        # 😴 has the highest weight (3) in the negative pool.
        rng = random.Random(11)
        rolls = [roll_reaction(SENTIMENT_NEGATIVE, rng=rng).emoji for _ in range(1000)]
        most_common, _ = Counter(rolls).most_common(1)[0]
        assert most_common == "😴"


class TestPoolEmojis:
    def test_positive_pool_emojis_are_distinct(self):
        emojis = pool_emojis(SENTIMENT_POSITIVE)
        assert len(emojis) == len(set(emojis))

    def test_negative_pool_emojis_are_distinct(self):
        emojis = pool_emojis(SENTIMENT_NEGATIVE)
        assert len(emojis) == len(set(emojis))

    def test_pools_do_not_overlap(self):
        # An emoji appearing in both pools would let a positive click
        # land in the negative pool's sentiment-event mapping — a
        # silent inversion bug. Keep them disjoint.
        positive = set(pool_emojis(SENTIMENT_POSITIVE))
        negative = set(pool_emojis(SENTIMENT_NEGATIVE))
        assert positive.isdisjoint(negative)

    def test_unknown_sentiment_returns_empty(self):
        assert pool_emojis("neutral") == []
