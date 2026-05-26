"""Fake-hand simulator for unseated cash tables.

In v1.5 the lobby's `refresh_unseated_tables` runs on every lobby
read, evaluates AI movement, and live-fills open seats. But AI chip
counts at unseated tables stay frozen — there are no actual hands
running — so won-big / lost-big movement triggers never fire from
ambient world activity, only as a delayed consequence of player
sessions.

`roll_fake_hand` is the cheap fix that makes other tables feel
alive: roll a zero-sum chip movement between two AIs at the table.
No cards, no betting rounds, no controllers — just a random pot
size capped to keep swings believable. Big enough rolls (above the
threshold) emit lobby activity events (`big_win`/`big_loss`).

**Honest by construction:** the returned `new_seats` carry the
mutated chip counts. The caller persists them via `cash_table_repo.
save_table`, so when the player taps that table to join, they see
the post-fake-sim chip counts in the live game. The AI's bankroll
doesn't credit until the player actually sits and the existing
leave-time path settles the session — fake-sim P&L is "predicted"
P&L, and real sessions ratify it.

**Conservation:** chips are zero-sum per pair. Total chips at the
table stay constant. (An AI being driven to 0 still flows through
the normal `forced_leave` movement path on the next refresh tick.)

Pure function. The caller (lobby refresh loop) decides the per-
table probability gate, supplies the RNG, and handles event
emission + persistence. This module knows nothing about Flask,
repos, or the activity buffer.

Spec: design discussion 2026-05-19, "fake-sim lite" as the
intermediate step between v1.5 lobby and full Path C background
sim.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional

# Pot size cap as multiples of the table's big blind. 15 BB is a
# generous-but-realistic ceiling — actual poker pots above this are
# rare. Caps stack swings so a single fake hand can't wipe a seat.
DEFAULT_MAX_POT_BB = 15

# Threshold (multiples of big blind) above which a fake hand emits
# a big_win / big_loss event. Smaller deltas drift the chips quietly
# without spamming the ticker. 8 BB is a "you'd notice it" pot.
DEFAULT_BIG_EVENT_THRESHOLD_BB = 8

# Per-table probability gate (per lobby read). With ~5 unseated
# tables × an 8-second polling cadence, 0.25 averages out to one
# fake hand per table every ~30 seconds — enough to keep the world
# breathing without flooding the ticker.
DEFAULT_FAKE_HAND_PROB = 0.25


@dataclass(frozen=True)
class FakeHandResult:
    """Outcome of one fake-sim roll.

    `new_seats` is the table's seats list with winner/loser chip
    counts updated. Always returned (equal to input on no-op).
    `winner_pid` / `loser_pid` are None when the roll was a no-op
    (fewer than 2 AI seats with positive chips, or pot cap hit 0).
    `delta` is the chips moved; 0 on no-op.
    `big_event` is True iff `delta >= big_blind × big_event_threshold_bb`.
    """

    new_seats: List[dict] = field(default_factory=list)
    winner_pid: Optional[str] = None
    loser_pid: Optional[str] = None
    delta: int = 0
    big_event: bool = False


def _copy_seats(seats: List[dict]) -> List[dict]:
    """Deep-copy the seats list so callers never see in-place
    mutation of input data."""
    return [dict(s) for s in seats]


def roll_fake_hand(
    seats: List[dict],
    *,
    big_blind: int,
    rng: random.Random,
    max_pot_bb: int = DEFAULT_MAX_POT_BB,
    big_event_threshold_bb: int = DEFAULT_BIG_EVENT_THRESHOLD_BB,
) -> FakeHandResult:
    """Roll one fake hand between two AIs at a table.

    Picks winner and loser uniformly from AI seats with positive
    chips. Pot size is uniform random in `[1, min(loser_chips,
    max_pot_bb × big_blind)]` — capped so a single hand can't wipe
    a seat. The returned `new_seats` reflect the move (winner += pot,
    loser -= pot).

    No-op cases (winner_pid=None, delta=0):
      - fewer than 2 AI seats with positive chips
      - loser's chip cap rounds the max pot to 0

    Pure: only RNG consumption is observable.
    """
    ai_indices = [i for i, s in enumerate(seats) if s.get("kind") == "ai" and s.get("chips", 0) > 0]
    if len(ai_indices) < 2:
        return FakeHandResult(new_seats=_copy_seats(seats))

    winner_idx = rng.choice(ai_indices)
    loser_pool = [i for i in ai_indices if i != winner_idx]
    loser_idx = rng.choice(loser_pool)

    loser_chips = int(seats[loser_idx].get("chips", 0))
    max_pot = min(loser_chips, big_blind * max_pot_bb)
    if max_pot < 1:
        return FakeHandResult(new_seats=_copy_seats(seats))

    delta = rng.randint(1, max_pot)

    new_seats = _copy_seats(seats)
    new_seats[winner_idx] = {
        **new_seats[winner_idx],
        "chips": int(new_seats[winner_idx].get("chips", 0)) + delta,
    }
    new_seats[loser_idx] = {
        **new_seats[loser_idx],
        "chips": loser_chips - delta,
    }

    return FakeHandResult(
        new_seats=new_seats,
        winner_pid=new_seats[winner_idx].get("personality_id"),
        loser_pid=new_seats[loser_idx].get("personality_id"),
        delta=delta,
        big_event=delta >= big_blind * big_event_threshold_bb,
    )
