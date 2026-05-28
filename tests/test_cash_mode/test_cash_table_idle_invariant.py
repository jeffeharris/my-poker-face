"""The seated⇒not-idle invariant, enforced centrally in `save_table`.

An AI is either at a table or resting in `cash_idle_pool`, never both
(the recurring `seated_and_idle` split-brain). Rather than make every
seating path remember to clear the idle row, `CashTableRepository.save_table`
— the sole writer of `cash_tables.seats_json` — drops the idle row for any
AI present in the seats it persists, in the same transaction. These tests
pin that behavior at the chokepoint.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from cash_mode.tables import CashTableState, IdlePoolEntry, ai_slot, open_slot
from poker.repositories.cash_table_repository import CashTableRepository
from poker.repositories.schema_manager import SchemaManager

ANCHOR = datetime(2026, 5, 26, 12, 0, 0)
SBX = "test-idle-invariant"


def _repo(tmp_path):
    db = str(tmp_path / "idle_invariant.db")
    SchemaManager(db).ensure_schema()
    return CashTableRepository(db)


def _idle_pids(repo) -> set:
    return {e.personality_id for e in repo.list_idle(sandbox_id=SBX)}


def test_save_table_clears_idle_for_seated_ai(tmp_path):
    """Persisting a table with an AI in a seat drops that AI's idle row."""
    repo = _repo(tmp_path)
    repo.save_idle(
        IdlePoolEntry(
            personality_id="napoleon",
            left_at=ANCHOR,
            reason="stake_up_queued",
            target_stake="$50",
        ),
        sandbox_id=SBX,
    )
    assert "napoleon" in _idle_pids(repo)

    table = CashTableState(
        table_id="cash-table-10-001",
        stake_label="$10",
        seats=[ai_slot("napoleon", 400)] + [open_slot()] * 5,
    )
    repo.save_table(table, sandbox_id=SBX, now=ANCHOR)

    assert "napoleon" not in _idle_pids(repo)


def test_save_table_keeps_idle_for_unseated_ai(tmp_path):
    """An AI that's NOT in the seats keeps its idle row — so a just-left
    AI (gone from the seats before the save) isn't wrongly evicted."""
    repo = _repo(tmp_path)
    repo.save_idle(
        IdlePoolEntry(personality_id="zeus", left_at=ANCHOR, reason="take_break"),
        sandbox_id=SBX,
    )
    table = CashTableState(
        table_id="cash-table-10-001",
        stake_label="$10",
        seats=[ai_slot("napoleon", 400)] + [open_slot()] * 5,
    )
    repo.save_table(table, sandbox_id=SBX, now=ANCHOR)

    assert "zeus" in _idle_pids(repo)


def test_save_table_clears_only_seated_pids(tmp_path):
    """Every seated AI's row is cleared; unseated ones survive — even when
    several are idle at once."""
    repo = _repo(tmp_path)
    for pid in ("napoleon", "zeus", "athena", "gatsby"):
        repo.save_idle(
            IdlePoolEntry(personality_id=pid, left_at=ANCHOR, reason="take_break"),
            sandbox_id=SBX,
        )
    table = CashTableState(
        table_id="cash-table-10-001",
        stake_label="$10",
        seats=[ai_slot("napoleon", 400), ai_slot("athena", 400)] + [open_slot()] * 4,
    )
    repo.save_table(table, sandbox_id=SBX, now=ANCHOR)

    assert _idle_pids(repo) == {"zeus", "gatsby"}


def test_save_table_idle_clear_is_sandbox_scoped(tmp_path):
    """A seated AI in one sandbox doesn't clear a same-named AI's idle row
    in another sandbox."""
    repo = _repo(tmp_path)
    other = "test-idle-invariant-2"
    repo.save_idle(
        IdlePoolEntry(personality_id="napoleon", left_at=ANCHOR, reason="take_break"),
        sandbox_id=other,
    )
    table = CashTableState(
        table_id="cash-table-10-001",
        stake_label="$10",
        seats=[ai_slot("napoleon", 400)] + [open_slot()] * 5,
    )
    repo.save_table(table, sandbox_id=SBX, now=ANCHOR)

    assert "napoleon" in {e.personality_id for e in repo.list_idle(sandbox_id=other)}
