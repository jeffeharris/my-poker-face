"""Character dossier routes — surfaces existing data for the in-game
CharacterDetailCard ("Dossier 1972") overlay.

Two endpoints:

  GET  /api/character/<identifier>/dossier
       Fans out from the (observer = current user, opponent = identifier)
       pair: personality block, projected relationship axes + hint,
       cash pair PnL, last-5 hand summaries from the active cash
       session (if any), and the player-authored note.

  PUT  /api/character/<identifier>/note      body {note: str}
       Persists the note to relationship_states.notes (schema v95).
       Stored cross-session, cross-game — keyed on the same stable
       (observer_id, opponent_id) the affinity axes use.

`<identifier>` resolves as personality_id first, then falls back to a
name lookup, so the React side can pass either without a separate
resolution call. Tournament-only opponents that aren't in
`personalities` resolve to `None` and the route returns 404.
"""

from __future__ import annotations

import logging
from typing import Optional

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

character_bp = Blueprint('character', __name__)


def _resolve_observer_id() -> Optional[str]:
    """Current user's stable id, or None if no session.

    Same path cash_routes uses (`auth_manager.get_current_user()['id']`).
    Returning None lets the route distinguish "no auth, dossier is read-
    only" from "auth present, full surface available" — the dossier is
    still useful unauthenticated for the personality block alone, but
    the relationship / pair-stats / notes axes all need an observer.
    """
    from flask_app.extensions import auth_manager
    user = auth_manager.get_current_user() if auth_manager else None
    return user.get("id") if user and user.get("id") else None


def _resolve_personality_id(identifier: str) -> Optional[str]:
    """Try direct id match, fall back to name resolution.

    Frontend can pass either the stable personality_id (lobby seats
    carry it) or the display name (table-side `player.name` is all the
    React Player blob exposes today). Returning None means neither hit.
    """
    from flask_app.extensions import personality_repo
    try:
        if personality_repo.load_personality_by_id(identifier):
            return identifier
    except Exception:
        pass
    try:
        return personality_repo.resolve_name_to_personality_id(identifier)
    except Exception:
        return None


def _find_active_cash_game_id_for_owner(owner_id: str) -> Optional[str]:
    """Reuse the cash_routes helper to find an in-progress cash session.

    Recent-hands surfacing scopes to the current session — across
    sessions the per-game `hand_history` rows would mix unrelated
    streaks. If no active cash game, recent_hands is just empty.
    """
    try:
        from flask_app.routes.cash_routes import _find_active_cash_game_id
        return _find_active_cash_game_id(owner_id)
    except Exception:
        return None


def _relationship_hint(
    *, likability: float, heat: float, respect: float,
) -> str:
    """Mirrors `cash_mode.sponsor_offers._relationship_hint`.

    Duplicated rather than imported to keep this route free of the
    sponsor-offers dependency tree (which pulls in stakes, lender
    profiles, etc.). The lobby surface uses the original; the dossier
    uses this clone.
    """
    if heat > 0.4:
        return "wants their money back"
    if heat > 0.2:
        return "watching you"
    if respect > 0.6 and likability > 0.5:
        return "trusts you"
    if respect > 0.5:
        return "respects your game"
    if likability > 0.5:
        return "friendly"
    return ""


def _curated_anchors(personality: dict) -> Optional[dict]:
    """Return the player-facing subset of psychology anchors, or None.

    The full anchor block has 9 axes (psychology_model.PersonalityAnchors).
    We surface only the five that meaningfully shape what the player
    sees across the table — the rest (ego, adaptation_bias, recovery_rate,
    baseline_energy) are inside-baseball plumbing for tilt dynamics
    and session drift and don't add player-actionable signal.

    Curated set:
      - aggression     ← baseline_aggression   (bet/raise vs check/call frequency)
      - looseness      ← baseline_looseness    (starting hand range width)
      - poise          ← poise                 (composure under pressure / tilt resistance)
      - expressiveness ← expressiveness        (how readable they are at the table)
      - risk           ← risk_identity         (variance tolerance / dramatic plays)

    Returns None when the personality has no anchors block (a small
    set of user-created personalities from the legacy admin tool).
    """
    anchors = personality.get('anchors') or {}
    if not anchors:
        return None

    def get(key: str) -> Optional[float]:
        v = anchors.get(key)
        return float(v) if isinstance(v, (int, float)) else None

    return {
        'aggression':     get('baseline_aggression'),
        'looseness':      get('baseline_looseness'),
        'poise':          get('poise'),
        'expressiveness': get('expressiveness'),
        'risk':           get('risk_identity'),
    }


def _build_personality_payload(personality_id: str) -> dict:
    """Return the subset of personality fields the dossier renders."""
    from flask_app.extensions import personality_repo
    try:
        p = personality_repo.load_personality_by_id(personality_id) or {}
    except Exception:
        p = {}

    return {
        'name': p.get('name'),
        'nickname': p.get('nickname'),
        'play_style': p.get('play_style'),
        'attitude': p.get('attitude') or p.get('default_attitude'),
        'confidence': p.get('confidence') or p.get('default_confidence'),
        'signature_line': p.get('signature_line'),
        'anchors': _curated_anchors(p),
    }


def _find_game_data_with_player(player_name: str) -> Optional[dict]:
    """Locate any in-memory game_data whose roster includes `player_name`.

    The live observation / emotion / pressure_summary fields live on
    transient controllers and the memory_manager — neither survives
    a backend restart and neither is persisted in a way that's safe
    to query out-of-band. So we look them up on whatever game is
    currently in memory containing this player.
    """
    try:
        from flask_app.services import game_state_service
        for _gid, gdata in game_state_service.games.items():
            sm = gdata.get('state_machine')
            if sm is None:
                continue
            try:
                roster = sm.game_state.players
            except Exception:
                continue
            if any(p.name == player_name for p in roster):
                return gdata
    except Exception:
        pass
    return None


def _build_live_emotion(game_data: dict, player_name: str) -> Optional[str]:
    """Read the same emotion the WebSocket emit serializes."""
    controllers = (game_data or {}).get('ai_controllers') or {}
    controller = controllers.get(player_name)
    if controller is None:
        return None
    runout = (game_data.get('runout_emotion_overrides') or {})
    if player_name in runout:
        return runout[player_name]
    psych = getattr(controller, 'psychology', None)
    if psych is not None:
        try:
            return psych.get_display_emotion()
        except Exception:
            return None
    return 'confident'  # Default for RuleBots, matches socket emit's fallback.


def _build_observation(game_data: dict, player_name: str) -> Optional[dict]:
    """Tendencies (VPIP/PFR/AF) seen for `player_name` by any observer.

    Prefers the human observer's view (if any), falls back to whichever
    observer has the most hands recorded. Mirrors the selection logic in
    `update_and_emit_game_state` so the dossier and the socket payload
    agree on what's surfaced.
    """
    mgr = (game_data or {}).get('memory_manager')
    if mgr is None:
        return None
    om = getattr(mgr, 'opponent_model_manager', None)
    if om is None or not getattr(om, 'models', None):
        return None
    candidate = None
    # Try every observer; prefer one with the most hands recorded.
    for observer_name, models in om.models.items():
        model = models.get(player_name)
        if model is None:
            continue
        hands = getattr(model.tendencies, 'hands_observed', 0)
        if hands <= 0:
            continue
        if candidate is None or hands > getattr(candidate.tendencies, 'hands_observed', 0):
            candidate = model
    if candidate is None:
        return None
    t = candidate.tendencies
    return {
        'hands_observed': t.hands_observed,
        'vpip': round(t.vpip, 2),
        'pfr': round(t.pfr, 2),
        'aggression_factor': round(t.aggression_factor, 2),
        'play_style': t.get_play_style_label(),
    }


def _build_pressure_summary(game_data: dict, player_name: str) -> Optional[dict]:
    """Pull pressure_stats.get_summary() for this player, if available."""
    pstats = (game_data or {}).get('pressure_stats')
    if pstats is None:
        return None
    player_pressure = getattr(pstats, 'player_stats', {}).get(player_name)
    if player_pressure is None:
        return None
    try:
        return player_pressure.get_summary()
    except Exception:
        return None


def _build_memorable_hands(game_data: dict, player_name: str) -> list:
    """Top-impact memorable hands the human observer has against `player_name`.

    Returns a list of dicts (narrative, hand_summary, impact, event,
    hand_id). Empty list when there's no game in memory, no model yet,
    or no hands have crossed `MEMORABLE_HAND_THRESHOLD` (0.7).
    """
    mgr = (game_data or {}).get('memory_manager')
    if mgr is None:
        return []
    om = getattr(mgr, 'opponent_model_manager', None)
    if om is None or not getattr(om, 'models', None):
        return []
    # Human observer: scan models for the seat marked human in this game.
    human_name = None
    sm = game_data.get('state_machine')
    if sm is not None:
        try:
            for p in sm.game_state.players:
                if p.is_human:
                    human_name = p.name
                    break
        except Exception:
            human_name = None

    # Pull the human's view first; if absent, accept any observer with
    # memorable hands recorded. Either way callers get the same shape.
    model = None
    if human_name:
        model = om.models.get(human_name, {}).get(player_name)
    if model is None or not getattr(model, 'memorable_hands', None):
        for observer_models in om.models.values():
            candidate = observer_models.get(player_name)
            if candidate and getattr(candidate, 'memorable_hands', None):
                model = candidate
                break
    if model is None or not model.memorable_hands:
        return []

    # MemorableHand list is already sorted by impact desc and capped at 5.
    return [
        {
            'hand_id': h.hand_id,
            'event': h.event.value if hasattr(h.event, 'value') else str(h.event),
            'impact_score': round(h.impact_score, 2),
            'narrative': h.narrative,
            'hand_summary': h.hand_summary,
            'timestamp': h.timestamp.isoformat() if h.timestamp else None,
        }
        for h in model.memorable_hands
    ]


@character_bp.route('/api/character/<identifier>/dossier', methods=['GET'])
def get_dossier(identifier: str):
    """GET /api/character/<identifier>/dossier

    Fans out (all top-level keys always present; nested values are
    null/empty when the underlying data isn't available):

      {
        "personality_id": "batman",
        "personality": {
          "name", "nickname", "play_style", "attitude", "confidence",
          "signature_line",
          "anchors": {aggression, looseness, poise,
                      expressiveness, risk} | null
        } | null,
        "emotion": "focused" | null,
        "observation": {
          "hands_observed": 87, "vpip": 0.21, "pfr": 0.18,
          "aggression_factor": 3.4, "play_style": "tight-aggressive"
        } | null,
        "pressure_summary": {...} | null,
        "ai_bankroll": 4250 | null,
        "relationship": {
          "heat": 0.31, "respect": 0.62, "likability": 0.48,
          "last_seen": "2026-05-18T22:14:01",
          "hint": "watching you"
        } | null,
        "cash_pair_stats": {
          "cumulative_pnl": -2400, "hands_played_cash": 87
        } | null,
        "memorable_hands": [
          {hand_id, event, impact_score, narrative,
           hand_summary, timestamp}, ...
        ],
        "note": "calls light on the turn" | null
      }
    """
    from flask_app.extensions import (
        bankroll_repo as _bankroll_repo,  # noqa: F401  # ensures init
        relationship_repo,
    )

    personality_id = _resolve_personality_id(identifier)
    if not personality_id:
        return jsonify({'error': 'Personality not found'}), 404

    personality = _build_personality_payload(personality_id)
    observer_id = _resolve_observer_id()

    # Live in-memory game data — needed for emotion / observation /
    # pressure_summary / memorable_hands. Resolved by player name
    # because that's the dossier's identity key on the controller side.
    player_name = (personality or {}).get('name') or identifier
    game_data = _find_game_data_with_player(player_name) or {}

    # AI bankroll (off-table chips, projected through regen). Lives
    # in the bankroll repo keyed on personality_id; cheap independent
    # read regardless of whether the AI is currently seated.
    ai_bankroll_chips: Optional[int] = None
    try:
        from flask_app.extensions import bankroll_repo
        ai_bankroll_chips = bankroll_repo.load_ai_bankroll_current(personality_id)
    except Exception as e:
        logger.debug("[CHARACTER] ai_bankroll lookup failed: %s", e)

    response = {
        'personality_id': personality_id,
        'personality': personality,
        'emotion': _build_live_emotion(game_data, player_name),
        'observation': _build_observation(game_data, player_name),
        'pressure_summary': _build_pressure_summary(game_data, player_name),
        'ai_bankroll': ai_bankroll_chips,
        'relationship': None,
        'cash_pair_stats': None,
        'memorable_hands': _build_memorable_hands(game_data, player_name),
        'note': None,
    }

    if not observer_id:
        # Anonymous read: relationship-derived sections drop, but
        # everything sourced from the in-memory game still applies.
        return jsonify(response)

    # Relationship axes (projected through decay).
    try:
        rs = relationship_repo.load_relationship_state(observer_id, personality_id)
    except Exception as e:
        logger.debug("[CHARACTER] relationship load failed: %s", e)
        rs = None
    if rs is not None:
        response['relationship'] = {
            'heat': rs.heat,
            'respect': rs.respect,
            'likability': rs.likability,
            'last_seen': rs.last_seen.isoformat() if rs.last_seen else None,
            'hint': _relationship_hint(
                likability=rs.likability, heat=rs.heat, respect=rs.respect,
            ),
        }

    # Cash pair stats (lifetime cash-mode PnL with this personality).
    try:
        cps = relationship_repo.load_cash_pair_stats(observer_id, personality_id)
    except Exception as e:
        logger.debug("[CHARACTER] cash_pair_stats load failed: %s", e)
        cps = None
    if cps is not None:
        response['cash_pair_stats'] = {
            'cumulative_pnl': cps.cumulative_pnl,
            'hands_played_cash': cps.hands_played_cash,
        }

    # Player-authored note (v95). None when no row OR row has NULL note.
    try:
        response['note'] = relationship_repo.load_note(observer_id, personality_id)
    except Exception as e:
        logger.debug("[CHARACTER] note load failed: %s", e)

    return jsonify(response)


@character_bp.route('/api/character/<identifier>/note', methods=['PUT'])
def put_note(identifier: str):
    """PUT /api/character/<identifier>/note  body: {"note": str}

    Persists the note to relationship_states.notes. An empty / blank
    note is stored as NULL so "has a note" stays a meaningful
    predicate. Returns 401 if no observer (notes are player-authored
    so a session is required); 404 if the personality doesn't exist.
    """
    observer_id = _resolve_observer_id()
    if not observer_id:
        return jsonify({'error': 'Authentication required'}), 401

    personality_id = _resolve_personality_id(identifier)
    if not personality_id:
        return jsonify({'error': 'Personality not found'}), 404

    payload = request.get_json(silent=True) or {}
    note = payload.get('note')
    if note is not None and not isinstance(note, str):
        return jsonify({'error': 'note must be a string'}), 400
    if isinstance(note, str) and len(note) > 2000:
        # Soft cap — keeps the textarea from being abused as cold
        # storage. 2000 chars is ~400 words; plenty for player notes.
        return jsonify({'error': 'note exceeds 2000 character limit'}), 400

    from flask_app.extensions import relationship_repo
    try:
        relationship_repo.save_note(observer_id, personality_id, note)
    except Exception as e:
        logger.error(
            "[CHARACTER] save_note failed observer=%r personality=%r: %s",
            observer_id, personality_id, e,
        )
        return jsonify({'error': 'Failed to save note'}), 500

    saved = relationship_repo.load_note(observer_id, personality_id)
    return jsonify({'note': saved})
