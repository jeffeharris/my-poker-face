"""Character dossier routes — surfaces existing data for the in-game
CharacterDetailCard ("Dossier 1972") overlay.

Four endpoints:

  GET  /api/character/<identifier>/dossier
       Fans out from the (observer = current user, opponent = identifier)
       pair: personality block, projected relationship axes + hint,
       cash pair PnL, last-5 hand summaries from the active cash
       session (if any), and the player-authored note.

  GET  /api/character/nickname-overrides
       Bulk-loader: returns every nickname override the current viewer
       has set, keyed by personality display name (so the React side
       can look up by `player.name` without a separate resolver). Used
       at app load so opponent labels everywhere (table seats, chat
       targets, heads-up panel, etc.) display the viewer's private
       alias rather than the canonical nickname.

  PUT  /api/character/<identifier>/note      body {note: str}
       Persists the note to relationship_states.notes (schema v95).
       Stored cross-session, cross-game — keyed on the same stable
       (observer_id, opponent_id) the affinity axes use.

  PUT  /api/character/<identifier>/nickname  body {nickname: str}
       Persists a per-viewer nickname override to
       relationship_states.nickname_override (schema v101). Lets the
       player privately rename an opponent for easier recognition;
       empty / whitespace clears the override and reverts to the
       canonical nickname.

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
    *,
    likability: float,
    heat: float,
    respect: float,
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
        return float(v) if isinstance(v, int | float) else None

    return {
        'aggression': get('baseline_aggression'),
        'looseness': get('baseline_looseness'),
        'poise': get('poise'),
        'expressiveness': get('expressiveness'),
        'risk': get('risk_identity'),
    }


def _build_personality_payload(
    personality_id: str,
    *,
    nickname_override: Optional[str] = None,
) -> dict:
    """Return the subset of personality fields the dossier renders.

    `nickname` is the *displayed* alias — when the viewer has set a
    private override it takes precedence over the personality's
    canonical nickname. `canonical_nickname` is always the original
    so the editor UI can show what the override is replacing, and
    `nickname_override` is the raw stored value (None when unset).
    """
    from flask_app.extensions import personality_repo

    try:
        p = personality_repo.load_personality_by_id(personality_id) or {}
    except Exception:
        p = {}

    canonical = p.get('nickname')
    return {
        'name': p.get('name'),
        'nickname': nickname_override or canonical,
        'canonical_nickname': canonical,
        'nickname_override': nickname_override,
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
    except Exception as e:
        # Display-only dossier scan; never fail the request over it, but log
        # so a persistent failure (e.g. corrupt game_state) isn't invisible.
        logger.warning("dossier game_data scan failed for %r: %s", player_name, e)
    return None


def _build_live_emotion(game_data: dict, player_name: str) -> Optional[str]:
    """Read the same emotion the WebSocket emit serializes."""
    controllers = (game_data or {}).get('ai_controllers') or {}
    controller = controllers.get(player_name)
    if controller is None:
        return None
    runout = game_data.get('runout_emotion_overrides') or {}
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
          "name", "nickname", "canonical_nickname", "nickname_override",
          "play_style", "attitude", "confidence", "signature_line",
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

    observer_id = _resolve_observer_id()

    # Pull the viewer's private nickname override first so it can be
    # baked into the personality block — the rendered `nickname`
    # field then reflects what the player chose to call this
    # opponent. Anonymous reads (no observer) skip this entirely and
    # see the canonical nickname only.
    nickname_override: Optional[str] = None
    if observer_id:
        try:
            from flask_app.extensions import relationship_repo

            nickname_override = relationship_repo.load_nickname_override(
                observer_id,
                personality_id,
            )
        except Exception as e:
            logger.debug("[CHARACTER] nickname_override load failed: %s", e)

    personality = _build_personality_payload(
        personality_id,
        nickname_override=nickname_override,
    )

    # Live in-memory game data — needed for emotion / observation /
    # pressure_summary / memorable_hands. Resolved by player name
    # because that's the dossier's identity key on the controller side.
    player_name = (personality or {}).get('name') or identifier
    game_data = _find_game_data_with_player(player_name) or {}

    # AI bankroll (off-table chips, projected through regen). Lives
    # in the bankroll repo keyed on (personality_id, sandbox_id) since
    # the v102 per-sandbox scoping; the dossier is per-viewer so we
    # resolve the observer's default sandbox.
    ai_bankroll_chips: Optional[int] = None
    try:
        from flask_app.extensions import bankroll_repo, sandbox_repo
        from flask_app.services.sandbox_resolver import resolve_default_sandbox_for

        sandbox_id = resolve_default_sandbox_for(observer_id, sandbox_repo=sandbox_repo)
        ai_bankroll_chips = bankroll_repo.load_ai_bankroll_current(
            personality_id,
            sandbox_id=sandbox_id,
        )
    except Exception as e:
        logger.debug("[CHARACTER] ai_bankroll lookup failed: %s", e)

    # Stake summary (Phase 4 dossier enrichment). Two directions:
    #   - `as_borrower`: this AI's outstanding carries as borrower
    #     (Phase 4 AI-as-borrower). Pre-Phase-4 AIs never borrowed,
    #     so the list is empty for older data.
    #   - `as_staker`: humans' (and Phase-4-onward AIs') outstanding
    #     carries TO this AI (Path B onward).
    # Both summaries report counts + total chip amounts so the dossier
    # can render "Owes $X across N carries" / "Owed $Y across M carries"
    # without rendering individual stake rows (the drawer is the
    # detail view).
    stake_summary = {
        'as_borrower': {'carry_count': 0, 'total_carried': 0},
        'as_staker': {'carry_count': 0, 'total_owed_to_them': 0},
    }
    try:
        from flask_app.extensions import stake_repo

        if stake_repo is not None:
            from cash_mode.stakes import BORROWER_KIND_PERSONALITY

            borrower_carries = stake_repo.list_carries_for_borrower(
                personality_id,
                BORROWER_KIND_PERSONALITY,
            )
            stake_summary['as_borrower'] = {
                'carry_count': len(borrower_carries),
                'total_carried': sum(int(s.carry_amount) for s in borrower_carries),
            }
            staker_carries = stake_repo.list_carries_for_staker(personality_id)
            stake_summary['as_staker'] = {
                'carry_count': len(staker_carries),
                'total_owed_to_them': sum(int(s.carry_amount) for s in staker_carries),
            }
    except Exception as e:
        logger.debug("[CHARACTER] stake_summary lookup failed: %s", e)

    response = {
        'personality_id': personality_id,
        'personality': personality,
        'emotion': _build_live_emotion(game_data, player_name),
        'observation': _build_observation(game_data, player_name),
        'pressure_summary': _build_pressure_summary(game_data, player_name),
        'ai_bankroll': ai_bankroll_chips,
        'stake_summary': stake_summary,
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
                likability=rs.likability,
                heat=rs.heat,
                respect=rs.respect,
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


@character_bp.route('/api/character/nickname-overrides', methods=['GET'])
def get_nickname_overrides():
    """GET /api/character/nickname-overrides

    Returns the current viewer's full nickname-override map. Shape:

        {
          "overrides": {
            "Batman": "the tight one",
            "Joker":  "river bluffer"
          }
        }

    Keyed by personality display name so the client can look up
    against `player.name` from socket payloads directly — no need to
    push `personality_id` through every game-state emit. Anonymous
    callers (no session) get an empty map rather than a 401 — the
    rest of the UI still has to function for guests, and an empty
    map collapses cleanly through the display helper.
    """
    response = {'overrides': {}}
    observer_id = _resolve_observer_id()
    if not observer_id:
        return jsonify(response)

    from flask_app.extensions import personality_repo, relationship_repo

    try:
        by_id = relationship_repo.load_all_nickname_overrides(observer_id)
    except Exception as e:
        logger.error("[CHARACTER] bulk override load failed: %s", e)
        return jsonify(response)

    # Resolve each personality_id → display name. Small N (one row
    # per opponent the viewer has explicitly renamed), so a per-row
    # lookup is fine and lets the personality_repo's own caching /
    # times_used bookkeeping do its thing.
    by_name: dict = {}
    for personality_id, override in by_id.items():
        try:
            p = personality_repo.load_personality_by_id(personality_id)
        except Exception:
            p = None
        if p and p.get('name'):
            by_name[p['name']] = override
        # Orphan override (personality deleted): silently drop. The
        # row stays in the DB so if the personality is restored the
        # alias comes back, but we don't expose the dangling alias
        # to the client.

    response['overrides'] = by_name
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
            observer_id,
            personality_id,
            e,
        )
        return jsonify({'error': 'Failed to save note'}), 500

    saved = relationship_repo.load_note(observer_id, personality_id)
    return jsonify({'note': saved})


# Nicknames are displayed prominently and are mostly short cues —
# 60 chars covers "the tight guy in the red shirt" with room to
# spare and keeps the dossier layout from being abused as a second
# notes field.
NICKNAME_OVERRIDE_MAX_LEN = 60


@character_bp.route('/api/character/<identifier>/nickname', methods=['PUT'])
def put_nickname_override(identifier: str):
    """PUT /api/character/<identifier>/nickname  body: {"nickname": str}

    Persists a per-viewer nickname override to
    relationship_states.nickname_override. Empty / blank input
    clears the override (stored as NULL) so the dossier reverts to
    the personality's canonical nickname. Returns 401 if no observer
    (per-viewer overrides require a session); 404 if the personality
    doesn't exist.
    """
    observer_id = _resolve_observer_id()
    if not observer_id:
        return jsonify({'error': 'Authentication required'}), 401

    personality_id = _resolve_personality_id(identifier)
    if not personality_id:
        return jsonify({'error': 'Personality not found'}), 404

    payload = request.get_json(silent=True) or {}
    nickname = payload.get('nickname')
    if nickname is not None and not isinstance(nickname, str):
        return jsonify({'error': 'nickname must be a string'}), 400
    if isinstance(nickname, str) and len(nickname) > NICKNAME_OVERRIDE_MAX_LEN:
        return jsonify(
            {
                'error': (f'nickname exceeds {NICKNAME_OVERRIDE_MAX_LEN} character limit'),
            }
        ), 400

    from flask_app.extensions import relationship_repo

    try:
        relationship_repo.save_nickname_override(
            observer_id,
            personality_id,
            nickname,
        )
    except Exception as e:
        logger.error(
            "[CHARACTER] save_nickname_override failed observer=%r personality=%r: %s",
            observer_id,
            personality_id,
            e,
        )
        return jsonify({'error': 'Failed to save nickname'}), 500

    saved = relationship_repo.load_nickname_override(observer_id, personality_id)
    return jsonify({'nickname_override': saved})
