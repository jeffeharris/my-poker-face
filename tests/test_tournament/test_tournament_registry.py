"""Pure tests for the in-memory tournament registry."""

import pytest

from flask_app.services import tournament_registry as registry
from tournament.config import TournamentConfig
from tournament.director import FakeHandResolver
from tournament.session import TournamentSession


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.clear()
    yield
    registry.clear()


def _session() -> TournamentSession:
    config = TournamentConfig(field_size=6, table_size=3, starting_stack=1000, seed=0)
    return TournamentSession(config, ai_resolver=FakeHandResolver())


def test_put_get_delete():
    tid = registry.new_tournament_id()
    registry.put(tid, {'session': _session(), 'owner_id': 'u1'})
    assert registry.get(tid)['owner_id'] == 'u1'
    registry.delete(tid)
    assert registry.get(tid) is None


def test_find_active_for_owner_ignores_completed():
    s = _session()
    s.play_out()  # run to completion
    assert s.is_complete()
    tid = registry.new_tournament_id()
    registry.put(tid, {'session': s, 'owner_id': 'u1'})
    # completed tournaments are not "active"
    assert registry.find_active_for_owner('u1') is None


def test_find_active_for_owner_returns_live():
    tid = registry.new_tournament_id()
    registry.put(tid, {'session': _session(), 'owner_id': 'u1'})
    assert registry.find_active_for_owner('u1') == tid
    assert registry.find_active_for_owner('someone-else') is None


def test_ids_are_unique():
    ids = {registry.new_tournament_id() for _ in range(100)}
    assert len(ids) == 100
