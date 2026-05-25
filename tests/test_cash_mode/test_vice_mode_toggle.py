"""The 3-state vice-mode toggle is mutually exclusive.

`refresh_unseated_tables(vice_mode=...)` runs *exactly one* vice mechanism
(or none): 'real' → real LLM-narrated vice, 'fake' → the sim stub, 'off' →
neither. A single value makes it impossible for both to drain rich AIs at
once (the double-drain bug) or to silently both be off. The live default
comes from `economy_flags.VICE_MODE`; the sim forces 'fake'.

We mock the two resolver entry points and assert which got called — no
LLM, no chip-state setup needed.
"""

from __future__ import annotations

import json
import sqlite3
import random
from datetime import datetime
from unittest import mock

import pytest

pytestmark = pytest.mark.integration

from cash_mode.lobby import (
    ensure_ai_bankrolls_seeded,
    ensure_lobby_seeded,
    refresh_unseated_tables,
)
from poker.repositories import create_repos

SBX = "vice-mode-sbx"
NOW = datetime(2026, 5, 25, 12, 0, 0)


def _insert_personality(db_path: str, pid: str) -> None:
    knobs = {
        "starting_bankroll": 50_000, "bankroll_rate": 50,
        "buy_in_multiplier": 1.0, "stake_comfort_zone": "$10",
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
    db = str(tmp_path / "vm.db")
    r = create_repos(db)
    for i in range(12):
        _insert_personality(db, f"p{i}")
    ensure_ai_bankrolls_seeded(
        personality_repo=r["personality_repo"], bankroll_repo=r["bankroll_repo"],
        sandbox_id=SBX, chip_ledger_repo=r["chip_ledger_repo"],
    )
    ensure_lobby_seeded(
        cash_table_repo=r["cash_table_repo"], personality_repo=r["personality_repo"],
        bankroll_repo=r["bankroll_repo"], sandbox_id=SBX,
    )
    yield r
    for repo in r.values():
        if hasattr(repo, "close"):
            repo.close()


def _run(repos, **kwargs):
    """Run one refresh with the two vice resolvers mocked; return
    (real_called, fake_called)."""
    with mock.patch(
        "cash_mode.ai_vice_spending.resolve_ai_vice_spending", return_value=[],
    ) as m_real, mock.patch(
        "cash_mode.closed_economy.resolve_closed_economy",
    ) as m_fake:
        refresh_unseated_tables(
            cash_table_repo=repos["cash_table_repo"],
            personality_repo=repos["personality_repo"],
            bankroll_repo=repos["bankroll_repo"],
            chip_ledger_repo=repos["chip_ledger_repo"],
            vice_repo=repos["vice_state_repo"],
            side_hustle_repo=repos["side_hustle_state_repo"],
            sandbox_id=SBX, now=NOW, rng=random.Random(1),
            **kwargs,
        )
    return m_real.called, m_fake.called


def test_real_mode_runs_only_real_vice(repos):
    real, fake = _run(repos, vice_mode="real")
    assert real and not fake


def test_fake_mode_runs_only_fake_vice(repos):
    real, fake = _run(repos, vice_mode="fake")
    assert fake and not real


def test_off_mode_runs_neither(repos):
    real, fake = _run(repos, vice_mode="off")
    assert not real and not fake


def test_default_falls_back_to_economy_flags_vice_mode(repos, monkeypatch):
    # No vice_mode kwarg → uses economy_flags.VICE_MODE.
    monkeypatch.setattr("cash_mode.economy_flags.VICE_MODE", "fake")
    real, fake = _run(repos)
    assert fake and not real

    monkeypatch.setattr("cash_mode.economy_flags.VICE_MODE", "real")
    real, fake = _run(repos)
    assert real and not fake


def test_unknown_mode_runs_neither(repos):
    # A bad value falls through both gates — safe (no vice), not a crash.
    real, fake = _run(repos, vice_mode="bogus")
    assert not real and not fake
