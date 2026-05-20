"""Commit 3 chip-ledger instrumentation tests — destruction events.

Covers `cap_clamp`, `house_stake_settle`, and the `forgive_balance`
annotation. Same shape as `test_chip_ledger_instrumentation.py`:
real repos against tempdb, dataclass inputs, assert against ledger
contents.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from cash_mode.bankroll import AIBankrollState, PlayerBankrollState, credit_ai_cash_out
from core.economy import ledger as chip_ledger
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "destruction.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def bankroll_repo(db_path):
    r = BankrollRepository(db_path)
    yield r
    r.close()


@pytest.fixture
def ledger_repo(db_path):
    r = ChipLedgerRepository(db_path)
    yield r
    r.close()


def _insert_personality(db_path: str, personality_id: str, *, knobs: dict) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO personalities (name, config_json, personality_id) "
            "VALUES (?, ?, ?)",
            (
                f"Personality {personality_id}",
                json.dumps({"bankroll_knobs": knobs}),
                personality_id,
            ),
        )
        conn.commit()


# --- cap_clamp: credit_ai_cash_out evaporates overflow above starting_bankroll ---


class TestCapClampLedger:
    def test_cap_clamp_fired_when_post_credit_exceeds_cap(
        self, bankroll_repo, ledger_repo, db_path,
    ):
        # Cap = 5000. Stored = 4500, no elapsed time so projected =
        # 4500. Player stack = 1000. post_credit = 5500. Overflow = 500.
        _insert_personality(db_path, "napoleon", knobs={
            "starting_bankroll": 5_000, "bankroll_rate": 500,
            "buy_in_multiplier": 1.0,
            "stake_comfort_zone": "$10",
        })
        anchor = datetime(2026, 5, 18, 12, 0, 0)
        bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id="napoleon", chips=4_500, last_regen_tick=anchor,
        ), sandbox_id="test-sandbox-1")

        credit_ai_cash_out(
            bankroll_repo, "napoleon", 1_000, sandbox_id="test-sandbox-1", now=anchor,
            chip_ledger_repo=ledger_repo,
        )

        clamps = [e for e in ledger_repo.recent_entries() if e['reason'] == 'cap_clamp']
        assert len(clamps) == 1
        assert clamps[0]['amount'] == 500
        assert clamps[0]['source'] == 'ai:napoleon'
        assert clamps[0]['sink'] == 'central_bank'
        assert clamps[0]['context']['cap'] == 5_000
        assert clamps[0]['context']['player_stack'] == 1_000

    def test_no_clamp_when_post_credit_below_cap(
        self, bankroll_repo, ledger_repo, db_path,
    ):
        _insert_personality(db_path, "napoleon", knobs={
            "starting_bankroll": 50_000, "bankroll_rate": 500,
            "buy_in_multiplier": 1.0,
            "stake_comfort_zone": "$10",
        })
        anchor = datetime(2026, 5, 18, 12, 0, 0)
        bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id="napoleon", chips=5_000, last_regen_tick=anchor,
        ), sandbox_id="test-sandbox-1")

        credit_ai_cash_out(
            bankroll_repo, "napoleon", 1_000, sandbox_id="test-sandbox-1", now=anchor,
            chip_ledger_repo=ledger_repo,
        )

        clamps = [e for e in ledger_repo.recent_entries() if e['reason'] == 'cap_clamp']
        assert clamps == []


# NOTE: The `TestHouseStakeSettleLedger` class that previously lived
# here exercised house_stake_settle + forgive_balance via the legacy
# `settle_loan_on_leave` code path. That path was removed in Cleanup A
# of the backing-system handoff; equivalent coverage now lives in
# `tests/test_stake_settlement.py` (house-stake forgive path) and
# `tests/test_stake_chip_flow.py` (house-stake chip flow + ledger
# annotation) against the stakes-table-backed implementation.


# --- Helper-level: small sanity tests for the destruction sugar ---


class TestDestructionHelpers:
    def test_cap_clamp_helper_no_op_when_overflow_zero(self, ledger_repo):
        result = chip_ledger.record_cap_clamp(
            ledger_repo, personality_id="zeus", overflow=0,
        )
        assert result is None
        assert ledger_repo.recent_entries() == []

    def test_house_stake_settle_helper_no_op_when_amount_zero(self, ledger_repo):
        result = chip_ledger.record_house_stake_settle(
            ledger_repo, owner_id="alice", amount=0,
        )
        assert result is None
        assert ledger_repo.recent_entries() == []

    def test_forgive_balance_helper_no_op_when_principal_zero(self, ledger_repo):
        result = chip_ledger.record_forgive_balance(
            ledger_repo, owner_id="alice", forgiven_principal=0,
        )
        assert result is None
        assert ledger_repo.recent_entries() == []

    def test_forgive_balance_stamps_principal_in_context(self, ledger_repo):
        chip_ledger.record_forgive_balance(
            ledger_repo, owner_id="alice", forgiven_principal=150,
            context={'loan_amount': 200},
        )
        entries = ledger_repo.recent_entries()
        assert entries[0]['amount'] == 0
        assert entries[0]['context']['forgiven_principal'] == 150
        assert entries[0]['context']['loan_amount'] == 200
