"""Canonical seat-identity model (T3-80 unification).

Locks in the invariants every identity bridge relies on:
  - a persona seat keys on its personality_id; the human seat on `human:<owner>`
  - SeatId round-trips through to_dict / seat_id_from_dict
  - seat_key falls back (personality_id, then name) for not-yet-migrated seats
  - two seats with the SAME display name still get DISTINCT keys (R1 collision)
"""

from poker.poker_game import Player
from poker.table.seat import HumanSeat, PersonaSeat, seat_id_from_dict, seat_key


def test_persona_seat_key_is_personality_id():
    p = Player(name="James Bond", stack=100, is_human=False, seat_id=PersonaSeat("james_bond"))
    assert seat_key(p) == "james_bond"


def test_human_seat_key_is_owner_prefixed():
    h = Player(name="Jeff", stack=100, is_human=True, seat_id=HumanSeat("guest_jeff"))
    assert seat_key(h) == "human:guest_jeff"


def test_seat_id_round_trips_through_dict():
    for sid in (PersonaSeat("sun_tzu"), HumanSeat("owner_1")):
        p = Player(name="x", stack=1, is_human=isinstance(sid, HumanSeat), seat_id=sid)
        assert seat_id_from_dict(p.to_dict()["seat_id"]) == sid


def test_seat_id_from_dict_handles_empty():
    assert seat_id_from_dict(None) is None
    assert seat_id_from_dict({}) is None


def test_seat_key_falls_back_to_personality_id_then_name():
    assert (
        seat_key(Player(name="Fish", stack=1, is_human=False, personality_id="fish_a")) == "fish_a"
    )
    assert seat_key(Player(name="Quickie", stack=1, is_human=False)) == "Quickie"


def test_duplicate_display_names_get_distinct_keys():
    a = Player(name="Fish", stack=1, is_human=False, seat_id=PersonaSeat("loose_larry"))
    b = Player(name="Fish", stack=1, is_human=False, seat_id=PersonaSeat("weak_walt"))
    assert seat_key(a) != seat_key(b)
