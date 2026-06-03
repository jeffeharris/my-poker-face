"""Unit tests for the Presence state machine (Cut 3) and its repository.

Two layers:

1. The PURE machine (`cash_mode.presence`) — no DB, no I/O. These are the
   load-bearing tests: every legal edge, every illegal edge rejected, the
   seat/state invariant, sandbox scoping, and explicit proofs that
   `seated_and_idle` / `double_seat` are unrepresentable.

2. The repository (`poker.repositories.entity_presence_repository`) on a fresh
   temp SQLite DB built by `SchemaManager`. No Docker / Flask app needed, so the
   whole module runs standalone with `python3 -m pytest`.

Both layers are fast and unmarked (they don't cross into app/LLM/sim territory).
"""

import sqlite3
import tempfile

import pytest

from cash_mode.presence import (
    LEGAL_TRANSITIONS,
    IllegalPresenceTransition,
    Presence,
    PresenceEvent,
    PresenceState,
    PresenceState_,
    ai_entity_id,
    can_transition,
    offline,
    player_entity_id,
    transition,
)

# ===========================================================================
# Pure machine — constructors & invariants
# ===========================================================================


def test_offline_default_constructor():
    s = offline("ai:snoop", "sandbox-1")
    assert s.state is Presence.OFFLINE
    assert s.table_id is None and s.seat_index is None
    assert not s.is_seated and not s.is_off_grid


def test_entity_id_helpers_match_ledger_convention():
    assert player_entity_id("owner123") == "player:owner123"
    assert ai_entity_id("snoop") == "ai:snoop"


def test_seated_requires_seat_fields():
    with pytest.raises(IllegalPresenceTransition):
        PresenceState("ai:x", "s", Presence.SEATED)  # no table/seat
    with pytest.raises(IllegalPresenceTransition):
        PresenceState("ai:x", "s", Presence.SEATED, table_id="t1")  # no seat_index


def test_non_seated_must_not_carry_a_seat():
    # A non-seated entity holding a seat is exactly the ghost-seat bug.
    for st in (Presence.IDLE, Presence.OFFLINE, Presence.SIDE_HUSTLE, Presence.VICE, Presence.POOL):
        with pytest.raises(IllegalPresenceTransition):
            PresenceState("ai:x", "s", st, table_id="t1", seat_index=0)


def test_valid_seated_state_constructs():
    s = PresenceState("ai:x", "s", Presence.SEATED, table_id="t1", seat_index=2)
    assert s.is_seated
    assert s.table_id == "t1" and s.seat_index == 2


def test_states_are_frozen():
    s = offline("ai:x", "s")
    with pytest.raises(Exception):
        s.state = Presence.SEATED  # type: ignore[misc]


# ===========================================================================
# Pure machine — every LEGAL transition
# ===========================================================================


def _seat_kwargs(event):
    if event in (PresenceEvent.SIT, PresenceEvent.RESEAT):
        return {"table_id": "t1", "seat_index": 0}
    return {}


@pytest.mark.parametrize(
    "from_state,event,to_state", [(k[0], k[1], v) for k, v in LEGAL_TRANSITIONS.items()]
)
def test_every_legal_transition_lands_on_expected_state(from_state, event, to_state):
    # Build a valid `from` state (seat fields when seated).
    if from_state is Presence.SEATED:
        current = PresenceState("ai:x", "s", from_state, table_id="t0", seat_index=1)
    else:
        current = PresenceState("ai:x", "s", from_state)

    result = transition(current, event, **_seat_kwargs(event))
    assert result.state is to_state
    assert can_transition(current, event) is True
    # input unchanged (purity)
    assert current.state is from_state


def test_legal_transition_returns_new_object_not_mutated():
    s = offline("ai:x", "s")
    s2 = transition(s, PresenceEvent.SIT, table_id="t1", seat_index=0)
    assert s2 is not s
    assert s.state is Presence.OFFLINE  # original untouched
    assert s2.state is Presence.SEATED


def test_seated_carries_supplied_seat():
    s = offline("ai:x", "s")
    s2 = transition(s, PresenceEvent.SIT, table_id="tableA", seat_index=4)
    assert s2.table_id == "tableA" and s2.seat_index == 4


def test_leave_clears_seat():
    s = PresenceState("ai:x", "s", Presence.SEATED, table_id="t1", seat_index=0)
    s2 = transition(s, PresenceEvent.LEAVE)
    assert s2.state is Presence.IDLE
    assert s2.table_id is None and s2.seat_index is None


def test_reseat_from_idle():
    s = PresenceState("ai:x", "s", Presence.IDLE)
    s2 = transition(s, PresenceEvent.RESEAT, table_id="t2", seat_index=5)
    assert s2.state is Presence.SEATED and s2.seat_index == 5


def test_offgrid_round_trip():
    idle = PresenceState("ai:x", "s", Presence.IDLE)
    hustle = transition(idle, PresenceEvent.START_HUSTLE)
    assert hustle.state is Presence.SIDE_HUSTLE and hustle.is_off_grid
    back = transition(hustle, PresenceEvent.END_OFFGRID)
    assert back.state is Presence.IDLE

    vice = transition(idle, PresenceEvent.START_VICE)
    assert vice.state is Presence.VICE and vice.is_off_grid
    assert transition(vice, PresenceEvent.END_OFFGRID).state is Presence.IDLE


def test_pool_origin_seed_and_seat_and_return():
    # Pool-funded casino AI (§6.2): OFFLINE -> POOL -> SEATED -> POOL.
    s = offline("ai:fish1", "s")
    pooled = transition(s, PresenceEvent.SEED)
    assert pooled.state is Presence.POOL
    seated = transition(pooled, PresenceEvent.SIT, table_id="t1", seat_index=0)
    assert seated.state is Presence.SEATED
    returned = transition(seated, PresenceEvent.RETURN_TO_POOL)
    assert returned.state is Presence.POOL
    assert returned.table_id is None


# ===========================================================================
# Pure machine — ILLEGAL transitions are rejected (the structural guarantees)
# ===========================================================================


def _all_states():
    return list(PresenceState_)


def _build(state):
    if state is Presence.SEATED:
        return PresenceState("ai:x", "s", state, table_id="t0", seat_index=0)
    return PresenceState("ai:x", "s", state)


@pytest.mark.parametrize("state", _all_states())
@pytest.mark.parametrize("event", list(PresenceEvent))
def test_illegal_edges_raise_and_legal_edges_dont(state, event):
    current = _build(state)
    is_legal = (state, event) in LEGAL_TRANSITIONS
    if is_legal:
        # Should not raise (supply seat args where required).
        transition(current, event, **_seat_kwargs(event))
    else:
        with pytest.raises(IllegalPresenceTransition):
            transition(current, event, **_seat_kwargs(event))
        assert can_transition(current, event) is False


def test_seated_to_seated_is_illegal_no_double_seat():
    # The core double_seat guard at the machine level: you cannot SIT again
    # while already SEATED — you must LEAVE first.
    s = PresenceState("ai:x", "s", Presence.SEATED, table_id="t1", seat_index=0)
    with pytest.raises(IllegalPresenceTransition):
        transition(s, PresenceEvent.SIT, table_id="t2", seat_index=1)
    with pytest.raises(IllegalPresenceTransition):
        transition(s, PresenceEvent.RESEAT, table_id="t2", seat_index=1)


def test_idle_cannot_go_directly_offgrid_back_to_seated_without_seat_args():
    s = PresenceState("ai:x", "s", Presence.IDLE)
    # RESEAT without seat args is rejected.
    with pytest.raises(IllegalPresenceTransition):
        transition(s, PresenceEvent.RESEAT)


def test_sit_without_seat_args_rejected():
    s = offline("ai:x", "s")
    with pytest.raises(IllegalPresenceTransition):
        transition(s, PresenceEvent.SIT)


def test_seat_clearing_event_must_not_supply_seat():
    s = PresenceState("ai:x", "s", Presence.SEATED, table_id="t1", seat_index=0)
    with pytest.raises(IllegalPresenceTransition):
        transition(s, PresenceEvent.LEAVE, table_id="t1", seat_index=0)


def test_offgrid_states_cannot_sit_directly():
    # SIDE_HUSTLE/VICE must END_OFFGRID -> IDLE before sitting.
    for st in (Presence.SIDE_HUSTLE, Presence.VICE):
        s = PresenceState("ai:x", "s", st)
        with pytest.raises(IllegalPresenceTransition):
            transition(s, PresenceEvent.SIT, table_id="t1", seat_index=0)


# ===========================================================================
# seated_and_idle is unrepresentable (the headline invariant, I3)
# ===========================================================================


def test_seated_and_idle_is_unrepresentable():
    # A presence value holds exactly ONE state. There is no field, no
    # constructor, no transition that yields "seated AND idle". The closest a
    # caller can express is one or the other.
    seated = PresenceState("ai:x", "s", Presence.SEATED, table_id="t1", seat_index=0)
    idle = transition(seated, PresenceEvent.LEAVE)
    assert seated.state is Presence.SEATED
    assert idle.state is Presence.IDLE
    # They are distinct immutable values; neither can be both.
    assert seated.state is not idle.state
    # The state attribute is a single enum, not a set.
    assert isinstance(seated.state, PresenceState_)


# ===========================================================================
# Sandbox scoping — same entity, two sandboxes, two independent states
# ===========================================================================


def test_sandbox_scoping_independent_states():
    seated_s1 = transition(
        offline("ai:x", "sandbox-1"), PresenceEvent.SIT, table_id="t1", seat_index=0
    )
    idle_s2 = transition(
        transition(offline("ai:x", "sandbox-2"), PresenceEvent.SIT, table_id="t9", seat_index=0),
        PresenceEvent.LEAVE,
    )
    assert seated_s1.sandbox_id == "sandbox-1" and seated_s1.state is Presence.SEATED
    assert idle_s2.sandbox_id == "sandbox-2" and idle_s2.state is Presence.IDLE
    # Same entity_id, different sandbox => independent.
    assert seated_s1.entity_id == idle_s2.entity_id


# ===========================================================================
# Repository layer (temp SQLite, no app)
# ===========================================================================


@pytest.fixture
def presence_repo(tmp_path):
    from poker.repositories.entity_presence_repository import EntityPresenceRepository
    from poker.repositories.schema_manager import SchemaManager

    db = str(tmp_path / "presence.db")
    SchemaManager(db).ensure_schema()
    return EntityPresenceRepository(db)


def test_repo_load_defaults_offline(presence_repo):
    s = presence_repo.load("ai:snoop", "s1")
    assert s.state is Presence.OFFLINE


def test_repo_persist_and_reload(presence_repo):
    eid = ai_entity_id("snoop")
    s = presence_repo.persist_transition(eid, "s1", PresenceEvent.SIT, table_id="t1", seat_index=3)
    assert s.state is Presence.SEATED
    reloaded = presence_repo.load(eid, "s1")
    assert reloaded.state is Presence.SEATED
    assert reloaded.table_id == "t1" and reloaded.seat_index == 3


def test_repo_seat_occupant_lookup(presence_repo):
    eid = ai_entity_id("snoop")
    presence_repo.persist_transition(eid, "s1", PresenceEvent.SIT, table_id="t1", seat_index=0)
    occ = presence_repo.seat_occupant("s1", "t1", 0)
    assert occ is not None and occ.entity_id == eid
    assert presence_repo.seat_occupant("s1", "t1", 1) is None


def test_repo_leave_frees_seat(presence_repo):
    eid = ai_entity_id("snoop")
    presence_repo.persist_transition(eid, "s1", PresenceEvent.SIT, table_id="t1", seat_index=0)
    presence_repo.persist_transition(eid, "s1", PresenceEvent.LEAVE)
    assert presence_repo.seat_occupant("s1", "t1", 0) is None
    assert presence_repo.load(eid, "s1").state is Presence.IDLE


def test_repo_go_offline_deletes_row(presence_repo):
    eid = ai_entity_id("snoop")
    presence_repo.persist_transition(eid, "s1", PresenceEvent.SIT, table_id="t1", seat_index=0)
    presence_repo.persist_transition(eid, "s1", PresenceEvent.LEAVE)
    presence_repo.persist_transition(eid, "s1", PresenceEvent.GO_OFFLINE)
    assert presence_repo.load(eid, "s1").state is Presence.OFFLINE
    assert presence_repo.list_for_sandbox("s1") == []


def test_repo_double_seat_blocked_at_db_layer(presence_repo):
    # Even if the sandbox lock were somehow bypassed, the partial unique index
    # is the last-line backstop against double_seat.
    a, b = ai_entity_id("a"), player_entity_id("jeff")
    presence_repo.persist_transition(a, "s1", PresenceEvent.SIT, table_id="t1", seat_index=0)
    with pytest.raises(sqlite3.IntegrityError):
        presence_repo.persist_transition(b, "s1", PresenceEvent.SIT, table_id="t1", seat_index=0)


def test_repo_same_seat_different_sandbox_allowed(presence_repo):
    a, b = ai_entity_id("a"), ai_entity_id("b")
    presence_repo.persist_transition(a, "s1", PresenceEvent.SIT, table_id="t1", seat_index=0)
    # Same physical (table_id, seat_index) but a different sandbox is fine.
    presence_repo.persist_transition(b, "s2", PresenceEvent.SIT, table_id="t1", seat_index=0)
    assert presence_repo.seat_occupant("s1", "t1", 0).entity_id == a
    assert presence_repo.seat_occupant("s2", "t1", 0).entity_id == b


def test_repo_illegal_transition_leaves_row_untouched(presence_repo):
    eid = ai_entity_id("snoop")
    presence_repo.persist_transition(eid, "s1", PresenceEvent.SIT, table_id="t1", seat_index=0)
    with pytest.raises(IllegalPresenceTransition):
        # SIT-from-SEATED is illegal; row must be unchanged.
        presence_repo.persist_transition(eid, "s1", PresenceEvent.SIT, table_id="t2", seat_index=1)
    still = presence_repo.load(eid, "s1")
    assert still.table_id == "t1" and still.seat_index == 0


def test_repo_seated_and_idle_unrepresentable_one_row(presence_repo):
    # The compound PK means exactly one row -> exactly one state per entity.
    eid = ai_entity_id("snoop")
    presence_repo.persist_transition(eid, "s1", PresenceEvent.SIT, table_id="t1", seat_index=0)
    rows = presence_repo.list_for_sandbox("s1")
    assert len(rows) == 1
    presence_repo.persist_transition(eid, "s1", PresenceEvent.LEAVE)
    rows = presence_repo.list_for_sandbox("s1")
    assert len(rows) == 1 and rows[0].state is Presence.IDLE


def test_repo_save_offline_is_delete(presence_repo):
    eid = ai_entity_id("snoop")
    presence_repo.persist_transition(eid, "s1", PresenceEvent.SIT, table_id="t1", seat_index=0)
    presence_repo.save(offline(eid, "s1"))
    assert presence_repo.list_for_sandbox("s1") == []


def test_repo_migration_idempotent(tmp_path):
    from poker.repositories.schema_manager import SCHEMA_VERSION, SchemaManager

    db = str(tmp_path / "idem.db")
    SchemaManager(db).ensure_schema()
    # Re-run is a no-op.
    SchemaManager(db).ensure_schema()
    conn = sqlite3.connect(db)
    try:
        v = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        assert v == SCHEMA_VERSION
        # table present
        names = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "entity_presence" in names
    finally:
        conn.close()
