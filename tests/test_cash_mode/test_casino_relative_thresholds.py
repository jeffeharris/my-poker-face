"""resolve_pool_threshold: absolute by default, fraction-of-holdings on flag."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cash_mode import economy_flags
from cash_mode.casino_provisioning import resolve_pool_threshold
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager

SBX = "test-casino-rel"


@pytest.fixture
def ledger():
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "l.db")
        SchemaManager(db).ensure_schema()
        r = ChipLedgerRepository(db)
        # holdings = 1,000,000 (a player_seed creation)
        r.record('central_bank', 'player:p', 1_000_000, 'player_seed', sandbox_id=SBX)
        yield r
        r.close()


@pytest.fixture(autouse=True)
def _reset():
    saved = economy_flags.CASINO_RELATIVE_THRESHOLDS
    yield
    economy_flags.CASINO_RELATIVE_THRESHOLDS = saved


def test_absolute_by_default(ledger):
    economy_flags.CASINO_RELATIVE_THRESHOLDS = False
    assert resolve_pool_threshold(100_000, 0.038, ledger, SBX) == 100_000


def test_relative_scales_with_holdings(ledger):
    economy_flags.CASINO_RELATIVE_THRESHOLDS = True
    # holdings 1M × 0.038 = 38_000
    assert resolve_pool_threshold(100_000, 0.038, ledger, SBX) == 38_000


def test_none_fraction_stays_absolute(ledger):
    economy_flags.CASINO_RELATIVE_THRESHOLDS = True
    assert resolve_pool_threshold(45_000, None, ledger, SBX) == 45_000


def test_zero_holdings_falls_back_to_absolute():
    economy_flags.CASINO_RELATIVE_THRESHOLDS = True
    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "empty.db")
        SchemaManager(db).ensure_schema()
        r = ChipLedgerRepository(db)
        try:
            assert resolve_pool_threshold(50_000, 0.02, r, "empty-sbx") == 50_000
        finally:
            r.close()
