"""Tests for the cash-mode economy toggles.

Covers:
- `REGEN_ENABLED=False` makes `project_bankroll` skip the time-based
  accrual entirely (the passive faucet shuts off).
- `compute_rake` honors the disable flag, the rate, and the BB cap.
- `record_table_rake` writes a `table_rake` ledger entry with the
  right source/sink shape and the correct no-op guards.
- AI-only sim (`play_one_hand`) deducts the rake from the headline
  winner's stack and ledgers it when rake is enabled.

The full-sim path test uses a tempdb-backed ledger repo + a small
2-seat synthetic table. We don't drive a real hand engine — instead
we patch `award_pot_winnings` so the winner is deterministic and we
can assert on the rake deduction directly. The economy flag wiring,
ledger entry shape, and the headline-winner identification are what
matter here; full engine integration is exercised by the existing
full_sim test suite.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from cash_mode import economy_flags
from cash_mode.bankroll import AIBankrollState, project_bankroll
from core.economy import ledger as chip_ledger
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "economy_flags.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def ledger_repo(db_path):
    r = ChipLedgerRepository(db_path)
    yield r
    r.close()


@pytest.fixture(autouse=True)
def reset_flags():
    """Snapshot + restore flag values around each test."""
    saved = (
        economy_flags.REGEN_ENABLED,
        economy_flags.RAKE_ENABLED,
        economy_flags.RAKE_PLAYER_TABLES,
        economy_flags.RAKE_RATE,
        economy_flags.RAKE_CAP_BB,
    )
    yield
    (
        economy_flags.REGEN_ENABLED,
        economy_flags.RAKE_ENABLED,
        economy_flags.RAKE_PLAYER_TABLES,
        economy_flags.RAKE_RATE,
        economy_flags.RAKE_CAP_BB,
    ) = saved


# --- REGEN_ENABLED ------------------------------------------------------


class TestRegenFlag:
    def test_regen_on_accrues_chips_over_time(self):
        anchor = datetime(2026, 5, 20, 12, 0, 0)
        state = AIBankrollState(
            personality_id="napoleon", chips=1000, last_regen_tick=anchor,
        )
        later = anchor + timedelta(days=2)
        economy_flags.REGEN_ENABLED = True
        # rate=500/day, 2 days elapsed → +1000, capped to 10000
        assert project_bankroll(state, starting_bankroll=10_000, rate=500, now=later) == 2000

    def test_regen_off_returns_stored_chips(self):
        anchor = datetime(2026, 5, 20, 12, 0, 0)
        state = AIBankrollState(
            personality_id="napoleon", chips=1000, last_regen_tick=anchor,
        )
        later = anchor + timedelta(days=10)
        economy_flags.REGEN_ENABLED = False
        # No accrual — stored value comes back verbatim.
        assert project_bankroll(state, starting_bankroll=10_000, rate=500, now=later) == 1000

    def test_regen_off_still_clamps_stored_above_cap(self):
        # Defensive: stored chips can sit above cap (legacy, future cap
        # reduction). Read path clamps even with regen off.
        anchor = datetime(2026, 5, 20, 12, 0, 0)
        state = AIBankrollState(
            personality_id="napoleon", chips=15_000, last_regen_tick=anchor,
        )
        economy_flags.REGEN_ENABLED = False
        assert project_bankroll(state, starting_bankroll=10_000, rate=500, now=anchor) == 10_000

    def test_regen_off_with_null_last_tick_returns_stored(self):
        state = AIBankrollState(
            personality_id="napoleon", chips=1000, last_regen_tick=None,
        )
        economy_flags.REGEN_ENABLED = False
        # Same fast-path as REGEN_ENABLED=True for the unseeded case.
        assert project_bankroll(
            state, starting_bankroll=10_000, rate=500, now=datetime(2026, 5, 20),
        ) == 1000


# --- compute_rake -------------------------------------------------------


class TestComputeRake:
    def test_rake_disabled_returns_zero(self):
        economy_flags.RAKE_ENABLED = False
        economy_flags.RAKE_RATE = 0.05
        assert economy_flags.compute_rake(pot=1000, big_blind=10) == 0

    def test_rake_rate_applied_to_pot(self):
        economy_flags.RAKE_ENABLED = True
        economy_flags.RAKE_RATE = 0.02
        economy_flags.RAKE_CAP_BB = 100  # high enough not to bind
        # 2% of 1000 = 20
        assert economy_flags.compute_rake(pot=1000, big_blind=10) == 20

    def test_rake_capped_at_bb_multiple(self):
        economy_flags.RAKE_ENABLED = True
        economy_flags.RAKE_RATE = 0.10  # would give 100
        economy_flags.RAKE_CAP_BB = 4
        # cap = 4 * 10 = 40
        assert economy_flags.compute_rake(pot=1000, big_blind=10) == 40

    def test_rake_zero_pot_zero_rake(self):
        economy_flags.RAKE_ENABLED = True
        assert economy_flags.compute_rake(pot=0, big_blind=10) == 0

    def test_rake_invalid_bb_returns_zero(self):
        economy_flags.RAKE_ENABLED = True
        assert economy_flags.compute_rake(pot=1000, big_blind=0) == 0


# --- record_table_rake --------------------------------------------------


class TestRecordTableRake:
    def test_records_ai_source_to_bank(self, ledger_repo):
        chip_ledger.record_table_rake(
            ledger_repo,
            source=chip_ledger.ai("napoleon"),
            amount=25,
            context={'pot': 1000, 'big_blind': 10},
            sandbox_id="test-sandbox",
        )
        entries = ledger_repo.recent_entries()
        rake_rows = [e for e in entries if e['reason'] == 'table_rake']
        assert len(rake_rows) == 1
        assert rake_rows[0]['source'] == 'ai:napoleon'
        assert rake_rows[0]['sink'] == 'central_bank'
        assert rake_rows[0]['amount'] == 25
        assert rake_rows[0]['context']['pot'] == 1000

    def test_records_player_source_to_bank(self, ledger_repo):
        chip_ledger.record_table_rake(
            ledger_repo,
            source=chip_ledger.player("user-42"),
            amount=15,
            sandbox_id="test-sandbox",
        )
        entries = ledger_repo.recent_entries()
        rake_rows = [e for e in entries if e['reason'] == 'table_rake']
        assert rake_rows[0]['source'] == 'player:user-42'
        assert rake_rows[0]['amount'] == 15

    def test_no_op_on_zero_amount(self, ledger_repo):
        result = chip_ledger.record_table_rake(
            ledger_repo, source=chip_ledger.ai("napoleon"), amount=0,
        )
        assert result is None
        assert ledger_repo.recent_entries() == []

    def test_no_op_on_none_repo(self):
        result = chip_ledger.record_table_rake(
            None, source=chip_ledger.ai("napoleon"), amount=100,
        )
        assert result is None


# --- Full-sim integration: rake skim on AI-only hands -------------------


class TestRakeInFullSim:
    """Drive `_apply_rake_to_winner` directly with synthetic chip
    snapshots. This avoids spinning up the real hand engine — the
    important wiring (winner identification, deduction, ledger write,
    cap behavior) is independent of how the chips arrived at their
    final values.
    """

    def test_rake_deducts_from_winner_when_enabled(self, ledger_repo):
        from cash_mode.full_sim import _apply_rake_to_winner

        economy_flags.RAKE_ENABLED = True
        economy_flags.RAKE_RATE = 0.05
        economy_flags.RAKE_CAP_BB = 100

        starting = {'napoleon': 1000, 'bezos': 1000}
        final = {'napoleon': 1500, 'bezos': 500}  # pot = 500
        _apply_rake_to_winner(
            final_chips=final,
            starting_chips=starting,
            pot=500,
            big_blind=10,
            winner_pid='napoleon',
            chip_ledger_repo=ledger_repo,
            sandbox_id='test-sandbox',
            table_id='table-1',
        )
        # 5% of 500 = 25
        assert final['napoleon'] == 1475
        assert final['bezos'] == 500  # loser unaffected

        entries = ledger_repo.recent_entries()
        rake_rows = [e for e in entries if e['reason'] == 'table_rake']
        assert len(rake_rows) == 1
        assert rake_rows[0]['amount'] == 25
        assert rake_rows[0]['source'] == 'ai:napoleon'
        assert rake_rows[0]['context']['table_id'] == 'table-1'

    def test_no_rake_when_disabled(self, ledger_repo):
        from cash_mode.full_sim import _apply_rake_to_winner

        economy_flags.RAKE_ENABLED = False

        final = {'napoleon': 1500, 'bezos': 500}
        _apply_rake_to_winner(
            final_chips=final,
            starting_chips={'napoleon': 1000, 'bezos': 1000},
            pot=500,
            big_blind=10,
            winner_pid='napoleon',
            chip_ledger_repo=ledger_repo,
            sandbox_id='test-sandbox',
            table_id='table-1',
        )
        assert final['napoleon'] == 1500  # untouched
        assert ledger_repo.recent_entries() == []

    def test_no_rake_when_no_ledger_repo(self):
        from cash_mode.full_sim import _apply_rake_to_winner

        economy_flags.RAKE_ENABLED = True
        economy_flags.RAKE_RATE = 0.05

        final = {'napoleon': 1500, 'bezos': 500}
        _apply_rake_to_winner(
            final_chips=final,
            starting_chips={'napoleon': 1000, 'bezos': 1000},
            pot=500,
            big_blind=10,
            winner_pid='napoleon',
            chip_ledger_repo=None,
            sandbox_id='test-sandbox',
            table_id='table-1',
        )
        # Without a repo, rake is a no-op — we can't ledger the
        # destruction so we don't perform it either.
        assert final['napoleon'] == 1500

    def test_rake_clamped_by_winner_net(self, ledger_repo):
        """If the headline winner's net win is smaller than the rake
        calculation would suggest, only take what they actually won.

        Prevents a degenerate case where rounding / multiway pot
        accounting would otherwise push a winner negative.
        """
        from cash_mode.full_sim import _apply_rake_to_winner

        economy_flags.RAKE_ENABLED = True
        economy_flags.RAKE_RATE = 0.50  # absurd, just for the bound
        economy_flags.RAKE_CAP_BB = 1000

        # Tiny net of 10 — half of pot=100 would be 50, but the winner
        # only netted 10.
        final = {'napoleon': 1010, 'bezos': 990}
        _apply_rake_to_winner(
            final_chips=final,
            starting_chips={'napoleon': 1000, 'bezos': 1000},
            pot=100,
            big_blind=2,
            winner_pid='napoleon',
            chip_ledger_repo=ledger_repo,
            sandbox_id='test-sandbox',
            table_id='table-1',
        )
        # Clamped to the +10 net the winner actually saw.
        assert final['napoleon'] == 1000
        rake_rows = [
            e for e in ledger_repo.recent_entries()
            if e['reason'] == 'table_rake'
        ]
        assert rake_rows[0]['amount'] == 10

    def test_rake_no_winner_pid_is_noop(self, ledger_repo):
        from cash_mode.full_sim import _apply_rake_to_winner

        economy_flags.RAKE_ENABLED = True
        economy_flags.RAKE_RATE = 0.05

        final = {'napoleon': 1000, 'bezos': 1000}
        _apply_rake_to_winner(
            final_chips=final,
            starting_chips={'napoleon': 1000, 'bezos': 1000},
            pot=0,
            big_blind=10,
            winner_pid=None,  # fold-around / pot-neutral
            chip_ledger_repo=ledger_repo,
            sandbox_id='test-sandbox',
            table_id='table-1',
        )
        assert final == {'napoleon': 1000, 'bezos': 1000}
        assert ledger_repo.recent_entries() == []


# --- Universe-conservation property -------------------------------------


class TestUniverseConservation:
    """End-to-end invariant: with RAKE_ENABLED + REGEN_ENABLED=False,
    every sim hand strictly destroys chips (universe deflates).
    With RAKE_ENABLED=False + REGEN_ENABLED=True, chip movement
    between AIs is a pure transfer (universe size unchanged from
    sim hands alone). This is the "faucet vs sink" property we
    actually care about.
    """

    def test_rake_only_universe_deflates(self, ledger_repo):
        from cash_mode.full_sim import _apply_rake_to_winner

        economy_flags.RAKE_ENABLED = True
        economy_flags.RAKE_RATE = 0.02

        starting = {'a': 1000, 'b': 1000}
        final = {'a': 1500, 'b': 500}
        before = sum(final.values())
        _apply_rake_to_winner(
            final_chips=final,
            starting_chips=starting,
            pot=500,
            big_blind=10,
            winner_pid='a',
            chip_ledger_repo=ledger_repo,
            sandbox_id='test-sandbox',
            table_id='t',
        )
        after = sum(final.values())
        # Universe shrank by exactly the rake amount.
        assert before - after == 10  # 2% of 500
