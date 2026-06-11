"""Intake world warm-up burst — conservation, reach, one-shot, gating.

Covers `flask_app/services/world_warmup.py`: the warm-up plays real off-screen
hands across the hidden lobby tables (so reads form + the economy moves) while
CONSERVING chips and never touching the pinned Scene-0 table, and is scheduled
exactly once per sandbox behind the flags.
"""

from __future__ import annotations

import pytest

from cash_mode import career_progression as cp, economy_flags
from cash_mode.lobby import ensure_ai_bankrolls_seeded, ensure_lobby_seeded

pytestmark = [pytest.mark.integration]

SANDBOX = "warmup-sb"
OWNER = "warmup-owner"


def _audit_drift(repos, sandbox_id) -> int:
    """Canonical conservation drift for the sandbox (the audit endpoint's number).

    Reconciles the ledger against bankrolls + live AI seat stacks + stakes, so it
    correctly accounts for chips sitting in seats mid-action (which a naive
    ledger-only sum misses). A conserved operation leaves drift UNCHANGED."""
    from datetime import datetime

    from flask_app.services.chip_ledger_audit import compute_audit

    audit = compute_audit(
        ledger_repo=repos["chip_ledger_repo"],
        bankroll_repo=repos["bankroll_repo"],
        cash_table_repo=repos["cash_table_repo"],
        stake_repo=repos["stake_repo"],
        db_path=repos["db_path"],
        list_game_ids_fn=None,
        get_game_fn=None,
        now=datetime.utcnow(),
        sandbox_id=sandbox_id,
    )
    return int(audit["drift"])


def _ai_table_hands(chip_ledger_repo, repos, sandbox_id):
    import sqlite3

    conn = sqlite3.connect(repos["db_path"])
    rows = conn.execute(
        "SELECT table_id, SUM(hands) FROM ai_table_hand_counts "
        "WHERE sandbox_id = ? GROUP BY table_id",
        (sandbox_id,),
    ).fetchall()
    conn.close()
    return {r[0]: int(r[1] or 0) for r in rows}


@pytest.fixture
def seeded(repos):
    """A wired, lobby-seeded sandbox with a pinned Scene-0 table.

    Snapshots + restores the flask extension globals the warm-up reads (the xdist
    import-ordering gotcha in tests/CLAUDE.md)."""
    import flask_app.extensions as ext

    repos["personality_repo"].seed_personalities_from_json("poker/personalities.json")
    ensure_ai_bankrolls_seeded(
        personality_repo=repos["personality_repo"],
        bankroll_repo=repos["bankroll_repo"],
        sandbox_id=SANDBOX,
        chip_ledger_repo=repos["chip_ledger_repo"],
    )
    ensure_lobby_seeded(
        cash_table_repo=repos["cash_table_repo"],
        personality_repo=repos["personality_repo"],
        bankroll_repo=repos["bankroll_repo"],
        sandbox_id=SANDBOX,
    )
    # Pin a Scene-0 table (Sal + the fish) — the warm-up must never touch it.
    cp.ensure_scene0_seeded(
        career_progress_repo=repos["career_progress_repo"],
        cash_table_repo=repos["cash_table_repo"],
        bankroll_repo=repos["bankroll_repo"],
        sandbox_id=SANDBOX,
        owner_id=OWNER,
        chip_ledger_repo=repos["chip_ledger_repo"],
    )

    keys = [k for k in repos if k != "db_path"]
    snapshot = {k: getattr(ext, k, None) for k in keys}
    for k in keys:
        setattr(ext, k, repos[k])
    try:
        yield repos
    finally:
        for k, v in snapshot.items():
            setattr(ext, k, v)


def test_warm_up_plays_hands_conserves_chips_and_skips_scene0(seeded):
    from flask_app.services.world_warmup import warm_up_world

    repos = seeded
    ledger = repos["chip_ledger_repo"]

    drift_before = _audit_drift(repos, SANDBOX)
    hands_before = _ai_table_hands(ledger, repos, SANDBOX)

    result = warm_up_world(SANDBOX, iterations=8, max_seconds=30.0)

    assert result["iterations_run"] >= 1
    hands_after = _ai_table_hands(ledger, repos, SANDBOX)
    hidden_hands = sum(hands_after.values()) - sum(hands_before.values())
    assert hidden_hands > 0  # reads form: real hands accrued on the hidden tables

    # Conservation (the soft spot): the burst introduces no audit drift — every
    # chip it moves is a balanced ledger transfer (no mint, no burn).
    assert _audit_drift(repos, SANDBOX) == drift_before

    # The pinned Scene-0 table is scripted → excluded from refresh → never played.
    assert hands_after.get(cp.SCENE0_TABLE_ID, 0) == 0


def test_warm_up_respects_iteration_bound(seeded):
    from flask_app.services.world_warmup import warm_up_world

    result = warm_up_world(SANDBOX, iterations=3, max_seconds=30.0)
    assert result["iterations_run"] <= 3


def test_schedule_is_one_shot_and_flag_gated(seeded, monkeypatch):
    import flask_app.extensions as ext
    from flask_app.services import world_warmup

    repos = seeded
    # Stub the background spawn so we don't actually run the burst here; just
    # record that it was scheduled.
    calls = []
    fake_socketio = type(
        "S",
        (),
        {
            "start_background_task": lambda self, *a, **k: calls.append(a),
            "sleep": lambda self, _s: None,
        },
    )()
    monkeypatch.setattr(ext, "socketio", fake_socketio, raising=False)

    monkeypatch.setattr(economy_flags, "CAREER_PROGRESSION_ENABLED", True)
    monkeypatch.setattr(economy_flags, "INTAKE_WORLD_WARMUP_ENABLED", True)

    # First schedule fires + stamps the one-shot guard.
    assert world_warmup.schedule_warm_up(SANDBOX, OWNER) is True
    assert repos["career_progress_repo"].load(SANDBOX, OWNER).world_warmed is True
    assert len(calls) == 1

    # Second schedule is a no-op (already warmed).
    assert world_warmup.schedule_warm_up(SANDBOX, OWNER) is False
    assert len(calls) == 1


def test_schedule_off_when_flags_disabled(seeded, monkeypatch):
    from flask_app.services import world_warmup

    # Master flag off → never schedules, never stamps.
    monkeypatch.setattr(economy_flags, "CAREER_PROGRESSION_ENABLED", False)
    monkeypatch.setattr(economy_flags, "INTAKE_WORLD_WARMUP_ENABLED", True)
    assert world_warmup.schedule_warm_up(SANDBOX, OWNER) is False
    assert seeded["career_progress_repo"].load(SANDBOX, OWNER).world_warmed is False

    # Warm-up flag off → also never schedules.
    monkeypatch.setattr(economy_flags, "CAREER_PROGRESSION_ENABLED", True)
    monkeypatch.setattr(economy_flags, "INTAKE_WORLD_WARMUP_ENABLED", False)
    assert world_warmup.schedule_warm_up(SANDBOX, OWNER) is False
