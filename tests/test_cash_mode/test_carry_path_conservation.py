"""PRH-17 — conservation regression for the runaway-debt / carry path.

The carry-resolution path (bust → carry → partial/full payoff → default) is
believed to conserve chips, but had no dedicated drift==0 test. This drives the
real helpers (`try_ai_voluntary_payoff`, `try_ai_explicit_default`) against a
seeded universe and asserts the chip-ledger audit `drift` stays 0 across each
step — a payoff is a balanced bankroll→bankroll transfer (no mint/burn) and a
default writes off a debt without moving chips, so any unbalanced or unledgered
move would surface as non-zero drift.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from cash_mode.ai_carry_resolution import (
    PAYOFF_EVENT_BASE_RATE,
    try_ai_explicit_default,
    try_ai_voluntary_payoff,
)
from cash_mode.bankroll import AIBankrollState
from cash_mode.stakes import (
    BORROWER_KIND_PERSONALITY,
    STAKE_FORMAT_PURE,
    STAKE_STATUS_CARRY,
    STAKE_STATUS_DEFAULTED,
    STAKE_STATUS_SETTLED,
    STAKER_KIND_PERSONALITY,
    Stake,
)
from flask_app.services.chip_ledger_audit import compute_audit
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.cash_table_repository import CashTableRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.stake_repository import StakeRepository

ANCHOR = datetime(2026, 5, 21, 12, 0, 0)
SBX = "test-sandbox-1"


class _AlwaysLowRng:
    def random(self):
        return 0.0


def _rel(*, likability=0.5, respect=0.5, heat=0.0):
    repo = MagicMock()
    repo.load_relationship_state.return_value = MagicMock(
        likability=likability, respect=respect, heat=heat
    )
    return repo


@pytest.fixture
def repos(tmp_path):
    db = str(tmp_path / "carry_conservation.db")
    SchemaManager(db).ensure_schema()
    return {
        "db": db,
        "bankroll": BankrollRepository(db),
        "stake": StakeRepository(db),
        "ledger": ChipLedgerRepository(db),
        "cash_table": CashTableRepository(db),
    }


def _seed_ai(repos, pid, chips, last_regen_tick):
    """Seed an AI bankroll AND its ai_seed ledger entry (first write fires it
    when chip_ledger_repo is passed), so the audit universe starts balanced."""
    repos["bankroll"].save_ai_bankroll(
        AIBankrollState(personality_id=pid, chips=chips, last_regen_tick=last_regen_tick),
        sandbox_id=SBX,
        chip_ledger_repo=repos["ledger"],
    )


def _carry_stake(repos, *, stake_id, carry_amount, created_at):
    repos["stake"].create_stake(
        Stake(
            stake_id=stake_id,
            session_id=f"ai_session_{stake_id}",
            staker_id="staker",
            staker_kind=STAKER_KIND_PERSONALITY,
            borrower_id="borrower",
            borrower_kind=BORROWER_KIND_PERSONALITY,
            format=STAKE_FORMAT_PURE,
            principal=carry_amount,
            match_amount=0,
            origination_fee=0,
            cut=0.30,
            status=STAKE_STATUS_CARRY,
            carry_amount=carry_amount,
            stake_tier="$10",
            created_at=created_at,
        )
    )


def _drift(repos, now):
    data = compute_audit(
        ledger_repo=repos["ledger"],
        bankroll_repo=repos["bankroll"],
        cash_table_repo=repos["cash_table"],
        stake_repo=repos["stake"],
        db_path=repos["db"],
        now=now,
        sandbox_id=SBX,
    )
    return data["drift"]


def test_payoff_conserves_chips(repos):
    """A voluntary payoff is a balanced borrower→staker transfer — drift==0
    before and after, and the chip totals just move between the two AIs."""
    now = ANCHOR + timedelta(days=20)  # carry old enough that payoff fires
    _seed_ai(repos, "borrower", 10_000, now)
    _seed_ai(repos, "staker", 5_000, now)
    _carry_stake(repos, stake_id="carry_pay", carry_amount=400, created_at=ANCHOR)

    assert _drift(repos, now) == 0  # seeded universe balances

    carries = repos["stake"].list_carries_for_borrower("borrower", BORROWER_KIND_PERSONALITY)
    result = try_ai_voluntary_payoff(
        personality_id="borrower",
        carries=carries,
        bankroll_repo=repos["bankroll"],
        stake_repo=repos["stake"],
        relationship_repo=_rel(heat=0.9),  # angry staker → payoff fires
        chip_ledger_repo=repos["ledger"],
        sandbox_id=SBX,
        rng=_AlwaysLowRng(),
        now=now,
        base_rate=PAYOFF_EVENT_BASE_RATE,
    )
    assert result is not None and result.kind == "payoff" and result.amount == 400

    # Conservation: the 400 moved borrower → staker, nothing minted/burned.
    assert repos["bankroll"].load_ai_bankroll("borrower", sandbox_id=SBX).chips == 9_600
    assert repos["bankroll"].load_ai_bankroll("staker", sandbox_id=SBX).chips == 5_400
    assert repos["stake"].load_stake("carry_pay").status == STAKE_STATUS_SETTLED
    assert _drift(repos, now) == 0


def test_default_conserves_chips(repos):
    """An explicit default writes off the debt without moving chips — drift==0
    before and after, and neither bankroll changes."""
    now = ANCHOR
    # Borrower drowning (bankroll << carries) so the default pressure fires.
    _seed_ai(repos, "borrower", 100, now)
    _seed_ai(repos, "staker", 5_000, now)
    _carry_stake(repos, stake_id="carry_def", carry_amount=400, created_at=ANCHOR)

    assert _drift(repos, now) == 0

    carries = repos["stake"].list_carries_for_borrower("borrower", BORROWER_KIND_PERSONALITY)
    result = try_ai_explicit_default(
        personality_id="borrower",
        carries=carries,
        bankroll_repo=repos["bankroll"],
        stake_repo=repos["stake"],
        relationship_repo=_rel(likability=0.0, respect=-0.5, heat=0.5),
        sandbox_id=SBX,
        energy_lookup=lambda pid: 0.1,  # very tired → defaults
        rng=_AlwaysLowRng(),
        now=now,
    )
    assert result is not None and result.kind == "default"

    # Default moves no chips: the debt is written off, bankrolls untouched.
    assert repos["bankroll"].load_ai_bankroll("borrower", sandbox_id=SBX).chips == 100
    assert repos["bankroll"].load_ai_bankroll("staker", sandbox_id=SBX).chips == 5_000
    assert repos["stake"].load_stake("carry_def").status == STAKE_STATUS_DEFAULTED
    assert _drift(repos, now) == 0
