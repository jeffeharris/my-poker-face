"""Tests for the Presence-machine shadow-write helper (cutover Phase 1).

`cash_mode.presence_shadow.shadow_transition` is the single funnel every
seat/idle/hustle/vice reroute call site uses to dual-write into the dormant
`entity_presence` table. Its two guarantees — gated on a default-off kill
switch, and never-raises so it can't break the real write it shadows — are
what make the parallel cutover safe, so they're pinned here.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

from cash_mode import economy_flags, presence_shadow
from cash_mode.presence import PresenceEvent, ai_entity_id, player_entity_id
from poker.repositories.entity_presence_repository import EntityPresenceRepository
from poker.repositories.schema_manager import SchemaManager


@pytest.fixture
def repo(tmp_path):
    p = str(tmp_path / "presence.db")
    SchemaManager(p).ensure_schema()
    return EntityPresenceRepository(p)


@pytest.fixture(autouse=True)
def _flag_off():
    """Default BOTH presence gates OFF around every test; restore after. These
    are shadow-flag-isolation unit tests: `presence_shadow.is_enabled()` is
    `SHADOW or AUTHORITY`, and authority is hardwired True in prod, so we pin it
    off here to isolate the shadow flag's effect."""
    prev_shadow = economy_flags.PRESENCE_SHADOW_WRITE_ENABLED
    prev_authority = economy_flags.PRESENCE_AUTHORITY_ENABLED
    economy_flags.PRESENCE_SHADOW_WRITE_ENABLED = False
    economy_flags.PRESENCE_AUTHORITY_ENABLED = False
    yield
    economy_flags.PRESENCE_SHADOW_WRITE_ENABLED = prev_shadow
    economy_flags.PRESENCE_AUTHORITY_ENABLED = prev_authority


def test_disabled_is_a_noop(repo):
    """Both gates off → a shadow call writes nothing."""
    eid = player_entity_id("jeff")
    presence_shadow.shadow_transition(
        entity_id=eid,
        sandbox_id="sb",
        event=PresenceEvent.SIT,
        table_id="t1",
        seat_index=2,
        repo=repo,
    )
    assert repo.load(eid, "sb").state.value == "offline"


def test_enabled_writes_the_transition(repo):
    economy_flags.PRESENCE_SHADOW_WRITE_ENABLED = True
    eid = player_entity_id("jeff")
    presence_shadow.shadow_transition(
        entity_id=eid,
        sandbox_id="sb",
        event=PresenceEvent.SIT,
        table_id="t1",
        seat_index=2,
        repo=repo,
    )
    st = repo.load(eid, "sb")
    assert st.state.value == "seated"
    assert st.table_id == "t1"
    assert st.seat_index == 2


def test_illegal_transition_is_swallowed_state_intact(repo):
    """An illegal edge (SIT from SEATED) must not raise and must leave the
    existing row untouched — a shadow failure can't corrupt state."""
    economy_flags.PRESENCE_SHADOW_WRITE_ENABLED = True
    eid = player_entity_id("jeff")
    presence_shadow.shadow_transition(
        entity_id=eid,
        sandbox_id="sb",
        event=PresenceEvent.SIT,
        table_id="t1",
        seat_index=2,
        repo=repo,
    )
    # SIT again (illegal — must LEAVE first). Must be swallowed.
    presence_shadow.shadow_transition(
        entity_id=eid,
        sandbox_id="sb",
        event=PresenceEvent.SIT,
        table_id="t9",
        seat_index=5,
        repo=repo,
    )
    st = repo.load(eid, "sb")
    assert st.state.value == "seated"
    assert st.table_id == "t1"  # unchanged


def test_double_seat_is_swallowed(repo):
    """Two entities into one seat: the DB partial-unique index rejects the
    second; the helper swallows it and the first occupant stands."""
    economy_flags.PRESENCE_SHADOW_WRITE_ENABLED = True
    presence_shadow.shadow_transition(
        entity_id=player_entity_id("jeff"),
        sandbox_id="sb",
        event=PresenceEvent.SIT,
        table_id="t1",
        seat_index=2,
        repo=repo,
    )
    presence_shadow.shadow_transition(
        entity_id=ai_entity_id("bot"),
        sandbox_id="sb",
        event=PresenceEvent.SIT,
        table_id="t1",
        seat_index=2,
        repo=repo,
    )
    occupant = repo.seat_occupant("sb", "t1", 2)
    assert occupant is not None
    assert occupant.entity_id == "player:jeff"


def test_missing_repo_is_swallowed(repo):
    """Enabled but no repo available (None, e.g. outside the app) → no raise."""
    economy_flags.PRESENCE_SHADOW_WRITE_ENABLED = True
    # Explicit repo=None and no extensions singleton in test context.
    presence_shadow.shadow_transition(
        entity_id=player_entity_id("jeff"),
        sandbox_id="sb",
        event=PresenceEvent.SIT,
        table_id="t1",
        seat_index=2,
        repo=None,
    )
    # Nothing to assert beyond "did not raise".


def test_is_enabled_reads_flag_live(repo):
    economy_flags.PRESENCE_SHADOW_WRITE_ENABLED = False
    assert presence_shadow.is_enabled() is False
    economy_flags.PRESENCE_SHADOW_WRITE_ENABLED = True
    assert presence_shadow.is_enabled() is True
