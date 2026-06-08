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
from cash_mode.stake_lifecycle import fund_climb_stake, unwind_climb_funding
from core.economy import ledger as L
from core.economy.ledger import (
    OBLIGATION_REASONS,
    TRANSFER_REASONS,
    _record_obligation,
    record_stake_cancel,
    record_stake_extinguish,
    record_stake_forgive,
    record_stake_originate,
)
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager

pytestmark = pytest.mark.integration


# --- pure flow emitters (functional core — no DB) --------------------------


def test_flows_on_originate_is_principal_only():
    from cash_mode.stake_obligations import OP_ORIGINATE, flows_on_originate

    flows = flows_on_originate("s", 8000)
    assert flows == [type(flows[0])(OP_ORIGINATE, "s", 8000)]
    assert flows_on_originate("s", 0) == []  # nothing owed


def test_flows_on_settle_clean_win_extinguishes_full_principal():
    from cash_mode.stake_obligations import OP_EXTINGUISH, flows_on_settle, net_principal_delta

    # Win: staker_total 11600 > principal 8000 → recover full principal, the
    # profit is NOT in the obligation. Originate(+8000) then this nets to 0.
    flows = flows_on_settle("s", principal=8000, staker_total=11600, is_carry=False)
    assert [(f.op, f.amount) for f in flows] == [(OP_EXTINGUISH, 8000)]
    assert net_principal_delta(flows_on_originate_list() + flows) == 0


def flows_on_originate_list():
    from cash_mode.stake_obligations import flows_on_originate

    return flows_on_originate("s", 8000)


def test_flows_on_settle_carry_leaves_residual():
    from cash_mode.stake_obligations import OP_EXTINGUISH, flows_on_settle, net_principal_delta

    # Loss to 3000: recover 3000, 5000 carries (NOT forgiven). Originate then
    # these net to the +5000 residual that rolls forward.
    flows = flows_on_settle("s", principal=8000, staker_total=3000, is_carry=True)
    assert [(f.op, f.amount) for f in flows] == [(OP_EXTINGUISH, 3000)]
    assert net_principal_delta(flows_on_originate_list() + flows) == 5000


def test_flows_on_settle_default_forgives_residual():
    from cash_mode.stake_obligations import (
        OP_EXTINGUISH,
        OP_FORGIVE,
        flows_on_settle,
        net_principal_delta,
    )

    # Default (not carry) with partial recovery: extinguish 2000 + forgive 6000.
    # Originate then these net to 0 — the debt fully closes.
    flows = flows_on_settle("s", principal=8000, staker_total=2000, is_carry=False)
    assert [(f.op, f.amount) for f in flows] == [(OP_EXTINGUISH, 2000), (OP_FORGIVE, 6000)]
    assert net_principal_delta(flows_on_originate_list() + flows) == 0


def test_flows_on_cancel_reverses_origination():
    from cash_mode.stake_obligations import flows_on_cancel, flows_on_originate, net_principal_delta

    assert net_principal_delta(flows_on_originate("s", 8000) + flows_on_cancel("s", 8000)) == 0


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


def test_cancel_reverses_originate_exactly(lr):
    # A rolled-back stake: cancel is the exact inverse of originate (debt → 0,
    # genesis made whole), distinct from forgive (no bad-debt contra touched).
    record_stake_originate(lr, stake_id=SID, principal=PRINCIPAL, sandbox_id=SB)
    record_stake_cancel(lr, stake_id=SID, principal=PRINCIPAL, sandbox_id=SB)
    assert lr.balance_of(L.oblig(SID), sandbox_id=SB) == 0
    assert lr.balance_of(L.oblig_genesis(), sandbox_id=SB) == 0
    assert lr.balance_of(L.oblig_forgiven(), sandbox_id=SB) == 0


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


def test_unwind_cancels_the_originated_debt(fund_repos):
    # If the stake-row write fails after funding, unwind_climb_funding must
    # reverse the obligation too — else oblig:<id> orphans at the full principal
    # for a stake that never existed (the codex-flagged rollback gap).
    br, lr = fund_repos
    with patch("cash_mode.economy_flags.CHIP_CUSTODY_ENABLED", True):
        debited = fund_climb_stake(
            staker_id="the_staker",
            climber_id="the_climber",
            principal=PRINCIPAL,
            stake_id=SID,
            bankroll_repo=br,
            chip_ledger_repo=lr,
            sandbox_id=SB,
            now=NOW,
        )
        assert lr.balance_of(L.oblig(SID), sandbox_id=SB) == PRINCIPAL
        unwind_climb_funding(
            staker_id="the_staker",
            climber_id="the_climber",
            principal=PRINCIPAL,
            stake_id=SID,
            debited=debited,
            bankroll_repo=br,
            chip_ledger_repo=lr,
            sandbox_id=SB,
        )
    # Debt reversed to zero; staker made whole; climber seat back to zero.
    assert lr.balance_of(L.oblig(SID), sandbox_id=SB) == 0
    assert br.load_ai_bankroll("the_staker", sandbox_id=SB).chips == 100_000
    assert lr.balance_of(L.ai_seat(SB, "the_climber"), sandbox_id=SB) == 0


# --- settle-side wiring: full originate -> settle lifecycle ----------------


@pytest.fixture
def settle_repos(db_path):
    from poker.repositories.stake_repository import StakeRepository

    SchemaManager(db_path).ensure_schema()
    br = BankrollRepository(db_path)
    lr = ChipLedgerRepository(db_path)
    sr = StakeRepository(db_path)
    for pid in ("the_staker", "the_borrower"):
        br.save_ai_bankroll(
            AIBankrollState(personality_id=pid, chips=100_000, last_regen_tick=NOW),
            sandbox_id=SB,
        )
    yield br, lr, sr
    br.close()
    lr.close()
    sr.close()


def _make_active_stake(sr, stake_id, *, principal, cut):
    from cash_mode.stakes import (
        BORROWER_KIND_PERSONALITY,
        STAKE_FORMAT_PURE,
        STAKE_STATUS_ACTIVE,
        STAKER_KIND_PERSONALITY,
        Stake,
    )

    sr.create_stake(
        Stake(
            stake_id=stake_id,
            session_id=f"sess_{stake_id}",
            staker_id="the_staker",
            staker_kind=STAKER_KIND_PERSONALITY,
            borrower_id="the_borrower",
            borrower_kind=BORROWER_KIND_PERSONALITY,
            format=STAKE_FORMAT_PURE,
            principal=principal,
            match_amount=0,
            origination_fee=0,
            cut=cut,
            status=STAKE_STATUS_ACTIVE,
            carry_amount=0,
            stake_tier="$2",
            created_at=NOW,
        )
    )


def _settle(br, lr, sr, pid, chips_at_leave):
    from cash_mode.lobby import settle_departed_ai_stake

    with patch("cash_mode.economy_flags.CHIP_CUSTODY_ENABLED", True):
        return settle_departed_ai_stake(
            pid,
            chips_at_leave,
            stake_repo=sr,
            bankroll_repo=br,
            chip_ledger_repo=lr,
            relationship_repo=None,
            personality_repo=None,
            table_id="t1",
            sandbox_id=SB,
            now=NOW,
        )


def test_clean_settle_zeroes_the_debt(settle_repos):
    br, lr, sr = settle_repos
    _make_active_stake(sr, SID, principal=8000, cut=0.3)
    record_stake_originate(lr, stake_id=SID, principal=8000, sandbox_id=SB)
    assert lr.balance_of(L.oblig(SID), sandbox_id=SB) == 8000
    # Borrower won: leaves with 20000. Staker recovers full principal → debt 0.
    _settle(br, lr, sr, "the_borrower", chips_at_leave=20000)
    assert lr.balance_of(L.oblig(SID), sandbox_id=SB) == 0


def test_loss_settle_carries_unrecovered_principal(settle_repos):
    br, lr, sr = settle_repos
    _make_active_stake(sr, SID, principal=8000, cut=0.3)
    record_stake_originate(lr, stake_id=SID, principal=8000, sandbox_id=SB)
    # Borrower lost down to 3000: staker recovers 3000, 5000 unrecovered → carry.
    settlement = _settle(br, lr, sr, "the_borrower", chips_at_leave=3000)
    assert settlement is not None and settlement.carry_amount == 5000
    # The debt account holds exactly the carried (unrecovered) principal.
    assert lr.balance_of(L.oblig(SID), sandbox_id=SB) == 5000
