"""Integration test for the global greedy seat-fill wiring (Phase C2b).

Spec: `docs/plans/CASH_MODE_TABLE_ATTRACTIVENESS.md` §2.

Drives `_process_global_greedy_fills` directly (rather than the whole
`refresh_unseated_tables` burst machinery) to validate the WIRING the pure
core (`assign_seats_greedy`, tested in test_attractiveness.py) doesn't cover:
FillableTable construction from real seats (fish-chip gathering, fillable
open indices), the inline bankroll debit, seat placement, idle removal, and
the seated_globally mutation.
"""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime, timedelta

import pytest

from cash_mode.bankroll import AIBankrollState
from cash_mode.lobby import _process_global_greedy_fills
from cash_mode.movement import RosterRefreshResult
from cash_mode.tables import CashTableState, IdlePoolEntry, ai_slot_fish, open_slot
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.cash_table_repository import CashTableRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.schema_manager import SchemaManager

SB = "sb-greedy-fill"

pytestmark = pytest.mark.integration


def _insert_personality(db_path, pid, *, name, knobs):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO personalities (name, config_json, personality_id, visibility) "
            "VALUES (?, ?, ?, 'public')",
            (name, json.dumps({"bankroll_knobs": knobs}), pid),
        )
        conn.commit()


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "greedy_fill.db")
    SchemaManager(path).ensure_schema()
    return path


def _open_indices(seats):
    return frozenset(i for i, s in enumerate(seats) if s["kind"] == "open")


def test_global_fill_seats_idle_grinder_at_the_fishier_table(db_path):
    cash_table_repo = CashTableRepository(db_path)
    bankroll_repo = BankrollRepository(db_path)
    personality_repo = PersonalityRepository(db_path)
    chip_ledger_repo = ChipLedgerRepository(db_path)
    now = datetime(2026, 5, 29, 12, 0, 0)

    # A non-fish grinder, comfortably rolled for $2, sitting idle.
    _insert_personality(
        db_path,
        "grinder_g",
        name="Grinder",
        knobs={
            "starting_bankroll": 5_000,
            "bankroll_rate": 0,
            "buy_in_multiplier": 1.0,
            "stake_comfort_zone": "$2",
        },
    )
    bankroll_repo.save_ai_bankroll(
        AIBankrollState(personality_id="grinder_g", chips=5_000, last_regen_tick=None),
        sandbox_id=SB,
    )

    # Two $2 tables: one casino table with a seated fish (chips on the
    # felt), one dead all-open table. The grinder should pick the fishy one.
    fishy_seats = [ai_slot_fish("vacation_greg", 600)] + [open_slot() for _ in range(5)]
    fishy = CashTableState(
        table_id="cash-fishy",
        stake_label="$2",
        seats=fishy_seats,
        name="Fishy",
        table_type="casino",
    )
    dead = CashTableState(
        table_id="cash-dead",
        stake_label="$2",
        seats=[open_slot() for _ in range(6)],
        name="Dead",
        table_type="casino",
    )
    cash_table_repo.save_table(fishy, sandbox_id=SB)
    cash_table_repo.save_table(dead, sandbox_id=SB)

    # The grinder is idle and well-rested (left long ago).
    idle_entry = IdlePoolEntry(
        personality_id="grinder_g",
        left_at=now - timedelta(hours=6),
        reason="bored_move",
        target_stake=None,
    )
    cash_table_repo.save_idle(idle_entry, sandbox_id=SB)

    fill_ctx = {
        "cash-fishy": (RosterRefreshResult(new_table=fishy), _open_indices(fishy.seats)),
        "cash-dead": (RosterRefreshResult(new_table=dead), _open_indices(dead.seats)),
    }
    seated_globally = {"vacation_greg"}

    _process_global_greedy_fills(
        fill_ctx=fill_ctx,
        idle_pool=[idle_entry],
        eligible=[],
        seated_globally=seated_globally,
        fish_ids={"vacation_greg"},
        bankroll_lookup=lambda pid: 5_000 if pid == "grinder_g" else None,
        bankroll_repo=bankroll_repo,
        cash_table_repo=cash_table_repo,
        chip_ledger_repo=chip_ledger_repo,
        personality_repo=personality_repo,
        sandbox_id=SB,
        now=now,
        rng=random.Random(0),
        seek_rate=1.0,  # force the grinder to go room-hunting
    )

    # Seated at the FISHY table, not the dead one.
    fishy_after = cash_table_repo.load_table("cash-fishy", sandbox_id=SB)
    dead_after = cash_table_repo.load_table("cash-dead", sandbox_id=SB)
    fishy_pids = {s["personality_id"] for s in fishy_after.seats if s["kind"] == "ai"}
    dead_pids = {s["personality_id"] for s in dead_after.seats if s["kind"] == "ai"}
    assert "grinder_g" in fishy_pids
    assert "grinder_g" not in dead_pids

    # Funded by an inline debit (no chip mint): bankroll dropped by the $2
    # buy-in (40bb = 80), and the seat carries those chips.
    after = bankroll_repo.load_ai_bankroll_current("grinder_g", sandbox_id=SB, now=now)
    assert after == 5_000 - 80
    seat = next(s for s in fishy_after.seats if s.get("personality_id") == "grinder_g")
    assert seat["chips"] == 80

    # seated_globally mutated in place; idle row removed.
    assert "grinder_g" in seated_globally
    remaining_idle = {e.personality_id for e in cash_table_repo.list_idle(sandbox_id=SB)}
    assert "grinder_g" not in remaining_idle


def test_global_fill_refuses_to_seat_unfundable_ai(db_path):
    # An AI whose bankroll can't cover the buy-in is never seated (no mint).
    cash_table_repo = CashTableRepository(db_path)
    bankroll_repo = BankrollRepository(db_path)
    personality_repo = PersonalityRepository(db_path)
    chip_ledger_repo = ChipLedgerRepository(db_path)
    now = datetime(2026, 5, 29, 12, 0, 0)

    _insert_personality(
        db_path,
        "broke_b",
        name="Broke",
        knobs={
            "starting_bankroll": 50,
            "bankroll_rate": 0,
            "buy_in_multiplier": 1.0,
            "stake_comfort_zone": "$2",
        },
    )
    bankroll_repo.save_ai_bankroll(
        AIBankrollState(personality_id="broke_b", chips=50, last_regen_tick=None),
        sandbox_id=SB,
    )
    table = CashTableState(
        table_id="cash-t",
        stake_label="$2",
        seats=[open_slot() for _ in range(6)],
        name="T",
        table_type="casino",
    )
    cash_table_repo.save_table(table, sandbox_id=SB)
    entry = IdlePoolEntry(
        personality_id="broke_b",
        left_at=now - timedelta(hours=6),
        reason="bored_move",
        target_stake=None,
    )

    _process_global_greedy_fills(
        fill_ctx={"cash-t": (RosterRefreshResult(new_table=table), _open_indices(table.seats))},
        idle_pool=[entry],
        eligible=[],
        seated_globally=set(),
        fish_ids=set(),
        bankroll_lookup=lambda pid: 50,  # below the $2 buy-in (80)
        bankroll_repo=bankroll_repo,
        cash_table_repo=cash_table_repo,
        chip_ledger_repo=chip_ledger_repo,
        personality_repo=personality_repo,
        sandbox_id=SB,
        now=now,
        rng=random.Random(0),
        seek_rate=1.0,
    )

    after = cash_table_repo.load_table("cash-t", sandbox_id=SB)
    assert all(s["kind"] == "open" for s in after.seats)  # never seated
