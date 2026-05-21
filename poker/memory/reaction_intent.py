"""Emoji-reaction → RelationshipEvent weighted roll.

Players express engagement with AI chat messages via two buttons
(positive / negative). On each click the server rolls a weighted pool
of `(emoji, RelationshipEvent, multiplier)` entries to choose a
specific reaction. The emoji shown to all players is the same one that
drove the axis shift, so the on-screen variety matches the variety of
signal flowing into the relationship layer.

Why weighted rather than uniform: most rolls should be mild (the modal
outcome of a positive reaction is "you're funny" — `FRIENDLY_BANTER`).
The occasional spicy outcome (😠 full hostile, ❤️ full warm) is rare
enough to feel meaningful when it lands. This shape avoids the failure
mode where a player spamming the same button quickly saturates an axis.

Mirrors the layout of `chat_intent.py` — both modules collapse a UI-
layer signal to `(RelationshipEvent, multiplier)`. The two stay
separate because reactions and typed quick-chat have categorically
different trigger mechanisms and tuning knobs.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .relationship_events import RelationshipEvent


# Sentiment vocabulary — the two-button UI sends one of these.
SENTIMENT_POSITIVE = "positive"
SENTIMENT_NEGATIVE = "negative"
VALID_SENTIMENTS = frozenset({SENTIMENT_POSITIVE, SENTIMENT_NEGATIVE})


@dataclass(frozen=True)
class ReactionEntry:
    """One row of a sentiment pool: emoji + axis shift + roll weight."""
    emoji: str
    event: RelationshipEvent
    multiplier: float
    weight: int


@dataclass(frozen=True)
class ReactionRoll:
    """Result of rolling a sentiment pool — the emoji shown plus the
    relationship-axis instructions for `record_event`.
    """
    emoji: str
    event: RelationshipEvent
    multiplier: float


# Positive pool. Skewed toward `FRIENDLY_BANTER` outcomes (likability-
# only) because the modal positive reaction is "I enjoyed that", not
# "I deeply respect you". The respect-bearing `COMPLIMENT` entries are
# the lower-weight, higher-impact rolls.
_POSITIVE_POOL: Sequence[ReactionEntry] = (
    ReactionEntry("😂", RelationshipEvent.FRIENDLY_BANTER, 1.0, weight=3),
    ReactionEntry("👏", RelationshipEvent.COMPLIMENT,      0.7, weight=2),
    ReactionEntry("❤️", RelationshipEvent.COMPLIMENT,      1.0, weight=2),
    ReactionEntry("🔥", RelationshipEvent.COMPLIMENT,      0.8, weight=2),
    ReactionEntry("👍", RelationshipEvent.FRIENDLY_BANTER, 0.7, weight=1),
)


# Negative pool. Skewed toward mild dismissals (😴 bored, 🙄 eye-roll)
# because the modal negative reaction is "meh", not "I hate you". The
# spicy 😠 entry is rare but applies the full `TRASH_TALK` shift.
_NEGATIVE_POOL: Sequence[ReactionEntry] = (
    ReactionEntry("😴", RelationshipEvent.TRASH_TALK, 0.3, weight=3),
    ReactionEntry("🙄", RelationshipEvent.TRASH_TALK, 0.5, weight=2),
    ReactionEntry("👎", RelationshipEvent.TRASH_TALK, 0.6, weight=2),
    ReactionEntry("😬", RelationshipEvent.TRASH_TALK, 0.4, weight=1),
    ReactionEntry("😠", RelationshipEvent.TRASH_TALK, 1.0, weight=2),
)


_POOL_BY_SENTIMENT = {
    SENTIMENT_POSITIVE: _POSITIVE_POOL,
    SENTIMENT_NEGATIVE: _NEGATIVE_POOL,
}


def is_valid_sentiment(sentiment: Optional[str]) -> bool:
    """Boundary check for route validation."""
    return sentiment in VALID_SENTIMENTS


def roll_reaction(
    sentiment: str,
    *,
    rng: Optional[random.Random] = None,
) -> Optional[ReactionRoll]:
    """Roll a weighted entry from the sentiment's pool.

    `rng` defaults to the global `random` module. Tests pass a seeded
    `random.Random` to make the roll deterministic without mutating
    the global RNG (matches the functional-purity convention from the
    project's coding style guide).

    Returns `None` for an unknown sentiment so callers can collapse
    "unknown payload" and "no axis movement" into a single early-out
    without a try/except.
    """
    pool = _POOL_BY_SENTIMENT.get(sentiment)
    if not pool:
        return None
    picker = rng if rng is not None else random
    entry = picker.choices(
        pool, weights=[e.weight for e in pool], k=1,
    )[0]
    return ReactionRoll(
        emoji=entry.emoji,
        event=entry.event,
        multiplier=entry.multiplier,
    )


def pool_emojis(sentiment: str) -> List[str]:
    """Return the emojis a given sentiment can produce.

    Exposed for the route validator — when the client echoes back the
    emoji it received (debug paths, optimistic-UI sync), the validator
    can confirm the emoji belongs to the declared sentiment.
    """
    pool = _POOL_BY_SENTIMENT.get(sentiment)
    return [e.emoji for e in pool] if pool else []
