"""Tests for the autonomous Main Event world-tick hook (P3.7).

Covers the two pure helpers — `is_autonomous` (the human-seat discriminator) and
`beats_to_world_events` (the structural-only beat filter) — and the registry
wrapper `advance_owner_tournament` end-to-end: an autonomous tournament spawned
into a temp sandbox advances round-by-round under the ticker, surfaces a winner
beat, and settles; a human-entered tournament is never auto-advanced.

See `docs/plans/P3_REMAINING_HANDOFF.md` §P3.7.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from cash_mode import activity
from flask_app.services import tournament_registry, tournament_ticker
from flask_app.services.tournament_spawn import (
    create_human_tournament,
    human_seat_id,
    spawn_autonomous_tournament,
)
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.schema_manager import SchemaManager
from poker.repositories.tournament_session_repository import TournamentSessionRepository

SB = 'sb-tick'
OWNER = 'owner-tick'


class FakePersonalityRepo:
    def __init__(self, ids):
        self._ids = ids

    def list_eligible_for_cash_mode(self, *, user_id=None):
        return [{'personality_id': pid, 'name': pid} for pid in self._ids]

    def load_personality_by_id(self, pid):
        # The canonical resolver turns a field id into the persona's display
        # name through this lookup (the same call cash seats use).
        return {'id': pid, 'name': pid} if pid in self._ids else None


@pytest.fixture
def repos():
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "tick.db")
        SchemaManager(path).ensure_schema()
        ledger_repo = ChipLedgerRepository(path)
        bankroll_repo = BankrollRepository(path)
        session_repo = TournamentSessionRepository(path)
        yield ledger_repo, bankroll_repo, session_repo
        ledger_repo.close()
        bankroll_repo.close()
        session_repo.close()


@pytest.fixture(autouse=True)
def clean_registry():
    tournament_registry.clear()
    yield
    tournament_registry.clear()


@pytest.fixture
def wired_session_repo(repos, monkeypatch):
    """Point the registry's durable backing at the temp session repo so its
    `persist` writes land in the same DB the settle does (hermetic — no live DB)."""
    _ledger, _bankroll, session_repo = repos
    from flask_app import extensions

    monkeypatch.setattr(extensions, 'tournament_session_repo', session_repo, raising=False)
    return session_repo


def _make_flush(ledger_repo):
    ledger_repo.record('central_bank', 'player:univ', 1_000_000, 'player_seed', sandbox_id=SB)
    ledger_repo.record('ai:donor', 'central_bank', 300_000, 'bank_pool_deposit', sandbox_id=SB)


# --- is_autonomous -----------------------------------------------------------


class _StubSession:
    def __init__(self, entries):
        self.entries = entries


def test_is_autonomous_true_without_human_seat():
    session = _StubSession({'persona_a': 'A', 'persona_b': 'B'})
    assert tournament_ticker.is_autonomous(session, OWNER) is True


def test_is_autonomous_false_with_human_seat():
    session = _StubSession({human_seat_id(OWNER): 'You', 'persona_b': 'B'})
    assert tournament_ticker.is_autonomous(session, OWNER) is False


# --- beats_to_world_events ---------------------------------------------------


def test_beats_filter_keeps_only_structural():
    beats = [
        {'type': 'knockout', 'round': 1, 'player_id': 'x'},
        {'type': 'table_break', 'round': 1, 'table_id': 'T2'},
        {'type': 'bubble', 'round': 2, 'paid_places': 3},
        {'type': 'milestone', 'round': 3, 'kind': 'final_table', 'remaining': 6},
        {'type': 'milestone', 'round': 4, 'kind': 'heads_up', 'remaining': 2},
    ]
    events = tournament_ticker.beats_to_world_events(
        beats, winner_name=None, sandbox_id=SB, complete=False, now=datetime(2026, 6, 2)
    )
    types = [e.type for e in events]
    # Knockouts + table breaks dropped; bubble + both milestones kept; no winner.
    assert types == [
        activity.EVENT_TOURNAMENT_BUBBLE,
        activity.EVENT_TOURNAMENT_MILESTONE,
        activity.EVENT_TOURNAMENT_MILESTONE,
    ]
    # Strictly-increasing created_at so same-tick beats keep distinct de-dup keys.
    stamps = [e.created_at for e in events]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps)
    # The milestones carry their kind in `reason` and a rendered message.
    assert events[1].reason == 'final_table'
    assert 'final table' in events[1].message
    assert all(e.sandbox_id == SB for e in events)


def test_beats_appends_winner_when_complete():
    events = tournament_ticker.beats_to_world_events(
        [], winner_name='Napoleon', sandbox_id=SB, complete=True, now=datetime(2026, 6, 2)
    )
    assert len(events) == 1
    assert events[0].type == activity.EVENT_TOURNAMENT_WINNER
    assert events[0].name == 'Napoleon'
    assert 'Napoleon' in events[0].message


def test_beats_no_winner_when_incomplete_even_with_name():
    events = tournament_ticker.beats_to_world_events(
        [], winner_name='Napoleon', sandbox_id=SB, complete=False
    )
    assert events == []


# --- advance_owner_tournament (registry wrapper, e2e) ------------------------


def _register_autonomous(spawned):
    """Put a spawned autonomous tournament into the registry the way the ticker
    expects to find it (no live game_id, fake resolver)."""
    from tournament.director import FakeHandResolver

    tournament_registry.put(
        spawned['tournament_id'],
        {
            'session': spawned['session'],
            'owner_id': OWNER,
            'created_at': datetime.utcnow().isoformat(),
            'resolver': FakeHandResolver(),
            'resolver_kind': 'fake',
            'game_id': None,
        },
    )


def test_advance_owner_tournament_plays_out_and_surfaces_winner(repos, wired_session_repo):
    ledger_repo, bankroll_repo, session_repo = repos
    _make_flush(ledger_repo)
    persona_repo = FakePersonalityRepo([f'persona_{i}' for i in range(8)])
    spawned = spawn_autonomous_tournament(
        owner_id=OWNER, sandbox_id=SB,
        personality_repo=persona_repo, bankroll_repo=bankroll_repo,
        ledger_repo=ledger_repo, session_repo=session_repo,
        field_size=6, table_size=3, starting_stack=10_000, seed=11, rng_seed=11,
    )
    assert spawned is not None
    _register_autonomous(spawned)

    all_events = []
    complete = False
    ticks = 0
    while not complete:
        result = tournament_ticker.advance_owner_tournament(
            owner_id=OWNER, sandbox_id=SB, registry=tournament_registry,
            session_repo=session_repo, bankroll_repo=bankroll_repo, ledger_repo=ledger_repo,
            personality_repo=persona_repo,
        )
        assert result is not None, "autonomous tournament should advance"
        all_events.extend(result['events'])
        complete = result['complete']
        ticks += 1
        assert ticks < 10_000

    # A winner beat surfaced exactly once, naming a real entrant by its persona
    # DISPLAY name (resolved via personality_repo) — NOT the bot archetype that
    # `session.entries` maps each persona to.
    winner_events = [e for e in all_events if e.type == activity.EVENT_TOURNAMENT_WINNER]
    assert len(winner_events) == 1
    assert winner_events[0].name in spawned['entries'].keys()
    assert winner_events[0].name not in spawned['entries'].values()
    # The winner event also carries the persona's stable id for linking.
    assert winner_events[0].personality_id in spawned['entries'].keys()
    # At least one field-collapse milestone surfaced along the way.
    assert any(e.type == activity.EVENT_TOURNAMENT_MILESTONE for e in all_events)
    # Settled: the durable row is terminal.
    assert session_repo.load(spawned['tournament_id'])['payout_status'] == 'complete'

    # Once complete, the next tick finds nothing active to advance.
    assert tournament_ticker.advance_owner_tournament(
        owner_id=OWNER, sandbox_id=SB, registry=tournament_registry,
        session_repo=session_repo, bankroll_repo=bankroll_repo, ledger_repo=ledger_repo,
    ) is None


def test_advance_skips_human_tournament(repos, wired_session_repo):
    ledger_repo, bankroll_repo, session_repo = repos
    _make_flush(ledger_repo)
    persona_repo = FakePersonalityRepo([f'persona_{i}' for i in range(8)])
    built = create_human_tournament(
        owner_id=OWNER, sandbox_id=SB,
        personality_repo=persona_repo, bankroll_repo=bankroll_repo,
        ledger_repo=ledger_repo, session_repo=session_repo,
        buy_in=0, field_size=6, table_size=3, starting_stack=10_000, seed=5, rng_seed=5,
    )
    assert built is not None  # registers itself into the registry

    # The human's tournament is player-gated — the ticker must not advance it.
    assert tournament_ticker.advance_owner_tournament(
        owner_id=OWNER, sandbox_id=SB, registry=tournament_registry,
        session_repo=session_repo, bankroll_repo=bankroll_repo, ledger_repo=ledger_repo,
    ) is None


def test_advance_returns_none_without_tournament(repos, wired_session_repo):
    ledger_repo, bankroll_repo, session_repo = repos
    assert tournament_ticker.advance_owner_tournament(
        owner_id=OWNER, sandbox_id=SB, registry=tournament_registry,
        session_repo=session_repo, bankroll_repo=bankroll_repo, ledger_repo=ledger_repo,
    ) is None
