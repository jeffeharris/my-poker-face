"""Relationship event taxonomy and dispatch tables.

The relationship layer's only legal axis-mutation entry point is
`OpponentModelManager.record_event(actor, target, event, ...)`. Each
event maps to two sets of axis shifts:

  - **Actor's-POV** — how the actor's view of the target moves.
    Stored in `ACTOR_AXIS_SHIFTS`. e.g. when the actor takes a bad
    beat against the target, the actor's heat toward target goes up.

  - **Mirror (target's-POV)** — how the target's view of the actor
    moves. Stored in `MIRROR_AXIS_SHIFTS`. e.g. the target (the one
    who hit the lucky card) feels mildly awkward about the unearned
    win and their likability toward actor dips slightly.

Both tables are keyed on `RelationshipEvent` and yield an
`AxisShift` dataclass. A poker outcome is one event with two views;
the bilateral update inside `record_event` looks up both rows in a
single call so the actor-side and target-side rows can never drift.

**UNKNOWN sentinel.** Legacy `memorable_hands` rows may contain
event strings older than this enum. On load, an unrecognized string
is coerced to `RelationshipEvent.UNKNOWN`, which has explicit
zero-shift entries in both tables. The result: old data loads
without crashing and without silently moving axes from values we
can't account for. A one-shot offline migration script enumerates
the corpus and either maps unknowns to known events or drops the
rows; that's separate from the load-path safety net here.

Spec: `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 1 (Event
vocabulary and Event → axis shift dispatch sections). The numeric
shift values are the **starting calibration** from the design doc.
Both tables are tunable from play data without changing the enum
or its consumers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict


class RelationshipEvent(Enum):
    """Canonical taxonomy of events that move relationship axes.

    The `.value` strings are the canonical DB representation written
    to the `memorable_hands.memory_type` column (column name kept for
    schema-compat; semantically it now holds `event.value`).

    New events go in `Hand-outcome events` if they're emitted by
    `HandOutcomeDetector` from gameplay, or `Chat events` if they're
    emitted by the chat categorizer. Economy events (staking,
    unlocks, private games) get their own `EconomyEvent` taxonomy
    when those systems ship — keeping this enum narrow is
    deliberate.
    """

    # Hand-outcome events (existing memorable_hand types)
    BLUFFED_OFF = "bluffed_off"
    HERO_CALL = "hero_call"
    BIG_LOSS = "big_loss"
    BIG_WIN = "big_win"
    BAD_BEAT = "bad_beat"
    DOMINATED_SHOWDOWN = "dominated_showdown"
    STRONG_FOLD_SHOWN = "strong_fold_shown"
    COOLER = "cooler"

    # Chat events. Emitted synchronously from the quick-chat dispatch
    # path (`flask_app/handlers/chat_relationship.py`) when the UI sends
    # a structured `(tone, target)` message — no LLM categorizer needed.
    TRASH_TALK = "chat_trash_talk"
    COMPLIMENT = "chat_compliment"
    TAUNT_POST_WIN = "chat_taunt_post_win"
    FRIENDLY_BANTER = "chat_friendly_banter"
    # Tipping your cap — genuine respect for an opponent's play. Distinct
    # from COMPLIMENT (likability-weighted warmth): PROPS is respect-weighted,
    # the one chat lever that meaningfully raises the respect axis.
    PROPS = "chat_props"
    # Flattery — insincere / over-the-top praise. The valence flips by the
    # TARGET's vanity, so the dispatch picks which of these fires per-target:
    # LANDED on the vain (charmed → likability up), BACKFIRED on the perceptive
    # (they catch the ploy → respect + likability down).
    FLATTERY_LANDED = "chat_flattery_landed"
    FLATTERY_BACKFIRED = "chat_flattery_backfired"

    # Cash-mode staking events. The "actor" is the AI staker (extending
    # the stake, being repaid, or being defaulted on), the "target" is
    # the borrower (player in Phase 1; AIs join in Phase 4, humans
    # become stakers in Phase 5).
    STAKE_OFFERED = "stake_offered"
    STAKE_REPAID = "stake_repaid"
    STAKE_DEFAULTED = "stake_defaulted"
    STAKE_FORGIVEN = "stake_forgiven"
    STAKE_FORGIVENESS_REFUSED = "stake_forgiveness_refused"

    # Cash-mode table-dynamics event. Fires once per hand per
    # (observer, deep_stack) pair when a seated peer has a stack
    # significantly above the table max buy-in AND the observer has
    # negative cumulative_pnl against them in this sandbox. Actor =
    # observer (the one losing respect); target = deep stack. See
    # `HandOutcomeDetector._detect_stack_dominance` for the gating.
    STACK_DOMINANCE = "stack_dominance"

    # Quarantine sentinel for unknown strings encountered on load.
    # Has zero entries in both dispatch tables — `record_event` with
    # this value is a documented no-op.
    UNKNOWN = "_unknown"

    @classmethod
    def from_string(cls, value: str) -> RelationshipEvent:
        """Parse a DB-side memory_type string into the enum.

        Unknown strings coerce to `UNKNOWN` rather than raising — this
        is the load-path safety net for legacy `memorable_hands`
        rows. Callers that need strict parsing should construct
        `RelationshipEvent(value)` directly and handle `ValueError`.
        """
        try:
            return cls(value)
        except ValueError:
            return cls.UNKNOWN


@dataclass(frozen=True)
class AxisShift:
    """Per-event axis deltas, in absolute units against a [0,1] scale.

    Positive `heat` means the observer is more hostile toward the
    target after the event; negative cools them off. Respect and
    likability are bounded [0,1] with 0.5 default neutrality;
    heat is bounded [0,1] with 0.0 default (one-sided axis).
    Clamping is the caller's responsibility — these are raw deltas.
    """

    heat: float = 0.0
    respect: float = 0.0
    likability: float = 0.0


# Actor's-POV: how the actor's view of the target moves.
#
# Starting calibration from `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md`
# Part 1 "Event → axis shift dispatch" table. Numbers are tunable
# from play data — they live in code rather than config because
# tuning them changes behavior shape, not deployment config.
ACTOR_AXIS_SHIFTS: Dict[RelationshipEvent, AxisShift] = {
    # Hand-outcome events
    RelationshipEvent.BLUFFED_OFF: AxisShift(heat=+0.20, respect=-0.05, likability=-0.02),
    RelationshipEvent.HERO_CALL: AxisShift(heat=-0.05, respect=-0.10, likability=+0.01),
    RelationshipEvent.BIG_LOSS: AxisShift(heat=+0.15, respect=+0.08, likability=-0.05),
    RelationshipEvent.BIG_WIN: AxisShift(heat=-0.10, respect=-0.05, likability=+0.02),
    RelationshipEvent.BAD_BEAT: AxisShift(heat=+0.30, respect=-0.15, likability=-0.10),
    RelationshipEvent.DOMINATED_SHOWDOWN: AxisShift(heat=0.00, respect=-0.15, likability=0.00),
    RelationshipEvent.STRONG_FOLD_SHOWN: AxisShift(heat=0.00, respect=+0.10, likability=0.00),
    # COOLER actor: the loser brought a strong hand and ran into a
    # stronger one. Emotional signature differs from BAD_BEAT (no
    # equity injustice) and from DOMINATED_SHOWDOWN (where the loser
    # didn't have much to begin with) — "I had it, they had more."
    # Heat ticks up (frustration of losing a real hand), respect up
    # (winner had even more), likability down slightly (no malice).
    # Starting calibration; tune from play data once distribution vs
    # BAD_BEAT and DOMINATED_SHOWDOWN is visible.
    RelationshipEvent.COOLER: AxisShift(heat=+0.10, respect=+0.10, likability=-0.05),
    # Chat events
    RelationshipEvent.TRASH_TALK: AxisShift(heat=+0.10, respect=0.00, likability=-0.05),
    RelationshipEvent.COMPLIMENT: AxisShift(heat=0.00, respect=+0.03, likability=+0.05),
    RelationshipEvent.TAUNT_POST_WIN: AxisShift(heat=+0.20, respect=0.00, likability=-0.10),
    RelationshipEvent.FRIENDLY_BANTER: AxisShift(heat=0.00, respect=0.00, likability=+0.03),
    RelationshipEvent.PROPS: AxisShift(heat=0.00, respect=+0.10, likability=+0.02),
    RelationshipEvent.FLATTERY_LANDED: AxisShift(heat=0.00, respect=0.00, likability=+0.02),
    RelationshipEvent.FLATTERY_BACKFIRED: AxisShift(heat=0.00, respect=0.00, likability=-0.02),
    # Cash-mode staking. Actor = AI staker; their view of the borrower
    # moves on stake lifecycle events.
    #   STAKE_OFFERED: staker extends trust → small respect bump, small
    #     likability bump (offering a stake is a positive gesture).
    #   STAKE_REPAID: borrower returned principal + cut → respect +
    #     likability up, heat cools slightly (any prior friction
    #     abated by payment).
    #   STAKE_DEFAULTED: borrower stiffed the staker (explicit default
    #     action — passive carries don't fire this) → respect plummets,
    #     heat surges, likability drops. The sharpest axis hit in the
    #     starting calibration — defaulting is the worst thing a
    #     borrower can do to a staker.
    #   STAKE_FORGIVEN: staker wrote off a carry on request → small
    #     heat drop, small respect/likability bump (forgiveness reads
    #     as generous, not pushover, at this magnitude).
    RelationshipEvent.STAKE_OFFERED: AxisShift(heat=0.00, respect=+0.05, likability=+0.03),
    RelationshipEvent.STAKE_REPAID: AxisShift(heat=-0.05, respect=+0.15, likability=+0.10),
    RelationshipEvent.STAKE_DEFAULTED: AxisShift(heat=+0.30, respect=-0.30, likability=-0.20),
    RelationshipEvent.STAKE_FORGIVEN: AxisShift(heat=-0.10, respect=+0.05, likability=+0.05),
    # STAKE_FORGIVENESS_REFUSED: borrower asked for forgiveness and the
    # staker refused. Small actor-side hit — "you have some nerve
    # asking" — without escalating to the full STAKE_DEFAULTED magnitude
    # (defaulting is a much sharper transgression than asking). Actor
    # is staker, target is borrower; the staker's view of the borrower
    # drops slightly. Mirror (borrower's view of staker) shifts only
    # mildly — the borrower wasn't expecting the ask to be granted, but
    # being refused does cool their warmth toward the staker a touch.
    RelationshipEvent.STAKE_FORGIVENESS_REFUSED: AxisShift(
        heat=+0.02,
        respect=0.00,
        likability=-0.05,
    ),
    # STACK_DOMINANCE: observer (actor) resents a deep-stacked peer
    # (target). Per-hand drip, scaled at dispatch time by the deep
    # stack's excess above the 1.5× max-buy-in threshold via the
    # `context_multiplier` arg on `record_event`. So at 2× cap the
    # effective per-hand shift is half these values; at 3× cap, 1.5×.
    # Heat stays at zero — envy is not hostility, and a non-zero heat
    # would unintentionally trigger `RelationshipModifier`'s "rival"
    # exploitation path. Small respect + likability drips compose into
    # a meaningful pair-state shift only after sustained co-presence.
    RelationshipEvent.STACK_DOMINANCE: AxisShift(
        heat=0.00,
        respect=-0.002,
        likability=-0.003,
    ),
    # Quarantine — no axis impact
    RelationshipEvent.UNKNOWN: AxisShift(),
}


# Mirror (target's-POV): how the target's view of the actor moves.
#
# A poker outcome is one event with two views. Mirror shifts are
# generally smaller than actor shifts because the actor is the one
# directly experiencing the outcome; the target experiences only
# the secondary effect (witnessing the actor's reaction or
# benefitting/suffering from the outcome).
#
# The BAD_BEAT mirror is the design-doc canonical example:
#   target (the lucky winner) → heat 0, respect +0.05 (feared
#   actor as a tough opponent), likability −0.05 (unearned win
#   feels awkward). Other mirrors derive from poker semantics
#   in the same shape and are tunable.
#
# Chat-event mirrors capture how the target reacts to receiving
# the message: trash talk increases their heat toward speaker;
# compliments warm them up.
MIRROR_AXIS_SHIFTS: Dict[RelationshipEvent, AxisShift] = {
    # Hand-outcome events. Mirror is the OPPONENT of the actor —
    # if actor was bluffed off, target is the one who bluffed them.
    RelationshipEvent.BLUFFED_OFF: AxisShift(heat=0.00, respect=0.00, likability=0.00),
    RelationshipEvent.HERO_CALL: AxisShift(heat=+0.10, respect=+0.05, likability=0.00),
    RelationshipEvent.BIG_LOSS: AxisShift(heat=-0.05, respect=0.00, likability=+0.02),
    RelationshipEvent.BIG_WIN: AxisShift(heat=+0.10, respect=0.00, likability=-0.02),
    RelationshipEvent.BAD_BEAT: AxisShift(heat=0.00, respect=+0.05, likability=-0.05),
    RelationshipEvent.DOMINATED_SHOWDOWN: AxisShift(heat=-0.02, respect=0.00, likability=-0.02),
    RelationshipEvent.STRONG_FOLD_SHOWN: AxisShift(heat=0.00, respect=0.00, likability=0.00),
    # COOLER mirror: the winner had a monster too and ran over the
    # loser's strong hand. Small respect bump (the loser put up a
    # fight, not a passive loss); heat near zero (no animosity from
    # the winner — they got there cleanly); likability roughly neutral.
    RelationshipEvent.COOLER: AxisShift(heat=0.00, respect=+0.05, likability=0.00),
    # Chat events. Mirror is the speaker's TARGET (who hears the
    # message). Trash-talk-toward-target moves target's heat against
    # the speaker; compliments do the inverse.
    RelationshipEvent.TRASH_TALK: AxisShift(heat=+0.05, respect=0.00, likability=-0.10),
    RelationshipEvent.COMPLIMENT: AxisShift(heat=-0.02, respect=+0.02, likability=+0.05),
    RelationshipEvent.TAUNT_POST_WIN: AxisShift(heat=+0.15, respect=0.00, likability=-0.10),
    RelationshipEvent.FRIENDLY_BANTER: AxisShift(heat=0.00, respect=0.00, likability=+0.03),
    RelationshipEvent.PROPS: AxisShift(heat=-0.02, respect=+0.08, likability=+0.05),
    RelationshipEvent.FLATTERY_LANDED: AxisShift(heat=-0.02, respect=-0.02, likability=+0.06),
    RelationshipEvent.FLATTERY_BACKFIRED: AxisShift(heat=+0.03, respect=-0.08, likability=-0.05),
    # Cash-mode staking. Mirror = borrower's view of the AI staker.
    # Receiving a stake creates gratitude; repaying confirms the
    # staker was trustworthy; defaulting curdles into mutual animosity
    # (borrower sees staker as a creditor breathing down their neck);
    # forgiveness leaves the borrower with strong positive feeling
    # toward someone who let them off the hook.
    RelationshipEvent.STAKE_OFFERED: AxisShift(heat=0.00, respect=+0.05, likability=+0.05),
    RelationshipEvent.STAKE_REPAID: AxisShift(heat=-0.05, respect=+0.05, likability=+0.05),
    RelationshipEvent.STAKE_DEFAULTED: AxisShift(heat=+0.20, respect=0.00, likability=-0.10),
    RelationshipEvent.STAKE_FORGIVEN: AxisShift(heat=-0.10, respect=+0.10, likability=+0.15),
    # Mirror: borrower felt rejected. Small negative — being told "no"
    # cools the borrower's warmth a touch, but not as much as the
    # staker's annoyance moves their own axes.
    RelationshipEvent.STAKE_FORGIVENESS_REFUSED: AxisShift(
        heat=+0.03,
        respect=0.00,
        likability=-0.03,
    ),
    # STACK_DOMINANCE mirror: the deep stack's view of the resenting
    # peer is unchanged. The bully doesn't notice; the social cost is
    # one-sided. Keeps the dynamic asymmetric — a player who runs hot
    # at a table loses standing with peers without gaining any
    # corresponding contempt or affinity in return.
    RelationshipEvent.STACK_DOMINANCE: AxisShift(),
    # Quarantine — no axis impact, same as actor table.
    RelationshipEvent.UNKNOWN: AxisShift(),
}


def actor_shift(event: RelationshipEvent) -> AxisShift:
    """Look up the actor's-POV axis shift for an event.

    UNKNOWN events return a zero shift (the quarantine path);
    callers should still gate on `event is RelationshipEvent.UNKNOWN`
    if they want to log/skip rather than silently no-op.
    """
    return ACTOR_AXIS_SHIFTS.get(event, AxisShift())


def mirror_shift(event: RelationshipEvent) -> AxisShift:
    """Look up the target's-POV (mirror) axis shift for an event.

    UNKNOWN events return a zero shift (the quarantine path).
    """
    return MIRROR_AXIS_SHIFTS.get(event, AxisShift())
