"""Continuation-bet detection state machine.

Extracted from AIMemoryManager so simulator paths that bypass the
full memory pipeline can drive the same detection logic and feed
`fold_to_cbet` observations into opponent models.

Hand-level state machine:
  1. On accepted preflop raise / all-in → record `_preflop_raiser`.
  2. On flop, if the preflop raiser bets / raises and no c-bet has
     fired yet on this hand → mark `_cbet_made` and snapshot the
     active non-bettor names as `_players_facing_cbet`.
  3. When a player in `_players_facing_cbet` next acts → emit a
     `(player_name, folded_to_cbet)` response and drop them from the
     facing set.

The detector does NOT update opponent models. It returns response
tuples; the caller (MemoryManager in production, sim runners in
validation) is responsible for applying them via
`OpponentTendencies.update_fold_to_cbet()`.
"""

from typing import List, Optional, Set, Tuple


class CbetDetector:
    """Hand-scoped continuation-bet state machine."""

    def __init__(self):
        self._preflop_raiser: Optional[str] = None
        self._cbet_made: bool = False
        self._players_facing_cbet: Set[str] = set()
        # Phase 8.1a: PFR-attempt tracking. Records one
        # `(name, attempted: bool)` event per hand when the preflop
        # aggressor has a clean c-bet decision on the flop — bet/raise
        # when no one had bet first (`attempted=True`) or check when
        # no one had bet first (`attempted=False`). Donk-bet-into-PFR
        # scenarios are intentionally excluded since the PFR didn't
        # have a clean opportunity to c-bet.
        self._pfr_attempt_recorded: bool = False
        self._flop_bet_made: bool = False
        self._pending_pfr_attempts: List[Tuple[str, bool]] = []
        # Phase B Item 1: barrel tracking.
        # The exploit induce_override targets is "PFR fires multiple
        # streets after being called." Phase A used AF_pf×cbet_attempt
        # as a proxy; Phase B tracks barrels directly.
        #
        # Barrel = PFR's first action on the next street when:
        #   1. They c-bet the prior street (cbet on flop / barrel on turn)
        #   2. At least one player called (not folded) that bet
        #   3. The PFR has a "clean decision" — no one donked into them
        #      first on the new street.
        #
        # bet/raise/all_in → barrel attempt = True
        # check → barrel attempt = False
        #
        # Third barrel = same pattern for turn→river.
        self._cbet_called: bool = False
        self._barrel_attempt_recorded: bool = False
        self._turn_bet_made: bool = False
        self._turn_barrel_made: bool = False
        self._turn_barrel_called: bool = False
        self._players_facing_turn_barrel: Set[str] = set()
        self._third_barrel_attempt_recorded: bool = False
        self._river_bet_made: bool = False
        self._pending_barrel_attempts: List[Tuple[str, bool]] = []
        self._pending_third_barrel_attempts: List[Tuple[str, bool]] = []
        # Phase B Item 4: flop-check-then-barrel tracking. Signal for
        # the open-spot IP induce branch — measures how often a villain
        # (typically OOP) checks flop AND then bets turn after a
        # check-through flop. The "barrel" attribution goes to the
        # FIRST player who voluntarily checked the flop (no prior flop
        # bet); in HU this is the BB seat (OOP first-to-act on flop)
        # whenever no one bet the flop.
        self._first_flop_checker: Optional[str] = None
        self._flop_checked_through: bool = False
        self._flop_check_barrel_attempt_recorded: bool = False
        self._pending_flop_check_barrel_attempts: List[Tuple[str, bool]] = []

    # ── Read-only views ────────────────────────────────────────────────

    @property
    def preflop_aggressor(self) -> Optional[str]:
        """Last accepted preflop raiser/all-in. Resets at hand start.

        Phase 6.6 surfaces this through MemoryManager.last_preflop_aggressor
        for HU c-bet exploit gating.
        """
        return self._preflop_raiser

    @property
    def cbet_made(self) -> bool:
        """Whether a c-bet has already fired this hand."""
        return self._cbet_made

    # ── Lifecycle ──────────────────────────────────────────────────────

    def reset_for_new_hand(self) -> None:
        """Clear all per-hand state. Call once at hand start."""
        self._preflop_raiser = None
        self._cbet_made = False
        self._players_facing_cbet = set()
        self._pfr_attempt_recorded = False
        self._flop_bet_made = False
        self._pending_pfr_attempts = []
        self._cbet_called = False
        self._barrel_attempt_recorded = False
        self._turn_bet_made = False
        self._turn_barrel_made = False
        self._turn_barrel_called = False
        self._players_facing_turn_barrel = set()
        self._third_barrel_attempt_recorded = False
        self._river_bet_made = False
        self._pending_barrel_attempts = []
        self._pending_third_barrel_attempts = []
        self._first_flop_checker = None
        self._flop_checked_through = False
        self._flop_check_barrel_attempt_recorded = False
        self._pending_flop_check_barrel_attempts = []

    def record_preflop_aggression(self, player_name: str) -> None:
        """Manually set the preflop aggressor.

        Production callers reach this through `record_action`; this
        method exists for sim-path hooks that want to seed state
        directly without an action event.
        """
        self._preflop_raiser = player_name

    # ── Detection ──────────────────────────────────────────────────────

    def record_action(
        self,
        player_name: str,
        action: str,
        phase: str,
        active_players: Optional[List[str]] = None,
    ) -> List[Tuple[str, bool]]:
        """Run the state machine for one accepted action.

        Args:
            player_name: Player who acted.
            action: Action label — 'fold', 'check', 'call', 'bet',
                'raise', 'all_in'.
            phase: 'PRE_FLOP', 'FLOP', 'TURN', or 'RIVER'.
            active_players: Names of non-folded players AT THE TIME OF
                THE ACTION. Required for c-bet response tracking; if
                None, no facing-set is built so no responses can fire.

        Returns:
            List of (player_name, folded_to_cbet) tuples — typically
            empty or one element. Callers apply these to opponent
            models via tendencies.update_fold_to_cbet(folded).
        """
        responses: List[Tuple[str, bool]] = []

        # 1. Track preflop aggressor. all_in is the most aggressive
        #    preflop action and counts alongside raise.
        if phase == 'PRE_FLOP' and action in ('raise', 'all_in'):
            self._preflop_raiser = player_name

        # 2. Phase 8.1a — PFR's first flop action. Only record an
        #    attempt event when the PFR has a CLEAN c-bet opportunity:
        #    either they're first to act and choose to bet/check, or
        #    no one has bet ahead of them and they choose to bet/check.
        #    A donk-bet-into-PFR scenario means the PFR's first action
        #    is responding to a bet, not voluntarily c-betting, so
        #    that opportunity is excluded from the rate.
        if (
            phase == 'FLOP'
            and self._preflop_raiser is not None
            and player_name == self._preflop_raiser
            and not self._pfr_attempt_recorded
            and not self._flop_bet_made
        ):
            if action in ('bet', 'raise', 'all_in'):
                self._pending_pfr_attempts.append((player_name, True))
                self._pfr_attempt_recorded = True
            elif action == 'check':
                self._pending_pfr_attempts.append((player_name, False))
                self._pfr_attempt_recorded = True
            # call/fold by PFR with no prior bet is impossible — fall
            # through silently if state is somehow corrupted.

        # 2b. Track any flop bet so we can disambiguate "PFR voluntarily
        #     c-bet" from "PFR called a donk" in step 2 on subsequent
        #     actions. Set AFTER the PFR-attempt check above so the
        #     PFR's own opening bet doesn't trip the donk filter.
        if phase == 'FLOP' and action in ('bet', 'raise', 'all_in'):
            self._flop_bet_made = True

        # 3. Detect the c-bet itself. Only the first qualifying flop
        #    bet/raise from the preflop raiser counts. Includes 'all_in'
        #    so low-SPR shoves register and opponent fold-to-cbet stats
        #    stay accurate.
        if (
            phase == 'FLOP'
            and action in ('bet', 'raise', 'all_in')
            and player_name == self._preflop_raiser
            and not self._cbet_made
        ):
            self._cbet_made = True
            if active_players:
                self._players_facing_cbet = {
                    p for p in active_players if p != player_name
                }

        # 2c. Phase B Item 4 — record the first voluntary flop checker
        #    (the player whose first flop action is a check with no
        #    prior flop bet). By poker order-of-action this is always
        #    the player furthest OOP. In HU it's the BB seat. Used
        #    later to attribute a turn barrel-after-check-through.
        if (
            phase == 'FLOP'
            and action == 'check'
            and not self._flop_bet_made
            and self._first_flop_checker is None
        ):
            self._first_flop_checker = player_name

        # 4. Track facing-player responses. Phase-agnostic by design —
        #    the facing set is drained as players respond, so non-flop
        #    responses only fire if a player stayed in the set across
        #    streets (shouldn't happen in normal play).
        if self._cbet_made and player_name in self._players_facing_cbet:
            folded = (action == 'fold')
            responses.append((player_name, folded))
            self._players_facing_cbet.discard(player_name)
            # Phase B Item 1: cbet_called gates barrel-opportunity. Any
            # non-fold response (call / raise / all_in) means someone
            # stayed in and the PFR has a turn-barrel decision coming.
            if not folded:
                self._cbet_called = True

        # 5. Phase B Item 1 — PFR's first turn action. Mirrors step 2's
        #    "clean opportunity" gate for c-bet: barrel attempt only
        #    counts when the PFR isn't responding to someone else's
        #    donk bet on the new street.
        if (
            phase == 'TURN'
            and self._cbet_made
            and self._cbet_called
            and self._preflop_raiser is not None
            and player_name == self._preflop_raiser
            and not self._barrel_attempt_recorded
            and not self._turn_bet_made
        ):
            if action in ('bet', 'raise', 'all_in'):
                self._pending_barrel_attempts.append((player_name, True))
                self._barrel_attempt_recorded = True
                self._turn_barrel_made = True
                if active_players:
                    self._players_facing_turn_barrel = {
                        p for p in active_players if p != player_name
                    }
            elif action == 'check':
                self._pending_barrel_attempts.append((player_name, False))
                self._barrel_attempt_recorded = True

        # 5b-ii. Phase B Item 4 — flop-check-then-barrel attempt. Fires
        #    when the first voluntary flop checker is the first to act
        #    on the turn after a check-through flop (no flop bet). The
        #    flop-through is the trap-bait pattern; the turn action is
        #    the barrel attempt.
        #
        #    Mutually exclusive with step 5 (regular barrel): that
        #    requires `_cbet_made == True`; this requires
        #    `_flop_bet_made == False`. Must run BEFORE step 5b's
        #    `_turn_bet_made = True` update so the player's own bet
        #    doesn't pre-trip the guard.
        if (
            phase == 'TURN'
            and self._first_flop_checker is not None
            and not self._flop_bet_made
            and player_name == self._first_flop_checker
            and not self._flop_check_barrel_attempt_recorded
            and not self._turn_bet_made
        ):
            if action in ('bet', 'raise', 'all_in'):
                self._pending_flop_check_barrel_attempts.append(
                    (player_name, True)
                )
                self._flop_check_barrel_attempt_recorded = True
            elif action == 'check':
                self._pending_flop_check_barrel_attempts.append(
                    (player_name, False)
                )
                self._flop_check_barrel_attempt_recorded = True

        # 5b. Track turn bets so the PFR's response to a donk doesn't
        #     count as a barrel attempt (mirrors _flop_bet_made).
        if phase == 'TURN' and action in ('bet', 'raise', 'all_in'):
            self._turn_bet_made = True

        # 5c. Did someone call the turn barrel? Gates third-barrel.
        if (
            self._turn_barrel_made
            and player_name in self._players_facing_turn_barrel
        ):
            if action != 'fold':
                self._turn_barrel_called = True
            self._players_facing_turn_barrel.discard(player_name)

        # 6. Phase B Item 1 — PFR's first river action (third barrel).
        if (
            phase == 'RIVER'
            and self._turn_barrel_made
            and self._turn_barrel_called
            and self._preflop_raiser is not None
            and player_name == self._preflop_raiser
            and not self._third_barrel_attempt_recorded
            and not self._river_bet_made
        ):
            if action in ('bet', 'raise', 'all_in'):
                self._pending_third_barrel_attempts.append((player_name, True))
                self._third_barrel_attempt_recorded = True
            elif action == 'check':
                self._pending_third_barrel_attempts.append((player_name, False))
                self._third_barrel_attempt_recorded = True

        if phase == 'RIVER' and action in ('bet', 'raise', 'all_in'):
            self._river_bet_made = True

        return responses

    def consume_pfr_attempt_events(self) -> List[Tuple[str, bool]]:
        """Drain Phase 8.1a PFR-attempt events queued by record_action.

        Returns a list of `(name, attempted: bool)` tuples, typically
        zero or one element per call. Caller (MemoryManager in
        production) applies each via
        `OpponentTendencies.update_cbet_attempt(attempted)` on the
        appropriate observer/opponent models. The internal queue is
        cleared on read so successive calls don't double-emit.
        """
        events = self._pending_pfr_attempts
        self._pending_pfr_attempts = []
        return events

    def consume_barrel_attempt_events(self) -> List[Tuple[str, bool]]:
        """Drain Phase B Item 1 barrel-attempt events.

        Returns `(name, attempted: bool)` tuples for the PFR's first
        turn action when they cbet flop and got called. Caller
        applies via `OpponentTendencies.update_barrel_attempt`.
        """
        events = self._pending_barrel_attempts
        self._pending_barrel_attempts = []
        return events

    def consume_third_barrel_attempt_events(self) -> List[Tuple[str, bool]]:
        """Drain Phase B Item 1 third-barrel-attempt events.

        Returns `(name, attempted: bool)` tuples for the PFR's first
        river action when they barreled turn and got called.
        """
        events = self._pending_third_barrel_attempts
        self._pending_third_barrel_attempts = []
        return events

    def consume_flop_check_barrel_attempt_events(
        self,
    ) -> List[Tuple[str, bool]]:
        """Drain Phase B Item 4 flop-check-then-barrel events.

        Returns `(name, attempted: bool)` tuples for the first
        voluntary flop checker's first turn action after a check-through
        flop. Caller applies via
        `OpponentTendencies.update_flop_check_barrel_attempt(attempted)`.
        Used by the open-spot IP induce branch to detect villains who
        check OOP on the flop and then barrel the turn.
        """
        events = self._pending_flop_check_barrel_attempts
        self._pending_flop_check_barrel_attempts = []
        return events
