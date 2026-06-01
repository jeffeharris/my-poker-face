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


def _models_dict(hands, vpip=1):
    """Build the dict form save_opponent_models accepts (Jeff observes Greg)."""
    counts = _counts(hands_dealt=hands, hands_observed=hands, _vpip_count=vpip)
    return {
        '__name_to_id__': {'Jeff': 'obs1', 'Greg': 'opp1'},
        'Jeff': {
            'Greg': {
                'observer_id': 'obs1',
                'opponent_id': 'opp1',
                'tendencies': counts,
            }
        },
    }


def test_save_then_fold_repeatedly_does_not_double_count(repo):
    """Regression: save_opponent_models delete+reinserts the row, dropping
    lifetime_applied_json. If the mark isn't preserved, the post-save fold
    re-adds the full count every save (over-counting). Mirrors the live
    per-action save→fold cadence."""
    # Two actions in a "hand" at hands_observed=2, each save followed by a fold.
    repo.save_opponent_models("g1", _models_dict(2))
    repo.fold_observations_into_lifetime("g1", "sb1")
    repo.save_opponent_models("g1", _models_dict(2))  # next action, same count
    repo.fold_observations_into_lifetime("g1", "sb1")

    life = repo.load_observation_lifetime("sb1", "obs1", "opp1")
    assert life['hands_observed'] == 2, "save+fold cycle double-counted"

    # Hands advance to 5 → lifetime should track the real total, not inflate.
    repo.save_opponent_models("g1", _models_dict(5))
    repo.fold_observations_into_lifetime("g1", "sb1")
    life = repo.load_observation_lifetime("sb1", "obs1", "opp1")
    assert life['hands_observed'] == 5


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


# --- v125 deep postflop counts (the Tier-2 reads) ---------------------------

def test_fold_stores_deep_postflop_counts(repo, db_path):
    """The fold picks up the new count/sum fields automatically (they're in
    the flat field maps), including the float equity sums."""
    _insert_model(
        db_path, "g1", "obs1", "opp1",
        _counts(
            hands_observed=30, hands_dealt=30,
            _all_in_count=3,
            _fold_to_cbet_count=6, _cbet_faced_count=10,
            _barrel_count=3, _barrel_opportunity_count=8,
            _postflop_bet_raise_count=9, _postflop_call_count=3,
            _equity_betting_count=4, _equity_betting_sum=2.6,
        ),
    )
    assert repo.fold_observations_into_lifetime("g1", "sb1") == 1

    life = repo.load_observation_lifetime("sb1", "obs1", "opp1")
    assert life['all_in_count'] == 3
    assert life['fold_to_cbet_count'] == 6
    assert life['cbet_faced_count'] == 10
    assert life['barrel_count'] == 3
    assert life['barrel_opportunity_count'] == 8
    assert life['postflop_bet_raise_count'] == 9
    assert life['postflop_call_count'] == 3
    assert life['equity_betting_count'] == 4
    assert life['equity_betting_sum'] == pytest.approx(2.6)


def test_deep_counts_merge_lossless_incl_equity_sum(repo, db_path):
    """Cross-game merge sums the new counts AND the float equity sums."""
    _insert_model(db_path, "g1", "obs1", "opp1",
                  _counts(hands_observed=10, _cbet_faced_count=5,
                          _fold_to_cbet_count=2,
                          _equity_calling_count=2, _equity_calling_sum=0.8))
    repo.fold_observations_into_lifetime("g1", "sb1")

    _insert_model(db_path, "g2", "obs1", "opp1",
                  _counts(hands_observed=8, _cbet_faced_count=3,
                          _fold_to_cbet_count=1,
                          _equity_calling_count=1, _equity_calling_sum=0.5))
    repo.fold_observations_into_lifetime("g2", "sb1")

    life = repo.load_observation_lifetime("sb1", "obs1", "opp1")
    assert life['cbet_faced_count'] == 8        # 5 + 3
    assert life['fold_to_cbet_count'] == 3      # 2 + 1
    assert life['equity_calling_count'] == 3    # 2 + 1
    assert life['equity_calling_sum'] == pytest.approx(1.3)  # 0.8 + 0.5


def test_deep_refold_unchanged_is_idempotent(repo, db_path):
    """A re-fold with an unchanged equity sum writes nothing (no float drift,
    no double-count)."""
    _insert_model(db_path, "g1", "obs1", "opp1",
                  _counts(hands_observed=10, _equity_betting_count=2,
                          _equity_betting_sum=1.2))
    assert repo.fold_observations_into_lifetime("g1", "sb1") == 1
    assert repo.fold_observations_into_lifetime("g1", "sb1") == 0
    life = repo.load_observation_lifetime("sb1", "obs1", "opp1")
    assert life['equity_betting_sum'] == pytest.approx(1.2)


def test_deeper_reads_from_lifetime_derives_rates():
    from flask_app.routes.character_routes import _deeper_reads_from_lifetime

    deep = _deeper_reads_from_lifetime({
        'hands_observed': 30, 'hands_dealt': 30,
        'all_in_count': 3,
        'fold_to_cbet_count': 6, 'cbet_faced_count': 10,
        'cbet_attempt_count': 7, 'postflop_seen_as_pfr_count': 10,
        'barrel_count': 3, 'barrel_opportunity_count': 6,
        'third_barrel_count': 0, 'third_barrel_opportunity_count': 0,
        'postflop_bet_raise_count': 9, 'postflop_call_count': 3,
        'equity_betting_count': 4, 'equity_betting_sum': 2.6,
        'equity_raising_count': 0, 'equity_raising_sum': 0.0,
        'equity_calling_count': 2, 'equity_calling_sum': 0.8,
    })
    assert deep is not None
    assert deep['lifetime'] is True
    assert deep['fold_to_cbet'] == pytest.approx(0.6)              # 6 / 10
    assert deep['cbet_attempt_rate'] == pytest.approx(0.7)         # 7 / 10
    assert deep['barrel_frequency'] == pytest.approx(0.5)          # 3 / 6
    assert deep['all_in_frequency'] == pytest.approx(0.1)          # 3 / 30
    assert deep['aggression_factor_postflop'] == pytest.approx(3.0)  # 9 / 3
    # Equity means derive as sum / count (NOT via _recalculate_stats).
    assert deep['equity_when_betting'] == pytest.approx(0.65)      # 2.6 / 4
    assert deep['equity_when_calling'] == pytest.approx(0.4)       # 0.8 / 2
    # No opportunities observed → None (not the model's neutral 0.5 prior).
    assert deep['third_barrel_frequency'] is None
    assert deep['equity_when_raising'] is None


def test_deeper_reads_from_lifetime_empty_is_none():
    from flask_app.routes.character_routes import _deeper_reads_from_lifetime

    assert _deeper_reads_from_lifetime(None) is None
    assert _deeper_reads_from_lifetime({'hands_observed': 0}) is None


def test_fold_stores_preflop_opportunity_counts(repo, db_path):
    """v126: the preflop opportunity counters fold into the lifetime row and
    drive the opportunity-normalized rate on read (the signal the station/nit
    'the read' detectors gate on)."""
    _insert_model(
        db_path, "g1", "obs1", "opp1",
        _counts(hands_observed=40, hands_dealt=40,
                _preflop_voluntary_action_count=27,
                _preflop_voluntary_opportunities=30,
                _preflop_open_raise_count=4,
                _preflop_open_opportunities=30),
    )
    assert repo.fold_observations_into_lifetime("g1", "sb1") == 1

    life = repo.load_observation_lifetime("sb1", "obs1", "opp1")
    assert life['preflop_voluntary_action_count'] == 27
    assert life['preflop_voluntary_opportunities'] == 30
    assert life['preflop_open_raise_count'] == 4
    assert life['preflop_open_opportunities'] == 30

    # And the reconstructed tendency derives vpip_per_voluntary_opportunity.
    from flask_app.routes.character_routes import _tendencies_from_lifetime
    t = _tendencies_from_lifetime(life)
    assert t.vpip_per_voluntary_opportunity == pytest.approx(0.9)  # 27 / 30


# --- Informant unlock store (Phase 3) ---------------------------------------

def test_informant_unlock_record_and_load(repo):
    assert repo.load_informant_unlocks("sb1", "obs1", "opp1") == set()
    assert repo.record_informant_unlock("sb1", "obs1", "opp1", "read", 750) is True
    assert repo.load_informant_unlocks("sb1", "obs1", "opp1") == {"read"}


def test_informant_unlock_is_idempotent(repo):
    assert repo.record_informant_unlock("sb1", "obs1", "opp1", "read", 750) is True
    # Second buy of the same section is a no-op (so the route won't charge twice).
    assert repo.record_informant_unlock("sb1", "obs1", "opp1", "read", 750) is False
    assert repo.load_informant_unlocks("sb1", "obs1", "opp1") == {"read"}


def test_informant_unlock_scoped_per_pair_and_sandbox(repo):
    repo.record_informant_unlock("sb1", "obs1", "opp1", "read", 750)
    assert repo.load_informant_unlocks("sb1", "obs1", "opp2") == set()
    assert repo.load_informant_unlocks("sb2", "obs1", "opp1") == set()


def test_informant_unlock_ledger_reason_registered():
    from core.economy.ledger import BANK_POOL_DEPOSIT_REASONS, LEDGER_REASONS

    assert 'informant_unlock' in LEDGER_REASONS
    assert 'informant_unlock' in BANK_POOL_DEPOSIT_REASONS  # recyclable sink
