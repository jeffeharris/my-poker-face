"""Tests for schema migration v94 — pre_ledger_universe seed.

Covers: the seed writes one entry per chip-bearing location with
matching amounts, idempotency on re-run, drift goes to zero after
seed, and personality loans are correctly excluded (the AI lender's
bankroll seed already covers those chips).
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from cash_mode.bankroll import AIBankrollState, PlayerBankrollState
from cash_mode.tables import CashTableState, ai_slot, open_slot
from flask_app.services.chip_ledger_audit import compute_audit
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.cash_table_repository import CashTableRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager, SCHEMA_VERSION


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "v94.db")


@pytest.fixture
def seeded_db(db_path):
    """A DB at v93 state with chip-bearing rows but no ledger entries yet.

    Strategy: build the schema up through v93 only, write the
    bankroll/table/loan rows, then run v94 in the test and assert.
    Achieved by setting SCHEMA_VERSION temporarily — but simpler: run
    ensure_schema (lands at SCHEMA_VERSION, including v94 seed), then
    wipe the ledger to simulate a "before v94" state, then run v94.
    """
    SchemaManager(db_path).ensure_schema()

    # Wipe any v94 seed that ran during ensure_schema.
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM chip_ledger_entries")
        conn.commit()

    # Now seed chip-bearing surfaces with known values.
    bankroll_repo = BankrollRepository(db_path)
    cash_table_repo = CashTableRepository(db_path)
    anchor = datetime(2026, 5, 18, 12, 0, 0)

    bankroll_repo.save_player_bankroll(PlayerBankrollState(
        player_id='alice', chips=500, starting_bankroll=200,
    ))
    # bob has an active anonymous house loan; carol has a personality
    # loan. The v94 migration reads `active_loan_*` directly from SQL,
    # so we seed those columns via raw SQL — `PlayerBankrollState` no
    # longer carries the fields after Cleanup B. The columns themselves
    # disappear in v99 (Cleanup C); at that point this whole migration
    # test goes too since v94 only matters on DBs that lived through
    # v89-v98.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE player_bankroll_state "
            "SET active_loan_amount=300, active_loan_floor=1.0, "
            "active_loan_rate=0.0, active_loan_lender_id=NULL "
            "WHERE player_id='bob'",
        )
        # bob's row doesn't exist yet — insert with loan columns.
        conn.execute(
            "INSERT OR REPLACE INTO player_bankroll_state "
            "(player_id, chips, starting_bankroll, active_loan_amount, "
            "active_loan_floor, active_loan_rate, active_loan_lender_id) "
            "VALUES ('bob', 0, 200, 300, 1.0, 0.0, NULL)",
        )
        conn.execute(
            "INSERT OR REPLACE INTO player_bankroll_state "
            "(player_id, chips, starting_bankroll, active_loan_amount, "
            "active_loan_floor, active_loan_rate, active_loan_lender_id) "
            "VALUES ('carol', 100, 200, 400, 1.0, 0.0, 'zeus')",
        )
        conn.commit()

    bankroll_repo.save_ai_bankroll(AIBankrollState(
        personality_id='zeus', chips=3_000, last_regen_tick=anchor,
    ))
    bankroll_repo.save_ai_bankroll(AIBankrollState(
        personality_id='hera', chips=1_000, last_regen_tick=anchor,
    ))

    cash_table_repo.save_table(CashTableState(
        table_id='cash-table-2-001',
        stake_label='$2',
        seats=[
            ai_slot('zeus', 150),
            ai_slot('hera', 250),
            open_slot(),
            open_slot(),
            open_slot(),
            open_slot(),
        ],
        created_at=anchor,
        last_activity_at=anchor,
    ))

    bankroll_repo.close()
    cash_table_repo.close()
    return db_path, anchor


def _apply_v94(db_path: str) -> int:
    """Run the v94 migration; return count of pre_ledger_universe rows."""
    with sqlite3.connect(db_path) as conn:
        sm = SchemaManager.__new__(SchemaManager)
        sm._migrate_v94_seed_pre_ledger_universe(conn)
        conn.commit()
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM chip_ledger_entries "
            "WHERE reason='pre_ledger_universe'"
        ).fetchone()[0]


class TestV94Seed:
    def test_schema_version_bumped(self, db_path):
        SchemaManager(db_path).ensure_schema()
        with sqlite3.connect(db_path) as conn:
            assert SCHEMA_VERSION >= 94
            version = conn.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()[0]
            assert version >= 94

    def test_seeds_player_bankrolls(self, seeded_db):
        db_path, _ = seeded_db
        _apply_v94(db_path)
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT sink, amount FROM chip_ledger_entries "
                "WHERE reason='pre_ledger_universe' "
                "AND json_extract(context_json, '$.kind') = 'player_bankroll'"
            ).fetchall()
        sinks = {r['sink']: r['amount'] for r in rows}
        assert sinks.get('player:alice') == 500
        # bob has chips=0 → no bankroll entry (only the loan principal entry).
        assert 'player:bob' not in sinks
        assert sinks.get('player:carol') == 100

    def test_seeds_ai_bankrolls_using_stored(self, seeded_db):
        db_path, _ = seeded_db
        _apply_v94(db_path)
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT sink, amount FROM chip_ledger_entries "
                "WHERE reason='pre_ledger_universe' AND sink LIKE 'ai:%' "
                "AND json_extract(context_json, '$.kind') = 'ai_bankroll'"
            ).fetchall()
        sinks = {sink: amount for sink, amount in rows}
        assert sinks.get('ai:zeus') == 3_000
        assert sinks.get('ai:hera') == 1_000

    def test_seeds_cash_table_ai_seats(self, seeded_db):
        db_path, _ = seeded_db
        _apply_v94(db_path)
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT sink, amount FROM chip_ledger_entries "
                "WHERE reason='pre_ledger_universe' "
                "AND json_extract(context_json, '$.kind') = 'cash_table_seat'"
            ).fetchall()
        sinks = {sink: amount for sink, amount in rows}
        assert sinks.get('ai:zeus') == 150
        assert sinks.get('ai:hera') == 250

    def test_loans_seeded_with_kind_tag(self, seeded_db):
        """Both anonymous (house) and named (personality) loans get a
        pre_ledger_universe entry. The audit sums active_loans_principal
        across all rows with active_loan_amount > 0, so the seed has to
        match — otherwise drift on a session with a personality loan
        would always start non-zero. The `kind` field in context_json
        differentiates them for downstream analysis."""
        db_path, _ = seeded_db
        _apply_v94(db_path)
        with sqlite3.connect(db_path) as conn:
            house = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM chip_ledger_entries "
                "WHERE reason='pre_ledger_universe' "
                "AND json_extract(context_json, '$.kind') = 'house_loan_principal'"
            ).fetchone()[0]
            personality = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM chip_ledger_entries "
                "WHERE reason='pre_ledger_universe' "
                "AND json_extract(context_json, '$.kind') = 'personality_loan_principal'"
            ).fetchone()[0]
        assert house == 300  # bob's anonymous loan
        assert personality == 400  # carol's loan from zeus

    def test_idempotent_on_rerun(self, seeded_db):
        db_path, _ = seeded_db
        first = _apply_v94(db_path)
        second = _apply_v94(db_path)
        assert first > 0
        assert second == first  # no new rows on second run

    def test_drift_zero_after_seed(self, seeded_db):
        """The point of v94: post-seed, audit drift = 0 (modulo the
        live_session_ai_stacks term, which is empty in this test)."""
        db_path, anchor = seeded_db
        _apply_v94(db_path)

        bankroll_repo = BankrollRepository(db_path)
        cash_table_repo = CashTableRepository(db_path)
        ledger_repo = ChipLedgerRepository(db_path)
        try:
            data = compute_audit(
                ledger_repo=ledger_repo,
                bankroll_repo=bankroll_repo,
                cash_table_repo=cash_table_repo,
                db_path=db_path,
                now=anchor,
            )
        finally:
            bankroll_repo.close()
            cash_table_repo.close()
            ledger_repo.close()

        assert data['drift'] == 0, (
            f"post-seed drift = {data['drift']}\n"
            f"ledger: {data['ledger_totals']}\n"
            f"actual: {data['actual_totals']}"
        )

    def test_fresh_db_seeds_via_ensure_schema(self, db_path):
        """A new DB with no chip-bearing rows still runs v94 cleanly
        (no rows to seed) and lands at SCHEMA_VERSION."""
        SchemaManager(db_path).ensure_schema()
        with sqlite3.connect(db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM chip_ledger_entries "
                "WHERE reason='pre_ledger_universe'"
            ).fetchone()[0]
            # No chip-bearing rows on a fresh DB → no seed entries.
            assert count == 0
            version = conn.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()[0]
            assert version >= 94
