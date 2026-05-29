"""Tests for TournamentSessionRepository (Persistence layer B) on a temp DB."""

import json

import pytest

from poker.repositories.schema_manager import SchemaManager
from poker.repositories.tournament_session_repository import TournamentSessionRepository
from tournament.config import TournamentConfig
from tournament.director import FakeHandResolver
from tournament.session import TournamentSession


@pytest.fixture
def repo(tmp_path):
    db_path = str(tmp_path / "test.db")
    SchemaManager(db_path).ensure_schema()
    return TournamentSessionRepository(db_path)


def _session_json() -> str:
    cfg = TournamentConfig(field_size=6, table_size=3, seed=1)
    s = TournamentSession(cfg, ai_resolver=FakeHandResolver())
    return json.dumps(s.to_dict())


def test_save_and_load_roundtrips_all_fields(repo):
    repo.save(
        tournament_id='t1', owner_id='owner-a', status='active',
        resolver_kind='fake', session_json=_session_json(), created_at='2026-05-29T00:00:00',
    )
    row = repo.load('t1')
    assert row is not None
    assert row['tournament_id'] == 't1'
    assert row['owner_id'] == 'owner-a'
    assert row['status'] == 'active'
    assert row['resolver_kind'] == 'fake'
    assert row['game_id'] is None
    assert row['created_at'] == '2026-05-29T00:00:00'
    assert row['updated_at']
    restored = TournamentSession.from_dict(json.loads(row['session_json']), FakeHandResolver())
    restored.field.assert_conservation()


def test_load_missing_returns_none(repo):
    assert repo.load('nope') is None
    assert repo.find_by_game_id('nope') is None
    assert repo.find_active_for_owner('nobody') is None


def test_find_active_for_owner(repo):
    repo.save(
        tournament_id='t1', owner_id='owner-a', status='active',
        resolver_kind='fake', session_json=_session_json(), created_at='2026-05-29T00:00:00',
    )
    found = repo.find_active_for_owner('owner-a')
    assert found is not None and found['tournament_id'] == 't1'
    assert repo.find_active_for_owner('owner-b') is None


def test_set_status_complete_hides_from_active(repo):
    repo.save(
        tournament_id='t1', owner_id='owner-a', status='active',
        resolver_kind='fake', session_json=_session_json(), created_at='2026-05-29T00:00:00',
    )
    repo.set_status('t1', 'complete')
    assert repo.load('t1')['status'] == 'complete'
    assert repo.find_active_for_owner('owner-a') is None


def test_set_game_id_and_find_by_game_id(repo):
    repo.save(
        tournament_id='t1', owner_id='owner-a', status='active',
        resolver_kind='engine', session_json=_session_json(), created_at='2026-05-29T00:00:00',
    )
    repo.set_game_id('t1', 'tourney-abc')
    assert repo.load('t1')['game_id'] == 'tourney-abc'
    found = repo.find_by_game_id('tourney-abc')
    assert found is not None and found['tournament_id'] == 't1'
    assert found['resolver_kind'] == 'engine'


def test_save_update_preserves_created_at(repo):
    repo.save(
        tournament_id='t1', owner_id='owner-a', status='active',
        resolver_kind='fake', session_json=_session_json(), created_at='2026-05-29T00:00:00',
    )
    repo.save(
        tournament_id='t1', owner_id='owner-a', status='active',
        resolver_kind='fake', session_json=_session_json(), created_at='2099-01-01T00:00:00',
        game_id='tourney-abc',
    )
    second = repo.load('t1')
    assert second['created_at'] == '2026-05-29T00:00:00'  # preserved on update
    assert second['game_id'] == 'tourney-abc'


def test_find_active_returns_most_recent(repo):
    repo.save(
        tournament_id='t1', owner_id='owner-a', status='complete',
        resolver_kind='fake', session_json=_session_json(), created_at='2026-05-29T00:00:00',
    )
    repo.save(
        tournament_id='t2', owner_id='owner-a', status='active',
        resolver_kind='fake', session_json=_session_json(), created_at='2026-05-29T01:00:00',
    )
    assert repo.find_active_for_owner('owner-a')['tournament_id'] == 't2'


def test_delete(repo):
    repo.save(
        tournament_id='t1', owner_id='owner-a', status='active',
        resolver_kind='fake', session_json=_session_json(), created_at='2026-05-29T00:00:00',
    )
    repo.delete('t1')
    assert repo.load('t1') is None
