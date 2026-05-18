"""Seat filler — selects eligible AI personalities for open seats.

One job: walk open seats on a `CashTable`, find personalities whose
projected bankroll can afford the buy-in, and sit them via
`sit_down_ai`. Persistence (writing the post-sit `AIBankrollState`)
fires inside this module so the session loop's hand iteration
doesn't have to thread it.

Algorithm (per spec §"Fill-seats algorithm"):

  1. Source: `PersonalityRepository.list_eligible_for_cash_mode`
     (public personalities, deterministic by `personality_id`).
  2. Exclude personalities already seated (by display name, since
     hand engine keys pot lookup on `Player.name`).
  3. For each remaining candidate: read projected bankroll via
     `BankrollRepository.load_ai_bankroll_current`; skip if below
     `min_buy_in × buy_in_multiplier`.
  4. Buy-in = `min_buy_in × buy_in_multiplier`, clamped to `max_buy_in`.
  5. `sit_down_ai` in seat-index ascending order; persist the
     resulting `AIBankrollState`.

Returns the updated `CashTable`. The caller's `hand_in_progress`
flag must be False when this runs — sit_down_ai re-checks but
this is the seam where session.py would gate first.

Display-name collision (codex concern #9): the "already seated"
filter dedupes by display name. If two distinct personality_ids
share a display name AND both are eligible, the second is skipped
this hand. v1's seeded corpus has no collisions; v2 needs a
unique-name-within-table suffix scheme.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List

from cash_mode.bankroll import AIBankrollState
from cash_mode.seating import sit_down_ai
from cash_mode.table import PLAYER_SEAT_ID, CashTable
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.personality_repository import PersonalityRepository

logger = logging.getLogger(__name__)


def fill_seats(
    table: CashTable,
    *,
    personality_repo: PersonalityRepository,
    bankroll_repo: BankrollRepository,
    now: datetime,
    user_id: str = None,
) -> CashTable:
    """Fill every open seat on `table` with an eligible AI.

    Returns the new table state. Persists each new `AIBankrollState`
    via the repository as a side effect — necessary to make
    `load_ai_bankroll_current` consistent on the next read.

    If no eligible candidates exist (all priced out, or table is
    full), returns the table unchanged.

    `user_id` is forwarded to `list_eligible_for_cash_mode` so v2's
    private-personality-inclusion path drops in without a signature
    change. v1 callers can omit it.
    """
    open_indices = list(table.open_seats())
    if not open_indices:
        return table

    occupied_names = _occupied_seat_ids(table)

    candidates = personality_repo.list_eligible_for_cash_mode(user_id=user_id)
    if not candidates:
        return table

    # Pre-filter: exclude already-seated personalities and the player
    # sentinel. The PLAYER_SEAT_ID check is defensive — a personality
    # whose id collides with the literal "player" string would be a
    # config bug, but excluding it here means the filler can't seat
    # anyone in the human's seat by accident.
    eligible: List[dict] = []
    for c in candidates:
        if c["personality_id"] == PLAYER_SEAT_ID:
            continue
        if c["personality_id"] in occupied_names:
            continue
        if c["name"] in occupied_names:
            # Display-name collision with an already-seated personality.
            # See module docstring §"Display-name collision".
            continue
        eligible.append(c)

    if not eligible:
        return table

    # Per-candidate bankroll eligibility. Done lazily inside the loop
    # so a personality whose load_ai_bankroll_current returns None
    # doesn't have to be pre-resolved if a later seat doesn't reach it.
    for seat_index in sorted(open_indices):
        seated_this_pass = False
        while eligible:
            candidate = eligible.pop(0)
            pid = candidate["personality_id"]
            knobs = bankroll_repo.load_personality_knobs(pid)
            # `round` (not `int`) so a multiplier like 2.3 gives the
            # intuitive 920 chips off a 400-bb min, not 919. Floating-
            # point truncation here would be a UI surprise.
            threshold = round(table.min_buy_in * knobs.buy_in_multiplier)
            buy_in = min(threshold, table.max_buy_in)

            # First-sit-ever path: load_ai_bankroll returns None →
            # seed at bankroll_cap. The spec doesn't pin a starting
            # value explicitly; cap is the cleanest default and
            # matches "AI has been around forever, just hasn't sat
            # in cash mode yet."
            stored = bankroll_repo.load_ai_bankroll(pid)
            if stored is None:
                projected = knobs.bankroll_cap
                state_for_sit = AIBankrollState(
                    personality_id=pid,
                    chips=projected,
                    last_regen_tick=None,
                )
            else:
                projected = bankroll_repo.load_ai_bankroll_current(pid, now=now)
                # projected is None only if load_ai_bankroll returned None
                # (already handled above); the projection itself returns int.
                # Snap the in-memory state to the projected value so the
                # post-sit row reflects the live bankroll, not the stale
                # snapshot.
                state_for_sit = AIBankrollState(
                    personality_id=pid,
                    chips=projected,
                    last_regen_tick=stored.last_regen_tick,
                )

            if projected < threshold:
                # Not affordable — skip this candidate, try the next.
                continue

            try:
                table, new_state = sit_down_ai(
                    table, seat_index, pid, buy_in, state_for_sit, now=now,
                )
            except Exception as e:
                logger.warning(
                    "fill_seats: sit_down_ai failed for %r at seat %d: %s",
                    pid, seat_index, e,
                )
                continue

            bankroll_repo.save_ai_bankroll(new_state)
            occupied_names.add(pid)
            occupied_names.add(candidate["name"])
            seated_this_pass = True
            break  # this seat is filled; advance to the next seat_index

        if not seated_this_pass:
            # No more eligible candidates for any remaining seat.
            break

    return table


def _occupied_seat_ids(table: CashTable) -> set:
    """Return the set of occupied seat-ids (personality_ids + player sentinel)."""
    return {seat for seat in table.seats if seat is not None}
