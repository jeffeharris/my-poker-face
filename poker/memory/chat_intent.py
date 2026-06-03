"""Quick-chat tone → RelationshipEvent mapping.

The React quick-chat UI collects structured `(tone, intensity, target)`
intent at message-send time. Because the user already declares what
kind of message they're sending, no LLM categorization is needed —
the tone string is the categorization, and a direct mapping suffices.

Two vocabularies feed this module:

  - Mid-hand `ChatTone`: tilt, bait, needle, goad, bluff, befriend, props
  - Post-round `PostRoundTone`: gloat, humble, salty, gracious, props

Plus a global `ChatIntensity` modifier (`chill` / `spicy`) applied
only to mid-hand tones — post-round tones use the implicit intensity
already encoded in the tone choice (gloat is always full-intensity,
gracious is always full-intensity, etc.).

The mapping is intentionally lossy: four hostile mid-hand tones all
collapse to `TRASH_TALK`, distinguished by the multiplier. If
play-data tuning later shows the four need separate calibration,
they can be promoted to their own events without changing call
sites — the helper is the only place that needs an update.

Spec: chat-event family in `poker/memory/relationship_events.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .relationship_events import RelationshipEvent


@dataclass(frozen=True)
class ChatEventMapping:
    """Result of mapping a quick-chat tone to a relationship event.

    `multiplier` scales the dispatch-table axis shifts via
    `OpponentModelManager.record_event(context_multiplier=…)`. A value
    of 1.0 applies the full calibrated shift; 0.5 applies half. This
    is the lever that distinguishes `goad` (full TRASH_TALK) from
    `needle` (half TRASH_TALK) when both map to the same event.
    """

    event: RelationshipEvent
    multiplier: float


# Mid-hand tone → (event, base multiplier). The base multiplier is
# composed with the intensity modifier when the tone is mid-hand.
#
# `bluff` is intentionally absent — verbal bluffing is about the
# speaker's own hand, not the opponent, so it doesn't move relationship
# axes. Sends with tone="bluff" return None from `map_tone`.
_MID_HAND_TONE_MAP: dict[str, ChatEventMapping] = {
    # Canonical hostile tone (the consolidation of the retired
    # tilt/goad/needle/bait near-duplicates, all of which still resolve here).
    "trash_talk": ChatEventMapping(RelationshipEvent.TRASH_TALK, 1.0),
    "tilt": ChatEventMapping(RelationshipEvent.TRASH_TALK, 1.0),
    "goad": ChatEventMapping(RelationshipEvent.TRASH_TALK, 1.0),
    "needle": ChatEventMapping(RelationshipEvent.TRASH_TALK, 0.5),
    "bait": ChatEventMapping(RelationshipEvent.TRASH_TALK, 0.5),
    "befriend": ChatEventMapping(RelationshipEvent.FRIENDLY_BANTER, 1.0),
    "props": ChatEventMapping(RelationshipEvent.PROPS, 1.0),
    # NOTE: `intimidate` and `dare` are intentionally absent — they're
    # emotional-layer tones (move composure/confidence, not relationship
    # axes) and are dispatched directly in chat_relationship.py, not here.
    # `flatter` is also absent (its own valence-flipping path).
}


# Tone → sarcasm surface-direction mode, used only when the delivery register
# is `sarcastic`. The mode selects which reception transform applies (see
# `sarcasm_mirror_shift` in relationship_events.py): a warm tone sharpens into
# a barb, a hostile one softens into banter, a self-directed one becomes
# self-mockery. Tones absent here take no sarcastic variant.
_SARCASM_MODE_BY_TONE: dict[str, str] = {
    # Mid-hand
    "trash_talk": "soften",   # → banter
    "props": "sharpen",       # → backhanded "nice play"
    "flatter": "sharpen",     # → mocking (resolved on the flattery path)
    # Post-round
    "gracious": "sharpen",    # → fake-nice "wp"
    "commiserate": "sharpen", # → fake sympathy
    "humble": "self",         # → dry self-deprecation
}


def sarcasm_mode_for_tone(tone: Optional[str]) -> Optional[str]:
    """Return the sarcasm surface-direction mode for a tone, or None.

    None means the tone has no sarcastic variant (the emotional-layer tones
    and `bluff`), so a `sarcastic` register on it should fall back to the
    neutral reception. Keyed by tone, not event, because the same event
    (e.g. FRIENDLY_BANTER) backs both a warm tone and a self-directed one.
    """
    if tone is None:
        return None
    return _SARCASM_MODE_BY_TONE.get(tone)


# Post-round tone → (event, multiplier). No intensity modifier applies
# here — post-round tones encode their own intensity in the choice.
_POST_ROUND_TONE_MAP: dict[str, ChatEventMapping] = {
    "gloat": ChatEventMapping(RelationshipEvent.TAUNT_POST_WIN, 1.0),
    "humble": ChatEventMapping(RelationshipEvent.FRIENDLY_BANTER, 1.0),
    "salty": ChatEventMapping(RelationshipEvent.TRASH_TALK, 1.0),
    "gracious": ChatEventMapping(RelationshipEvent.COMPLIMENT, 1.0),
    "props": ChatEventMapping(RelationshipEvent.PROPS, 1.0),
    # Consoling a bystander who lost — the warm color aimed at someone other
    # than the player who beat you.
    "commiserate": ChatEventMapping(RelationshipEvent.COMMISERATE, 1.0),
    # NOTE: `cry_luck` and `vow` are post-round EMOTIONAL tones (ego poke /
    # poise rattle on the winner) and are dispatched to psychology in
    # chat_relationship.py, not mapped here.
}


# Intensity multiplier — mid-hand only. `chill` halves the axis impact,
# `spicy` is full strength. Unknown / missing intensity defaults to
# `spicy` (1.0×) — the safer end of the lever (don't silently swallow
# axis movement when the field is omitted).
_INTENSITY_MULT: dict[str, float] = {
    "chill": 0.5,
    "spicy": 1.0,
}
_DEFAULT_INTENSITY_MULT = 1.0


def map_tone(
    tone: Optional[str],
    intensity: Optional[str] = None,
) -> Optional[ChatEventMapping]:
    """Map a quick-chat tone string to a relationship event mapping.

    Returns `None` when the tone has no relationship-axis effect
    (unknown tone, missing tone, or `bluff` — see module docstring).
    Returning None is the explicit "no axis movement" signal; callers
    should skip the `record_event` dispatch in that case rather than
    fire a zero-impact event.

    Composition rule:
      - Mid-hand tones: final_multiplier = tone_base × intensity_modifier
      - Post-round tones: intensity is ignored; tone_base is the full
        multiplier. (Post-round tones encode intensity in the choice
        itself — `gloat` doesn't have a `chill` variant.)

    Both vocabularies share this single entry point because the
    `ChatTone` and `PostRoundTone` enums don't overlap — a tone string
    unambiguously belongs to one vocabulary.
    """
    if tone is None:
        return None

    mid_hand = _MID_HAND_TONE_MAP.get(tone)
    if mid_hand is not None:
        intensity_mult = _INTENSITY_MULT.get(intensity, _DEFAULT_INTENSITY_MULT)
        return ChatEventMapping(
            event=mid_hand.event,
            multiplier=mid_hand.multiplier * intensity_mult,
        )

    post_round = _POST_ROUND_TONE_MAP.get(tone)
    if post_round is not None:
        return post_round

    return None
