"""Pin the bankroll ↔ seat chip conservation invariant.

Before the fix, live-fill in `refresh_table_roster` created `ai_buy_in`
chips on a new seat without debiting the AI's bankroll — a leak the
chip-ledger audit was catching at ~675 chips per lobby tick under full
sim. This test asserts that lobby refreshes are now zero-net in the
audit (drift delta stays at 0 across N ticks) by virtue of the
explicit `BankrollChange` plumbing that pairs every seat-mint with a
bankroll-debit and every seat-vacate with a credit-via-`credit_ai_cash_out`.

The test runs an actual lobby refresh loop because the fix lives in
the lobby's caller path (`refresh_unseated_tables` applies the
changes), not the pure helper alone. The helper's pure-function
tests live in `test_movement.py`.
"""

from __future__ import annotations

import random
from datetime import datetime

import pytest

pytestmark = pytest.mark.integration

from cash_mode.bankroll import AIBankrollState
from cash_mode.lobby import ensure_lobby_seeded, refresh_unseated_tables
from cash_mode.tables import open_slot
from flask_app.services.chip_ledger_audit import compute_audit
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.cash_table_repository import CashTableRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.stake_repository import StakeRepository


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "lobby_conservation.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def repos(db_path):
    return {
        "bankroll_repo": BankrollRepository(db_path),
        "cash_table_repo": CashTableRepository(db_path),
        "chip_ledger_repo": ChipLedgerRepository(db_path),
        "personality_repo": PersonalityRepository(db_path),
        "stake_repo": StakeRepository(db_path),
        "db_path": db_path,
    }


def _seed_personality(db_path, pid, name, bankroll_chips, cap=10_000, rate=500):
    """Insert a personality with bankroll_knobs configured + an
    ai_bankroll_state row so it's eligible for the lobby seed."""
    import json
    import sqlite3

    config_json = json.dumps(
        {
            "bankroll_knobs": {
                "starting_bankroll": cap,
                "bankroll_rate": rate,
                "buy_in_multiplier": 1.0,
                "stake_comfort_zone": "$10",
            },
        }
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO personalities "
            "(name, personality_id, config_json, visibility, circulating) "
            "VALUES (?, ?, ?, 'public', 1)",
            (name, pid, config_json),
        )
        conn.execute(
            "INSERT INTO ai_bankroll_state "
            "(personality_id, sandbox_id, chips, last_regen_tick) "
            "VALUES (?, ?, ?, ?)",
            (pid, "test-sandbox-1", bankroll_chips, datetime.utcnow().isoformat()),
        )


def _audit(repos):
    return compute_audit(
        ledger_repo=repos["chip_ledger_repo"],
        bankroll_repo=repos["bankroll_repo"],
        cash_table_repo=repos["cash_table_repo"],
        stake_repo=repos["stake_repo"],
        db_path=repos["db_path"],
        list_game_ids_fn=lambda: [],
        get_game_fn=lambda gid: None,
    )


def test_lobby_seed_preserves_audit_outstanding(repos, db_path):
    """Seeding a fresh lobby should NOT inflate actual_outstanding —
    seat chips come from the AIs' bankrolls (pure transfer)."""
    # Seed 5 personalities, each rich enough to cover the largest stake's buy-in.
    # $1000 table needs 40_000 min buy-in; give each AI 100k.
    for i, pid in enumerate(["napoleon", "bezos", "trump", "buffett", "lincoln"]):
        _seed_personality(db_path, pid, pid.title(), bankroll_chips=100_000, cap=200_000)

    before = _audit(repos)
    ensure_lobby_seeded(
        cash_table_repo=repos["cash_table_repo"],
        personality_repo=repos["personality_repo"],
        bankroll_repo=repos["bankroll_repo"],
        sandbox_id="test-sandbox-1",
    )
    after = _audit(repos)

    # The cardinal invariant: total chips in the universe didn't change.
    # ai_bankrolls_stored should go DOWN by exactly the amount that
    # cash_table_seats_ai went UP.
    bankrolls_delta = (
        after["actual_totals"]["ai_bankrolls_stored"]
        - before["actual_totals"]["ai_bankrolls_stored"]
    )
    seats_delta = (
        after["actual_totals"]["cash_table_seats_ai"]
        - before["actual_totals"]["cash_table_seats_ai"]
    )
    assert seats_delta > 0, "expected seed to place AI chips on tables"
    assert bankrolls_delta == -seats_delta, (
        "lobby seed should be a pure bankroll → seat transfer "
        f"(bankrolls Δ={bankrolls_delta}, seats Δ={seats_delta})"
    )


def test_refresh_ticks_preserve_drift(repos, db_path):
    """Run many lobby refresh ticks. Drift must not move.

    Triggers live-fill (which debits bankrolls) and movement-driven
    vacates (which credit bankrolls via credit_ai_cash_out, including
    any cap_clamp ledger entries for overflow).
    """
    # Need a richer eligible pool than the number of seats so live-fill
    # has candidates. 12 personalities for 5 tables × 4 baseline AI =
    # 20 desired seats, but lobby will only fill what fits.
    for i in range(12):
        _seed_personality(
            db_path,
            f"p_{i}",
            f"Pers{i}",
            bankroll_chips=200_000,
            cap=500_000,
        )

    ensure_lobby_seeded(
        cash_table_repo=repos["cash_table_repo"],
        personality_repo=repos["personality_repo"],
        bankroll_repo=repos["bankroll_repo"],
        sandbox_id="test-sandbox-1",
    )

    # Open a seat by force on one table so live-fill has work to do.
    tables = repos["cash_table_repo"].list_all_tables()
    if tables and tables[0].seats:
        for j, seat in enumerate(tables[0].seats):
            if seat.get("kind") == "ai":
                # Free this seat manually and credit chips back so the
                # baseline drift starts clean. (We're simulating a
                # mid-tick state where a seat is open.)
                from cash_mode.bankroll import debit_bankroll_for_seat

                # Reverse-direction: credit the AI back since we're
                # forcing the seat open — keep audit balanced.
                repos["bankroll_repo"].save_ai_bankroll(
                    AIBankrollState(
                        personality_id=seat["personality_id"],
                        chips=200_000,  # back to original
                        last_regen_tick=datetime.utcnow(),
                    ),
                    sandbox_id="test-sandbox-1",
                )
                tables[0].seats[j] = open_slot()
                break
        repos["cash_table_repo"].save_table(tables[0], sandbox_id="test-sandbox-1")

    baseline = _audit(repos)
    rng = random.Random(42)
    for _ in range(30):
        refresh_unseated_tables(
            cash_table_repo=repos["cash_table_repo"],
            personality_repo=repos["personality_repo"],
            bankroll_repo=repos["bankroll_repo"],
            chip_ledger_repo=repos["chip_ledger_repo"],
            rng=rng,
            sandbox_id="test-sandbox-1",
        )
    after = _audit(repos)

    # The principal correctness signal.
    assert after["drift"] == baseline["drift"], (
        f"Drift moved by {after['drift'] - baseline['drift']} across "
        f"30 refresh ticks; live-fill or vacate is leaking chips. "
        f"actual_outstanding Δ="
        f"{after['actual_totals']['actual_outstanding'] - baseline['actual_totals']['actual_outstanding']}, "
        f"ledger_outstanding Δ="
        f"{after['ledger_totals']['outstanding'] - baseline['ledger_totals']['outstanding']}"
    )
