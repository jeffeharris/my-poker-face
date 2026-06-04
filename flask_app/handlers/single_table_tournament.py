"""Single-table game ⇄ TournamentSession bridge (unification step 3B).

A single-table poker game is just a one-table tournament. This module builds a
`TournamentSession` for an ordinary game's real players and runs the light
hand-boundary that keeps the session's field (standings / eliminations /
completion) in sync with the live table.

Crucially the session is a PASSIVE observer here: the live `PokerStateMachine`
remains the authority for play and for blinds (it self-escalates from its own
`blind_config`). We never reconcile the live table off the session or impose a
session blind level — so single-table play is byte-for-byte unchanged. The
session only replaces what `TournamentTracker` used to do: track eliminations and
decide when the game is over, now through the same type the multi-table field
uses, feeding the one unified completion path.

Behaviour parity with the legacy tracker path (handle_eliminations /
check_tournament_complete):
  - every elimination emits `player_eliminated` + an "Nth place" table line;
  - the game ENDS the moment the human's fate is sealed — they bust (any number
    of opponents may remain) or they win heads-up;
  - on a human bust the end screen shows `winner: None` + `human_eliminated`,
    matching the legacy payload.
"""

from __future__ import annotations

import logging

from tournament.session import TournamentSession

logger = logging.getLogger(__name__)


def _ordinal_suffix(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return 'th'
    return {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')


def build_session_for_new_game(players, *, starting_stack: int, seed: int) -> TournamentSession:
    """Build a one-table session from a new game's player tuple. Seat order +
    names mirror the live table exactly; the human is flagged by `is_human`.

    `starting_stack` is the per-player BUY-IN — pass the configured value, not a
    live `player.stack`, since blinds are already posted by the time this runs
    (so live stacks differ and wouldn't conserve). `seed` only feeds the
    never-called single-table AI resolver, so any stable int is fine."""
    entries: dict[str, str] = {}
    human_id = None
    for p in players:
        entries[p.name] = 'human' if p.is_human else 'ai'
        if p.is_human:
            human_id = p.name
    if human_id is None:
        # Headless/AI-only games keep the first seat as the nominal "human" so
        # the session has a valid anchor; such games never drive this bridge.
        human_id = next(iter(entries))
    return TournamentSession.for_single_table(
        entries=entries, human_id=human_id, starting_stack=starting_stack, seed=seed
    )


def single_table_hand_boundary(
    game_id: str,
    game_data: dict,
    game_state,
    winning_player_names: list,
    final_hand_data: dict | None,
) -> bool:
    """Fold the just-finished hand into the session field, emit per-elimination
    beats, and signal whether the game is over. Returns True at the human's
    terminal moment (bust or heads-up win) — the caller then sets GAME_OVER."""
    from flask_app import extensions
    from flask_app.handlers.game_handler import send_message
    from flask_app.handlers.tournament_completion import (
        build_completion_result,
        finalize_tournament,
    )
    from poker.table.seat import seat_key

    session: TournamentSession = game_data['tournament_session']
    # Fold live stacks back into the field by the seat's STABLE id, not the
    # display name — the field (and `fold_live_hand`) key on `seat_id` /
    # `personality_id`, so a name-keyed map silently fails to update any seat
    # whose display name != id (e.g. the human, named for the owner), freezing
    # its field stack and breaking chip conservation. The build-time
    # `tournament_seat_ids` (display-name → field id) is the reliable bridge,
    # since the live Player's typed `seat_id` doesn't survive the per-hand
    # re-deal; `seat_key` is the fallback when the map is absent (older games).
    seat_ids = game_data.get('tournament_seat_ids') or {}

    def _field_id(p):
        return seat_ids.get(p.name) or seat_key(p)

    eliminator = winning_player_names[0] if winning_player_names else None
    stacks_after = {_field_id(p): p.stack for p in game_state.players}

    events = session.fold_live_hand(stacks_after, eliminator=eliminator)

    # Persist the field after every hand so a cold-load resumes with the right
    # standings/eliminations (best-effort; in-memory stays authoritative).
    try:
        from flask_app.services import tournament_registry

        tournament_registry.persist_single_session(
            game_id=game_id, owner_id=game_data.get('owner_id'), session=session
        )
    except Exception:  # noqa: BLE001
        logger.debug("[ST] session persist failed for %s", game_id, exc_info=True)

    socketio = extensions.socketio
    for e in events:
        suffix = _ordinal_suffix(e.finishing_position)
        if socketio is not None:
            socketio.emit(
                'player_eliminated',
                {
                    'eliminated': e.player_id,
                    'eliminator': e.eliminator,
                    'finishing_position': e.finishing_position,
                    'remaining_players': session.field.active_count,
                },
                to=game_id,
            )
        send_message(
            game_id,
            "Table",
            f"{e.player_id} has been eliminated in {e.finishing_position}{suffix} place!",
            "system",
        )

    human_out = session.human_out
    complete = session.is_complete()
    if not (human_out or complete):
        return False

    # Terminal: record career stats + result row once (idempotent), then emit
    # the end screen. On a human bust we present winner=None + human_eliminated,
    # matching the legacy single-table payload even if an opponent technically
    # holds every chip heads-up.
    finalize_tournament(game_id, game_data, emit=False)
    result = build_completion_result(
        session,
        game_id=game_id,
        biggest_pot=game_data.get('tournament_biggest_pot', 0),
        started_at=game_data.get('tournament_started_at'),
        personality_repo=getattr(extensions, 'personality_repo', None),
    )
    human_pos = result.get('human_finishing_position')

    if socketio is not None:
        socketio.emit(
            'tournament_complete',
            {
                'winner': None if human_out else result['winner_name'],
                'standings': result['standings'],
                'total_hands': result['total_hands'],
                'biggest_pot': result['biggest_pot'],
                'human_position': human_pos,
                'human_eliminated': human_out,
                'game_id': game_id,
                'final_hand_data': final_hand_data,
            },
            to=game_id,
        )

    if human_out and human_pos is not None:
        send_message(
            game_id,
            "Table",
            f"You finished in {human_pos}{_ordinal_suffix(human_pos)} place!",
            "system",
        )
    elif complete and result['winner_name']:
        send_message(game_id, "Table", f"TOURNAMENT OVER! {result['winner_name']} wins!", "system")
    return True
