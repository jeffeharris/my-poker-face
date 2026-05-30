"""Single-table tournament envelopes (unification step 2).

Every ordinary game is recorded as a one-table tournament via a lightweight
`tournaments` row (`resolver_kind='single'`). These envelopes are an
identity/index record only — they are NOT rehydrated into a session and must not
interfere with the multi-table lobby's `find_active_for_owner`.
"""

import pytest

from poker.repositories.schema_manager import SchemaManager
from poker.repositories.tournament_session_repository import TournamentSessionRepository
from flask_app.services import tournament_registry as registry


@pytest.fixture
def repo(tmp_path, monkeypatch):
    db_path = str(tmp_path / "t.db")
    SchemaManager(db_path).ensure_schema()
    r = TournamentSessionRepository(db_path)
    import flask_app.extensions as ext

    monkeypatch.setattr(ext, 'tournament_session_repo', r, raising=False)
    registry.clear()
    yield r
    registry.clear()


def test_envelope_id_is_deterministic():
    assert registry.single_envelope_id('tourney-abc') == 'single-tourney-abc'


def test_persist_creates_addressable_envelope(repo):
    registry.persist_single_envelope(game_id='game-1', owner_id='owner-a')

    row = repo.find_by_game_id('game-1')
    assert row is not None
    assert row['tournament_id'] == 'single-game-1'
    assert row['resolver_kind'] == 'single'
    assert row['owner_id'] == 'owner-a'
    assert row['status'] == 'active'


def test_persist_is_idempotent(repo):
    registry.persist_single_envelope(game_id='game-1', owner_id='owner-a')
    registry.persist_single_envelope(game_id='game-1', owner_id='owner-a')
    # Upsert on a deterministic id -> still exactly one row for this game.
    assert repo.load('single-game-1') is not None
    assert repo.find_by_game_id('game-1')['tournament_id'] == 'single-game-1'


def test_delete_removes_only_the_envelope(repo):
    registry.persist_single_envelope(game_id='game-1', owner_id='owner-a')
    registry.delete_single_envelope('game-1')
    assert repo.load('single-game-1') is None
    assert repo.find_by_game_id('game-1') is None


def test_envelope_does_not_shadow_active_mtt_lookup(repo):
    """A single envelope must not be returned by find_active_for_owner, which the
    MTT lobby uses to decide whether the owner has an event to resume."""
    registry.persist_single_envelope(game_id='game-1', owner_id='owner-a')
    assert repo.find_active_for_owner('owner-a') is None
    # The registry read path agrees (no active MTT for this owner).
    assert registry.find_active_for_owner('owner-a') is None


def test_persist_is_noop_without_game_id(repo):
    registry.persist_single_envelope(game_id='', owner_id='owner-a')
    assert repo.find_active_for_owner('owner-a') is None


@pytest.mark.parametrize('game_id', ['tourney-abc', 'cash-xyz'])
def test_persist_refuses_non_single_prefixes(repo, game_id):
    """Cash sessions and multi-table tournament tables own dedicated records and
    must never be mislabeled as a single-table envelope (e.g. an orphaned
    `tourney-` table whose session row is missing)."""
    registry.persist_single_envelope(game_id=game_id, owner_id='owner-a')
    assert repo.find_by_game_id(game_id) is None
