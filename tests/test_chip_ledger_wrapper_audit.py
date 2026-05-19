"""End-to-end smoke: `core.economy.ledger` wrapper → `compute_audit`.

Existing tests cover the wrapper's validation in isolation and the
audit's math against direct repo inserts. This file wires them
together — every helper writes through the wrapper, then the audit
reads back and the by_reason totals must reflect the inputs exactly.

Catches:
  * Vocabulary drift between LEDGER_REASONS and audit consumers
  * Source/sink format mismatches (e.g. if someone changes the
    `player:<id>` prefix in the wrapper but not the audit reads)
  * Helpers silently rejecting valid inputs (e.g. a future
    tightening that breaks production call sites)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from cash_mode.bankroll import AIBankrollState, PlayerBankrollState
from core.economy import ledger as chip_ledger
from flask_app.services.chip_ledger_audit import compute_audit
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.cash_table_repository import CashTableRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager


@pytest.fixture
def env(tmp_path):
    db_path = str(tmp_path / "wrapper_audit.db")
    SchemaManager(db_path).ensure_schema()

    bankroll_repo = BankrollRepository(db_path)
    cash_table_repo = CashTableRepository(db_path)
    ledger_repo = ChipLedgerRepository(db_path)

    # Pre-seed wipe so we can pin exact ledger contents.
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM chip_ledger_entries")
        conn.commit()

    yield db_path, bankroll_repo, cash_table_repo, ledger_repo

    bankroll_repo.close()
    cash_table_repo.close()
    ledger_repo.close()


def _audit(db_path, bankroll_repo, cash_table_repo, ledger_repo, now):
    return compute_audit(
        ledger_repo=ledger_repo,
        bankroll_repo=bankroll_repo,
        cash_table_repo=cash_table_repo,
        db_path=db_path,
        now=now,
    )


class TestWrapperToAudit:
    def test_player_seed_helper_shows_up_in_audit(self, env):
        db_path, bankroll_repo, cash_table_repo, ledger_repo = env
        chip_ledger.record_player_seed(
            ledger_repo, owner_id='alice', amount=200,
        )
        data = _audit(db_path, bankroll_repo, cash_table_repo, ledger_repo,
                      datetime(2026, 5, 18, 12, 0, 0))
        assert data['by_reason']['player_seed'] == 200
        assert data['ledger_totals']['chips_created'] == 200

    def test_ai_regen_helper_shows_up_in_audit(self, env):
        db_path, bankroll_repo, cash_table_repo, ledger_repo = env
        chip_ledger.record_ai_regen(
            ledger_repo,
            personality_id='zeus',
            stored_chips=1000, projected_chips=1500,
        )
        data = _audit(db_path, bankroll_repo, cash_table_repo, ledger_repo,
                      datetime(2026, 5, 18, 12, 0, 0))
        assert data['by_reason']['ai_regen'] == 500

    def test_cap_clamp_helper_shows_up_in_audit_as_destruction(self, env):
        db_path, bankroll_repo, cash_table_repo, ledger_repo = env
        chip_ledger.record_cap_clamp(
            ledger_repo, personality_id='zeus', overflow=300,
        )
        data = _audit(db_path, bankroll_repo, cash_table_repo, ledger_repo,
                      datetime(2026, 5, 18, 12, 0, 0))
        assert data['by_reason']['cap_clamp'] == -300
        assert data['ledger_totals']['chips_destroyed'] == 300

    def test_house_loan_lifecycle_through_wrapper(self, env):
        """Full anonymous-loan lifecycle: issue + settle + forgive."""
        db_path, bankroll_repo, cash_table_repo, ledger_repo = env
        chip_ledger.record_house_loan_issue(
            ledger_repo, owner_id='alice', amount=200,
        )
        chip_ledger.record_house_loan_settle(
            ledger_repo, owner_id='alice', amount=50,
        )
        chip_ledger.record_forgive_balance(
            ledger_repo, owner_id='alice', forgiven_principal=150,
        )

        data = _audit(db_path, bankroll_repo, cash_table_repo, ledger_repo,
                      datetime(2026, 5, 18, 12, 0, 0))
        assert data['by_reason']['house_loan_issue'] == 200
        assert data['by_reason']['house_loan_settle'] == -50
        # Annotation visible at zero (not eaten by SUM(amount=0)).
        assert data['by_reason']['forgive_balance'] == 0
        # Net created = 200 - 50 = 150 chips still in the universe;
        # the forgive_balance annotation reconciles the remainder.
        assert data['ledger_totals']['outstanding'] == 150

    def test_unknown_reason_rejected_doesnt_pollute_audit(self, env):
        """The wrapper rejects unknown reasons silently. Audit should
        be unaffected — no phantom buckets, no drift."""
        db_path, bankroll_repo, cash_table_repo, ledger_repo = env
        eid = chip_ledger.record(
            ledger_repo,
            source=chip_ledger.bank(),
            sink=chip_ledger.player('alice'),
            amount=100,
            reason='made_up_reason',
        )
        assert eid is None
        data = _audit(db_path, bankroll_repo, cash_table_repo, ledger_repo,
                      datetime(2026, 5, 18, 12, 0, 0))
        assert data['by_reason'] == {}
        assert data['ledger_totals']['outstanding'] == 0

    def test_wrapper_writes_match_audit_drift_against_real_state(self, env):
        """End-to-end with real chip state: seed both ledger and DB
        through the wrapper / repos, audit drift must be zero."""
        db_path, bankroll_repo, cash_table_repo, ledger_repo = env
        now = datetime(2026, 5, 18, 12, 0, 0)

        # State the audit will see.
        bankroll_repo.save_player_bankroll(PlayerBankrollState(
            player_id='alice', chips=200, starting_bankroll=200,
        ))

        # Ledger fired through the wrapper.
        chip_ledger.record_player_seed(
            ledger_repo, owner_id='alice', amount=200,
        )

        data = _audit(db_path, bankroll_repo, cash_table_repo, ledger_repo, now)
        assert data['drift'] == 0
        assert data['actual_totals']['player_bankrolls'] == 200
        assert data['ledger_totals']['chips_created'] == 200
