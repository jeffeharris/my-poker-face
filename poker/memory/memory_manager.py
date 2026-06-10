"""
AI Memory Manager - Central orchestrator for all memory systems.

Coordinates:
- Hand history recording
- Session memory per player
- Opponent modeling
- Commentary generation
"""

import logging
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from ..hand_narrator import narrate_key_moments
from .cbet_detector import CbetDetector
from .commentary_filter import should_player_comment
from .commentary_generator import CommentaryGenerator, HandCommentary
from .hand_history import HandHistoryRecorder, RecordedHand
from .hand_outcome_detector import HandOutcomeDetector, dispatch_events
from .opponent_model import OpponentModelManager
from .session_memory import SessionMemory

logger = logging.getLogger(__name__)

# Async equity telemetry (live only). The showdown equity-at-action recording is
# best-effort enrichment that writes only to in-memory opponent models — it has
# no bearing on the hand result, so in a LIVE game it can run off the
# hand-completion path. A single worker serializes the writes (no telemetry-vs-
# telemetry races); main-thread reads of the maturing models are benign-racy and
# enrichment-only. Default OFF so SIMS/TESTS stay synchronous + deterministic
# (the economy sim uses this same AIMemoryManager); the live app flips it on.
ASYNC_EQUITY_TELEMETRY = False
_EQUITY_TELEMETRY_EXECUTOR = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix='equity-telemetry'
)


def enable_async_equity_telemetry() -> None:
    """Call once at LIVE app startup to move showdown equity recording off the
    hand-completion critical path. Never call from sims/tests (keeps them
    deterministic)."""
    global ASYNC_EQUITY_TELEMETRY
    ASYNC_EQUITY_TELEMETRY = True


def normalize_action_amount(
    action: str,
    raw_amount: int,
    *,
    highest_bet: int,
    player_bet: int,
    player_stack: int,
) -> int:
    """Convert a `BoundedOption` action amount into the chip INCREMENT to record.

    `BoundedOption` sets `raise_to=0` for `call` and `all_in` (the action
    dispatcher reads the cost-to-call / remaining stack directly), but the
    contribution + chip-flow accounting (`RecordedHand.get_player_contributions`
    → `allocate_chip_flow`) needs the actual chips the player put in. Recording
    the raw `0` makes a caller/shover contribute nothing, so `allocate_chip_flow`
    drops them as a loser and silently kills BIG_WIN/BIG_LOSS/KNOCKOUT events and
    `cash_pair_stats` PnL — invisible, because real chip settlement is separate.

    - `call`  → the call cost, clamped to the player's stack.
    - `all_in`→ the player's entire remaining stack (the shove increment), which
      matches `poker_game.player_all_in` and the `all_in` branch of
      `get_player_contributions`.
    - `raise`/`bet` → pass through unchanged (raise-TO snapshots; the
      contribution helper deltas them against prior in-phase commitment).

    All three recording paths (live web, lobby sim, experiment runner) MUST go
    through this one helper so the normalization can't drift between them.
    """
    if action == 'call':
        return max(0, min(highest_bet - player_bet, player_stack))
    if action == 'all_in':
        return max(0, player_stack)
    return raw_amount


class AIMemoryManager:
    """Orchestrates all memory systems for AI players in a game."""

    def __init__(
        self,
        game_id: str,
        db_path: Optional[str] = None,
        owner_id: Optional[str] = None,
        commentary_enabled: Optional[bool] = None,
    ):
        """Initialize the memory manager.

        Args:
            game_id: Unique identifier for the game
            db_path: Path to database for persistence (optional)
            owner_id: Owner/user ID for tracking (optional)
            commentary_enabled: Per-instance override (optional). If None, falls
                                back to the ENABLE_AI_COMMENTARY feature flag.
        """
        self.game_id = game_id
        self.db_path = db_path
        self.owner_id = owner_id
        # Allow instance-level override for commentary generation
        self._commentary_enabled_override = commentary_enabled

        # Core systems
        self.hand_recorder = HandHistoryRecorder(game_id)
        self.opponent_model_manager = OpponentModelManager()
        self.commentary_generator = CommentaryGenerator(game_id=game_id, owner_id=owner_id)

        # Phase 3 (relationship layer): detector fires per completed
        # hand. Shares the manager's `_name_to_id` dict by reference
        # so a later `register_player_id` call surfaces the new id to
        # the detector on the next emission without an explicit sync.
        # `_relationship_repo` defaults to None — `set_relationship_repo`
        # below activates the dispatch path. Without it the detector
        # is silent.
        self.hand_outcome_detector = HandOutcomeDetector(
            name_to_id=self.opponent_model_manager._name_to_id,
        )
        self._relationship_repo = None
        self._cash_mode: bool = False
        # Pids whose relationship/cash-pair writes are suppressed in BOTH
        # directions (observer OR opponent). Set via `set_fish_ids` for
        # casino fish: they're transient chip-donors nobody should learn
        # about, and they don't read the dossier themselves. Grinders and
        # the human still build history with each other at the casino.
        self._fish_ids: set = set()
        # v109: sandbox_id is required for every cash_pair_stats write so
        # the admin Chip Economy panel can scope Won/Lost/Net per sandbox.
        # `set_relationship_repo(cash_mode=True, sandbox_id=...)` populates
        # this; tournaments leave it None (cash_mode=False bypasses the
        # cash-PnL path entirely).
        self._sandbox_id: Optional[str] = None
        # Table max buy-in in chips. Required for STACK_DOMINANCE
        # detection — the detector compares each seat's starting_stack
        # against `STACK_DOMINANCE_THRESHOLD * max_buy_in`. Only set
        # for cash-mode games where `cash_stake_label` is known at
        # game-creation time; tournaments leave it None and the
        # detector silently skips.
        self._table_max_buy_in: Optional[int] = None
        # Dedup for cash_pair_stats writes — a replay of the same
        # hand_number through `_process_relationship_events` (e.g.,
        # tests, recovery flows) must not double-apply PnL. Event-axis
        # dedup is handled inside the detector; this is the parallel
        # gate for the chip-flow path.
        self._cash_pnl_emitted: Set[int] = set()

        # Per-player session memories
        self.session_memories: Dict[str, SessionMemory] = {}

        # Tracking
        self.hand_count = 0
        self.initialized_players: set = set()

        # C-bet tracking — delegated to a CbetDetector so simulator
        # paths that bypass MemoryManager can drive the same state
        # machine. Reset on hand start.
        self._cbet_detector = CbetDetector()

        # Phase 6.7a: per-street live aggressor for spot-aware exploitation.
        # Reset on hand start AND each street transition. Updated only on
        # accepted postflop bet/raise/all_in. Preflop aggressor still lives
        # in _preflop_raiser; this field is the post-flop counterpart.
        self._recent_aggressor_name: Optional[str] = None
        self._current_street: Optional[str] = None

        # Thread safety for parallel commentary generation
        self._lock = threading.Lock()
        self._last_recorded_hand: Optional[RecordedHand] = None

        # Persistence layer (set externally to avoid circular imports)
        self._persistence = None

    @property
    def sandbox_id(self) -> Optional[str]:
        """The sandbox this game is bound to, or None for non-sandbox games.

        Set by `set_relationship_repo(cash_mode=True, sandbox_id=...)` for
        Circuit games. Read by the dossier observation fold to gate
        per-sandbox lifetime accrual (non-Circuit games stay None → no fold).
        """
        return self._sandbox_id

    def set_hand_history_repo(self, hand_history_repo) -> None:
        """Set the hand history repository for saving hand history.

        Args:
            hand_history_repo: HandHistoryRepository instance
        """
        self._persistence = hand_history_repo

    def _process_relationship_events(
        self,
        recorded_hand: RecordedHand,
        equity_history=None,
    ) -> None:
        """Run the hand-outcome detector and dispatch its events.

        Silent no-op when no relationship_repo is wired
        (`set_relationship_repo` wasn't called). Otherwise: ask the
        detector for events triggered by this hand, then dispatch
        each through `record_event` (relationship axis updates) and,
        in cash mode, through `apply_cash_pair_pnl` (cumulative_pnl
        + hands_played_cash bilateral writes).

        `equity_history` (optional `HandEquityHistory`): when
        supplied, BAD_BEAT detection runs. Built by `EquityTracker`
        in both production paths (experiment runner + Flask game
        handler) before `on_hand_complete` runs. Passes None when a
        caller hasn't computed equity (e.g., custom integrations);
        in that case BAD_BEAT silently no-ops.

        Wrapped in a broad try/except: a detector or dispatch
        failure must not block the downstream session_memory and
        commentary paths. Errors are logged and swallowed; the
        relationship layer simply misses this hand's events.
        """
        if self._relationship_repo is None:
            return
        try:
            # STACK_DOMINANCE wiring: requires cash mode, a known
            # table cap, AND a sandbox id (PnL is sandbox-scoped via
            # cash_pair_stats). Missing any of the three skips the
            # detector entirely — without the PnL gate, every seated
            # peer would resent every deep stack, which is "winning
            # tax" behavior the design explicitly avoids. The lookup
            # closure captures repo + sandbox by local-variable
            # rebind so a later set_relationship_repo with a
            # different sandbox doesn't poison this hand's reads.
            stack_dom_max: Optional[int] = None
            stack_dom_lookup: Optional[Callable[[str, str], int]] = None
            hands_played_lookup: Optional[Callable[[str, str], int]] = None
            if self._cash_mode and self._table_max_buy_in and self._sandbox_id is not None:
                stack_dom_max = self._table_max_buy_in
                relationship_repo = self._relationship_repo
                sandbox_id = self._sandbox_id

                def stack_dom_lookup(observer_id: str, deep_id: str) -> int:
                    stats = relationship_repo.load_cash_pair_stats(
                        observer_id,
                        deep_id,
                        sandbox_id=sandbox_id,
                    )
                    return stats.cumulative_pnl if stats is not None else 0

                # Shared-hand volume gate for the RIVAL/NEMESIS tiers — the
                # persisted per-pair hand count (symmetric).
                def hands_played_lookup(a_id: str, b_id: str) -> int:
                    stats = relationship_repo.load_cash_pair_stats(
                        a_id,
                        b_id,
                        sandbox_id=sandbox_id,
                    )
                    return stats.hands_played_cash if stats is not None else 0

            events = self.hand_outcome_detector.detect_events(
                recorded_hand,
                equity_history=equity_history,
                max_buy_in=stack_dom_max,
                cash_pnl_lookup=stack_dom_lookup,
                hands_played_lookup=hands_played_lookup,
            )
            # Cash pair PnL feeds from every chip flow (no big-pot
            # gate), independent of whether relationship-axis events
            # fired. Compute flows even when `events` is empty — small
            # pots still accumulate. Replays of the same hand_number
            # are short-circuited to None (skips the chip_flows path
            # entirely) so cumulative_pnl can't double-apply.
            already_emitted = recorded_hand.hand_number in self._cash_pnl_emitted
            chip_flows = (
                self.hand_outcome_detector.compute_chip_flows(recorded_hand)
                if self._cash_mode and not already_emitted
                else None
            )
            if chip_flows:
                self._cash_pnl_emitted.add(recorded_hand.hand_number)
            if not events and not chip_flows:
                return
            dispatch_events(
                events,
                self.opponent_model_manager,
                cash_pair_repo=(self._relationship_repo if self._cash_mode else None),
                chip_flows=chip_flows,
                # Detector carries the name→id map (initialized from
                # the manager's `_name_to_id`), and its `_resolve_id`
                # falls back to the name itself when no id is registered.
                id_resolver=self.hand_outcome_detector._resolve_id,
                hand_id=recorded_hand.hand_number,
                sandbox_id=self._sandbox_id,
                suppress_ids=self._fish_ids,
            )
        except Exception as e:
            # Loud: a swallowed dispatch failure here means relationship
            # state, cash_pair_stats, and memorable_hands all silently
            # stop writing for the rest of the session. Log at ERROR
            # with full traceback so the next regression surfaces in
            # production logs instead of accumulating zero-row sessions.
            logger.error(
                "HandOutcomeDetector dispatch failed for hand %s: %s",
                getattr(recorded_hand, "hand_number", "?"),
                e,
                exc_info=True,
            )

    def set_relationship_repo(
        self,
        relationship_repo,
        *,
        cash_mode: bool = False,
        sandbox_id: Optional[str] = None,
        table_max_buy_in: Optional[int] = None,
    ) -> None:
        """Wire the relationship repository into the manager + detector.

        Required for Phase 3 relationship-event population. Without
        this call the detector is silent: `on_hand_complete` skips
        the dispatch entirely, so games without relationship
        persistence accumulate no axis state from gameplay.

        Args:
            relationship_repo: `RelationshipRepository` instance for
                both `record_event` (used internally by
                `OpponentModelManager`) and `cash_pair_stats`
                writes when `cash_mode=True`.
            cash_mode: When True, the per-hand dispatch also updates
                `cash_pair_stats` (cumulative_pnl + hands_played_cash).
                Tournament games keep this False — chips reset, PnL
                is meaningless.
            sandbox_id: v109 scoping field. Required when
                `cash_mode=True` so each pair's PnL accumulates per
                sandbox (the admin Chip Economy panel filters on it).
                Tournament callers leave it None.
            table_max_buy_in: Table cap in chips, used by
                `_detect_stack_dominance` to identify deep stacks.
                Only meaningful when `cash_mode=True`; tournament
                callers leave it None and STACK_DOMINANCE never fires.
        """
        self._relationship_repo = relationship_repo
        # OpponentModelManager.record_event requires this attribute.
        # Mutating directly is the documented contract — the repo is
        # an optional construction param and there's no setter yet.
        self.opponent_model_manager._relationship_repo = relationship_repo
        # The detector holds `_name_to_id` by reference so the manager
        # and detector see the same registry. If the OPM was swapped
        # (cold-load restores from DB), the detector's reference still
        # points at the old OPM's dict — re-point it now so newly
        # registered ids resolve correctly. Idempotent when the OPM
        # wasn't swapped: same dict identity, the assignment is a no-op.
        self.hand_outcome_detector._name_to_id = self.opponent_model_manager._name_to_id
        self._cash_mode = cash_mode
        if cash_mode and sandbox_id is None:
            logger.warning(
                "set_relationship_repo(cash_mode=True) called without "
                "sandbox_id — cash_pair_stats writes will be skipped "
                "this session"
            )
        self._sandbox_id = sandbox_id
        self._table_max_buy_in = table_max_buy_in

    def set_fish_ids(self, fish_ids) -> None:
        """Suppress relationship + cash-pair writes for these pids.

        Casino fish are transient chip-donors: nobody should accumulate a
        dossier *about* them, and they don't read one themselves. The
        suppression is per-pair (skips any event/flow where either side is
        a fish), so grinder↔grinder and grinder↔human history at the same
        casino table still accrues normally. Idempotent; pass an empty set
        to clear.
        """
        self._fish_ids = set(fish_ids or ())

    def set_table_max_buy_in(self, max_buy_in: Optional[int]) -> None:
        """Set the table cap used by STACK_DOMINANCE detection.

        Separate from `set_relationship_repo` because the cold-load
        path resolves the stake_label from the persisted big_blind
        AFTER it wires the relationship repo. Callers that know the
        cap at repo-wiring time can pass `table_max_buy_in` to
        `set_relationship_repo` instead and skip this entirely.
        """
        self._table_max_buy_in = max_buy_in

    @property
    def last_preflop_aggressor(self) -> Optional[str]:
        """Name of the last player to make an accepted preflop raise/all-in.

        Phase 6.6: surfaces the c-bet detector's preflop-aggressor state
        for HU c-bet exploitation gating. Resets at hand start. Reads
        from accepted-action recording, not controller intent.
        """
        return self._cbet_detector.preflop_aggressor

    @property
    def recent_aggressor_name(self) -> Optional[str]:
        """Name of the most recent postflop aggressor on the current street.

        Phase 6.7a: surfaces the per-street live aggressor for
        select_primary_aggressor() tie-break in multiway facing-bet
        spots. Reset on hand start and on each street transition.
        Updated only on accepted postflop bet/raise/all_in.

        Returns None on preflop streets (preflop aggression uses
        `last_preflop_aggressor` instead) and whenever no postflop
        aggression has occurred on the current street yet.
        """
        return self._recent_aggressor_name

    def record_preflop_aggression(self, player_name: str) -> None:
        """Manually record a preflop aggressor (test / sim-path hook).

        Production paths reach this state via `on_action()` and shouldn't
        call this directly. Simulators that bypass MemoryManager (e.g.
        simulate_bb100, analyze_6max_vs_rules) drive their own
        CbetDetector instead; this method exists for tests that hold a
        MemoryManager and want to seed the aggressor without firing a
        full on_action.
        """
        self._cbet_detector.record_preflop_aggression(player_name)

    def record_postflop_aggression(self, player_name: str, phase: str) -> None:
        """Manually record a postflop aggressor (sim-path hook).

        Production paths reach this state via `on_action()`; sims that
        bypass MemoryManager (analyze_6max_vs_rules, simulate_bb100) use
        this to feed the same signal. Caller is responsible for street
        transition (passing the correct phase) — this method does not
        reset on its own.
        """
        if phase in ('FLOP', 'TURN', 'RIVER'):
            self._recent_aggressor_name = player_name
            self._current_street = phase

    def initialize_for_player(self, player_name: str, personality_id: Optional[str] = None) -> None:
        """Set up memory systems for an AI player.

        Args:
            player_name: Name of the AI player
            personality_id: Stable personality_id (slug) for this player.
                Passed through to the opponent_model_manager so cross-session
                callers (relationship layer, AI bankrolls) can key on the
                id rather than the display name. None for AI players whose
                personality predates v85 or wasn't resolved at startup.
        """
        if player_name in self.initialized_players:
            return

        # Create session memory with DB backing if persistence is available
        session_memory = SessionMemory(player_name)
        if self._persistence:
            session_memory.set_hand_history_repo(self._persistence, self.game_id)
        self.session_memories[player_name] = session_memory

        # Register the player's stable personality_id with the opponent
        # model manager so newly-created OpponentModels carry it and
        # save_opponent_models persists it. Always call even when None,
        # so the registry distinguishes "known no id" (human guests,
        # pre-v85 personalities) from "never registered."
        self.opponent_model_manager.register_player_id(player_name, personality_id)

        self.initialized_players.add(player_name)
        logger.info(
            f"Initialized memory systems for {player_name} " f"(personality_id={personality_id!r})"
        )

    def initialize_human_observer(
        self, player_name: str, personality_id: Optional[str] = None
    ) -> None:
        """Add human player as an observer for opponent modeling.

        Unlike AI players, humans don't need session memory or other AI systems,
        but they should still track observations about opponents.

        Args:
            player_name: Name of the human player
            personality_id: Almost always None for humans (they aren't
                personalities). Plumbed through anyway so callers can
                use a single uniform path for both player types.
        """
        if player_name in self.initialized_players:
            return

        # Register with the opponent model manager. Most human players
        # have personality_id=None; explicitly recording that prevents
        # repeated name-lookup attempts.
        self.opponent_model_manager.register_player_id(player_name, personality_id)

        self.initialized_players.add(player_name)
        logger.info(
            f"Initialized human observer: {player_name} " f"(personality_id={personality_id!r})"
        )

    def on_hand_start(
        self, game_state: Any, hand_number: int, deck_seed: Optional[int] = None
    ) -> None:
        """Called when a new hand begins.

        Args:
            game_state: Current PokerGameState
            hand_number: The hand number in this game
            deck_seed: Optional deterministic deck seed used for this hand
        """
        self.hand_count = hand_number
        self.hand_recorder.start_hand(game_state, hand_number, deck_seed=deck_seed)

        # Reset c-bet tracking for new hand.
        self._cbet_detector.reset_for_new_hand()

        # Phase 6.7a: reset per-street aggressor state.
        self._recent_aggressor_name = None
        self._current_street = None

        # Phase 6/6.5: record that each opponent was dealt this hand. This
        # is the correct denominator for VPIP/PFR/all_in_frequency — opponents
        # who fold before action reaches them never trigger observe_action,
        # so hands_dealt has to be incremented independently.
        all_player_names = [p.name for p in game_state.players]
        for observer in self.initialized_players:
            opponents = [n for n in all_player_names if n != observer]
            if opponents:
                self.opponent_model_manager.record_hand_dealt(
                    observer=observer,
                    opponents=opponents,
                    hand_number=hand_number,
                )

        logger.debug(f"Started recording hand #{hand_number}")

    def record_blinds(self, game_state: Any) -> None:
        """Record blind posts as actions at the start of a hand.

        Args:
            game_state: Current PokerGameState with table_positions and current_ante
        """
        if not self.hand_recorder.current_hand:
            logger.warning("record_blinds called but no hand in progress")
            return

        table_positions = getattr(game_state, 'table_positions', {})
        bb_amount = game_state.current_ante
        sb_amount = bb_amount // 2

        pot_running = 0

        # Record SB post. Keys come from PokerGameState.table_positions, which
        # uses 'small_blind_player'/'big_blind_player' (NOT 'SB'/'BB') — the old
        # short keys silently no-op'd, so blinds were never recorded.
        sb_player = table_positions.get('small_blind_player')
        if sb_player:
            pot_running += sb_amount
            self.hand_recorder.record_action(
                player_name=sb_player,
                action='post_blind',
                amount=sb_amount,
                phase='PRE_FLOP',
                pot_total=pot_running,
            )

        # Record BB post
        bb_player = table_positions.get('big_blind_player')
        if bb_player:
            pot_running += bb_amount
            self.hand_recorder.record_action(
                player_name=bb_player,
                action='post_blind',
                amount=bb_amount,
                phase='PRE_FLOP',
                pot_total=pot_running,
            )

        logger.debug(f"Recorded blinds: SB={sb_player}(${sb_amount}), BB={bb_player}(${bb_amount})")

    def on_action(
        self,
        player_name: str,
        action: str,
        amount: int,
        phase: str,
        pot_total: int,
        active_players: List[str] = None,
    ) -> None:
        """Record an action and update opponent models.

        Args:
            player_name: Name of player who acted
            action: The action taken ('fold', 'check', 'call', 'raise', 'bet', 'all_in')
            amount: Amount added to pot
            phase: Current game phase ('PRE_FLOP', 'FLOP', 'TURN', 'RIVER')
            pot_total: Total pot after the action
            active_players: List of players still in the hand (for c-bet tracking)
        """
        # Record to hand history
        self.hand_recorder.record_action(player_name, action, amount, phase, pot_total)

        # Phase 6.7a: per-street live aggressor reset. When the phase
        # changes between actions, the previous street's aggression no
        # longer applies. Detect the transition off the prior action's
        # phase rather than off a separate street-transition hook so
        # all callers go through one path.
        if self._current_street != phase:
            self._recent_aggressor_name = None
            self._current_street = phase

        # Phase 7.5 Step 0: capture was_facing_bet BEFORE the current
        # action updates _recent_aggressor_name. The snapshot reflects
        # the state the actor SAW at decision time. On postflop streets,
        # facing a bet = there's a prior aggressor on this street whose
        # bet hasn't been folded out. The actor's own previous action
        # on this street (if they were the prior aggressor) is treated
        # as "not facing a bet" — they were free to act when they bet,
        # and someone re-raising them is what would make them face a
        # bet next.
        #
        # Preflop extension (for opportunity-normalized VPIP/PFR):
        # facing a bet preflop = a live RAISE above the blind has been
        # made by someone other than the current player. Forced blind
        # posts ('sb'/'bb') are not raises and don't count. The
        # preflop aggressor lives on the cbet detector and is read
        # BEFORE cbet_detector.record_action updates it below.
        if phase in ('FLOP', 'TURN', 'RIVER'):
            was_facing_bet = (
                self._recent_aggressor_name is not None
                and self._recent_aggressor_name != player_name
            )
        elif phase == 'PRE_FLOP':
            prior_raiser = self._cbet_detector.preflop_aggressor
            was_facing_bet = prior_raiser is not None and prior_raiser != player_name
        else:
            was_facing_bet = False

        # Phase 6.7a: postflop live aggressor — last accepted bet/raise/
        # all_in on flop/turn/river. Used by select_primary_aggressor()
        # to disambiguate tied-bet spots when multiple opponents called.
        if phase in ('FLOP', 'TURN', 'RIVER') and action in ('bet', 'raise', 'all_in'):
            self._recent_aggressor_name = player_name

        # C-bet detection (preflop aggressor → flop bet → response). The
        # detector also owns the preflop-aggressor field that Phase 6.6's
        # `last_preflop_aggressor` property surfaces.
        cbet_responses = self._cbet_detector.record_action(
            player_name=player_name,
            action=action,
            phase=phase,
            active_players=active_players,
        )
        for opp_name, folded in cbet_responses:
            logger.debug(f"{opp_name} {'folded to' if folded else 'called/raised'} c-bet")
            for observer in self.initialized_players:
                if observer != opp_name:
                    model = self.opponent_model_manager.get_model(observer, opp_name)
                    model.tendencies.update_fold_to_cbet(folded)

        # Phase 8.1a: drain PFR-attempt events (typically zero or one
        # per call). When the preflop aggressor takes their first
        # flop action with a clean c-bet decision (bet/raise/check
        # without anyone donk-betting ahead of them), apply the
        # attempt to every observer's model of that player.
        for pfr_name, attempted in self._cbet_detector.consume_pfr_attempt_events():
            logger.debug(f"{pfr_name} {'attempted' if attempted else 'declined'} c-bet")
            for observer in self.initialized_players:
                if observer != pfr_name:
                    model = self.opponent_model_manager.get_model(observer, pfr_name)
                    model.tendencies.update_cbet_attempt(attempted)

        # Phase B Item 1: drain barrel-attempt and third-barrel-attempt
        # events. Same shape as the PFR-attempt drain above.
        for pfr_name, attempted in self._cbet_detector.consume_barrel_attempt_events():
            logger.debug(f"{pfr_name} {'fired' if attempted else 'declined'} turn barrel")
            for observer in self.initialized_players:
                if observer != pfr_name:
                    model = self.opponent_model_manager.get_model(observer, pfr_name)
                    model.tendencies.update_barrel_attempt(attempted)
        for pfr_name, attempted in self._cbet_detector.consume_third_barrel_attempt_events():
            logger.debug(f"{pfr_name} {'fired' if attempted else 'declined'} third barrel")
            for observer in self.initialized_players:
                if observer != pfr_name:
                    model = self.opponent_model_manager.get_model(observer, pfr_name)
                    model.tendencies.update_third_barrel_attempt(attempted)
        # Phase B Item 4: drain flop-check-then-barrel events. Mirrors
        # the barrel-attempt drain above but for the OOP-check-then-
        # barrel pattern (no preflop-aggressor gating).
        for (
            checker_name,
            attempted,
        ) in self._cbet_detector.consume_flop_check_barrel_attempt_events():
            logger.debug(
                f"{checker_name} {'fired' if attempted else 'declined'} " "flop-check-then-barrel"
            )
            for observer in self.initialized_players:
                if observer != checker_name:
                    model = self.opponent_model_manager.get_model(observer, checker_name)
                    model.tendencies.update_flop_check_barrel_attempt(attempted)

        # Update opponent models for all observers (including self-observation
        # for coaching stats like VPIP/PFR)
        for observer in self.initialized_players:
            self.opponent_model_manager.observe_action(
                observer=observer,
                opponent=player_name,
                action=action,
                phase=phase,
                is_voluntary=(action not in ('sb', 'bb')),
                hand_number=self.hand_count,
                was_facing_bet=was_facing_bet,
            )

    def on_community_cards(self, phase: str, cards: List[str]) -> None:
        """Record community cards dealt.

        Args:
            phase: Game phase ('FLOP', 'TURN', 'RIVER')
            cards: List of card strings
        """
        self.hand_recorder.record_community_cards(phase, cards)

    def on_hand_complete(
        self,
        winner_info: Dict[str, Any],
        game_state: Any,
        ai_players: Dict[str, Any] = None,
        skip_commentary: bool = False,
        equity_history=None,
        record_showdown_equity: bool = True,
    ) -> Dict[str, HandCommentary]:
        """Process end of hand - record history, update models, optionally generate commentary.

        Args:
            winner_info: Dict with 'winnings', 'hand_name', 'hand_rank'
            game_state: Current game state
            ai_players: Dict mapping player names to their AIPokerPlayer objects
            skip_commentary: If True, skip commentary generation (for async flow)
            record_showdown_equity: If False, skip the showdown
                equity-at-actions enrichment (`_record_showdown_equity_at_actions`).
                That step runs inline eval7 (iterations=400 per postflop
                showdown action) and is the dominant cost of this method;
                it only feeds opponent-model equity buckets, not the
                relationship detector. The cash lobby sim passes False so
                relationship-simming stays write-light on the hot path.
            equity_history: Optional HandEquityHistory built by the
                caller before this method runs. Forwarded to the
                relationship detector to enable BAD_BEAT detection.
                Both production paths (experiment runner + Flask
                game handler) wire this; custom integrations may
                pass None to skip BAD_BEAT.

        Returns:
            Dict mapping player names to their HandCommentary (or None if skip_commentary)
        """
        ai_players = ai_players or {}

        # Complete hand recording
        try:
            recorded_hand = self.hand_recorder.complete_hand(winner_info, game_state)
            # Store for async commentary generation (thread-safe)
            with self._lock:
                self._last_recorded_hand = recorded_hand

            # Persist hand to database
            if self._persistence:
                try:
                    self._persistence.save_hand_history(recorded_hand)
                except Exception as e:
                    logger.warning(f"Failed to persist hand history: {e}")
        except Exception as e:
            logger.error(f"Failed to complete hand recording: {e}")
            return {}

        # Update opponent models with showdown info
        if recorded_hand.was_showdown:
            # Track all players at showdown (winners and losers)
            for player in recorded_hand.players:
                outcome = recorded_hand.get_player_outcome(player.name)

                # Skip players who folded - they weren't at showdown
                if outcome == 'folded':
                    continue

                # Update opponent models for all observers
                for observer in self.initialized_players:
                    if observer != player.name:
                        model = self.opponent_model_manager.get_model(observer, player.name)
                        model.observe_showdown(won=(outcome == 'won'))

            # Polarization Phase A: record equity-at-action for each
            # postflop bet/raise/call by every showdown player. Walks
            # each player's postflop action history; for each action,
            # computes the equity they had at that moment (their hole
            # cards vs the board snapshot for that street) and records
            # it into the matching action bucket on every observer's
            # model of them.
            #
            # Wrapped in a broad try/except: the equity calculation is
            # an enrichment, not a hard requirement for showdown bookkeeping.
            # If eval7 is unavailable or the cards/board can't be parsed
            # for any reason, fall through silently so the rest of the
            # showdown path stays unaffected.
            def _record(rh=recorded_hand):
                try:
                    self._record_showdown_equity_at_actions(rh)
                except Exception as e:
                    logger.warning(f"Polarization Phase A equity recording failed: {e}")

            if not record_showdown_equity:
                # Lean path (cash lobby sim): skip the eval7 enrichment
                # entirely — it's the dominant per-hand cost and the
                # opponent-model equity buckets it feeds are not needed
                # for relationship detection.
                pass
            elif ASYNC_EQUITY_TELEMETRY:
                # Live: off the hand-completion path (best-effort enrichment).
                try:
                    _EQUITY_TELEMETRY_EXECUTOR.submit(_record)
                except Exception:
                    _record()  # executor unavailable → fall back to sync
            else:
                _record()  # sims/tests: synchronous + deterministic

        # Sizing-aware Phase A: fold_to_big_bet is NOT showdown-gated — every
        # hand where someone faces a large postflop bet teaches us whether they
        # over-fold to it (the Phase C attack trigger). Runs on all hands.
        try:
            self._record_fold_to_big_bet(recorded_hand)
        except Exception as e:
            logger.warning(f"Sizing-aware fold_to_big_bet recording failed: {e}")

        # §5j: stab frequency — bet rate when CHECKED TO postflop (gates the
        # stab-defense). Not showdown-gated; runs on all hands like fold_to_big_bet.
        try:
            self._record_stab_frequency(recorded_hand)
        except Exception as e:
            logger.warning(f"Stab-frequency recording failed: {e}")

        # Phase 3: relationship event detection + dispatch. Runs only
        # when a relationship_repo is wired; tournament-only games
        # without persistence stay detector-silent. Wrapped in
        # try/except so detector failures (e.g., transient DB lock)
        # don't block commentary / session_memory updates downstream.
        self._process_relationship_events(
            recorded_hand,
            equity_history=equity_history,
        )

        # Update session memories
        for player_name, session_memory in self.session_memories.items():
            # Determine player's outcome
            outcome = recorded_hand.get_player_outcome(player_name)

            # Calculate amount won/lost
            amount = 0
            for winner in recorded_hand.winners:
                if winner.name == player_name:
                    amount = winner.amount_won
                    break
            if outcome == 'lost':
                # Estimate loss from actions
                player_actions = recorded_hand.get_player_actions(player_name)
                amount = -sum(a.amount for a in player_actions)

            # Extract notable events (use hand narrator for richer key moments)
            notable_events = self.commentary_generator.extract_notable_events(
                recorded_hand, player_name
            )
            try:
                key_moment = narrate_key_moments(
                    recorded_hand, player_name, big_blind=game_state.current_ante
                )
                if key_moment:
                    notable_events = [key_moment] + notable_events
            except Exception as e:
                logger.debug(f"Key moment narration failed for {player_name}: {e}")

            # Update session memory
            session_memory.record_hand_outcome(
                hand_number=recorded_hand.hand_number,
                outcome=outcome,
                pot_size=recorded_hand.pot_size,
                amount_won_or_lost=amount,
                notable_events=notable_events,
            )

        # Skip commentary generation if requested (for async flow)
        if skip_commentary:
            return {}

        # Generate commentary synchronously (legacy flow)
        return self.generate_commentary_for_hand(ai_players)

    def generate_commentary_for_hand(
        self,
        ai_players: Dict[str, Any],
        on_commentary_ready: Optional[Callable] = None,
        big_blind: Optional[int] = None,
        human_bio: Optional[str] = None,
        human_name: Optional[str] = None,
    ) -> Dict[str, HandCommentary]:
        """Generate commentary for the last completed hand.

        This can be called asynchronously after on_hand_complete(skip_commentary=True).
        All AI players' commentary is generated in parallel using ThreadPoolExecutor.

        Thread Safety:
            - Acquires lock to get snapshot of recorded hand
            - Creates immutable snapshots of session context before spawning threads
            - Each thread only reads from its own snapshot

        Args:
            ai_players: Dict mapping player names to context dicts:
                        {'ai_player': AIPokerPlayer, 'is_eliminated': bool,
                         'spectator_context': Optional[str]}
            on_commentary_ready: Optional callback called immediately when each commentary
                                 is ready. Signature: (player_name, commentary) -> None
            big_blind: Current big blind for dynamic interest thresholds

        Returns:
            Dict mapping player names to their HandCommentary
        """
        # Thread-safe access to recorded hand - acquire lock, get snapshot, release
        with self._lock:
            if self._last_recorded_hand is None:
                logger.warning("No recorded hand available for commentary generation")
                return {}
            # RecordedHand is immutable (frozen dataclass), safe to share
            recorded_hand = self._last_recorded_hand
            # Clear to prevent memory leak - we have our reference now
            self._last_recorded_hand = None

        commentaries: Dict[str, HandCommentary] = {}

        # Check instance override first, then fall back to the ENABLE_AI_COMMENTARY flag
        from core.feature_flags import is_enabled

        commentary_enabled = (
            self._commentary_enabled_override
            if self._commentary_enabled_override is not None
            else is_enabled("ENABLE_AI_COMMENTARY")
        )
        if not commentary_enabled:
            return commentaries

        # A player who actually played THIS hand is never a "spectator" for it.
        # Guard against a stale/mis-scoped is_eliminated flag — e.g. the
        # tournament name-vs-id mismatch in generate_ai_commentary
        # (game_handler `is_eliminated = name not in active_players`, comparing a
        # display name to a set of tournament IDs) which told WINNERS they were
        # "watching from the rail", contradicting the recap and confusing every
        # model. recorded_hand.players is in the same name space, so this is a
        # robust, id-agnostic backstop. See EXP_008 root-cause notes.
        hand_participants = {p.name for p in recorded_hand.players}

        # Build list of players to generate commentary for
        # Apply filtering rules (preflop folds, eliminated players, etc.)
        players_to_process = []
        for player_name, context in ai_players.items():
            # Skip if no session memory
            if player_name not in self.session_memories:
                continue

            # Extract context (support both old format and new dict format)
            if isinstance(context, dict):
                is_eliminated = context.get('is_eliminated', False) and (
                    player_name not in hand_participants
                )
            else:
                is_eliminated = False

            # Apply commentary filter rules
            if should_player_comment(player_name, recorded_hand, is_eliminated):
                players_to_process.append(player_name)
            else:
                logger.debug(f"Filtering out {player_name} from commentary")

        if not players_to_process:
            return commentaries

        # Create thread-safe snapshots BEFORE spawning threads
        # This prevents race conditions if session memory or opponent models
        # are modified by another thread (e.g., new hand starting)
        player_snapshots: Dict[str, Dict[str, Any]] = {}
        for player_name in players_to_process:
            context = ai_players[player_name]

            # Extract context (support both old format and new dict format)
            if isinstance(context, dict):
                ai_player = context.get('ai_player')
                controller = context.get('controller')
                is_eliminated = context.get('is_eliminated', False) and (
                    player_name not in hand_participants
                )
                spectator_context = context.get('spectator_context')
            else:
                ai_player = context
                controller = None
                is_eliminated = False
                spectator_context = None

            # Skip RuleBots - they don't use LLM commentary
            if getattr(ai_player, 'is_rule_based', False):
                logger.debug(f"Skipping commentary for {player_name}: RuleBot (no LLM)")
                continue

            session_memory = self.session_memories[player_name]

            # Opponent behavioral summary, plus the human's self-description so
            # the AI's post-hand comment can riff on it ("nice hand, for someone
            # who calls themselves a shark").
            opponent_summaries = self.opponent_model_manager.get_table_summary(
                player_name, [p for p in ai_players if p != player_name], 200
            )
            if human_bio:
                who = human_name or "the human player"
                # Neutralize the section delimiter (mild prompt-injection defense).
                safe_bio = human_bio.replace('===', '==')
                bio_block = f"What {who} says about themselves: {safe_bio}"
                opponent_summaries = (
                    f"{opponent_summaries}\n\n{bio_block}" if opponent_summaries else bio_block
                )

            # Capture all data needed for commentary generation
            player_snapshots[player_name] = {
                'outcome': recorded_hand.get_player_outcome(player_name),
                'player_cards': list(recorded_hand.hole_cards.get(player_name, [])),
                'session_context': session_memory.get_context_for_prompt(100),
                'opponent_summaries': opponent_summaries,
                'confidence': getattr(ai_player, 'confidence', 'neutral'),
                'attitude': getattr(ai_player, 'attitude', 'neutral'),
                'chattiness': self._resolve_chattiness(controller, ai_player),
                'assistant': getattr(ai_player, 'assistant', None),
                'is_eliminated': is_eliminated,
                'spectator_context': spectator_context,
            }

        # Pre-roll _should_speak for each player and apply a per-hand
        # speaker cap. Without this, every AI who passes _should_reflect
        # (effectively all post-flop participants) emits a visible chat
        # line on showdown hands — 5 simultaneous reactions feels like
        # spam. Cap is soft: top-3 by priority get through unconditionally,
        # additional speakers must pass a 15% gate.
        speaker_overrides = self._cap_post_hand_speakers(recorded_hand, player_snapshots, big_blind)

        def generate_single_commentary(player_name: str) -> Tuple[str, Optional[HandCommentary]]:
            """Generate commentary for a single player. Returns (player_name, commentary).

            Uses pre-captured snapshot data to avoid accessing shared mutable state.
            """
            snapshot = player_snapshots[player_name]

            try:
                commentary = self.commentary_generator.generate_commentary(
                    player_name=player_name,
                    hand=recorded_hand,  # Immutable, safe to share
                    player_outcome=snapshot['outcome'],
                    player_cards=snapshot['player_cards'],
                    session_memory=None,  # Pass context string instead
                    opponent_models=None,  # Pass summary string instead
                    confidence=snapshot['confidence'],
                    attitude=snapshot['attitude'],
                    chattiness=snapshot['chattiness'],
                    assistant=snapshot['assistant'],
                    # Pre-computed context for thread safety
                    session_context_override=snapshot['session_context'],
                    opponent_context_override=snapshot['opponent_summaries'],
                    # New params for filtering and spectator mode
                    big_blind=big_blind,
                    is_eliminated=snapshot['is_eliminated'],
                    spectator_context=snapshot['spectator_context'],
                    should_speak_override=speaker_overrides.get(player_name),
                )
                return (player_name, commentary)
            except Exception as e:
                logger.warning(f"Failed to generate commentary for {player_name}: {e}")
                return (player_name, None)

        # Generate all commentaries in parallel
        with ThreadPoolExecutor(max_workers=len(players_to_process)) as executor:
            futures = {
                executor.submit(generate_single_commentary, player_name): player_name
                for player_name in players_to_process
            }

            for future in as_completed(futures):
                player_name, commentary = future.result()
                if commentary:
                    commentaries[player_name] = commentary
                    # Call callback immediately so commentary can be emitted right away
                    if on_commentary_ready:
                        try:
                            on_commentary_ready(player_name, commentary)
                        except Exception as e:
                            logger.warning(f"Commentary callback failed for {player_name}: {e}")

        return commentaries

    # Soft cap: above this many speakers per hand we start gating with the
    # OVERFLOW_SPEAK_PROB roll. Two reactions read as a quick exchange; three+
    # start to feel like everyone shouting over the hand. (The drama-score
    # speak gate already thins the field upstream — this caps the pile-on.)
    MAX_UNCAPPED_SPEAKERS = 2
    OVERFLOW_SPEAK_PROB = 0.15

    def _cap_post_hand_speakers(
        self,
        recorded_hand: RecordedHand,
        player_snapshots: Dict[str, Dict[str, Any]],
        big_blind: Optional[int],
    ) -> Dict[str, bool]:
        """Decide which players visibly speak after the hand.

        Per-player `_should_speak` rolls run first (so quiet personalities
        still self-suppress). If more than MAX_UNCAPPED_SPEAKERS clear
        their roll, the winner and biggest-contributor non-winner take the
        first two slots, the third slot is chattiness-weighted among the
        remaining, and anyone beyond that must clear a small extra gate.

        Returns a dict suitable for passing into
        `generate_commentary(..., should_speak_override=...)` — keys are
        every player in `player_snapshots`, values are the final decision.
        """
        wants_speak: Dict[str, bool] = {}
        for name, snap in player_snapshots.items():
            wants_speak[name] = self.commentary_generator._should_speak(
                recorded_hand,
                name,
                big_blind,
                snap['chattiness'],
            )

        speakers = [n for n, ok in wants_speak.items() if ok]
        if len(speakers) <= self.MAX_UNCAPPED_SPEAKERS:
            return wants_speak

        winner_names = {w.name for w in recorded_hand.winners}
        contributions = recorded_hand.get_player_contributions()

        def priority_key(name: str) -> tuple:
            # Sort key: winners first, then non-winners by chips committed
            # (biggest stake in the pot = most emotional investment in
            # talking about it), then chattiness as a tiebreaker.
            is_winner = name in winner_names
            return (
                0 if is_winner else 1,
                -contributions.get(name, 0),
                -player_snapshots[name]['chattiness'],
            )

        ranked = sorted(speakers, key=priority_key)
        protected = set(ranked[: self.MAX_UNCAPPED_SPEAKERS])
        ranked[self.MAX_UNCAPPED_SPEAKERS :]

        final: Dict[str, bool] = {}
        for name in wants_speak:
            if not wants_speak[name]:
                final[name] = False
            elif name in protected:
                final[name] = True
            else:
                # Rare extra speaker beyond the cap
                final[name] = random.random() < self.OVERFLOW_SPEAK_PROB

        kept = [n for n, v in final.items() if v]
        if len(speakers) != len(kept):
            logger.debug(
                f"Post-hand speaker cap applied: {len(speakers)} wanted, "
                f"{len(kept)} kept ({sorted(kept)})"
            )
        return final

    def get_decision_context(self, player_name: str, opponents: List[str]) -> str:
        """Get memory context for a decision prompt.

        Args:
            player_name: Name of the AI player making the decision
            opponents: List of opponent names at the table

        Returns:
            Formatted string with session and opponent context
        """
        parts = []

        # Session context
        if player_name in self.session_memories:
            session_ctx = self.session_memories[player_name].get_context_for_prompt()
            if session_ctx:
                parts.append(f"=== Your Session ===\n{session_ctx}")

        # Opponent summaries
        opponent_ctx = self.opponent_model_manager.get_table_summary(player_name, opponents)
        if opponent_ctx:
            parts.append(f"=== Opponent Intel ===\n{opponent_ctx}")

        return "\n\n".join(parts)

    def _record_showdown_equity_at_actions(self, recorded_hand) -> None:
        """Polarization Phase A: walk every showdown player's postflop
        actions and credit the equity-they-had-at-that-decision into
        the matching bet / raise / call bucket on every observer's
        OpponentModel of them.

        Equity is computed using EquityCalculator vs uniform random for
        each player's hole cards at the board snapshot for that street.
        Same equity definition as `player_decision_analysis.equity` so
        downstream consumers see consistent numbers across the codebase.

        Only fires for postflop actions (PRE_FLOP actions are skipped).
        Only bet / raise / call bucket; check / fold / all_in are no-ops
        in the per-action tracker (fold/check don't reveal strength,
        all_in is bucketed by the existing aggression_factor signal —
        adding it to the equity tracker would double-count when an
        all-in is also a raise, and miscount when it's a call-shove).

        Best effort: any failure to compute equity for a particular
        action (cards unparseable, board missing for that phase, etc.)
        silently skips that action without affecting the rest.
        """
        from poker.decision_analyzer import DecisionAnalyzer

        # Lower iteration count: this runs N times per showdown, the
        # result feeds a running mean, and we don't need solver-grade
        # precision for archetype classification.
        analyzer = DecisionAnalyzer(iterations=400)

        # Count active (non-folded) opponents per phase. Equity vs N
        # uniform random opponents is what `player_decision_analysis.equity`
        # uses, so the per-action stat ends up comparable to the live
        # decision-time equity field.
        non_folded_at_phase = self._count_non_folded_per_phase(recorded_hand)

        # Showdown players whose cards we know
        revealed_players = {
            p.name
            for p in recorded_hand.players
            if recorded_hand.get_player_outcome(p.name) != 'folded'
        }
        if not revealed_players:
            return

        # Phase → board snapshot. The recorder may store these either
        # as community_cards_by_phase (preferred) or we reconstruct
        # from the running community_cards list (fallback). Phase
        # naming follows the engine: 'FLOP' = first 3 cards, 'TURN'
        # = +1, 'RIVER' = +1.
        phase_boards = dict(recorded_hand.community_cards_by_phase or {})
        if not phase_boards:
            # Reconstruct from full community_cards if per-phase data
            # is missing. Use whatever cards are visible — better an
            # approximation than nothing.
            community = list(recorded_hand.community_cards or [])
            if len(community) >= 3:
                phase_boards['FLOP'] = community[:3]
            if len(community) >= 4:
                phase_boards['TURN'] = community[:4]
            if len(community) >= 5:
                phase_boards['RIVER'] = community[:5]

        # Sizing-aware Phase A: how big was each aggressive action relative to
        # the pot-before? Keyed by id(action); used to bin bet/raise equity into
        # big vs small for the per-opponent sizing_polarization_score.
        bet_fractions = recorded_hand.bet_fraction_by_action()

        # Walk each revealed player's postflop actions
        for player_name in revealed_players:
            hole_cards = recorded_hand.hole_cards.get(player_name)
            if not hole_cards:
                continue
            player_actions = recorded_hand.get_player_actions(player_name)

            for action in player_actions:
                phase = action.phase
                if phase == 'PRE_FLOP':
                    continue
                if action.action not in ('bet', 'raise', 'call'):
                    continue

                board = phase_boards.get(phase)
                if not board:
                    continue

                # Estimate opponent count at the moment of this action.
                # Use the count of non-folded other players at the start
                # of this phase as a proxy. Default to 1 if we can't
                # determine — equity vs 1 random opponent is the standard
                # Phase A definition.
                num_opp = max(1, non_folded_at_phase.get(phase, 1) - 1)

                try:
                    equity = analyzer.calculate_equity_vs_random(
                        player_hand=hole_cards,
                        community_cards=board,
                        num_opponents=num_opp,
                    )
                except Exception:
                    continue
                if equity is None:
                    continue

                # Credit the equity into every observer's model of this player
                for observer in self.initialized_players:
                    if observer == player_name:
                        continue
                    model = self.opponent_model_manager.get_model(observer, player_name)
                    model.tendencies.update_equity_at_action(action.action, equity)
                    # Sizing-aware Phase A: also bin this bet/raise's equity by
                    # how big it was — the bettor's size↔strength tell.
                    bet_fraction = bet_fractions.get(id(action))
                    if bet_fraction is not None and action.action in ('bet', 'raise'):
                        model.tendencies.update_equity_at_bet_size(equity, bet_fraction)

    def _record_fold_to_big_bet(self, recorded_hand) -> None:
        """Sizing-aware Phase A: live (all-hands) fold_to_big_bet tracking.

        Replays the hand in action order, tracking each player's committed chips
        and the running pot, to detect when a player FACES a large postflop bet
        (`cost_to_call / pot_before_the_bet >= SIZING_BIG_BET_POT_RATIO`) and
        records whether they folded. This is the offensive read for Phase C
        (attack measured over-folders with wider overbets) and, unlike the
        showdown-gated polarization score, it fires on every hand a big bet is
        faced — far better sample coverage. Preflop is excluded: a preflop
        "big bet" is a 3-bet/4-bet, a distinct signal from postflop sizing.
        """
        from .opponent_model import SIZING_BIG_BET_POT_RATIO

        committed: Dict[tuple, int] = {}  # (player, phase) -> chips in this round
        current_level: Dict[str, int] = {}  # phase -> highest bet-to level
        running_pot = 0
        for action in recorded_hand.actions:
            phase = action.phase
            name = action.player_name
            level = current_level.get(phase, 0)
            prior = committed.get((name, phase), 0)
            cost_to_call = level - prior

            if (
                phase in ('FLOP', 'TURN', 'RIVER')
                and cost_to_call > 0
                and action.action in ('fold', 'call', 'raise', 'all_in')
            ):
                pot_before = running_pot - cost_to_call
                if pot_before > 0 and cost_to_call / pot_before >= SIZING_BIG_BET_POT_RATIO:
                    folded = action.action == 'fold'
                    for observer in self.initialized_players:
                        if observer != name:
                            model = self.opponent_model_manager.get_model(observer, name)
                            model.tendencies.update_fold_to_big_bet(folded)

            # Advance the replay state past this action.
            if action.action in ('bet', 'raise'):
                current_level[phase] = max(level, action.amount)
                committed[(name, phase)] = max(prior, action.amount)
            elif action.action in ('call', 'all_in'):
                committed[(name, phase)] = prior + max(0, action.amount)
                current_level[phase] = max(level, committed[(name, phase)])
            running_pot = action.pot_after

    def _record_stab_frequency(self, recorded_hand) -> None:
        """§5j: live (all-hands) stab-frequency tracking — how often a player BETS
        when CHECKED TO postflop (the capped-checking dual of fold_to_big_bet).

        Replays in action order. A "stab opportunity" = a player acts postflop with
        nothing to call (cost_to_call == 0) AND is NOT first to act on the street
        (so a prior player checked — it is checked TO them, not leading). Betting
        there = a stab. High stab_frequency ⇒ a frequent stabber → the bot widens
        its defense facing that opponent's bets into its checked range.
        """
        current_level: Dict[str, int] = {}  # phase -> highest bet-to level
        acted_this_phase: Dict[str, int] = {}  # phase -> count of actions so far
        for action in recorded_hand.actions:
            phase = action.phase
            name = action.player_name
            level = current_level.get(phase, 0)
            n_prior = acted_this_phase.get(phase, 0)

            if phase in ('FLOP', 'TURN', 'RIVER') and level == 0 and n_prior > 0:
                # Checked to this player (no bet yet, but others acted = checked).
                stabbed = action.action in ('bet', 'raise', 'all_in')
                if stabbed or action.action == 'check':
                    for observer in self.initialized_players:
                        if observer != name:
                            model = self.opponent_model_manager.get_model(observer, name)
                            model.tendencies.update_stab(stabbed)

            if action.action in ('bet', 'raise'):
                current_level[phase] = max(level, action.amount)
            elif action.action == 'all_in':
                current_level[phase] = max(level, action.amount)
            acted_this_phase[phase] = n_prior + 1

    def _count_non_folded_per_phase(self, recorded_hand) -> Dict[str, int]:
        """For each postflop phase, count players who hadn't folded yet
        at the START of that phase. Used by the equity-at-action recorder
        to estimate how many random-opponent equity slots to simulate.

        Returns a dict like {'FLOP': 3, 'TURN': 2, 'RIVER': 2}. Missing
        phases default to 0; callers should treat 0 as 'unknown' and
        fall back to a sensible default (probably 1).
        """
        # Track who has folded by walking actions in order. A player who
        # folds preflop doesn't see the flop; one who folds on the flop
        # doesn't see the turn; etc.
        folded_before: Dict[str, bool] = {p.name: False for p in recorded_hand.players}
        # Phase ordering: count is captured at start of each phase.
        result: Dict[str, int] = {}

        # Total seated players minus pre-existing folders gives the
        # count at the start of preflop. For postflop phases, decrement
        # as folds happen during prior phases.
        phase_order = ['PRE_FLOP', 'FLOP', 'TURN', 'RIVER']
        for phase in phase_order:
            non_folded = sum(1 for v in folded_before.values() if not v)
            result[phase] = non_folded
            # Apply this phase's folds for the next iteration
            for action in recorded_hand.actions:
                if action.phase == phase and action.action == 'fold':
                    folded_before[action.player_name] = True
        return result

    def get_session_memory(self, player_name: str) -> Optional[SessionMemory]:
        """Get session memory for a player."""
        return self.session_memories.get(player_name)

    def get_opponent_model_manager(self) -> OpponentModelManager:
        """Get the opponent model manager."""
        return self.opponent_model_manager

    def apply_learned_adjustments(self, player_name: str, elastic_personality: Any) -> None:
        """Apply learned opponent patterns to adjust AI personality.

        Args:
            player_name: Name of the AI player
            elastic_personality: The player's personality object (unused, pass None)
        """
        if not elastic_personality:
            return

        # Get all opponent models for this player
        opponent_models = self.opponent_model_manager.get_all_models_for_observer(player_name)

        if not opponent_models:
            return

        # Average the tendencies of all opponents
        avg_tendencies = {
            'aggression_factor': 0,
            'bluff_frequency': 0,
            'vpip': 0,
            'fold_to_cbet': 0,
        }
        count = 0

        for model in opponent_models.values():
            if (
                model.tendencies.hands_observed >= 15
            ):  # Need enough data (sync with poker/config.py)
                avg_tendencies['aggression_factor'] += model.tendencies.aggression_factor
                avg_tendencies['bluff_frequency'] += model.tendencies.bluff_frequency
                avg_tendencies['vpip'] += model.tendencies.vpip
                avg_tendencies['fold_to_cbet'] += model.tendencies.fold_to_cbet
                count += 1

        if count > 0:
            for key in avg_tendencies:
                avg_tendencies[key] /= count

            # Apply adjustment to elastic personality
            elastic_personality.apply_learned_adjustment(avg_tendencies)

    def _resolve_chattiness(self, controller: Any, ai_player: Any) -> float:
        """Single source of truth for the player's chattiness signal.

        Prefers the live `psychology.energy` axis (same value the
        per-action narration gate reads via psychology.table_talk). This
        unifies the two gates onto one personality knob — `baseline_energy`
        in personalities.json — and makes post-hand commentary mood-aware
        (a tilted player goes quiet post-hand too).

        Falls back to the legacy static personality_traits when the
        controller isn't available (e.g., the deprecated sync path on
        on_hand_complete that hands raw ai_players in).
        """
        if controller is not None:
            psychology = getattr(controller, 'psychology', None)
            if psychology is not None:
                energy = getattr(psychology, 'energy', None)
                if energy is not None:
                    return float(energy)
        return self._get_chattiness(ai_player)

    def _get_chattiness(self, ai_player: Any) -> float:
        """Legacy chattiness lookup from static personality config.

        Retained for the sync `on_hand_complete` path that passes raw
        ai_player objects without controllers. Prefer `_resolve_chattiness`
        for new call sites — it reads the live psychology axis when a
        controller is available.
        """
        if hasattr(ai_player, 'elastic_personality'):
            # Try new trait name first, fall back to old
            value = ai_player.elastic_personality.get_trait_value('table_talk')
            if value is None:
                value = ai_player.elastic_personality.get_trait_value('chattiness')
            return value if value is not None else 0.5
        if hasattr(ai_player, 'personality_config'):
            traits = ai_player.personality_config.get('personality_traits', {})
            return traits.get('table_talk', traits.get('chattiness', 0.5))
        return 0.5

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for persistence."""
        return {
            'game_id': self.game_id,
            'hand_count': self.hand_count,
            'initialized_players': list(self.initialized_players),
            'session_memories': {
                name: memory.to_dict() for name, memory in self.session_memories.items()
            },
            'opponent_models': self.opponent_model_manager.to_dict(),
            'completed_hands': [hand.to_dict() for hand in self.hand_recorder.completed_hands],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AIMemoryManager':
        """Deserialize from persistence."""
        manager = cls(game_id=data['game_id'])
        manager.hand_count = data.get('hand_count', 0)
        manager.initialized_players = set(data.get('initialized_players', []))

        # Restore session memories
        for name, memory_data in data.get('session_memories', {}).items():
            manager.session_memories[name] = SessionMemory.from_dict(memory_data)

        # Restore opponent models
        if 'opponent_models' in data:
            manager.opponent_model_manager = OpponentModelManager.from_dict(data['opponent_models'])

        # Restore completed hands
        for hand_data in data.get('completed_hands', []):
            hand = RecordedHand.from_dict(hand_data)
            manager.hand_recorder.completed_hands.append(hand)

        return manager
