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
from core.economy import ledger as L
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

    def test_bust_writes_no_cash_out_row(self, bankroll_repo, ledger_repo, db_path, custody_on):
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
    def test_sit_play_leave_reconciles(self, bankroll_repo, ledger_repo, db_path, custody_on):
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


class _StubGameRepo:
    def __init__(self, rows):
        self._rows = rows
        self.deleted = []

    def list_games(self, owner_id=None, limit=10000, offset=0):
        return list(self._rows)

    def delete_game(self, game_id):
        self.deleted.append(game_id)


class TestSettleBeforeDelete:
    """Phase 3 structural reaper: a non-empty human seat balance is settled back
    to the bankroll before the row is deleted — never zeroed (forfeiture)."""

    def test_orphan_seat_settled_to_bankroll(self, bankroll_repo, ledger_repo, db_path, custody_on):
        from datetime import datetime, timedelta

        from cash_mode.bankroll import PlayerBankrollState
        from cash_mode.lobby import _boot_sweep_stale_cash_rows

        now = datetime(2026, 6, 1, 12, 0, 0)
        gid = "cash-orphan-1"
        OID = "guest_settle"
        # Owner has a bankroll; a sit committed a 2_000 buy-in to the seat
        # (player_buy_in) but the session row never landed — an orphan. The seat
        # account holds 2_000 with no cash-out.
        bankroll_repo.save_player_bankroll(PlayerBankrollState(OID, 5_000, 10_000))
        L.record_player_buy_in(ledger_repo, owner_id=OID, game_id=gid, amount=2_000, sandbox_id=SB)
        assert ledger_repo.balance_of(L.seat(gid), sandbox_id=None) == 2_000

        game_repo = _StubGameRepo(
            [
                type(
                    "Row",
                    (),
                    {"game_id": gid, "owner_id": OID, "updated_at": now - timedelta(hours=2)},
                )()
            ]
        )

        class _Sessions:  # sessionless orphan → load returns None
            def load(self, _gid):
                return None

        swept = _boot_sweep_stale_cash_rows(
            game_repo=game_repo,
            cash_session_repo=_Sessions(),
            chip_ledger_repo=ledger_repo,
            bankroll_repo=bankroll_repo,
            stale_ttl_seconds=1800,
            now=now,
        )

        assert swept == 1
        assert gid in game_repo.deleted
        # The 2_000 was SETTLED back to the bankroll, not forfeited.
        assert bankroll_repo.load_player_bankroll(OID).chips == 7_000
        # And the seat balance is now zero (a cash-out transfer, not a zeroing).
        assert ledger_repo.balance_of(L.seat(gid), sandbox_id=None) == 0

    def test_no_settle_when_seat_empty(self, bankroll_repo, ledger_repo, db_path, custody_on):
        from datetime import datetime, timedelta

        from cash_mode.bankroll import PlayerBankrollState
        from cash_mode.lobby import _boot_sweep_stale_cash_rows

        now = datetime(2026, 6, 1, 12, 0, 0)
        gid = "cash-empty-1"
        OID = "guest_empty"
        bankroll_repo.save_player_bankroll(PlayerBankrollState(OID, 5_000, 10_000))
        game_repo = _StubGameRepo(
            [
                type(
                    "Row",
                    (),
                    {"game_id": gid, "owner_id": OID, "updated_at": now - timedelta(hours=2)},
                )()
            ]
        )

        class _Sessions:
            def load(self, _gid):
                return None

        _boot_sweep_stale_cash_rows(
            game_repo=game_repo,
            cash_session_repo=_Sessions(),
            chip_ledger_repo=ledger_repo,
            bankroll_repo=bankroll_repo,
            stale_ttl_seconds=1800,
            now=now,
        )
        assert gid in game_repo.deleted
        assert bankroll_repo.load_player_bankroll(OID).chips == 5_000  # untouched


class TestPersonaDeleteSettle:
    """Phase 5 deletion integrity: deleting an AI persona returns its bankroll
    chips (every sandbox) to the bank pool — conservation-safe, not stranded."""

    def test_returns_all_sandbox_bankrolls_to_pool(
        self, bankroll_repo, ledger_repo, db_path, custody_on
    ):
        from cash_mode.bankroll import settle_ai_bankroll_to_pool_on_delete

        bankroll_repo.save_ai_bankroll(AIBankrollState(PID, 4_000, NOW), sandbox_id=SB)
        bankroll_repo.save_ai_bankroll(AIBankrollState(PID, 1_500, NOW), sandbox_id="sb-2")

        returned = settle_ai_bankroll_to_pool_on_delete(
            PID, bankroll_repo=bankroll_repo, chip_ledger_repo=ledger_repo
        )
        assert returned == 5_500
        # Rows zeroed (chips recycled, not stranded).
        assert bankroll_repo.load_ai_bankroll(PID, sandbox_id=SB).chips == 0
        assert bankroll_repo.load_ai_bankroll(PID, sandbox_id="sb-2").chips == 0
        # Two casino_seat_return rows (ai → bank pool) recorded.
        rows = [
            e
            for e in ledger_repo.recent_entries()
            if e["reason"] == "casino_seat_return" and e["source"] == f"ai:{PID}"
        ]
        assert sum(e["amount"] for e in rows) == 5_500

    def test_noop_when_custody_off(self, bankroll_repo, ledger_repo, db_path):
        from cash_mode.bankroll import settle_ai_bankroll_to_pool_on_delete

        bankroll_repo.save_ai_bankroll(AIBankrollState(PID, 4_000, NOW), sandbox_id=SB)
        returned = settle_ai_bankroll_to_pool_on_delete(
            PID, bankroll_repo=bankroll_repo, chip_ledger_repo=ledger_repo
        )
        assert returned == 0
        assert bankroll_repo.load_ai_bankroll(PID, sandbox_id=SB).chips == 4_000
