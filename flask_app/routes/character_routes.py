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


def _observation_from_lifetime(counts: Optional[dict]) -> Optional[dict]:
    """Shape the durable lifetime observation counts into the dossier's
    `observation` block, deriving rates through the canonical
    `OpponentTendencies` formula so VPIP/PFR/AF/play-style match the live
    path exactly (no duplicated thresholds, no drift).

    Returns None when there's no lifetime row or no hands yet.
    """
    if not counts or not counts.get('hands_observed'):
        return None

    from poker.memory.opponent_model import OpponentTendencies

    t = OpponentTendencies()
    t.hands_dealt = counts['hands_dealt']
    t.hands_observed = counts['hands_observed']
    t._vpip_count = counts['vpip_count']
    t._pfr_count = counts['pfr_count']
    t._bet_raise_count = counts['bet_raise_count']
    t._call_count = counts['call_count']
    t._showdowns = counts['showdowns_seen']
    t._showdowns_won = counts['showdowns_won']
    t._recalculate_stats()

    return {
        'hands_observed': t.hands_observed,
        'vpip': round(t.vpip, 2),
        'pfr': round(t.pfr, 2),
        'aggression_factor': round(t.aggression_factor, 2),
        'play_style': t.get_play_style_label(),
        # Marks this as the cross-game scouting read (vs. a live in-game one)
        # so the dossier can label it "lifetime".
        'lifetime': True,
    }


def _tendencies_from_lifetime(counts: Optional[dict]):
    """Reconstruct a full `OpponentTendencies` from the durable lifetime COUNTS.

    Thin back-compat shim — the implementation moved to
    `flask_app.services.opponent_reads.reconstruct_tendencies_from_lifetime`
    so the coach (a service) can share it without importing this route module.
    Kept under the original name for the dossier callers and tests that import
    it from here.
    """
    from flask_app.services.opponent_reads import (
        reconstruct_tendencies_from_lifetime,
    )

    return reconstruct_tendencies_from_lifetime(counts)


def _deeper_reads_from_lifetime(counts: Optional[dict]) -> Optional[dict]:
    """Shape the durable lifetime COUNTS into the dossier's `deeper_reads`
    block — the Tier-2 reads (fold-to-cbet, c-bet %, barreling, all-in
    frequency, postflop aggression, polarization, limp rate, showdown win
    rate). Each rate is `None` until its own spot is observed (rather than
    the model's neutral prior) so the gated UI shows "—".

    Delegates the shaping to the shared `deep_reads_from_tendencies` (also used
    by the coach) so the read definitions never drift. Returns None when there's
    no lifetime row or no hands yet.
    """
    from flask_app.services.opponent_reads import deep_reads_from_tendencies

    reads = deep_reads_from_tendencies(_tendencies_from_lifetime(counts))
    if reads is None:
        return None
    reads['lifetime'] = True
    return reads


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


def _build_lifetime_pressure_summary(
    owner_id: Optional[str], player_name: Optional[str]
) -> Optional[dict]:
    """Durable cross-game pressure summary for `player_name`, replaying every
    pressure event across the owner's games through the canonical
    `PlayerPressureStats` aggregation (same get_summary() the live path uses,
    so signature move / biggest pots / HU record / bluff tallies are derived
    identically — just over a lifetime instead of one game).

    Owner-scoped (≈ sandbox under v1's 1:1 ownership). Returns None when there
    are no events. Reusing the live aggregator means a fresh re-aggregation
    each read — it can't double-count.
    """
    if not owner_id or not player_name:
        return None
    try:
        from datetime import datetime

        from flask_app.extensions import persistence_db_path
        from poker.pressure_stats import PlayerPressureStats, PressureEvent
        from poker.repositories.sqlite_repositories import PressureEventRepository

        if not persistence_db_path:
            return None
        repo = PressureEventRepository(persistence_db_path)
        events = repo.get_player_events_for_owner(player_name, owner_id)
        if not events:
            return None

        stats = PlayerPressureStats(player_name)
        for e in events:
            raw_ts = e.get('timestamp')
            if isinstance(raw_ts, datetime):
                ts = raw_ts
            else:
                try:
                    ts = datetime.fromisoformat(str(raw_ts))
                except (TypeError, ValueError):
                    ts = datetime.now()
            stats.add_event(
                PressureEvent(
                    timestamp=ts,
                    event_type=e['event_type'],
                    player_name=player_name,
                    details=e.get('details') or {},
                )
            )
        return stats.get_summary()
    except Exception as exc:
        logger.debug("[CHARACTER] lifetime pressure load failed: %s", exc)
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
    # Resolved once and reused for both the AI bankroll lookup and the
    # per-sandbox cash_pair_stats read below. Stays None if resolution
    # fails — load_cash_pair_stats(sandbox_id=None) then falls back to the
    # cross-sandbox sum (identical under v1's 1:1 ownership).
    sandbox_id: Optional[str] = None
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
        'reputation': None,
    }

    # AI renown (Renown-v2, field-relative). Sandbox-scoped and
    # viewer-agnostic — this AI's own standing in the sandbox's field, not a
    # per-observer relationship axis — so it's populated before the anonymous
    # early-return. Present only when the per-AI persist path has run
    # (RENOWN_V2_PERSIST_AI on + the migration applied + the ticker has scored
    # the field at least once); null otherwise, so the badge simply doesn't
    # render. Read-only; never blocks the dossier.
    if sandbox_id:
        try:
            from flask_app.extensions import prestige_snapshots_repo

            snap = prestige_snapshots_repo.load_latest(sandbox_id, personality_id, entity_kind='ai')
            if snap and snap.get('formula_version') == 'v2' and snap.get('renown_v2') is not None:
                response['reputation'] = {
                    'formula_version': 'v2',
                    'quadrant': snap['quadrant'],
                    'renown_v2': snap['renown_v2'],
                    'victim_percentile': snap.get('victim_percentile'),
                    'high_cut': snap.get('high_cut'),
                    'field_size': snap.get('field_size'),
                }
        except Exception as e:
            logger.debug("[CHARACTER] reputation load failed: %s", e)

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

    # Cash pair stats (per-sandbox cash-mode PnL with this personality).
    # Scoped to the observer's active sandbox per the dossier per-sandbox
    # principle; falls back to the cross-sandbox sum when sandbox_id is None.
    try:
        cps = relationship_repo.load_cash_pair_stats(
            observer_id, personality_id, sandbox_id=sandbox_id
        )
    except Exception as e:
        logger.debug("[CHARACTER] cash_pair_stats load failed: %s", e)
        cps = None
    if cps is not None:
        response['cash_pair_stats'] = {
            'cumulative_pnl': cps.cumulative_pnl,
            'hands_played_cash': cps.hands_played_cash,
        }

    # Durable cross-game observation (Phase 1 scouting memory). Prefer it
    # over the live in-game read when a lifetime row exists: it accumulates
    # across every game in this sandbox (folded each hand), so it's the more
    # complete read, and it survives game-end — the whole point of the
    # dossier becoming persistent. Falls back to the live `observation`
    # (already set above) when there's no lifetime row yet. `life_counts` is
    # also the source of the scouting gate's observed-hand count below.
    life_counts = None
    response['deeper_reads'] = None
    response['the_read'] = []
    response['archetype'] = None
    if sandbox_id:
        try:
            from flask_app.extensions import game_repo

            life_counts = game_repo.load_observation_lifetime(
                sandbox_id, observer_id, personality_id
            )
            life_obs = _observation_from_lifetime(life_counts)
            if life_obs is not None:
                response['observation'] = life_obs
            # Tier-2 deep postflop reads (gated past 180 hands below).
            deeper = _deeper_reads_from_lifetime(life_counts)
            if deeper is not None:
                response['deeper_reads'] = deeper
            # B2 "the read": exploit advice + archetype badge, from the
            # tiered-bot exploitation detectors over the same tendencies.
            tendencies = _tendencies_from_lifetime(life_counts)
            if tendencies is not None:
                from flask_app.services.dossier_read import build_the_read

                read = build_the_read(tendencies)
                response['the_read'] = read['tips']
                response['archetype'] = read['archetype']
        except Exception as e:
            logger.debug("[CHARACTER] lifetime observation load failed: %s", e)

    # Durable cross-game pressure + memorable hands. Like observation, these
    # were live-only (lost between games); prefer the lifetime version so a
    # signature move / biggest pots / HU record / memorable hands survive
    # game-end. Owner-scoped (≈ sandbox under 1:1 ownership). Falls back to
    # the live builders (already set above) when there's no durable history.
    try:
        lifetime_pressure = _build_lifetime_pressure_summary(observer_id, player_name)
        if lifetime_pressure is not None:
            response['pressure_summary'] = lifetime_pressure
    except Exception as e:
        logger.debug("[CHARACTER] lifetime pressure merge failed: %s", e)
    try:
        from flask_app.extensions import game_repo

        lifetime_memorable = game_repo.load_lifetime_memorable_hands(observer_id, player_name)
        if lifetime_memorable:
            response['memorable_hands'] = lifetime_memorable
    except Exception as e:
        logger.debug("[CHARACTER] lifetime memorable merge failed: %s", e)

    # The history (rivalry read): aggregate the logged relationship events
    # between the human and this opponent into a headline + defining clash +
    # clash/banter tallies. Owner-scoped, from the same memorable_hands store.
    response['relationship_history'] = None
    try:
        from flask_app.extensions import game_repo
        from flask_app.services.dossier_history import (
            CLASH_EVENTS,
            build_relationship_history,
        )

        hist = game_repo.load_relationship_history(observer_id, player_name, CLASH_EVENTS)
        response['relationship_history'] = build_relationship_history(hist)
    except Exception as e:
        logger.debug("[CHARACTER] relationship history failed: %s", e)

    # Player-authored note (v95). None when no row OR row has NULL note.
    try:
        response['note'] = relationship_repo.load_note(observer_id, personality_id)
    except Exception as e:
        logger.debug("[CHARACTER] note load failed: %s", e)

    # The viewer's own bankroll — lets the informant UI know what they can
    # afford up front (disable unaffordable unlocks instead of failing the
    # click with a 402). None when no bankroll row yet.
    response['player_bankroll'] = None
    try:
        from flask_app.extensions import bankroll_repo

        player_bankroll = bankroll_repo.load_player_bankroll(observer_id)
        if player_bankroll is not None:
            response['player_bankroll'] = player_bankroll.chips
    except Exception as e:
        logger.debug("[CHARACTER] player_bankroll load failed: %s", e)

    # B3 emotional read + B4 field standing — pure derivations over numbers
    # already loaded (pressure tilt, psychology anchors, observation vpip/af).
    # Computed before the gate so it can redact them by their own tiers.
    try:
        from flask_app.services.dossier_signals import (
            build_temperament,
            field_position,
        )

        anchors_block = (response.get('personality') or {}).get('anchors')
        response['temperament'] = build_temperament(response.get('pressure_summary'), anchors_block)
        obs_block = response.get('observation') or {}
        response['field_position'] = field_position(
            obs_block.get('vpip'), obs_block.get('aggression_factor')
        )
    except Exception as e:
        logger.debug("[CHARACTER] temperament/field signals failed: %s", e)
        response['temperament'] = None
        response['field_position'] = None

    # Scouting gate (Phase 2 — the grind). Circuit-only: applies when a
    # sandbox is in play, gating the earnable reads behind hands observed
    # against this opponent. Outside the Circuit (no sandbox) the dossier is
    # ungated, as before. Behind a kill switch. Strips locked values + adds
    # the `scouting` descriptor the client renders the locked file from.
    if sandbox_id:
        try:
            from cash_mode import economy_flags

            if economy_flags.DOSSIER_SCOUTING_GATE_ENABLED:
                from flask_app.extensions import game_repo
                from flask_app.services.dossier_scouting import apply_scouting_gate

                purchased = game_repo.load_informant_unlocks(
                    sandbox_id, observer_id, personality_id
                )
                # Pass the full lifetime counts (not just hands) so the Tier-2
                # opportunity gates can read their sample denominators.
                apply_scouting_gate(response, life_counts or {}, purchased)
        except Exception as e:
            logger.debug("[CHARACTER] scouting gate failed: %s", e)

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


@character_bp.route('/api/character/<identifier>/informant', methods=['POST'])
def post_informant_unlock(identifier: str):
    """POST /api/character/<identifier>/informant  body: {"section_id": str}

    Pay the informant to reveal a still-locked dossier section (Phase 3 —
    the chip sink). Debits the player's bankroll into the recyclable bank
    pool and records the purchase so the section stays unlocked. The
    informant bypasses the grind floor — you can buy intel on someone you've
    barely played.

    Returns 401 (no observer), 404 (unknown personality), 400 (unknown
    section / scouting disabled), 409 (section already unlocked), 402
    (insufficient bankroll), or 200 with the updated scouting + bankroll.
    """
    observer_id = _resolve_observer_id()
    if not observer_id:
        return jsonify({'error': 'Authentication required'}), 401

    personality_id = _resolve_personality_id(identifier)
    if not personality_id:
        return jsonify({'error': 'Personality not found'}), 404

    from cash_mode import economy_flags
    from flask_app.services.dossier_scouting import INFORMANT_SECTIONS, compute_scouting

    if not economy_flags.DOSSIER_SCOUTING_GATE_ENABLED:
        return jsonify({'error': 'Scouting is disabled'}), 400

    payload = request.get_json(silent=True) or {}
    section_id = payload.get('section_id')
    section = INFORMANT_SECTIONS.get(section_id)
    if not section:
        return jsonify({'error': 'Unknown section'}), 400

    from flask_app.extensions import (
        bankroll_repo,
        chip_ledger_repo,
        game_repo,
        sandbox_repo,
    )
    from flask_app.services.sandbox_resolver import resolve_default_sandbox_for

    sandbox_id = resolve_default_sandbox_for(observer_id, sandbox_repo=sandbox_repo)

    # Only sections with still-locked items are buyable (a payment always
    # makes progress — never "you paid for what you already had").
    life = game_repo.load_observation_lifetime(sandbox_id, observer_id, personality_id)
    purchased = game_repo.load_informant_unlocks(sandbox_id, observer_id, personality_id)
    offers = {o['id'] for o in compute_scouting(life or {}, purchased)['informant_offers']}
    if section_id not in offers:
        return jsonify({'error': 'Section already unlocked'}), 409

    price = int(section['price'])

    bankroll = bankroll_repo.load_player_bankroll(observer_id)
    if bankroll is None or bankroll.chips < price:
        return jsonify(
            {
                'error': 'Insufficient bankroll',
                'price': price,
                'bankroll': bankroll.chips if bankroll else 0,
            }
        ), 402

    # Record the unlock first (idempotent). If it was already owned (a race),
    # bail before charging — never double-charge on a retry. The reverse
    # order would risk charging twice; storing first at worst grants a free
    # unlock if the debit then fails, which favors the player.
    newly = game_repo.record_informant_unlock(
        sandbox_id, observer_id, personality_id, section_id, price
    )
    if not newly:
        return jsonify({'error': 'Section already unlocked'}), 409

    # Debit bankroll → recyclable bank pool (mirrors the vice-spending sink).
    from cash_mode.bankroll import PlayerBankrollState
    from core.economy import ledger

    new_bankroll = PlayerBankrollState(
        player_id=bankroll.player_id,
        chips=bankroll.chips - price,
        starting_bankroll=bankroll.starting_bankroll,
    )
    bankroll_repo.save_player_bankroll(new_bankroll)
    ledger.record_informant_unlock(
        chip_ledger_repo,
        owner_id=observer_id,
        amount=price,
        sandbox_id=sandbox_id,
        context={'opponent_id': personality_id, 'section_id': section_id},
    )

    updated = compute_scouting(life or {}, purchased | {section_id})
    return jsonify(
        {
            'scouting': updated,
            'bankroll': new_bankroll.chips,
            'section_id': section_id,
            'price': price,
        }
    )


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
