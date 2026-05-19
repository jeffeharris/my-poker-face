"""Smoke test: drift behavior across `ensure_lobby_seeded`.

Pins the current lobby-v1.5 behavior so a future fix is visible.

`ensure_lobby_seeded` places `chips=ai_buy_in` at AI seats but does
NOT debit the AI's persistent bankroll — by design, per its docstring.
The justification was idempotency: re-running the seed pass shouldn't
double-spend bankrolls. But this means the same chips appear in two
places to the audit:

  - `ai_bankrolls_stored` counts the full bankroll
  - `cash_table_seats_ai` counts the placeholder seat chips

The audit sums both, so post-seed drift is `-cash_table_seats_ai`.
That isn't a bug introduced by the chip ledger work — it's a
pre-existing semantic gap in lobby v1.5 that the ledger reveals.

This test exists so:
  1. A future fix to lobby seeding (debit at seed time, or audit
     dedupes against `cash_tables` for AI seats whose bankroll is
     intact) shows up immediately as a test delta.
  2. The drift baseline behavior is documented in a runnable form.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from cash_mode.bankroll import AIBankrollState
from cash_mode.lobby import ensure_lobby_seeded
from flask_app.services.chip_ledger_audit import compute_audit
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.cash_table_repository import CashTableRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.schema_manager import SchemaManager


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "lobby_seed.db")


@pytest.fixture
def repos(db_path):
    SchemaManager(db_path).ensure_schema()

    bankroll_repo = BankrollRepository(db_path)
    cash_table_repo = CashTableRepository(db_path)
    chip_ledger_repo = ChipLedgerRepository(db_path)
    personality_repo = PersonalityRepository(db_path)

    yield db_path, bankroll_repo, cash_table_repo, chip_ledger_repo, personality_repo

    bankroll_repo.close()
    cash_table_repo.close()
    chip_ledger_repo.close()


def _seed_personalities(db_path: str, pids: list[str]) -> None:
    knobs = {
        "bankroll_cap": 50_000, "bankroll_rate": 500,
        "buy_in_multiplier": 1.0,
        "stop_loss_buy_ins": 3, "stop_win_buy_ins": 5,
        "stake_comfort_zone": "$10",
    }
    with sqlite3.connect(db_path) as conn:
        for pid in pids:
            conn.execute(
                "INSERT INTO personalities (name, config_json, personality_id) "
                "VALUES (?, ?, ?)",
                (pid.title(), json.dumps({"bankroll_knobs": knobs}), pid),
            )
        conn.commit()


def _audit_drift(db_path, bankroll_repo, cash_table_repo, chip_ledger_repo, now):
    data = compute_audit(
        ledger_repo=chip_ledger_repo,
        bankroll_repo=bankroll_repo,
        cash_table_repo=cash_table_repo,
        db_path=db_path,
        now=now,
    )
    return data


def test_lobby_seed_drift_pin(repos):
    """Pins the current behavior: drift shifts by `-cash_table_seats_ai`
    when ensure_lobby_seeded creates fresh tables.

    Once lobby seeding moves chips off the AI bankroll (or the audit
    dedupes against active seats), this test will fail with drift
    closer to zero — at which point the assertion should be tightened
    to `drift_after == drift_before`.
    """
    db_path, bankroll_repo, cash_table_repo, chip_ledger_repo, personality_repo = repos
    now = datetime(2026, 5, 18, 12, 0, 0)

    pids = ['zeus', 'hera', 'ares', 'athena', 'apollo']
    _seed_personalities(db_path, pids)
    for pid in pids:
        bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id=pid, chips=5_000, last_regen_tick=now,
        ))

    # Run v94 seed manually — it normally fires during ensure_schema,
    # but tests new ai_bankroll rows were added after that. Re-run is
    # idempotent if entries already exist; otherwise it picks up the
    # new rows.
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM chip_ledger_entries")
        sm = SchemaManager.__new__(SchemaManager)
        sm._migrate_v94_seed_pre_ledger_universe(conn)
        conn.commit()

    before = _audit_drift(db_path, bankroll_repo, cash_table_repo, chip_ledger_repo, now)
    assert before['drift'] == 0, (
        f"baseline drift should be 0 after v94 seed, got {before['drift']}"
    )

    ensure_lobby_seeded(
        cash_table_repo=cash_table_repo,
        personality_repo=personality_repo,
        bankroll_repo=bankroll_repo,
        now=now,
    )

    after = _audit_drift(db_path, bankroll_repo, cash_table_repo, chip_ledger_repo, now)

    seats_chips = after['actual_totals']['cash_table_seats_ai']
    assert seats_chips > 0, "lobby seed should have placed at least one AI seat"

    # Pinned behavior: drift shifts down by exactly the seat-chip total.
    # AI bankrolls weren't debited (per ensure_lobby_seeded's docstring),
    # so the seat chips are phantom additions to actual_outstanding.
    assert after['drift'] == before['drift'] - seats_chips, (
        f"drift shift ({before['drift'] - after['drift']}) should equal "
        f"cash_table_seats_ai ({seats_chips}); if this fails because they're "
        "now equal (drift unchanged), great — lobby seeding has been fixed; "
        "tighten the assertion to drift_after == drift_before"
    )


def test_lobby_reseed_is_idempotent_for_drift(repos):
    """Running ensure_lobby_seeded twice doesn't change drift between
    the two calls — second pass is a no-op because tables already exist."""
    db_path, bankroll_repo, cash_table_repo, chip_ledger_repo, personality_repo = repos
    now = datetime(2026, 5, 18, 12, 0, 0)

    _seed_personalities(db_path, ['zeus', 'hera'])
    for pid in ['zeus', 'hera']:
        bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id=pid, chips=5_000, last_regen_tick=now,
        ))
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM chip_ledger_entries")
        sm = SchemaManager.__new__(SchemaManager)
        sm._migrate_v94_seed_pre_ledger_universe(conn)
        conn.commit()

    ensure_lobby_seeded(
        cash_table_repo=cash_table_repo,
        personality_repo=personality_repo,
        bankroll_repo=bankroll_repo,
        now=now,
    )
    first = _audit_drift(db_path, bankroll_repo, cash_table_repo, chip_ledger_repo, now)

    ensure_lobby_seeded(
        cash_table_repo=cash_table_repo,
        personality_repo=personality_repo,
        bankroll_repo=bankroll_repo,
        now=now,
    )
    second = _audit_drift(db_path, bankroll_repo, cash_table_repo, chip_ledger_repo, now)

    assert first['drift'] == second['drift']
    assert (
        first['actual_totals']['cash_table_seats_ai']
        == second['actual_totals']['cash_table_seats_ai']
    )
