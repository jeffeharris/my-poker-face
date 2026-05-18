"""Tests for cash_mode.seat_filler.fill_seats.

Verifies the per-spec algorithm: deterministic ordering by
personality_id, eligibility gated on projected bankroll vs
min_buy_in × buy_in_multiplier, display-name-collision skip,
first-sit seeding at bankroll_cap, persisted AIBankrollState on
each sit.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

from cash_mode import (
    AIBankrollState,
    PLAYER_SEAT_ID,
    fill_seats,
    new_table,
    sit_down,
    PlayerBankrollState,
)
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.schema_manager import SchemaManager


# --- Fixtures ---


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "cash.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def personality_repo(db_path):
    r = PersonalityRepository(db_path)
    yield r
    r.close()


@pytest.fixture
def bankroll_repo(db_path):
    r = BankrollRepository(db_path)
    yield r
    r.close()


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 18, 12, 0, 0)


@pytest.fixture
def cash_table():
    # $10 table: BB=10, min=400, max=1000
    return new_table(
        table_id="cash-1",
        stake_label="$10",
        big_blind=10,
        seat_count=6,
    )


def _seed_personality(
    db_path, *, name, personality_id, bankroll_knobs=None, visibility="public",
):
    config = {}
    if bankroll_knobs is not None:
        config["bankroll_knobs"] = bankroll_knobs
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO personalities (name, config_json, personality_id, visibility)
            VALUES (?, ?, ?, ?)
            """,
            (name, json.dumps(config), personality_id, visibility),
        )
        conn.commit()


# --- Tests ---


class TestFillSeats:
    def test_empty_db_no_change(self, cash_table, personality_repo, bankroll_repo, now):
        result = fill_seats(
            cash_table,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            now=now,
        )
        assert result.seats == (None,) * 6
        assert result.stacks == {}

    def test_full_table_no_change(self, cash_table, personality_repo, bankroll_repo, now):
        # Fill all 6 seats first (synthetic — bypassing real seating)
        from cash_mode.table import CashTable
        full = CashTable(
            table_id=cash_table.table_id,
            stake_label=cash_table.stake_label,
            big_blind=cash_table.big_blind,
            min_buy_in=cash_table.min_buy_in,
            max_buy_in=cash_table.max_buy_in,
            seat_count=cash_table.seat_count,
            seats=tuple(f"p{i}" for i in range(6)),
            stacks={f"p{i}": 500 for i in range(6)},
        )
        result = fill_seats(
            full,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            now=now,
        )
        assert result == full

    def test_fills_one_open_seat_with_first_sit_seed(
        self, cash_table, db_path, personality_repo, bankroll_repo, now,
    ):
        # Seed one personality with knobs (cap 10k, default multiplier 1.0)
        _seed_personality(
            db_path,
            name="Bob Ross",
            personality_id="bob_ross",
            bankroll_knobs={
                "bankroll_cap": 10_000, "bankroll_rate": 500,
                "buy_in_multiplier": 1.0, "stop_loss_buy_ins": 3,
                "stop_win_buy_ins": 5, "stake_comfort_zone": "$10",
            },
        )
        result = fill_seats(
            cash_table,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            now=now,
        )
        # Bob Ross filled seat 0 (lowest open index, only candidate)
        assert result.seats[0] == "bob_ross"
        # Buy-in = min_buy_in * multiplier = 400 * 1.0 = 400, capped at max=1000
        assert result.stack_of("bob_ross") == 400
        # Other seats still empty
        assert result.seats[1:] == (None,) * 5

        # AI bankroll persisted: cap (10_000) minus buy_in (400)
        loaded = bankroll_repo.load_ai_bankroll("bob_ross")
        assert loaded is not None
        assert loaded.chips == 9_600
        assert loaded.last_regen_tick == now

    def test_fills_seats_in_ascending_order_by_personality_id(
        self, cash_table, db_path, personality_repo, bankroll_repo, now,
    ):
        # Three candidates — should fill seats 0, 1, 2 in personality_id order
        for pid, name in [
            ("zeus", "Zeus"),
            ("abraham_lincoln", "Abraham Lincoln"),
            ("napoleon", "Napoleon"),
        ]:
            _seed_personality(
                db_path, name=name, personality_id=pid,
                bankroll_knobs={
                    "bankroll_cap": 10_000, "bankroll_rate": 500,
                    "buy_in_multiplier": 1.0, "stop_loss_buy_ins": 3,
                    "stop_win_buy_ins": 5, "stake_comfort_zone": "$10",
                },
            )
        result = fill_seats(
            cash_table,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            now=now,
        )
        # Personality IDs sorted ascending: abraham_lincoln, napoleon, zeus
        assert result.seats[0] == "abraham_lincoln"
        assert result.seats[1] == "napoleon"
        assert result.seats[2] == "zeus"
        assert result.seats[3:] == (None, None, None)

    def test_skips_personality_with_insufficient_bankroll(
        self, cash_table, db_path, personality_repo, bankroll_repo, now,
    ):
        # Bob has tiny cap < min_buy_in (400). Should be skipped.
        _seed_personality(
            db_path, name="Broke Bob", personality_id="broke_bob",
            bankroll_knobs={
                "bankroll_cap": 100, "bankroll_rate": 10,
                "buy_in_multiplier": 1.0, "stop_loss_buy_ins": 1,
                "stop_win_buy_ins": 2, "stake_comfort_zone": "$2",
            },
        )
        _seed_personality(
            db_path, name="Rich Rita", personality_id="rich_rita",
            bankroll_knobs={
                "bankroll_cap": 50_000, "bankroll_rate": 1_000,
                "buy_in_multiplier": 1.0, "stop_loss_buy_ins": 3,
                "stop_win_buy_ins": 5, "stake_comfort_zone": "$50",
            },
        )
        result = fill_seats(
            cash_table,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            now=now,
        )
        assert result.seats[0] == "rich_rita"
        assert "broke_bob" not in result.seats

    def test_skips_personality_already_seated(
        self, cash_table, db_path, personality_repo, bankroll_repo, now,
    ):
        # Bob Ross already at seat 0
        _seed_personality(
            db_path, name="Bob Ross", personality_id="bob_ross",
            bankroll_knobs={
                "bankroll_cap": 10_000, "bankroll_rate": 500,
                "buy_in_multiplier": 1.0, "stop_loss_buy_ins": 3,
                "stop_win_buy_ins": 5, "stake_comfort_zone": "$10",
            },
        )
        # Place bob_ross at seat 0 manually
        table_with_bob = cash_table.with_seat(0, "bob_ross").with_stack("bob_ross", 500)
        # No other personalities; nothing more should fill
        result = fill_seats(
            table_with_bob,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            now=now,
        )
        # Bob still in seat 0; seats 1-5 still None
        assert result.seats[0] == "bob_ross"
        assert result.seats[1:] == (None,) * 5

    def test_skips_player_seat(
        self, cash_table, db_path, personality_repo, bankroll_repo, now,
    ):
        # The human seated at seat 0; filler should not touch it.
        player = PlayerBankrollState(player_id="alice", chips=2_000, starting_bankroll=2_000)
        table_with_player, _ = sit_down(cash_table, 0, PLAYER_SEAT_ID, 500, player)

        _seed_personality(
            db_path, name="Napoleon", personality_id="napoleon",
            bankroll_knobs={
                "bankroll_cap": 80_000, "bankroll_rate": 2_200,
                "buy_in_multiplier": 1.0, "stop_loss_buy_ins": 8,
                "stop_win_buy_ins": None, "stake_comfort_zone": "$200",
            },
        )
        result = fill_seats(
            table_with_player,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            now=now,
        )
        # Seat 0 still has player; Napoleon takes seat 1
        assert result.seats[0] == PLAYER_SEAT_ID
        assert result.seats[1] == "napoleon"

    def test_uses_personality_specific_buy_in_multiplier(
        self, cash_table, db_path, personality_repo, bankroll_repo, now,
    ):
        # Napoleon's buy_in_multiplier = 2.3 → buy_in = 400 * 2.3 = 920
        _seed_personality(
            db_path, name="Napoleon", personality_id="napoleon",
            bankroll_knobs={
                "bankroll_cap": 80_000, "bankroll_rate": 2_200,
                "buy_in_multiplier": 2.3, "stop_loss_buy_ins": 8,
                "stop_win_buy_ins": None, "stake_comfort_zone": "$200",
            },
        )
        result = fill_seats(
            cash_table,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            now=now,
        )
        assert result.seats[0] == "napoleon"
        # int(400 * 2.3) = 920, under max_buy_in 1000
        assert result.stack_of("napoleon") == 920

    def test_clamps_buy_in_to_max(
        self, cash_table, db_path, personality_repo, bankroll_repo, now,
    ):
        # Multiplier 3.0 would give 1200, but max_buy_in is 1000 — clamp.
        _seed_personality(
            db_path, name="Zeus", personality_id="zeus",
            bankroll_knobs={
                "bankroll_cap": 200_000, "bankroll_rate": 3_500,
                "buy_in_multiplier": 3.0, "stop_loss_buy_ins": None,
                "stop_win_buy_ins": None, "stake_comfort_zone": "$1000",
            },
        )
        result = fill_seats(
            cash_table,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            now=now,
        )
        assert result.stack_of("zeus") == 1_000  # capped

    def test_uses_projected_bankroll_for_re_sit(
        self, cash_table, db_path, personality_repo, bankroll_repo, now,
    ):
        # Personality with knobs (cap 10k, rate 500), stored at 200 chips
        # 4 days ago. Projected = 200 + 4*500 = 2200 — affordable.
        _seed_personality(
            db_path, name="Bob Ross", personality_id="bob_ross",
            bankroll_knobs={
                "bankroll_cap": 10_000, "bankroll_rate": 500,
                "buy_in_multiplier": 1.0, "stop_loss_buy_ins": 3,
                "stop_win_buy_ins": 5, "stake_comfort_zone": "$10",
            },
        )
        bankroll_repo.save_ai_bankroll(
            AIBankrollState("bob_ross", chips=200, last_regen_tick=now - timedelta(days=4))
        )
        result = fill_seats(
            cash_table,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            now=now,
        )
        assert result.seats[0] == "bob_ross"
        # Bankroll: 2200 (projected) - 400 (buy_in) = 1800
        loaded = bankroll_repo.load_ai_bankroll("bob_ross")
        assert loaded.chips == 1_800
        assert loaded.last_regen_tick == now

    def test_skips_personality_when_projected_still_insufficient(
        self, cash_table, db_path, personality_repo, bankroll_repo, now,
    ):
        # Cap 1000 (below min_buy_in 400 * mult... no wait, mult=1.0 → 400, so
        # threshold is 400). Stored 50 chips 1 day ago, rate 100 → projected
        # = 50 + 100 = 150. Still below 400 — skip.
        _seed_personality(
            db_path, name="Broke Bob", personality_id="broke_bob",
            bankroll_knobs={
                "bankroll_cap": 1_000, "bankroll_rate": 100,
                "buy_in_multiplier": 1.0, "stop_loss_buy_ins": 1,
                "stop_win_buy_ins": 2, "stake_comfort_zone": "$2",
            },
        )
        bankroll_repo.save_ai_bankroll(
            AIBankrollState("broke_bob", chips=50, last_regen_tick=now - timedelta(days=1))
        )
        result = fill_seats(
            cash_table,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            now=now,
        )
        assert "broke_bob" not in result.seats

    def test_excludes_disabled_personalities(
        self, cash_table, db_path, personality_repo, bankroll_repo, now,
    ):
        _seed_personality(
            db_path, name="Banned Bob", personality_id="banned_bob",
            visibility="disabled",
            bankroll_knobs={
                "bankroll_cap": 10_000, "bankroll_rate": 500,
                "buy_in_multiplier": 1.0, "stop_loss_buy_ins": 3,
                "stop_win_buy_ins": 5, "stake_comfort_zone": "$10",
            },
        )
        _seed_personality(
            db_path, name="Bob Ross", personality_id="bob_ross",
            bankroll_knobs={
                "bankroll_cap": 10_000, "bankroll_rate": 500,
                "buy_in_multiplier": 1.0, "stop_loss_buy_ins": 3,
                "stop_win_buy_ins": 5, "stake_comfort_zone": "$10",
            },
        )
        result = fill_seats(
            cash_table,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            now=now,
        )
        # Only Bob Ross seated; banned Bob excluded by visibility filter
        assert "banned_bob" not in result.seats
        assert result.seats[0] == "bob_ross"

    def test_runs_out_of_candidates_leaves_seats_empty(
        self, cash_table, db_path, personality_repo, bankroll_repo, now,
    ):
        # Only one candidate, six open seats. Should fill seat 0 and leave 1-5.
        _seed_personality(
            db_path, name="Lone Wolf", personality_id="lone_wolf",
            bankroll_knobs={
                "bankroll_cap": 10_000, "bankroll_rate": 500,
                "buy_in_multiplier": 1.0, "stop_loss_buy_ins": 3,
                "stop_win_buy_ins": 5, "stake_comfort_zone": "$10",
            },
        )
        result = fill_seats(
            cash_table,
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            now=now,
        )
        assert result.seats[0] == "lone_wolf"
        assert result.seats[1:] == (None,) * 5
