"""CashSession — drives the hand loop for a single cash table.

One human + N AI personalities at one table; the session owns the
table state, the per-seat controllers, the per-session memory
manager (with `cash_mode=True` wired), and the per-hand state-
machine construction.

Each `run_hand()` builds a **fresh** `PokerStateMachine` from the
current `CashTable.stacks`. The state machine is per-hand, not
per-session — AI compositions change between hands as personalities
bust and refill, and rebuilding from scratch each hand is simpler
than mutating the state machine's player tuple. Memory manager
state (opponent models, hand-history recorder) persists across
hands; the state machine doesn't.

Spec: docs/plans/CASH_MODE_AND_RELATIONSHIPS.md Part 2.
Wiring plan: docs/plans/CASH_MODE_V1_WIRING_PLAN.md.

What v1 ships here (commits 3 + 4):
  - Sit/leave/topup methods (delegate to seating.py).
  - run_hand: full memory lifecycle, fresh state machine, settlement
    sync, bust handling.
  - Mid-hand quit: player's full table stack is forfeited; remaining
    stack distributed to surviving seats at settlement.
  - Disconnect grace: 60s window with auto-check / auto-fold during
    the player's turn; timeout triggers the quit accounting path.
  - Controller-based design — every seat has a controller. Real
    human-input handling lands in commit 5 (Flask routes detect
    is_human and yield).

What v1 doesn't ship in this commit:
  - Flask route / SocketIO integration (commit 5).
  - Stop-loss / stop-win (deferred to v2).
  - Persisting CashTable across process restarts (deferred to v2).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Protocol, Set

from cash_mode.bankroll import AIBankrollState, PlayerBankrollState
from cash_mode.seat_filler import fill_seats as _fill_seats
from cash_mode.seating import (
    bust_at_table,
    full_bankroll_bust,
    leave_table,
    sit_down,
    top_up,
)
from cash_mode.table import PLAYER_SEAT_ID, CashTable
from poker.memory.memory_manager import AIMemoryManager
from poker.poker_game import (
    Player,
    PokerGameState,
    advance_to_next_active_player,
    award_pot_winnings,
    create_deck,
    determine_winner,
    play_turn,
)
from poker.poker_state_machine import PokerPhase, PokerStateMachine
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.relationship_repository import RelationshipRepository

logger = logging.getLogger(__name__)


class CashController(Protocol):
    """The minimum a CashSession needs from a per-seat controller.

    `decide_action` is invoked when the seat's turn comes up. Returns
    a dict like `{'action': 'fold'|'check'|'call'|'raise'|'all_in', 'raise_to': int}`.
    The session forwards everything to `play_turn`.

    `current_hand_number` is updated by the session at hand start.
    The existing `AIPlayerController` and `HybridAIController` both
    satisfy this protocol.
    """

    current_hand_number: int

    def decide_action(self, action_log: List[str]) -> Dict[str, Any]:
        ...


# Factory: build a controller for a seat. The session calls this once
# per seated personality (commit 3 lifecycle: per seated stint).
ControllerFactory = Callable[[str, str, AIMemoryManager], CashController]
# Args: (seat_id, display_name, memory_manager)


@dataclass
class HandResult:
    """Per-hand return value from `CashSession.run_hand`.

    `status`:
      - "continue" — hand ran, ready for the next one.
      - "awaiting_human" — paused mid-hand, waiting for the human seat
        to call `apply_human_action`. The session retains internal
        state until then. Routes should NOT call run_hand again until
        the human acts.
      - "not_enough_players" — fewer than 2 seats filled; can't run a hand.
        The session loop should refill or wait for the human.
      - "error" — unrecoverable hand-engine failure; details in `error`.

    `hand_number` is the hand index that ran (or attempted to run).
    `bust_seats` is the list of seat-ids whose stack went to 0; the
    session has already cleared them from the table.

    `awaiting_player_name` is set when status="awaiting_human" — the
    Player.name (display string from the hand engine, e.g. "you") of
    whoever the engine is asking next. Routes use this to emit the
    legal-actions surface to the UI.
    """

    status: str
    hand_number: int
    bust_seats: List[str] = field(default_factory=list)
    error: Optional[str] = None
    awaiting_player_name: Optional[str] = None


DISCONNECT_GRACE_SECONDS = 60
"""How long a disconnected player has to reconnect before the seat
is treated as a mid-hand quit. Spec §"Disconnect" pins this at 60s
as the starting value; tunable later if production traffic suggests
a different fit.
"""


class CashSession:
    """One human + N AI at one cash table.

    Construction wires `cash_mode=True` on the memory manager — this
    is the only new wiring vs tournament mode; the rest of the Phase 3
    dispatch path is reused verbatim, so `cash_pair_stats` updates
    fire automatically from `on_hand_complete`.

    `controller_factory` is invoked per-personality when the AI sits
    (called from inside `fill_seats`'s wrapping). The factory returns
    an object satisfying `CashController`. For tests this can be a
    minimal scripted bot; for production (commit 5) it'll be the
    existing AI controller factory used by Flask.

    `now_fn` lets tests pin time without monkey-patching datetime;
    production uses `datetime.utcnow`. Wraps both the regen
    projection and `last_regen_tick` writes for cash-mode AI sit-downs.
    """

    def __init__(
        self,
        table: CashTable,
        player_bankroll: PlayerBankrollState,
        *,
        bankroll_repo: BankrollRepository,
        relationship_repo: RelationshipRepository,
        personality_repo: PersonalityRepository,
        memory_manager: AIMemoryManager,
        controller_factory: ControllerFactory,
        game_id: str,
        big_blind: int,
        user_id: Optional[str] = None,
        now_fn: Callable[[], datetime] = None,
        on_state_change: Optional[Callable[[], None]] = None,
    ):
        self.table = table
        self.player_bankroll = player_bankroll
        self.bankroll_repo = bankroll_repo
        self.relationship_repo = relationship_repo
        self.personality_repo = personality_repo
        self.memory_manager = memory_manager
        self.controller_factory = controller_factory
        self.game_id = game_id
        self.big_blind = big_blind
        self.user_id = user_id
        self._now_fn = now_fn or datetime.utcnow
        self._on_state_change = on_state_change

        self.hand_number = 0
        self.controllers: Dict[str, CashController] = {}

        # Persistent state machine across hands. Cash mode rebuilds the
        # game_state (PokerGameState) each hand because the player set
        # changes (busts/refills), but the state_machine OBJECT stays
        # the same — Flask's `game_state_service` and registered
        # controllers all hold this reference. Lazy-initialized on the
        # first run_hand call.
        self._state_machine: Optional[PokerStateMachine] = None

        # Mid-hand quit + disconnect grace state (commit 4).
        # `_pending_quit` holds seat-ids whose player has declared a
        # mid-hand quit; they are auto-folded at their next turn and
        # their remaining stack is forfeited at settlement. Cleared at
        # end-of-hand (the seat is also cleared during bust handling).
        # `_disconnect_times` holds seat-id → disconnect timestamp for
        # the 60s grace window. During that window, the seat's turns
        # auto-check or auto-fold (whichever is legal). Past the
        # window, the entry is promoted to _pending_quit.
        self._pending_quit: Set[str] = set()
        self._disconnect_times: Dict[str, datetime] = {}

        # Held mid-hand state for human-input awaiting. When run_hand
        # returns status="awaiting_human", the partially-played state
        # machine and action log live here until apply_human_action
        # resumes them.
        self._in_progress_state_machine: Optional[PokerStateMachine] = None
        self._in_progress_action_log: Optional[List[str]] = None

        # THE WIRING POINT: cash_mode=True enables cash_pair_stats writes
        # from the Phase 3 dispatch. Everything else flows through the
        # existing relationship-event pipeline unchanged.
        self.memory_manager.set_relationship_repo(
            relationship_repo, cash_mode=True,
        )

    # --- Between-hands actions (delegate to seating; persist bankrolls) ---

    def sit_player(self, seat_index: int, buy_in: int) -> None:
        """Seat the human player at seat_index with buy_in chips."""
        self.table, self.player_bankroll = sit_down(
            self.table, seat_index, PLAYER_SEAT_ID, buy_in, self.player_bankroll,
        )
        self.bankroll_repo.save_player_bankroll(self.player_bankroll)

    def leave_player(self) -> None:
        """Player stands up between hands. Stack returns to bankroll."""
        self.table, self.player_bankroll = leave_table(
            self.table, PLAYER_SEAT_ID, self.player_bankroll,
        )
        self.bankroll_repo.save_player_bankroll(self.player_bankroll)

    def top_up_player(self, amount: int) -> None:
        """Player tops up between hands."""
        self.table, self.player_bankroll = top_up(
            self.table, PLAYER_SEAT_ID, amount, self.player_bankroll,
        )
        self.bankroll_repo.save_player_bankroll(self.player_bankroll)

    # --- Mid-hand quit + disconnect grace (commit 4) ---

    def quit_player(self) -> None:
        """Player declares a mid-hand quit.

        The seat is added to `_pending_quit`. On the next turn the
        action loop will auto-fold (instead of asking for a decision)
        and at settlement the player's remaining table stack will be
        forfeited and distributed among surviving seats. Bankroll is
        untouched per spec §"Bust semantics".

        Idempotent — re-declaring quit is a no-op.

        Safe to call when no hand is in progress; the flag will be
        consumed at the start of the next hand's settlement (the seat
        will appear bust-empty and clear via `_handle_busts`). Real
        production callers (commit 5 Flask routes) gate this on
        hand_in_progress=True for the "leave during a hand" path;
        between-hands "leave" goes through `leave_player` instead.
        """
        if self.table.is_seated(PLAYER_SEAT_ID):
            self._pending_quit.add(PLAYER_SEAT_ID)

    def mark_player_disconnected(self, *, now: Optional[datetime] = None) -> None:
        """Start the 60s grace timer for the player seat.

        During the window, the player's turns auto-check or auto-fold
        (whichever is legal). Reconnect via `mark_player_reconnected`.
        Window expiry promotes the seat to `_pending_quit` —
        accounting identical to a deliberate mid-hand quit.

        `now` defaults to the session's `now_fn` so tests can pin
        time. Idempotent within the same window (re-marking does NOT
        reset the timer — that's a deliberate anti-abuse choice; the
        spec note says "preventing reconnect-as-fold-equity-saving
        over multiple hands").
        """
        if not self.table.is_seated(PLAYER_SEAT_ID):
            return
        if PLAYER_SEAT_ID in self._disconnect_times:
            return  # already disconnected; don't reset the timer
        self._disconnect_times[PLAYER_SEAT_ID] = now or self._now_fn()

    def mark_player_reconnected(self) -> None:
        """Clear the disconnect timer for the player seat.

        No-op if the seat wasn't disconnected. Doesn't undo any
        auto-folds that already fired during the window — those
        actions are already in the hand history. The player resumes
        seated for subsequent turns.
        """
        self._disconnect_times.pop(PLAYER_SEAT_ID, None)

    def is_player_disconnected(self) -> bool:
        return PLAYER_SEAT_ID in self._disconnect_times

    def apply_human_action(self, action: str, amount: int = 0) -> HandResult:
        """Apply the player's action to the held mid-hand state, then
        continue the hand loop.

        Requires `run_hand` to have previously returned
        status="awaiting_human" (which stashes the state machine and
        action log on the session). If called without an in-progress
        hand, returns status="error".

        After applying, this method re-enters the loop: if another
        human turn comes up (rare in v1 one-human, possible in v2),
        yields again; if the hand finishes, settles and returns
        status="continue".
        """
        if self._in_progress_state_machine is None:
            return HandResult(
                status="error",
                hand_number=self.hand_number,
                error="apply_human_action called with no in-progress hand",
            )
        state_machine = self._in_progress_state_machine
        hand_action_log = self._in_progress_action_log
        game_state = state_machine.game_state
        current_player = game_state.current_player
        if current_player is None or not current_player.is_human:
            return HandResult(
                status="error",
                hand_number=self.hand_number,
                error=(
                    f"apply_human_action called when current_player is "
                    f"{current_player.name if current_player else 'None'}"
                ),
            )

        self._apply_action(
            state_machine, current_player.name, action, amount,
            hand_action_log,
        )
        # Re-enter run_hand to keep driving (it'll detect _in_progress_state_machine).
        return self.run_hand()

    # --- Hand loop ---

    def run_hand(self) -> HandResult:
        """Start a new hand OR resume an in-progress one.

        If `_in_progress_state_machine` is set, this resumes (caller
        just applied a human action via apply_human_action and is now
        re-driving the loop). Otherwise starts a fresh hand: fill
        seats, build state machine, increment hand_number.

        See module docstring for the full sequence. Status surface:
          - "continue" — hand completed; ready for the next.
          - "awaiting_human" — player's turn, no controller registered;
            session retains state, caller must invoke apply_human_action.
          - "not_enough_players" — fewer than 2 seated.
          - "error" — hand-engine failure mid-loop.
        """
        # Resume path: state machine already exists from a previous
        # awaiting_human yield.
        if self._in_progress_state_machine is not None:
            state_machine = self._in_progress_state_machine
            hand_action_log = self._in_progress_action_log
        else:
            now = self._now_fn()
            self._refill_seats(now)

            if self._seated_count() < 2:
                return HandResult(
                    status="not_enough_players",
                    hand_number=self.hand_number,
                )

            self.table = self.table.with_hand_in_progress(True)
            self.hand_number += 1
            state_machine = self._build_state_machine()
            for ctrl in self.controllers.values():
                ctrl.current_hand_number = self.hand_number
            hand_action_log = []
            # Stash so a yield mid-loop preserves state.
            self._in_progress_state_machine = state_machine
            self._in_progress_action_log = hand_action_log

        try:
            yielded = self._run_action_loop(state_machine, hand_action_log)
            if yielded is not None:
                # _run_action_loop saw a human turn with no controller.
                # Preserve state for apply_human_action; do not settle.
                return HandResult(
                    status="awaiting_human",
                    hand_number=self.hand_number,
                    awaiting_player_name=yielded,
                )

            # 8. Manual settlement (must happen even if loop ended on safety break)
            game_state = state_machine.game_state
            if state_machine.current_phase != PokerPhase.EVALUATING_HAND:
                logger.warning(
                    "Hand %d ended at phase %s, not EVALUATING_HAND; "
                    "skipping settlement",
                    self.hand_number, state_machine.current_phase,
                )
                return HandResult(
                    status="error",
                    hand_number=self.hand_number,
                    error=f"hand ended at {state_machine.current_phase}",
                )

            winner_info = determine_winner(game_state)
            game_state = award_pot_winnings(game_state, winner_info)
            state_machine.game_state = game_state

            # 8b. Mid-hand quit / disconnect-timeout forfeit:
            # quitting seats forfeit their *remaining* table stack
            # (after settlement) to the surviving seats. Spec §"Bust
            # semantics" pins the entire stack as forfeit; the
            # quitter's Player.stack at this point already had bets
            # in pot withdrawn, plus any won-back chips from the
            # award. We zero out the quitter's stack and redistribute
            # to survivors so chip conservation holds.
            game_state = self._apply_forfeit_distribution(game_state)
            state_machine.game_state = game_state

            # 9. Sync table stacks from post-hand Player.stack
            seat_id_by_name = self._seat_id_by_player_name()
            for player in game_state.players:
                seat_id = seat_id_by_name.get(player.name)
                if seat_id is not None and self.table.is_seated(seat_id):
                    self.table = self.table.with_stack(seat_id, player.stack)

            # 10. Memory.on_hand_complete — Phase 3 dispatch runs automatically
            try:
                self.memory_manager.on_hand_complete(
                    winner_info=winner_info,
                    game_state=game_state,
                    ai_players={},
                    skip_commentary=True,
                    equity_history=None,  # commit 7 wires EquityTracker
                )
            except Exception as e:
                logger.warning("on_hand_complete failed: %s", e, exc_info=True)

            # 11. Bust handling — also catches quitter seats (stack==0
            # after forfeit distribution).
            bust_seats = self._handle_busts()

            # Final emit for this hand — frontend renders the winner +
            # post-settlement stacks. The state machine is still at
            # EVALUATING_HAND; the next run_hand call will reset it.
            self._emit_state_change()

        finally:
            # 12. Unblock + clear in-progress state + filter
            # pending_quit/disconnect to keep only entries for seats
            # still at the table (busted or quit ones fall out).
            self.table = self.table.with_hand_in_progress(False)
            self._in_progress_state_machine = None
            self._in_progress_action_log = None
            self._pending_quit = {
                seat_id for seat_id in self._pending_quit
                if self.table.is_seated(seat_id)
            }
            self._disconnect_times = {
                seat_id: tick for seat_id, tick in self._disconnect_times.items()
                if self.table.is_seated(seat_id)
            }

        return HandResult(
            status="continue",
            hand_number=self.hand_number,
            bust_seats=bust_seats,
        )

    # --- Internal helpers ---

    def _emit_state_change(self) -> None:
        """Fire the on_state_change callback if one was registered.

        Production callers wire this to `update_and_emit_game_state(game_id)`
        which serializes the current state machine and emits via SocketIO
        to the game's room. Tests leave the callback as None — no-op.

        Wrapped in try/except so an emit failure (broken socket, etc.)
        doesn't crash the hand loop. The next state-change will retry.
        """
        if self._on_state_change is None:
            return
        try:
            self._on_state_change()
        except Exception as e:
            logger.warning("on_state_change callback failed: %s", e)

    def _refill_seats(self, now: datetime) -> None:
        """Fill open AI seats and instantiate controllers for new arrivals."""
        prev_ids = set(seat for seat in self.table.seats if seat is not None)
        self.table = _fill_seats(
            self.table,
            personality_repo=self.personality_repo,
            bankroll_repo=self.bankroll_repo,
            now=now,
            user_id=self.user_id,
        )
        new_ids = {seat for seat in self.table.seats if seat is not None} - prev_ids
        for pid in new_ids:
            if pid == PLAYER_SEAT_ID:
                continue  # player controllers managed elsewhere
            if pid in self.controllers:
                continue  # already have one (re-sit after a session-internal leave)
            display_name = self._lookup_display_name(pid) or pid
            self.controllers[pid] = self.controller_factory(
                pid, display_name, self.memory_manager,
            )

    def _seated_count(self) -> int:
        return sum(1 for seat in self.table.seats if seat is not None)

    def _build_state_machine(self) -> PokerStateMachine:
        """Refresh the persistent state machine for a new hand.

        On the first call, creates `self._state_machine` and attaches
        it to every existing controller. On subsequent calls, builds
        a fresh `PokerGameState` from the current CashTable (new
        player set, fresh deck) and assigns it to the existing state
        machine — the state_machine OBJECT stays the same so
        downstream consumers (game_state_service, controllers) keep
        their references valid.
        """
        # Map seat_id → display_name. AI: lookup from personality repo.
        # Human: hardcoded "you" (matches Flask convention).
        players = []
        for seat_id in self.table.seats:
            if seat_id is None:
                continue
            stack = self.table.stack_of(seat_id)
            if seat_id == PLAYER_SEAT_ID:
                players.append(Player(name="you", stack=stack, is_human=True))
            else:
                display_name = self._lookup_display_name(seat_id) or seat_id
                players.append(
                    Player(name=display_name, stack=stack, is_human=False)
                )

        deck = create_deck(shuffled=True)
        game_state = PokerGameState(
            players=tuple(players),
            deck=deck,
            current_ante=self.big_blind,
            last_raise_amount=self.big_blind,
        )

        if self._state_machine is None:
            self._state_machine = PokerStateMachine(game_state)
        else:
            # Reuse: reassign game_state + reset phase. The state
            # machine's evaluating_hand_transition is bypassed by the
            # manual settlement in run_hand, so leftover phase from
            # the previous hand is EVALUATING_HAND. Reset to
            # INITIALIZING_HAND so the next run_until advances through
            # the new hand's lifecycle (deals cards, runs blinds, etc.).
            self._state_machine.game_state = game_state
            self._state_machine.update_phase(PokerPhase.INITIALIZING_HAND)

        # Refresh every controller's state_machine reference. For the
        # initial creation this attaches the new state machine; on
        # subsequent hands the reference is already correct, but
        # writing again is harmless and keeps the loop simple.
        for ctrl in self.controllers.values():
            try:
                ctrl.state_machine = self._state_machine
            except AttributeError:
                pass  # protocol-only mock that doesn't accept attrs

        return self._state_machine

    def _run_action_loop(
        self,
        state_machine: PokerStateMachine,
        hand_action_log: List[str],
    ) -> Optional[str]:
        """Drive the hand engine through one hand's action sequence.

        Returns `None` when the hand reaches EVALUATING_HAND (caller
        should settle). Returns the awaiting player's name when a
        human seat is up and has no controller (caller should yield
        with status="awaiting_human").

        Mirrors the experiment runner's run_hand inner loop minus
        the enterprise concerns (heartbeats, per-action saves,
        commentary, psychology). Streamlined to the load-bearing
        pieces: memory lifecycle hooks, controller decisions,
        play_turn, advance_to_next_active_player.
        """
        max_actions = 100
        action_count = 0
        hand_start_recorded = False

        # Stuck-loop detector — borrowed from the experiment runner
        last_player_name = None
        same_player_count = 0

        while action_count < max_actions:
            state_machine.run_until([PokerPhase.EVALUATING_HAND])
            game_state = state_machine.game_state

            # Memory.on_hand_start after first deal (hole_cards now exist)
            if not hand_start_recorded:
                self.memory_manager.on_hand_start(
                    game_state,
                    self.hand_number,
                    deck_seed=getattr(state_machine, "current_hand_seed", None),
                )
                self.memory_manager.record_blinds(game_state)
                hand_start_recorded = True

            # Done?
            if state_machine.current_phase == PokerPhase.EVALUATING_HAND:
                return None

            # run-it-out: all-ins + 1 player who can act; engine auto-advances streets
            if game_state.run_it_out:
                current_phase = state_machine.current_phase
                next_phase = (
                    PokerPhase.SHOWDOWN if current_phase == PokerPhase.RIVER
                    else PokerPhase.DEALING_CARDS
                )
                game_state = game_state.update(awaiting_action=False, run_it_out=False)
                state_machine.game_state = game_state
                state_machine.update_phase(next_phase)
                continue

            if not game_state.awaiting_action:
                action_count += 1
                continue

            current_player = game_state.current_player
            if current_player is None:
                action_count += 1
                continue

            # Stuck-loop guard
            if current_player.name == last_player_name:
                same_player_count += 1
                if same_player_count > 5:
                    logger.warning(
                        "Stuck loop: %s asked %d times, forcing hand end",
                        current_player.name, same_player_count,
                    )
                    return None
            else:
                same_player_count = 0
                last_player_name = current_player.name

            # Resolve seat & check quit / disconnect state before controllers
            seat_id = self._player_name_to_seat_id(current_player.name)

            # Mid-hand quit: auto-fold and don't ask the controller.
            if seat_id is not None and seat_id in self._pending_quit:
                self._apply_action(
                    state_machine, current_player.name, "fold", 0,
                    hand_action_log,
                )
                action_count += 1
                continue

            # Disconnect grace window: timeout → quit; within window →
            # auto-check (if free) or auto-fold.
            if seat_id is not None and seat_id in self._disconnect_times:
                disconnected_at = self._disconnect_times[seat_id]
                elapsed = (self._now_fn() - disconnected_at).total_seconds()
                if elapsed >= DISCONNECT_GRACE_SECONDS:
                    # Promote to quit; release the disconnect entry so
                    # the next pass treats this as pending_quit
                    self._pending_quit.add(seat_id)
                    self._disconnect_times.pop(seat_id, None)
                    self._apply_action(
                        state_machine, current_player.name, "fold", 0,
                        hand_action_log,
                    )
                    action_count += 1
                    continue
                # Within grace: auto-check if legal, else auto-fold.
                cost_to_call = max(
                    0, game_state.highest_bet - current_player.bet
                )
                auto_action = "check" if cost_to_call == 0 else "fold"
                self._apply_action(
                    state_machine, current_player.name, auto_action, 0,
                    hand_action_log,
                )
                action_count += 1
                continue

            # Player seat without a controller → yield for human input.
            # Tests can register a mock controller for PLAYER_SEAT_ID to
            # bypass this path (which is what the commit-3 + commit-4
            # tests do). Production Flask routes leave it unregistered
            # so run_hand returns awaiting_human and apply_human_action
            # resumes the loop.
            controller = self.controllers.get(seat_id) if seat_id else None
            if controller is None and current_player.is_human:
                return current_player.name

            if controller is None:
                logger.warning(
                    "No controller for non-human seat %r (player %s); folding",
                    seat_id, current_player.name,
                )
                self._apply_action(
                    state_machine, current_player.name, "fold", 0,
                    hand_action_log,
                )
                action_count += 1
                continue

            # Decide + apply
            try:
                response = controller.decide_action(hand_action_log)
            except Exception as e:
                logger.warning(
                    "Controller error for %s: %s; defaulting to fold",
                    current_player.name, e, exc_info=True,
                )
                response = {"action": "fold", "raise_to": 0}

            action = response.get("action", "fold")
            amount = response.get("raise_to", 0)
            self._apply_action(
                state_machine, current_player.name, action, amount,
                hand_action_log,
            )

            action_count += 1

    def _apply_action(
        self,
        state_machine: PokerStateMachine,
        player_name: str,
        action: str,
        amount: int,
        hand_action_log: List[str],
    ) -> None:
        """Apply one action, log it, and update memory."""
        game_state = state_machine.game_state

        if action == "raise" and amount:
            raise_to = game_state.highest_bet + amount
            hand_action_log.append(f"{player_name} raises to ${raise_to}")
        elif action == "call":
            hand_action_log.append(f"{player_name} calls")
        elif action == "check":
            hand_action_log.append(f"{player_name} checks")
        elif action == "fold":
            hand_action_log.append(f"{player_name} folds")
        elif action == "all_in":
            hand_action_log.append(f"{player_name} goes all-in")

        game_state = play_turn(game_state, action, amount)

        active_player_names = [
            p.name for p in game_state.players
            if not p.is_folded and p.stack > 0
        ]
        self.memory_manager.on_action(
            player_name=player_name,
            action=action,
            amount=amount,
            phase=state_machine.current_phase.name,
            pot_total=game_state.pot["total"],
            active_players=active_player_names,
        )

        advanced = advance_to_next_active_player(game_state)
        if advanced is not None:
            game_state = advanced
        state_machine.game_state = game_state

        # Notify the SocketIO emitter (if wired) so the frontend animates
        # the action. Tournament games do this from progress_game in
        # the Flask game_handler; cash mode does it inline.
        self._emit_state_change()

    def _apply_forfeit_distribution(self, game_state) -> "PokerGameState":
        """Zero quitting seats' stacks and split the forfeit among survivors.

        Called after award_pot_winnings, before sync to CashTable.stacks.
        For each seat in `_pending_quit`, take that player's remaining
        `Player.stack` (post-settlement), distribute evenly among
        survivors (non-quitting players still at the table with chips).
        Remainder goes to the first survivor in seat order.

        Survivors = seated players who are NOT in pending_quit AND
        whose post-settlement stack > 0. This excludes seats that
        also busted at the table (their stack hit 0 from losing the
        hand) — we don't redistribute to busted seats; they wouldn't
        benefit anyway.

        If there are no survivors, the forfeit chips are dropped
        (chip leak, but the only scenario is "everyone left or busted
        at once" which is a corner case worth flagging in logs). v1
        accepts the leak; v2 may add a session-level rake account.

        Returns the updated game_state with quitter stacks zeroed
        and survivor stacks credited. Pure-ish — uses update_player
        which returns a new immutable game_state.
        """
        if not self._pending_quit:
            return game_state

        # Map seat_id → Player index in game_state.players.
        seat_to_player_idx: Dict[str, int] = {}
        for idx, player in enumerate(game_state.players):
            seat_id = self._player_name_to_seat_id(player.name)
            if seat_id is not None:
                seat_to_player_idx[seat_id] = idx

        # Total forfeit chips across all quitters.
        forfeit_total = 0
        quitter_indices: List[int] = []
        for seat_id in self._pending_quit:
            idx = seat_to_player_idx.get(seat_id)
            if idx is None:
                continue
            forfeit_total += game_state.players[idx].stack
            quitter_indices.append(idx)

        if forfeit_total == 0:
            # Quitters already at 0 (lost everything in the hand). Nothing to
            # redistribute; just no-op.
            return game_state

        # Survivors: seated, non-quitting, stack > 0.
        survivor_indices = [
            idx for seat_id, idx in sorted(seat_to_player_idx.items())
            if seat_id not in self._pending_quit
            and game_state.players[idx].stack > 0
        ]

        if not survivor_indices:
            # No survivors — log and drop chips (rare corner case).
            logger.warning(
                "Forfeit distribution: no survivors for %d chips from %d quitters; "
                "chips dropped from circulation",
                forfeit_total, len(quitter_indices),
            )
            # Still zero out the quitters' stacks for accurate sync.
            for idx in quitter_indices:
                game_state = game_state.update_player(idx, stack=0)
            return game_state

        # Even split with remainder to first survivor (seat-order).
        per_survivor = forfeit_total // len(survivor_indices)
        remainder = forfeit_total - per_survivor * len(survivor_indices)

        for i, idx in enumerate(survivor_indices):
            bonus = per_survivor + (remainder if i == 0 else 0)
            new_stack = game_state.players[idx].stack + bonus
            game_state = game_state.update_player(idx, stack=new_stack)

        # Zero out the quitters' stacks.
        for idx in quitter_indices:
            game_state = game_state.update_player(idx, stack=0)

        return game_state

    def _handle_busts(self) -> List[str]:
        """Clear seats whose stack hit 0. Fresh-grant player if bankroll == 0.

        Per spec: AI bankroll state does NOT update at settlement —
        the chips at the table were lost during the hand. Settlement
        already debited Player.stack; bust_at_table just clears the
        seat slot. The AI bankroll was debited at sit-down and stays
        wherever it was; passive regen from last_regen_tick can refill
        for future eligibility.

        Returns the list of seat-ids that busted.
        """
        bust_seats = []
        for seat_id in list(self.table.seats):
            if seat_id is None:
                continue
            if self.table.stack_of(seat_id) > 0:
                continue
            # Seat stack went to 0
            bust_seats.append(seat_id)
            self.table = bust_at_table(self.table, seat_id)

            if seat_id == PLAYER_SEAT_ID:
                # Player busted at the table. If bankroll is also 0,
                # fresh-grant per spec §"Bust semantics".
                if self.player_bankroll.chips == 0:
                    self.player_bankroll = full_bankroll_bust(self.player_bankroll)
                    self.bankroll_repo.save_player_bankroll(self.player_bankroll)
            else:
                # AI bust: discard the controller. Re-sit (later hand)
                # will instantiate a fresh one via the factory.
                self.controllers.pop(seat_id, None)

        return bust_seats

    def _seat_id_by_player_name(self) -> Dict[str, str]:
        """Map Player.name (from PokerGameState) back to seat_id (CashTable)."""
        mapping: Dict[str, str] = {}
        for seat_id in self.table.seats:
            if seat_id is None:
                continue
            if seat_id == PLAYER_SEAT_ID:
                mapping["you"] = seat_id
            else:
                display = self._lookup_display_name(seat_id) or seat_id
                mapping[display] = seat_id
        return mapping

    def _player_name_to_seat_id(self, player_name: str) -> Optional[str]:
        return self._seat_id_by_player_name().get(player_name)

    def _lookup_display_name(self, personality_id: str) -> Optional[str]:
        """Resolve personality_id → display name (cached lookup)."""
        # Simple loop over personality_repo.list_eligible (small set, infrequent calls).
        # If this becomes a bottleneck, swap in a session-cached map.
        for entry in self.personality_repo.list_eligible_for_cash_mode(
            user_id=self.user_id,
        ):
            if entry["personality_id"] == personality_id:
                return entry["name"]
        return None
