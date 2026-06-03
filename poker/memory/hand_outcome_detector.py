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

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Set, Tuple

from ..moment_analyzer import MomentAnalyzer
from .chip_flow import ChipFlow, PotShare, allocate_chip_flow
from .hand_history import RecordedHand
from .relationship_events import RelationshipEvent

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..equity_snapshot import HandEquityHistory
    from ..repositories.relationship_repository import RelationshipRepository
    from .opponent_model import OpponentModelManager


# BAD_BEAT detection threshold: loser was favorite at some pre-river
# point with at least this much equity. Tuned conservatively so the
# event reads as a genuine bad beat rather than a marginal favorite
# losing — the dispatch-table calibration (heat +0.30, the strongest
# axis movement in the whole event vocabulary) assumes this.
BAD_BEAT_EQUITY_MIN = 0.70

# COOLER threshold: both hands must be three-of-a-kind or better to
# qualify (HandEvaluator hand_rank <= 7 — lower is better; 7 = three of
# a kind, 6 = straight, 5 = flush, …, 1 = royal flush). Two pair (8)
# is decent but doesn't read as a "monster" — coolers are the "I had
# it, they had MORE" emotional event, which needs both sides to have
# brought real strength. DOMINATED_SHOWDOWN handles weaker matchups.
COOLER_STRONG_HAND_RANK_MAX = 7

# STACK_DOMINANCE threshold: a player is "deep" once their stack at
# hand start crosses this multiple of the table max buy-in. Picked at
# 1.5× so normal winning sessions (typically 1.0-1.4× transiently)
# don't trigger; sustained presence above is what bites. Pairs with
# `movement.W_STAKE_UP` which nudges AIs to leave/stake-up starting
# at exactly 1.0× — by 1.5× the system has already "asked" the deep
# stack to consider moving, so further accumulation reads as refusal.
STACK_DOMINANCE_THRESHOLD = 1.5

# STACK_DOMINANCE saturation cap. The per-hand axis drip scales with
# `excess = stack/max_buy_in - 1.5`, but we cap excess at this value
# so a single session against a long-stay whale can't tank a pair's
# axis state into the floor. At cap (3.5× max buy-in), `excess=2.0`
# yields a per-hand likability shift of −0.006 (−0.003 base × 2.0);
# over 30 hands, −0.18 likability — meaningful, recoverable, not
# catastrophic. Stacks above 3.5× cap saturate at this signal level.
STACK_DOMINANCE_EXCESS_CAP = 2.0

# STACK_DOMINANCE firing cooldown, in hands, per (observer, deep_stack)
# pair. The detector would otherwise emit once PER HAND for every peer
# of every deep stack — in a 6-max table with one chip leader that's
# ~5 events/hand, which swamps real poker events (~0.2/hand) by two
# orders of magnitude and erodes respect/likability toward the floor
# long before any other signal registers (measured: 98.5% of all
# relationship events in a 3000-hand lobby sim). Throttling to once
# per N hands per pair makes dominance a slow background pressure
# rather than a flood, while still accumulating over genuinely
# sustained co-presence. 0 disables the throttle.
STACK_DOMINANCE_COOLDOWN_HANDS = 10

# --- Rivalry tiers (RIVAL -> NEMESIS) -------------------------------------
# Both tiers gate first on shared-hand VOLUME (hands_played_cash — the
# persisted per-pair count maintained on every settled pot), so a couple of
# big pots in a pair's first session can't mint a rivalry. The competition
# signal is the big-pot CLASH count: each big pot a player loses to a specific
# opponent is one clash against them. Count-based and stack-relative (see
# MomentAnalyzer.is_big_pot), so it means the same at $2 and $50 — a chip
# threshold would be unreachable at low stakes and instant-and-plural at high
# stakes.

# RIVAL (tier 1 — "we've got history"): symmetric / mutual. Fires for a pair
# once they've shared RIVAL_MIN_HANDS hands AND tangled in RIVAL_MIN_CLASHES
# big pots total (either direction). Balance-agnostic — a rivalry is
# engagement, win or lose.
RIVAL_MIN_HANDS = 40
RIVAL_MIN_CLASHES = 2

# NEMESIS (tier 2 — "deep-seated"): directed. Fires X->Y when, after a longer
# shared history (NEMESIS_MIN_HANDS), X has lost NEMESIS_BIG_LOSS_COUNT big
# pots to Y AND X is not clearly ahead in the big-pot record — i.e. X's losses
# to Y are within NEMESIS_AHEAD_TOLERANCE of X's wins back. The tolerance is
# why it "needn't be exactly equal": X can be up a pot or two and still hold
# the grudge, and an even back-and-forth war fires for BOTH sides (mutual
# nemesis) — which a pure antisymmetric net deficit could never produce.
NEMESIS_MIN_HANDS = 80
NEMESIS_BIG_LOSS_COUNT = 3
NEMESIS_AHEAD_TOLERANCE = 1

# REGULAR: low-grade familiarity from peaceful co-presence. Fires once per
# this many hands per co-present pair that did NOT have any other relationship
# event that hand (no warmth on a hand you clashed). The first time a pair is
# seen only starts their clock — familiarity is earned over time, not on the
# first shared hand. 0 disables.
REGULAR_COOLDOWN_HANDS = 20

# REGULAR fires at most this many times per pair, then plateaus. Familiarity
# is a bounded mild warmth ("we're regulars, I have a baseline good feeling
# about this person"), NOT ever-climbing affection. Without this cap every
# always-co-present pair saturates likability to 1.0 over a long session
# (measured: 25/30 pairs maxed in a 3000-hand sim). At MAX_FIRES x the +0.02
# likability shift, a pair warms by at most +0.10 (0.5 -> ~0.60).
REGULAR_MAX_FIRES = 5


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
    # Scales the AxisShift at dispatch time via `record_event(...,
    # context_multiplier=...)`. Defaults to 1.0 so existing detectors
    # keep their full magnitude; `_detect_stack_dominance` uses it to
    # scale the per-hand drip by the deep stack's excess over the
    # threshold. Keep numeric — dispatch multiplies raw shifts.
    context_multiplier: float = 1.0


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
        self._name_to_id: Dict[str, Optional[str]] = name_to_id if name_to_id is not None else {}
        # Dedup set; key shape: (hand_number, actor_id, target_id, event)
        self._emitted: Set[Tuple[int, str, str, RelationshipEvent]] = set()
        # STACK_DOMINANCE per-pair cooldown: (observer_id, deep_id) -> last
        # hand_number it fired. Throttles the per-hand drip to once every
        # STACK_DOMINANCE_COOLDOWN_HANDS so a long-seated deep stack doesn't
        # flood the relationship layer. Persists for the detector's lifetime
        # (per-game / per-sandbox), so the spacing holds across hands.
        self._stack_dominance_last_fired: Dict[Tuple[str, str], int] = {}
        # NEMESIS: count of big pots lost, per ordered (loser_id, winner_id)
        # pair. The reverse entry (winner_id, loser_id) is the loser's big
        # wins back, so the net deficit is loss[(a,b)] - loss[(b,a)]. When a
        # pair both reaches NEMESIS_BIG_LOSS_COUNT losses AND isn't ahead, the
        # rivalry fires once (latched in _nemesis_fired). In-memory for the
        # detector's lifetime (per-game / per-sandbox), like the other
        # throttle/latch state.
        self._big_pot_loss_count: Dict[Tuple[str, str], int] = {}
        # RIVAL fire-once latch, keyed by the UNORDERED pair (frozenset) since
        # the tier is symmetric/mutual. NEMESIS latch is directed.
        self._rival_fired: Set[frozenset] = set()
        self._nemesis_fired: Set[Tuple[str, str]] = set()
        # REGULAR per-pair cooldown: ordered (lo_id, hi_id) -> last hand fired
        # (or first-seen hand). Throttles the familiarity drip.
        self._regular_last_fired: Dict[Tuple[str, str], int] = {}
        # REGULAR per-pair fire count, capped at REGULAR_MAX_FIRES so
        # familiarity plateaus instead of climbing to max affection.
        self._regular_fire_count: Dict[Tuple[str, str], int] = {}

    def detect_events(
        self,
        recorded_hand: RecordedHand,
        *,
        equity_history: Optional[HandEquityHistory] = None,
        max_buy_in: Optional[int] = None,
        cash_pnl_lookup: Optional[Callable[[str, str], int]] = None,
        hands_played_lookup: Optional[Callable[[str, str], int]] = None,
    ) -> List[DetectedEvent]:
        """Inspect a completed hand and return the events it triggered.

        Returns an empty list when the hand triggers no relationship
        events (small pot, no losers, etc.). Within a single call to
        this method, dedup also filters out duplicates: a second call
        on the same `RecordedHand` instance will return [] because
        every event key was added to `self._emitted` on the first pass.

        `equity_history` is optional. When supplied, BAD_BEAT
        detection runs (favorite-loser-with-bad-runout pattern);
        without it, BAD_BEAT is silently skipped. Built by
        `EquityTracker` in both production paths (experiment
        runner + Flask game handler) and forwarded through
        `on_hand_complete`. See `_detect_bad_beats` for the
        per-path data-dependency details.

        `max_buy_in` enables STACK_DOMINANCE detection: when supplied
        (cash-mode games only — tournaments leave it None), peers
        with `starting_stack >= STACK_DOMINANCE_THRESHOLD * max_buy_in`
        trigger the per-hand drip event. `cash_pnl_lookup`, when
        provided, gates emission to observers with negative
        cumulative_pnl against the deep stack — strangers don't envy
        chips, only victims do. See `_detect_stack_dominance`.
        """
        events: List[DetectedEvent] = []
        big_pot_events = self._detect_big_pot_events(recorded_hand)
        events.extend(big_pot_events)
        events.extend(self._detect_knockouts(recorded_hand))
        events.extend(self._detect_hero_calls(recorded_hand))
        events.extend(self._detect_bluffed_off(recorded_hand))
        events.extend(self._detect_dominated_showdown(recorded_hand))
        events.extend(self._detect_coolers(recorded_hand))
        events.extend(self._detect_strong_fold_shown(recorded_hand))
        if equity_history is not None:
            events.extend(self._detect_bad_beats(recorded_hand, equity_history))
        if max_buy_in is not None and max_buy_in > 0:
            events.extend(
                self._detect_stack_dominance(
                    recorded_hand,
                    max_buy_in,
                    cash_pnl_lookup,
                )
            )
        # RIVAL / NEMESIS: accumulate big-pot clashes and fire the rivalry
        # tiers, gated on shared-hand volume. Cash-only — runs only when a
        # hands_played_lookup is wired (the same cash-mode context that builds
        # the cash_pnl_lookup); tournaments leave it None and never tier up.
        if hands_played_lookup is not None:
            events.extend(self._detect_rivalries(big_pot_events, hands_played_lookup))
        # REGULAR runs last: it keys off which pairs already have an event this
        # hand (no familiarity bump on a hand you clashed), so it needs the
        # full prior-event list.
        events.extend(self._detect_regulars(recorded_hand, events))
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
                hand_number,
                event.actor_id,
                event.target_id,
                event.event,
            )
            if key in self._emitted:
                continue
            self._emitted.add(key)
            surviving.append(event)
        return surviving

    def compute_chip_flows(self, hand: RecordedHand) -> List[ChipFlow]:
        """Pure allocation: return every (winner, loser, chips) flow for
        this hand. No big-pot threshold — every pot, regardless of size.

        Distinct from `_detect_big_pot_events`, which gates BIG_WIN/
        BIG_LOSS *event* emission on the big-pot threshold (the axes
        should only move on stack-threatening pots). This method
        exists so `cash_pair_stats` can track full lifetime PnL across
        every hand a pair shares, not just the dramatic ones. Both
        callers funnel through the same `allocate_chip_flow` allocator
        so the two views stay aligned.

        This method is **pure** — it can be called multiple times per
        hand without side effects. Dedup for cash_pair_stats writes
        lives in the caller (`AIMemoryManager._process_relationship_events`)
        so the same hand_number can be allocated by both
        `_detect_big_pot_events` and the cash-PnL dispatch path on
        a single pass without one starving the other.
        """
        if hand.pot_size <= 0 or not hand.winners:
            return []
        winner_amounts: Dict[str, int] = {}
        for w in hand.winners:
            winner_amounts[w.name] = winner_amounts.get(w.name, 0) + w.amount_won
        if not winner_amounts:
            return []
        contributions = hand.get_player_contributions()
        pot = PotShare(
            amount=hand.pot_size,
            winners=tuple(winner_amounts.keys()),
            contributions=contributions,
        )
        return allocate_chip_flow([pot])

    def _detect_big_pot_events(
        self,
        hand: RecordedHand,
    ) -> List[DetectedEvent]:
        """Emit BIG_WIN / BIG_LOSS pairs for big-pot hands.

        Reuses `compute_chip_flows` for the allocation, then gates
        event emission on `MomentAnalyzer.is_big_pot` — the same
        threshold `PressureEventDetector` uses, so the relationship-
        axis updates here align with the pressure layer's big_win/
        big_loss signals.

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
        flows = self.compute_chip_flows(hand)
        if not flows:
            return []

        # Big-pot threshold: same `MomentAnalyzer.is_big_pot` calc as
        # `PressureEventDetector` uses for the pressure big_win/big_loss
        # signals. We pass `player_stack=0` so the method falls through
        # to the average-stack comparison, matching pressure detector's
        # invocation (it doesn't bind to any single player's stack at
        # showdown — the pot's bigness is observer-agnostic).
        starting_stacks = [p.starting_stack for p in hand.players if p.starting_stack > 0]
        avg_stack = sum(starting_stacks) / len(starting_stacks) if starting_stacks else 0
        if not MomentAnalyzer.is_big_pot(hand.pot_size, 0, avg_stack):
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
            events.append(
                DetectedEvent(
                    actor_id=winner_id,
                    target_id=loser_id,
                    event=RelationshipEvent.BIG_WIN,
                    narrative=(f"{flow.winner} won a big pot from {flow.loser}"),
                    hand_summary=summary,
                    chips_won=flow.chips,
                )
            )
            events.append(
                DetectedEvent(
                    actor_id=loser_id,
                    target_id=winner_id,
                    event=RelationshipEvent.BIG_LOSS,
                    narrative=(f"{flow.loser} lost a big pot to {flow.winner}"),
                    hand_summary=summary,
                    chips_won=-flow.chips,
                )
            )
        return events

    def _compute_revealed_ranks(
        self,
        hand: RecordedHand,
    ) -> Optional[Tuple[list, Dict[str, int], Set[str], str]]:
        """Shared setup for the revealed-ranks showdown detectors.

        Folds the block that ``_detect_hero_calls``,
        ``_detect_dominated_showdown``, and ``_detect_coolers`` repeat
        verbatim:

          1. Require a showdown (else preconditions fail).
          2. Parse the community board (degraded data → preconditions
             fail rather than crash).
          3. Evaluate every revealed player's hand_rank from
             ``hole_cards + community`` (silently skipping players whose
             cards don't parse), requiring at least two ranks.
          4. Collect winner names and the hand summary.

        Returns ``(community, revealed_ranks, winner_names, summary)`` on
        success, or ``None`` when any precondition fails — callers do
        ``ctx = self._compute_revealed_ranks(hand); if ctx is None:
        return []`` to preserve the original short-circuits exactly.

        Local imports of ``Card`` / ``HandEvaluator`` keep the
        relationship module's load-time import graph clean (same
        rationale the inlined blocks documented).
        """
        if not hand.was_showdown:
            return None

        from core.card import Card
        from poker.hand_evaluator import HandEvaluator

        try:
            community = [Card.from_short(c) for c in hand.community_cards]
        except Exception:
            return None

        revealed_ranks: Dict[str, int] = {}
        for name, hole in hand.hole_cards.items():
            try:
                cards = [Card.from_short(c) for c in hole] + community
                result = HandEvaluator(cards).evaluate_hand()
                revealed_ranks[name] = result['hand_rank']
            except Exception:
                continue
        if len(revealed_ranks) < 2:
            return None

        winner_names = {w.name for w in hand.winners}
        summary = hand.get_summary()
        return community, revealed_ranks, winner_names, summary

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
        # Shared setup: showdown gate, board parse, revealed ranks,
        # winner names, summary. `community` is computed by the helper
        # but unused here (hero-call works off ranks + river actions).
        ctx = self._compute_revealed_ranks(hand)
        if ctx is None:
            return []
        _community, revealed_ranks, winner_names, summary = ctx

        # A hero call needs at least one winner that reached showdown
        # with a rank we computed. Most of the time
        # winner_info.hand_rank matches our computed value, but
        # using the computed rank keeps the comparison consistent
        # (same evaluator on both sides of the comparison).
        river_actions = [a for a in hand.actions if a.phase == 'RIVER']
        if not river_actions:
            return []

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

            events.append(
                DetectedEvent(
                    actor_id=winner_id,
                    target_id=loser_id,
                    event=RelationshipEvent.HERO_CALL,
                    narrative=(
                        f"{winner} called {called_against}'s river bet "
                        f"and showed down the winner"
                    ),
                    hand_summary=summary,
                )
            )

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
        fold_actors = {a.player_name for a in hand.actions if a.action == 'fold'}
        showdown_with_cards = {name for name in hand.hole_cards if name not in fold_actors}
        if not showdown_with_cards:
            return []

        postflop_folds = [
            a for a in hand.actions if a.action == 'fold' and a.phase in ('FLOP', 'TURN', 'RIVER')
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
                folder_cards = [Card.from_short(c) for c in hand.hole_cards[folder]] + community
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

            events.append(
                DetectedEvent(
                    actor_id=folder_id,
                    target_id=bettor_id,
                    event=RelationshipEvent.BLUFFED_OFF,
                    narrative=(
                        f"{folder} folded a winner to {prior_bettor}'s "
                        f"{fold_action.phase.lower()} bet"
                    ),
                    hand_summary=summary,
                )
            )

        return events

    def _detect_dominated_showdown(
        self,
        hand: RecordedHand,
    ) -> List[DetectedEvent]:
        """Emit DOMINATED_SHOWDOWN for committed losers who got outclassed.

        Semantic: at showdown, a non-winner who was committed postflop
        (called a bet/raise on FLOP/TURN/RIVER) reaches the river with
        a hand whose category is strictly weaker than a winner's.
        "Materially worse" = different hand category, not just kicker
        domination — uses `HandEvaluator.hand_rank` (1=royal flush …
        10=high card, lower=better) and requires `winner_rank <
        loser_rank` strictly.

        Why categorical, not kicker-level: kicker domination (AK vs AQ
        on an A-high board) is a different emotional event than
        set-over-set. The current calibration (actor: respect −0.15,
        no heat) reads as "they outclassed me," which fits the
        category-jump shape. A future split could add a separate
        KICKER_DOMINATED event with smaller weights.

        Commitment gate: only fires when the loser called at least one
        postflop bet/raise/all_in. A passive check-down to showdown
        doesn't qualify — the loser didn't invest enough chips to feel
        outclassed.

        Mutually exclusive with COOLER: when both sides held strong
        hands (rank ≤ `COOLER_STRONG_HAND_RANK_MAX`), the matchup is a
        cooler and `_detect_coolers` fires instead. This detector
        excludes that case so the same outcome doesn't emit two
        overlapping events with different emotional signatures.

        Reuses the same revealed-ranks scan as `_detect_hero_calls`;
        sharing the import path keeps the relationship module's
        load-time footprint small.
        """
        # Shared setup (same block as `_detect_hero_calls` /
        # `_detect_coolers`); `community` unused here.
        ctx = self._compute_revealed_ranks(hand)
        if ctx is None:
            return []
        _community, revealed_ranks, winner_names, summary = ctx

        # Postflop-committed losers only. "Committed" = called a
        # bet/raise/all_in on FLOP, TURN, or RIVER. A river-only call
        # qualifies; so does a flop call that mucks no further bets.
        postflop_committed = {
            a.player_name
            for a in hand.actions
            if a.action == 'call'
            and a.phase in ('FLOP', 'TURN', 'RIVER')
            and a.player_name not in winner_names
        }
        if not postflop_committed:
            return []

        events: List[DetectedEvent] = []

        for loser in postflop_committed:
            loser_rank = revealed_ranks.get(loser)
            if loser_rank is None:
                continue
            for winner in winner_names:
                winner_rank = revealed_ranks.get(winner)
                if winner_rank is None:
                    continue
                # Strict category jump. Equal-rank kicker decisions
                # don't qualify (they're a different emotional event;
                # see method docstring).
                if winner_rank >= loser_rank:
                    continue

                # Cooler exclusion: when both sides are strong, the
                # emotional signature is "I had it, they had more,"
                # not "they outclassed me." Let `_detect_coolers`
                # handle that subset.
                if (
                    winner_rank <= COOLER_STRONG_HAND_RANK_MAX
                    and loser_rank <= COOLER_STRONG_HAND_RANK_MAX
                ):
                    continue

                winner_id = self._resolve_id(winner)
                loser_id = self._resolve_id(loser)
                if winner_id is None or loser_id is None:
                    continue

                events.append(
                    DetectedEvent(
                        actor_id=loser_id,
                        target_id=winner_id,
                        event=RelationshipEvent.DOMINATED_SHOWDOWN,
                        narrative=(
                            f"{loser} called postflop and showed down "
                            f"a weaker hand than {winner}"
                        ),
                        hand_summary=summary,
                    )
                )

        return events

    def _detect_coolers(
        self,
        hand: RecordedHand,
    ) -> List[DetectedEvent]:
        """Emit COOLER when both showdown hands are strong and the
        category gap is real.

        Semantic: postflop-committed loser shows down a strong hand
        (rank ≤ `COOLER_STRONG_HAND_RANK_MAX`, i.e., three-of-a-kind
        or better) and gets beat by a winner with a strictly stronger
        category (also strong, also ≤ threshold). The "I had it, they
        had more" event — distinct from DOMINATED_SHOWDOWN ("they
        outclassed me, I had nothing") and from BAD_BEAT ("I was
        favorite, they got there").

        Does NOT depend on `equity_history` — the rank delta at
        showdown is the only signal. BAD_BEAT needs equity history
        because its semantic is "you were ahead, you ran bad"; COOLER
        doesn't care who was ahead, just that both showed up with
        real hands and one outflopped/outdrew the other.

        Mutually exclusive with DOMINATED_SHOWDOWN by construction:
        DOMINATED skips the both-strong case and lets this fire.
        """
        # Shared setup (same block as `_detect_hero_calls` /
        # `_detect_dominated_showdown`); `community` unused here.
        ctx = self._compute_revealed_ranks(hand)
        if ctx is None:
            return []
        _community, revealed_ranks, winner_names, summary = ctx

        postflop_committed = {
            a.player_name
            for a in hand.actions
            if a.action == 'call'
            and a.phase in ('FLOP', 'TURN', 'RIVER')
            and a.player_name not in winner_names
        }
        if not postflop_committed:
            return []

        events: List[DetectedEvent] = []

        for loser in postflop_committed:
            loser_rank = revealed_ranks.get(loser)
            if loser_rank is None or loser_rank > COOLER_STRONG_HAND_RANK_MAX:
                # Loser didn't have a strong hand — not a cooler.
                # If there's a category gap, DOMINATED_SHOWDOWN handles it.
                continue
            for winner in winner_names:
                winner_rank = revealed_ranks.get(winner)
                if winner_rank is None or winner_rank > COOLER_STRONG_HAND_RANK_MAX:
                    continue
                # Strict category jump within the strong-hand band.
                if winner_rank >= loser_rank:
                    continue

                winner_id = self._resolve_id(winner)
                loser_id = self._resolve_id(loser)
                if winner_id is None or loser_id is None:
                    continue

                events.append(
                    DetectedEvent(
                        actor_id=loser_id,
                        target_id=winner_id,
                        event=RelationshipEvent.COOLER,
                        narrative=(
                            f"{loser} had a strong hand but ran into {winner}'s " f"stronger one"
                        ),
                        hand_summary=summary,
                    )
                )

        return events

    def _detect_strong_fold_shown(
        self,
        hand: RecordedHand,
    ) -> List[DetectedEvent]:
        """Emit STRONG_FOLD_SHOWN for postflop folds that were correct.

        Mirror of `_detect_bluffed_off`: same scan, opposite outcome.
        Fires when the folder's would-have-been hand at the final
        board was strictly *worse* than the bettor's revealed
        showdown hand. The folder made a disciplined laydown — they
        gain respect for the bettor for having it.

        Detection mirrors `_detect_bluffed_off`'s data requirements
        exactly: showdown reached, folder's `hole_cards` preserved
        through `complete_hand`, the bettor reached showdown with
        revealed cards, postflop street (`FLOP`/`TURN`/`RIVER`), and
        both sides' hand_ranks computable. Equal ranks don't qualify
        (the would-have-been outcome is ambiguous).

        Dispatch-table asymmetry: the actor (folder) gains respect
        (+0.10) for the bettor. The mirror (bettor) is all zeros
        because the bettor doesn't see the fold reveal in normal
        play — they don't know the folder made a good fold. If a
        future `show_cards_on_fold` feature lands, the mirror values
        can be revisited.

        Data-dependency note: same as `_detect_bluffed_off`. The
        tournament experiment path strips folded players' cards
        before `complete_hand`, so STRONG_FOLD_SHOWN will rarely fire
        in experiment paths until that change lands. Flask game paths
        preserve cards and detection works.
        """
        if not hand.was_showdown:
            return []

        fold_actors = {a.player_name for a in hand.actions if a.action == 'fold'}
        showdown_with_cards = {name for name in hand.hole_cards if name not in fold_actors}
        if not showdown_with_cards:
            return []

        postflop_folds = [
            a for a in hand.actions if a.action == 'fold' and a.phase in ('FLOP', 'TURN', 'RIVER')
        ]
        if not postflop_folds:
            return []

        from core.card import Card
        from poker.hand_evaluator import HandEvaluator

        try:
            community = [Card.from_short(c) for c in hand.community_cards]
        except Exception:
            return []
        if len(community) < 5:
            return []

        summary = hand.get_summary()
        events: List[DetectedEvent] = []

        for fold_action in postflop_folds:
            folder = fold_action.player_name
            if folder not in hand.hole_cards:
                continue

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
                continue
            if prior_bettor not in showdown_with_cards:
                continue

            try:
                folder_cards = [Card.from_short(c) for c in hand.hole_cards[folder]] + community
                bettor_cards = [
                    Card.from_short(c) for c in hand.hole_cards[prior_bettor]
                ] + community
                folder_rank = HandEvaluator(folder_cards).evaluate_hand()['hand_rank']
                bettor_rank = HandEvaluator(bettor_cards).evaluate_hand()['hand_rank']
            except Exception:
                continue

            # Folder was behind (strictly) — higher rank is worse.
            # Equal ranks don't qualify; the would-have-been outcome
            # is ambiguous at the rank-only level.
            if folder_rank <= bettor_rank:
                continue

            folder_id = self._resolve_id(folder)
            bettor_id = self._resolve_id(prior_bettor)
            if folder_id is None or bettor_id is None:
                continue

            events.append(
                DetectedEvent(
                    actor_id=folder_id,
                    target_id=bettor_id,
                    event=RelationshipEvent.STRONG_FOLD_SHOWN,
                    narrative=(
                        f"{folder} folded to {prior_bettor}'s "
                        f"{fold_action.phase.lower()} bet and would have lost"
                    ),
                    hand_summary=summary,
                )
            )

        return events

    def _detect_bad_beats(
        self,
        hand: RecordedHand,
        equity_history: HandEquityHistory,
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
        by the caller. Wired in both production paths:
          - Experiment runner (`run_ai_tournament.py`) — builds it
            when `enable_psychology` or `enable_telemetry` is true.
            Configs with neither enabled won't fire BAD_BEAT.
          - Flask game handler (`game_handler.py`) — always builds it
            for its own equity-persistence path, then forwards to
            `on_hand_complete`. BAD_BEAT fires unconditionally for
            qualifying hands in live user games.

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
        fold_actors = {a.player_name for a in hand.actions if a.action == 'fold'}
        losers = [
            p.name for p in hand.players if p.name != winner_name and p.name not in fold_actors
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

            events.append(
                DetectedEvent(
                    actor_id=loser_id,
                    target_id=winner_id,
                    event=RelationshipEvent.BAD_BEAT,
                    narrative=(
                        f"{loser_name} had {int(max_pre_river * 100)}% equity "
                        f"pre-river but lost to {winner_name}"
                    ),
                    hand_summary=summary,
                )
            )

        return events

    def _detect_stack_dominance(
        self,
        hand: RecordedHand,
        max_buy_in: int,
        cash_pnl_lookup: Optional[Callable[[str, str], int]],
    ) -> List[DetectedEvent]:
        """Emit STACK_DOMINANCE for peers seated with a deep stack.

        Per-hand drip: for every player whose `starting_stack` at the
        beginning of this hand is at least `STACK_DOMINANCE_THRESHOLD ×
        max_buy_in`, emit one event from each other seated player who
        is net-down against them in cash_pair_stats. The actor is the
        observer (the one whose respect/likability will dip); the
        target is the deep stack.

        `context_multiplier` carries the deep stack's excess above the
        threshold — `starting_stack / max_buy_in − STACK_DOMINANCE_THRESHOLD`
        — so dispatch scales the small base AxisShift up as the stack
        gets deeper. A player at exactly 1.5× the cap produces a
        multiplier of 0 and therefore no axis movement (filtered out
        below); 2× cap → 0.5; 3× cap → 1.5.

        `cash_pnl_lookup(observer_id, deep_stack_id) -> chips` returns
        the observer's cumulative_pnl against the deep stack within
        the active sandbox (positive = observer is up; negative =
        observer is down). When the lookup is None, the gate is
        skipped and every seated peer emits — useful for tests and as
        a safe fallback during early sandbox life when nobody has
        registered a chip flow yet.

        Cash-mode only. Tournament callers pass `max_buy_in=None` to
        `detect_events()`, which skips this detector entirely.
        """
        if len(hand.players) < 2:
            return []
        threshold_chips = STACK_DOMINANCE_THRESHOLD * max_buy_in
        # Snapshot the seat list so the inner loop iterates over the
        # same set the deep-stack scan saw — protects against any
        # caller passing a mutable view.
        seated = list(hand.players)
        events: List[DetectedEvent] = []
        for deep in seated:
            if deep.starting_stack < threshold_chips:
                continue
            excess = (deep.starting_stack / max_buy_in) - STACK_DOMINANCE_THRESHOLD
            if excess <= 0:
                # Defensive: float comparison may permit equality at
                # exactly the threshold. Skip rather than emit a zero-
                # multiplier event that would round to no-op anyway.
                continue
            # Saturate so extreme whales can't drive a pair's axes to
            # the floor in one session. See STACK_DOMINANCE_EXCESS_CAP
            # docstring for the math.
            excess = min(excess, STACK_DOMINANCE_EXCESS_CAP)
            deep_id = self._resolve_id(deep.name)
            if deep_id is None:
                continue
            for observer in seated:
                if observer.name == deep.name:
                    continue
                observer_id = self._resolve_id(observer.name)
                if observer_id is None:
                    continue
                if cash_pnl_lookup is not None:
                    try:
                        pnl = cash_pnl_lookup(observer_id, deep_id)
                    except Exception as exc:
                        # Treat lookup failure as "no data" — skip the
                        # pair rather than emitting an ungated event.
                        # A persistent lookup failure surfaces only as
                        # absent resentment, not as wrong resentment.
                        logger.debug(
                            "stack_dominance pnl lookup failed " "(observer=%s deep=%s): %s",
                            observer_id,
                            deep_id,
                            exc,
                        )
                        continue
                    if pnl >= 0:
                        # Observer hasn't lost to this deep stack —
                        # no resentment. Strangers and net winners
                        # against the deep stack stay neutral.
                        continue
                # Throttle: at most one drip per pair per cooldown window so a
                # long-seated deep stack doesn't flood the relationship layer.
                if STACK_DOMINANCE_COOLDOWN_HANDS > 0:
                    pair_key = (observer_id, deep_id)
                    last = self._stack_dominance_last_fired.get(pair_key)
                    if (
                        last is not None
                        and (hand.hand_number - last) < STACK_DOMINANCE_COOLDOWN_HANDS
                    ):
                        continue
                    self._stack_dominance_last_fired[pair_key] = hand.hand_number
                events.append(
                    DetectedEvent(
                        actor_id=observer_id,
                        target_id=deep_id,
                        event=RelationshipEvent.STACK_DOMINANCE,
                        context_multiplier=excess,
                        narrative=(
                            f"{observer.name} watched {deep.name} sit deep "
                            f"({deep.starting_stack} chips, "
                            f"{deep.starting_stack / max_buy_in:.1f}× cap)"
                        ),
                    )
                )
        return events

    def _detect_knockouts(self, hand: RecordedHand) -> List[DetectedEvent]:
        """Emit KNOCKOUT when a player busts (final_stack <= 0) this hand.

        The buster is attributed via `compute_chip_flows`: the winner who took
        the largest share of the busted player's chips this hand. Actor = the
        buster, target = the busted player — the emotional weight is on the
        mirror (the busted player's view of who took them out). Requires
        `final_stack` to be populated on the recorded players; paths that don't
        capture it (final_stack=None) simply never fire KNOCKOUT.
        """
        busted = {
            p.name
            for p in hand.players
            if p.final_stack is not None and p.final_stack <= 0
        }
        if not busted:
            return []
        # Largest taker from each busted loser = the buster.
        best: Dict[str, Tuple[int, str]] = {}  # loser -> (chips, winner)
        for flow in self.compute_chip_flows(hand):
            if flow.loser in busted and flow.chips > best.get(flow.loser, (0, ""))[0]:
                best[flow.loser] = (flow.chips, flow.winner)
        events: List[DetectedEvent] = []
        for loser, (_chips, winner) in best.items():
            loser_id = self._resolve_id(loser)
            buster_id = self._resolve_id(winner)
            if loser_id is None or buster_id is None or loser_id == buster_id:
                continue
            events.append(
                DetectedEvent(
                    actor_id=buster_id,
                    target_id=loser_id,
                    event=RelationshipEvent.KNOCKOUT,
                    narrative=f"{winner} busted {loser}",
                )
            )
        return events

    def _detect_rivalries(
        self,
        big_pot_events: List[DetectedEvent],
        hands_played_lookup: Callable[[str, str], int],
    ) -> List[DetectedEvent]:
        """Fold this hand's big-pot clashes into the running counts, then emit
        the rivalry tiers gated on shared-hand volume.

        RIVAL (tier 1, symmetric/mutual): the pair has shared >= RIVAL_MIN_HANDS
        hands AND clashed in >= RIVAL_MIN_CLASHES big pots (either direction).
        Balance-agnostic.

        NEMESIS (tier 2, directed): after >= NEMESIS_MIN_HANDS shared hands, a
        player has lost >= NEMESIS_BIG_LOSS_COUNT big pots to a specific
        opponent AND isn't ahead of them by more than NEMESIS_AHEAD_TOLERANCE
        big pots. The tolerance lets an even war fire for both sides (mutual)
        while a lopsided matchup stays one-directional. Both tiers latch.

        `hands_played_lookup(a, b)` returns the pair's persisted
        `hands_played_cash` (symmetric); this method is only called in the
        cash-mode context where that lookup is wired.
        """
        touched: Set[frozenset] = set()
        for ev in big_pot_events:
            if ev.event is not RelationshipEvent.BIG_LOSS:
                continue
            loser_id, winner_id = ev.actor_id, ev.target_id
            if loser_id == winner_id:
                continue
            key = (loser_id, winner_id)
            self._big_pot_loss_count[key] = self._big_pot_loss_count.get(key, 0) + 1
            touched.add(frozenset(key))

        events: List[DetectedEvent] = []
        for pair_set in touched:
            a, b = tuple(pair_set)
            try:
                hands = hands_played_lookup(a, b)
            except Exception:
                continue
            loss_ab = self._big_pot_loss_count.get((a, b), 0)
            loss_ba = self._big_pot_loss_count.get((b, a), 0)
            clashes = loss_ab + loss_ba

            # RIVAL — mutual, once per unordered pair.
            if (
                pair_set not in self._rival_fired
                and hands >= RIVAL_MIN_HANDS
                and clashes >= RIVAL_MIN_CLASHES
            ):
                self._rival_fired.add(pair_set)
                events.append(
                    DetectedEvent(
                        actor_id=a,
                        target_id=b,
                        event=RelationshipEvent.RIVAL,
                        narrative=f"{a} and {b} have history ({clashes} big-pot clashes)",
                    )
                )

            # NEMESIS — directed; check each side.
            for loser_id, winner_id, l_loss, l_win in (
                (a, b, loss_ab, loss_ba),
                (b, a, loss_ba, loss_ab),
            ):
                pair = (loser_id, winner_id)
                if (
                    pair not in self._nemesis_fired
                    and hands >= NEMESIS_MIN_HANDS
                    and l_loss >= NEMESIS_BIG_LOSS_COUNT
                    and l_loss >= l_win - NEMESIS_AHEAD_TOLERANCE
                ):
                    self._nemesis_fired.add(pair)
                    events.append(
                        DetectedEvent(
                            actor_id=loser_id,
                            target_id=winner_id,
                            event=RelationshipEvent.NEMESIS,
                            narrative=(
                                f"{loser_id} has lost {l_loss} big pots to "
                                f"{winner_id} — nemesis"
                            ),
                        )
                    )
        return events

    def _detect_regulars(
        self,
        hand: RecordedHand,
        prior_events: List[DetectedEvent],
    ) -> List[DetectedEvent]:
        """Emit a bilateral REGULAR familiarity drip for co-present pairs.

        Fires once per REGULAR_COOLDOWN_HANDS per pair, skipping any pair that
        already has an event this hand (no warmth on a hand you clashed). The
        first time a pair is seen only starts their clock — the first bump
        comes one cooldown window later, so familiarity is earned, not granted
        on the opening shared hand. The shift is symmetric, so one directed
        event per unordered pair updates both views via record_event.
        """
        if REGULAR_COOLDOWN_HANDS <= 0 or len(hand.players) < 2:
            return []
        clashed = {frozenset((e.actor_id, e.target_id)) for e in prior_events}
        ids = [
            pid
            for pid in (self._resolve_id(p.name) for p in hand.players)
            if pid is not None
        ]
        hn = hand.hand_number
        events: List[DetectedEvent] = []
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                if a == b or frozenset((a, b)) in clashed:
                    continue
                pair = (a, b) if a < b else (b, a)
                if self._regular_fire_count.get(pair, 0) >= REGULAR_MAX_FIRES:
                    continue  # familiarity plateaued for this pair
                last = self._regular_last_fired.get(pair)
                if last is None:
                    self._regular_last_fired[pair] = hn  # start the clock
                    continue
                if (hn - last) < REGULAR_COOLDOWN_HANDS:
                    continue
                self._regular_last_fired[pair] = hn
                self._regular_fire_count[pair] = self._regular_fire_count.get(pair, 0) + 1
                events.append(
                    DetectedEvent(
                        actor_id=pair[0],
                        target_id=pair[1],
                        event=RelationshipEvent.REGULAR,
                        narrative="familiar tablemates",
                    )
                )
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
    manager: OpponentModelManager,
    *,
    cash_pair_repo: Optional[RelationshipRepository] = None,
    chip_flows: Optional[List[ChipFlow]] = None,
    id_resolver: Optional[Callable[[str], Optional[str]]] = None,
    hand_id: Optional[int] = None,
    now: Optional[datetime] = None,
    sandbox_id: Optional[str] = None,
    suppress_ids: Optional[set] = None,
) -> None:
    """Apply detected events to the relationship + cash layers.

    Two independent surfaces fan out from here:

      1. **Relationship axes** — every event in `events` goes
         through `manager.record_event` (bilateral axis update).
         BIG_WIN/BIG_LOSS, HERO_CALL, BAD_BEAT, etc. all drive
         this. Gating (big-pot threshold, etc.) is the detector's
         job before the events get here.

      2. **Cash pair PnL** — when `cash_pair_repo` is provided AND
         `chip_flows` is provided, *every* flow gets written via
         `apply_cash_pair_pnl`. No big-pot threshold — small pots
         count too, so cumulative PnL between any two players is
         a true lifetime total. `id_resolver` maps each flow's
         display name → stable id (mirrors `_resolve_id` on the
         detector); when not supplied, names are used verbatim.

    Backward-compat shim: callers that don't yet pass `chip_flows`
    fall through to the legacy path — `BIG_WIN` events drive
    `apply_cash_pair_pnl`. New callers should pass `chip_flows`;
    the old path is kept so out-of-tree integrations don't break.

    `now` defaults to `datetime.utcnow()` for `record_event`'s
    decay anchor. `hand_id` is forwarded to `record_event` for the
    `MemorableHand` sidecar.
    """
    if not events and not chip_flows:
        return
    if now is None:
        now = datetime.utcnow()

    # Per-pair suppression: skip any event/flow touching a suppressed pid
    # (casino fish). Both directions — observer OR opponent — so a fish
    # neither learns nor is learned about, while grinder/human pairs at
    # the same table still accrue history normally.
    _suppress = suppress_ids or frozenset()

    for event in events or []:
        if _suppress and (event.actor_id in _suppress or event.target_id in _suppress):
            continue
        manager.record_event(
            actor_id=event.actor_id,
            target_id=event.target_id,
            event=event.event,
            impact_score=event.impact_score,
            context_multiplier=event.context_multiplier,
            narrative=event.narrative,
            hand_summary=event.hand_summary,
            hand_id=hand_id,
            now=now,
        )

    if cash_pair_repo is None:
        return

    # v109 invariant: every cash_pair_stats write needs a sandbox_id
    # so the admin Chip Economy panel can scope Won/Lost/Net by the
    # sandbox dropdown. Callers that wire `cash_pair_repo` must also
    # supply the sandbox they're playing in; refuse to write rather
    # than fall back to an empty-string bucket that would silently
    # mix sandboxes back together.
    if sandbox_id is None:
        logger.warning(
            "dispatch_events: cash_pair_repo wired without sandbox_id; "
            "skipping cash_pair_stats writes (PnL won't accumulate this hand)"
        )
        return

    if chip_flows is not None:
        # New path: every chip flow tallies into cash_pair_stats,
        # regardless of pot size. This is the canonical surface for
        # lifetime PnL between two players.
        resolve = id_resolver or (lambda name: name)
        for flow in chip_flows:
            if flow.chips <= 0:
                continue
            winner_id = resolve(flow.winner)
            loser_id = resolve(flow.loser)
            if not winner_id or not loser_id:
                continue
            if winner_id in _suppress or loser_id in _suppress:
                continue
            cash_pair_repo.apply_cash_pair_pnl(
                winner_id=winner_id,
                loser_id=loser_id,
                chips=flow.chips,
                sandbox_id=sandbox_id,
            )
        return

    # Legacy path: derive cash PnL from BIG_WIN events only.
    for event in events or []:
        if event.event is not RelationshipEvent.BIG_WIN:
            continue
        if event.chips_won <= 0:
            continue
        if _suppress and (event.actor_id in _suppress or event.target_id in _suppress):
            continue
        cash_pair_repo.apply_cash_pair_pnl(
            winner_id=event.actor_id,
            loser_id=event.target_id,
            chips=event.chips_won,
            sandbox_id=sandbox_id,
        )
