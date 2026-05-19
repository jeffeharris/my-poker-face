"""Commit 3 chip-ledger instrumentation tests — destruction events.

Covers `cap_clamp`, `house_loan_settle`, and the `forgive_balance`
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
from cash_mode.loan_settlement import settle_loan_on_leave
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


# --- cap_clamp: credit_ai_cash_out evaporates overflow above bankroll_cap ---


class TestCapClampLedger:
    def test_cap_clamp_fired_when_post_credit_exceeds_cap(
        self, bankroll_repo, ledger_repo, db_path,
    ):
        # Cap = 5000. Stored = 4500, no elapsed time so projected =
        # 4500. Player stack = 1000. post_credit = 5500. Overflow = 500.
        _insert_personality(db_path, "napoleon", knobs={
            "bankroll_cap": 5_000, "bankroll_rate": 500,
            "buy_in_multiplier": 1.0,
            "stop_loss_buy_ins": 3, "stop_win_buy_ins": 5,
            "stake_comfort_zone": "$10",
        })
        anchor = datetime(2026, 5, 18, 12, 0, 0)
        bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id="napoleon", chips=4_500, last_regen_tick=anchor,
        ))

        credit_ai_cash_out(
            bankroll_repo, "napoleon", 1_000, now=anchor,
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
            "bankroll_cap": 50_000, "bankroll_rate": 500,
            "buy_in_multiplier": 1.0,
            "stop_loss_buy_ins": 3, "stop_win_buy_ins": 5,
            "stake_comfort_zone": "$10",
        })
        anchor = datetime(2026, 5, 18, 12, 0, 0)
        bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id="napoleon", chips=5_000, last_regen_tick=anchor,
        ))

        credit_ai_cash_out(
            bankroll_repo, "napoleon", 1_000, now=anchor,
            chip_ledger_repo=ledger_repo,
        )

        clamps = [e for e in ledger_repo.recent_entries() if e['reason'] == 'cap_clamp']
        assert clamps == []


# --- house_loan_settle + forgive_balance ---


class TestHouseLoanSettleLedger:
    def test_settle_records_sponsor_total(self, ledger_repo):
        # Anonymous loan: 200 chips, floor=1.0 (full), rate=0.0.
        # Player returns with 200 chips → to_floor=200, remaining=0,
        # sponsor_total=200 (all back to bank).
        bankroll = PlayerBankrollState(
            player_id="alice",
            chips=0,
            starting_bankroll=200,
            active_loan_amount=200,
            active_loan_floor=1.0,
            active_loan_rate=0.0,
            active_loan_lender_id=None,
        )

        settlement = settle_loan_on_leave(
            bankroll, chips_at_table=200,
            chip_ledger_repo=ledger_repo,
        )
        assert settlement.sponsor_total == 200

        settles = [
            e for e in ledger_repo.recent_entries()
            if e['reason'] == 'house_loan_settle'
        ]
        assert len(settles) == 1
        assert settles[0]['amount'] == 200
        assert settles[0]['source'] == 'player:alice'
        assert settles[0]['sink'] == 'central_bank'

    def test_settle_below_floor_emits_settle_and_forgive(self, ledger_repo):
        # Loan 200, floor=1.0, returned with 50. to_floor=50,
        # sponsor_total=50 (back to bank), forgiven=150 (annotation).
        bankroll = PlayerBankrollState(
            player_id="alice",
            chips=0,
            starting_bankroll=200,
            active_loan_amount=200,
            active_loan_floor=1.0,
            active_loan_rate=0.0,
            active_loan_lender_id=None,
        )

        settle_loan_on_leave(
            bankroll, chips_at_table=50,
            chip_ledger_repo=ledger_repo,
        )

        entries = ledger_repo.recent_entries()
        reasons = {e['reason']: e for e in entries}
        assert 'house_loan_settle' in reasons
        assert 'forgive_balance' in reasons
        assert reasons['house_loan_settle']['amount'] == 50
        assert reasons['forgive_balance']['amount'] == 0
        assert reasons['forgive_balance']['context']['forgiven_principal'] == 150

    def test_personality_loan_does_not_fire_house_settle(
        self, bankroll_repo, ledger_repo, db_path,
    ):
        # Path B: lender_id set → sponsor_total credits the AI's
        # bankroll instead. No house_loan_settle, no forgive_balance.
        _insert_personality(db_path, "zeus", knobs={
            "bankroll_cap": 50_000, "bankroll_rate": 500,
            "buy_in_multiplier": 1.0,
            "stop_loss_buy_ins": 3, "stop_win_buy_ins": 5,
            "stake_comfort_zone": "$10",
        })
        bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id="zeus", chips=5_000,
            last_regen_tick=datetime(2026, 5, 18, 12, 0, 0),
        ))

        bankroll = PlayerBankrollState(
            player_id="alice",
            chips=0,
            starting_bankroll=200,
            active_loan_amount=200,
            active_loan_floor=1.0,
            active_loan_rate=0.0,
            active_loan_lender_id="zeus",
        )

        settle_loan_on_leave(
            bankroll, chips_at_table=50,
            bankroll_repo=bankroll_repo,
            chip_ledger_repo=ledger_repo,
            now=datetime(2026, 5, 18, 12, 0, 0),
        )

        reasons = {e['reason'] for e in ledger_repo.recent_entries()}
        # ai_regen may fire for the lender bankroll write (no time
        # elapsed here so it shouldn't), but neither destruction
        # entry is allowed for Path B.
        assert 'house_loan_settle' not in reasons
        assert 'forgive_balance' not in reasons

    def test_full_payback_no_forgive(self, ledger_repo):
        bankroll = PlayerBankrollState(
            player_id="alice",
            chips=0,
            starting_bankroll=200,
            active_loan_amount=200,
            active_loan_floor=1.0,
            active_loan_rate=0.0,
            active_loan_lender_id=None,
        )
        settle_loan_on_leave(
            bankroll, chips_at_table=200,
            chip_ledger_repo=ledger_repo,
        )
        reasons = {e['reason'] for e in ledger_repo.recent_entries()}
        assert 'forgive_balance' not in reasons


# --- Helper-level: small sanity tests for the destruction sugar ---


class TestDestructionHelpers:
    def test_cap_clamp_helper_no_op_when_overflow_zero(self, ledger_repo):
        result = chip_ledger.record_cap_clamp(
            ledger_repo, personality_id="zeus", overflow=0,
        )
        assert result is None
        assert ledger_repo.recent_entries() == []

    def test_house_loan_settle_helper_no_op_when_amount_zero(self, ledger_repo):
        result = chip_ledger.record_house_loan_settle(
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
