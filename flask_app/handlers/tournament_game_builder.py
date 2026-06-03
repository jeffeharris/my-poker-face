"""Builds the human's live single-table game for a multi-table tournament.

The human plays one table as an ordinary single-table game through the existing
machinery; this builder constructs that game from the tournament field's view of
the human's table (`human_table_seat_specs`). It mirrors the NON-cash tournament
game shape from `game_routes.api_new_game` — minus the single-table
`tournament_tracker` (the multi-table `TournamentSession` owns elimination /
completion) and minus all cash plumbing (bankroll / fish / relationship / rake).

AI seats are production tiered solver bots with the expression (LLM table-talk)
layer OFF — zero LLM, consistent with the headless field. Seats are named by
their field id (e.g. `P07`); the human's seat is named by the field human id and
flagged `is_human` (the frontend renders it as "You").
"""

from __future__ import annotations

import logging
from datetime import datetime

from tournament.identity import resolve_display_name
from tournament.session import TournamentSession

from .tournament_handler import human_table_seat_specs

logger = logging.getLogger(__name__)

# The tournament seat's `name` is its field id (`personality_id` for the MTT
# bridge); the human-readable `nickname` is resolved through the canonical
# persona-identity resolver (`tournament.identity`) — the same lookup cash seats
# use — so the felt, the ticker, and the final standings all render one
# consistent name.

# One game id per human-tournament; relocation reconciles in place rather than
# spawning a new game (the id is just a key).


def make_tournament_ai_controller(name: str, state_machine, *, game_id: str, owner_id: str):
    """A production-compatible, no-LLM tiered solver controller for one AI seat."""
    from flask_app.extensions import capture_label_repo, decision_analysis_repo
    from flask_app.handlers.tiered_factory import build_tiered_controller

    return build_tiered_controller(
        player_name=name,
        state_machine=state_machine,
        llm_config={},
        game_id=game_id,
        owner_id=owner_id,
        capture_label_repo=capture_label_repo,
        decision_analysis_repo=decision_analysis_repo,
        expression_enabled=False,  # no LLM table talk in v1
    )


def build_tournament_seat_controller(
    name: str,
    state_machine,
    *,
    game_id: str,
    owner_id: str,
    real_persona_ids,
    bot_types: dict,
    player_llm_configs: dict,
    prompt_config=None,
):
    """P3.9c — build one AI seat for the human's live table.

    A **real-persona** seat gets the SAME persona-flavored controller cash mode
    builds: a per-persona `bot_type` from `assign_bot(personality_config)` +
    `expression_enabled=True`, so the circuit's characters actually play *and talk*
    like themselves (psychology + the relationship axes wired in P3.9a). A
    **synthetic** `P##` seat (legacy `/register` field) keeps the zero-LLM tiered
    solver — no personality config to flavor it. The chosen `bot_type` + llm_config
    are recorded into the passed dicts so the same intent persists for cold-load
    (`restore_ai_controllers` rebuilds from `player_llm_configs` + `bot_types`).
    """
    from flask_app import extensions

    if name in real_persona_ids:
        from flask_app.handlers.tiered_factory import build_controller
        from poker.cash_bot_assignment import assign_bot

        personality_config = extensions.personality_repo.load_personality_by_id(name)
        rule_strategy = (
            personality_config.get("rule_strategy")
            if isinstance(personality_config, dict)
            else None
        )
        # A fish persona (calling-station donor) routes to the rule-based fish
        # controller, not the poise bucket — mirrors cash. Rare in a tournament
        # field (fish are non-circulating), but handled so a stray entry behaves.
        if rule_strategy == "fish":
            bot_types[name] = "fish"
            player_llm_configs[name] = {}
            return build_controller(
                bot_type="fish",
                player_name=name,
                state_machine=state_machine,
                game_id=game_id,
                owner_id=owner_id,
                capture_label_repo=extensions.capture_label_repo,
                decision_analysis_repo=extensions.decision_analysis_repo,
            )
        assignment = assign_bot(personality_config)
        bot_types[name] = assignment.bot_type
        player_llm_configs[name] = assignment.llm_config
        return build_controller(
            bot_type=assignment.bot_type,
            player_name=name,
            state_machine=state_machine,
            llm_config=assignment.llm_config,
            prompt_config=prompt_config,
            game_id=game_id,
            owner_id=owner_id,
            capture_label_repo=extensions.capture_label_repo,
            decision_analysis_repo=extensions.decision_analysis_repo,
            expression_enabled=True,
        )

    # Synthetic seat — zero-LLM tiered solver (recorded as such for cold-load).
    bot_types[name] = "sharp"
    player_llm_configs[name] = {}
    return make_tournament_ai_controller(name, state_machine, game_id=game_id, owner_id=owner_id)


def build_tournament_game(
    session: TournamentSession,
    *,
    tournament_id: str,
    owner_id: str,
    owner_name: str,
    resolver_kind: str = 'fake',
) -> str:
    """Create + register the human's live game for their current table. Returns
    the game_id. Advances to the first human action (hole cards dealt)."""
    from flask_app import extensions
    from flask_app.game_adapter import StateMachineAdapter
    from flask_app.routes.game_routes import generate_game_id, load_game_mode_preset
    from flask_app.services import game_state_service, tournament_economy_service as econ
    from flask_app.services.sandbox_resolver import resolve_default_sandbox_for
    from poker.memory import AIMemoryManager
    from poker.poker_game import Player, PokerGameState, create_deck
    from poker.poker_state_machine import PokerStateMachine
    from poker.pressure_detector import PressureEventDetector
    from poker.pressure_stats import PressureStatsTracker
    from poker.repositories.sqlite_repositories import PressureEventRepository

    specs = human_table_seat_specs(session)
    big_blind = session.current_level().big_blind
    players = tuple(
        Player(
            name=s.player_id,
            stack=s.stack,
            is_human=s.is_human,
            nickname=resolve_display_name(
                s.player_id,
                is_human=s.is_human,
                owner_name=owner_name,
                personality_repo=extensions.personality_repo,
            ),
        )
        for s in specs
    )
    dealer_idx = next((i for i, s in enumerate(specs) if s.is_button), 0)
    deck_seed = session.config.seed * 100_003 + session.rounds

    game_state = PokerGameState(
        players=players,
        deck=create_deck(shuffled=True, random_seed=deck_seed),
        current_ante=big_blind,
        last_raise_amount=big_blind,
        current_dealer_idx=dealer_idx,
    )
    base_state_machine = PokerStateMachine(
        game_state=game_state,
        blind_config={"growth": 1.0, "hands_per_level": 999_999, "max_blind": big_blind},
    )
    state_machine = StateMachineAdapter(base_state_machine)
    game_id = f"tourney-{generate_game_id()}"

    persistence_db_path = extensions.persistence_db_path
    hand_history_repo = extensions.hand_history_repo

    # P3.9c — real persona play on the human's table. A real-persona field
    # (autonomous/accept) builds its seats like a cash table — per-persona
    # bot_type + LLM table talk — so the circuit's characters show up to play
    # YOU. A synthetic `P##` field (legacy /register) stays the zero-LLM tiered
    # solver. `real_persona_ids` (computed once, reused for the dossier wiring
    # below) is the gate; fields are homogeneous, so its truthiness == persona
    # field. The per-seat bot_type + llm_config are recorded for cold-load.
    real_persona_ids = econ.real_persona_ids_for(session, extensions.personality_repo)
    is_persona_field = bool(real_persona_ids)
    seat_prompt_config = load_game_mode_preset("standard") if is_persona_field else None
    bot_types: dict = {}
    player_llm_configs: dict = {}
    ai_controllers: dict = {}
    for s in specs:
        if s.is_human:
            continue
        ai_controllers[s.player_id] = build_tournament_seat_controller(
            s.player_id,
            state_machine,
            game_id=game_id,
            owner_id=owner_id,
            real_persona_ids=real_persona_ids,
            bot_types=bot_types,
            player_llm_configs=player_llm_configs,
            prompt_config=seat_prompt_config,
        )

    memory_manager = AIMemoryManager(game_id, persistence_db_path, owner_id=owner_id)
    memory_manager.set_hand_history_repo(hand_history_repo)
    # P3.9a — light up the opponent-dossier grind for the human's tournament
    # table. Two seams unify tournament observations with the cash dossier:
    #   (a) a sandbox_id so `fold_observations_into_lifetime` is not a no-op, and
    #   (b) each real-persona AI seat registered under its `personality_id` (which
    #       IS `Player.name` for the MTT bridge) so folds key the SAME lifetime row
    #       the cash dossier reads → one running observed-hand count per persona.
    # `cash_mode=False` keeps chip PnL / cash_pair_stats writes off (chips reset in
    # a tournament) while still firing relationship events at the boundary — your
    # nemesis remembers you busted them. Synthetic `P##` fields skip registration
    # (gated on `real_persona_ids`) so a non-persona field writes no junk rows.
    sandbox_id = resolve_default_sandbox_for(owner_id, sandbox_repo=extensions.sandbox_repo)
    if extensions.relationship_repo is not None and sandbox_id:
        memory_manager.set_relationship_repo(
            extensions.relationship_repo, cash_mode=False, sandbox_id=sandbox_id
        )
    real_persona_ids = econ.real_persona_ids_for(session, extensions.personality_repo)
    for s in specs:
        if s.is_human:
            memory_manager.initialize_human_observer(s.player_id, personality_id=owner_id)
            continue
        memory_manager.initialize_for_player(
            s.player_id,
            personality_id=s.player_id if s.player_id in real_persona_ids else None,
        )
        controller = ai_controllers[s.player_id]
        controller.session_memory = memory_manager.get_session_memory(s.player_id)
        controller.opponent_model_manager = memory_manager.get_opponent_model_manager()
        controller.memory_manager = memory_manager

    # T3-77 — a cash-world (Circuit) tournament is part of the cash world, so its
    # real personas arrive in the mood the world left them in. Hydrate each
    # real-persona seat from the per-persona emotional_state_json (schema v97);
    # finalize_tournament flushes the evolved mood back, so it's two-way (chips
    # reset per tournament, but mood is continuous with the cash world). Gated on
    # real_persona_ids so a synthetic P## field stays at baseline. The seat name
    # IS the personality_id (MTT bridge). Fresh-build only — cold-load restores
    # the per-game psychology_json instead.
    if sandbox_id and real_persona_ids:
        from cash_mode.psychology_persistence import hydrate_persona_psychology

        for pid, controller in ai_controllers.items():
            if pid in real_persona_ids:
                hydrate_persona_psychology(controller, pid, extensions.bankroll_repo, sandbox_id)

    pressure_event_repo = PressureEventRepository(persistence_db_path)
    pressure_detector = PressureEventDetector()
    pressure_stats = PressureStatsTracker(game_id, pressure_event_repo)

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
        "owner_id": owner_id,
        "owner_name": owner_name,
        "last_announced_phase": None,
        "guest_tracking_id": None,
        "messages": [
            {
                "id": "1",
                "sender": "Table",
                "content": "***   MAIN EVENT   ***",
                "timestamp": datetime.now().isoformat(),
                "type": "table",
            }
        ],
        "hand_start_stacks": {p.name: p.stack for p in state_machine.game_state.players},
        "short_stack_players": set(),
        # Tournament meta keys — the gate + bridge read these. NOTE: no
        # `tournament_tracker` (the multi-table session owns eliminations) and no
        # `cash_mode`, so handle_eliminations / check_tournament_complete /
        # the cash block all early-return for this game.
        "tournament_session": session,
        "tournament_multi_table": True,  # use the MTT boundary, not the single-table one
        "tournament_id": tournament_id,
        "tournament_table_id": session.human_table.table_id,
        "tournament_human_id": session.human_id,
        # P3.9c — the living per-seat controller intent. The boundary re-persists
        # these after a table balance adds seats, and `_make` (the reconcile
        # controller factory) updates them in place so balanced-in personas get
        # the same flavored build + cold-load contract.
        "tournament_is_persona_field": is_persona_field,
        "tournament_bot_types": bot_types,
        "tournament_player_llm_configs": player_llm_configs,
        # T3-77 — the cash sandbox this cash-world tournament belongs to, so
        # finalize_tournament can flush each persona's evolved mood back to the
        # right per-persona emotional_state_json. Only meaningful for a persona
        # field (see the hydrate gate above).
        "tournament_sandbox_id": sandbox_id,
    }
    game_state_service.set_game(game_id, game_data)
    # Persist the per-seat LLM/bot intent so it survives a cold load.
    #
    # SYNTHETIC field (legacy /register): a zero-LLM tiered field. Stamp
    # `ai_chat=False` + every seat `sharp` so `restore_ai_controllers` rebuilds
    # WITHOUT the expression layer (an empty config would otherwise 404 on the
    # per-decision narration call — see tiered_factory). The `tourney-` restore
    # guard in game_routes is the belt; this is the suspenders.
    #
    # PERSONA field (P3.9c, autonomous/accept): mirror the cash cold-load
    # contract exactly — `ai_chat=True` + per-seat `bot_types` + the real
    # `player_llm_configs` (provider/model) so each persona seat rebuilds WITH
    # table talk and a valid config. The game_routes `tourney-` guard reads this
    # persisted `ai_chat` (defaulting False for legacy rows that saved none).
    llm_configs = _build_seat_llm_configs(
        is_persona_field, ai_controllers, bot_types, player_llm_configs
    )
    extensions.game_repo.save_game(
        game_id, state_machine._state_machine, owner_id, owner_name, llm_configs=llm_configs
    )
    return game_id


def _build_seat_llm_configs(
    is_persona_field: bool, ai_controllers: dict, bot_types: dict, player_llm_configs: dict
) -> dict:
    """The `llm_configs` blob persisted on the games row for cold-load. Persona
    fields carry `ai_chat=True` + per-seat bot_types/configs (cash contract);
    synthetic fields carry `ai_chat=False` + all-`sharp` (zero-LLM contract)."""
    if is_persona_field:
        return {
            "ai_chat": True,
            "player_llm_configs": dict(player_llm_configs),
            "default_llm_config": {},
            "bot_types": dict(bot_types),
        }
    return {"ai_chat": False, "bot_types": {name: "sharp" for name in ai_controllers}}


def _load_standard_preset():
    """The default prompt-config preset for persona seats (mirrors cash)."""
    from flask_app.routes.game_routes import load_game_mode_preset

    return load_game_mode_preset("standard")


def _persist_seat_llm_configs(
    game_id: str, game_data: dict, state_machine, bot_types: dict, player_llm_configs: dict
) -> None:
    """Re-save the persona field's per-seat LLM/bot intent after a balance.
    Best-effort — a miss just means a balanced-in seat falls back to defaults on
    a cold-load before the next save; never break the live boundary."""
    try:
        from flask_app import extensions

        extensions.game_repo.save_game(
            game_id,
            state_machine._state_machine,
            game_data.get("owner_id"),
            game_data.get("owner_name"),
            llm_configs={
                "ai_chat": True,
                "player_llm_configs": dict(player_llm_configs),
                "default_llm_config": {},
                "bot_types": dict(bot_types),
            },
        )
    except Exception:  # noqa: BLE001 — durability only, never break the game
        logger.exception("tournament seat-config re-persist failed for %s", game_id)


def tournament_hand_boundary(game_id: str, game_data: dict, state_machine) -> bool:
    """Effectful hand-boundary hook for the human's tournament game (called from
    game_handler). Advances the field, reconciles or stops, emits tournament
    events. Returns True if the human's game should pause (out / complete)."""
    from .tournament_handler import (
        COMPLETE,
        HUMAN_OUT,
        RELOCATED,
        advance_tournament_after_hand,
    )

    owner_id = game_data.get("owner_id")

    # P3.9c — build balanced-in seats (table breaks bring new players mid-event)
    # with the same flavor as the starting table: a real persona gets the talking
    # cash-style controller, a synthetic seat the tiered solver. The bot_type +
    # config are recorded into the live game_data dicts so the boundary re-persist
    # (below) keeps cold-load correct after a balance. Recompute the persona set
    # from the (possibly cold-loaded) session — game_data may lack the build-time
    # cache after a rehydrate.
    from .tournament_handler import _real_persona_ids_for_session

    session = game_data.get("tournament_session")
    real_persona_ids = (
        _real_persona_ids_for_session(session) if session is not None else frozenset()
    )
    is_persona_field = game_data.get("tournament_is_persona_field", bool(real_persona_ids))
    bot_types = game_data.setdefault("tournament_bot_types", {})
    player_llm_configs = game_data.setdefault("tournament_player_llm_configs", {})
    seat_prompt_config = _load_standard_preset() if is_persona_field else None

    def _make(name, sm):
        return build_tournament_seat_controller(
            name,
            sm,
            game_id=game_id,
            owner_id=owner_id,
            real_persona_ids=real_persona_ids,
            bot_types=bot_types,
            player_llm_configs=player_llm_configs,
            prompt_config=seat_prompt_config,
        )

    outcome = advance_tournament_after_hand(game_data, state_machine, make_controller=_make)
    if is_persona_field:
        # Re-persist the seat intent so a balanced-in persona survives cold-load
        # with its bot_type + config (the live save path uses COALESCE, so an
        # llm_configs-less save would otherwise keep the stale pre-balance blob).
        _persist_seat_llm_configs(game_id, game_data, state_machine, bot_types, player_llm_configs)
    _emit_tournament(
        game_data, outcome, RELOCATED=RELOCATED, HUMAN_OUT=HUMAN_OUT, COMPLETE=COMPLETE
    )
    if outcome.kind == COMPLETE:
        # Distribute the prize pool the moment the field locks every finishing
        # position (the play-out route + advance carry the same idempotent call
        # for completions that don't run through this live boundary).
        _apply_tournament_payout(game_data)
    if outcome.kind in (COMPLETE, HUMAN_OUT):
        # Unified completion: persist the result row + the human's career stats,
        # the same way a single-table game does — at the human's terminal moment
        # (field complete, or the human busting out). Emit `tournament_complete`
        # to the game room only when the field is actually COMPLETE (the human is
        # at the table for the finish — they won, or busted on the last hand), so
        # both single- and multi-table tournaments land on the same
        # TournamentComplete screen. On an early bust (HUMAN_OUT) we only record
        # stats; the human routes to the standings hub to watch/leave.
        from flask_app.handlers.tournament_completion import finalize_tournament

        finalize_tournament(game_id, game_data, emit=(outcome.kind == COMPLETE))
    _fold_observations(game_id, game_data)
    _persist_boundary(game_id, game_data)
    return outcome.kind in (HUMAN_OUT, COMPLETE)


def _fold_observations(game_id: str, game_data: dict) -> None:
    """P3.9a — persist this hand's opponent observations into the durable
    lifetime table at the tournament boundary (robustness).

    The per-human-action path in `game_routes.api_process_action` already folds
    after the human's POST, but a hand that finishes on an AI action (the human
    folded/checked earlier) — or the final / AI-only progression hand — isn't
    captured until the next human action that never comes. Mirror the two repo
    calls here so the completed hand's observations land. No-op unless the
    memory_manager carries a sandbox_id (set in the builder for real-persona
    fields); isolated so a fold hiccup never breaks the live game."""
    try:
        from flask_app import extensions

        mm = game_data.get("memory_manager")
        if mm is None or not mm.sandbox_id:
            return
        extensions.game_repo.save_opponent_models(game_id, mm.get_opponent_model_manager())
        extensions.game_repo.fold_observations_into_lifetime(game_id, mm.sandbox_id)
    except Exception:  # noqa: BLE001 — dossier grind is best-effort
        logger.exception("tournament boundary observation fold failed for %s", game_id)


def _apply_tournament_payout(game_data: dict) -> None:
    """Distribute the prize pool at the live boundary on COMPLETE. Best-effort
    and idempotent (the payout_status guard) — never break the game; a retry via
    the play-out route is a safe no-op once paid."""
    try:
        from flask_app.extensions import (
            bankroll_repo,
            chip_ledger_repo,
            personality_repo,
            prestige_snapshots_repo,
            sandbox_repo,
            tournament_session_repo,
        )
        from flask_app.services import game_state_service, tournament_economy_service as econ
        from flask_app.services.sandbox_resolver import resolve_default_sandbox_for

        owner_id = game_data.get("owner_id")
        tournament_id = game_data.get("tournament_id")
        session = game_data.get("tournament_session")
        if not (owner_id and tournament_id and session):
            return
        sandbox_id = resolve_default_sandbox_for(owner_id, sandbox_repo=sandbox_repo)
        with game_state_service.get_sandbox_lock(sandbox_id):
            econ.apply_payout_on_complete(
                tournament_id=tournament_id,
                session=session,
                human_owner_id=owner_id,
                sandbox_id=sandbox_id,
                bankroll_repo=bankroll_repo,
                ledger_repo=chip_ledger_repo,
                session_repo=tournament_session_repo,
                # Credit real personas in a human-played persona field; synthetic
                # ids sweep to the pool. (Without this the AIs are never paid.)
                real_persona_ids=econ.real_persona_ids_for(session, personality_repo),
                # Phase D: grant renown to in-the-money finishers (flag-gated).
                prestige_repo=prestige_snapshots_repo,
            )
    except Exception:  # noqa: BLE001 — payout must never crash the live game
        logger.exception("tournament boundary payout failed")


def _persist_boundary(game_id: str, game_data: dict) -> None:
    """Persist the field after a live hand boundary — the critical save point
    (captures field/seating/standings after every advance). Runs inside
    progress_game's game lock. Best-effort: the in-memory session stays
    authoritative for the live process if the write fails."""
    try:
        from flask_app.services import tournament_registry as registry

        registry.persist_session(
            tournament_id=game_data.get("tournament_id"),
            owner_id=game_data.get("owner_id"),
            session=game_data.get("tournament_session"),
            resolver_kind=game_data.get("tournament_resolver_kind", 'fake'),
            game_id=game_id,
        )
    except Exception:  # noqa: BLE001 — durability layer, never break the game
        logger.exception("tournament boundary persist failed for %s", game_id)


def _emit_tournament(game_data, outcome, *, RELOCATED, HUMAN_OUT, COMPLETE) -> None:
    """Push multi-table-tournament (MTT) events to the owner's lobby room (the
    game-page socket is already joined to it on connect). Always a standings
    update; plus a relocation / elimination / complete beat.

    Events use the `mtt_` namespace, deliberately distinct from the legacy
    single-table `tournament_complete` (emitted to the *game* room and consumed
    by usePokerGame into the `TournamentResult` end screen). The two payload
    shapes are incompatible — keeping the namespaces separate stops the MTT
    standings payload from being coerced into the single-table screen."""
    try:
        from flask_app.extensions import socketio
        from flask_app.services import presence

        if socketio is None:
            return
        owner_id = game_data.get("owner_id")
        tournament_id = game_data.get("tournament_id")
        room = presence.lobby_room_name(owner_id)
        payload = {
            "tournament_id": tournament_id,
            "standings": outcome.standings,
            # Activity beats since the human's last hand (KOs / breaks / bubble /
            # milestones / level-up) — the ticker, toasts, and hub feed read these.
            "beats": getattr(outcome, "beats", []),
        }
        socketio.emit("mtt_update", payload, to=room)
        if outcome.kind == RELOCATED:
            socketio.emit(
                "mtt_relocated",
                {"tournament_id": tournament_id, "table_id": outcome.table_id},
                to=room,
            )
        elif outcome.kind == HUMAN_OUT:
            socketio.emit(
                "mtt_eliminated",
                {
                    "tournament_id": tournament_id,
                    "finishing_position": outcome.standings["human"]["rank"],
                },
                to=room,
            )
        elif outcome.kind == COMPLETE:
            socketio.emit(
                "mtt_complete",
                {"tournament_id": tournament_id, "standings": outcome.standings},
                to=room,
            )
    except Exception:  # noqa: BLE001 — emits are best-effort
        pass
