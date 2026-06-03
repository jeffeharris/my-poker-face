"""Tests for the real-persona tournament field builder (P3 foundation)."""

from __future__ import annotations

import pytest

from flask_app.services.tournament_field import assign_archetypes, select_persona_field
from tournament.config import DEFAULT_FIELD_ARCHETYPES, TournamentConfig
from tournament.director import build_initial_state

try:
    from experiments.simulate_bb100 import ARCHETYPES
except Exception:  # pragma: no cover - engine import optional in pure runs
    ARCHETYPES = None


class FakePersonalityRepo:
    def __init__(self, ids):
        self._ids = ids

    def list_eligible_for_cash_mode(self, *, user_id=None):
        return [{'personality_id': pid, 'name': pid.title()} for pid in self._ids]


class TestAssignArchetypes:
    def test_cycles_in_order(self):
        out = assign_archetypes(['a', 'b', 'c'], ('X', 'Y'))
        assert out == {'a': 'X', 'b': 'Y', 'c': 'X'}
        assert list(out) == ['a', 'b', 'c']  # order preserved (seat order)

    def test_empty_archetypes_raises(self):
        with pytest.raises(ValueError):
            assign_archetypes(['a'], ())


class TestSelectPersonaField:
    def test_fields_real_personas(self):
        repo = FakePersonalityRepo([f'persona_{i}' for i in range(10)])
        entries = select_persona_field(
            personality_repo=repo, owner_id='alice', field_size=6, rng_seed=1
        )
        assert len(entries) == 6
        assert all(k.startswith('persona_') for k in entries)
        # archetypes are all valid default keys
        assert set(entries.values()) <= set(DEFAULT_FIELD_ARCHETYPES)

    def test_human_takes_a_seat_first(self):
        repo = FakePersonalityRepo([f'persona_{i}' for i in range(10)])
        entries = select_persona_field(
            personality_repo=repo,
            owner_id='alice',
            field_size=6,
            rng_seed=1,
            human_id='human:alice',
        )
        assert len(entries) == 6
        assert list(entries)[0] == 'human:alice'  # human first
        # the rest are personas (5 of them)
        assert sum(1 for k in entries if k.startswith('persona_')) == 5

    def test_capped_at_pool_size(self):
        repo = FakePersonalityRepo(['p0', 'p1', 'p2'])
        entries = select_persona_field(
            personality_repo=repo, owner_id='alice', field_size=9, rng_seed=1
        )
        assert len(entries) == 3  # can't field more than the pool

    def test_deterministic_by_seed(self):
        repo = FakePersonalityRepo([f'p{i}' for i in range(20)])
        a = select_persona_field(personality_repo=repo, owner_id='x', field_size=6, rng_seed=42)
        b = select_persona_field(personality_repo=repo, owner_id='x', field_size=6, rng_seed=42)
        c = select_persona_field(personality_repo=repo, owner_id='x', field_size=6, rng_seed=99)
        assert a == b  # same seed → same field
        assert list(a) != list(c) or a != c  # different seed → (very likely) different

    def test_none_repo_is_empty(self):
        entries = select_persona_field(
            personality_repo=None, owner_id='alice', field_size=6, rng_seed=1
        )
        assert entries == {}

    def test_entries_drive_build_initial_state(self):
        """The produced entries plug straight into the engine's field builder
        with real-persona player_ids as seat identities."""
        repo = FakePersonalityRepo([f'persona_{i}' for i in range(6)])
        entries = select_persona_field(
            personality_repo=repo, owner_id='alice', field_size=6, rng_seed=3
        )
        config = TournamentConfig(field_size=len(entries), table_size=3, starting_stack=10_000)
        player_ids, built_entries, field, seating = build_initial_state(config, entries=entries)
        assert set(player_ids) == set(entries)
        assert field.field_size == 6
        field.assert_conservation()


class TestScoredOrder:
    """tournaments-as-a-draw: the invite's draw-ranked reserved_pids order the
    field (reserved-and-eligible first) instead of a blind shuffle."""

    def test_reserved_seated_first_in_rank_order(self):
        repo = FakePersonalityRepo([f'p{i}' for i in range(10)])
        entries = select_persona_field(
            personality_repo=repo,
            owner_id='x',
            field_size=3,
            rng_seed=1,
            scored_order=['p7', 'p2', 'p5'],
        )
        # The top-3 draws take the seats, in draw order.
        assert list(entries) == ['p7', 'p2', 'p5']

    def test_excluded_reserved_persona_is_skipped(self):
        # p7 is reserved-top but still cash-seated (excluded) → fail-closed: it's
        # skipped and fill takes over; the remaining reserved keep their order.
        repo = FakePersonalityRepo([f'p{i}' for i in range(10)])
        entries = select_persona_field(
            personality_repo=repo,
            owner_id='x',
            field_size=3,
            rng_seed=1,
            scored_order=['p7', 'p2', 'p5'],
            exclude={'p7'},
        )
        assert 'p7' not in entries
        # p2, p5 stay first (in order); the 3rd seat fills from the rest.
        assert list(entries)[:2] == ['p2', 'p5']
        assert len(entries) == 3

    def test_reserved_short_fills_remaining_seats(self):
        repo = FakePersonalityRepo([f'p{i}' for i in range(10)])
        entries = select_persona_field(
            personality_repo=repo,
            owner_id='x',
            field_size=4,
            rng_seed=1,
            scored_order=['p3'],  # only one reserved
        )
        assert list(entries)[0] == 'p3'
        assert len(entries) == 4  # rest filled from the pool

    def test_human_first_then_reserved(self):
        repo = FakePersonalityRepo([f'p{i}' for i in range(10)])
        entries = select_persona_field(
            personality_repo=repo,
            owner_id='x',
            field_size=3,
            rng_seed=1,
            human_id='human:x',
            scored_order=['p8', 'p1'],
        )
        assert list(entries) == ['human:x', 'p8', 'p1']

    def test_unknown_reserved_ids_ignored(self):
        # reserved ids not in the eligible pool (busted/ineligible) just drop out.
        repo = FakePersonalityRepo(['p0', 'p1', 'p2'])
        entries = select_persona_field(
            personality_repo=repo,
            owner_id='x',
            field_size=2,
            rng_seed=1,
            scored_order=['ghost', 'p1'],
        )
        assert list(entries)[0] == 'p1'
        assert len(entries) == 2


@pytest.mark.skipif(ARCHETYPES is None, reason="engine archetypes unavailable")
def test_default_archetypes_are_valid_engine_keys():
    """The funny-money EngineHandResolver requires every archetype VALUE to be a
    real ARCHETYPES key — guard that the defaults we assign satisfy it."""
    assert set(DEFAULT_FIELD_ARCHETYPES) <= set(ARCHETYPES)
