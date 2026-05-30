"""Unified tournament completion — one result shape + one finalize path for both
single-table and multi-table tournaments.

Step 3 of the tournament-unification work (`docs/plans/TOURNAMENT_UNIFICATION_STEP3.md`).
Historically the single-table game built its end-of-tournament result from
`poker/tournament_tracker.py::TournamentTracker.get_result()`, while the
multi-table tournament (`tournament/session.py::TournamentSession`) had no
equivalent — it ended only via the standings hub and never wrote career stats.

`build_completion_result` derives the **same** result dict a `TournamentTracker`
produced, but from a `TournamentSession`'s field/standings. `finalize_tournament`
is the single completion side-effect path: persist the result row, update the
human's career stats, and (optionally) emit `tournament_complete` to the game
room so both kinds of tournament land on the same end screen.

The session tracks chips/eliminations but not pot sizes, so `biggest_pot` is
threaded in from the live game (`game_data['tournament_biggest_pot']`, updated by
the game handler at each hand boundary).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _ordinal_position(session, player_id: str) -> Optional[int]:
    """Finishing position for a player in a *complete* session: 1 for the
    winner, else their elimination position."""
    if session.winner() == player_id:
        return 1
    for e in session.field.eliminations:
        if e.player_id == player_id:
            return e.finishing_position
    return None


def build_completion_result(
    session,
    *,
    game_id: str,
    biggest_pot: int = 0,
    started_at: Optional[str] = None,
) -> dict[str, Any]:
    """Build the tournament-result dict (the shape `tournament_repo` and the
    `tournament_complete` event consume) from a completed `TournamentSession`.

    Standings mirror `TournamentTracker.get_standings`: winner first (position
    1), then eliminations ordered by finishing position. Player ids double as
    display names (MTT seats are `P01`…; single-table games use real names)."""
    human_id = session.human_id
    winner_id = session.winner()

    standings: list[dict[str, Any]] = []
    if winner_id is not None:
        standings.append(
            {
                'player_name': winner_id,
                'is_human': winner_id == human_id,
                'finishing_position': 1,
                'eliminated_by': None,
                'eliminated_at_hand': None,
            }
        )
    for e in session.field.eliminations:
        standings.append(
            {
                'player_name': e.player_id,
                'is_human': e.player_id == human_id,
                'finishing_position': e.finishing_position,
                'eliminated_by': e.eliminator,
                'eliminated_at_hand': e.round_index,
            }
        )
    standings.sort(key=lambda s: s['finishing_position'])

    return {
        'game_id': game_id,
        'winner_name': winner_id,
        'total_hands': session._hand_counter,
        'biggest_pot': biggest_pot,
        'starting_player_count': session.field.field_size,
        'human_player_name': human_id,
        'human_finishing_position': _ordinal_position(session, human_id),
        'started_at': started_at,
        'standings': standings,
    }


def finalize_tournament(
    game_id: str,
    game_data: dict,
    *,
    final_hand_data: Optional[dict] = None,
    emit: bool = False,
) -> bool:
    """Persist the result + career stats for a completed session-backed
    tournament, optionally emitting `tournament_complete` to the game room.

    Fires at the human's terminal moment — either the field is COMPLETE (the
    human won, or busted on the final hand) or the human is OUT (busted earlier).
    The latter mirrors the single-table tracker, which records the human's
    career result the moment they're eliminated (winner may still be unknown).

    Idempotent: a one-shot `tournament_finalized` flag on game_data guards
    against a second call (e.g. a re-entered boundary, or a later play-out
    completing the field). Returns True if it finalized this call, False if not
    applicable or already done."""
    session = game_data.get('tournament_session')
    if session is None or not (session.is_complete() or session.human_out):
        return False
    if game_data.get('tournament_finalized'):
        return False

    # Read repos/socketio live off `extensions` (not import-copied) to dodge the
    # xdist import-ordering pollution called out in tests/CLAUDE.md.
    from flask_app import extensions
    from flask_app.services import game_state_service

    result = build_completion_result(
        session,
        game_id=game_id,
        biggest_pot=game_data.get('tournament_biggest_pot', 0),
        started_at=game_data.get('tournament_started_at'),
    )

    try:
        owner_id, _owner_name = game_state_service.get_game_owner_info(game_id)
        result['owner_id'] = owner_id
        extensions.tournament_repo.save_tournament_result(game_id, result)
        human_name = result.get('human_player_name')
        if human_name and owner_id:
            extensions.tournament_repo.update_career_stats(owner_id, human_name, result)
        logger.info(
            "[TOURNEY] finalized %s: winner=%s human_pos=%s",
            game_id,
            result['winner_name'],
            result.get('human_finishing_position'),
        )
    except Exception:  # noqa: BLE001 — never let stats writes break completion
        logger.exception("[TOURNEY] failed to persist completion for %s", game_id)

    game_data['tournament_finalized'] = True

    if emit and extensions.socketio is not None:
        from flask_app.handlers.game_handler import send_message

        extensions.socketio.emit(
            'tournament_complete',
            {
                'winner': result['winner_name'],
                'standings': result['standings'],
                'total_hands': result['total_hands'],
                'biggest_pot': result['biggest_pot'],
                'human_position': result.get('human_finishing_position'),
                'game_id': game_id,
                'final_hand_data': final_hand_data,
            },
            to=game_id,
        )
        try:
            send_message(
                game_id, "Table", f"TOURNAMENT OVER! {result['winner_name']} wins!", "system"
            )
        except Exception:  # noqa: BLE001 — cosmetic
            pass

    return True
