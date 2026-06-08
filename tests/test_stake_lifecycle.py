"""Tests for `cash_mode.stake_lifecycle` — the aspiration grubstake funding fix.

The bug (prod drift investigated 2026-06-08, ~ −1.3M in one sandbox): the
aspiration climb funded the STAKER's own seat while settlement drained the
CLIMBER's seat, so the climber's seat was drained for a principal it never
received → minted chips. These tests pin that `fund_climb_stake` credits the
CLIMBER's seat — the seat settlement drains — and that `unwind_climb_funding`
fully reverses it.

See docs/plans/CASH_MODE_STAKE_STATE_MACHINE.md.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

from cash_mode.bankroll import AIBankrollState
from cash_mode.stake_lifecycle import fund_climb_stake, unwind_climb_funding
from core.economy import ledger as L
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager

SB = "sbx_stake_lifecycle"
NOW = datetime(2026, 6, 8, 12, 0, 0)
STAKER = "the_staker"
CLIMBER = "the_climber"
PRINCIPAL = 8000


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "stake_lifecycle.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def repos(db_path):
    br = BankrollRepository(db_path)
    lr = ChipLedgerRepository(db_path)
    # last_regen_tick == NOW so projection is a no-op (no regen noise).
    br.save_ai_bankroll(
        AIBankrollState(personality_id=STAKER, chips=100_000, last_regen_tick=NOW),
        sandbox_id=SB,
    )
    br.save_ai_bankroll(
        AIBankrollState(personality_id=CLIMBER, chips=5_000, last_regen_tick=NOW),
        sandbox_id=SB,
    )
    yield br, lr
    br.close()
    lr.close()


@pytest.fixture
def custody_on():
    with patch("cash_mode.economy_flags.CHIP_CUSTODY_ENABLED", True):
        yield


def test_fund_climb_credits_climber_seat_not_staker_seat(repos, custody_on):
    br, lr = repos
    result = fund_climb_stake(
        staker_id=STAKER,
        climber_id=CLIMBER,
        principal=PRINCIPAL,
        stake_id="s1",
        bankroll_repo=br,
        chip_ledger_repo=lr,
        sandbox_id=SB,
        now=NOW,
    )
    assert result is not None
    # Staker bankroll int debited by the principal.
    assert br.load_ai_bankroll(STAKER, sandbox_id=SB).chips == 100_000 - PRINCIPAL
    # THE FIX: the principal landed on the CLIMBER's seat...
    assert lr.balance_of(L.ai_seat(SB, CLIMBER), sandbox_id=SB) == PRINCIPAL
    # ...and NOT on the staker's own seat (the historical bug).
    assert lr.balance_of(L.ai_seat(SB, STAKER), sandbox_id=SB) == 0
    # Ledger-derived staker balance dropped by exactly the principal.
    assert lr.balance_of(L.ai(STAKER), sandbox_id=SB) == -PRINCIPAL


def test_fund_climb_refuses_when_staker_insolvent(repos, custody_on):
    br, lr = repos
    result = fund_climb_stake(
        staker_id=STAKER,
        climber_id=CLIMBER,
        principal=1_000_000,  # far more than the staker holds
        stake_id="s2",
        bankroll_repo=br,
        chip_ledger_repo=lr,
        sandbox_id=SB,
        now=NOW,
    )
    assert result is None
    # Nothing moved: no debit, no seat credit.
    assert br.load_ai_bankroll(STAKER, sandbox_id=SB).chips == 100_000
    assert lr.balance_of(L.ai_seat(SB, CLIMBER), sandbox_id=SB) == 0


def test_unwind_reverses_funding_completely(repos, custody_on):
    br, lr = repos
    debited = fund_climb_stake(
        staker_id=STAKER,
        climber_id=CLIMBER,
        principal=PRINCIPAL,
        stake_id="s3",
        bankroll_repo=br,
        chip_ledger_repo=lr,
        sandbox_id=SB,
        now=NOW,
    )
    assert debited is not None
    unwind_climb_funding(
        staker_id=STAKER,
        climber_id=CLIMBER,
        principal=PRINCIPAL,
        stake_id="s3",
        debited=debited,
        bankroll_repo=br,
        chip_ledger_repo=lr,
        sandbox_id=SB,
    )
    # Staker made whole; climber seat back to zero — no orphaned credit.
    assert br.load_ai_bankroll(STAKER, sandbox_id=SB).chips == 100_000
    assert lr.balance_of(L.ai_seat(SB, CLIMBER), sandbox_id=SB) == 0
    assert lr.balance_of(L.ai(STAKER), sandbox_id=SB) == 0
