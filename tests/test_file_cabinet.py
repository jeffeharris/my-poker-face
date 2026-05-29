"""Tests for the file-cabinet roster aggregator (dossier Phase 4)."""

import sqlite3

import pytest

from cash_mode.bankroll import PlayerBankrollState  # noqa: F401 (parity import)
from flask_app.services.file_cabinet import build_file_cabinet
from poker.memory.opponent_model import CashPairStats, RelationshipState
from poker.repositories.game_repository import GameRepository
from poker.repositories.relationship_repository import RelationshipRepository
from poker.repositories.schema_manager import SchemaManager

SB = "sb1"
OBS = "obs_jeff"


@pytest.fixture
def repos(db_path):
    # Root conftest's db_path is a bare path (no schema); build it here.
    SchemaManager(db_path).ensure_schema()
    gr = GameRepository(db_path)
    rr = RelationshipRepository(db_path)
    yield gr, rr, db_path
    gr.close()
    rr.close()


class _NamesRepo:
    """Minimal personality_repo stub — display_names_by_ids only."""
    def __init__(self, mapping):
        self._m = mapping

    def display_names_by_ids(self, ids):
        return {i: self._m[i] for i in ids if i in self._m}


def _seed_lifetime(db_path, opponent_id, hands_observed):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO opponent_observation_lifetime "
            "(sandbox_id, observer_id, opponent_id, hands_observed, hands_dealt, "
            " first_seen, last_updated) "
            "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (SB, OBS, opponent_id, hands_observed, hands_observed),
        )
        conn.commit()
    finally:
        conn.close()


def test_empty_roster_for_new_player(repos):
    gr, rr, _ = repos
    out = build_file_cabinet(
        sandbox_id=SB, observer_id=OBS,
        game_repo=gr, relationship_repo=rr, personality_repo=_NamesRepo({}),
    )
    assert out == {'people': [], 'people_met': 0, 'dossiers_unlocked': 0}


def test_roster_joins_stats_and_counts_unlocks(repos):
    gr, rr, db_path = repos
    # greg: deep history (fully unlocked); cleo: just past the floor.
    _seed_lifetime(db_path, "greg", 500)
    _seed_lifetime(db_path, "cleo", 30)
    rr.save_cash_pair_stats(
        OBS, "greg", CashPairStats(OBS, "greg", cumulative_pnl=2500,
                                   hands_played_cash=120), sandbox_id=SB)
    rr.save_relationship_state(OBS, "greg", RelationshipState(heat=0.8, respect=0.6))
    names = _NamesRepo({"greg": "Greg", "cleo": "Cleopatra"})

    out = build_file_cabinet(
        sandbox_id=SB, observer_id=OBS,
        game_repo=gr, relationship_repo=rr, personality_repo=names,
    )

    assert out['people_met'] == 2
    assert out['dossiers_unlocked'] == 1  # only greg is fully unlocked

    by_id = {p['personality_id']: p for p in out['people']}
    greg = by_id['greg']
    assert greg['name'] == 'Greg'
    assert greg['net_pnl'] == 2500
    assert greg['hands_played_cash'] == 120
    assert greg['heat'] == pytest.approx(0.8)
    assert greg['fully_unlocked'] is True
    assert greg['reads_unlocked'] == greg['reads_total']

    cleo = by_id['cleo']
    assert cleo['name'] == 'Cleopatra'
    assert cleo['net_pnl'] == 0          # no cash_pair_stats row
    assert cleo['fully_unlocked'] is False
    assert 0 < cleo['reads_unlocked'] < cleo['reads_total']
    assert cleo['floor_met'] is True     # 30 >= 25


def test_roster_sorted_most_observed_first(repos):
    gr, rr, db_path = repos
    _seed_lifetime(db_path, "low", 40)
    _seed_lifetime(db_path, "high", 300)
    out = build_file_cabinet(
        sandbox_id=SB, observer_id=OBS,
        game_repo=gr, relationship_repo=rr, personality_repo=_NamesRepo({}),
    )
    assert [p['personality_id'] for p in out['people']] == ['high', 'low']


def test_observer_self_row_is_excluded(repos):
    gr, rr, db_path = repos
    _seed_lifetime(db_path, "greg", 100)
    _seed_lifetime(db_path, OBS, 80)  # observer observing themselves — noise
    out = build_file_cabinet(
        sandbox_id=SB, observer_id=OBS,
        game_repo=gr, relationship_repo=rr, personality_repo=_NamesRepo({}),
    )
    ids = {p['personality_id'] for p in out['people']}
    assert OBS not in ids
    assert ids == {"greg"}
    assert out['people_met'] == 1


def test_informant_purchase_counts_toward_unlocked(repos):
    gr, rr, db_path = repos
    _seed_lifetime(db_path, "greg", 10)  # below floor by grind alone
    # Buy every section → fully unlocked despite ~no grind.
    from flask_app.services.dossier_scouting import INFORMANT_SECTIONS
    for sid in INFORMANT_SECTIONS:
        gr.record_informant_unlock(SB, OBS, "greg", sid, 0)

    out = build_file_cabinet(
        sandbox_id=SB, observer_id=OBS,
        game_repo=gr, relationship_repo=rr, personality_repo=_NamesRepo({}),
    )
    assert out['dossiers_unlocked'] == 1
    assert out['people'][0]['fully_unlocked'] is True
