"""Lobby table configuration — N tables per stake tier with names.

Single source of truth for the public-lobby table layout. The boot-time
seed (`ensure_lobby_seeded` in `cash_mode/lobby.py`) iterates this dict
to create exactly the tables listed, in order, per stake.

To add/rename/remove a lobby table: edit `LOBBY_TABLES` and restart.
The seed is idempotent — existing tables (matched by full `table_id`)
are preserved; only missing entries are created. Re-running on an
existing sandbox adds new tables without disturbing populated ones.

`id_suffix` is the trailing segment of the table_id
(`cash-table-{slug}-{suffix}`). Keep suffixes zero-padded to three
digits for sort-friendly ordering. `001` is the canonical first table
per stake (legacy table_id pre-v111 — preserved for back-compat with
tests and existing rows).

`name` is the human-facing label rendered in the lobby UI. Keep names
under ~18 chars so they fit the 180px min-card-width without truncation.
Theme by stake: low stakes feel scrappy (dive bars, home games), high
stakes feel exclusive (hotel mezzanines, private rooms). These are
placeholder flavor and can be tuned without code changes.

Future:
  - Private tables won't live here — they're DB rows with
    `table_type='private'` and an owner_id, created on demand.
  - Casino tables may live here OR get their own config when their
    semantics (different rake, themed AI lineup) get spec'd.
"""

from __future__ import annotations

from typing import Dict, List, TypedDict


class LobbyTableEntry(TypedDict):
    """One row in the lobby seed config."""

    id_suffix: str
    name: str


LOBBY_TABLES: Dict[str, List[LobbyTableEntry]] = {
    "$2": [
        {"id_suffix": "001", "name": "The Back Room"},
        {"id_suffix": "002", "name": "Coffee Counter"},
    ],
    "$10": [
        {"id_suffix": "001", "name": "Murphy's Bar"},
        {"id_suffix": "002", "name": "The Garage"},
        {"id_suffix": "003", "name": "Saturday Home Game"},
    ],
    "$50": [
        {"id_suffix": "001", "name": "Riverside Card Club"},
        {"id_suffix": "002", "name": "The Lodge"},
        {"id_suffix": "003", "name": "Tuesday Night Reg"},
    ],
    "$200": [
        {"id_suffix": "001", "name": "Hotel Mezzanine"},
        {"id_suffix": "002", "name": "The Quiet Room"},
    ],
    "$1000": [
        {"id_suffix": "001", "name": "High Roller Pit"},
    ],
}
