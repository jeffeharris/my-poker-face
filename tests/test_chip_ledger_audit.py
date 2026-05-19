"""Tests for `compute_audit` and the admin chip-ledger routes.

Drives the audit function with seeded fixtures (no live Flask test
client needed for the math). Verifies:

  * Ledger totals sum correctly across creations and destructions
  * Actual totals pull from all four chip-bearing surfaces
  * Drift is zero when ledger + actual agree, non-zero on simulated
    bypass
  * by_reason and by_reason_window_24h split correctly
  * Annotation rows (amount=0) don't affect bucket totals but
    appear in `by_reason`
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List

import pytest

from cash_mode.bankroll import AIBankrollState, PlayerBankrollState
from cash_mode.tables import CashTableState, ai_slot, open_slot
from flask_app.services.chip_ledger_audit import compute_audit
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.cash_table_repository import CashTableRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "audit.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def repos(db_path):
    bankroll_repo = BankrollRepository(db_path)
    cash_table_repo = CashTableRepository(db_path)
    ledger_repo = ChipLedgerRepository(db_path)
    yield bankroll_repo, cash_table_repo, ledger_repo
    bankroll_repo.close()
    cash_table_repo.close()
    ledger_repo.close()


def _insert_personality(db_path: str, personality_id: str, *, cap=50_000, rate=500) -> None:
    knobs = {
        "bankroll_cap": cap, "bankroll_rate": rate,
        "buy_in_multiplier": 1.0,
        "stop_loss_buy_ins": 3, "stop_win_buy_ins": 5,
        "stake_comfort_zone": "$10",
    }
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


class TestComputeAudit:
    def test_empty_state_zero_everywhere(self, repos, db_path):
        bankroll_repo, cash_table_repo, ledger_repo = repos
        now = datetime(2026, 5, 18, 12, 0, 0)

        data = compute_audit(
            ledger_repo=ledger_repo,
            bankroll_repo=bankroll_repo,
            cash_table_repo=cash_table_repo,
            db_path=db_path,
            now=now,
        )

        assert data['ledger_totals'] == {
            'chips_created': 0, 'chips_destroyed': 0, 'outstanding': 0,
        }
        assert data['actual_totals']['actual_outstanding'] == 0
        assert data['drift'] == 0
        assert data['by_reason'] == {}
        assert data['by_reason_window_24h'] == {}

    def test_ledger_totals_split_by_creation_vs_destruction(self, repos, db_path):
        bankroll_repo, cash_table_repo, ledger_repo = repos
        ledger_repo.record('central_bank', 'player:alice', 200, 'player_seed')
        ledger_repo.record('central_bank', 'ai:zeus', 1000, 'ai_regen')
        ledger_repo.record('ai:zeus', 'central_bank', 50, 'cap_clamp')

        now = datetime(2026, 5, 18, 12, 0, 0)
        data = compute_audit(
            ledger_repo=ledger_repo,
            bankroll_repo=bankroll_repo,
            cash_table_repo=cash_table_repo,
            db_path=db_path,
            now=now,
        )

        assert data['ledger_totals']['chips_created'] == 1200
        assert data['ledger_totals']['chips_destroyed'] == 50
        assert data['ledger_totals']['outstanding'] == 1150

        assert data['by_reason']['player_seed'] == 200
        assert data['by_reason']['ai_regen'] == 1000
        assert data['by_reason']['cap_clamp'] == -50

    def test_actual_totals_sum_bankrolls_tables_and_loans(
        self, repos, db_path,
    ):
        bankroll_repo, cash_table_repo, ledger_repo = repos
        # Player bankroll: 500 chips, 200 active loan principal.
        bankroll_repo.save_player_bankroll(PlayerBankrollState(
            player_id='alice', chips=500, starting_bankroll=200,
            active_loan_amount=200, active_loan_floor=1.0,
            active_loan_rate=0.0, active_loan_lender_id=None,
        ))
        # AI bankroll: 3000 chips, no elapsed time → projected = 3000.
        _insert_personality(db_path, "zeus")
        anchor = datetime(2026, 5, 18, 12, 0, 0)
        bankroll_repo.save_ai_bankroll(AIBankrollState(
            personality_id="zeus", chips=3000, last_regen_tick=anchor,
        ))
        # Cash table with 2 AI seats holding 100+200 chips.
        cash_table_repo.save_table(CashTableState(
            table_id='cash-table-2-001',
            stake_label='$2',
            seats=[
                ai_slot('zeus', 100),
                ai_slot('hera', 200),
                open_slot(),
                open_slot(),
                open_slot(),
                open_slot(),
            ],
            created_at=anchor,
            last_activity_at=anchor,
        ))

        data = compute_audit(
            ledger_repo=ledger_repo,
            bankroll_repo=bankroll_repo,
            cash_table_repo=cash_table_repo,
            db_path=db_path,
            now=anchor,
        )

        assert data['actual_totals']['player_bankrolls'] == 500
        assert data['actual_totals']['ai_bankrolls_stored'] == 3000
        assert data['actual_totals']['ai_bankrolls_projected'] == 3000
        assert data['actual_totals']['uncommitted_ai_regen'] == 0
        assert data['actual_totals']['cash_table_seats_ai'] == 300
        assert data['actual_totals']['active_loans_principal'] == 200
        assert data['actual_totals']['actual_outstanding'] == 4000

    def test_drift_zero_when_ledger_matches_actual(self, repos, db_path):
        bankroll_repo, cash_table_repo, ledger_repo = repos
        # Seed: 200 chips created (player_seed), 200 in player bankroll.
        bankroll_repo.save_player_bankroll(PlayerBankrollState(
            player_id='alice', chips=200, starting_bankroll=200,
        ))
        ledger_repo.record('central_bank', 'player:alice', 200, 'player_seed')

        now = datetime(2026, 5, 18, 12, 0, 0)
        data = compute_audit(
            ledger_repo=ledger_repo,
            bankroll_repo=bankroll_repo,
            cash_table_repo=cash_table_repo,
            db_path=db_path,
            now=now,
        )

        assert data['drift'] == 0

    def test_drift_nonzero_on_simulated_bypass(self, repos, db_path):
        """If chips appear in actual state without a matching ledger
        entry (the bug the audit is meant to surface), drift goes
        negative — ledger says fewer outstanding than reality."""
        bankroll_repo, cash_table_repo, ledger_repo = repos
        # Player bankroll seeded silently — no ledger entry.
        bankroll_repo.save_player_bankroll(PlayerBankrollState(
            player_id='alice', chips=200, starting_bankroll=200,
        ))

        now = datetime(2026, 5, 18, 12, 0, 0)
        data = compute_audit(
            ledger_repo=ledger_repo,
            bankroll_repo=bankroll_repo,
            cash_table_repo=cash_table_repo,
            db_path=db_path,
            now=now,
        )

        # Ledger outstanding = 0; actual = 200. drift = -200.
        assert data['drift'] == -200

    def test_window_24h_includes_recent_excludes_old(self, repos, db_path):
        bankroll_repo, cash_table_repo, ledger_repo = repos
        anchor = datetime(2026, 5, 18, 12, 0, 0)

        # Old entry: 3 days before anchor.
        old_iso = (anchor - timedelta(days=3)).isoformat()
        with ledger_repo._get_connection() as conn:
            conn.execute(
                "INSERT INTO chip_ledger_entries "
                "(created_at, source, sink, amount, reason) "
                "VALUES (?, ?, ?, ?, ?)",
                (old_iso, 'central_bank', 'player:a', 100, 'player_seed'),
            )
        # Recent entry: 1 hour before anchor.
        recent_iso = (anchor - timedelta(hours=1)).isoformat()
        with ledger_repo._get_connection() as conn:
            conn.execute(
                "INSERT INTO chip_ledger_entries "
                "(created_at, source, sink, amount, reason) "
                "VALUES (?, ?, ?, ?, ?)",
                (recent_iso, 'central_bank', 'player:b', 200, 'player_seed'),
            )

        data = compute_audit(
            ledger_repo=ledger_repo,
            bankroll_repo=bankroll_repo,
            cash_table_repo=cash_table_repo,
            db_path=db_path,
            now=anchor,
        )

        assert data['by_reason']['player_seed'] == 300  # both counted overall
        assert data['by_reason_window_24h']['player_seed'] == 200  # only recent

    def test_annotation_rows_dont_skew_totals(self, repos, db_path):
        bankroll_repo, cash_table_repo, ledger_repo = repos
        ledger_repo.record('central_bank', 'player:a', 200, 'house_loan_issue')
        ledger_repo.record('player:a', 'central_bank', 50, 'house_loan_settle')
        ledger_repo.record('player:a', 'central_bank', 0, 'forgive_balance',
                           context={'forgiven_principal': 150})

        now = datetime(2026, 5, 18, 12, 0, 0)
        data = compute_audit(
            ledger_repo=ledger_repo,
            bankroll_repo=bankroll_repo,
            cash_table_repo=cash_table_repo,
            db_path=db_path,
            now=now,
        )

        # Creation 200, destruction 50, annotation amount=0 doesn't shift totals.
        assert data['ledger_totals']['chips_created'] == 200
        assert data['ledger_totals']['chips_destroyed'] == 50
        assert data['by_reason']['forgive_balance'] == 0  # annotation visible but neutral

    def test_live_session_ai_stacks_summed(self, repos, db_path):
        bankroll_repo, cash_table_repo, ledger_repo = repos

        # Fake game_state_service: one cash session with 3 AI seats.
        class FakePlayer:
            def __init__(self, stack, is_human=False):
                self.stack = stack
                self.is_human = is_human

        class FakeGameState:
            players = [
                FakePlayer(1000),
                FakePlayer(500),
                FakePlayer(2000),
                FakePlayer(800, is_human=True),  # excluded
            ]

        class FakeStateMachine:
            game_state = FakeGameState()

        games = {'cash-abc': {'state_machine': FakeStateMachine()}}

        def list_ids():
            return list(games.keys())

        def get_game(gid):
            return games.get(gid)

        now = datetime(2026, 5, 18, 12, 0, 0)
        data = compute_audit(
            ledger_repo=ledger_repo,
            bankroll_repo=bankroll_repo,
            cash_table_repo=cash_table_repo,
            db_path=db_path,
            list_game_ids_fn=list_ids,
            get_game_fn=get_game,
            now=now,
        )

        # Excludes the human (800), includes 3 AI: 1000+500+2000.
        assert data['actual_totals']['live_session_ai_stacks'] == 3500

    def test_non_cash_games_excluded(self, repos, db_path):
        bankroll_repo, cash_table_repo, ledger_repo = repos

        class FakePlayer:
            def __init__(self, stack, is_human=False):
                self.stack = stack
                self.is_human = is_human

        class FakeGameState:
            players = [FakePlayer(5000)]

        class FakeStateMachine:
            game_state = FakeGameState()

        games = {
            'tournament-xyz': {'state_machine': FakeStateMachine()},
            'cash-abc': {'state_machine': FakeStateMachine()},
        }

        def list_ids():
            return list(games.keys())

        def get_game(gid):
            return games.get(gid)

        data = compute_audit(
            ledger_repo=ledger_repo,
            bankroll_repo=bankroll_repo,
            cash_table_repo=cash_table_repo,
            db_path=db_path,
            list_game_ids_fn=list_ids,
            get_game_fn=get_game,
            now=datetime(2026, 5, 18, 12, 0, 0),
        )

        # Only the cash- game's 5000 counts.
        assert data['actual_totals']['live_session_ai_stacks'] == 5000
