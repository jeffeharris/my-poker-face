"""Canonical seat identity for poker tables (T3-80 unification).

ONE identity model for every mode — cash, tournament (live + headless), and
regular games. A seat's stable identity is a typed `SeatId`:

  - ``PersonaSeat(personality_id)`` — an AI persona; stable key is the slug.
  - ``HumanSeat(owner_id)``        — the human; stable key is their owner id.

The rules this enshrines:

  * ``Player.name`` is ALWAYS the human-readable display name. It is never used
    as a key (display names collide — two "Fish" seats, edited names, etc.).
  * Identity bridges — controller maps, memory/dossier registration, tournament
    field entries / eliminations / payouts, cold-load controller maps, the
    live-result write-back — key on ``seat_key(player)`` (a string derived from
    the typed id), NEVER on the display name.
  * The human's stable key is ``human:<owner_id>``, which matches the tournament
    field's existing human entry-id convention, so the live seat key and the
    field key line up for free.

This module is intentionally pure — it imports nothing from the poker engine or
Flask — so it can be used anywhere without import cycles.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

# Prefix marking the human seat's stable key. Matches the tournament field's
# `human:<owner>` entry-id convention so live and field keys coincide.
HUMAN_KEY_PREFIX = "human:"


@dataclass(frozen=True)
class PersonaSeat:
    """An AI persona seat. Stable key is the personality slug."""

    personality_id: str

    @property
    def key(self) -> str:
        return self.personality_id

    def to_dict(self) -> dict:
        return {"kind": "persona", "personality_id": self.personality_id}


@dataclass(frozen=True)
class HumanSeat:
    """The human seat. Stable key is `human:<owner_id>`."""

    owner_id: str

    @property
    def key(self) -> str:
        return f"{HUMAN_KEY_PREFIX}{self.owner_id}"

    def to_dict(self) -> dict:
        return {"kind": "human", "owner_id": self.owner_id}


# A seat's typed identity. Two shapes — never a bare display string.
SeatId = Union[PersonaSeat, HumanSeat]


def seat_id_from_dict(data: Optional[dict]) -> Optional[SeatId]:
    """Reconstruct a `SeatId` from its serialized form (round-trips `to_dict`)."""
    if not data:
        return None
    kind = data.get("kind")
    if kind == "persona":
        return PersonaSeat(data["personality_id"])
    if kind == "human":
        return HumanSeat(data["owner_id"])
    return None


def seat_key(player) -> str:
    """The stable identity key for a seat — what every identity bridge keys on.

    Prefers the typed ``seat_id``. During the T3-80 migration it falls back to
    the legacy ``personality_id`` and finally the display ``name``, so a
    construction site that hasn't been migrated to stamp ``seat_id`` yet still
    resolves to a usable key. Once every builder stamps ``seat_id`` the
    fallbacks are dead and can be removed.
    """
    seat_id = getattr(player, "seat_id", None)
    if seat_id is not None:
        return seat_id.key
    personality_id = getattr(player, "personality_id", None)
    if personality_id:
        return personality_id
    return player.name
