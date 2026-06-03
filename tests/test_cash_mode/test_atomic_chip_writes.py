"""Atomic chip-movement writes (chip-custody): the bankroll int and its ledger
row commit in ONE transaction, closing the two-commit divergence window.

Covers the `conn`-sharing seam added to `BankrollRepository.save_ai_bankroll` +
`ChipLedgerRepository.record` + `core/economy/ledger.record*`:
  - a first-write `save_ai_bankroll` writes the int AND the `ai_seed` ledger row
    on one connection (the seed is no longer a separate post-commit write);
  - when a caller passes its own `conn`, both writes join that transaction, so a
    rollback discards BOTH (atomic) and a commit persists BOTH.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

pytestmark = pytest.mark.integration

from cash_mode.bankroll import AIBankrollState
from core.economy import ledger as chip_ledger
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager

SB = "sb-atomic"
NOW = datetime(2026, 6, 3, 12, 0, 0)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "atomic.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def repos(db_path):
    bk = BankrollRepository(db_path)
    led = ChipLedgerRepository(db_path)
    yield bk, led
    bk.close()
    led.close()


def _ai_row_exists(db_path, pid) -> bool:
    with sqlite3.connect(db_path) as c:
        return (
            c.execute(
                "SELECT 1 FROM ai_bankroll_state WHERE personality_id=? AND sandbox_id=?",
                (pid, SB),
            ).fetchone()
            is not None
        )


def _seed_rows(db_path, pid) -> int:
    with sqlite3.connect(db_path) as c:
        return c.execute(
            "SELECT COUNT(*) FROM chip_ledger_entries WHERE sink=? AND reason='ai_seed' AND sandbox_id=?",
            (chip_ledger.ai(pid), SB),
        ).fetchone()[0]


class TestFirstWriteSeedConsistent:
    def test_first_write_persists_int_and_seed(self, repos, db_path):
        bk, led = repos
        bk.save_ai_bankroll(
            AIBankrollState(personality_id="zeus", chips=5_000, last_regen_tick=NOW),
            sandbox_id=SB,
            chip_ledger_repo=led,
        )
        # Both the int and its ai_seed ledger row landed.
        assert _ai_row_exists(db_path, "zeus")
        assert _seed_rows(db_path, "zeus") == 1
        # And the ledger-derived balance equals the stored int (custody-complete).
        assert chip_ledger.derive_ai_balance(led, personality_id="zeus", sandbox_id=SB) == 5_000

    def test_second_write_does_not_re_seed(self, repos, db_path):
        bk, led = repos
        bk.save_ai_bankroll(
            AIBankrollState(personality_id="zeus", chips=5_000, last_regen_tick=NOW),
            sandbox_id=SB,
            chip_ledger_repo=led,
        )
        bk.save_ai_bankroll(
            AIBankrollState(personality_id="zeus", chips=4_000, last_regen_tick=NOW),
            sandbox_id=SB,
            chip_ledger_repo=led,
        )
        # Only the first write seeds; the second is not a first-write.
        assert _seed_rows(db_path, "zeus") == 1


class TestConnSharedTransaction:
    """When the caller passes its own `conn`, the int + seed join that
    transaction — proving atomicity: rollback discards BOTH, commit persists BOTH."""

    def test_rollback_discards_both(self, repos, db_path):
        bk, led = repos
        raw = sqlite3.connect(db_path)
        try:
            bk.save_ai_bankroll(
                AIBankrollState(personality_id="batman", chips=3_000, last_regen_tick=NOW),
                sandbox_id=SB,
                chip_ledger_repo=led,
                conn=raw,
            )
            # Visible within the caller's own (uncommitted) transaction...
            assert (
                raw.execute(
                    "SELECT chips FROM ai_bankroll_state WHERE personality_id='batman'"
                ).fetchone()
                is not None
            )
            raw.rollback()
        finally:
            raw.close()
        # ...but rolled back → neither the int nor the seed persisted (atomic).
        assert not _ai_row_exists(db_path, "batman")
        assert _seed_rows(db_path, "batman") == 0

    def test_commit_persists_both(self, repos, db_path):
        bk, led = repos
        raw = sqlite3.connect(db_path)
        try:
            bk.save_ai_bankroll(
                AIBankrollState(personality_id="robin", chips=2_500, last_regen_tick=NOW),
                sandbox_id=SB,
                chip_ledger_repo=led,
                conn=raw,
            )
            raw.commit()
        finally:
            raw.close()
        assert _ai_row_exists(db_path, "robin")
        assert _seed_rows(db_path, "robin") == 1
        assert chip_ledger.derive_ai_balance(led, personality_id="robin", sandbox_id=SB) == 2_500
