"""Tests for leave-time sponsor-loan settlement math.

Coverage targets the worked examples from the design discussion:

  Friendly Boost ($200 loan, floor 1.00, rate 20%):
    chips=$500: floor=$200 → remaining=$300 → cut=$60 → player=$240
    chips=$150: all $150 to sponsor, balance forgiven, player=$0

  Loan Shark ($1000 loan, floor 1.30, rate 40%):
    chips=$1200: floor=$1300 > chips → all $1200 to sponsor,
                  $100 of floor forgiven, player=$0
    chips=$2000: floor=$1300 → remaining=$700 → cut=$280 → player=$420
    chips=$3000: floor=$1300 → remaining=$1700 → cut=$680 → player=$1020

  The Premium ($400 loan, floor 1.30, rate 0%):
    chips=$1000: floor=$520 → remaining=$480 → cut=$0 → player=$480
    chips=$520: floor=$520 → remaining=$0 → cut=$0 → player=$0

Also covers the no-loan branch and the loan-fields-zero invariant.

Path B (commit 5) extension: when active_loan_lender_id is set on
the bankroll AND a bankroll_repo is passed, sponsor_total credits to
the AI lender's persistent bankroll. Tests at the bottom of this file.
"""

from __future__ import annotations

from typing import Optional
from unittest.mock import MagicMock

import pytest

from cash_mode.bankroll import (
    AIBankrollState,
    BANKROLL_KNOB_DEFAULTS,
    PlayerBankrollState,
)
from cash_mode.loan_settlement import (
    classify_loan_outcome,
    settle_loan_on_leave,
)


def _bankroll(
    chips: int = 0,
    starting: int = 200,
    *,
    loan: int = 0,
    floor: float = 0.0,
    rate: float = 0.0,
    lender_id: Optional[str] = None,
) -> PlayerBankrollState:
    return PlayerBankrollState(
        player_id="test",
        chips=chips,
        starting_bankroll=starting,
        active_loan_amount=loan,
        active_loan_floor=floor,
        active_loan_rate=rate,
        active_loan_lender_id=lender_id,
    )


class TestNoLoanBranch:
    def test_chips_return_to_bankroll(self):
        result = settle_loan_on_leave(_bankroll(chips=100), chips_at_table=500)
        assert result.new_bankroll.chips == 600
        assert result.sponsor_total == 0
        assert result.returned_chips == 500

    def test_zero_chips_zero_return(self):
        result = settle_loan_on_leave(_bankroll(chips=100), chips_at_table=0)
        assert result.new_bankroll.chips == 100
        assert result.returned_chips == 0


class TestFriendlyBoost:
    """$200 loan, floor 1.00, rate 20%."""

    def test_winning_path(self):
        # $500 chips: floor=$200, remaining=$300, cut=$60, player=$240
        b = _bankroll(chips=0, loan=200, floor=1.00, rate=0.20)
        result = settle_loan_on_leave(b, chips_at_table=500)
        assert result.sponsor_total == 260  # 200 floor + 60 cut
        assert result.returned_chips == 240
        assert result.new_bankroll.chips == 240

    def test_partial_repayment_forgiven(self):
        # $150 chips, $200 floor → all $150 to sponsor, $50 forgiven
        b = _bankroll(chips=0, loan=200, floor=1.00, rate=0.20)
        result = settle_loan_on_leave(b, chips_at_table=150)
        assert result.sponsor_total == 150
        assert result.returned_chips == 0
        assert result.new_bankroll.chips == 0

    def test_busted_with_loan_walks_away_clean(self):
        # 0 chips, active loan → sponsor gets 0, player gets 0,
        # loan forgiven (v1 — no reputation hit).
        b = _bankroll(chips=0, loan=200, floor=1.00, rate=0.20)
        result = settle_loan_on_leave(b, chips_at_table=0)
        assert result.sponsor_total == 0
        assert result.returned_chips == 0
        assert result.new_bankroll.chips == 0


class TestLoanShark:
    """$1000 loan, floor 1.30, rate 40% — the predatory archetype."""

    def test_below_floor_all_to_sponsor(self):
        # $1200 chips, $1300 floor → all $1200 to sponsor, $100 forgiven
        b = _bankroll(chips=0, loan=1000, floor=1.30, rate=0.40)
        result = settle_loan_on_leave(b, chips_at_table=1200)
        assert result.sponsor_total == 1200
        assert result.returned_chips == 0
        assert result.new_bankroll.chips == 0

    def test_modest_win_painful(self):
        # $2000 chips: floor=$1300, remaining=$700, cut=$280, player=$420
        b = _bankroll(chips=0, loan=1000, floor=1.30, rate=0.40)
        result = settle_loan_on_leave(b, chips_at_table=2000)
        assert result.sponsor_total == 1580  # 1300 + 280
        assert result.returned_chips == 420
        assert result.new_bankroll.chips == 420

    def test_big_win_finally_meaningful(self):
        # $3000 chips: floor=$1300, remaining=$1700, cut=$680, player=$1020
        b = _bankroll(chips=0, loan=1000, floor=1.30, rate=0.40)
        result = settle_loan_on_leave(b, chips_at_table=3000)
        assert result.sponsor_total == 1980  # 1300 + 680
        assert result.returned_chips == 1020


class TestThePremium:
    """$400 loan, floor 1.30, rate 0% — pay the premium, keep upside."""

    def test_winning_keeps_all_post_floor(self):
        # $1000 chips: floor=$520, remaining=$480, cut=$0, player=$480
        b = _bankroll(chips=0, loan=400, floor=1.30, rate=0.00)
        result = settle_loan_on_leave(b, chips_at_table=1000)
        assert result.sponsor_total == 520
        assert result.returned_chips == 480

    def test_exactly_at_floor_zero_to_player(self):
        # $520 chips: all to floor, nothing left to split.
        b = _bankroll(chips=0, loan=400, floor=1.30, rate=0.00)
        result = settle_loan_on_leave(b, chips_at_table=520)
        assert result.sponsor_total == 520
        assert result.returned_chips == 0


class TestInvariants:
    def test_loan_fields_always_zero_in_new_bankroll(self):
        # Whether settled fully, partially forgiven, or no-loan path,
        # the returned bankroll always has zeroed loan fields. Loans
        # are session-scoped per v1.
        for chips_at_table in [0, 100, 500, 5000]:
            b = _bankroll(chips=50, loan=500, floor=1.10, rate=0.25)
            result = settle_loan_on_leave(b, chips_at_table=chips_at_table)
            assert result.new_bankroll.active_loan_amount == 0
            assert result.new_bankroll.active_loan_floor == 0.0
            assert result.new_bankroll.active_loan_rate == 0.0

    def test_existing_bankroll_chips_preserved(self):
        # Pre-existing bankroll chips are added to, not replaced by,
        # the post-settlement returned_chips.
        b = _bankroll(chips=300, loan=200, floor=1.00, rate=0.20)
        result = settle_loan_on_leave(b, chips_at_table=500)
        # post-settlement returned = 240; existing chips = 300
        assert result.new_bankroll.chips == 540

    def test_starting_bankroll_preserved(self):
        b = _bankroll(chips=0, starting=200, loan=200, floor=1.00, rate=0.20)
        result = settle_loan_on_leave(b, chips_at_table=500)
        assert result.new_bankroll.starting_bankroll == 200

    def test_sums_match_chips_at_table_when_loan_clears(self):
        # When the loan settles fully (chips_at_table > floor), the
        # invariant `sponsor_total + returned_chips == chips_at_table`
        # must hold. No chips disappear into rounding.
        b = _bankroll(chips=0, loan=1000, floor=1.30, rate=0.40)
        result = settle_loan_on_leave(b, chips_at_table=3000)
        assert result.sponsor_total + result.returned_chips == 3000

    def test_no_negative_amounts(self):
        # All amounts non-negative regardless of input.
        for chips in [0, 1, 50, 1_000, 100_000]:
            b = _bankroll(chips=0, loan=500, floor=1.20, rate=0.30)
            result = settle_loan_on_leave(b, chips_at_table=chips)
            assert result.sponsor_total >= 0
            assert result.returned_chips >= 0
            assert result.new_bankroll.chips >= 0


# --- Path B: outcome classification ---


class TestClassifyLoanOutcome:
    """`classify_loan_outcome` labels the settlement so the route knows
    which RelationshipEvent to fire."""

    def test_no_loan_returns_no_loan(self):
        assert classify_loan_outcome(_bankroll(chips=0), 500) == "no_loan"

    def test_zero_chips_returns_no_chips_no_event(self):
        b = _bankroll(loan=200, floor=1.0, rate=0.2)
        assert classify_loan_outcome(b, 0) == "no_chips_no_event"

    def test_chips_above_floor_repaid(self):
        # 200 loan, 1.0 floor → floor amount 200. Chips 500 > floor.
        b = _bankroll(loan=200, floor=1.0, rate=0.2)
        assert classify_loan_outcome(b, 500) == "repaid"

    def test_chips_below_floor_defaulted(self):
        # 200 loan, 1.0 floor → floor 200. Chips 150 < floor → default.
        b = _bankroll(loan=200, floor=1.0, rate=0.2)
        assert classify_loan_outcome(b, 150) == "defaulted"

    def test_chips_exactly_at_floor_repaid(self):
        b = _bankroll(loan=200, floor=1.0, rate=0.2)
        assert classify_loan_outcome(b, 200) == "repaid"


# --- Path B: AI-lender credit on settlement ---


class TestAILenderCredit:
    """When lender_id is set and a bankroll_repo is provided, settlement
    credits sponsor_total back to the AI lender's persistent bankroll.
    """

    def _build_repo(self, *, lender_chips: int = 5_000, cap: int = 50_000):
        """Fake bankroll_repo with just enough surface for credit_ai_cash_out.

        Returns the mock so tests can assert against `save_ai_bankroll`
        calls.
        """
        repo = MagicMock()
        # load_ai_bankroll returns a current state.
        from datetime import datetime
        state = AIBankrollState(
            personality_id="napoleon",
            chips=lender_chips,
            last_regen_tick=datetime(2026, 5, 18, 12, 0),
        )
        repo.load_ai_bankroll.return_value = state
        # load_personality_knobs returns knobs with the given cap.
        knobs = MagicMock()
        knobs.bankroll_cap = cap
        knobs.bankroll_rate = 0  # disable regen for stable assertions
        repo.load_personality_knobs.return_value = knobs
        return repo

    def test_lender_credit_on_repaid(self):
        # Loan repaid (chips=500, loan=200, floor=1.0, rate=0.20 →
        # sponsor_total=260). Lender starts at 5_000 → credits to 5_260.
        repo = self._build_repo(lender_chips=5_000)
        b = _bankroll(loan=200, floor=1.0, rate=0.2, lender_id="napoleon")
        result = settle_loan_on_leave(b, 500, bankroll_repo=repo)
        assert result.sponsor_total == 260
        repo.save_ai_bankroll.assert_called_once()
        saved_state = repo.save_ai_bankroll.call_args[0][0]
        assert saved_state.chips == 5_260
        assert saved_state.personality_id == "napoleon"

    def test_lender_credit_clamped_to_cap(self):
        # Loan repaid (sponsor_total=260) but lender near cap →
        # final clamps to cap. 49_900 + 260 = 50_160 → clamp to 50_000.
        repo = self._build_repo(lender_chips=49_900, cap=50_000)
        b = _bankroll(loan=200, floor=1.0, rate=0.2, lender_id="napoleon")
        result = settle_loan_on_leave(b, 500, bankroll_repo=repo)
        repo.save_ai_bankroll.assert_called_once()
        saved_state = repo.save_ai_bankroll.call_args[0][0]
        assert saved_state.chips == 50_000

    def test_lender_credit_on_partial_default(self):
        # Default branch: chips < floor, all chips go to sponsor.
        # 200 loan, 1.0 floor, chips=150 → sponsor_total=150.
        repo = self._build_repo(lender_chips=5_000)
        b = _bankroll(loan=200, floor=1.0, rate=0.2, lender_id="napoleon")
        result = settle_loan_on_leave(b, 150, bankroll_repo=repo)
        assert result.sponsor_total == 150
        repo.save_ai_bankroll.assert_called_once()
        assert repo.save_ai_bankroll.call_args[0][0].chips == 5_150

    def test_no_lender_id_skips_ai_credit(self):
        # Anonymous house loan — bankroll_repo present but lender_id None.
        # No save_ai_bankroll call.
        repo = self._build_repo()
        b = _bankroll(loan=200, floor=1.0, rate=0.2, lender_id=None)
        result = settle_loan_on_leave(b, 500, bankroll_repo=repo)
        assert result.sponsor_total == 260
        repo.save_ai_bankroll.assert_not_called()

    def test_lender_id_but_no_repo_logs_and_skips(self):
        # Defensive: lender_id set but no repo → log warning, no crash,
        # math still works.
        b = _bankroll(loan=200, floor=1.0, rate=0.2, lender_id="napoleon")
        result = settle_loan_on_leave(b, 500)  # no bankroll_repo
        assert result.sponsor_total == 260
        # No exception raised — the load-bearing surface (settlement math)
        # still runs cleanly.

    def test_zero_sponsor_total_skips_credit(self):
        # Player busts (chips=0) with active loan → sponsor_total=0 →
        # no credit attempt (zero stack handled by the no_chips branch).
        repo = self._build_repo()
        b = _bankroll(loan=200, floor=1.0, rate=0.2, lender_id="napoleon")
        result = settle_loan_on_leave(b, 0, bankroll_repo=repo)
        assert result.sponsor_total == 0
        repo.save_ai_bankroll.assert_not_called()
