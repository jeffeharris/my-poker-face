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

from dataclasses import dataclass, field

from flask_app.services.tournament_naming import named_standings
from tournament.beats import build_beats, level_transition_beats
from tournament.session import TournamentSession, paid_places_for

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
    # Activity beats produced by the round(s) folded in at this boundary (KOs,
    # table breaks, bubble, milestones, level-ups). Rendered on the ticker /
    # toasts / hub feed; empty when nothing narratable happened.
    beats: list = field(default_factory=list)


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
    remaining_before = session.field.active_count
    level_before = session.current_level().level

    report = session.apply_live_round(human_table_result)
    standings = named_standings(session)

    beats = build_beats(
        [report],
        paid_places=paid_places_for(session.field.field_size),
        table_size=session.config.table_size,
        human_id=session.human_id,
        remaining_before=remaining_before,
    )
    # Blind-clock beats: announce the bump on the raise hand, and pre-announce it
    # one hand early ("blinds up next hand"). session.rounds is post-advance.
    beats += level_transition_beats(
        session.schedule,
        prev_level=level_before,
        rounds=session.rounds,
        round_index=report.round_index,
    )

    if session.is_complete():
        return BoundaryOutcome(COMPLETE, None, standings, beats)
    if session.human_out:
        return BoundaryOutcome(HUMAN_OUT, None, standings, beats)

    table_id = session.human_table.table_id
    kind = RELOCATED if table_id != prev_table_id else CONTINUE
    return BoundaryOutcome(kind, table_id, standings, beats)


def _real_persona_ids_for_session(session: TournamentSession) -> frozenset:
    """The field's real-persona ids (recomputed from the rehydrated session), used
    to gate dossier registration so synthetic `P##` fields write no lifetime rows.
    Best-effort — an empty set just means no AI seats register (no junk, no crash)."""
    try:
        from flask_app import extensions
        from flask_app.services import tournament_economy_service as econ

        return econ.real_persona_ids_for(session, getattr(extensions, 'personality_repo', None))
    except Exception:  # noqa: BLE001 — gating helper, never break the boundary
        return frozenset()


def reconcile_live_table(
    state_machine,
    ai_controllers: dict,
    memory_manager,
    seat_specs: list[SeatSpec],
    big_blind: int,
    *,
    make_controller,
    owner_name: str | None = None,
    real_persona_ids: frozenset[str] | set[str] = frozenset(),
    sandbox_id: str | None = None,
) -> tuple[list[str], list[str]]:
    """Mutate the human's live game to match the field's view of their table.

    Rebuilds the player tuple from `seat_specs` (busted players naturally drop
    out; players balanced in from other tables appear), prunes/creates AI
    controllers to match, and sets the dealer index + current blind. Mirrors the
    in-place swap pattern of cash mode's `_refill_cash_seats` — memory is keyed by
    NAME, so survivors keep their history and only genuinely-new seats are
    initialized.

    `make_controller(name, state_machine) -> controller` builds a controller for
    a newly-arrived seat. Returns (added_names, removed_names).
    """
    from flask_app import extensions
    from poker.poker_game import Player
    from poker.table.seat import HUMAN_KEY_PREFIX, HumanSeat, PersonaSeat
    from tournament.identity import resolve_display_name

    desired = list(seat_specs)
    new_players = tuple(
        Player(
            name=s.player_id,
            stack=s.stack,
            is_human=s.is_human,
            # Explicit persona identity (T3-80); None for the human seat (keyed by
            # owner_id, not the `human:<owner>` field id).
            personality_id=s.player_id if not s.is_human else None,
            # Canonical typed seat identity (T3-80). The human's field id is
            # `human:<owner>`, so strip the prefix to recover owner_id — HumanSeat
            # re-adds it, yielding the same field-aligned key. AI → PersonaSeat.
            seat_id=(
                HumanSeat(s.player_id.removeprefix(HUMAN_KEY_PREFIX))
                if s.is_human
                else PersonaSeat(s.player_id)
            ),
            # AI seats get their persona name; the human seat shows `owner_name`
            # when threaded through (the frontend still renders the human's own
            # seat as "You"). All seats resolve through the one canonical path.
            nickname=resolve_display_name(
                s.player_id,
                is_human=s.is_human,
                owner_name=owner_name,
                personality_repo=getattr(extensions, 'personality_repo', None),
            ),
        )
        for s in desired
    )
    dealer_idx = next((i for i, s in enumerate(desired) if s.is_button), 0)

    gs = state_machine.game_state
    state_machine.game_state = gs.update(
        players=new_players,
        current_ante=big_blind,
        last_raise_amount=big_blind,
        current_dealer_idx=dealer_idx,
    )

    desired_ai = {s.player_id for s in desired if not s.is_human}
    removed = [name for name in list(ai_controllers) if name not in desired_ai]
    for name in removed:
        del ai_controllers[name]

    added: list[str] = []
    for name in desired_ai:
        if name in ai_controllers:
            # Keep the existing controller; refresh its state-machine handle.
            ai_controllers[name].state_machine = state_machine
            continue
        controller = make_controller(name, state_machine)
        ai_controllers[name] = controller
        added.append(name)
        # T3-77 — a persona balanced ONTO the human's table mid-tournament is a
        # genuinely-new live seat, so hydrate its mood from the cash world just
        # like the initial builder does (off-table play is headless, so the
        # persona blob is the freshest mood available). Gated to a cash-world
        # persona field with a resolved sandbox; synthetic `P##` seats no-op.
        # This is a fresh-seat build, not cold-load, so D1 still holds.
        if (
            sandbox_id
            and name in real_persona_ids
            and getattr(extensions, 'bankroll_repo', None) is not None
        ):
            from cash_mode.psychology_persistence import hydrate_persona_psychology

            hydrate_persona_psychology(controller, name, extensions.bankroll_repo, sandbox_id)
        if memory_manager is not None:
            try:
                # P3.9a — register a balanced-in seat's personality_id (== name
                # for the MTT bridge) so its observations fold into the SAME
                # lifetime dossier row cash reads. Gated to real personas so a
                # synthetic `P##` field writes no junk rows. Mirrors the builder.
                memory_manager.initialize_for_player(
                    name, personality_id=name if name in real_persona_ids else None
                )
                controller.session_memory = memory_manager.get_session_memory(name)
                controller.opponent_model_manager = memory_manager.get_opponent_model_manager()
                controller.memory_manager = memory_manager
            except Exception:  # noqa: BLE001 — memory wiring is best-effort
                pass
    return added, removed


def advance_tournament_after_hand(
    game_data: dict, state_machine, *, make_controller
) -> BoundaryOutcome:
    """Core hand-boundary step for the human's tournament game (no I/O).

    Reads the just-finished hand's stacks off the live game, folds them into the
    field, paces the AI tables + settles, then either signals stop (human out /
    complete) or reconciles the live table for the next hand (continue /
    relocated). The effectful wrapper in game_handler handles socket emits + save
    + stopping the loop based on the returned outcome.
    """
    session: TournamentSession = game_data['tournament_session']
    prev_table_id = game_data['tournament_table_id']
    result = {p.name: p.stack for p in state_machine.game_state.players}

    outcome = coordinate_after_human_hand(session, result, prev_table_id)
    if outcome.kind in (HUMAN_OUT, COMPLETE):
        return outcome

    specs = human_table_seat_specs(session)
    reconcile_live_table(
        state_machine,
        game_data['ai_controllers'],
        game_data.get('memory_manager'),
        specs,
        session.current_level().big_blind,
        make_controller=make_controller,
        owner_name=game_data.get('owner_name'),
        real_persona_ids=_real_persona_ids_for_session(session),
        sandbox_id=game_data.get('tournament_sandbox_id'),
    )
    game_data['tournament_table_id'] = outcome.table_id
    game_data['hand_start_stacks'] = {p.name: p.stack for p in state_machine.game_state.players}
    return outcome


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
