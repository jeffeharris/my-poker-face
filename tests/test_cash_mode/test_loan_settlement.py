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
"""

from __future__ import annotations

import pytest

from cash_mode.bankroll import PlayerBankrollState
from cash_mode.loan_settlement import settle_loan_on_leave


def _bankroll(
    chips: int = 0,
    starting: int = 200,
    *,
    loan: int = 0,
    floor: float = 0.0,
    rate: float = 0.0,
) -> PlayerBankrollState:
    return PlayerBankrollState(
        player_id="test",
        chips=chips,
        starting_bankroll=starting,
        active_loan_amount=loan,
        active_loan_floor=floor,
        active_loan_rate=rate,
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
