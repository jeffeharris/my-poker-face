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
    personality_repo=None,
) -> dict[str, Any]:
    """Build the tournament-result dict (the shape `tournament_repo` and the
    `tournament_complete` event consume) from a completed `TournamentSession`.

    Standings mirror `TournamentTracker.get_standings`: winner first (position
    1), then eliminations ordered by finishing position.

    AI field ids are resolved to the persona's display name through the canonical
    resolver — the same lookup cash uses — so an MTT field (whose ids are
    `personality_id` slugs) renders real names instead of `sun_tzu`, while a
    single-table field (ids already real names) and synthetic `P##` seats pass
    through verbatim. The HUMAN seat is NOT a persona (its identity is the
    owner_id, like cash) so it is left as its `human_id` verbatim — that keeps
    `human_player_name` equal to the human's standings row, which
    `tournament_repo.update_career_stats` cross-references by name (and uses to
    count knockouts via `eliminated_by`); resolving only one side would silently
    break career stats."""
    from tournament.identity import resolve_display_name

    human_id = session.human_id
    winner_id = session.winner()

    def _name(pid: str) -> str:
        # Human seat stays verbatim (not a persona); AI seats resolve to the
        # persona name, falling back VERBATIM (no `.title()` mangling of a
        # single-table real name / a legible `P##` seat) when unresolved.
        if pid == human_id:
            return str(pid)
        return resolve_display_name(pid, personality_repo=personality_repo, humanize_fallback=False)

    standings: list[dict[str, Any]] = []
    if winner_id is not None:
        standings.append(
            {
                'player_name': _name(winner_id),
                'is_human': winner_id == human_id,
                'finishing_position': 1,
                'eliminated_by': None,
                'eliminated_at_hand': None,
            }
        )
    for e in session.field.eliminations:
        standings.append(
            {
                'player_name': _name(e.player_id),
                'is_human': e.player_id == human_id,
                'finishing_position': e.finishing_position,
                # Resolve to match `player_name` — the repo counts knockouts by
                # `eliminated_by == player_name`, so both sides must be the
                # resolved name (an MTT eliminator is a `personality_id` slug).
                'eliminated_by': _name(e.eliminator) if e.eliminator else None,
                'eliminated_at_hand': e.round_index,
            }
        )
    standings.sort(key=lambda s: s['finishing_position'])

    return {
        'game_id': game_id,
        'winner_name': _name(winner_id) if winner_id is not None else None,
        'total_hands': session._hand_counter,
        'biggest_pot': biggest_pot,
        'starting_player_count': session.field.field_size,
        # Verbatim `human_id` — equals the human's standings row (`_name(human_id)`
        # is also verbatim), the invariant `update_career_stats` relies on. None
        # for an autonomous (no-human) tournament (T3-80 F1) — avoids _name(None)
        # writing the literal string "None" as the human's name.
        'human_player_name': _name(human_id) if human_id is not None else None,
        'human_finishing_position': (
            _ordinal_position(session, human_id) if human_id is not None else None
        ),
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
        personality_repo=getattr(extensions, 'personality_repo', None),
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

    # T3-77 — flush real personas' evolved tournament mood back to the cash
    # world. A cash-world (persona) field is two-way, like a cash table; a
    # non-cash / single-table tournament leaves the flag unset and writes
    # nothing (baseline). Seat keys ARE personality_ids (MTT bridge). Guarded so
    # a flush hiccup never blocks completion.
    if game_data.get('tournament_is_persona_field'):
        try:
            from cash_mode.psychology_persistence import flush_persona_psychology

            sandbox_id = game_data.get('tournament_sandbox_id')
            bankroll_repo = getattr(extensions, 'bankroll_repo', None)
            if sandbox_id and bankroll_repo is not None:
                for pid, ctrl in (game_data.get('ai_controllers') or {}).items():
                    flush_persona_psychology(ctrl, pid, bankroll_repo, sandbox_id)
        except Exception:  # noqa: BLE001 — never let a flush break completion
            logger.exception("[TOURNEY] psychology flush failed for %s", game_id)

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
