"""Phase E — audit_ledger_completeness reconcile.

Proves the reconcile re-aligns a drifted ledger-derived bankroll with its
authoritative stored int by parking the delta in the `reconciliation` suspense
account, is idempotent, handles both drift directions, and is conservation-safe
(a bank-neutral transfer — the suspense balance just mirrors the net drift).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from cash_mode.bankroll import AIBankrollState, PlayerBankrollState
from cash_mode.ledger_reconcile import reconcile_ledger_completeness
from core.economy import ledger as L
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager

SB = "rec-sb"
PID = "napoleon"
OID = "guest_jeff"
NOW = datetime(2026, 6, 4, 12, 0, 0)


@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / "rec.db")
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


def _seed_with_drift(bankroll_repo, *, seed, final):
    # First write seeds the ledger (stored == derived == seed); the second write
    # bumps the stored int WITHOUT a ledger repo → derived stays at `seed`,
    # stored becomes `final` → drift of (final - seed).
    bankroll_repo.save_ai_bankroll(
        AIBankrollState(PID, seed, NOW),
        sandbox_id=SB,
        chip_ledger_repo=bankroll_repo.chip_ledger_repo,
    )
    bankroll_repo.save_ai_bankroll(AIBankrollState(PID, final, NOW), sandbox_id=SB)


def test_reconcile_aligns_positive_drift(bankroll_repo, ledger_repo):
    _seed_with_drift(bankroll_repo, seed=100, final=150)  # +50 drift
    assert ledger_repo.balance_of(L.ai(PID), sandbox_id=SB) == 100

    dry = reconcile_ledger_completeness(bankroll_repo=bankroll_repo, ledger_repo=ledger_repo)
    assert dry.ai_adjusted == 1 and dry.total_abs_drift == 50 and dry.net_drift == 50
    assert not dry.applied
    assert ledger_repo.balance_of(L.ai(PID), sandbox_id=SB) == 100  # dry run wrote nothing

    applied = reconcile_ledger_completeness(
        bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, apply=True
    )
    assert applied.ai_adjusted == 1
    # Derived now equals stored; the suspense account holds the negative mirror.
    assert ledger_repo.balance_of(L.ai(PID), sandbox_id=SB) == 150
    assert ledger_repo.balance_of(L.reconciliation(), sandbox_id=SB) == -50


def test_reconcile_aligns_negative_drift(bankroll_repo, ledger_repo):
    _seed_with_drift(bankroll_repo, seed=100, final=60)  # -40 drift
    reconcile_ledger_completeness(bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, apply=True)
    assert ledger_repo.balance_of(L.ai(PID), sandbox_id=SB) == 60
    assert ledger_repo.balance_of(L.reconciliation(), sandbox_id=SB) == 40


def test_reconcile_idempotent(bankroll_repo, ledger_repo):
    _seed_with_drift(bankroll_repo, seed=100, final=150)
    reconcile_ledger_completeness(bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, apply=True)
    again = reconcile_ledger_completeness(
        bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, apply=True
    )
    assert again.ai_adjusted == 0 and again.total_abs_drift == 0


def test_reconcile_player_drift(bankroll_repo, ledger_repo):
    # save_player_bankroll writes no seed row → stored=100, derived=0 → +100 drift.
    bankroll_repo.save_player_bankroll(
        PlayerBankrollState(player_id=OID, chips=100, starting_bankroll=100)
    )
    applied = reconcile_ledger_completeness(
        bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, apply=True
    )
    assert applied.player_adjusted == 1
    assert L.derive_player_balance(ledger_repo, owner_id=OID) == 100


def test_no_drift_is_noop(bankroll_repo, ledger_repo):
    bankroll_repo.save_ai_bankroll(
        AIBankrollState(PID, 100, NOW), sandbox_id=SB, chip_ledger_repo=ledger_repo
    )
    report = reconcile_ledger_completeness(
        bankroll_repo=bankroll_repo, ledger_repo=ledger_repo, apply=True
    )
    assert report.ai_adjusted == 0
    assert ledger_repo.balance_of(L.reconciliation(), sandbox_id=SB) == 0
