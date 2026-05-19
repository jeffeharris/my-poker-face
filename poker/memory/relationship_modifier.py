"""Relationship-axis → exploitation-modifier reader.

This is the read-only bridge between durable affinity state (heat,
respect, likability) and the runtime knobs the bot's exploitation
layer consumes. It takes a `(observer_id, target_opponent_id)` pair
and returns a `RelationshipModifier` — a small dataclass of
multipliers + an offset that Phase 2's `_apply_exploitation`
integration will multiply / add into existing offsets.

Strictly pairwise. Multiway target selection (which opponent at the
table to read against) is a controller-side concern handled in
Phase 2 at the `_apply_exploitation` call site, not in this reader.
The reader has no game state — it only knows the pair and the time.

Pure: reads from the manager's relationship repository, applies
`project_heat` for the live heat value, maps axes to modifiers. Does
not mutate any state. Safe to call from anywhere — including
post-hand commentary and lobby tooltips when those land — without
incurring side effects.

Initial axis → modifier mapping (tunable from play data):
  - `project_heat() > 0.5`  → bluff_freq_mult = 1.3,
                              call_threshold_offset = -0.03
                              (chase rivals harder)
  - `respect > 0.7`         → fold_to_pressure_mult = 0.7
                              (harder to bluff off opponents we
                               consider strong)
  - `likability > 0.7`      → bluff_freq_mult *= 0.85 (capped at 0.85
                              so the modifier never disables bluffing
                              entirely against friends)

Modifiers compose multiplicatively when multiple axes are high.
Defaults preserve current behavior when no relationship state exists
— get_relationship_modifier always returns a valid object; missing
state degrades gracefully to the identity modifier.

Spec: `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 1 §"Tier
modifier seam — exact insertion point".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from .opponent_model import RelationshipState, project_heat

if TYPE_CHECKING:
    from .opponent_model import OpponentModelManager


# Thresholds for the axis → modifier mapping. Module-level constants
# so tests can import + lock them in; tuning passes update the
# constants and the test expectations together.
HEAT_RIVAL_THRESHOLD = 0.5
RESPECT_HIGH_THRESHOLD = 0.7
LIKABILITY_HIGH_THRESHOLD = 0.7

# Modifier values applied when the corresponding axis exceeds its
# threshold. Multipliers compose; the offset is additive.
RIVAL_BLUFF_FREQ_MULT = 1.3
RIVAL_CALL_THRESHOLD_OFFSET = -0.03
HIGH_RESPECT_FOLD_TO_PRESSURE_MULT = 0.7
HIGH_LIKABILITY_BLUFF_FREQ_MULT = 0.85


@dataclass(frozen=True)
class RelationshipModifier:
    """Per-decision modifier emitted by `get_relationship_modifier`.

    All fields default to the identity modifier — no relationship
    state means no behavior change. Phase 2's `_apply_exploitation`
    consumes these:

      bluff_freq_mult        scales the bluff-probability shift
                             applied when an exploit pattern
                             recommends a bluff frequency adjustment.
                             >1 = bluff more vs this opponent;
                             <1 = bluff less.

      fold_to_pressure_mult  scales the fold-probability offset added
                             when facing aggression from an opponent
                             flagged as a bluff-prone exploit. <1 =
                             harder to bluff off (we respect their
                             aggression more).

      call_threshold_offset  absolute add to the call-equity
                             threshold used in the value-vs-station
                             preflop classifier path. Negative
                             means "chase wider" (lower equity bar
                             to call).
    """
    bluff_freq_mult: float = 1.0
    fold_to_pressure_mult: float = 1.0
    call_threshold_offset: float = 0.0

    @property
    def is_identity(self) -> bool:
        """True when this modifier produces no behavior change.

        Phase 2's call site can early-out on this rather than walking
        the full offset-mutation path for opponents with default
        relationship state.
        """
        return (
            self.bluff_freq_mult == 1.0
            and self.fold_to_pressure_mult == 1.0
            and self.call_threshold_offset == 0.0
        )


def _modifier_from_axes(
    heat: float,
    respect: float,
    likability: float,
) -> RelationshipModifier:
    """Map projected axis values to a RelationshipModifier.

    Composition rule: multipliers compose multiplicatively across
    triggered axes (a high-heat AND high-likability opponent gets
    both factors). The offset is additive (currently only one axis
    contributes, but the additive shape generalizes if future axes
    add their own offsets).

    Internal helper — callers should go through
    `get_relationship_modifier` so the heat value is the projected
    one. Public for unit tests; the function is pure.
    """
    bluff_freq_mult = 1.0
    fold_to_pressure_mult = 1.0
    call_threshold_offset = 0.0

    if heat > HEAT_RIVAL_THRESHOLD:
        bluff_freq_mult *= RIVAL_BLUFF_FREQ_MULT
        call_threshold_offset += RIVAL_CALL_THRESHOLD_OFFSET

    if respect > RESPECT_HIGH_THRESHOLD:
        fold_to_pressure_mult *= HIGH_RESPECT_FOLD_TO_PRESSURE_MULT

    if likability > LIKABILITY_HIGH_THRESHOLD:
        # Soft-on-friends. Compose multiplicatively; floor implied by
        # composition (no single axis can drive the multiplier below
        # 0.85 in this v1 mapping).
        bluff_freq_mult *= HIGH_LIKABILITY_BLUFF_FREQ_MULT

    return RelationshipModifier(
        bluff_freq_mult=bluff_freq_mult,
        fold_to_pressure_mult=fold_to_pressure_mult,
        call_threshold_offset=call_threshold_offset,
    )


def get_relationship_modifier(
    manager: "OpponentModelManager",
    observer_id: str,
    target_opponent_id: str,
    now: datetime,
) -> RelationshipModifier:
    """Read the (observer, target) pair's relationship and emit a modifier.

    Strictly pairwise. The caller is responsible for picking
    `target_opponent_id` from game state — typically the primary
    aggressor in a multiway pot, with fallbacks per the design doc.
    This reader has no game state and no opinion about which
    opponent to read against.

    Returns the identity modifier (all defaults) when:
      - The manager has no relationship_repo (in-memory tests)
      - No relationship_states row exists for the pair (the
        pre-event state, equivalent to default RelationshipState)

    Both of those degrade gracefully — no exception, no behavior
    change. Phase 2's call site can call this freely without first
    checking whether relationship state is wired up.

    `now` is required (not defaulted) so callers can pin the
    projection point for replay / test stability. The
    `OpponentModelManager.record_event` method takes the same
    parameter for the same reason.
    """
    if manager._relationship_repo is None:
        return RelationshipModifier()

    state = manager._relationship_repo.load_relationship_state(
        observer_id, target_opponent_id, now=now,
    )
    if state is None:
        return RelationshipModifier()

    # load_relationship_state already projected heat through decay.
    return _modifier_from_axes(
        heat=state.heat,
        respect=state.respect,
        likability=state.likability,
    )
