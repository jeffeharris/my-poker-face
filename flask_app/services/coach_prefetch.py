"""Server-side prefetch of the proactive coach tip.

Fired the instant a human's turn begins (`handle_human_turn`), so the coach LLM
call starts as early as possible — overlapping the player's read-the-board /
thinking time instead of only starting after the client round-trips
(see turn → GET stats → POST ask). The `/api/coach/<id>/ask` proactive path
serves the cached result, waiting on the in-flight call via an Event, so there
is **exactly one** coach LLM call per decision (no double-charge).

Why deterministic bots make this clean: rule/tiered opponents resolve the whole
orbit in <100ms, so the moment it's the human's turn the decision state is known
and stable — the perfect point to kick the coach call off.

Cache lives on `game_data['coach_tip_cache']` keyed by a decision signature, so
a stale tip is never served for a different decision. Only runs when coach mode
is 'proactive' (training games are stamped proactive at creation) — it never
adds cost in reactive/off mode. The whole module is best-effort: any failure is
swallowed and the on-demand `/ask` path still works.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

CACHE_KEY = 'coach_tip_cache'
# How long /ask will wait on an in-flight prefetch before giving up and
# returning None (caller then computes on-demand). Comfortably above a typical
# Assistant-tier latency; the coach client has its own hard timeout underneath.
WAIT_TIMEOUT_S = 8.0


def decision_signature(game_data: dict) -> tuple:
    """A stable key for the human's current decision.

    Two reads of the same pending decision produce the same signature; any
    change (new street, a bet faced, next hand) produces a different one, which
    invalidates a stale cached tip.
    """
    sm = game_data.get('state_machine')
    gs = sm.game_state
    cp = gs.current_player
    mm = game_data.get('memory_manager')
    hand = getattr(mm, 'hand_count', 0) if mm else 0
    pot_total = (gs.pot or {}).get('total', 0) if isinstance(gs.pot, dict) else 0
    return (
        hand,
        str(getattr(sm, 'current_phase', '')),
        gs.current_player_idx,
        cp.bet,
        gs.highest_bet,
        pot_total,
        len(gs.community_cards),
    )


def _build_proactive_payload(game_id: str, game_data: dict) -> dict | None:
    """Produce the same payload shape `coach_ask` returns for a proactive tip."""
    from flask_app import extensions
    from flask_app.routes.coach_routes import _get_human_player_name
    from flask_app.services.coach_assistant import get_or_create_coach_with_mode
    from flask_app.services.coach_engine import compute_coaching_data_with_progression

    player_name = _get_human_player_name(game_data)
    if not player_name:
        return None
    owner_id = game_data.get('owner_id')
    stats = compute_coaching_data_with_progression(
        game_id,
        player_name,
        user_id=owner_id,
        game_data=game_data,
        coach_repo=extensions.coach_repo,
    )
    progression = (stats or {}).get('progression', {})
    coach = get_or_create_coach_with_mode(
        game_data,
        game_id,
        player_name=player_name,
        mode=progression.get('coaching_mode', ''),
        skill_context=progression.get('coaching_prompt', ''),
    )
    result = coach.get_proactive_tip(stats or {})
    coach_action = result.get('action')
    coach_raise_to = result.get('raise_to')
    from flask_app.services.coach_assistant import apply_coach_highlight
    apply_coach_highlight(stats, coach_action, coach_raise_to)
    return {
        'answer': result.get('advice', ''),
        'coach_action': coach_action,
        'coach_raise_to': coach_raise_to,
        'stats': stats,
    }


def prefetch_proactive_tip(game_id: str) -> None:
    """Background task: compute + cache the proactive tip for the current turn.

    No-op (no LLM cost) unless coach mode is 'proactive'. Best-effort — never
    raises into the caller / hand flow.
    """
    from flask_app import extensions
    from flask_app.services import game_state_service

    try:
        game_data = game_state_service.get_game(game_id)
        if not game_data:
            return
        try:
            mode = extensions.game_repo.load_coach_mode(game_id)
        except Exception:
            mode = 'off'
        if mode != 'proactive':
            return

        sig = decision_signature(game_data)
        existing = game_data.get(CACHE_KEY)
        if existing and existing.get('sig') == sig:
            return  # already cached or in-flight for this exact decision

        event = threading.Event()
        game_data[CACHE_KEY] = {'sig': sig, 'event': event, 'payload': None}
        try:
            game_data[CACHE_KEY]['payload'] = _build_proactive_payload(game_id, game_data)
        except Exception as e:
            logger.debug('[COACH_PREFETCH] build failed for %s: %s', game_id, e)
        finally:
            event.set()
    except Exception as e:
        logger.debug('[COACH_PREFETCH] failed for %s: %s', game_id, e)


def take_cached_tip(game_data: dict, timeout: float = WAIT_TIMEOUT_S) -> dict | None:
    """Return the prefetched payload for the *current* decision, or None.

    Waits up to `timeout` for an in-flight prefetch. Returns None when there's no
    prefetch for this decision (signature mismatch / disabled / errored) — the
    caller then computes on-demand.
    """
    entry = game_data.get(CACHE_KEY)
    if not entry:
        return None
    try:
        if entry.get('sig') != decision_signature(game_data):
            return None
    except Exception:
        return None
    ev = entry.get('event')
    if ev is not None and not ev.is_set():
        ev.wait(timeout)
    return entry.get('payload')
