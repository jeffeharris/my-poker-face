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
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from .chip_flow import ChipFlow, PotShare, allocate_chip_flow
from .hand_history import RecordedHand
from .relationship_events import RelationshipEvent
from ..moment_analyzer import MomentAnalyzer

if TYPE_CHECKING:
    from .opponent_model import OpponentModelManager
    from ..repositories.relationship_repository import RelationshipRepository


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

    Holds an in-memory dedup set keyed on
    `(hand_id, actor_id, target_id, event)` so a second call on the
    same `RecordedHand` doesn't double-emit. The set lives on the
    detector instance, which is intended to be one-per-game-session
    (created in `MemoryManager.__init__`, reused across hands). It
    naturally bounds: the integration point in `complete_hand` calls
    `detect_events` exactly once per hand, so legitimate re-emission
    only happens if a hand is replayed.

    The `name_to_id` registry is consulted at emission time. A name
    with no registered id (or `None` registered) falls back to using
    the display name as the id — relationship state still persists,
    just keyed on the name rather than a stable cross-session id.
    """

    def __init__(self, name_to_id: Optional[Dict[str, Optional[str]]] = None):
        self._name_to_id: Dict[str, Optional[str]] = (
            dict(name_to_id) if name_to_id else {}
        )
        # Dedup set; key shape: (hand_number, actor_id, target_id, event)
        self._emitted: Set[
            Tuple[int, str, str, RelationshipEvent]
        ] = set()

    def detect_events(self, recorded_hand: RecordedHand) -> List[DetectedEvent]:
        """Inspect a completed hand and return the events it triggered.

        Returns an empty list when the hand triggers no relationship
        events (small pot, no losers, etc.). Within a single call to
        this method, dedup also filters out duplicates: a second call
        on the same `RecordedHand` instance will return [] because
        every event key was added to `self._emitted` on the first pass.
        """
        events: List[DetectedEvent] = []
        events.extend(self._detect_big_pot_events(recorded_hand))
        # Apply dedup AFTER detection so detection logic stays a
        # pure mapping. Each surviving event marks its key as
        # emitted; re-running the same hand returns no events.
        return self._filter_already_emitted(events, recorded_hand.hand_number)

    def _filter_already_emitted(
        self,
        events: List[DetectedEvent],
        hand_number: int,
    ) -> List[DetectedEvent]:
        """Drop events whose `(hand, actor, target, event)` key is
        already in the dedup set; record the rest before returning.
        """
        surviving: List[DetectedEvent] = []
        for event in events:
            key = (
                hand_number, event.actor_id, event.target_id, event.event,
            )
            if key in self._emitted:
                continue
            self._emitted.add(key)
            surviving.append(event)
        return surviving

    def _detect_big_pot_events(
        self, hand: RecordedHand,
    ) -> List[DetectedEvent]:
        """Emit BIG_WIN / BIG_LOSS pairs for big-pot hands.

        Uses the chip-flow allocation in `chip_flow.allocate_chip_flow`
        to produce one BIG_WIN + one BIG_LOSS per (winner, loser) pair
        — heads-up trivially, but also for multiway pots and split
        pots. The allocation rule is the same one feeding
        `cash_pair_stats` so the relationship layer and the cash
        bookkeeping stay aligned.

        Side-pot caveat: `RecordedHand` doesn't currently carry
        explicit per-pot structure (it has a flat winners list with
        per-winner amounts and a single contributions map). The
        detector reconstructs a single `PotShare` from that data,
        which is exact for headsup / split-pot / single-pot multiway
        hands and an approximation when side pots had different
        winners. The allocation helper is fully side-pot aware — if
        a future change adds `pot_breakdown` to `RecordedHand`, this
        method can build multiple `PotShare`s without touching the
        helper.
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

        # Build a single PotShare from the recorded hand. Aggregate
        # winner amounts in case the same name appears in multiple
        # pot_breakdown rows (shouldn't happen given the dedup in
        # `HandHistoryRecorder.complete_hand`, but defensive).
        winner_amounts: Dict[str, int] = {}
        for w in hand.winners:
            winner_amounts[w.name] = (
                winner_amounts.get(w.name, 0) + w.amount_won
            )
        if not winner_amounts:
            return []

        contributions = hand.get_player_contributions()
        pot = PotShare(
            amount=hand.pot_size,
            winners=tuple(winner_amounts.keys()),
            contributions=contributions,
        )
        flows = allocate_chip_flow([pot])
        if not flows:
            return []

        summary = hand.get_summary()
        events: List[DetectedEvent] = []
        for flow in flows:
            winner_id = self._resolve_id(flow.winner)
            loser_id = self._resolve_id(flow.loser)
            if winner_id is None or loser_id is None:
                continue

            # The bilateral axis update is encoded by emitting BOTH
            # events: BIG_WIN(winner→loser) applies the winner's POV
            # via the actor table and the loser's POV via the mirror
            # table; BIG_LOSS(loser→winner) applies the loser's POV
            # via the actor table and the winner's POV via the mirror
            # table. The calibration in `relationship_events.py`
            # assumes both events fire — emitting only one would
            # understate the axis movement for one side of the pair.
            events.append(DetectedEvent(
                actor_id=winner_id,
                target_id=loser_id,
                event=RelationshipEvent.BIG_WIN,
                narrative=(
                    f"{flow.winner} won a big pot from {flow.loser}"
                ),
                hand_summary=summary,
                chips_won=flow.chips,
            ))
            events.append(DetectedEvent(
                actor_id=loser_id,
                target_id=winner_id,
                event=RelationshipEvent.BIG_LOSS,
                narrative=(
                    f"{flow.loser} lost a big pot to {flow.winner}"
                ),
                hand_summary=summary,
                chips_won=-flow.chips,
            ))
        return events

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


def dispatch_events(
    events: List[DetectedEvent],
    manager: "OpponentModelManager",
    *,
    cash_pair_repo: Optional["RelationshipRepository"] = None,
    hand_id: Optional[int] = None,
    now: Optional[datetime] = None,
) -> None:
    """Apply a list of detected events to the relationship + cash layers.

    For each event:
      1. Call `manager.record_event` — bilateral relationship axis
         update through the only legal mutation entry point.
      2. If `cash_pair_repo` is provided (cash-mode hands), also
         apply the bilateral `cash_pair_stats` update via
         `apply_cash_pair_pnl`. cumulative_pnl moves by the event's
         `chips_won`; `hands_played_cash` increments by 1.

    Only `BIG_WIN` events drive the cash_pair_stats update — their
    paired `BIG_LOSS` events refer to the same chip flow with the
    opposite sign, and processing both would double-count. Other
    event types (`HERO_CALL`, `BAD_BEAT`, etc.) don't carry a
    chip-flow magnitude and are skipped for cash_pair_stats.

    `now` defaults to `datetime.utcnow()` for `record_event`'s
    decay anchor. `hand_id` is forwarded to `record_event` for the
    `MemorableHand` sidecar.

    Dedup is the detector's responsibility (`detect_events` already
    filters duplicates); this function processes every event it
    receives.
    """
    if not events:
        return
    if now is None:
        now = datetime.utcnow()

    for event in events:
        manager.record_event(
            actor_id=event.actor_id,
            target_id=event.target_id,
            event=event.event,
            impact_score=event.impact_score,
            narrative=event.narrative,
            hand_summary=event.hand_summary,
            hand_id=hand_id,
            now=now,
        )

    if cash_pair_repo is None:
        return

    for event in events:
        # Only positive-chips events drive cash_pair_stats — see
        # docstring. BIG_LOSS is the mirror view of the same flow.
        if event.event is not RelationshipEvent.BIG_WIN:
            continue
        if event.chips_won <= 0:
            # Defensive: a BIG_WIN with zero/negative chips would
            # mean the allocator emitted a flow that doesn't move
            # money — skip rather than write a no-op row.
            continue
        cash_pair_repo.apply_cash_pair_pnl(
            winner_id=event.actor_id,
            loser_id=event.target_id,
            chips=event.chips_won,
        )
