"""Tests for the 8-event cash-mode accounting matrix.

Mirrors Part 2's bankroll accounting order table row-by-row, both
happy-path and rollback scenarios. The accounting matrix is the
load-bearing correctness surface in v1 (per handoff); breakage here
desynchronizes bankroll vs table-stack chips and leaks/duplicates
money.

Row reference (spec §"Bankroll accounting order"):

  1. Sit down (buy-in)
  2. Top up (between hands)
  3. Leave table (between hands)
  4. Bust at table (in-hand loss)
  5. Full bankroll bust
  6. Mid-hand quit
  7. Disconnect timeout
  8. Hand settlement (winnings)
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from cash_mode import (
    PLAYER_SEAT_ID,
    AIBankrollState,
    BankrollKnobs,
    CashTable,
    HandInProgressError,
    PlayerBankrollState,
    SeatingError,
    apply_settlement,
    bust_at_table,
    cash_out_ai_seat,
    disconnect_timeout,
    leave_table,
    mid_hand_quit,
    new_table,
    sit_down,
    sit_down_ai,
    top_up,
)

# --- Fixtures ---


@pytest.fixture
def empty_table() -> CashTable:
    """6-max $10 table, no one seated, BB=$0.10 → min=$4 / max=$10
    (in chip units, integer)."""
    return new_table(
        table_id="cash-1",
        stake_label="$10",
        big_blind=10,  # chips
        seat_count=6,
        min_buy_in_bb=40,
        max_buy_in_bb=100,
    )


@pytest.fixture
def player_bankroll() -> PlayerBankrollState:
    return PlayerBankrollState(
        player_id="alice",
        chips=2_000,
        starting_bankroll=2_000,
    )


@pytest.fixture
def seated_table(empty_table, player_bankroll) -> tuple[CashTable, PlayerBankrollState]:
    """Player seated at seat 0 with a 500-chip buy-in."""
    return sit_down(empty_table, 0, PLAYER_SEAT_ID, 500, player_bankroll)


# --- Row 1: Sit down ---


class TestSitDown:
    def test_happy_path_debits_bankroll_and_sets_stack(self, empty_table, player_bankroll):
        new_t, new_b = sit_down(empty_table, 2, PLAYER_SEAT_ID, 500, player_bankroll)
        assert new_t.seats[2] == PLAYER_SEAT_ID
        assert new_t.stack_of(PLAYER_SEAT_ID) == 500
        assert new_b.chips == 1_500
        # Bankroll keeps its starting_bankroll across debits.
        assert new_b.starting_bankroll == 2_000

    def test_does_not_mutate_input(self, empty_table, player_bankroll):
        sit_down(empty_table, 0, PLAYER_SEAT_ID, 500, player_bankroll)
        # Original table + bankroll unchanged
        assert empty_table.seats == (None,) * 6
        assert empty_table.stack_of(PLAYER_SEAT_ID) == 0
        assert player_bankroll.chips == 2_000

    def test_blocked_during_hand(self, empty_table, player_bankroll):
        in_hand = empty_table.with_hand_in_progress(True)
        with pytest.raises(HandInProgressError):
            sit_down(in_hand, 0, PLAYER_SEAT_ID, 500, player_bankroll)
        # Rollback: original state untouched
        assert in_hand.seats == (None,) * 6

    def test_rejects_occupied_seat(self, seated_table, player_bankroll):
        # seat 0 is taken
        t, _ = seated_table
        bob = PlayerBankrollState("bob", 2_000, 2_000)
        with pytest.raises(SeatingError, match="occupied"):
            sit_down(t, 0, "bob", 500, bob)

    def test_rejects_already_seated_player(self, seated_table, player_bankroll):
        t, b = seated_table
        # Player already seated at 0; try seat 3 — should fail with
        # "already seated" rather than letting them double-buy.
        with pytest.raises(SeatingError, match="already seated"):
            sit_down(t, 3, PLAYER_SEAT_ID, 500, b)

    def test_rejects_buy_in_below_min(self, empty_table, player_bankroll):
        # min_buy_in = 10 * 40 = 400
        with pytest.raises(SeatingError, match="below table min_buy_in"):
            sit_down(empty_table, 0, PLAYER_SEAT_ID, 399, player_bankroll)

    def test_rejects_buy_in_above_max(self, empty_table, player_bankroll):
        # max_buy_in = 10 * 100 = 1000
        bigger_bankroll = PlayerBankrollState("alice", 5_000, 2_000)
        with pytest.raises(SeatingError, match="exceeds table max_buy_in"):
            sit_down(empty_table, 0, PLAYER_SEAT_ID, 1_001, bigger_bankroll)

    def test_rejects_insufficient_bankroll(self, empty_table):
        broke = PlayerBankrollState("alice", 300, 2_000)
        with pytest.raises(SeatingError, match="insufficient"):
            sit_down(empty_table, 0, PLAYER_SEAT_ID, 500, broke)

    def test_rejects_out_of_range_seat_index(self, empty_table, player_bankroll):
        with pytest.raises(SeatingError, match="out of range"):
            sit_down(empty_table, 7, PLAYER_SEAT_ID, 500, player_bankroll)
        with pytest.raises(SeatingError, match="out of range"):
            sit_down(empty_table, -1, PLAYER_SEAT_ID, 500, player_bankroll)

    def test_max_buy_in_exact_boundary_allowed(self, empty_table, player_bankroll):
        bigger = PlayerBankrollState("alice", 5_000, 5_000)
        new_t, new_b = sit_down(empty_table, 0, PLAYER_SEAT_ID, 1_000, bigger)
        assert new_t.stack_of(PLAYER_SEAT_ID) == 1_000


# --- Row 1 (AI variant): sit_down_ai ---


class TestSitDownAI:
    """AI sit-down mirrors the human sit_down test matrix but verifies
    the AI-specific invariants: chips debited from AIBankrollState,
    last_regen_tick written to `now`, no PlayerBankrollState entanglement.
    """

    @pytest.fixture
    def ai_bankroll(self) -> AIBankrollState:
        # last_regen_tick None: fresh seed state (never sat before).
        return AIBankrollState("napoleon", chips=10_000, last_regen_tick=None)

    @pytest.fixture
    def now(self) -> datetime:
        return datetime(2026, 5, 18, 12, 0, 0)

    def test_happy_path_debits_bankroll_and_sets_stack(self, empty_table, ai_bankroll, now):
        new_t, new_b = sit_down_ai(empty_table, 2, "napoleon", 500, ai_bankroll, now=now)
        assert new_t.seats[2] == "napoleon"
        assert new_t.stack_of("napoleon") == 500
        assert new_b.chips == 9_500
        assert new_b.last_regen_tick == now
        assert new_b.personality_id == "napoleon"

    def test_writes_last_regen_tick_on_fresh_seed(self, empty_table, now):
        # Even if the input had no last_regen_tick (never-sat state),
        # the output must carry now() so projection from this point
        # is correct.
        fresh = AIBankrollState("zeus", chips=200_000, last_regen_tick=None)
        _new_t, new_b = sit_down_ai(empty_table, 0, "zeus", 1_000, fresh, now=now)
        assert new_b.last_regen_tick == now

    def test_does_not_mutate_input(self, empty_table, ai_bankroll, now):
        sit_down_ai(empty_table, 0, "napoleon", 500, ai_bankroll, now=now)
        assert ai_bankroll.chips == 10_000
        assert ai_bankroll.last_regen_tick is None
        assert empty_table.seats == (None,) * 6

    def test_blocked_during_hand(self, empty_table, ai_bankroll, now):
        in_hand = empty_table.with_hand_in_progress(True)
        with pytest.raises(HandInProgressError):
            sit_down_ai(in_hand, 0, "napoleon", 500, ai_bankroll, now=now)

    def test_rejects_occupied_seat(self, empty_table, ai_bankroll, now):
        # Seat 0 taken by a different personality
        taken = empty_table.with_seat(0, "zeus").with_stack("zeus", 500)
        with pytest.raises(SeatingError, match="occupied"):
            sit_down_ai(taken, 0, "napoleon", 500, ai_bankroll, now=now)

    def test_rejects_already_seated_personality(self, empty_table, ai_bankroll, now):
        # Same personality, different seat — Napoleon can't double-buy
        sat = empty_table.with_seat(0, "napoleon").with_stack("napoleon", 500)
        with pytest.raises(SeatingError, match="already seated"):
            sit_down_ai(sat, 3, "napoleon", 500, ai_bankroll, now=now)

    def test_rejects_buy_in_below_min(self, empty_table, ai_bankroll, now):
        with pytest.raises(SeatingError, match="below table min_buy_in"):
            sit_down_ai(empty_table, 0, "napoleon", 399, ai_bankroll, now=now)

    def test_rejects_buy_in_above_max(self, empty_table, ai_bankroll, now):
        with pytest.raises(SeatingError, match="exceeds table max_buy_in"):
            sit_down_ai(empty_table, 0, "napoleon", 1_001, ai_bankroll, now=now)

    def test_rejects_insufficient_bankroll(self, empty_table, now):
        broke_ai = AIBankrollState("broke", chips=300, last_regen_tick=now)
        with pytest.raises(SeatingError, match="insufficient"):
            sit_down_ai(empty_table, 0, "broke", 500, broke_ai, now=now)

    def test_rejects_out_of_range_seat_index(self, empty_table, ai_bankroll, now):
        with pytest.raises(SeatingError, match="out of range"):
            sit_down_ai(empty_table, 7, "napoleon", 500, ai_bankroll, now=now)
        with pytest.raises(SeatingError, match="out of range"):
            sit_down_ai(empty_table, -1, "napoleon", 500, ai_bankroll, now=now)

    def test_max_buy_in_exact_boundary_allowed(self, empty_table, ai_bankroll, now):
        new_t, new_b = sit_down_ai(empty_table, 0, "napoleon", 1_000, ai_bankroll, now=now)
        assert new_t.stack_of("napoleon") == 1_000
        assert new_b.chips == 9_000

    def test_personality_id_preserved_in_output(self, empty_table, now):
        # If the input AIBankrollState has personality_id "x" but the
        # caller passes personality_id="y" to sit_down_ai, the function
        # uses the *input bankroll's* personality_id for the returned
        # state (since that's the bankroll being debited).
        # This guards against the caller accidentally writing a row to
        # the wrong personality_id.
        ai = AIBankrollState("napoleon", chips=10_000, last_regen_tick=None)
        _new_t, new_b = sit_down_ai(empty_table, 0, "napoleon", 500, ai, now=now)
        assert new_b.personality_id == "napoleon"

    def test_ai_seat_id_can_coexist_with_player_seat(
        self, empty_table, player_bankroll, ai_bankroll, now
    ):
        # Player sits at 0, AI sits at 1 — both seats occupied, both
        # stacks present in the stacks mapping. No interference.
        t, _ = sit_down(empty_table, 0, PLAYER_SEAT_ID, 500, player_bankroll)
        t, _ = sit_down_ai(t, 1, "napoleon", 500, ai_bankroll, now=now)
        assert t.seats[0] == PLAYER_SEAT_ID
        assert t.seats[1] == "napoleon"
        assert t.stack_of(PLAYER_SEAT_ID) == 500
        assert t.stack_of("napoleon") == 500


# --- Row 3 (AI variant): cash_out_ai_seat ---


class TestCashOutAISeat:
    """Pure-function variant of leave_table for AI seats.

    Unused in v1 — AI seats only leave the table on bust (no bankroll
    move; the chips were lost in the hand). Path B (relationship-driven
    stand-up) and Path C (stop-loss / stop-win) will use this. Tests
    cover the accounting math + table state transition.
    """

    @pytest.fixture
    def knobs(self) -> BankrollKnobs:
        return BankrollKnobs(
            starting_bankroll=50_000,
            bankroll_rate=500,
            buy_in_multiplier=1.0,
            stake_comfort_zone="$10",
        )

    @pytest.fixture
    def now(self) -> datetime:
        return datetime(2026, 5, 18, 12, 0, 0)

    @pytest.fixture
    def seated_ai(self, empty_table, now) -> tuple[CashTable, AIBankrollState]:
        """Napoleon seated at seat 1 with 500-chip buy-in. Bankroll
        starts at 10_000, gets debited to 9_500 by sit-down."""
        ai = AIBankrollState("napoleon", chips=10_000, last_regen_tick=None)
        return sit_down_ai(empty_table, 1, "napoleon", 500, ai, now=now)

    def test_happy_path_credits_table_stack_clears_seat(self, seated_ai, knobs, now):
        t, ai = seated_ai  # seat 1 = napoleon, stack=500, bankroll=9_500
        new_t, new_b = cash_out_ai_seat(t, "napoleon", ai, knobs, now=now)
        # Seat cleared.
        assert new_t.seats[1] is None
        assert not new_t.is_seated("napoleon")
        # Bankroll: projected (no elapsed time) = 9_500, + 500 chips = 10_000.
        assert new_b.chips == 10_000
        assert new_b.last_regen_tick == now
        assert new_b.personality_id == "napoleon"

    def test_projection_applied_before_credit(self, empty_table, knobs, now, monkeypatch):
        # Regen projection is retired as a *default* (REGEN_ENABLED=False)
        # per CASH_MODE_SIDE_HUSTLE.md but still supported; enable it so
        # this test exercises the projection-before-credit ordering.
        monkeypatch.setattr("cash_mode.economy_flags.REGEN_ENABLED", True)
        # last_regen_tick is one day ago. Bankroll debited by sit_down
        # would be 9_500, but we construct the seated state manually
        # so we control the regen tick.
        one_day_ago = now - timedelta(days=1)
        ai = AIBankrollState(
            "napoleon",
            chips=9_500,
            last_regen_tick=one_day_ago,
        )
        t = empty_table.with_seat(1, "napoleon").with_stack("napoleon", 500)
        _new_t, new_b = cash_out_ai_seat(t, "napoleon", ai, knobs, now=now)
        # 9_500 + 500 (regen) + 500 (table) = 10_500.
        assert new_b.chips == 10_500

    def test_winnings_above_target_are_kept(self, empty_table, knobs, now):
        # starting_bankroll is a regen *target*, not a ceiling. A win
        # that pushes bankroll above the target keeps the excess —
        # AIs can climb past their character's natural-wealth tier.
        ai = AIBankrollState(
            "napoleon",
            chips=49_900,
            last_regen_tick=now,
        )
        t = empty_table.with_seat(1, "napoleon").with_stack("napoleon", 500)
        _new_t, new_b = cash_out_ai_seat(t, "napoleon", ai, knobs, now=now)
        assert new_b.chips == 50_400

    def test_busted_seat_no_credit(self, empty_table, knobs, now):
        # Stack 0 at table: seat still clears, bankroll only refreshes
        # the regen tick (chips unchanged).
        ai = AIBankrollState(
            "napoleon",
            chips=8_000,
            last_regen_tick=now,
        )
        t = empty_table.with_seat(1, "napoleon").with_stack("napoleon", 0)
        new_t, new_b = cash_out_ai_seat(t, "napoleon", ai, knobs, now=now)
        assert new_t.seats[1] is None
        assert new_b.chips == 8_000
        assert new_b.last_regen_tick == now

    def test_blocked_during_hand(self, seated_ai, knobs, now):
        t, ai = seated_ai
        in_hand = t.with_hand_in_progress(True)
        with pytest.raises(HandInProgressError):
            cash_out_ai_seat(in_hand, "napoleon", ai, knobs, now=now)

    def test_rejects_not_seated(self, empty_table, knobs, now):
        ai = AIBankrollState("napoleon", chips=10_000, last_regen_tick=now)
        with pytest.raises(SeatingError, match="not seated"):
            cash_out_ai_seat(empty_table, "napoleon", ai, knobs, now=now)

    def test_does_not_mutate_inputs(self, seated_ai, knobs, now):
        t, ai = seated_ai
        cash_out_ai_seat(t, "napoleon", ai, knobs, now=now)
        # Originals unchanged
        assert t.seats[1] == "napoleon"
        assert t.stack_of("napoleon") == 500
        assert ai.chips == 9_500

    def test_stack_entry_removed(self, seated_ai, knobs, now):
        t, ai = seated_ai
        new_t, _ = cash_out_ai_seat(t, "napoleon", ai, knobs, now=now)
        # The stacks mapping no longer contains the AI.
        assert "napoleon" not in new_t.stacks

    def test_round_trip_with_sit_down_ai_conserves_chips_no_regen(self, empty_table, now):
        # Sit AI down, immediately cash them out — total bankroll
        # back to original (no elapsed time, no regen, no winnings).
        knobs = BankrollKnobs(
            starting_bankroll=50_000,
            bankroll_rate=0,  # no regen for invariant clarity
            buy_in_multiplier=1.0,
            stake_comfort_zone="$10",
        )
        ai = AIBankrollState("napoleon", chips=10_000, last_regen_tick=None)
        t, after_sit = sit_down_ai(empty_table, 0, "napoleon", 500, ai, now=now)
        # After sit-down: bankroll=9_500, table stack=500.
        assert after_sit.chips == 9_500
        # Immediately cash out: bankroll back to 10_000.
        _t, after_cash = cash_out_ai_seat(t, "napoleon", after_sit, knobs, now=now)
        assert after_cash.chips == 10_000


# --- Row 2: Top up ---


class TestTopUp:
    def test_happy_path_credits_stack_debits_bankroll(self, seated_table):
        t, b = seated_table  # stack=500, bankroll=1500
        new_t, new_b = top_up(t, PLAYER_SEAT_ID, 200, b)
        assert new_t.stack_of(PLAYER_SEAT_ID) == 700
        assert new_b.chips == 1_300

    def test_blocked_during_hand(self, seated_table):
        t, b = seated_table
        in_hand = t.with_hand_in_progress(True)
        with pytest.raises(HandInProgressError):
            top_up(in_hand, PLAYER_SEAT_ID, 200, b)

    def test_rejects_topup_that_would_overflow_max(self, seated_table):
        t, b = seated_table  # stack=500, max=1000
        with pytest.raises(SeatingError, match="above max_buy_in"):
            top_up(t, PLAYER_SEAT_ID, 600, b)  # 500+600 > 1000

    def test_at_max_boundary_allowed(self, seated_table):
        t, b = seated_table
        # 500 + 500 = 1000 (exact cap) — allowed
        new_t, _ = top_up(t, PLAYER_SEAT_ID, 500, b)
        assert new_t.stack_of(PLAYER_SEAT_ID) == 1_000

    def test_rejects_topup_when_not_seated(self, empty_table, player_bankroll):
        with pytest.raises(SeatingError, match="not seated"):
            top_up(empty_table, PLAYER_SEAT_ID, 200, player_bankroll)

    def test_rejects_zero_or_negative_topup(self, seated_table):
        t, b = seated_table
        with pytest.raises(SeatingError, match="must be positive"):
            top_up(t, PLAYER_SEAT_ID, 0, b)
        with pytest.raises(SeatingError, match="must be positive"):
            top_up(t, PLAYER_SEAT_ID, -100, b)

    def test_rejects_insufficient_bankroll(self, seated_table):
        t, b = seated_table
        broke = PlayerBankrollState("alice", 50, 2_000)
        with pytest.raises(SeatingError, match="insufficient"):
            top_up(t, PLAYER_SEAT_ID, 200, broke)


# --- Row 3: Leave table ---


class TestLeaveTable:
    def test_happy_path_returns_stack_clears_seat(self, seated_table):
        t, b = seated_table  # stack=500, bankroll=1500
        new_t, new_b = leave_table(t, PLAYER_SEAT_ID, b)
        assert new_t.seats[0] is None
        assert new_t.stack_of(PLAYER_SEAT_ID) == 0
        assert PLAYER_SEAT_ID not in new_t.stacks  # entry removed, not zeroed
        assert new_b.chips == 2_000  # 1500 + 500 back home

    def test_blocked_during_hand(self, seated_table):
        t, b = seated_table
        in_hand = t.with_hand_in_progress(True)
        with pytest.raises(HandInProgressError):
            leave_table(in_hand, PLAYER_SEAT_ID, b)

    def test_rejects_leave_when_not_seated(self, empty_table, player_bankroll):
        with pytest.raises(SeatingError, match="not seated"):
            leave_table(empty_table, PLAYER_SEAT_ID, player_bankroll)

    def test_leave_after_winnings_returns_credited_stack(self, seated_table):
        t, b = seated_table  # stack=500
        # Simulate winnings via apply_settlement, then leave
        after_win = apply_settlement(t, PLAYER_SEAT_ID, +250)
        new_t, new_b = leave_table(after_win, PLAYER_SEAT_ID, b)
        assert new_b.chips == 2_000 + 250  # original 1500 left + 750 returned


# --- Row 4: Bust at table ---


class TestBustAtTable:
    def test_clears_seat_after_zero_stack(self, seated_table):
        t, _ = seated_table
        # Simulate the hand having taken all chips
        zeroed = t.with_stack(PLAYER_SEAT_ID, 0)
        new_t = bust_at_table(zeroed, PLAYER_SEAT_ID)
        assert new_t.seats[0] is None
        assert PLAYER_SEAT_ID not in new_t.stacks

    def test_no_bankroll_change(self, seated_table, player_bankroll):
        # bust_at_table only takes (table, seat_id) — no bankroll
        # surface — so by construction the bankroll cannot change here.
        t, _ = seated_table
        result = bust_at_table(t.with_stack(PLAYER_SEAT_ID, 0), PLAYER_SEAT_ID)
        assert PLAYER_SEAT_ID not in result.stacks

    def test_idempotent_on_empty_seat(self, empty_table):
        # Re-running bust_at_table on a seat that's already empty
        # must not raise — defensive, since the session layer may
        # call this from multiple cleanup paths.
        new_t = bust_at_table(empty_table, PLAYER_SEAT_ID)
        assert new_t == empty_table


# Row 5 (`full_bankroll_bust`) intentionally absent. The "auto-reset
# busted player bankrolls to starting_bankroll" rule was deleted —
# busted players must use the staking flow (or wait, if AIs gain regen
# later) rather than receiving free chips. The function was dead code
# in production; removing it makes the absence load-bearing in the
# code instead of just in the wiring.


# --- Row 6: Mid-hand quit ---


class TestMidHandQuit:
    def test_returns_forfeit_chips_and_clears_seat(self, seated_table):
        t, _ = seated_table  # stack=500
        new_t, forfeit = mid_hand_quit(t, PLAYER_SEAT_ID)
        assert forfeit == 500
        assert new_t.seats[0] is None
        assert PLAYER_SEAT_ID not in new_t.stacks

    def test_does_not_check_hand_in_progress(self, seated_table):
        # Mid-hand quit is *expected* to fire mid-hand; no block.
        t, _ = seated_table
        in_hand = t.with_hand_in_progress(True)
        new_t, forfeit = mid_hand_quit(in_hand, PLAYER_SEAT_ID)
        assert forfeit == 500
        # hand_in_progress preserved — the session layer flips it,
        # not the accounting transition.
        assert new_t.hand_in_progress is True

    def test_rejects_quit_when_not_seated(self, empty_table):
        with pytest.raises(SeatingError, match="not seated"):
            mid_hand_quit(empty_table, PLAYER_SEAT_ID)


# --- Row 7: Disconnect timeout ---


class TestDisconnectTimeout:
    def test_identical_to_mid_hand_quit(self, seated_table):
        t, _ = seated_table
        quit_table, quit_forfeit = mid_hand_quit(t, PLAYER_SEAT_ID)
        dc_table, dc_forfeit = disconnect_timeout(t, PLAYER_SEAT_ID)
        assert quit_forfeit == dc_forfeit
        assert quit_table.seats == dc_table.seats
        assert quit_table.stacks == dc_table.stacks


# --- Row 8: Hand settlement ---


class TestApplySettlement:
    def test_winnings_credit_stack(self, seated_table):
        t, _ = seated_table  # stack=500
        new_t = apply_settlement(t, PLAYER_SEAT_ID, +250)
        assert new_t.stack_of(PLAYER_SEAT_ID) == 750

    def test_losses_debit_stack(self, seated_table):
        t, _ = seated_table  # stack=500
        new_t = apply_settlement(t, PLAYER_SEAT_ID, -300)
        assert new_t.stack_of(PLAYER_SEAT_ID) == 200

    def test_zero_delta_no_change(self, seated_table):
        t, _ = seated_table
        new_t = apply_settlement(t, PLAYER_SEAT_ID, 0)
        assert new_t.stack_of(PLAYER_SEAT_ID) == 500

    def test_overdraft_clamps_to_zero(self, seated_table):
        # Defensive: settlement upstream shouldn't ever debit below 0,
        # but if it does, clamp to 0 rather than carry negative chips.
        t, _ = seated_table
        new_t = apply_settlement(t, PLAYER_SEAT_ID, -10_000)
        assert new_t.stack_of(PLAYER_SEAT_ID) == 0

    def test_rejects_settlement_for_unseated(self, empty_table):
        with pytest.raises(SeatingError, match="not seated"):
            apply_settlement(empty_table, PLAYER_SEAT_ID, +100)

    def test_no_bankroll_field_in_signature(self, seated_table, player_bankroll):
        # Settlement is table-only — bankroll never moves on row 8
        # per spec. By construction the signature can't change it.
        # This is a smoke assertion that the spec's "Bankroll
        # unchanged" invariant is enforced structurally.
        t, b = seated_table
        before_bankroll = b.chips
        _new_t = apply_settlement(t, PLAYER_SEAT_ID, +250)
        # b is unchanged
        assert b.chips == before_bankroll


# --- Integrated chip-conservation tests ---


class TestChipConservation:
    """Round-trip tests verifying chips don't leak across sequences.

    The accounting matrix's correctness is most visible in
    composition: a sit-down + top-up + leave should return the
    player to their original total chips. Settlement winnings move
    chips between seats; the table's total chip count changes only
    when the bankroll or pot exchanges happen.
    """

    def test_sit_then_leave_round_trips(self, empty_table, player_bankroll):
        t1, b1 = sit_down(empty_table, 0, PLAYER_SEAT_ID, 500, player_bankroll)
        t2, b2 = leave_table(t1, PLAYER_SEAT_ID, b1)
        # Bankroll restored, table empty
        assert b2.chips == player_bankroll.chips
        assert t2.seats == empty_table.seats
        assert t2.stacks == empty_table.stacks

    def test_sit_topup_leave_round_trips(self, empty_table, player_bankroll):
        t1, b1 = sit_down(empty_table, 0, PLAYER_SEAT_ID, 500, player_bankroll)
        t2, b2 = top_up(t1, PLAYER_SEAT_ID, 200, b1)
        t3, b3 = leave_table(t2, PLAYER_SEAT_ID, b2)
        assert b3.chips == player_bankroll.chips
        assert t3.seats == empty_table.seats

    def test_sit_win_then_leave_returns_winnings_to_bankroll(
        self,
        empty_table,
        player_bankroll,
    ):
        t1, b1 = sit_down(empty_table, 0, PLAYER_SEAT_ID, 500, player_bankroll)
        t2 = apply_settlement(t1, PLAYER_SEAT_ID, +300)  # won 300
        t3, b3 = leave_table(t2, PLAYER_SEAT_ID, b1)
        # Bankroll: 2000 - 500 (sit) + 800 (leave with winnings) = 2300
        assert b3.chips == player_bankroll.chips + 300

    def test_sit_lose_all_bust_round_trip(self, empty_table, player_bankroll):
        t1, b1 = sit_down(empty_table, 0, PLAYER_SEAT_ID, 500, player_bankroll)
        # Lose entire stack at showdown
        t2 = apply_settlement(t1, PLAYER_SEAT_ID, -500)
        t3 = bust_at_table(t2, PLAYER_SEAT_ID)
        # Bankroll did not get topped up — player is out 500
        assert b1.chips == player_bankroll.chips - 500
        # Seat now empty
        assert t3.seats[0] is None
        # A bust at bankroll=0 leaves the player broke; the staking
        # flow (sponsor offers) is the only recovery path. The system
        # never re-grants chips for free.

    def test_full_bust_leaves_player_at_zero(self, empty_table):
        # Walk a player from starting bankroll → 0 → must be staked
        # to play again. No auto-refill.
        b0 = PlayerBankrollState("alice", 500, 2_000)  # already low
        t1, b1 = sit_down(empty_table, 0, PLAYER_SEAT_ID, 500, b0)
        # bankroll now 0; lose stack
        t2 = apply_settlement(t1, PLAYER_SEAT_ID, -500)
        t3 = bust_at_table(t2, PLAYER_SEAT_ID)
        assert b1.chips == 0
        assert t3.seats[0] is None
        # Bankroll stays at 0 — recovery is via the staking system,
        # not an automatic refill.


# --- CashTable invariants ---


class TestCashTableInvariants:
    def test_init_fills_empty_seats_for_seat_count(self):
        t = CashTable(
            table_id="t",
            stake_label="$10",
            big_blind=10,
            min_buy_in=400,
            max_buy_in=1000,
            seat_count=6,
        )
        assert t.seats == (None, None, None, None, None, None)

    def test_init_rejects_mismatched_seat_count(self):
        with pytest.raises(ValueError, match="seats length"):
            CashTable(
                table_id="t",
                stake_label="$10",
                big_blind=10,
                min_buy_in=400,
                max_buy_in=1000,
                seat_count=6,
                seats=(None, None),  # length 2, count 6
            )

    def test_seat_index_of_returns_index_or_none(self, seated_table):
        t, _ = seated_table
        assert t.seat_index_of(PLAYER_SEAT_ID) == 0
        assert t.seat_index_of("missing") is None

    def test_is_seated(self, seated_table):
        t, _ = seated_table
        assert t.is_seated(PLAYER_SEAT_ID)
        assert not t.is_seated("nobody")

    def test_open_seats(self, seated_table):
        t, _ = seated_table  # seat 0 taken
        assert t.open_seats() == (1, 2, 3, 4, 5)

    def test_stack_of_returns_zero_for_unseated(self, empty_table):
        assert empty_table.stack_of("anyone") == 0

    def test_new_table_derives_buy_in_bounds_from_bb(self):
        t = new_table(table_id="t", stake_label="$10", big_blind=10)
        assert t.min_buy_in == 400  # 10 * 40
        assert t.max_buy_in == 1_000  # 10 * 100

    def test_new_table_respects_custom_bb_multipliers(self):
        t = new_table(
            table_id="t",
            stake_label="custom",
            big_blind=20,
            min_buy_in_bb=20,
            max_buy_in_bb=200,
        )
        assert t.min_buy_in == 400
        assert t.max_buy_in == 4_000
