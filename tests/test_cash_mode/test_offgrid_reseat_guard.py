"""Regression: an AI that is (or is going) off-grid must never be re-seated.

These pin the two `cash_mode/lobby.py` seat-insertion paths that could
recreate the `seated_and_offgrid` split-brain (an AI both seated at a cash
table AND carrying an active `ai_vice_state` row):

  * Fix 1 — `refresh_unseated_tables`: a `go_vice` leaver vacates its seat
    mid-refresh but its vice row isn't written until AFTER the global greedy
    fill. The fill must exclude that this-tick `all_vice_bound` pid, or it
    re-seats the AI before the vice commit lands. (The presence authority
    can't catch this: the off-grid leg is still shadow-only, so the
    START_VICE-from-SEATED transition is silently swallowed.)

  * Fix 2 — `ensure_lobby_seeded`: seeding a missing table must skip an AI
    that already has an active vice / side-hustle row. The boot/expansion
    seed previously filtered only on `seated_globally`.

Both are tested against a tempdb to stay hermetic.
"""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

from cash_mode.bankroll import AIBankrollState
from cash_mode.lobby import ensure_lobby_seeded, refresh_unseated_tables
from cash_mode.tables import CashTableState, ai_slot, open_slot
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.cash_table_repository import CashTableRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.side_hustle_state_repository import SideHustleStateRepository
from poker.repositories.vice_state_repository import ViceState, ViceStateRepository

SANDBOX = "sb-offgrid-guard"


def _insert_personality(db_path: str, pid: str, *, name: str) -> None:
    config = {
        "bankroll_knobs": {
            "starting_bankroll": 100_000,
            "bankroll_rate": 0,
            "buy_in_multiplier": 1.0,
            "stake_comfort_zone": "$2",
        }
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO personalities "
            "(name, config_json, personality_id, visibility, circulating) "
            "VALUES (?, ?, ?, 'public', 1)",
            (name, json.dumps(config), pid),
        )
        conn.commit()


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "offgrid_guard.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def repos(db_path):
    bundle = dict(
        cash_table_repo=CashTableRepository(db_path),
        bankroll_repo=BankrollRepository(db_path),
        personality_repo=PersonalityRepository(db_path),
        chip_ledger_repo=ChipLedgerRepository(db_path),
        vice_repo=ViceStateRepository(db_path),
        side_hustle_repo=SideHustleStateRepository(db_path),
    )
    yield bundle
    for r in bundle.values():
        r.close()


def _seed_bankroll(bankroll_repo, pid):
    bankroll_repo.save_ai_bankroll(
        AIBankrollState(personality_id=pid, chips=100_000, last_regen_tick=None),
        sandbox_id=SANDBOX,
    )


def _put_on_vice(vice_repo, pid, *, now):
    vice_repo.insert_vice_state(
        ViceState(
            personality_id=pid,
            sandbox_id=SANDBOX,
            started_at=now,
            ends_at=now + timedelta(hours=2),
            amount=5_000,
            duration_bucket="long",
            narration=f"{pid} is out blowing the win.",
        )
    )


def _seated_pids(cash_table_repo):
    pids: set = set()
    for t in cash_table_repo.list_all_tables(sandbox_id=SANDBOX):
        pids |= {s["personality_id"] for s in t.seats if s["kind"] == "ai"}
    return pids


# ---------------------------------------------------------------------------
# Fix 2 — ensure_lobby_seeded skips an already-off-grid AI
# ---------------------------------------------------------------------------


class TestEnsureLobbySeededSkipsOffGrid:
    def test_on_vice_ai_is_not_seeded(self, repos, db_path):
        now = datetime.utcnow()
        _insert_personality(db_path, "on_vice", name="On Vice")
        _insert_personality(db_path, "free_ai", name="Free AI")
        _seed_bankroll(repos["bankroll_repo"], "on_vice")
        _seed_bankroll(repos["bankroll_repo"], "free_ai")
        _put_on_vice(repos["vice_repo"], "on_vice", now=now)

        ensure_lobby_seeded(
            cash_table_repo=repos["cash_table_repo"],
            personality_repo=repos["personality_repo"],
            bankroll_repo=repos["bankroll_repo"],
            chip_ledger_repo=repos["chip_ledger_repo"],
            sandbox_id=SANDBOX,
            now=now,
            vice_repo=repos["vice_repo"],
            side_hustle_repo=repos["side_hustle_repo"],
        )

        seated = _seated_pids(repos["cash_table_repo"])
        assert "on_vice" not in seated  # the guard
        assert "free_ai" in seated  # control: a free AI still seats

    def test_without_repo_the_guard_is_inert(self, repos, db_path):
        # Characterizes WHY the guard is load-bearing: omit the repos and the
        # on-vice AI (the only candidate) IS seeded — the pre-fix behavior.
        now = datetime.utcnow()
        _insert_personality(db_path, "on_vice", name="On Vice")
        _seed_bankroll(repos["bankroll_repo"], "on_vice")
        _put_on_vice(repos["vice_repo"], "on_vice", now=now)

        ensure_lobby_seeded(
            cash_table_repo=repos["cash_table_repo"],
            personality_repo=repos["personality_repo"],
            bankroll_repo=repos["bankroll_repo"],
            chip_ledger_repo=repos["chip_ledger_repo"],
            sandbox_id=SANDBOX,
            now=now,
            # vice_repo / side_hustle_repo intentionally omitted
        )

        assert "on_vice" in _seated_pids(repos["cash_table_repo"])


# ---------------------------------------------------------------------------
# Fix 1 — a go_vice leaver isn't re-seated by the same-tick greedy fill
# ---------------------------------------------------------------------------


class TestGoViceLeaverNotReseated:
    def test_go_vice_ai_is_not_reseated(self, repos, db_path, monkeypatch):
        now = datetime.utcnow()
        # Two AIs so a burst hand can actually run (per-hand movement, which
        # produces the go_vice decision, only fires inside the burst loop).
        _insert_personality(db_path, "rover", name="Rover")
        _insert_personality(db_path, "anchor", name="Anchor")
        _seed_bankroll(repos["bankroll_repo"], "rover")
        _seed_bankroll(repos["bankroll_repo"], "anchor")

        table = CashTableState(
            table_id="cash-table-2-001",
            stake_label="$2",
            seats=[ai_slot("rover", 200), ai_slot("anchor", 200)] + [open_slot() for _ in range(4)],
            name="The Back Room",
        )
        repos["cash_table_repo"].save_table(table, sandbox_id=SANDBOX)

        # Force every seated AI straight to a vice this refresh. go_vice
        # deliberately adds no idle row and no per-table leave cooldown, so
        # only the all_vice_bound greedy-fill guard (the fix) keeps a vacated
        # AI from being re-seated into one of the now-open seats. Both pids are
        # the only eligible candidates, so absent the fix the fill re-seats
        # them (the split-brain precondition).
        import cash_mode.movement as movement

        monkeypatch.setattr(movement, "evaluate_ai_movement", lambda ctx, rng: "go_vice")

        refresh_unseated_tables(
            cash_table_repo=repos["cash_table_repo"],
            personality_repo=repos["personality_repo"],
            bankroll_repo=repos["bankroll_repo"],
            sandbox_id=SANDBOX,
            now=now,
            rng=random.Random(0),
            hand_sim_prob=1.0,  # run a burst hand so per-hand movement fires
            seek_rate=1.0,  # force the greedy fill to try to staff open seats
            chip_ledger_repo=repos["chip_ledger_repo"],
            vice_repo=repos["vice_repo"],
            side_hustle_repo=repos["side_hustle_repo"],
        )

        # rover left for a vice this tick → must not be re-seated by the
        # same-tick greedy fill.
        assert "rover" not in _seated_pids(repos["cash_table_repo"])
