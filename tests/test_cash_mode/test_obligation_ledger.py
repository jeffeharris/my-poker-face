"""P1 tests for the staking obligation ledger — the debt dimension.

The obligation ledger tracks the PRINCIPAL a borrower owes a staker, in its own
`oblig*` account namespace, separate from chip custody. These tests pin:
  - the three writers move principal correctly (originate / extinguish / forgive);
  - obligation rows can never touch a chip account (namespace guard);
  - **drift isolation** — obligation rows are bank-neutral, so they are invisible
    to the chip creation/destruction sums that drive `compute_audit().drift`;
  - `fund_climb_stake` now writes the originate row atomically with the chip-side
    stake_fund, and that write does not perturb the chip drift sums.

See docs/plans/CASH_MODE_STAKING_OBLIGATION_LEDGER.md.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

from cash_mode.bankroll import AIBankrollState
from cash_mode.stake_lifecycle import fund_climb_stake
from core.economy import ledger as L
from core.economy.ledger import (
    OBLIGATION_REASONS,
    TRANSFER_REASONS,
    _record_obligation,
    record_stake_extinguish,
    record_stake_forgive,
    record_stake_originate,
)
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager

pytestmark = pytest.mark.integration

SB = "sbx_oblig"
NOW = datetime(2026, 6, 8, 12, 0, 0)
SID = "ai_stake_test01"
PRINCIPAL = 8000


@pytest.fixture
def lr(db_path):
    SchemaManager(db_path).ensure_schema()
    repo = ChipLedgerRepository(db_path)
    yield repo
    repo.close()


# --- writers ---------------------------------------------------------------


def test_originate_creates_principal_debt(lr):
    record_stake_originate(lr, stake_id=SID, principal=PRINCIPAL, sandbox_id=SB)
    # Debt lives on the per-stake account.
    assert lr.balance_of(L.oblig(SID), sandbox_id=SB) == PRINCIPAL
    # Sourced from the genesis contra (which goes negative by the same amount).
    assert lr.balance_of(L.oblig_genesis(), sandbox_id=SB) == -PRINCIPAL
    rows = lr.entries_for_stake(SID, sandbox_id=SB)
    assert len(rows) == 1 and rows[0]["reason"] == "stake_originate"
    assert rows[0]["source"] == "oblig_genesis" and rows[0]["sink"] == f"oblig:{SID}"


def test_extinguish_recovers_principal(lr):
    record_stake_originate(lr, stake_id=SID, principal=PRINCIPAL, sandbox_id=SB)
    # Partial recovery (borrower lost some): principal - recovered = carry.
    record_stake_extinguish(lr, stake_id=SID, amount=5000, sandbox_id=SB)
    assert lr.balance_of(L.oblig(SID), sandbox_id=SB) == PRINCIPAL - 5000  # carry = 3000
    # Full recovery zeroes the debt.
    record_stake_extinguish(lr, stake_id=SID, amount=3000, sandbox_id=SB)
    assert lr.balance_of(L.oblig(SID), sandbox_id=SB) == 0


def test_forgive_writes_off_residual(lr):
    record_stake_originate(lr, stake_id=SID, principal=PRINCIPAL, sandbox_id=SB)
    record_stake_extinguish(lr, stake_id=SID, amount=2000, sandbox_id=SB)  # recovered
    record_stake_forgive(lr, stake_id=SID, amount=6000, sandbox_id=SB)  # residual written off
    assert lr.balance_of(L.oblig(SID), sandbox_id=SB) == 0
    # The write-off accumulates in the bad-debt contra.
    assert lr.balance_of(L.oblig_forgiven(), sandbox_id=SB) == 6000


def test_writers_noop_on_nonpositive_or_no_repo(lr):
    assert record_stake_originate(lr, stake_id=SID, principal=0, sandbox_id=SB) is None
    assert record_stake_extinguish(lr, stake_id=SID, amount=-5, sandbox_id=SB) is None
    assert record_stake_forgive(None, stake_id=SID, amount=100, sandbox_id=SB) is None
    assert lr.entries_for_stake(SID, sandbox_id=SB) == []


# --- namespace guard -------------------------------------------------------


def test_obligation_row_cannot_touch_a_chip_account(lr):
    # A debt row that tried to sink to a real seat must be rejected outright —
    # this is the structural barrier that keeps the two axes from crossing.
    result = _record_obligation(
        lr,
        source=L.oblig(SID),
        sink=L.ai_seat(SB, "some_ai"),  # a chip account!
        amount=PRINCIPAL,
        reason="stake_extinguish",
        stake_id=SID,
        sandbox_id=SB,
    )
    assert result is None
    assert lr.entries_for_stake(SID, sandbox_id=SB) == []
    # And a non-obligation reason on an oblig row is rejected too.
    assert (
        _record_obligation(
            lr,
            source=L.oblig_genesis(),
            sink=L.oblig(SID),
            amount=PRINCIPAL,
            reason="stake_fund",  # a chip reason
            stake_id=SID,
            sandbox_id=SB,
        )
        is None
    )


def test_obligation_reasons_are_transfers_not_creations(lr):
    # Obligation reasons must be transfer reasons (bank-neutral), so record()
    # would reject them and record_transfer accepts them. This is what keeps
    # them out of the central_bank-filtered drift sums.
    assert OBLIGATION_REASONS <= TRANSFER_REASONS


# --- drift isolation (the P1 keystone) ------------------------------------


def test_obligation_rows_invisible_to_chip_drift_sums(lr):
    # A real creation (central_bank → ai) so the drift sums are non-empty.
    lr.record(source=L.bank(), sink=L.ai("seed_ai"), amount=10000, reason="ai_seed", sandbox_id=SB)
    creations_before = lr.sum_creations_by_reason(sandbox_id=SB)
    destructions_before = lr.sum_destructions_by_reason(sandbox_id=SB)

    # A full obligation lifecycle.
    record_stake_originate(lr, stake_id=SID, principal=PRINCIPAL, sandbox_id=SB)
    record_stake_extinguish(lr, stake_id=SID, amount=PRINCIPAL, sandbox_id=SB)

    # The bank-filtered drift inputs are byte-for-byte unchanged: no obligation
    # reason appears, and the existing creation totals don't move.
    assert lr.sum_creations_by_reason(sandbox_id=SB) == creations_before
    assert lr.sum_destructions_by_reason(sandbox_id=SB) == destructions_before
    for reason in OBLIGATION_REASONS:
        assert reason not in lr.sum_creations_by_reason(sandbox_id=SB)
        assert reason not in lr.sum_destructions_by_reason(sandbox_id=SB)
    # Chip account balances are untouched by the debt axis.
    assert lr.balance_of(L.ai("seed_ai"), sandbox_id=SB) == 10000


# --- fund_climb_stake writes the originate row atomically ------------------


@pytest.fixture
def fund_repos(db_path):
    SchemaManager(db_path).ensure_schema()
    br = BankrollRepository(db_path)
    lr = ChipLedgerRepository(db_path)
    br.save_ai_bankroll(
        AIBankrollState(personality_id="the_staker", chips=100_000, last_regen_tick=NOW),
        sandbox_id=SB,
    )
    yield br, lr
    br.close()
    lr.close()


def test_fund_climb_stake_writes_originate_and_keeps_drift_isolated(fund_repos):
    br, lr = fund_repos
    with patch("cash_mode.economy_flags.CHIP_CUSTODY_ENABLED", True):
        fund_climb_stake(
            staker_id="the_staker",
            climber_id="the_climber",
            principal=PRINCIPAL,
            stake_id=SID,
            bankroll_repo=br,
            chip_ledger_repo=lr,
            sandbox_id=SB,
            now=NOW,
        )
    # Chip side (PR #235): principal on the CLIMBER's seat, staker debited.
    assert lr.balance_of(L.ai_seat(SB, "the_climber"), sandbox_id=SB) == PRINCIPAL
    # Obligation side (P1): the debt is born on the same stake_id.
    assert lr.balance_of(L.oblig(SID), sandbox_id=SB) == PRINCIPAL
    # The debt row didn't leak into the chip drift sums.
    for reason in OBLIGATION_REASONS:
        assert reason not in lr.sum_creations_by_reason(sandbox_id=SB)
    # entries_for_stake sees both the chip funding and the obligation rows.
    reasons = {r["reason"] for r in lr.entries_for_stake(SID, sandbox_id=SB)}
    assert "stake_fund" in reasons and "stake_originate" in reasons
