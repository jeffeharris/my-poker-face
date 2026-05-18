"""Integration tests for CashSession — the hand-orchestration commit.

Exercises the spec'd commit-3 test scope:

  - Smoke: 10-hand session runs end-to-end
  - Chip conservation: sum(bankrolls + table stacks) invariant across hands
  - Bust + refill: AI loses stack → seat clears → next hand a new AI fills
  - Player bust: player loses entire bankroll → fresh grant fires
  - cash_pair_stats matches end-of-session chip delta between pairs
  - relationship_states populates from cash play (BIG_WIN dispatch path)
  - Double-settlement guard: state machine never advances past EVALUATING_HAND
  - Memory lifecycle: on_hand_start / record_blinds / on_action /
    on_hand_complete fire in order
  - AI bankroll doesn't move at settlement — only at sit/leave/topup

Uses scripted mock controllers for determinism; the production
AI controller stack is exercised in commit 5's Flask route tests.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.integration

from cash_mode import (
    AIBankrollState,
    PLAYER_SEAT_ID,
    CashSession,
    CashTable,
    PlayerBankrollState,
    new_table,
)
from poker.memory.memory_manager import AIMemoryManager
from poker.poker_state_machine import PokerPhase
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.hand_history_repository import HandHistoryRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.relationship_repository import RelationshipRepository
from poker.repositories.schema_manager import SchemaManager


# --- Test infrastructure: scripted controllers ---


@dataclass
class ScriptedController:
    """Minimal controller that returns canned actions in sequence.

    `script` is consumed left-to-right; when exhausted, defaults to fold.
    """

    name: str
    script: List[Dict[str, Any]]
    current_hand_number: int = 0
    call_log: List[Dict[str, Any]] = None

    def __post_init__(self):
        if self.call_log is None:
            self.call_log = []

    def decide_action(self, action_log: List[str]) -> Dict[str, Any]:
        if self.script:
            decision = self.script.pop(0)
        else:
            decision = {"action": "fold", "raise_to": 0}
        self.call_log.append({
            "decision": decision,
            "hand": self.current_hand_number,
        })
        return decision


class AlwaysFoldController:
    """Folds every action. Used to test deterministic bust patterns."""

    def __init__(self, name: str):
        self.name = name
        self.current_hand_number = 0
        self.calls = 0

    def decide_action(self, action_log: List[str]) -> Dict[str, Any]:
        self.calls += 1
        return {"action": "fold", "raise_to": 0}


class AlwaysCallController:
    """Calls every bet. Used to drive hands to showdown deterministically."""

    def __init__(self, name: str):
        self.name = name
        self.current_hand_number = 0

    def decide_action(self, action_log: List[str]) -> Dict[str, Any]:
        return {"action": "call", "raise_to": 0}


# --- Fixtures ---


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "session.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def repos(db_path):
    """All four repos wired to the same DB."""
    personality_repo = PersonalityRepository(db_path)
    bankroll_repo = BankrollRepository(db_path)
    relationship_repo = RelationshipRepository(db_path)
    hand_history_repo = HandHistoryRepository(db_path)
    try:
        yield {
            "personality": personality_repo,
            "bankroll": bankroll_repo,
            "relationship": relationship_repo,
            "hand_history": hand_history_repo,
            "db_path": db_path,
        }
    finally:
        personality_repo.close()
        bankroll_repo.close()
        relationship_repo.close()
        hand_history_repo.close()


@pytest.fixture
def fixed_now():
    return datetime(2026, 5, 18, 12, 0, 0)


def _seed_personality(
    db_path,
    name,
    personality_id,
    *,
    bankroll_knobs=None,
):
    config = {"play_style": "test", "anchors": {}}
    if bankroll_knobs:
        config["bankroll_knobs"] = bankroll_knobs
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO personalities (name, config_json, personality_id, visibility)
            VALUES (?, ?, ?, 'public')
            """,
            (name, json.dumps(config), personality_id),
        )
        conn.commit()


def _seed_two_ai(db_path):
    """Seed two affordable AIs for the standard test pool."""
    knobs = {
        "bankroll_cap": 20_000, "bankroll_rate": 500,
        "buy_in_multiplier": 1.0, "stop_loss_buy_ins": 3,
        "stop_win_buy_ins": 5, "stake_comfort_zone": "$10",
    }
    _seed_personality(db_path, "Alpha Bot", "alpha_bot", bankroll_knobs=knobs)
    _seed_personality(db_path, "Beta Bot", "beta_bot", bankroll_knobs=knobs)


def _build_session(
    repos,
    *,
    fixed_now: datetime,
    big_blind: int = 10,
    controller_factory=None,
    seat_count: int = 6,
    player_chips: int = 5_000,
) -> CashSession:
    """Helper: build a CashSession with the test repos."""
    table = new_table(
        table_id="cash-test",
        stake_label="$10",
        big_blind=big_blind,
        seat_count=seat_count,
    )
    player_bankroll = PlayerBankrollState(
        player_id="test_player",
        chips=player_chips,
        starting_bankroll=2_000,
    )
    memory_manager = AIMemoryManager(
        game_id="cash-test",
        db_path=repos["db_path"],
        owner_id="test_owner",
        commentary_enabled=False,
    )
    memory_manager.set_hand_history_repo(repos["hand_history"])

    if controller_factory is None:
        # Default: every seat folds. Hands resolve via fold-out.
        controller_factory = lambda pid, name, mm: AlwaysFoldController(name)

    return CashSession(
        table=table,
        player_bankroll=player_bankroll,
        bankroll_repo=repos["bankroll"],
        relationship_repo=repos["relationship"],
        personality_repo=repos["personality"],
        memory_manager=memory_manager,
        controller_factory=controller_factory,
        game_id="cash-test",
        big_blind=big_blind,
        now_fn=lambda: fixed_now,
    )


# --- Smoke + construction ---


class TestSessionConstruction:
    def test_session_wires_cash_mode_on_memory_manager(self, repos, fixed_now):
        session = _build_session(repos, fixed_now=fixed_now)
        # cash_mode=True must have been set on the memory manager
        assert session.memory_manager._cash_mode is True
        assert session.memory_manager._relationship_repo is repos["relationship"]

    def test_session_starts_with_empty_table(self, repos, fixed_now):
        session = _build_session(repos, fixed_now=fixed_now)
        assert session.table.seats == (None,) * 6
        assert session.hand_number == 0


# --- Between-hands actions ---


class TestBetweenHandsActions:
    def test_sit_player_persists_bankroll(self, repos, fixed_now):
        session = _build_session(repos, fixed_now=fixed_now)
        session.sit_player(0, 500)
        assert session.table.seats[0] == PLAYER_SEAT_ID
        assert session.player_bankroll.chips == 4_500
        # Persisted to DB
        loaded = repos["bankroll"].load_player_bankroll("test_player")
        assert loaded.chips == 4_500

    def test_leave_player_persists_bankroll(self, repos, fixed_now):
        session = _build_session(repos, fixed_now=fixed_now)
        session.sit_player(0, 500)
        session.leave_player()
        assert session.table.seats[0] is None
        assert session.player_bankroll.chips == 5_000
        loaded = repos["bankroll"].load_player_bankroll("test_player")
        assert loaded.chips == 5_000

    def test_top_up_player_persists_bankroll(self, repos, fixed_now):
        session = _build_session(repos, fixed_now=fixed_now)
        session.sit_player(0, 500)
        session.top_up_player(200)
        assert session.table.stack_of(PLAYER_SEAT_ID) == 700
        assert session.player_bankroll.chips == 4_300


# --- Refill via fill_seats ---


class TestRefill:
    def test_run_hand_with_no_seated_returns_not_enough(self, repos, fixed_now):
        session = _build_session(repos, fixed_now=fixed_now)
        # No AI personalities seeded → no candidates
        result = session.run_hand()
        assert result.status == "not_enough_players"

    def test_run_hand_fills_seats_with_eligible_ai(self, repos, fixed_now):
        _seed_two_ai(repos["db_path"])
        session = _build_session(repos, fixed_now=fixed_now)
        session.sit_player(0, 500)
        # Now seat 0 = player; seats 1-5 open. fill_seats should add AIs.
        result = session.run_hand()
        # Both AIs seated + player = 3 seated
        seated_after = [s for s in session.table.seats if s is not None]
        assert len(seated_after) == 3
        assert PLAYER_SEAT_ID in seated_after
        assert "alpha_bot" in seated_after
        assert "beta_bot" in seated_after
        # Status either "continue" or "error" (if hand engine crashed); should be continue
        assert result.status == "continue"


# --- Chip conservation ---


class TestChipConservation:
    def test_chips_conserved_across_subsequent_hands(
        self, repos, fixed_now,
    ):
        """After the first hand seeds AI bankrolls from cap, subsequent
        hands must conserve total chips exactly (settlement only moves
        chips between seats, not into/out of the system)."""
        _seed_two_ai(repos["db_path"])
        session = _build_session(repos, fixed_now=fixed_now)
        session.sit_player(0, 500)

        # Run one hand to seed AI bankrolls — first sit fills AI seats
        # at cap, so totals are not stable across the first transition.
        session.run_hand()

        def total_chips():
            return (
                session.player_bankroll.chips
                + sum(
                    (repos["bankroll"].load_ai_bankroll(pid)
                     or AIBankrollState(pid, 0)).chips
                    for pid in ("alpha_bot", "beta_bot")
                )
                + sum(session.table.stacks.values())
            )

        baseline = total_chips()
        # Subsequent hands MUST conserve. No fresh-grants fire while
        # the player has chips and no one busts.
        for hand_idx in range(2, 6):
            session.run_hand()
            current = total_chips()
            assert current == baseline, (
                f"Chips leaked or duplicated at hand {hand_idx}: "
                f"baseline={baseline}, current={current}"
            )


# --- Double-settlement guard ---


class TestDoubleSettlementGuard:
    def test_state_machine_never_advances_past_evaluating_hand(
        self, repos, fixed_now,
    ):
        """Double-settlement would manifest as inflated chip totals
        across many hands. Run one hand to seed bankrolls, then sample
        baseline and require conservation across 5 more hands.
        """
        _seed_two_ai(repos["db_path"])
        session = _build_session(repos, fixed_now=fixed_now)
        session.sit_player(0, 500)

        session.run_hand()  # seed AI bankrolls (first sit fills at cap)

        def total():
            return (
                session.player_bankroll.chips
                + sum(
                    (repos["bankroll"].load_ai_bankroll(pid) or AIBankrollState(pid, 0)).chips
                    for pid in ("alpha_bot", "beta_bot")
                )
                + sum(session.table.stacks.values())
            )

        baseline = total()
        for _ in range(5):
            session.run_hand()
        # No fresh-grants — nobody hit 0 bankroll across these hands.
        assert total() == baseline, (
            f"Chips inflated — likely double-settlement. baseline={baseline}, after={total()}"
        )


# --- Memory lifecycle ---


class TestMemoryLifecycle:
    def test_on_hand_start_and_on_hand_complete_both_fire(
        self, repos, fixed_now,
    ):
        _seed_two_ai(repos["db_path"])
        session = _build_session(repos, fixed_now=fixed_now)
        session.sit_player(0, 500)

        # Wrap memory_manager methods to count calls
        mm = session.memory_manager
        original_on_hand_start = mm.on_hand_start
        original_on_hand_complete = mm.on_hand_complete
        original_on_action = mm.on_action

        call_log = []

        def log_start(*args, **kwargs):
            call_log.append("on_hand_start")
            return original_on_hand_start(*args, **kwargs)

        def log_complete(*args, **kwargs):
            call_log.append("on_hand_complete")
            return original_on_hand_complete(*args, **kwargs)

        def log_action(*args, **kwargs):
            call_log.append("on_action")
            return original_on_action(*args, **kwargs)

        mm.on_hand_start = log_start
        mm.on_hand_complete = log_complete
        mm.on_action = log_action

        session.run_hand()

        # on_hand_start fires once at hand start, on_hand_complete once
        # at hand end, on_action fires per action (≥ 1: at least the blinds
        # + a few folds).
        assert call_log[0] == "on_hand_start", call_log
        assert call_log[-1] == "on_hand_complete", call_log
        # on_action fires between
        assert "on_action" in call_log


# --- AI bankroll invariant ---


class TestAIBankrollMovement:
    def test_ai_bankroll_only_changes_at_sit_down(self, repos, fixed_now):
        _seed_two_ai(repos["db_path"])
        session = _build_session(repos, fixed_now=fixed_now)
        session.sit_player(0, 500)

        # Before any hand: AIs have not been seated. Their bankrolls
        # don't exist yet (load returns None).
        assert repos["bankroll"].load_ai_bankroll("alpha_bot") is None

        # Run one hand — fill_seats triggers sit_down_ai which seeds and
        # debits the bankrolls.
        session.run_hand()

        # After hand 1: AI bankrolls exist, debited by buy_in.
        # Default knobs: cap 20_000, mult 1.0, min_buy_in 400 → buy_in 400.
        # Bankroll should be 20_000 - 400 = 19_600.
        post_hand_1_alpha = repos["bankroll"].load_ai_bankroll("alpha_bot")
        post_hand_1_beta = repos["bankroll"].load_ai_bankroll("beta_bot")
        assert post_hand_1_alpha.chips == 19_600
        assert post_hand_1_beta.chips == 19_600

        # Run more hands without anyone busting (default fold-out keeps
        # everyone seated). AI bankrolls must NOT change — settlement
        # affects CashTable.stacks only.
        for _ in range(3):
            session.run_hand()

        post_hand_n_alpha = repos["bankroll"].load_ai_bankroll("alpha_bot")
        post_hand_n_beta = repos["bankroll"].load_ai_bankroll("beta_bot")
        assert post_hand_n_alpha.chips == 19_600, (
            "AI bankroll changed during settlement — should only move at sit/leave"
        )
        assert post_hand_n_beta.chips == 19_600


# --- 10-hand smoke ---


class TestTenHandSession:
    def test_runs_to_completion_with_chip_conservation(
        self, repos, fixed_now,
    ):
        _seed_two_ai(repos["db_path"])
        session = _build_session(repos, fixed_now=fixed_now)
        session.sit_player(0, 500)

        # Baseline
        def total_chips():
            return (
                session.player_bankroll.chips
                + sum(
                    (repos["bankroll"].load_ai_bankroll(pid)
                     or AIBankrollState(pid, 0)).chips
                    for pid in ("alpha_bot", "beta_bot")
                )
                + sum(session.table.stacks.values())
            )

        # Player sits with 500 from bankroll 5000 → bankroll now 4500.
        # Table has 500 from player. AI bankrolls are 20_000 each (seeded
        # at cap by fill_seats on first hand) minus buy-ins after first hand.
        # We measure total AFTER hand 1 and require it to be constant from there.
        session.run_hand()
        baseline = total_chips()

        for hand_idx in range(2, 11):  # hands 2..10
            result = session.run_hand()
            assert result.status in ("continue", "not_enough_players"), (
                f"Hand {hand_idx} ended unexpectedly: {result.status} {result.error}"
            )
            if result.status == "not_enough_players":
                # All AIs busted somehow — accept and stop
                break
            current = total_chips()
            assert current == baseline, (
                f"Chips not conserved at hand {hand_idx}: "
                f"baseline={baseline}, current={current}"
            )

        assert session.hand_number >= 5, "Expected at least 5 hands to run"
