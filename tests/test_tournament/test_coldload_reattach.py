"""Regression: cold-loading a multi-table tournament must re-stamp the
`tournament_multi_table` flag.

The flag is set only at fresh-build time (tournament_game_builder) and lives in
in-memory game_data, so a cold load (eviction / restart) drops it. The MTT
re-attach block in the `/api/game-state` route is the only place it can be
restored. Without it the inter-hand step runs the SINGLE-table boundary instead
of the multi-table one: that path advances `session.rounds` (so the displayed
blind clock climbs) but never calls `reconcile_live_table` — the only thing that
pushes the new blind level onto the table — and the engine's own blind_config
has growth=1.0, so it can't self-escalate either. The table's blinds then freeze
at the cold-load level while the tournament clock keeps rising.

These exercise `_reattach_mtt_session` (the extracted re-attach helper) directly
against the in-memory registry, so no DB scaffolding is needed.
"""

from __future__ import annotations

import pytest

from flask_app.routes.game_routes import _reattach_mtt_session
from flask_app.services import tournament_registry as registry

pytestmark = [pytest.mark.flask, pytest.mark.integration]


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    # Force the registry memory-only so an entry can't leak across tests via the
    # persisted repo (mirrors test_tournament_routes.py).
    monkeypatch.setattr('flask_app.extensions.tournament_session_repo', None)
    registry.clear()
    yield
    registry.clear()


def _put_mtt(owner_id='owner-1', tid='tourney-mtt'):
    """A real multi-table (field_size > table_size) human tournament in the
    registry — the shape the MTT re-attach block rehydrates."""
    from flask_app.services.tournament_spawn import human_seat_id
    from tournament.config import DEFAULT_FIELD_ARCHETYPES, TournamentConfig
    from tournament.director import FakeHandResolver
    from tournament.session import TournamentSession

    human = human_seat_id(owner_id)
    seat_ids = [human] + [f'persona_{i}' for i in range(8)]
    archs = DEFAULT_FIELD_ARCHETYPES
    entries = {sid: archs[i % len(archs)] for i, sid in enumerate(seat_ids)}
    config = TournamentConfig(field_size=9, table_size=3, starting_stack=10_000, seed=1)
    resolver = FakeHandResolver()
    session = TournamentSession(config, ai_resolver=resolver, human_id=human, entries=entries)
    registry.put(
        tid,
        {
            'session': session,
            'owner_id': owner_id,
            'created_at': 'now',
            'resolver': resolver,
            'resolver_kind': 'fake',
            'game_id': None,
        },
    )
    return tid, session, human


def test_coldload_restamps_multi_table_flag():
    """The core regression: a cold load of an MTT game must set
    tournament_multi_table=True (so the inter-hand step routes to the multi-table
    boundary that escalates blinds), plus restore the session + identity fields."""
    tid, session, human = _put_mtt()
    game_id = 'tourney-abc'

    # game_data as it looks fresh off the games row on cold load: the flag and
    # the in-memory session are gone.
    game_data: dict = {}
    _reattach_mtt_session(game_data, {'tournament_id': tid}, game_id)

    assert game_data['tournament_multi_table'] is True  # the bug: this was missing
    assert game_data['tournament_session'] is session
    assert game_data['tournament_id'] == tid
    assert game_data['tournament_human_id'] == human
    assert game_data['tournament_table_id'] == session.human_table.table_id
    assert game_data['tournament_resolver_kind'] == 'fake'
    # the registry entry is re-pointed at this live game
    assert registry.get(tid)['game_id'] == game_id


def test_no_row_is_a_noop():
    """Cash / single-table cold loads pass mtt_session_row=None — the helper must
    not stamp the multi-table flag (that would misroute them to the MTT boundary)."""
    game_data: dict = {}
    _reattach_mtt_session(game_data, None, 'cash-xyz')
    assert game_data == {}


def test_missing_registry_entry_does_not_stamp_flag():
    """If the row points at a tournament that's no longer in the registry, the
    helper degrades to a no-op rather than half-stamping the flag without a
    session (which would route to the MTT boundary with no field to advance)."""
    game_data: dict = {}
    _reattach_mtt_session(game_data, {'tournament_id': 'gone'}, 'tourney-abc')
    assert 'tournament_multi_table' not in game_data
    assert 'tournament_session' not in game_data
