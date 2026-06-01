"""Chip-custody Phase 1 — AI ledger parity (the Presence twin).

Verifies the two AI bankroll chokepoints (`debit_bankroll_for_seat` /
`credit_ai_cash_out`) record `ai ↔ seat` transfers under
`CHIP_CUSTODY_ENABLED`, so an AI's bankroll becomes ledger-derivable exactly
as a human's is (Cut 2). The load-bearing assertion is CONSERVATION: after a
sit → play → leave cycle, the ledger-derived AI balance equals the stored
bankroll int (`Σ sink − Σ source` for `ai:<pid>` in the sandbox).

The flag is forced OFF by the autouse `reset_presence_cutover_flags` fixture;
tests that exercise the custody path set it explicitly.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest

from cash_mode.bankroll import (
    AIBankrollState,
    credit_ai_cash_out,
    debit_bankroll_for_seat,
)
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager

SB = "custody-sandbox-1"
PID = "napoleon"
NOW = datetime(2026, 6, 1, 12, 0, 0)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "custody.db")
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


@pytest.fixture
def custody_on(monkeypatch):
    monkeypatch.setattr("cash_mode.economy_flags.CHIP_CUSTODY_ENABLED", True)


def _insert_personality(db_path: str, pid: str, *, starting: int) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO personalities (name, config_json, personality_id) VALUES (?, ?, ?)",
            (
                f"Personality {pid}",
                json.dumps(
                    {
                        "bankroll_knobs": {
                            "starting_bankroll": starting,
                            "bankroll_rate": 500,
                            "buy_in_multiplier": 1.0,
                            "stake_comfort_zone": "$10",
                        }
                    }
                ),
                pid,
            ),
        )
        conn.commit()


def _derived_ai_balance(db_path: str, pid: str, sandbox_id: str) -> int:
    """Mirror audit_ledger_completeness: Σ(sink) − Σ(source) for ai:<pid>."""
    acct = f"ai:{pid}"
    bal = 0
    conn = sqlite3.connect(db_path)
    try:
        for source, sink, amount, sb in conn.execute(
            "SELECT source, sink, amount, sandbox_id FROM chip_ledger_entries WHERE sandbox_id = ?",
            (sandbox_id,),
        ):
            if sink == acct:
                bal += int(amount)
            if source == acct:
                bal -= int(amount)
    finally:
        conn.close()
    return bal


def _seat_balance(db_path: str, pid: str, sandbox_id: str) -> int:
    acct = f"seat:ai:{sandbox_id}:{pid}"
    bal = 0
    conn = sqlite3.connect(db_path)
    try:
        for source, sink, amount, sb in conn.execute(
            "SELECT source, sink, amount, sandbox_id FROM chip_ledger_entries WHERE sandbox_id = ?",
            (sandbox_id,),
        ):
            if sink == acct:
                bal += int(amount)
            if source == acct:
                bal -= int(amount)
    finally:
        conn.close()
    return bal


def _seed(bankroll_repo, ledger_repo, *, chips: int) -> None:
    """First-write seed (emits ai_seed via save_ai_bankroll's hook)."""
    bankroll_repo.save_ai_bankroll(
        AIBankrollState(personality_id=PID, chips=chips, last_regen_tick=NOW),
        sandbox_id=SB,
        chip_ledger_repo=ledger_repo,
    )


class TestBuyInLedger:
    def test_buy_in_records_ai_buy_in_transfer(
        self, bankroll_repo, ledger_repo, db_path, custody_on
    ):
        _insert_personality(db_path, PID, starting=10_000)
        _seed(bankroll_repo, ledger_repo, chips=10_000)

        debit_bankroll_for_seat(
            bankroll_repo,
            PID,
            3_000,
            sandbox_id=SB,
            chip_ledger_repo=ledger_repo,
            now=NOW,
        )

        entries = ledger_repo.recent_entries()
        buy_ins = [e for e in entries if e["reason"] == "ai_buy_in"]
        assert len(buy_ins) == 1
        assert buy_ins[0]["amount"] == 3_000
        assert buy_ins[0]["source"] == f"ai:{PID}"
        assert buy_ins[0]["sink"] == f"seat:ai:{SB}:{PID}"
        # Stored dropped by 3k; derived tracks it; seat holds the 3k.
        assert bankroll_repo.load_ai_bankroll(PID, sandbox_id=SB).chips == 7_000
        assert _derived_ai_balance(db_path, PID, SB) == 7_000
        assert _seat_balance(db_path, PID, SB) == 3_000

    def test_buy_in_inert_when_flag_off(self, bankroll_repo, ledger_repo, db_path):
        _insert_personality(db_path, PID, starting=10_000)
        _seed(bankroll_repo, ledger_repo, chips=10_000)

        debit_bankroll_for_seat(
            bankroll_repo, PID, 3_000, sandbox_id=SB, chip_ledger_repo=ledger_repo, now=NOW
        )

        entries = ledger_repo.recent_entries()
        assert [e for e in entries if e["reason"] == "ai_buy_in"] == []


class TestCashOutLedger:
    def test_cash_out_records_ai_cash_out_transfer(
        self, bankroll_repo, ledger_repo, db_path, custody_on
    ):
        _insert_personality(db_path, PID, starting=10_000)
        _seed(bankroll_repo, ledger_repo, chips=7_000)

        credit_ai_cash_out(
            bankroll_repo, PID, 4_000, sandbox_id=SB, now=NOW, chip_ledger_repo=ledger_repo
        )

        entries = ledger_repo.recent_entries()
        cash_outs = [e for e in entries if e["reason"] == "ai_cash_out"]
        assert len(cash_outs) == 1
        assert cash_outs[0]["amount"] == 4_000
        assert cash_outs[0]["source"] == f"seat:ai:{SB}:{PID}"
        assert cash_outs[0]["sink"] == f"ai:{PID}"

    def test_bust_writes_no_cash_out_row(
        self, bankroll_repo, ledger_repo, db_path, custody_on
    ):
        _insert_personality(db_path, PID, starting=10_000)
        _seed(bankroll_repo, ledger_repo, chips=7_000)

        credit_ai_cash_out(
            bankroll_repo, PID, 0, sandbox_id=SB, now=NOW, chip_ledger_repo=ledger_repo
        )

        entries = ledger_repo.recent_entries()
        assert [e for e in entries if e["reason"] == "ai_cash_out"] == []

    def test_stake_payoff_path_records_no_seat_transfer(
        self, bankroll_repo, ledger_repo, db_path, custody_on
    ):
        """from_seat=False (stake/carry payoff) must NOT emit ai_cash_out —
        the caller records `stake_payoff` for the funding source instead."""
        _insert_personality(db_path, PID, starting=10_000)
        _seed(bankroll_repo, ledger_repo, chips=7_000)

        credit_ai_cash_out(
            bankroll_repo,
            PID,
            4_000,
            sandbox_id=SB,
            now=NOW,
            chip_ledger_repo=ledger_repo,
            from_seat=False,
        )

        entries = ledger_repo.recent_entries()
        assert [e for e in entries if e["reason"] == "ai_cash_out"] == []


class TestRoundTripConservation:
    def test_sit_play_leave_reconciles(
        self, bankroll_repo, ledger_repo, db_path, custody_on
    ):
        """The load-bearing test: after sit (buy 3k) → win 1k → leave (stack 4k),
        the ledger-derived AI balance equals the stored bankroll int."""
        _insert_personality(db_path, PID, starting=10_000)
        _seed(bankroll_repo, ledger_repo, chips=10_000)

        # Sit: buy in 3,000.
        debit_bankroll_for_seat(
            bankroll_repo, PID, 3_000, sandbox_id=SB, chip_ledger_repo=ledger_repo, now=NOW
        )
        # Leave: cash out a 4,000 stack (won 1,000 at the table).
        credit_ai_cash_out(
            bankroll_repo, PID, 4_000, sandbox_id=SB, now=NOW, chip_ledger_repo=ledger_repo
        )

        stored = bankroll_repo.load_ai_bankroll(PID, sandbox_id=SB).chips
        assert stored == 11_000
        assert _derived_ai_balance(db_path, PID, SB) == stored
        # The 1,000 won lands as a negative seat balance (came from other seats);
        # the seat account is the custody substrate, not audited against a stored.
        assert _seat_balance(db_path, PID, SB) == -1_000

    def test_full_loss_reconciles(self, bankroll_repo, ledger_repo, db_path, custody_on):
        """Sit 3k → lose it all → bust leave (stack 0). Derived == stored."""
        _insert_personality(db_path, PID, starting=10_000)
        _seed(bankroll_repo, ledger_repo, chips=10_000)

        debit_bankroll_for_seat(
            bankroll_repo, PID, 3_000, sandbox_id=SB, chip_ledger_repo=ledger_repo, now=NOW
        )
        credit_ai_cash_out(
            bankroll_repo, PID, 0, sandbox_id=SB, now=NOW, chip_ledger_repo=ledger_repo
        )

        stored = bankroll_repo.load_ai_bankroll(PID, sandbox_id=SB).chips
        assert stored == 7_000  # 10k − 3k bought in, lost the 3k at the table
        assert _derived_ai_balance(db_path, PID, SB) == stored
        # Seat holds the 3k the AI bought in and never cashed out — the absent
        # cash_out paired with the buy_in IS the bust record.
        assert _seat_balance(db_path, PID, SB) == 3_000
