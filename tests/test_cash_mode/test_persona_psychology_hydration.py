"""T3-77 — shared persona-psychology hydrate/flush hook.

Pins the round-trip used by the live cash seat build (two-way) and the
cash-world tournament builder: a controller's `PlayerPsychology` is hydrated
from `ai_bankroll_state.emotional_state_json` (schema v97) on a fresh seat and
flushed back at a session boundary. Best-effort no-ops (no repo / NULL column)
must leave the controller at its freshly-built baseline.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from cash_mode.psychology_persistence import (
    flush_persona_psychology,
    hydrate_persona_psychology,
)
from poker.player_psychology import PlayerPsychology
from poker.repositories import create_repos

SANDBOX = "test-sandbox-1"
PID = "napoleon"


@pytest.fixture
def repo():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        yield create_repos(db_path)["bankroll_repo"]
    finally:
        try:
            os.unlink(db_path)
        except FileNotFoundError:
            pass


def _controller(hand_count: int = 0):
    """A minimal controller stand-in: the hook only touches `.psychology` and
    `.ai_player.personality_config`."""
    psych = PlayerPsychology.from_personality_config(PID, {})
    psych.hand_count = hand_count
    return SimpleNamespace(psychology=psych, ai_player=SimpleNamespace(personality_config={}))


def test_hydrate_replaces_psychology_from_blob(repo):
    # World left this persona at hand_count=42.
    world = PlayerPsychology.from_personality_config(PID, {})
    world.hand_count = 42
    repo.save_emotional_state_json(PID, json.dumps(world.to_dict()), sandbox_id=SANDBOX)

    ctrl = _controller(hand_count=0)  # fresh seat at baseline
    hydrate_persona_psychology(ctrl, PID, repo, SANDBOX)

    assert ctrl.psychology.hand_count == 42


def test_flush_writes_current_psychology_back(repo):
    ctrl = _controller(hand_count=7)
    flush_persona_psychology(ctrl, PID, repo, SANDBOX)

    blob = repo.load_emotional_state_json(PID, sandbox_id=SANDBOX)
    assert blob is not None
    assert json.loads(blob)["hand_count"] == 7


def test_round_trip_hydrate_after_flush(repo):
    src = _controller(hand_count=13)
    flush_persona_psychology(src, PID, repo, SANDBOX)

    dst = _controller(hand_count=0)
    hydrate_persona_psychology(dst, PID, repo, SANDBOX)
    assert dst.psychology.hand_count == 13


def test_hydrate_null_column_leaves_baseline(repo):
    ctrl = _controller(hand_count=0)
    hydrate_persona_psychology(ctrl, "never_seen_pid", repo, SANDBOX)
    assert ctrl.psychology.hand_count == 0  # untouched


def test_hydrate_none_repo_is_noop():
    ctrl = _controller(hand_count=5)
    hydrate_persona_psychology(ctrl, PID, None, SANDBOX)
    assert ctrl.psychology.hand_count == 5  # no crash, unchanged


def test_flush_none_repo_is_noop():
    ctrl = _controller(hand_count=5)
    flush_persona_psychology(ctrl, PID, None, SANDBOX)  # must not raise


def test_hydrate_malformed_blob_leaves_baseline(repo):
    repo.save_emotional_state_json(PID, "{not valid json", sandbox_id=SANDBOX)
    ctrl = _controller(hand_count=0)
    hydrate_persona_psychology(ctrl, PID, repo, SANDBOX)
    assert ctrl.psychology.hand_count == 0  # malformed → fresh defaults


def test_sandbox_scoping_isolates_blobs(repo):
    world = PlayerPsychology.from_personality_config(PID, {})
    world.hand_count = 99
    repo.save_emotional_state_json(PID, json.dumps(world.to_dict()), sandbox_id="sandbox-A")

    ctrl = _controller(hand_count=0)
    hydrate_persona_psychology(ctrl, PID, repo, "sandbox-B")
    assert ctrl.psychology.hand_count == 0  # different sandbox → no carry


def _blob_with(energy, baseline_energy, last_updated):
    """A drained-mood blob: energy/baseline + an explicit last-active time."""
    d = PlayerPsychology.from_personality_config(PID, {}).to_dict()
    d["axes"]["energy"] = energy
    d["anchors"]["baseline_energy"] = baseline_energy
    d["last_updated"] = last_updated
    return json.dumps(d)


def test_hydrate_recovers_energy_after_long_idle(repo):
    # Drained to 0.1, baseline 0.8, last active 2h ago. At +0.5/hr that's
    # 0.1 + 1.0 = 1.1, clamped to baseline 0.8.
    two_hours_ago = (datetime.utcnow() - timedelta(hours=2)).isoformat()
    repo.save_emotional_state_json(PID, _blob_with(0.1, 0.8, two_hours_ago), sandbox_id=SANDBOX)

    ctrl = _controller()
    hydrate_persona_psychology(ctrl, PID, repo, SANDBOX)
    assert ctrl.psychology.axes.energy == pytest.approx(0.8, abs=0.01)


def test_hydrate_recent_blob_barely_recovers(repo):
    # Just left (now): essentially no rest, so energy stays drained.
    repo.save_emotional_state_json(
        PID, _blob_with(0.1, 0.8, datetime.utcnow().isoformat()), sandbox_id=SANDBOX
    )

    ctrl = _controller()
    hydrate_persona_psychology(ctrl, PID, repo, SANDBOX)
    assert ctrl.psychology.axes.energy == pytest.approx(0.1, abs=0.02)


def test_hydrate_recovery_never_exceeds_baseline(repo):
    # Already above baseline → rest must not push it higher.
    long_ago = (datetime.utcnow() - timedelta(hours=10)).isoformat()
    repo.save_emotional_state_json(PID, _blob_with(0.7, 0.5, long_ago), sandbox_id=SANDBOX)

    ctrl = _controller()
    hydrate_persona_psychology(ctrl, PID, repo, SANDBOX)
    assert ctrl.psychology.axes.energy == pytest.approx(0.7, abs=0.01)  # unchanged
