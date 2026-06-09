"""Chip-custody Phase 2 — D2 ledger-derived bankroll.

Covers `ChipLedgerRepository.balance_of` (the derivation substrate, scope-aware
to resolve the player-global / AI-per-sandbox asymmetry) and the gated
derived-read path on `BankrollRepository` (ledger as authority, int as cache).
"""

from __future__ import annotations

import sqlite3

import pytest

from cash_mode.bankroll import AIBankrollState, PlayerBankrollState
from core.economy import ledger as L
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager

SB1 = "derive-sb-1"
SB2 = "derive-sb-2"
PID = "napoleon"
OID = "guest_x"


@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / "derive.db")
    SchemaManager(p).ensure_schema()
    return p


@pytest.fixture
def ledger_repo(db_path):
    r = ChipLedgerRepository(db_path)
    yield r
    r.close()


@pytest.fixture
def bankroll_repo(db_path, ledger_repo):
    r = BankrollRepository(db_path)
    r.chip_ledger_repo = ledger_repo
    yield r
    r.close()


@pytest.fixture
def derive_on(monkeypatch):
    monkeypatch.setattr("cash_mode.economy_flags.CHIP_CUSTODY_DERIVE_READS", True)


class TestBalanceOf:
    def test_scoped_sum_minus_source(self, ledger_repo):
        L.record(
            ledger_repo,
            source=L.bank(),
            sink=L.ai(PID),
            amount=10_000,
            reason="ai_seed",
            sandbox_id=SB1,
        )
        L.record_transfer(
            ledger_repo,
            source=L.ai(PID),
            sink=L.ai_seat(SB1, PID),
            amount=3_000,
            reason="ai_buy_in",
            sandbox_id=SB1,
        )
        # ai:PID in SB1 = 10000 (seed) - 3000 (buy_in) = 7000
        assert ledger_repo.balance_of(L.ai(PID), sandbox_id=SB1) == 7_000
        assert ledger_repo.balance_of(L.ai_seat(SB1, PID), sandbox_id=SB1) == 3_000

    def test_sandbox_isolation_and_global_sum(self, ledger_repo):
        L.record(
            ledger_repo,
            source=L.bank(),
            sink=L.player(OID),
            amount=5_000,
            reason="player_seed",
            sandbox_id=SB1,
        )
        L.record(
            ledger_repo,
            source=L.bank(),
            sink=L.player(OID),
            amount=2_000,
            reason="player_seed",
            sandbox_id=SB2,
        )
        # Per-sandbox is isolated; global (None) sums across both.
        assert ledger_repo.balance_of(L.player(OID), sandbox_id=SB1) == 5_000
        assert ledger_repo.balance_of(L.player(OID), sandbox_id=SB2) == 2_000
        assert ledger_repo.balance_of(L.player(OID), sandbox_id=None) == 7_000

    def test_unknown_account_is_zero(self, ledger_repo):
        assert ledger_repo.balance_of(L.ai("nobody"), sandbox_id=SB1) == 0


class TestDeriveHelpers:
    def test_derive_ai_and_player(self, ledger_repo):
        L.record(
            ledger_repo,
            source=L.bank(),
            sink=L.ai(PID),
            amount=8_000,
            reason="ai_seed",
            sandbox_id=SB1,
        )
        L.record(
            ledger_repo,
            source=L.bank(),
            sink=L.player(OID),
            amount=4_000,
            reason="player_seed",
            sandbox_id=SB1,
        )
        assert L.derive_ai_balance(ledger_repo, personality_id=PID, sandbox_id=SB1) == 8_000
        assert L.derive_player_balance(ledger_repo, owner_id=OID) == 4_000

    def test_none_repo_returns_none(self):
        assert L.derive_ai_balance(None, personality_id=PID, sandbox_id=SB1) is None
        assert L.derive_player_balance(None, owner_id=OID) is None


class TestDerivedReads:
    def test_default_read_is_stored_int(self, bankroll_repo, ledger_repo):
        # flag OFF (autouse reset): the stored int is returned even if the
        # ledger has nothing for this pid.
        bankroll_repo.save_ai_bankroll(AIBankrollState(PID, 9_999, None), sandbox_id=SB1)
        assert bankroll_repo.load_ai_bankroll(PID, sandbox_id=SB1).chips == 9_999

    def test_derived_read_prefers_ledger(self, bankroll_repo, ledger_repo, derive_on):
        # Stored int says 9999 but the ledger derives 7000 → derived wins.
        bankroll_repo.save_ai_bankroll(AIBankrollState(PID, 9_999, None), sandbox_id=SB1)
        L.record(
            ledger_repo,
            source=L.bank(),
            sink=L.ai(PID),
            amount=7_000,
            reason="ai_seed",
            sandbox_id=SB1,
        )
        assert bankroll_repo.load_ai_bankroll(PID, sandbox_id=SB1).chips == 7_000

    def test_derived_read_matches_when_consistent(self, bankroll_repo, ledger_repo, derive_on):
        bankroll_repo.save_ai_bankroll(AIBankrollState(PID, 6_000, None), sandbox_id=SB1)
        L.record(
            ledger_repo,
            source=L.bank(),
            sink=L.ai(PID),
            amount=6_000,
            reason="ai_seed",
            sandbox_id=SB1,
        )
        assert bankroll_repo.load_ai_bankroll(PID, sandbox_id=SB1).chips == 6_000

    def test_player_derived_read_sums_across_sandboxes(self, bankroll_repo, ledger_repo, derive_on):
        bankroll_repo.save_player_bankroll(PlayerBankrollState(OID, 12_345, 10_000))
        L.record(
            ledger_repo,
            source=L.bank(),
            sink=L.player(OID),
            amount=5_000,
            reason="player_seed",
            sandbox_id=SB1,
        )
        L.record(
            ledger_repo,
            source=L.bank(),
            sink=L.player(OID),
            amount=2_000,
            reason="player_seed",
            sandbox_id=SB2,
        )
        # Global derivation = 7000 (across both sandboxes), overriding the 12345 cache.
        assert bankroll_repo.load_player_bankroll(OID).chips == 7_000

    def test_divergent_read_heals_the_cache_row(
        self, bankroll_repo, ledger_repo, db_path, derive_on
    ):
        # Stored cache (12345) drifts from the ledger-derived balance (7000).
        bankroll_repo.save_player_bankroll(PlayerBankrollState(OID, 12_345, 10_000))
        L.record(
            ledger_repo,
            source=L.bank(),
            sink=L.player(OID),
            amount=7_000,
            reason="player_seed",
            sandbox_id=SB1,
        )

        def stored_chips() -> int:
            with sqlite3.connect(db_path) as c:
                return c.execute(
                    "SELECT chips FROM player_bankroll_state WHERE player_id = ?", (OID,)
                ).fetchone()[0]

        # One divergent read writes the derived value back into the cache row …
        assert bankroll_repo.load_player_bankroll(OID).chips == 7_000
        assert stored_chips() == 7_000
        # … and starting_bankroll is left untouched by the heal.
        assert bankroll_repo.load_player_bankroll(OID).starting_bankroll == 10_000
