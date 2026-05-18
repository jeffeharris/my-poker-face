"""HandOutcomeDetector — map a completed RecordedHand to RelationshipEvents.

Runs at hand resolution (after `MemoryManager.complete_hand` finishes
recording the hand). Emits a list of `DetectedEvent` tuples that the
caller dispatches through `OpponentModelManager.record_event`.

Adapter pattern: where existing pressure/equity events already detect
a moment (e.g., `MomentAnalyzer.is_big_pot` flags a big-pot showdown),
the detector maps the same signal to a `RelationshipEvent` rather than
re-detecting. The big-pot threshold here is the same one used in
`PressureEventDetector` for the existing pressure `big_win` / `big_loss`
events — single source of truth.

This commit ships the load-bearing case: BIG_WIN / BIG_LOSS for a single
winner against a single loser (heads-up showdowns; heads-up-by-fold;
multiway hands that collapse to a single loser before showdown).
Multiway chip-flow allocation — splitting a single winner's net gain
across multiple losers proportionally to their pot contributions —
lands in the next commit and feeds both this detector and the
`cash_pair_stats` write path.

Spec: `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 1, §"Input
sources" and §"Multiway PnL-pair allocation rule".
Sequencing: `docs/plans/RELATIONSHIP_PHASE_3_HANDOFF.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .hand_history import RecordedHand
from .relationship_events import RelationshipEvent
from ..moment_analyzer import MomentAnalyzer


@dataclass(frozen=True)
class DetectedEvent:
    """One relationship event extracted from a completed hand.

    Carries everything `OpponentModelManager.record_event` needs to
    apply the bilateral axis update, plus `chips_won` (observer-POV,
    signed) so the same emission feeds `cash_pair_stats` in cash mode
    without re-deriving the allocation.

    `actor_id` / `target_id` are stable personality_ids when the
    detector's registry can resolve them; otherwise display names are
    used as a pass-through identifier (the relationship layer is
    string-keyed and treats either form as opaque). Production
    callers should populate the registry so cross-session callers can
    join on a stable id.
    """

    actor_id: str
    target_id: str
    event: RelationshipEvent
    impact_score: float = 1.0
    narrative: str = ""
    hand_summary: str = ""
    chips_won: int = 0


class HandOutcomeDetector:
    """Maps `RecordedHand` records to `DetectedEvent` lists.

    Stateless across hands by design — each `detect_events` call
    inspects only the supplied `RecordedHand`. Cross-hand dedup
    (`(hand_id, actor_id, target_id, event)`) lives at the dispatch
    layer in a later commit, not here, so the detector stays a pure
    mapping function and unit tests don't need state setup.

    The `name_to_id` registry is consulted at emission time. A name
    with no registered id (or `None` registered) falls back to using
    the display name as the id — relationship state still persists,
    just keyed on the name rather than a stable cross-session id.
    """

    def __init__(self, name_to_id: Optional[Dict[str, Optional[str]]] = None):
        self._name_to_id: Dict[str, Optional[str]] = (
            dict(name_to_id) if name_to_id else {}
        )

    def detect_events(self, recorded_hand: RecordedHand) -> List[DetectedEvent]:
        """Inspect a completed hand and return the events it triggered.

        Returns an empty list when the hand triggers no relationship
        events (small pot, no losers, etc.). Order within the returned
        list is: `BIG_WIN` events first, then `BIG_LOSS` events for
        the same pair — but consumers should not rely on order.
        """
        events: List[DetectedEvent] = []
        events.extend(self._detect_big_pot_events(recorded_hand))
        return events

    def _detect_big_pot_events(
        self, hand: RecordedHand,
    ) -> List[DetectedEvent]:
        """Emit BIG_WIN / BIG_LOSS pairs for big-pot hands.

        Commit 1 scope: handles only the simple single-winner /
        single-loser case (heads-up showdown, or multiway hands where
        everyone but one opponent folds out). Split pots and multiway
        losers are skipped here — commit 2's chip-flow allocation
        handles those cases and feeds this method.
        """
        if hand.pot_size <= 0 or not hand.winners:
            return []

        # Big-pot threshold: same `MomentAnalyzer.is_big_pot` calc as
        # `PressureEventDetector` uses for the pressure big_win/big_loss
        # signals. We pass `player_stack=0` so the method falls through
        # to the average-stack comparison, matching pressure detector's
        # invocation (it doesn't bind to any single player's stack at
        # showdown — the pot's bigness is observer-agnostic).
        starting_stacks = [
            p.starting_stack for p in hand.players if p.starting_stack > 0
        ]
        avg_stack = (
            sum(starting_stacks) / len(starting_stacks)
            if starting_stacks else 0
        )
        if not MomentAnalyzer.is_big_pot(hand.pot_size, 0, avg_stack):
            return []

        # Aggregate winner chips across pot_breakdown rows: a single
        # winner who collects main + side pot appears twice in
        # `hand.winners`; sum to one entry.
        winner_chips: Dict[str, int] = {}
        for w in hand.winners:
            winner_chips[w.name] = winner_chips.get(w.name, 0) + w.amount_won

        # Losers = players who paid into the pot but didn't collect.
        # A BB who folded preflop still contributed the blind — they
        # are a loser. Players with zero contribution (folded before
        # posting) are excluded.
        contributions = hand.get_player_contributions()
        loser_chips: Dict[str, int] = {
            name: contrib
            for name, contrib in contributions.items()
            if name not in winner_chips and contrib > 0
        }

        # Commit 1 restriction: only emit when the chip flow is
        # unambiguous (one winner, one loser). Split pots and multiway
        # losers wait for the chip-flow allocation in commit 2 — the
        # design doc's adapter table says "Multiway: emit per (winner,
        # loser) pair", but the pair selection requires the allocation
        # rule that ships next. Returning [] here is the correct
        # behavior until that lands.
        if len(winner_chips) != 1 or len(loser_chips) != 1:
            return []

        winner_name, total_won = next(iter(winner_chips.items()))
        loser_name, loser_contribution = next(iter(loser_chips.items()))

        winner_id = self._resolve_id(winner_name)
        loser_id = self._resolve_id(loser_name)
        if winner_id is None or loser_id is None:
            return []

        # Heads-up chip flow: the winner's net gain from the loser is
        # bounded by both the loser's contribution (can't lose more
        # than they put in) and the winner's collection. In the
        # single-pair case these are equal once raked/uncalled bets
        # are accounted for; `min` is the conservative bound and
        # matches what the multiway allocation reduces to in commit 2.
        chips_flow = min(total_won, loser_contribution)
        summary = hand.get_summary()

        # The bilateral axis update is encoded by emitting BOTH events:
        # BIG_WIN(winner→loser) applies the winner's POV via the actor
        # table and the loser's POV via the mirror table; BIG_LOSS
        # (loser→winner) applies the loser's POV via the actor table
        # and the winner's POV via the mirror table. The actor/mirror
        # rows in `relationship_events.py` are calibrated assuming
        # both events fire — emitting only one would understate the
        # axis movement for one side of the pair.
        return [
            DetectedEvent(
                actor_id=winner_id,
                target_id=loser_id,
                event=RelationshipEvent.BIG_WIN,
                narrative=(
                    f"{winner_name} won a big pot from {loser_name}"
                ),
                hand_summary=summary,
                chips_won=chips_flow,
            ),
            DetectedEvent(
                actor_id=loser_id,
                target_id=winner_id,
                event=RelationshipEvent.BIG_LOSS,
                narrative=(
                    f"{loser_name} lost a big pot to {winner_name}"
                ),
                hand_summary=summary,
                chips_won=-chips_flow,
            ),
        ]

    def _resolve_id(self, name: str) -> Optional[str]:
        """Resolve a display name to its registered personality_id.

        Falls back to the display name itself when no registry entry
        exists or the registered id is None. Registry semantics match
        `OpponentModelManager.register_player_id`: a None entry
        explicitly marks a name as known-without-id (human guests,
        pre-v85 personalities), and the fallback uses the name as
        the relationship-state key.
        """
        if name in self._name_to_id:
            mapped = self._name_to_id[name]
            return mapped if mapped is not None else name
        return name
