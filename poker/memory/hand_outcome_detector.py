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
    from ..equity_snapshot import HandEquityHistory
    from ..repositories.relationship_repository import RelationshipRepository


# BAD_BEAT detection threshold: loser was favorite at some pre-river
# point with at least this much equity. Tuned conservatively so the
# event reads as a genuine bad beat rather than a marginal favorite
# losing — the dispatch-table calibration (heat +0.30, the strongest
# axis movement in the whole event vocabulary) assumes this.
BAD_BEAT_EQUITY_MIN = 0.70


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
        # Hold the registry by reference so callers (e.g.,
        # `AIMemoryManager`) can share a single map between the
        # detector and `OpponentModelManager._name_to_id` — when the
        # manager registers a new player id, the detector sees it on
        # the next `detect_events` call without an explicit sync.
        # `None` becomes a fresh dict owned by the detector.
        self._name_to_id: Dict[str, Optional[str]] = (
            name_to_id if name_to_id is not None else {}
        )
        # Dedup set; key shape: (hand_number, actor_id, target_id, event)
        self._emitted: Set[
            Tuple[int, str, str, RelationshipEvent]
        ] = set()

    def detect_events(
        self,
        recorded_hand: RecordedHand,
        *,
        equity_history: "Optional[HandEquityHistory]" = None,
    ) -> List[DetectedEvent]:
        """Inspect a completed hand and return the events it triggered.

        Returns an empty list when the hand triggers no relationship
        events (small pot, no losers, etc.). Within a single call to
        this method, dedup also filters out duplicates: a second call
        on the same `RecordedHand` instance will return [] because
        every event key was added to `self._emitted` on the first pass.

        `equity_history` is optional. When supplied, BAD_BEAT
        detection runs (favorite-loser-with-bad-runout pattern);
        without it, BAD_BEAT is silently skipped. The equity history
        is built by `EquityTracker` during the experiment-runner
        path; the Flask game path doesn't compute equity today, so
        BAD_BEAT only fires in experiments where psychology or
        telemetry is enabled.
        """
        events: List[DetectedEvent] = []
        events.extend(self._detect_big_pot_events(recorded_hand))
        events.extend(self._detect_hero_calls(recorded_hand))
        events.extend(self._detect_bluffed_off(recorded_hand))
        if equity_history is not None:
            events.extend(self._detect_bad_beats(recorded_hand, equity_history))
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

    def _detect_hero_calls(self, hand: RecordedHand) -> List[DetectedEvent]:
        """Emit HERO_CALL events for river calls that beat a worse hand.

        v1 simple semantic — fires on the outcome pattern, not on
        whether the call was equity-justified:

          1. Hand reached showdown.
          2. Winner's last RIVER action was a `call`.
          3. The call answered a `bet` / `raise` / `all_in` from a
             specific loser on the RIVER (most recent aggressive
             action before the call).
          4. That loser's revealed hand at showdown was weaker than
             the winner's (higher hand_rank — lower is better in
             this codebase's HandEvaluator convention).

        **Approximation, not equity-aware.** A call that was
        technically a suckout (caller had worse equity at decision
        time but caught up by the river) still fires HERO_CALL —
        the showdown reveal is what the loser sees and feels, and
        the dispatch table's actor shift (heat −0.05, respect
        −0.10, likability +0.01 from the caller's POV) reads cleanly
        either way: "they caught me." Equity-aware refinement
        ("called a bet your range didn't justify") needs decision-
        time equity, which is on Track A's polarization-Phase-B
        roadmap. BAD_BEAT ships in the same later wave because it
        depends on the same equity-history-at-on_hand_complete
        plumbing that doesn't exist yet.

        Pre-river hero calls (turn call then river check-check) are
        also not detected — the simple semantic restricts to river
        action because that's where bluff-catcher patterns
        concentrate. Future revision can broaden.

        Losers' hand_rank is computed from
        `hole_cards + community_cards` via `HandEvaluator` because
        `RecordedHand` only persists `hand_rank` on winners
        (`WinnerInfo.hand_rank`). The compute is local and cheap;
        no DB round-trip.
        """
        if not hand.was_showdown:
            return []

        # Local imports — avoid importing heavy poker.* modules at
        # detector module-load time (keeps the import graph clean
        # for the relationship layer's other consumers).
        from core.card import Card
        from poker.hand_evaluator import HandEvaluator

        # Compute hand_rank for every revealed player. `hole_cards`
        # is keyed by name and only contains showdown-reaching
        # players (folded players are stripped before
        # complete_hand). Errors parsing cards / evaluating skip
        # that player silently — degraded data shouldn't crash the
        # detector.
        try:
            community = [Card.from_short(c) for c in hand.community_cards]
        except Exception:
            return []

        revealed_ranks: Dict[str, int] = {}
        for name, hole in hand.hole_cards.items():
            try:
                cards = [Card.from_short(c) for c in hole] + community
                result = HandEvaluator(cards).evaluate_hand()
                revealed_ranks[name] = result['hand_rank']
            except Exception:
                continue
        if len(revealed_ranks) < 2:
            return []

        winner_names = {w.name for w in hand.winners}
        # A hero call needs at least one winner that reached showdown
        # with a rank we computed. Most of the time
        # winner_info.hand_rank matches our computed value, but
        # using the computed rank keeps the comparison consistent
        # (same evaluator on both sides of the comparison).
        river_actions = [a for a in hand.actions if a.phase == 'RIVER']
        if not river_actions:
            return []

        summary = hand.get_summary()
        events: List[DetectedEvent] = []

        for winner in winner_names:
            winner_rank = revealed_ranks.get(winner)
            if winner_rank is None:
                continue

            # Scan RIVER actions in order. Track the most recent
            # aggressive action by a non-winner; if the winner's
            # next action is `call`, that's a hero-call candidate.
            last_bettor: Optional[str] = None
            called_against: Optional[str] = None
            for action in river_actions:
                actor = action.player_name
                act = action.action
                if act in ('bet', 'raise', 'all_in') and actor != winner:
                    last_bettor = actor
                elif act == 'call' and actor == winner and last_bettor:
                    called_against = last_bettor
                    break
            if called_against is None:
                continue

            loser_rank = revealed_ranks.get(called_against)
            # Strictly weaker hand (higher rank number). Equal ranks
            # (chopped pots, split-rank ties) don't qualify — the
            # bluff-catcher framing requires the loser actually lost.
            if loser_rank is None or loser_rank <= winner_rank:
                continue

            winner_id = self._resolve_id(winner)
            loser_id = self._resolve_id(called_against)
            if winner_id is None or loser_id is None:
                continue

            events.append(DetectedEvent(
                actor_id=winner_id,
                target_id=loser_id,
                event=RelationshipEvent.HERO_CALL,
                narrative=(
                    f"{winner} called {called_against}'s river bet "
                    f"and showed down the winner"
                ),
                hand_summary=summary,
            ))

        return events

    def _detect_bluffed_off(self, hand: RecordedHand) -> List[DetectedEvent]:
        """Emit BLUFFED_OFF for folds where the folder would have won.

        Semantic: the folder gave up a hand that would have beat the
        opponent who bet into them. This is the first non-showdown-
        outcome-driven event in the detector — it fires on the
        emotional pain of folding a winner.

        Detection requires both sides' card visibility:

          1. Hand reached showdown (so the bluffer's cards were
             revealed there).
          2. Folder's `hole_cards` are still in the dict at detection
             time. **Data dependency**: the tournament experiment
             path in `run_ai_tournament.py` pops folded players'
             cards from `hand_in_progress.hole_cards` before
             `complete_hand` runs — equity-tracker setup that
             predates this detector. So in experiment paths
             BLUFFED_OFF will rarely fire; in Flask game paths
             (production user play) folder cards are preserved and
             detection works. Future change to make the strip
             optional or move it after detection unblocks
             experiment-path coverage.
          3. The fold was postflop (`FLOP` / `TURN` / `RIVER` —
             preflop folds have no community cards to evaluate
             against).
          4. The most recent aggressor on the fold's street (the
             player whose bet/raise the folder gave up to) reached
             showdown with revealed cards.
          5. At the final board, folder's hand_rank beats bettor's.

        Multi-bettor edge case: if two players were aggressive on
        the same street before the fold (bet + raise), attribute the
        BLUFFED_OFF to the most recent aggressor — they're the
        proximate cause of the fold decision.

        Dispatch table asymmetry (intentional): the actor (folder)
        feels +0.20 heat, -0.05 respect, -0.02 likability — the
        canonical "they got me with junk" anger. The mirror
        (bluffer) is all zeros because they don't see the fold
        reveal and can't experience the moment. This is why
        BLUFFED_OFF is one-sided in the dispatch table while
        BIG_WIN/BIG_LOSS are mostly symmetric.
        """
        if not hand.was_showdown:
            return []

        # Players who reached showdown and have visible cards.
        fold_actors = {
            a.player_name for a in hand.actions if a.action == 'fold'
        }
        showdown_with_cards = {
            name for name in hand.hole_cards
            if name not in fold_actors
        }
        if not showdown_with_cards:
            return []

        postflop_folds = [
            a for a in hand.actions
            if a.action == 'fold' and a.phase in ('FLOP', 'TURN', 'RIVER')
        ]
        if not postflop_folds:
            return []

        # Local imports — same rationale as `_detect_hero_calls`:
        # keep the relationship module's import graph clean.
        from core.card import Card
        from poker.hand_evaluator import HandEvaluator

        try:
            community = [Card.from_short(c) for c in hand.community_cards]
        except Exception:
            return []
        # Showdown implies a completed board; defensive check anyway.
        if len(community) < 5:
            return []

        summary = hand.get_summary()
        events: List[DetectedEvent] = []

        for fold_action in postflop_folds:
            folder = fold_action.player_name
            if folder not in hand.hole_cards:
                # Folder's cards stripped — can't compute their
                # would-have-been hand. Silently skip (see docstring
                # data-dependency note).
                continue

            # Find the most recent aggressive action on the same
            # street before this fold, by anyone other than the
            # folder. That's the bettor the folder gave up to.
            prior_bettor: Optional[str] = None
            for a in hand.actions:
                if a is fold_action:
                    break
                if a.phase != fold_action.phase:
                    continue
                if a.player_name == folder:
                    continue
                if a.action in ('bet', 'raise', 'all_in'):
                    prior_bettor = a.player_name

            if prior_bettor is None:
                # Fold to a check or no prior action — not a bluff
                # spot (folder gave up unforced).
                continue
            if prior_bettor not in showdown_with_cards:
                # Bettor didn't reach showdown / has no card
                # visibility — can't verify the bluff.
                continue

            try:
                folder_cards = [
                    Card.from_short(c) for c in hand.hole_cards[folder]
                ] + community
                bettor_cards = [
                    Card.from_short(c) for c in hand.hole_cards[prior_bettor]
                ] + community
                folder_rank = HandEvaluator(folder_cards).evaluate_hand()['hand_rank']
                bettor_rank = HandEvaluator(bettor_cards).evaluate_hand()['hand_rank']
            except Exception:
                continue

            # Folder was ahead (strictly) — lower rank is better.
            # Equal ranks don't qualify; the would-have-been outcome
            # is ambiguous (chopped pot, or kicker-level comparison
            # which our rank-only check can't resolve).
            if folder_rank >= bettor_rank:
                continue

            folder_id = self._resolve_id(folder)
            bettor_id = self._resolve_id(prior_bettor)
            if folder_id is None or bettor_id is None:
                continue

            events.append(DetectedEvent(
                actor_id=folder_id,
                target_id=bettor_id,
                event=RelationshipEvent.BLUFFED_OFF,
                narrative=(
                    f"{folder} folded a winner to {prior_bettor}'s "
                    f"{fold_action.phase.lower()} bet"
                ),
                hand_summary=summary,
            ))

        return events

    def _detect_bad_beats(
        self,
        hand: RecordedHand,
        equity_history: "HandEquityHistory",
    ) -> List[DetectedEvent]:
        """Emit BAD_BEAT when a favorite at the final betting round loses.

        **Semantic**: actor was the favorite at some pre-river street
        (equity ≥ `BAD_BEAT_EQUITY_MIN`, default 0.70) AND lost the
        hand. Attributed to the (single) winner.

        Uses pre-river snapshots only (`PRE_FLOP` / `FLOP` / `TURN`)
        rather than `RIVER` — by the time the river card is dealt,
        equity collapses to a deterministic outcome (1.0 or 0.0), so
        a RIVER snapshot doesn't tell you about "favoriteness" at any
        meaningful decision point. The pre-river MAX across streets
        captures both classic shapes:
          - All-in preflop with the best hand → PRE_FLOP equity.
          - Bet on the flop/turn with a made hand → FLOP/TURN equity.

        Multi-winner pots (chopped) are skipped — attribution to "the
        winner who bad-beat me" is ambiguous when there are multiple.

        **Data dependency**: requires `equity_history` to be supplied
        by the caller. The experiment runner builds it when
        `enable_psychology` or `enable_telemetry` is true; the Flask
        game path doesn't compute equity today. So BAD_BEAT fires in
        experiment paths only until equity computation gets wired
        into the Flask game flow.

        **Why the highest axis weights**: BAD_BEAT actor shift is
        heat +0.30, respect -0.15, likability -0.10 — the most
        emotionally-loaded event in the vocabulary. The threshold
        is intentionally conservative so it doesn't over-fire on
        marginal favorites losing flips.
        """
        if not hand.was_showdown:
            return []
        if len(hand.winners) != 1:
            # Split pots — "who bad-beat me" is ambiguous.
            return []

        winner_name = hand.winners[0].name
        fold_actors = {
            a.player_name for a in hand.actions if a.action == 'fold'
        }
        losers = [
            p.name for p in hand.players
            if p.name != winner_name and p.name not in fold_actors
        ]
        if not losers:
            return []

        summary = hand.get_summary()
        events: List[DetectedEvent] = []

        for loser_name in losers:
            # Max equity across pre-river streets. RIVER excluded
            # because river-snapshot equity is the deterministic
            # outcome (1.0 winner, 0.0 loser), which doesn't tell us
            # whether the loser was a favorite going INTO the river.
            max_pre_river = 0.0
            for street in ('PRE_FLOP', 'FLOP', 'TURN'):
                eq = equity_history.get_player_equity(loser_name, street)
                if eq is not None and eq > max_pre_river:
                    max_pre_river = eq

            if max_pre_river < BAD_BEAT_EQUITY_MIN:
                continue

            winner_id = self._resolve_id(winner_name)
            loser_id = self._resolve_id(loser_name)
            if winner_id is None or loser_id is None:
                continue

            events.append(DetectedEvent(
                actor_id=loser_id,
                target_id=winner_id,
                event=RelationshipEvent.BAD_BEAT,
                narrative=(
                    f"{loser_name} had {int(max_pre_river * 100)}% equity "
                    f"pre-river but lost to {winner_name}"
                ),
                hand_summary=summary,
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
