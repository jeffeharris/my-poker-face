"""Training / Coaching mode routes — spar against selectable-difficulty bots.

Training games are a **flavor of the tournament game flow**, not a separate
orchestrator (same pattern as cash mode). The action route, SocketIO emits,
`progress_game`, hand engine, AI controllers, and React UI are all reused. A
training game is identified by a `train-` `game_id` prefix and a
`training_mode=True` flag on the game_data dict.

Non-counting is achieved by *wiring absence* (see docs/plans/TRAINING_MODE.md):
the builder deliberately does NOT wire a relationship repo, a tournament
tracker, a bankroll, or a sandbox. That suppresses cash economy, prestige,
relationship memory, and tournament/leaderboard writes with no per-write
guards. The one persistent write training KEEPS is the per-user coach
skill-progression record (it only needs `owner_id`), so practice still improves
your tracked skills.

CAUTION — `relationship_states` is NOT `cash_mode`-gated: it writes whenever any
relationship repo is wired. The only safe suppression is to never call
`set_relationship_repo` for a training game (here AND on cold-load in
game_routes.py).

Phase 1 scope: free-play sparring + difficulty tiers + auto-on coach. Table
presets, scripted spots, the intercept coach, and the read-the-player drill are
later phases.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from flask_app import config
from flask_app.extensions import limiter
from training.opponent_roster import VALID_DIFFICULTIES, resolve_opponents
from training.scenario import (
    DEFAULT_PRESET_ID,
    VALID_PRESET_IDS,
    TablePreset,
    get_table_preset,
    list_table_presets,
)
from training.state_builder import (
    build_scripted_spot_state_machine,
    build_table_preset_state_machine,
)

logger = logging.getLogger(__name__)

training_bp = Blueprint("training", __name__)


def _make_controller(
    bot_type: str,
    *,
    player_name: str,
    state_machine,
    game_id: str,
    owner_id: Optional[str],
    llm_config: Dict[str, Any],
    ai_chat: bool,
):
    """Build one AI controller for a training seat.

    Dispatch MUST mirror `game_handler.restore_ai_controllers` exactly so a
    training game restores to identical controllers after eviction. Rule-bot
    strategy names (fish/foldy/...) fall through to the else-branch in both
    places.
    """
    from flask_app.extensions import capture_label_repo, decision_analysis_repo

    if bot_type == "sharp":
        from flask_app.handlers.tiered_factory import build_tiered_controller

        return build_tiered_controller(
            player_name=player_name,
            state_machine=state_machine,
            llm_config=llm_config,
            game_id=game_id,
            owner_id=owner_id,
            capture_label_repo=capture_label_repo,
            decision_analysis_repo=decision_analysis_repo,
            expression_enabled=ai_chat,
        )
    if bot_type == "baseline_solver":
        from flask_app.handlers.tiered_factory import build_tiered_controller

        return build_tiered_controller(
            player_name=player_name,
            state_machine=state_machine,
            llm_config=llm_config,
            game_id=game_id,
            owner_id=owner_id,
            capture_label_repo=capture_label_repo,
            decision_analysis_repo=decision_analysis_repo,
            baseline=True,
        )

    # Rule-based controllers (psychology-aware). casebot/gto_lite map to named
    # strategies; everything else is a BUILT_IN_STRATEGIES key passed straight
    # through (matches restore's else-branch → RuleBotController(strategy=name)).
    from poker.rule_bot_controller import RuleBotController

    strategy = {"casebot": "case_based", "gto_lite": "pot_odds_robot"}.get(bot_type, bot_type)
    return RuleBotController(
        player_name=player_name,
        state_machine=state_machine,
        strategy=strategy,
        llm_config={},
        game_id=game_id,
        owner_id=owner_id,
        capture_label_repo=capture_label_repo,
        decision_analysis_repo=decision_analysis_repo,
    )


def _purge_training_games(owner_id: str) -> None:
    """Delete this owner's prior `train-` games (DB row + in-memory session).

    Training sessions are ephemeral practice; we keep at most one per owner
    (mirrors cash's one-row-per-owner invariant) so they don't accumulate or
    eat into the normal saved-game limit (`count_user_games` counts all rows).
    Best-effort: a purge failure must not block starting a new session.
    """
    from flask_app import extensions
    from flask_app.services import game_state_service

    try:
        prior = extensions.game_repo.list_games(owner_id=owner_id, limit=100, offset=0)
    except Exception as e:
        logger.warning("[TRAINING] purge list_games failed for %s: %s", owner_id, e)
        return
    for g in prior:
        if not g.game_id.startswith("train-"):
            continue
        try:
            game_state_service.delete_game(g.game_id)
            extensions.game_repo.delete_game(g.game_id)
        except Exception as e:
            logger.warning("[TRAINING] failed to purge prior training game %s: %s", g.game_id, e)


def _build_training_game(
    *,
    owner_id: str,
    owner_name: Optional[str],
    player_name: str,
    difficulty: str,
    preset: TablePreset,
    scenario=None,
) -> str:
    """Create + register a training game; return its `train-` game_id.

    Two table sources: a free-play `preset` (default) or a scripted `scenario`
    (a TrainingScenario whose `config` is a ScriptedSpot). When a scenario is
    given, the table is built mid-hand via the scripted-spot factory and is
    already positioned at the human's decision — so `run_until_player_action`
    is skipped. `difficulty` still selects the villain roster either way.

    Deliberately does NOT wire: a relationship repo (relationship_states is not
    cash_mode-gated), a tournament tracker (no elimination/placement flow), a
    bankroll, or a sandbox. Coach mode is forced to 'proactive' so the coach is
    auto-engaged. See module docstring.
    """
    from flask_app import extensions
    from flask_app.routes.game_routes import generate_game_id
    from flask_app.services import game_state_service
    from poker.memory import AIMemoryManager
    from poker.pressure_detector import PressureEventDetector
    from poker.pressure_stats import PressureStatsTracker
    from poker.repositories.sqlite_repositories import PressureEventRepository
    from poker.utils import get_celebrities

    # Opponent identities are celebrity names (a curated pool — never triggers
    # junk-persona auto-create); the difficulty roster, not the identity,
    # drives how they play. Anonymized opponents arrive with the Phase 5
    # read-the-player drill.
    n_opponents = len(scenario.config.villain_stacks_bb) if scenario else preset.opponents
    pool = [n for n in get_celebrities(shuffled=True) if n.lower() != player_name.lower()]
    ai_names = pool[:n_opponents]
    bot_types_list = resolve_opponents(difficulty, len(ai_names))
    bot_types: Dict[str, str] = {name: bt for name, bt in zip(ai_names, bot_types_list)}

    if scenario is not None:
        state_machine = build_scripted_spot_state_machine(scenario.config, player_name, ai_names)
    else:
        state_machine = build_table_preset_state_machine(preset, player_name, ai_names)
    game_id = f"train-{generate_game_id()}"

    # Rule bots make zero LLM calls; the tiered "sharp" bot's narration is off
    # (ai_chat=False) so hard tables are instant too. The only LLM cost in
    # training is the coach.
    ai_chat = False
    from core.llm.settings import get_default_model, get_default_provider

    default_llm_config = {"provider": get_default_provider(), "model": get_default_model()}

    ai_controllers: Dict[str, Any] = {}
    player_llm_configs: Dict[str, Dict[str, Any]] = {}
    for player in state_machine.game_state.players:
        if player.is_human:
            continue
        bot_type = bot_types[player.name]
        player_llm_configs[player.name] = {}  # rule/tiered bots run config-light here
        ai_controllers[player.name] = _make_controller(
            bot_type,
            player_name=player.name,
            state_machine=state_machine,
            game_id=game_id,
            owner_id=owner_id,
            llm_config=default_llm_config,
            ai_chat=ai_chat,
        )

    pressure_event_repo = PressureEventRepository(config.DB_PATH)
    pressure_detector = PressureEventDetector()
    pressure_stats = PressureStatsTracker(game_id, pressure_event_repo)

    memory_manager = AIMemoryManager(game_id, extensions.persistence_db_path, owner_id=owner_id)
    memory_manager.set_hand_history_repo(extensions.hand_history_repo)
    # NOTE: intentionally NO set_relationship_repo — training is non-counting and
    # relationship_states is not cash_mode-gated, so wiring any relationship repo
    # would leak rows. This must stay omitted on cold-load too (game_routes.py).
    for player in state_machine.game_state.players:
        try:
            pid = extensions.personality_repo.resolve_name_to_personality_id(player.name)
        except Exception:
            pid = None
        if not player.is_human:
            memory_manager.initialize_for_player(player.name, personality_id=pid)
            controller = ai_controllers[player.name]
            controller.session_memory = memory_manager.get_session_memory(player.name)
            controller.opponent_model_manager = memory_manager.get_opponent_model_manager()
            controller.memory_manager = memory_manager
        else:
            memory_manager.initialize_human_observer(player.name, personality_id=owner_id or pid)

    # Deal cards + post blinds before recording hand start. A scripted spot is
    # already positioned mid-hand at the human's decision — advancing it would
    # move past their turn, so only free-play preset tables run-until-action.
    if scenario is None:
        state_machine.run_until_player_action()
    memory_manager.on_hand_start(
        state_machine.game_state, hand_number=1, deck_seed=state_machine.current_hand_seed
    )

    game_data = {
        "state_machine": state_machine,
        "ai_controllers": ai_controllers,
        "pressure_detector": pressure_detector,
        "pressure_stats": pressure_stats,
        "memory_manager": memory_manager,
        # No 'tournament_tracker' key — its absence disables the elimination /
        # placement flow (handle_eliminations keys off its presence).
        "training_mode": True,
        "training_difficulty": difficulty,
        "training_preset": None if scenario else preset.id,
        "training_scenario_id": scenario.id if scenario else None,
        "owner_id": owner_id,
        "owner_name": owner_name,
        "llm_config": default_llm_config,
        "player_llm_configs": player_llm_configs,
        "player_prompt_configs": {},
        "bot_types": bot_types,
        "default_game_mode": "casual",
        "ai_chat": ai_chat,
        "last_announced_phase": None,
        "guest_tracking_id": None,
        "guest_messages_this_action": 0,
        "messages": [
            {
                "id": "1",
                "sender": "Table",
                "content": (
                    f"***   DRILL: {scenario.name} — this game does not count   ***"
                    if scenario
                    else "***   TRAINING TABLE — this game does not count   ***"
                ),
                "timestamp": _now_iso(),
                "type": "table",
            }
        ],
        "hand_start_stacks": {p.name: p.stack for p in state_machine.game_state.players},
        "short_stack_players": set(),
    }
    game_state_service.set_game(game_id, game_data)

    # Persist bot_types so cold-load rebuilds the same controllers (the else-
    # branch in restore_ai_controllers handles rule-strategy names directly).
    extensions.game_repo.save_game(
        game_id,
        state_machine._state_machine,
        owner_id,
        owner_name,
        llm_configs={
            "player_llm_configs": player_llm_configs,
            "default_llm_config": default_llm_config,
            "bot_types": dict(bot_types),
            "ai_chat": ai_chat,
        },
    )
    # Coach is the point of training mode: force it on (proactive). This is
    # persisted on the games row, so it survives cold-load without extra wiring.
    extensions.game_repo.save_coach_mode(game_id, "proactive")

    logger.info(
        "[TRAINING] Created game_id=%r owner=%r difficulty=%r table=%r opponents=%r bot_types=%r",
        game_id,
        owner_id,
        difficulty,
        f"scenario:{scenario.id}" if scenario else f"preset:{preset.id}",
        ai_names,
        bot_types,
    )
    return game_id


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat()


@training_bp.route("/api/training/start", methods=["POST"])
@limiter.limit(config.RATE_LIMIT_GAME_ACTION)
def start_training_session():
    """Create a non-counting practice game vs difficulty-tiered opponents."""
    from flask_app import extensions

    current_user = extensions.auth_manager.get_current_user() if extensions.auth_manager else None
    if not current_user or not current_user.get("id"):
        return jsonify({"error": "Authentication required", "code": "AUTH_REQUIRED"}), 401

    owner_id = current_user.get("id")
    owner_name = current_user.get("name")
    data = request.json or {}
    player_name = data.get("playerName", owner_name or "Player")

    difficulty = str(data.get("difficulty", "medium")).lower()
    if difficulty not in VALID_DIFFICULTIES:
        return jsonify(
            {
                "error": f"Invalid difficulty: {difficulty}",
                "valid_difficulties": sorted(VALID_DIFFICULTIES),
            }
        ), 400

    # A scripted drill (scenario_id) takes precedence over a free-play preset.
    scenario = None
    scenario_id = data.get("scenario_id")
    if scenario_id:
        from training.scenario_library import get_scenario

        scenario = get_scenario(scenario_id)
        if scenario is None:
            return jsonify({"error": f"Unknown scenario_id: {scenario_id}"}), 404

    preset_id = data.get("preset_id", DEFAULT_PRESET_ID)
    if preset_id not in VALID_PRESET_IDS:
        return jsonify(
            {"error": f"Invalid preset_id: {preset_id}", "valid_presets": sorted(VALID_PRESET_IDS)}
        ), 400
    preset = get_table_preset(preset_id)

    # One training session per owner — clear any prior one so practice games
    # don't accumulate or count against the saved-game limit.
    _purge_training_games(owner_id)

    try:
        game_id = _build_training_game(
            owner_id=owner_id,
            owner_name=owner_name,
            player_name=player_name,
            difficulty=difficulty,
            preset=preset,
            scenario=scenario,
        )
    except Exception as e:
        logger.error("[TRAINING] failed to build training game: %s", e, exc_info=True)
        return jsonify({"error": "Failed to create training game"}), 500

    return jsonify(
        {
            "game_id": game_id,
            "training_mode": True,
            "difficulty": difficulty,
            "preset_id": None if scenario else preset.id,
            "scenario_id": scenario.id if scenario else None,
        }
    )


@training_bp.route("/api/training/scenarios", methods=["GET"])
def list_training_scenarios():
    """List what a practice game can be set up with: free-play table presets
    AND scripted-spot drills.

    Difficulty (who you face) is chosen separately; presets describe the table
    shape (seats, stack depth, blinds), drills are fixed teaching spots.
    Auth-gated to match /start.
    """
    from flask_app import extensions
    from training.scenario_library import list_scenarios

    current_user = extensions.auth_manager.get_current_user() if extensions.auth_manager else None
    if not current_user or not current_user.get("id"):
        return jsonify({"error": "Authentication required", "code": "AUTH_REQUIRED"}), 401

    presets = [
        {
            "id": p.id,
            "title": p.title,
            "description": p.description,
            "opponents": p.opponents,
            "big_blind": p.big_blind,
            "starting_stack_bb": p.starting_stack_bb,
        }
        for p in list_table_presets()
    ]
    drills = [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "tags": s.tags,
            "phase": s.config.phase,
        }
        for s in list_scenarios()
    ]
    return jsonify(
        {"presets": presets, "default_preset_id": DEFAULT_PRESET_ID, "drills": drills}
    )
