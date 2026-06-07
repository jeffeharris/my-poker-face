"""Regression: a cold-loaded multi-table tournament must route back through the
multi-table hand boundary (the only path that escalates the live table's blinds).

The single/multi boundary split used to key on `tournament_multi_table`, an
in-memory game_data flag set only at fresh-build time. A cold load (TTL eviction
/ restart / page reload) dropped it, so a rehydrated MTT ran the SINGLE-table
boundary: that path advances `session.rounds` (the displayed blind clock keeps
rising) but never calls `reconcile_live_table` — the only thing that pushes the
new blind level onto the table — and the engine's own blind_config has
growth=1.0, so it can't self-escalate either. The table's blinds froze at the
cold-load level while the tournament clock climbed.

The fix moves the discriminator ONTO the session (`is_multi_table`), which is
always reconstructed on cold-load (and serialized), so it can't desync. These
cover the session invariant, its serialization round-trip, and the cold-load
re-attach helper (`_reattach_mtt_session`) restoring a multi-table session.
"""

from __future__ import annotations

import pytest

from flask_app.routes.game_routes import _reattach_mtt_session
from flask_app.services import tournament_registry as registry
from tournament.config import TournamentConfig
from tournament.director import FakeHandResolver
from tournament.session import TournamentSession

pytestmark = [pytest.mark.flask, pytest.mark.integration]


# ── the session-level invariant (the crux of the fix) ───────────────────────────


def test_for_single_table_is_not_multi_table():
    session = TournamentSession.for_single_table(
        entries={'human:me': 'human', 'persona_0': 'TAG'},
        human_id='human:me',
        starting_stack=10_000,
    )
    assert session.is_multi_table is False


def test_spawned_session_is_multi_table():
    # A tiny MTT (field == one table) MUST still be multi-table — this is exactly
    # the case `field_size > table_size` would misclassify (MIN_FIELD=2).
    config = TournamentConfig(field_size=2, table_size=3, starting_stack=10_000, seed=1)
    session = TournamentSession(
        config,
        ai_resolver=FakeHandResolver(),
        human_id='human:me',
        entries={'human:me': 'human', 'persona_0': 'TAG'},
    )
    assert session.is_multi_table is True


@pytest.mark.parametrize('single', [True, False])
def test_single_table_flag_round_trips(single):
    if single:
        session = TournamentSession.for_single_table(
            entries={'human:me': 'human', 'persona_0': 'TAG'},
            human_id='human:me',
            starting_stack=10_000,
        )
    else:
        config = TournamentConfig(field_size=9, table_size=3, starting_stack=10_000, seed=1)
        entries = {'human:me': 'human', **{f'persona_{i}': 'TAG' for i in range(8)}}
        session = TournamentSession(
            config, ai_resolver=FakeHandResolver(), human_id='human:me', entries=entries
        )

    restored = TournamentSession.from_dict(session.to_dict(), FakeHandResolver())
    assert restored.single_table is single
    assert restored.is_multi_table is (not single)


def test_legacy_blob_without_key_defaults_to_multi_table():
    # Blobs persisted before the `single_table` key existed default to multi-table
    # (the dominant MTT case); the cold-load route forces single for resolver_kind
    # == 'single', so a legacy single blob is still routed correctly there.
    config = TournamentConfig(field_size=9, table_size=3, starting_stack=10_000, seed=1)
    entries = {'human:me': 'human', **{f'persona_{i}': 'TAG' for i in range(8)}}
    session = TournamentSession(
        config, ai_resolver=FakeHandResolver(), human_id='human:me', entries=entries
    )
    blob = session.to_dict()
    del blob['single_table']  # simulate a pre-key persisted session
    restored = TournamentSession.from_dict(blob, FakeHandResolver())
    assert restored.is_multi_table is True


# ── the cold-load re-attach helper ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    # Force the registry memory-only so an entry can't leak across tests via the
    # persisted repo (mirrors test_tournament_routes.py).
    monkeypatch.setattr('flask_app.extensions.tournament_session_repo', None)
    registry.clear()
    yield
    registry.clear()


def _put_mtt(owner_id='owner-1', tid='tourney-mtt'):
    """A real multi-table human tournament in the registry — the shape the MTT
    re-attach block rehydrates."""
    from flask_app.services.tournament_spawn import human_seat_id
    from tournament.config import DEFAULT_FIELD_ARCHETYPES

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


def test_coldload_restores_a_multi_table_session():
    """The core regression: a cold load of an MTT game restores a session whose
    `is_multi_table` is True (so the inter-hand step routes to the multi-table
    boundary that escalates blinds), plus the identity fields."""
    tid, session, human = _put_mtt()
    game_id = 'tourney-abc'

    # game_data as it looks fresh off the games row on cold load: the session is
    # gone (and there is no longer any separate multi_table flag to drop).
    game_data: dict = {}
    _reattach_mtt_session(game_data, {'tournament_id': tid}, game_id)

    assert game_data['tournament_session'] is session
    assert game_data['tournament_session'].is_multi_table is True  # routes to MTT boundary
    assert game_data['tournament_id'] == tid
    assert game_data['tournament_human_id'] == human
    assert game_data['tournament_table_id'] == session.human_table.table_id
    assert game_data['tournament_resolver_kind'] == 'fake'
    # the registry entry is re-pointed at this live game
    assert registry.get(tid)['game_id'] == game_id


def test_no_row_is_a_noop():
    """Cash / single-table cold loads pass mtt_session_row=None — the helper must
    not attach a session (that would misroute them to the MTT boundary)."""
    game_data: dict = {}
    _reattach_mtt_session(game_data, None, 'cash-xyz')
    assert game_data == {}


def test_missing_registry_entry_is_a_noop():
    """If the row points at a tournament that's no longer in the registry, the
    helper degrades to a no-op rather than half-attaching without a session."""
    game_data: dict = {}
    _reattach_mtt_session(game_data, {'tournament_id': 'gone'}, 'tourney-abc')
    assert 'tournament_session' not in game_data
