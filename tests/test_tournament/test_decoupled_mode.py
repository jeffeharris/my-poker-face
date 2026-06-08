"""Decoupled ("exhibition") tournament invariants.

A decoupled tournament is a fully-isolated standalone event: a real-persona
field for flavor but NO wires to the persistent world — no money, no
persona-mood carry, exempt from the one-active-per-owner guard. These pure
tests pin the seams that keep it isolated (see DECOUPLED_TOURNAMENT_MODE.md).
"""

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


def _session(*, decoupled: bool = False) -> TournamentSession:
    config = TournamentConfig(field_size=6, table_size=3, starting_stack=1000, seed=0)
    return TournamentSession(config, ai_resolver=FakeHandResolver(), decoupled=decoupled)


class TestSerialization:
    def test_decoupled_flag_round_trips(self):
        s = _session(decoupled=True)
        restored = TournamentSession.from_dict(s.to_dict(), ai_resolver=FakeHandResolver())
        assert restored.decoupled is True

    def test_default_is_coupled(self):
        s = _session()
        assert s.decoupled is False
        restored = TournamentSession.from_dict(s.to_dict(), ai_resolver=FakeHandResolver())
        assert restored.decoupled is False

    def test_legacy_blob_without_flag_defaults_coupled(self):
        # Blobs serialized before the flag existed must rehydrate as coupled.
        blob = _session().to_dict()
        blob.pop("decoupled", None)
        restored = TournamentSession.from_dict(blob, ai_resolver=FakeHandResolver())
        assert restored.decoupled is False


class TestActiveGuardExemption:
    def test_decoupled_is_not_the_active_event(self):
        tid = registry.new_tournament_id()
        registry.put(
            tid, {"session": _session(decoupled=True), "owner_id": "u1", "decoupled": True}
        )
        # Exempt from the one-active guard → never shadows the cash Main Event.
        assert registry.find_active_for_owner("u1") is None

    def test_coupled_still_counts_as_active(self):
        tid = registry.new_tournament_id()
        registry.put(tid, {"session": _session(), "owner_id": "u1"})
        assert registry.find_active_for_owner("u1") == tid

    def test_decoupled_does_not_mask_a_real_active_event(self):
        # A decoupled event alongside a real one returns the real one.
        registry.put(
            registry.new_tournament_id(),
            {"session": _session(decoupled=True), "owner_id": "u1", "decoupled": True},
        )
        real = registry.new_tournament_id()
        registry.put(real, {"session": _session(), "owner_id": "u1"})
        assert registry.find_active_for_owner("u1") == real

    def test_exemption_falls_back_to_session_flag(self):
        # Record without the propagated key still detected via the session.
        tid = registry.new_tournament_id()
        registry.put(tid, {"session": _session(decoupled=True), "owner_id": "u1"})
        assert registry.find_active_for_owner("u1") is None
