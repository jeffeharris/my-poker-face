"""Tests for the Phase 1 opponent observation lifetime store (schema v123).

Covers the continuous delta-fold from per-game `opponent_models` into the
per-sandbox `opponent_observation_lifetime` rows: lossless cross-game merge,
idempotent re-fold (no double-count), resume-safe deltas, sandbox gating, and
the derived rates on read.
"""

import json
import sqlite3

import pytest

from poker.repositories.game_repository import GameRepository


@pytest.fixture
def repo(db_path):
    r = GameRepository(db_path)
    yield r
    r.close()


def _counts(**overrides):
    base = {
        'hands_dealt': 0,
        'hands_observed': 0,
        '_vpip_count': 0,
        '_pfr_count': 0,
        '_bet_raise_count': 0,
        '_call_count': 0,
        '_showdowns': 0,
        '_showdowns_won': 0,
    }
    base.update(overrides)
    return base


def _insert_model(db_path, game_id, observer_id, opponent_id, counts,
                  observer_name="Alice", opponent_name="Bob"):
    """Insert a raw opponent_models row with crafted tendencies counts."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO opponent_models
                (game_id, observer_name, opponent_name, observer_id,
                 opponent_id, hands_observed, tendencies_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game_id, observer_name, opponent_name, observer_id,
                opponent_id, counts['hands_observed'], json.dumps(counts),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _set_tendencies(db_path, game_id, opponent_id, counts):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE opponent_models SET tendencies_json = ? "
            "WHERE game_id = ? AND opponent_id = ?",
            (json.dumps(counts), game_id, opponent_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_first_fold_stores_counts(repo, db_path):
    _insert_model(
        db_path, "g1", "obs1", "opp1",
        _counts(hands_observed=10, hands_dealt=12, _vpip_count=3, _pfr_count=2,
                _bet_raise_count=5, _call_count=4, _showdowns=2,
                _showdowns_won=1),
    )

    assert repo.fold_observations_into_lifetime("g1", "sb1") == 1

    life = repo.load_observation_lifetime("sb1", "obs1", "opp1")
    assert life is not None
    assert life['hands_observed'] == 10
    assert life['hands_dealt'] == 12
    assert life['vpip_count'] == 3
    assert life['pfr_count'] == 2
    assert life['bet_raise_count'] == 5
    assert life['call_count'] == 4
    assert life['showdowns_seen'] == 2
    assert life['showdowns_won'] == 1


def test_refold_unchanged_is_idempotent(repo, db_path):
    _insert_model(db_path, "g1", "obs1", "opp1",
                  _counts(hands_observed=10, _vpip_count=3))
    assert repo.fold_observations_into_lifetime("g1", "sb1") == 1
    # Nothing changed → second fold writes nothing, counts stay put.
    assert repo.fold_observations_into_lifetime("g1", "sb1") == 0
    assert repo.load_observation_lifetime("sb1", "obs1", "opp1")['hands_observed'] == 10


def test_delta_fold_accumulates_within_game(repo, db_path):
    _insert_model(db_path, "g1", "obs1", "opp1",
                  _counts(hands_observed=10, _vpip_count=3))
    repo.fold_observations_into_lifetime("g1", "sb1")

    # More hands accrue in the SAME game (resume / continued session).
    _set_tendencies(db_path, "g1", "opp1",
                    _counts(hands_observed=25, _vpip_count=8))
    assert repo.fold_observations_into_lifetime("g1", "sb1") == 1

    life = repo.load_observation_lifetime("sb1", "obs1", "opp1")
    assert life['hands_observed'] == 25  # 10 + delta(15), not 35
    assert life['vpip_count'] == 8       # 3 + delta(5)


def test_cross_game_merge_is_lossless(repo, db_path):
    _insert_model(db_path, "g1", "obs1", "opp1",
                  _counts(hands_observed=10, _vpip_count=3, _bet_raise_count=4,
                          _call_count=2))
    repo.fold_observations_into_lifetime("g1", "sb1")

    # A whole new game vs the same opponent in the same sandbox.
    _insert_model(db_path, "g2", "obs1", "opp1",
                  _counts(hands_observed=5, _vpip_count=2, _bet_raise_count=2,
                          _call_count=2))
    repo.fold_observations_into_lifetime("g2", "sb1")

    life = repo.load_observation_lifetime("sb1", "obs1", "opp1")
    assert life['hands_observed'] == 15      # 10 + 5
    assert life['vpip_count'] == 5           # 3 + 2
    assert life['bet_raise_count'] == 6      # 4 + 2
    assert life['call_count'] == 4           # 2 + 2


def test_sandbox_gate_makes_fold_a_noop(repo, db_path):
    _insert_model(db_path, "g1", "obs1", "opp1",
                  _counts(hands_observed=10, _vpip_count=3))
    # Falsy sandbox_id → non-Circuit game → nothing folds.
    assert repo.fold_observations_into_lifetime("g1", None) == 0
    assert repo.fold_observations_into_lifetime("g1", "") == 0
    assert repo.load_observation_lifetime("sb1", "obs1", "opp1") is None


def test_rows_without_ids_are_skipped(repo, db_path):
    # Human / ad-hoc seat with no stable id — nothing reads its lifetime.
    _insert_model(db_path, "g1", None, None,
                  _counts(hands_observed=10, _vpip_count=3),
                  observer_name="Human", opponent_name="Bob")
    assert repo.fold_observations_into_lifetime("g1", "sb1") == 0


def test_per_sandbox_isolation(repo, db_path):
    _insert_model(db_path, "g1", "obs1", "opp1",
                  _counts(hands_observed=10, _vpip_count=3))
    repo.fold_observations_into_lifetime("g1", "sandbox_A")
    # A different save (sandbox) never sees the other's intel.
    assert repo.load_observation_lifetime("sandbox_A", "obs1", "opp1") is not None
    assert repo.load_observation_lifetime("sandbox_B", "obs1", "opp1") is None


def test_load_missing_returns_none(repo):
    assert repo.load_observation_lifetime("sb1", "nope", "nada") is None


# --- Dossier rate derivation (reuses the canonical OpponentTendencies) ---

def test_observation_from_lifetime_derives_canonical_rates():
    from flask_app.routes.character_routes import _observation_from_lifetime

    obs = _observation_from_lifetime({
        'hands_dealt': 12, 'hands_observed': 10,
        'vpip_count': 3, 'pfr_count': 2,
        'bet_raise_count': 5, 'call_count': 4,
        'showdowns_seen': 2, 'showdowns_won': 1,
    })
    assert obs is not None
    assert obs['lifetime'] is True
    assert obs['hands_observed'] == 10
    # VPIP/PFR use hands_dealt as the denominator (canonical formula).
    assert obs['vpip'] == pytest.approx(0.25)          # 3 / 12
    assert obs['pfr'] == pytest.approx(0.17, abs=0.01)  # 2 / 12
    assert obs['aggression_factor'] == pytest.approx(1.25)  # 5 / 4
    assert 'play_style' in obs


def test_observation_from_lifetime_empty_is_none():
    from flask_app.routes.character_routes import _observation_from_lifetime

    assert _observation_from_lifetime(None) is None
    assert _observation_from_lifetime({'hands_observed': 0}) is None
