"""Phase 0 DIAGNOSTIC harness for the cash-mode seat-occupancy invariants.

This is NOT a fix and touches no production code. It is a seeded
invariant / conservation probe that drives the REAL world-tick
(`cash_mode.lobby.refresh_unseated_tables`) across many seeds × ticks
and, after every tick, asserts the two properties the recurring
"ghost-seat" bug class violates:

  (A) ONE-SEAT-PER-AI — no `personality_id` appears in an AI seat at
      more than one table simultaneously (the double-seat bug). The
      world tick builds a `seated_globally: Set[str]` and threads it
      *by reference* through `refresh_table_roster` and
      `_process_global_greedy_fills`; correctness hinges on every path
      remembering to `.add` / `.discard`. This asserts the result.

  (B) CHIP CONSERVATION — `Σ ai-seat-chips + Σ ai-bankroll-chips-stored`
      is constant across ticks. With `bankroll_rate=0` (no regen) and no
      vice / side-hustle / staking wired, every seat fill is a pure
      bankroll→seat transfer and every vacate a seat→bankroll transfer,
      so the total must never move. This mirrors the two line items the
      chip-ledger audit tracks (`ai_bankrolls_stored` +
      `cash_table_seats_ai`); a move means a mint or burn leaked in.

SCOPE: drives the FULL `refresh_unseated_tables` (the actual bug
surface), not the narrower `_process_global_greedy_fills` helper which
`test_global_greedy_fill.py` already covers. LLM / narrative paths are
off (disabled suite-wide in conftest + no repos passed), vice_mode is
'off', and only NON-fish personalities are seeded so the closed-economy
fish (pool-funded, separate path) don't complicate the conservation sum.
"""

from __future__ import annotations

import json
import random
import sqlite3
from collections import Counter
from datetime import datetime

import pytest

from cash_mode.lobby import ensure_lobby_seeded, refresh_unseated_tables
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.cash_table_repository import CashTableRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.schema_manager import SchemaManager

# Out of the quick loop: this runs many seeded world ticks (hands sim'd).
pytestmark = pytest.mark.simulation

SANDBOX = "test-seat-occupancy-invariants"
ANCHOR = datetime(2026, 5, 29, 12, 0, 0)

# A pool comfortably larger than the seat supply so movement + greedy
# refill have real work to do every tick (the path that re-seats AIs and
# is where a double-seat would surface).
N_PERSONALITIES = 16
# Each AI starts rich enough to cover several buy-ins at low stakes and to
# stake up; rate=0 means NO regen, keeping the conservation sum exact.
STARTING_BANKROLL = 200_000
BANKROLL_CAP = 500_000


def _make_db(tmp_path):
    path = str(tmp_path / "seat_occupancy_invariants.db")
    SchemaManager(path).ensure_schema()
    return path


def _seed_personality(db_path, pid, name):
    """Insert a non-fish personality with bankroll_knobs + a bankroll row.

    rate=0 -> no regen, so `seat_chips + bankroll_stored` is a true
    conserved quantity (no projection mint between ticks).
    """
    config_json = json.dumps(
        {
            "bankroll_knobs": {
                "starting_bankroll": BANKROLL_CAP,
                "bankroll_rate": 0,
                "buy_in_multiplier": 1.0,
                "stake_comfort_zone": "$10",
            },
        }
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO personalities (name, personality_id, config_json, visibility) "
            "VALUES (?, ?, ?, 'public')",
            (name, pid, config_json),
        )
        conn.execute(
            "INSERT INTO ai_bankroll_state "
            "(personality_id, sandbox_id, chips, last_regen_tick) "
            "VALUES (?, ?, ?, ?)",
            (pid, SANDBOX, STARTING_BANKROLL, ANCHOR.isoformat()),
        )
        conn.commit()


def _build_sandbox(tmp_path):
    """Fresh DB + seeded lobby. Returns (repos_dict, db_path)."""
    db_path = _make_db(tmp_path)
    for i in range(N_PERSONALITIES):
        _seed_personality(db_path, f"pers_{i}", f"Pers{i}")

    repos = {
        "cash_table_repo": CashTableRepository(db_path),
        "personality_repo": PersonalityRepository(db_path),
        "bankroll_repo": BankrollRepository(db_path),
        "chip_ledger_repo": ChipLedgerRepository(db_path),
    }
    ensure_lobby_seeded(
        cash_table_repo=repos["cash_table_repo"],
        personality_repo=repos["personality_repo"],
        bankroll_repo=repos["bankroll_repo"],
        sandbox_id=SANDBOX,
        now=ANCHOR,
    )
    return repos, db_path


def _seat_occupancy(cash_table_repo):
    """Return {personality_id: [table_id, ...]} for every AI seat.

    A pid mapping to >1 distinct table is a double-seat (invariant A).
    """
    placements: dict[str, list[str]] = {}
    for table in cash_table_repo.list_all_tables(sandbox_id=SANDBOX):
        for slot in table.seats:
            if slot.get("kind") == "ai":
                pid = slot.get("personality_id")
                if pid:
                    placements.setdefault(pid, []).append(table.table_id)
    return placements


def _total_chips(cash_table_repo, bankroll_repo):
    """Σ ai-seat-chips + Σ ai-bankroll-chips-stored (the conserved total).

    Matches the chip-ledger audit's `cash_table_seats_ai` +
    `ai_bankrolls_stored` line items (scoped to this sandbox). With no
    regen and no vice/hustle/stake, fills and vacates only move chips
    *between* these two buckets, so the sum must be invariant.
    """
    seat_chips = 0
    for table in cash_table_repo.list_all_tables(sandbox_id=SANDBOX):
        for slot in table.seats:
            if slot.get("kind") == "ai":
                seat_chips += int(slot.get("chips", 0) or 0)
    bankroll_chips = bankroll_repo.sum_ai_bankroll_chips_stored(sandbox_id=SANDBOX)
    return seat_chips + bankroll_chips


def _double_seated(placements):
    """pids seated at more than one distinct table."""
    return {pid: tids for pid, tids in placements.items() if len(set(tids)) > 1}


def _within_table_dupes(cash_table_repo):
    """A pid appearing twice in the SAME table's seats is also a ghost seat."""
    dupes = {}
    for table in cash_table_repo.list_all_tables(sandbox_id=SANDBOX):
        counts = Counter(
            slot.get("personality_id")
            for slot in table.seats
            if slot.get("kind") == "ai" and slot.get("personality_id")
        )
        for pid, c in counts.items():
            if c > 1:
                dupes.setdefault(table.table_id, {})[pid] = c
    return dupes


# A modest seed sweep × several ticks each. Each tick sims hands +
# movement + a global greedy refill, so per-seed state diverges quickly.
SEEDS = list(range(40))
TICKS_PER_SEED = 6


@pytest.mark.parametrize("seed", SEEDS)
def test_seat_occupancy_and_conservation_hold_across_ticks(tmp_path, seed):
    repos, _db_path = _build_sandbox(tmp_path)
    cash_table_repo = repos["cash_table_repo"]
    bankroll_repo = repos["bankroll_repo"]

    initial_total = _total_chips(cash_table_repo, bankroll_repo)
    # The seeded lobby itself must already be single-seat + dupe-free.
    assert not _double_seated(_seat_occupancy(cash_table_repo)), (
        f"[seed {seed}] lobby seed produced a double-seat before any tick"
    )

    rng = random.Random(seed)
    for tick in range(TICKS_PER_SEED):
        refresh_unseated_tables(
            cash_table_repo=cash_table_repo,
            personality_repo=repos["personality_repo"],
            bankroll_repo=bankroll_repo,
            chip_ledger_repo=repos["chip_ledger_repo"],
            rng=rng,
            now=ANCHOR,
            sandbox_id=SANDBOX,
            # Disable every off-grid / credit mechanic so the only chip
            # movements are pure seat<->bankroll transfers: no vice, no
            # side hustle, no staking, no human-headroom reservation.
            vice_mode="off",
            vice_repo=None,
            side_hustle_repo=None,
            stake_repo=None,
            relationship_repo=None,
            human_headroom=0,
        )

        # (A) one-seat-per-AI — across tables AND within a table.
        placements = _seat_occupancy(cash_table_repo)
        cross = _double_seated(placements)
        within = _within_table_dupes(cash_table_repo)
        assert not cross, (
            f"[seed {seed} tick {tick}] DOUBLE-SEAT (cross-table): {cross}"
        )
        assert not within, (
            f"[seed {seed} tick {tick}] DUPLICATE seat within a table: {within}"
        )

        # (B) chip conservation — exact (integer transfers, no regen).
        total = _total_chips(cash_table_repo, bankroll_repo)
        assert total == initial_total, (
            f"[seed {seed} tick {tick}] CHIP CONSERVATION broken: "
            f"total={total} initial={initial_total} delta={total - initial_total}"
        )
