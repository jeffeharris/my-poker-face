"""Live-table bridge for multi-table tournaments (Phase 2c).

The human plays ONE table as an ordinary single-table game through the existing
`game_handler`. This module is the meta-layer coordination that runs at that
game's hand boundary: fold the live result into the field, pace the AI tables,
settle, and decide what happens to the human's game next.

The decision logic here is pure (no Flask / no engine types) so it can be unit
tested with a real `TournamentSession` and plain dicts. The thin effectful hook
that calls it — emitting socket events, rebuilding the human's game on
relocation, syncing roster/blinds on continue — lives in `game_handler` and
consumes the `SeatSpec`s this module produces.
"""

from __future__ import annotations

from dataclasses import dataclass

from tournament.session import TournamentSession

# Outcome kinds for the human's game after a hand boundary.
CONTINUE = 'continue'  # human still in, same table — deal the next hand
RELOCATED = 'relocated'  # human moved to a new table — rebuild their game there
HUMAN_OUT = 'human_out'  # human busted — stop their game, show standings
COMPLETE = 'complete'  # tournament finished


@dataclass(frozen=True)
class BoundaryOutcome:
    """What should happen to the human's live game after one hand boundary."""

    kind: str
    table_id: int | None
    standings: dict


@dataclass(frozen=True)
class SeatSpec:
    """One seat at the human's table, the contract the game builder/sync uses to
    construct or reconcile the live game's player tuple."""

    player_id: str
    stack: int
    archetype: str
    is_human: bool
    is_button: bool


def coordinate_after_human_hand(
    session: TournamentSession,
    human_table_result: dict[str, int],
    prev_table_id: int,
) -> BoundaryOutcome:
    """Fold the live human-table result into the field, advance the AI tables,
    settle, and classify the outcome for the human's game.

    `human_table_result` is `{player_id: stack}` for every seat at the human's
    table after the just-completed live hand. `prev_table_id` is the table the
    human's game was running before this boundary (to detect relocation).
    """
    session.apply_live_round(human_table_result)
    standings = session.standings_view()

    if session.is_complete():
        return BoundaryOutcome(COMPLETE, None, standings)
    if session.human_out:
        return BoundaryOutcome(HUMAN_OUT, None, standings)

    table_id = session.human_table.table_id
    kind = RELOCATED if table_id != prev_table_id else CONTINUE
    return BoundaryOutcome(kind, table_id, standings)


def human_table_seat_specs(session: TournamentSession) -> list[SeatSpec]:
    """The seats at the human's current table (in seat order) — used to build or
    reconcile the live game's players. Raises if the human is out."""
    table = session.human_table
    if table is None:
        raise RuntimeError("human is not seated — no table to build")
    dealer_index = table.dealer_index_in_occupied()
    specs: list[SeatSpec] = []
    for i, pid in enumerate(table.players):
        specs.append(
            SeatSpec(
                player_id=pid,
                stack=session.field.stacks[pid],
                archetype=session.entries[pid],
                is_human=(pid == session.human_id),
                is_button=(i == dealer_index),
            )
        )
    return specs
