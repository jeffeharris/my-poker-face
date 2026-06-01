"""Window B (cold-start) partial-commit reproduction + fix gate.

`ensure_lobby_seeded` builds AI seats in memory, marks each in the
`seated_globally` set, then calls `debit_bankroll_for_seat`, and finally
`save_table` ONCE after the per-table loop.

The risk (plan §1.2 Window B, §3): `debit_bankroll_for_seat` can FAIL by
*returning None* (row missing / projected < buy-in — the audit-safe refusal
that the docstring says the caller MUST unwind a pre-placed seat for) or by
*raising*. The original code ignored the return value, so a refused debit left
a seat written + `seated_globally`-marked but the AI's bankroll un-debited →
after `save_table`, the table row shows the AI holding `ai_buy_in` seat chips
that were never pulled from any bankroll → chips minted, conservation broken.

These tests:
  - reproduce the seated-but-unfunded mint on a refused debit, and
  - pin the fixed behavior: a failed-debit AI is simply absent from the seated
    table (dropped cleanly), no chips drained without a seat, conservation
    holds; the all-funded happy path is unchanged.
"""

from __future__ import annotations

from datetime import datetime

import pytest

pytestmark = pytest.mark.integration

import cash_mode.bankroll as bankroll_mod
from cash_mode.lobby import ensure_lobby_seeded
from flask_app.services.chip_ledger_audit import compute_audit
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.cash_table_repository import CashTableRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.stake_repository import StakeRepository

SANDBOX = "test-sandbox-1"


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "lobby_seed_atomicity.db")
    SchemaManager(path).ensure_schema()
    return path


@pytest.fixture
def repos(db_path):
    return {
        "bankroll_repo": BankrollRepository(db_path),
        "cash_table_repo": CashTableRepository(db_path),
        "chip_ledger_repo": ChipLedgerRepository(db_path),
        "personality_repo": PersonalityRepository(db_path),
        "stake_repo": StakeRepository(db_path),
        "db_path": db_path,
    }


def _seed_personality(db_path, pid, name, bankroll_chips, cap=200_000, rate=500):
    import json
    import sqlite3

    config_json = json.dumps(
        {
            "bankroll_knobs": {
                "starting_bankroll": cap,
                "bankroll_rate": rate,
                "buy_in_multiplier": 1.0,
                "stake_comfort_zone": "$10",
            },
        }
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO personalities "
            "(name, personality_id, config_json, visibility, circulating) "
            "VALUES (?, ?, ?, 'public', 1)",
            (name, pid, config_json),
        )
        conn.execute(
            "INSERT INTO ai_bankroll_state "
            "(personality_id, sandbox_id, chips, last_regen_tick) "
            "VALUES (?, ?, ?, ?)",
            (pid, SANDBOX, bankroll_chips, datetime.utcnow().isoformat()),
        )


def _audit(repos):
    return compute_audit(
        ledger_repo=repos["chip_ledger_repo"],
        bankroll_repo=repos["bankroll_repo"],
        cash_table_repo=repos["cash_table_repo"],
        stake_repo=repos["stake_repo"],
        db_path=repos["db_path"],
        list_game_ids_fn=lambda: [],
        get_game_fn=lambda gid: None,
    )


def _all_seated_pids(repos):
    pids = []
    for t in repos["cash_table_repo"].list_all_tables(sandbox_id=SANDBOX):
        for slot in t.seats:
            if slot.get("kind") == "ai":
                pids.append(slot["personality_id"])
    return pids


def _seed_rich_pool(db_path, n=12):
    for i in range(n):
        _seed_personality(
            db_path,
            f"p_{i}",
            f"Pers{i}",
            bankroll_chips=200_000,
            cap=500_000,
        )


def test_refused_debit_does_not_mint_seated_but_unfunded(repos, db_path, monkeypatch):
    """A debit that REFUSES (returns None) must not leave a seated-but-unfunded AI.

    On current (buggy) code: the seat is pre-placed before the debit and the
    return value is ignored, so the refused AI is seated holding buy-in chips
    that were never debited → minted chips, conservation breaks.

    After the fix: that AI is simply absent from the table; conservation holds.
    """
    _seed_rich_pool(db_path)

    # The loop does `from cash_mode.bankroll import debit_bankroll_for_seat`
    # at call time, so patch it on its source module.
    real_debit = bankroll_mod.debit_bankroll_for_seat
    refused = {"pid": None}

    def fake_debit(bankroll_repo, pid, amount, **kwargs):
        # Refuse the FIRST AI we're asked to fund (simulates the audit-safe
        # `return None` refusal path) and keep refusing that same pid for
        # the rest of the seed (a persistently-unfundable AI), so the test
        # can assert it never ends up seated anywhere. All other AIs fund
        # for real.
        if refused["pid"] is None:
            refused["pid"] = pid
        if pid == refused["pid"]:
            return None
        return real_debit(bankroll_repo, pid, amount, **kwargs)

    monkeypatch.setattr(bankroll_mod, "debit_bankroll_for_seat", fake_debit)

    before = _audit(repos)
    ensure_lobby_seeded(
        cash_table_repo=repos["cash_table_repo"],
        personality_repo=repos["personality_repo"],
        bankroll_repo=repos["bankroll_repo"],
        chip_ledger_repo=repos["chip_ledger_repo"],
        sandbox_id=SANDBOX,
    )
    after = _audit(repos)

    refused_pid = refused["pid"]
    assert refused_pid is not None, "fixture never exercised a refused debit"

    seated = _all_seated_pids(repos)

    # The refused AI must NOT be sitting at any table (it was never funded).
    assert refused_pid not in seated, (
        f"refused-debit AI {refused_pid!r} was seated despite no chips being "
        f"debited — seated-but-unfunded mint (Window B partial commit)"
    )

    # Hard conservation gate: total chips in the universe is unchanged.
    # A seated-but-unfunded AI inflates cash_table_seats_ai without a matching
    # ai_bankrolls_stored decrease → drift moves.
    assert after["drift"] == before["drift"], (
        f"drift moved by {after['drift'] - before['drift']} — Window B minted "
        f"chips by seating an AI whose debit was refused"
    )

    # And the seat→bankroll transfer stays exactly paired.
    bankrolls_delta = (
        after["actual_totals"]["ai_bankrolls_stored"]
        - before["actual_totals"]["ai_bankrolls_stored"]
    )
    seats_delta = (
        after["actual_totals"]["cash_table_seats_ai"]
        - before["actual_totals"]["cash_table_seats_ai"]
    )
    assert bankrolls_delta == -seats_delta, (
        f"seed not a pure transfer (bankrolls Δ={bankrolls_delta}, "
        f"seats Δ={seats_delta}); refused-debit seat broke the pairing"
    )


def test_happy_path_seed_all_funded_unchanged(repos, db_path):
    """All-funded seed: every placed AI is fully debited, pure transfer.

    Guards the SUCCESS path against the fix (must stay byte-identical in
    observable outcome: AIs seated, chips moved, drift flat).
    """
    _seed_rich_pool(db_path)

    before = _audit(repos)
    ensure_lobby_seeded(
        cash_table_repo=repos["cash_table_repo"],
        personality_repo=repos["personality_repo"],
        bankroll_repo=repos["bankroll_repo"],
        chip_ledger_repo=repos["chip_ledger_repo"],
        sandbox_id=SANDBOX,
    )
    after = _audit(repos)

    seated = _all_seated_pids(repos)
    assert seated, "expected the seed to place AI seats"
    # No personality seated at more than one table.
    assert len(seated) == len(set(seated)), "an AI was seated at >1 table"

    bankrolls_delta = (
        after["actual_totals"]["ai_bankrolls_stored"]
        - before["actual_totals"]["ai_bankrolls_stored"]
    )
    seats_delta = (
        after["actual_totals"]["cash_table_seats_ai"]
        - before["actual_totals"]["cash_table_seats_ai"]
    )
    assert seats_delta > 0
    assert bankrolls_delta == -seats_delta, "happy-path seed must be a pure transfer"
    assert after["drift"] == before["drift"], "happy-path seed moved drift"
