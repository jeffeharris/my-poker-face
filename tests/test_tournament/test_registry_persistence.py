"""Write-through registry: re-entry after in-memory eviction (Persistence C).

These exercise the registry's repo-backed reads — the heart of "a tournament
survives a restart": register -> evict memory -> the lobby/standings/sit routes'
`find_active_for_owner` / `get` rehydrate it from the DB. The repo singleton is
monkeypatched onto a temp DB.
"""

import pytest

from poker.repositories.schema_manager import SchemaManager
from poker.repositories.tournament_session_repository import TournamentSessionRepository
from flask_app.services import tournament_registry as registry
from tournament.config import TournamentConfig
from tournament.director import FakeHandResolver
from tournament.session import TournamentSession


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


def _register(owner='owner-a', seed=1):
    cfg = TournamentConfig(field_size=6, table_size=3, seed=seed)
    resolver = FakeHandResolver()
    session = TournamentSession(cfg, ai_resolver=resolver)
    tid = registry.new_tournament_id()
    registry.put(
        tid,
        {
            'session': session,
            'owner_id': owner,
            'created_at': '2026-05-29T00:00:00',
            'resolver': resolver,
            'resolver_kind': 'fake',
            'game_id': None,
        },
    )
    registry.persist(tid)
    return tid, session


def _human(seat_order, stacks, level, button, seed):
    return FakeHandResolver().resolve(seat_order, stacks, level, button, seed)


def test_reentry_after_eviction_via_find_active(repo):
    tid, session = _register()
    standings = session.standings_view()
    registry.clear()  # simulate server restart / TTL eviction
    assert registry.find_active_for_owner('owner-a') == tid
    rec = registry.get(tid)
    assert rec is not None
    assert rec['session'].standings_view() == standings
    rec['session'].field.assert_conservation()


def test_get_cold_rehydrates_resolver_kind(repo):
    tid, _ = _register(seed=3)
    registry.clear()
    rec = registry.get(tid)
    assert rec is not None
    assert rec['resolver_kind'] == 'fake'
    assert rec['game_id'] is None


def test_persist_after_play_then_reenter_matches(repo):
    tid, session = _register(seed=5)
    for _ in range(4):
        if session.is_complete() or session.human_out:
            break
        session.play_round(_human)
        registry.persist(tid)  # simulate hand-boundary saves
    after = session.standings_view()
    registry.clear()
    rec = registry.get(tid)
    assert rec['session'].standings_view() == after
    rec['session'].field.assert_conservation()


def test_complete_marks_status_and_drops_from_active(repo):
    tid, session = _register(seed=2)
    session.play_out()
    registry.persist(tid)  # status -> complete
    registry.clear()
    assert registry.find_active_for_owner('owner-a') is None  # no longer active
    assert registry.get(tid) is not None  # still loadable by id


def test_game_id_persists_across_eviction(repo):
    tid, _ = _register(seed=7)
    registry.get(tid)['game_id'] = 'tourney-xyz'
    registry.persist(tid)
    registry.clear()
    assert registry.get(tid)['game_id'] == 'tourney-xyz'


def test_delete_removes_from_repo(repo):
    tid, _ = _register()
    registry.delete(tid)
    registry.clear()
    assert registry.get(tid) is None
    assert registry.find_active_for_owner('owner-a') is None


def test_memory_only_when_repo_absent(monkeypatch):
    """With no repo wired, the registry degrades to memory-only (no crash)."""
    import flask_app.extensions as ext

    monkeypatch.setattr(ext, 'tournament_session_repo', None, raising=False)
    registry.clear()
    tid, _ = _register(seed=9)
    assert registry.get(tid) is not None  # in-memory record works
    registry.clear()
    assert registry.get(tid) is None  # nothing durable was written
    registry.clear()
