"""Shared factory for constructing TieredBotController / BaselineSolverBot instances.

Used by `api_new_game()` (creating a new live game) and
`restore_ai_controllers()` (rehydrating after server restart).
Mirrors the canonical pattern in experiments/run_ai_tournament.py.
"""

import logging
from typing import Optional

from core.llm import CallType, LLMClient
from poker.strategy.expression_generator import ExpressionGenerator
from poker.strategy.strategy_table import (
    load_archetype_preflop_tables,
    load_depth_strategy_tables,
    load_hu_strategy_table,
    load_strategy_table,
)
from poker.tiered_bot_controller import BaselineSolverBot, TieredBotController

logger = logging.getLogger(__name__)


def build_tiered_controller(
    *,
    player_name: str,
    state_machine,
    llm_config: Optional[dict],
    game_id: Optional[str],
    owner_id: Optional[str],
    capture_label_repo=None,
    decision_analysis_repo=None,
    expression_enabled: bool = True,
    debug_logging: bool = False,
    baseline: bool = False,
) -> TieredBotController:
    """Build a TieredBotController, optionally with the Layer 3 expression generator wired.

    Args:
        baseline: If True, instantiate BaselineSolverBot (pure solver, no personality
            distortion, no expression layer regardless of expression_enabled).
    """
    llm_config = llm_config or {}
    strategy_table = load_strategy_table()
    hu_strategy_table = load_hu_strategy_table()  # None if file missing
    depth_strategy_tables = load_depth_strategy_tables()  # {} if files missing
    # Width-tier preflop charts (loose/station/tight) keyed by archetype; {} if
    # files missing → every archetype uses the base table. BaselineSolverBot
    # classifies as 'baseline' (not in the map) so it always uses the base.
    archetype_preflop_tables = load_archetype_preflop_tables()
    controller_cls = BaselineSolverBot if baseline else TieredBotController
    controller = controller_cls(
        player_name=player_name,
        state_machine=state_machine,
        strategy_table=strategy_table,
        hu_strategy_table=hu_strategy_table,
        depth_strategy_tables=depth_strategy_tables,
        archetype_preflop_tables=archetype_preflop_tables,
        llm_config=llm_config,
        game_id=game_id,
        owner_id=owner_id,
        capture_label_repo=capture_label_repo,
        decision_analysis_repo=decision_analysis_repo,
        debug_logging=debug_logging,
    )

    # Baseline solver intentionally skips the expression layer — it's the
    # "pure GTO, no personality" option.
    if expression_enabled and not baseline:
        llm_client = LLMClient(
            provider=llm_config.get('provider', 'openai'),
            model=llm_config.get('model'),
        )
        controller.expression_generator = ExpressionGenerator(
            llm_client=llm_client,
            prompt_manager=controller.prompt_manager,
        )
        controller._expression_call_type = CallType.COMMENTARY

    return controller


def build_fish_controller(
    *,
    player_name: str,
    state_machine,
    game_id=None,
    owner_id=None,
    capture_label_repo=None,
    decision_analysis_repo=None,
    fish_leak: Optional[str] = None,
) -> TieredBotController:
    """Build a casino fish as a tiered `calling_station` (the unified engine).

    Fish used to be RuleBotController(`fish`) bots; they now run through the
    tiered engine, where their loose-passive anchors classify as `calling_station`
    and pick up the station width-tier table (a true caller: VPIP ~45 / PFR ~16 /
    pays off). The legacy `fish_leak` tell is re-expressed as a `spot_tendency`
    override (see fish_loadout.fish_spot_tendencies). Expression (LLM) is OFF —
    fish make no LLM calls, exactly like the rule bot — and the per-decision
    equity Monte Carlo is skipped (analyzer-only, not the table decision), so the
    fish stay table-lookup fast. See docs/plans/FISH_AS_CALLING_STATION.md.
    """
    from poker.strategy.fish_loadout import fish_spot_tendencies

    controller = build_tiered_controller(
        player_name=player_name,
        state_machine=state_machine,
        llm_config={},
        game_id=game_id,
        owner_id=owner_id,
        capture_label_repo=capture_label_repo,
        decision_analysis_repo=decision_analysis_repo,
        expression_enabled=False,
    )
    tendencies = fish_spot_tendencies(fish_leak)
    if tendencies:
        controller._spot_tendencies_override = tendencies
        controller._spot_tendencies_resolved = True
    controller.skip_equity_in_analysis = True
    return controller
