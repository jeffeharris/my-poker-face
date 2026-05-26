"""Tests for cash_mode.stake_settlement.settle_stake_on_leave.

Covers the four math branches the spec calls out:
  - Clean settle at multiple cut ratios.
  - Partial-bust carry (chips_at_leave > 0 but < principal+match).
  - Full-bust carry (chips_at_leave == 0).
  - Match-share variants (match_amount > 0).
Plus side-effect coverage on the stake_repo (status + carry persisted)
and idempotency on a non-active stake.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cash_mode.stake_settlement import (
    StakeSettlement,
    settle_stake_on_leave,
)
from cash_mode.stakes import (
    BORROWER_KIND_HUMAN,
    STAKE_FORMAT_HOUSE,
    STAKE_FORMAT_MATCH_SHARE,
    STAKE_FORMAT_PURE,
    STAKE_STATUS_ACTIVE,
    STAKE_STATUS_CARRY,
    STAKE_STATUS_DEFAULTED,
    STAKE_STATUS_SETTLED,
    STAKER_KIND_HOUSE,
    STAKER_KIND_PERSONALITY,
    Stake,
)
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.stake_repository import StakeRepository

ANCHOR = datetime(2026, 5, 19, 12, 0, 0)
SETTLE_TIME = ANCHOR + timedelta(hours=1)


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory() as d:
        db_path = str(Path(d) / "test.db")
        SchemaManager(db_path).ensure_schema()
        yield StakeRepository(db_path)


def _seed_stake(
    repo: StakeRepository,
    *,
    stake_id: str = "stk-1",
    session_id: str = "sess-1",
    principal: int = 400,
    match_amount: int = 0,
    cut: float = 0.20,
    format: str = STAKE_FORMAT_PURE,
    staker_kind: str = STAKER_KIND_PERSONALITY,
    staker_id="napoleon",
    status: str = STAKE_STATUS_ACTIVE,
) -> Stake:
    stake = Stake(
        stake_id=stake_id,
        session_id=session_id,
        staker_id=staker_id,
        staker_kind=staker_kind,
        borrower_id="alice",
        borrower_kind=BORROWER_KIND_HUMAN,
        format=format,
        principal=principal,
        match_amount=match_amount,
        origination_fee=20,
        cut=cut,
        status=status,
        carry_amount=0,
        stake_tier="$10",
        created_at=ANCHOR,
    )
    repo.create_stake(stake)
    return stake


class TestCleanSettle:
    def test_double_up_at_20_pct_cut(self, repo):
        _seed_stake(repo, principal=400, cut=0.20)

        result = settle_stake_on_leave(
            "stk-1",
            800,
            stake_repo=repo,
            now=SETTLE_TIME,
        )

        # net_winnings = 800 - 400 - 0 = 400
        # staker_winnings = 400 * 0.20 = 80
        # staker_total = 400 + 80 = 480
        # borrower_total = 0 + (400 - 80) = 320
        assert result.new_status == STAKE_STATUS_SETTLED
        assert result.staker_total == 480
        assert result.borrower_total == 320
        assert result.carry_amount == 0
        assert result.forgiven_amount == 0
        assert result.staker_id == "napoleon"
        assert result.staker_kind == STAKER_KIND_PERSONALITY

        # Persistence side-effect: status + settled_at flipped.
        stake = repo.load_stake("stk-1")
        assert stake.status == STAKE_STATUS_SETTLED
        assert stake.settled_at == SETTLE_TIME
        assert stake.carry_amount == 0

    def test_double_up_at_45_pct_cut(self, repo):
        _seed_stake(repo, principal=400, cut=0.45)

        result = settle_stake_on_leave(
            "stk-1",
            800,
            stake_repo=repo,
            now=SETTLE_TIME,
        )

        # net_winnings = 400; staker_cut = int(400 * 0.45) = 180.
        assert result.staker_total == 580
        assert result.borrower_total == 220
        assert result.new_status == STAKE_STATUS_SETTLED

    def test_break_even_returns_principal_only(self, repo):
        _seed_stake(repo, principal=400, cut=0.30)

        result = settle_stake_on_leave(
            "stk-1",
            400,
            stake_repo=repo,
            now=SETTLE_TIME,
        )

        # net_winnings = 0. Cut on zero is zero. Staker gets principal,
        # borrower gets nothing.
        assert result.staker_total == 400
        assert result.borrower_total == 0
        assert result.new_status == STAKE_STATUS_SETTLED


class TestPartialBustCarry:
    def test_recover_some_principal_creates_carry(self, repo):
        _seed_stake(repo, principal=400, cut=0.20)

        result = settle_stake_on_leave(
            "stk-1",
            150,
            stake_repo=repo,
            now=SETTLE_TIME,
        )

        # chips_at_leave=150 < principal+match=400.
        # staker recovers min(150, 400) = 150.
        # borrower gets 0.
        # carry = 400 - 150 = 250.
        assert result.new_status == STAKE_STATUS_CARRY
        assert result.staker_total == 150
        assert result.borrower_total == 0
        assert result.carry_amount == 250
        assert result.forgiven_amount == 250

        stake = repo.load_stake("stk-1")
        assert stake.status == STAKE_STATUS_CARRY
        assert stake.carry_amount == 250

    def test_chips_just_below_principal(self, repo):
        _seed_stake(repo, principal=400)
        result = settle_stake_on_leave(
            "stk-1",
            399,
            stake_repo=repo,
            now=SETTLE_TIME,
        )
        assert result.staker_total == 399
        assert result.carry_amount == 1


class TestFullBustCarry:
    def test_zero_chips_full_principal_carry(self, repo):
        _seed_stake(repo, principal=400)

        result = settle_stake_on_leave(
            "stk-1",
            0,
            stake_repo=repo,
            now=SETTLE_TIME,
        )

        assert result.new_status == STAKE_STATUS_CARRY
        assert result.staker_total == 0
        assert result.borrower_total == 0
        assert result.carry_amount == 400
        assert result.forgiven_amount == 400

        stake = repo.load_stake("stk-1")
        assert stake.status == STAKE_STATUS_CARRY
        assert stake.carry_amount == 400

    def test_negative_chips_treated_as_zero(self, repo):
        # Defensive — should never happen in practice but the math
        # should be safe.
        _seed_stake(repo, principal=400)
        result = settle_stake_on_leave(
            "stk-1",
            -10,
            stake_repo=repo,
            now=SETTLE_TIME,
        )
        assert result.carry_amount == 400


class TestMatchShare:
    def test_match_share_clean_settle(self, repo):
        # principal=200, match=200, cut=0.50. Borrower returns with
        # 800 → net_winnings = 800 - 400 = 400.
        # staker_cut = int(400 * 0.50) = 200.
        # staker_total = 200 + 200 = 400.
        # borrower_total = 200 + (400 - 200) = 400.
        _seed_stake(
            repo,
            principal=200,
            match_amount=200,
            cut=0.50,
            format=STAKE_FORMAT_MATCH_SHARE,
        )
        result = settle_stake_on_leave(
            "stk-1",
            800,
            stake_repo=repo,
            now=SETTLE_TIME,
        )
        assert result.staker_total == 400
        assert result.borrower_total == 400
        assert result.new_status == STAKE_STATUS_SETTLED

    def test_match_share_partial_bust(self, repo):
        # principal=200, match=200, returning with 250.
        # net_winnings = 250 - 400 = -150 < 0 (carry path).
        # staker recovers min(250, 200) = 200, carry = 0… wait.
        # Per the spec's carry math: carry_amount = principal - recovered
        # = 200 - 200 = 0. So the staker is whole, but the borrower
        # got their match_amount eaten by the table.
        # borrower_total = 250 - 200 = 50.
        # Edge case: status='carry' but carry_amount=0 — that's a
        # weird state. The spec doesn't address this combo.
        # Per the math: net_winnings < 0 AND chips_at_leave > 0 →
        # carry path, but principal fully recovered means carry=0.
        # The status reflects "borrower came up short on their own
        # match"; staker is fine.
        _seed_stake(
            repo,
            principal=200,
            match_amount=200,
            cut=0.50,
            format=STAKE_FORMAT_MATCH_SHARE,
        )
        result = settle_stake_on_leave(
            "stk-1",
            250,
            stake_repo=repo,
            now=SETTLE_TIME,
        )
        assert result.staker_total == 200
        assert result.borrower_total == 50
        assert result.carry_amount == 0
        # Status is still 'carry' because invested > chips_at_leave —
        # documents the "borrower lost their match" outcome.
        assert result.new_status == STAKE_STATUS_CARRY


class TestHouseStake:
    """House stakes never carry — Phase 1 Commit 5 override.

    The pure math kernel still produces a carry-shaped result on a
    bust; the public `settle_stake_on_leave` function flips it to
    settled + records `forgive_balance` ledger annotation for the
    unrecovered principal.
    """

    def test_house_partial_bust_settles_with_forgive(self, repo):
        _seed_stake(
            repo,
            principal=200,
            cut=0.40,
            format=STAKE_FORMAT_HOUSE,
            staker_kind=STAKER_KIND_HOUSE,
            staker_id=None,
        )
        result = settle_stake_on_leave(
            "stk-1",
            50,
            stake_repo=repo,
            now=SETTLE_TIME,
        )
        # House override: status='settled', no carry row even though
        # the math says we'd be short.
        assert result.new_status == STAKE_STATUS_SETTLED
        assert result.carry_amount == 0
        assert result.forgiven_amount == 150  # 200 - 50 recovered
        assert result.staker_total == 50
        assert result.staker_id is None
        assert result.staker_kind == STAKER_KIND_HOUSE

        stake = repo.load_stake("stk-1")
        assert stake.status == STAKE_STATUS_SETTLED
        assert stake.carry_amount == 0

    def test_house_full_bust_settles_with_forgive(self, repo):
        _seed_stake(
            repo,
            principal=200,
            cut=0.40,
            format=STAKE_FORMAT_HOUSE,
            staker_kind=STAKER_KIND_HOUSE,
            staker_id=None,
        )
        result = settle_stake_on_leave(
            "stk-1",
            0,
            stake_repo=repo,
            now=SETTLE_TIME,
        )
        assert result.new_status == STAKE_STATUS_SETTLED
        assert result.carry_amount == 0
        assert result.forgiven_amount == 200
        assert result.staker_total == 0

    def test_house_clean_settle_no_forgive(self, repo):
        _seed_stake(
            repo,
            principal=200,
            cut=0.40,
            format=STAKE_FORMAT_HOUSE,
            staker_kind=STAKER_KIND_HOUSE,
            staker_id=None,
        )
        result = settle_stake_on_leave(
            "stk-1",
            400,
            stake_repo=repo,
            now=SETTLE_TIME,
        )
        # Math: net_winnings = 200, staker_cut = 80, staker_total = 280.
        assert result.new_status == STAKE_STATUS_SETTLED
        assert result.staker_total == 280
        assert result.borrower_total == 120
        assert result.forgiven_amount == 0
        assert result.carry_amount == 0


class TestIdempotency:
    def test_missing_stake_returns_none(self, repo):
        result = settle_stake_on_leave(
            "ghost",
            100,
            stake_repo=repo,
            now=SETTLE_TIME,
        )
        assert result is None

    def test_already_settled_returns_none(self, repo):
        _seed_stake(repo, status=STAKE_STATUS_SETTLED)
        # The stake repo will load a row with status='settled' even
        # though we seeded it as such; this guard prevents re-settle.
        result = settle_stake_on_leave(
            "stk-1",
            1000,
            stake_repo=repo,
            now=SETTLE_TIME,
        )
        assert result is None

    def test_already_carrying_returns_none(self, repo):
        _seed_stake(repo, status=STAKE_STATUS_CARRY)
        result = settle_stake_on_leave(
            "stk-1",
            1000,
            stake_repo=repo,
            now=SETTLE_TIME,
        )
        assert result is None

    def test_defaulted_returns_none(self, repo):
        _seed_stake(repo, status=STAKE_STATUS_DEFAULTED)
        result = settle_stake_on_leave(
            "stk-1",
            1000,
            stake_repo=repo,
            now=SETTLE_TIME,
        )
        assert result is None
