"""Translate raw `RoundReport`s into typed "beats" for the activity surfaces.

A *beat* is one narratable thing that happened across the field — a knockout, a
table breaking, the bubble bursting, the blinds going up, the field collapsing to
a milestone. The headless engine already computes the raw material every round
(`RoundReport`: eliminations + seat moves + broken tables); this module is the
pure, I/O-free adapter that turns a burst of reports into the small dicts the
frontend ticker / toasts / hub feed render.

Pure by design (no Flask, no engine, no clock): the live bridge
(`tournament_handler.coordinate_after_human_hand`) and the AI-only play-out route
both call `build_beats(...)` on the reports they just produced and ship the result
on the existing `mtt_update` socket payload. Time is player-gated, so beats arrive
in a *burst* at each of the human's hand boundaries — "what happened across the
field since your last hand" — not continuously.

Each beat carries a `round` (the producing round index) so the frontend can build
a stable key (round + type + subject) for de-dup and entrance animation. Beats are
returned in chronological order within the burst (oldest first).
"""

from __future__ import annotations

from .blinds import BlindLevel
from .director import RoundReport

# Field sizes the ticker calls out as the field collapses. `table_size` (the
# final table forming) is added per-call; 3-handed and heads-up are universal.
_HEADS_UP = 2
_THREE_HANDED = 3


def level_up_beat(level: BlindLevel, *, round_index: int) -> dict:
    """A 'blinds are up' beat — the level in effect for the human's *next* hand
    jumped up (they're about to play the first hand at the higher level)."""
    return {
        'type': 'level_up',
        'round': round_index,
        'level': level.level,
        'small_blind': level.small_blind,
        'big_blind': level.big_blind,
        'ante': level.ante,
    }


def level_up_next_beat(level: BlindLevel, *, round_index: int) -> dict:
    """A 'blinds up next hand' pre-announce — the hand the human is about to play
    is the last at the current level; `level` is the (higher) level it raises to
    the hand after. The classic one-hand heads-up before the actual bump."""
    return {
        'type': 'level_up_next',
        'round': round_index,
        'level': level.level,
        'small_blind': level.small_blind,
        'big_blind': level.big_blind,
        'ante': level.ante,
    }


def level_transition_beats(
    schedule, *, prev_level: int, rounds: int, round_index: int
) -> list[dict]:
    """Blind-clock beats for one live boundary (pure).

    `prev_level` is the level of the hand just played; `rounds` is the session's
    round counter AFTER the advance (the round the *next* hand will play).
    Emits at most one beat:
      - `level_up` if the next hand is at a higher level than the one just played
        (the bump just happened — announce it on the raise hand); else
      - `level_up_next` if the hand *after* next is higher (the next hand is the
        last at this level — pre-announce the bump one hand early).
    """
    cur = schedule.level_for_round(rounds)
    if cur.level > prev_level:
        return [level_up_beat(cur, round_index=round_index)]
    upcoming = schedule.level_for_round(rounds + 1)
    if upcoming.level > cur.level:
        return [level_up_next_beat(upcoming, round_index=round_index)]
    return []


def build_beats(
    reports: list[RoundReport],
    *,
    paid_places: int,
    table_size: int,
    human_id: str,
    remaining_before: int,
) -> list[dict]:
    """Turn a burst of `RoundReport`s into ordered beat dicts.

    `paid_places` is how many finishers are in the money (for the bubble beat),
    `table_size` seeds the final-table milestone, `human_id` flags the human's own
    knockout, and `remaining_before` is the field's active count *before* the
    first report (so milestone crossings are detected without re-deriving it).
    """
    beats: list[dict] = []
    # Milestones fire as the field crosses *down* through these counts.
    thresholds = sorted(
        {t for t in (table_size, _THREE_HANDED, _HEADS_UP) if t >= _HEADS_UP},
        reverse=True,
    )
    remaining = remaining_before

    for report in reports:
        rnd = report.round_index

        # Knockouts (report order: worst finishing position first). The bubble
        # beat trails the knockout that burst it (finished one short of the cash).
        for elim in report.eliminations:
            beats.append(
                {
                    'type': 'knockout',
                    'round': rnd,
                    'player_id': elim.player_id,
                    'finishing_position': elim.finishing_position,
                    'eliminator': elim.eliminator,
                    'is_human': elim.player_id == human_id,
                }
            )
            if elim.finishing_position == paid_places + 1:
                beats.append(
                    {
                        'type': 'bubble',
                        'round': rnd,
                        'player_id': elim.player_id,
                        'paid_places': paid_places,
                    }
                )

        # Table breaks (an id present before the rebalance, gone after).
        for table_id in report.broken_tables:
            beats.append({'type': 'table_break', 'round': rnd, 'table_id': table_id})

        # Field-collapse milestones — emit each threshold this round crossed down
        # through, highest first (a big multi-bust round can cross several).
        new_remaining = remaining - len(report.eliminations)
        for threshold in thresholds:
            if remaining > threshold >= new_remaining:
                # Check final_table before heads_up so a 2-handed final table
                # (table_size == 2) reads "Final table", not "Heads up!".
                if threshold == table_size and threshold > _HEADS_UP:
                    kind = 'final_table'
                elif threshold == _HEADS_UP:
                    kind = 'heads_up'
                else:
                    kind = 'down_to'
                beats.append(
                    {
                        'type': 'milestone',
                        'round': rnd,
                        'kind': kind,
                        'remaining': threshold,
                    }
                )
        remaining = new_remaining

    return beats
