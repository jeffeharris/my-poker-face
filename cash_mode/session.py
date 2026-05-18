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

What v1 ships here (commit 3):
  - Sit/leave/topup methods (delegate to seating.py).
  - run_hand: full memory lifecycle, fresh state machine, settlement
    sync, bust handling.
  - Controller-based design — every seat has a controller. Real
    human-input handling lands in commit 5 (Flask routes detect
    is_human and yield).

What v1 doesn't ship in this commit:
  - Mid-hand quit / disconnect grace (commit 4).
  - Flask route / SocketIO integration (commit 5).
  - Stop-loss / stop-win (deferred to v2).
  - Persisting CashTable across process restarts (deferred to v2).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Protocol

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
      - "not_enough_players" — fewer than 2 seats filled; can't run a hand.
        The session loop should refill or wait for the human.
      - "error" — unrecoverable hand-engine failure; details in `error`.

    `hand_number` is the hand index that ran (or attempted to run).
    `bust_seats` is the list of seat-ids whose stack went to 0; the
    session has already cleared them from the table.
    """

    status: str
    hand_number: int
    bust_seats: List[str] = field(default_factory=list)
    error: Optional[str] = None


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

        self.hand_number = 0
        self.controllers: Dict[str, CashController] = {}

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

    # --- Hand loop ---

    def run_hand(self) -> HandResult:
        """Run one hand to completion. Returns when settlement is done.

        Sequence:
          1. Refill open AI seats via `fill_seats`.
          2. If fewer than 2 seats, return "not_enough_players".
          3. Block sit/leave/topup via `hand_in_progress=True`.
          4. Build fresh PokerStateMachine from CashTable.stacks.
          5. Update controllers' hand_number.
          6. Memory: on_hand_start + record_blinds (after first deal).
          7. Run the action loop until phase == EVALUATING_HAND.
             DOES NOT call advance_state() past EVALUATING_HAND — the
             state machine's evaluating_hand_transition would auto-
             settle (double-settlement). Settlement is manual.
          8. Manual settlement: determine_winner + award_pot_winnings.
          9. Sync CashTable.stacks from post-hand Player.stack (codex
             concern #1: no delta arithmetic, just take the final stack).
         10. Memory: on_hand_complete (Phase 3 dispatch — cash_pair_stats
             writes fire automatically because cash_mode=True is set).
         11. Bust handling (codex concern #5: AI bankroll doesn't move
             at settlement; the chips were lost at the table).
         12. Unblock sit/leave/topup.
        """
        now = self._now_fn()

        # 1. Refill
        self._refill_seats(now)

        # 2. Player count check
        if self._seated_count() < 2:
            return HandResult(
                status="not_enough_players",
                hand_number=self.hand_number,
            )

        # 3. Block
        self.table = self.table.with_hand_in_progress(True)
        self.hand_number += 1

        try:
            # 4. Build state machine
            state_machine = self._build_state_machine()

            # 5. Controllers
            for ctrl in self.controllers.values():
                ctrl.current_hand_number = self.hand_number

            # 6 + 7. Action loop + memory lifecycle
            hand_action_log: List[str] = []
            self._run_action_loop(state_machine, hand_action_log)

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

            # 11. Bust handling
            bust_seats = self._handle_busts()

        finally:
            # 12. Unblock
            self.table = self.table.with_hand_in_progress(False)

        return HandResult(
            status="continue",
            hand_number=self.hand_number,
            bust_seats=bust_seats,
        )

    # --- Internal helpers ---

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
        """Construct a fresh state machine from current CashTable state."""
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
        return PokerStateMachine(game_state)

    def _run_action_loop(
        self,
        state_machine: PokerStateMachine,
        hand_action_log: List[str],
    ) -> None:
        """Drive the hand engine through one hand's action sequence.

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
                return

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
                    return
            else:
                same_player_count = 0
                last_player_name = current_player.name

            # Resolve controller. For commit 3 every seat has one; commit 5
            # will detect is_human and yield instead.
            seat_id = self._player_name_to_seat_id(current_player.name)
            controller = self.controllers.get(seat_id) if seat_id else None
            if controller is None:
                logger.warning(
                    "No controller for seat %r (player %s); folding",
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
