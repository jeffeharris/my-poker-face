"""Shared table primitives (T3-80 tournament/cash unification).

Currently exposes the canonical seat-identity model (`seat.py`). The `Table`
primitive + runners land in Phase 2.
"""

from .seat import HumanSeat, PersonaSeat, SeatId, seat_id_from_dict, seat_key

__all__ = ["HumanSeat", "PersonaSeat", "SeatId", "seat_id_from_dict", "seat_key"]
