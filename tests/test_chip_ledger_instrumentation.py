"""Tests for commit 2 chip-ledger instrumentation.

Verifies that each creation-event call site fires the expected
`record(...)` with the right reason and amount. Two strategies:

  1. **Pure helpers** (`credit_ai_cash_out`): drive directly with a
     real `ChipLedgerRepository` against a tempdb. Assert against
     the ledger contents.
  2. **Route-touching helpers** (`_load_or_seed_player_bankroll`,
     `sponsor_and_sit`): import the route module by file path so we
     don't trigger `flask_app.routes.__init__` (which needs a live
     limiter). Then drive the helper with bankroll/ledger repos
     patched at the `flask_app.extensions` lookup site.

Commit 3 will add the destruction-event tests in a separate file.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from cash_mode.bankroll import AIBankrollState, credit_ai_cash_out
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager


# --- Fixtures ---


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "ledger_instr.db")
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


@pytest.fixture(scope="module")
def cash_routes_module():
    """Load `flask_app/routes/cash_routes.py` directly, skipping the
    package init that wires a live Flask limiter."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(here, 'flask_app', 'routes', 'cash_routes.py')
    spec = importlib.util.spec_from_file_location('flask_app_cash_routes_for_test', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


# --- ai_regen: credit_ai_cash_out emits ai_regen for the projected delta ---


class TestCreditAICashOutLedger:
    def test_regen_recorded_when_projected_exceeds_stored(
        self, bankroll_repo, ledger_repo, db_path,
    ):
        _insert_personality(db_path, "napoleon", knobs={
            "bankroll_cap": 50_000, "bankroll_rate": 500,
            "buy_in_multiplier": 1.0,
            "stop_loss_buy_ins": 3, "stop_win_buy_ins": 5,
            "stake_comfort_zone": "$10",
        })
        # last_regen_tick was 4 days ago → +500/day * 4 = +2000 regen.
        anchor = datetime(2026, 5, 18, 12, 0, 0)
        bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id="napoleon", chips=5_000,
            last_regen_tick=anchor - timedelta(days=4),
        ))

        credit_ai_cash_out(
            bankroll_repo, "napoleon", 1_000, now=anchor,
            chip_ledger_repo=ledger_repo,
            ledger_context={'game_id': 'cash-test'},
        )

        entries = ledger_repo.recent_entries()
        regen_entries = [e for e in entries if e['reason'] == 'ai_regen']
        assert len(regen_entries) == 1
        assert regen_entries[0]['amount'] == 2000
        assert regen_entries[0]['source'] == 'central_bank'
        assert regen_entries[0]['sink'] == 'ai:napoleon'
        assert regen_entries[0]['context']['site'] == 'credit_ai_cash_out'
        assert regen_entries[0]['context']['game_id'] == 'cash-test'

    def test_no_regen_entry_when_projected_equals_stored(
        self, bankroll_repo, ledger_repo, db_path,
    ):
        _insert_personality(db_path, "napoleon", knobs={
            "bankroll_cap": 50_000, "bankroll_rate": 500,
            "buy_in_multiplier": 1.0,
            "stop_loss_buy_ins": 3, "stop_win_buy_ins": 5,
            "stake_comfort_zone": "$10",
        })
        anchor = datetime(2026, 5, 18, 12, 0, 0)
        # Same tick → zero elapsed → no regen.
        bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id="napoleon", chips=5_000, last_regen_tick=anchor,
        ))
        credit_ai_cash_out(
            bankroll_repo, "napoleon", 1_000, now=anchor,
            chip_ledger_repo=ledger_repo,
        )
        assert ledger_repo.recent_entries() == []

    def test_omitting_ledger_repo_is_silent(self, bankroll_repo, db_path):
        """Legacy callers that don't wire the ledger don't crash."""
        _insert_personality(db_path, "napoleon", knobs={
            "bankroll_cap": 50_000, "bankroll_rate": 500,
            "buy_in_multiplier": 1.0,
            "stop_loss_buy_ins": 3, "stop_win_buy_ins": 5,
            "stake_comfort_zone": "$10",
        })
        anchor = datetime(2026, 5, 18, 12, 0, 0)
        bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id="napoleon", chips=5_000,
            last_regen_tick=anchor - timedelta(days=4),
        ))
        # No chip_ledger_repo → returns normally, no exception.
        result = credit_ai_cash_out(
            bankroll_repo, "napoleon", 1_000, now=anchor,
        )
        assert result is not None


# --- player_seed: _load_or_seed_player_bankroll fires on the seed branch only ---


class TestPlayerSeedLedger:
    def test_fresh_player_fires_player_seed(
        self, bankroll_repo, ledger_repo, cash_routes_module,
    ):
        with patch('flask_app.extensions.bankroll_repo', bankroll_repo), \
             patch('flask_app.extensions.chip_ledger_repo', ledger_repo):
            result = cash_routes_module._load_or_seed_player_bankroll('alice')

        assert result.chips == cash_routes_module.DEFAULT_PLAYER_STARTING_BANKROLL

        entries = ledger_repo.recent_entries()
        assert len(entries) == 1
        assert entries[0]['reason'] == 'player_seed'
        assert entries[0]['amount'] == cash_routes_module.DEFAULT_PLAYER_STARTING_BANKROLL
        assert entries[0]['source'] == 'central_bank'
        assert entries[0]['sink'] == 'player:alice'

    def test_returning_player_does_not_fire(
        self, bankroll_repo, ledger_repo, cash_routes_module,
    ):
        from cash_mode.bankroll import PlayerBankrollState
        bankroll_repo.save_player_bankroll(PlayerBankrollState(
            player_id='alice', chips=300, starting_bankroll=200,
        ))

        with patch('flask_app.extensions.bankroll_repo', bankroll_repo), \
             patch('flask_app.extensions.chip_ledger_repo', ledger_repo):
            result = cash_routes_module._load_or_seed_player_bankroll('alice')

        # Got the existing bankroll back, not a re-seed.
        assert result.chips == 300
        # Ledger stays clean.
        assert ledger_repo.recent_entries() == []


# --- house_stake_issue: sponsor route fires only for the house-archetype branch ---


class TestHouseStakeIssueLedger:
    def test_record_house_stake_issue_helper(self, ledger_repo):
        from core.economy import ledger as chip_ledger

        chip_ledger.record_house_stake_issue(
            ledger_repo,
            owner_id='alice',
            amount=200,
            context={'archetype_id': 'shark_loan_500'},
        )

        entries = ledger_repo.recent_entries()
        assert len(entries) == 1
        assert entries[0]['reason'] == 'house_stake_issue'
        assert entries[0]['amount'] == 200
        assert entries[0]['source'] == 'central_bank'
        assert entries[0]['sink'] == 'player:alice'
        assert entries[0]['context']['archetype_id'] == 'shark_loan_500'

    def test_personality_stakes_dont_route_through_helper(self, cash_routes_module):
        """The helper exists for house-archetype stakes only;
        personality-stake principal is a pure transfer between non-bank
        entities and shouldn't reach this code path. The sponsor route
        guards the call with `if offer_lender_id is None` — assert that
        contract via a grep-style check on the route source."""
        import inspect
        src = inspect.getsource(cash_routes_module.sponsor_and_sit)
        guarded = (
            "if offer_lender_id is None:" in src
            and "record_house_stake_issue" in src
        )
        assert guarded, (
            "sponsor_and_sit must guard record_house_stake_issue behind "
            "an `if offer_lender_id is None:` check"
        )


# --- ai_regen helper: math correctness ---


class TestAiRegenHelper:
    def test_records_delta_only(self, ledger_repo):
        from core.economy import ledger as chip_ledger
        chip_ledger.record_ai_regen(
            ledger_repo,
            personality_id='zeus', stored_chips=1000, projected_chips=1500,
            context={'site': 'sit_down_debit'},
        )
        entries = ledger_repo.recent_entries()
        assert entries[0]['amount'] == 500
        assert entries[0]['reason'] == 'ai_regen'

    def test_no_op_when_delta_zero(self, ledger_repo):
        from core.economy import ledger as chip_ledger
        result = chip_ledger.record_ai_regen(
            ledger_repo,
            personality_id='zeus', stored_chips=1500, projected_chips=1500,
        )
        assert result is None
        assert ledger_repo.recent_entries() == []

    def test_no_op_when_delta_negative(self, ledger_repo):
        """Sit-down debit makes new chips < stored, but we computed
        delta = projected - stored (pre-debit). Defensive: even if a
        caller mis-passes the post-debit value, the helper drops it
        rather than emitting an audit-confusing entry."""
        from core.economy import ledger as chip_ledger
        result = chip_ledger.record_ai_regen(
            ledger_repo,
            personality_id='zeus', stored_chips=1500, projected_chips=1000,
        )
        assert result is None
        assert ledger_repo.recent_entries() == []
