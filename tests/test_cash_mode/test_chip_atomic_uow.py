"""Chip-custody atomic-write unit-of-work (T3-82 Tier-2, Phase A+B).

Proves the int↔ledger split-commit divergence window is closed for the two
hottest chokepoints:

  * `BaseRepository.transaction()` commits the bankroll int and its ledger
    row(s) together, and rolls BOTH back on failure (re-entrant).
  * `chip_unit_of_work` yields a real shared connection for real repos and
    `None` (graceful fallback) for test doubles / cross-DB.
  * `debit_bankroll_for_seat` + `credit_ai_cash_out` keep `derive == stored`
    across a seat round-trip — zero fresh divergence (the conservation gate).
"""

from __future__ import annotations

import types
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from cash_mode.bankroll import (
    AIBankrollState,
    chip_unit_of_work,
    credit_ai_cash_out,
    debit_bankroll_for_seat,
)
from core.economy import ledger as L
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager

SB = "uow-sb"
PID = "napoleon"
PID2 = "cleopatra"
NOW = datetime(2026, 6, 4, 12, 0, 0)


@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / "uow.db")
    SchemaManager(p).ensure_schema()
    return p


@pytest.fixture
def ledger_repo(db_path):
    r = ChipLedgerRepository(db_path)
    yield r
    r.close()


@pytest.fixture
def bankroll_repo(db_path, ledger_repo):
    r = BankrollRepository(db_path)
    r.chip_ledger_repo = ledger_repo
    yield r
    r.close()


@pytest.fixture
def custody_on(monkeypatch):
    monkeypatch.setattr("cash_mode.economy_flags.CHIP_CUSTODY_ENABLED", True)


# --- transaction() primitive -------------------------------------------------


def test_transaction_commits_int_and_ledger_together(bankroll_repo, ledger_repo):
    with bankroll_repo.transaction() as c:
        bankroll_repo.save_ai_bankroll(AIBankrollState(PID, 5000, NOW), sandbox_id=SB, conn=c)
        L.record(
            ledger_repo,
            source=L.bank(),
            sink=L.ai(PID),
            amount=5000,
            reason="ai_seed",
            sandbox_id=SB,
            conn=c,
        )
    assert bankroll_repo.load_ai_bankroll(PID, sandbox_id=SB).chips == 5000
    assert ledger_repo.balance_of(L.ai(PID), sandbox_id=SB) == 5000


def test_transaction_rolls_back_both_on_exception(bankroll_repo, ledger_repo):
    with pytest.raises(RuntimeError):
        with bankroll_repo.transaction() as c:
            bankroll_repo.save_ai_bankroll(AIBankrollState(PID, 5000, NOW), sandbox_id=SB, conn=c)
            L.record(
                ledger_repo,
                source=L.bank(),
                sink=L.ai(PID),
                amount=5000,
                reason="ai_seed",
                sandbox_id=SB,
                conn=c,
            )
            raise RuntimeError("boom")  # crash mid-unit-of-work
    # NEITHER the int nor the ledger row survived.
    assert bankroll_repo.load_ai_bankroll(PID, sandbox_id=SB) is None
    assert ledger_repo.balance_of(L.ai(PID), sandbox_id=SB) == 0


def test_transaction_reentrant_outer_rollback_discards_inner(bankroll_repo):
    with pytest.raises(RuntimeError):
        with bankroll_repo.transaction() as c1:
            bankroll_repo.save_ai_bankroll(AIBankrollState(PID, 1, NOW), sandbox_id=SB, conn=c1)
            with bankroll_repo.transaction() as c2:
                assert c2 is c1  # nested joins the same connection
                bankroll_repo.save_ai_bankroll(
                    AIBankrollState(PID2, 2, NOW), sandbox_id=SB, conn=c2
                )
            # inner exit must NOT have committed (depth > 0)
            raise RuntimeError("boom")
    assert bankroll_repo.load_ai_bankroll(PID, sandbox_id=SB) is None
    assert bankroll_repo.load_ai_bankroll(PID2, sandbox_id=SB) is None


# --- chip_unit_of_work fallback ----------------------------------------------


def test_uow_yields_real_conn_for_real_repo(bankroll_repo, ledger_repo):
    with chip_unit_of_work(bankroll_repo, ledger_repo=ledger_repo) as conn:
        assert conn is not None


def test_uow_yields_none_for_test_double(ledger_repo):
    with chip_unit_of_work(MagicMock(), ledger_repo=ledger_repo) as conn:
        assert conn is None


def test_uow_yields_none_cross_db(bankroll_repo):
    other = types.SimpleNamespace(db_path="/somewhere/else.db")
    with chip_unit_of_work(bankroll_repo, ledger_repo=other) as conn:
        assert conn is None


# --- conservation through the real chokepoints -------------------------------


def test_seat_round_trip_keeps_derive_equal_to_stored(bankroll_repo, ledger_repo, custody_on):
    """debit_bankroll_for_seat then credit_ai_cash_out: the ledger-derived
    ai:<pid> balance tracks the stored int exactly (zero fresh divergence)."""
    # Seed via the first-write path (int + ai_seed in one txn).
    bankroll_repo.save_ai_bankroll(
        AIBankrollState(PID, 10_000, NOW), sandbox_id=SB, chip_ledger_repo=ledger_repo
    )

    def stored():
        return bankroll_repo.load_ai_bankroll(PID, sandbox_id=SB).chips

    def derived():
        return ledger_repo.balance_of(L.ai(PID), sandbox_id=SB)

    assert stored() == derived() == 10_000

    debit_bankroll_for_seat(
        bankroll_repo, PID, 3000, sandbox_id=SB, chip_ledger_repo=ledger_repo, now=NOW
    )
    assert stored() == derived()  # int and ledger moved together

    credit_ai_cash_out(
        bankroll_repo, PID, 5000, sandbox_id=SB, chip_ledger_repo=ledger_repo, now=NOW
    )
    assert stored() == derived()  # still aligned after the cash-out
