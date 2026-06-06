"""Closed-loop validation for the side hustle through the lobby refresh.

Drives `refresh_unseated_tables` end-to-end: broke AIs in the idle pool
go off-grid to a side hustle and are paid up front from the bank pool at
the START pass (the expiry pass just returns them to idle). Asserts the
load-bearing invariants of CASH_MODE_SIDE_HUSTLE.md:

  - the hustle draws from the bank pool (reserves fall, never go negative),
  - broke AIs actually recover (bankrolls credited via side_hustle_earning),
  - conservation holds (audit drift == 0) with the whole loop wired.

Passive regen is off by default (the point of this work), so the side
hustle is the *only* recovery faucet here — exactly the production config.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

from cash_mode.bankroll import AIBankrollState
from cash_mode.closed_economy import compute_bank_pool_reserves, seed_bank_pool
from cash_mode.lobby import refresh_unseated_tables
from flask_app.services.chip_ledger_audit import compute_audit
from poker.repositories import create_repos

SBX = "test-hustle-loop"
T0 = datetime(2026, 5, 24, 12, 0, 0)
STARTING = 5_000


def _insert_personality(db_path: str, pid: str) -> None:
    knobs = {
        "starting_bankroll": STARTING,
        "bankroll_rate": 500,
        "buy_in_multiplier": 1.0,
        "stake_comfort_zone": "$2",
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO personalities (name, config_json, personality_id, visibility) "
            "VALUES (?, ?, ?, 'public')",
            (f"Personality {pid}", json.dumps({"bankroll_knobs": knobs}), pid),
        )
        conn.commit()


@pytest.fixture
def repos(tmp_path):
    db_path = str(tmp_path / "hustle_loop.db")
    r = create_repos(db_path)
    yield r, db_path
    for repo in r.values():
        if hasattr(repo, "close"):
            repo.close()


def _refresh(repos_dict, *, now):
    refresh_unseated_tables(
        cash_table_repo=repos_dict["cash_table_repo"],
        personality_repo=repos_dict["personality_repo"],
        bankroll_repo=repos_dict["bankroll_repo"],
        chip_ledger_repo=repos_dict["chip_ledger_repo"],
        side_hustle_repo=repos_dict["side_hustle_state_repo"],
        sandbox_id=SBX,
        now=now,
        rng=__import__("random").Random(1234),
    )


def _audit_drift(repos_dict, db_path):
    audit = compute_audit(
        ledger_repo=repos_dict["chip_ledger_repo"],
        bankroll_repo=repos_dict["bankroll_repo"],
        cash_table_repo=repos_dict["cash_table_repo"],
        stake_repo=repos_dict["stake_repo"],
        db_path=db_path,
        sandbox_id=SBX,
    )
    return audit["drift"]


def test_broke_ais_hustle_and_recover_from_pool(repos, seed_idle):
    repos_dict, db_path = repos
    bankroll = repos_dict["bankroll_repo"]
    ledger = repos_dict["chip_ledger_repo"]
    cash_table = repos_dict["cash_table_repo"]
    side_hustle = repos_dict["side_hustle_state_repo"]

    # Three broke AIs (10 chips << the $2 table's 80-chip min buy-in), so
    # none can afford to play anywhere → all are side-hustle candidates.
    # Seed WITH the ledger so ai_seed creations match the bankrolls
    # (drift starts at 0).
    pids = ["broke_a", "broke_b", "broke_c"]
    for pid in pids:
        _insert_personality(db_path, pid)
        bankroll.save_ai_bankroll(
            AIBankrollState(personality_id=pid, chips=10, last_regen_tick=T0),
            sandbox_id=SBX,
            chip_ledger_repo=ledger,
        )
        seed_idle(cash_table, pid, sandbox_id=SBX, reason="forced_leave", left_at=T0)

    # Fund the bank pool so the hustle has chips to draw.
    seed_bank_pool(ledger, sandbox_id=SBX, amount=50_000)
    pool_before = compute_bank_pool_reserves(ledger, sandbox_id=SBX)
    assert pool_before == 50_000
    assert _audit_drift(repos_dict, db_path) == 0

    # --- Tick 1: start pass sends broke AIs off-grid AND pays them up front. ---
    _refresh(repos_dict, now=T0)
    active = side_hustle.active_pids(sandbox_id=SBX, now=T0)
    assert len(active) >= 1, "at least one broke AI should be on a hustle"
    # Paid up front at START — active hustlers already hold their earnings.
    for pid in active:
        assert bankroll.load_ai_bankroll(pid, sandbox_id=SBX).chips > 10
    creations = ledger.sum_creations_by_reason(sandbox_id=SBX)
    assert (
        creations.get("side_hustle_earning", 0) > 0
    ), "the hustle should have drawn a pool-funded payout at start"
    # Pool drawn down at start, never negative.
    pool_after_start = compute_bank_pool_reserves(ledger, sandbox_id=SBX)
    assert 0 <= pool_after_start < pool_before
    assert _audit_drift(repos_dict, db_path) == 0

    # --- Tick 2 (5h later): the started hustles expire — return to idle, no
    # new chips move at expiry. ---
    later = T0 + timedelta(hours=5)
    _refresh(repos_dict, now=later)

    # At least one AI recovered above its broke starting point and KEPT it.
    recovered = [pid for pid in pids if bankroll.load_ai_bankroll(pid, sandbox_id=SBX).chips > 10]
    assert recovered, "a hustling AI should return with chips"

    # Pool never went negative across the whole loop.
    pool_after = compute_bank_pool_reserves(ledger, sandbox_id=SBX)
    assert 0 <= pool_after < pool_before

    # Conservation: the whole loop is ledgered, so drift stays 0.
    assert _audit_drift(repos_dict, db_path) == 0


def test_empty_pool_blocks_payout_but_keeps_drift_zero(repos, seed_idle):
    """With no pool funding, a hustle returns empty-handed — the broke AI
    stays broke (real scarcity) and conservation still holds."""
    repos_dict, db_path = repos
    bankroll = repos_dict["bankroll_repo"]
    ledger = repos_dict["chip_ledger_repo"]
    cash_table = repos_dict["cash_table_repo"]

    _insert_personality(db_path, "broke_solo")
    bankroll.save_ai_bankroll(
        AIBankrollState(personality_id="broke_solo", chips=10, last_regen_tick=T0),
        sandbox_id=SBX,
        chip_ledger_repo=ledger,
    )
    seed_idle(cash_table, "broke_solo", sandbox_id=SBX, reason="forced_leave", left_at=T0)
    # No seed_bank_pool — the pool is empty.

    _refresh(repos_dict, now=T0)
    _refresh(repos_dict, now=T0 + timedelta(hours=5))

    # No payout possible (pool dry) → AI stays broke.
    assert bankroll.load_ai_bankroll("broke_solo", sandbox_id=SBX).chips == 10
    creations = ledger.sum_creations_by_reason(sandbox_id=SBX)
    assert creations.get("side_hustle_earning", 0) == 0
    # Conservation holds regardless.
    assert _audit_drift(repos_dict, db_path) == 0
