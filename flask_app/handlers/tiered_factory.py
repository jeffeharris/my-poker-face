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
        from core.llm.config import INGAME_LLM_TIMEOUT_SECONDS

        llm_client = LLMClient(
            provider=llm_config.get('provider', 'openai'),
            model=llm_config.get('model'),
            # PRH-18: in-game narration — bound it so a stalled provider can't
            # hang the hand under the per-game lock.
            default_timeout=INGAME_LLM_TIMEOUT_SECONDS,
        )
        controller.expression_generator = ExpressionGenerator(
            llm_client=llm_client,
            prompt_manager=controller.prompt_manager,
        )
        controller._expression_call_type = CallType.COMMENTARY

    return controller


# Map of the rule-based "training" bot types exposed in Custom Game to their
# underlying RuleBotController strategy name.
_RULE_BOT_STRATEGY_MAP = {
    'casebot': 'case_based',
    'gto_lite': 'pot_odds_robot',
}


def build_controller(
    *,
    bot_type: Optional[str],
    player_name: str,
    state_machine,
    llm_config: Optional[dict] = None,
    prompt_config=None,
    game_id: Optional[str] = None,
    owner_id: Optional[str] = None,
    capture_label_repo=None,
    decision_analysis_repo=None,
    expression_enabled: bool = True,
    debug_logging: bool = False,
    fish_leak=None,
    default_strategy: Optional[str] = None,
):
    """Construct the AI controller for a given ``bot_type``.

    This is the single, canonical dispatch over bot type — the union of the
    branch-sets that were previously copy-pasted across the new-game route,
    the cash sit route, and the restore/refill paths. It ONLY constructs and
    returns the controller object; all call-site bookkeeping (registering the
    controller, stamping ``bot_types`` / ``llm_configs``, ``assign_bot``,
    fish detection, memory wiring, logging) stays at the call site.

    Dispatch:
        - ``'fish'``        → RuleBotController(strategy='fish', fish_leak=...)
        - ``'sharp'``       → build_tiered_controller(...)
        - ``'baseline_solver'`` → build_tiered_controller(..., baseline=True)
        - ``'casebot'`` / ``'gto_lite'`` → RuleBotController with the mapped
                              rule strategy (case_based / pot_odds_robot)
        - ``'chaos'``       → AIPlayerController (full LLM, full personality)
        - ``'lean'``        → LeanBoundedController
        - default/else      → HybridAIController, UNLESS ``default_strategy`` is
                              set, in which case RuleBotController(strategy=bot_type)
                              — this mirrors the restore path, which treats an
                              unknown bot_type as a rule-bot strategy name.

    Unknown-bot_type contract (intentional divergence -- NOT a bug):
        The create paths and the restore path send unknown bot_types down
        DIFFERENT fallbacks, and that is correct because they handle
        NON-OVERLAPPING input domains:

        * CREATE paths (``api_new_game``, cash sit/refill) never emit an
          unknown bot_type. ``api_new_game`` rejects anything outside
          ``VALID_BOT_TYPES`` (+ legacy aliases) with a 400; cash/refill only
          pass ``assign_bot`` outputs (chaos/standard/sharp) or the literals
          'fish'/'sharp'. So for create ``default_strategy is None`` and the
          Hybrid fallback is reachable ONLY via the recognised 'standard' key.
        * The RESTORE path reads PERSISTED bot_types. Today those are always
          VALID_BOT_TYPES (handled by explicit branches above) or legacy
          aliases (remapped before this call). The ``default_strategy``
          else-branch below therefore only ever catches LEGACY RAW
          rule-strategy names from old/experiment saves (e.g. 'abc',
          'always_fold', 'case_based', 'pot_odds_robot') -- and routing those
          to ``RuleBotController(strategy=bot_type)`` is exactly right (they
          ARE rule-bot strategy names; RuleBotController itself defaults an
          unrecognised strategy to always_fold).

        Net: the "same unknown value builds a different class on create vs
        restore" scenario cannot occur for any value a create path can emit.
        If you ever add a NEW value to ``VALID_BOT_TYPES`` in game_routes you
        MUST add a matching explicit branch here (above the else), or restored
        games will silently route it to RuleBot. The regression test
        ``tests/test_strategy/test_build_controller_unknown_bot_type.py`` pins
        this contract.

    Args:
        default_strategy: When provided (any truthy marker), unknown bot types
            are routed to ``RuleBotController(strategy=bot_type)`` rather than
            ``HybridAIController``. Used by the restore path. Defaults to None
            (new-game / cash semantics: unknown -> Hybrid).
        fish_leak: Passed through only on the ``'fish'`` branch.
        debug_logging: Forwarded to ``build_tiered_controller`` (sharp /
            baseline_solver branches).
    """
    llm_config = llm_config or {}

    if bot_type == 'fish':
        # Fish run through the unified tiered engine as a `calling_station`
        # (see build_fish_controller / docs/plans/FISH_AS_CALLING_STATION.md),
        # NOT a RuleBotController. The fish's tell now rides on its persona's
        # `spot_tendencies` (read natively on every build path), so `fish_leak`
        # is no longer threaded here — the kwarg stays on the signature for
        # back-compat with existing callers but is ignored on this branch.
        return build_fish_controller(
            player_name=player_name,
            state_machine=state_machine,
            game_id=game_id,
            owner_id=owner_id,
            capture_label_repo=capture_label_repo,
            decision_analysis_repo=decision_analysis_repo,
        )

    if bot_type == 'sharp':
        return build_tiered_controller(
            player_name=player_name,
            state_machine=state_machine,
            llm_config=llm_config,
            game_id=game_id,
            owner_id=owner_id,
            capture_label_repo=capture_label_repo,
            decision_analysis_repo=decision_analysis_repo,
            expression_enabled=expression_enabled,
            debug_logging=debug_logging,
        )

    if bot_type == 'baseline_solver':
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

    if bot_type in _RULE_BOT_STRATEGY_MAP:
        from poker.rule_bot_controller import RuleBotController

        return RuleBotController(
            player_name=player_name,
            state_machine=state_machine,
            strategy=_RULE_BOT_STRATEGY_MAP[bot_type],
            llm_config=llm_config,
            game_id=game_id,
            owner_id=owner_id,
            capture_label_repo=capture_label_repo,
            decision_analysis_repo=decision_analysis_repo,
        )

    if bot_type == 'chaos':
        from poker.controllers import AIPlayerController

        return AIPlayerController(
            player_name=player_name,
            state_machine=state_machine,
            llm_config=llm_config,
            prompt_config=prompt_config,
            game_id=game_id,
            owner_id=owner_id,
            capture_label_repo=capture_label_repo,
            decision_analysis_repo=decision_analysis_repo,
        )

    if bot_type == 'lean':
        from poker.lean_bounded_controller import LeanBoundedController

        return LeanBoundedController(
            player_name,
            state_machine,
            llm_config=llm_config,
            prompt_config=prompt_config,
            game_id=game_id,
            owner_id=owner_id,
            capture_label_repo=capture_label_repo,
            decision_analysis_repo=decision_analysis_repo,
        )

    if default_strategy is not None and bot_type != 'standard':
        # Restore-path semantics: an unrecognised bot_type is a rule-bot
        # strategy name (e.g. 'abc', 'always_fold', 'case_based'). The explicit
        # 'standard' key is excluded — it maps to HybridAIController below.
        from poker.rule_bot_controller import RuleBotController

        return RuleBotController(
            player_name=player_name,
            state_machine=state_machine,
            strategy=bot_type,
            llm_config=llm_config,
            game_id=game_id,
            owner_id=owner_id,
            capture_label_repo=capture_label_repo,
            decision_analysis_repo=decision_analysis_repo,
        )

    # Default: HybridAIController (full prompt pipeline + bounded options).
    from poker.hybrid_ai_controller import HybridAIController

    return HybridAIController(
        player_name,
        state_machine,
        llm_config=llm_config,
        prompt_config=prompt_config,
        game_id=game_id,
        owner_id=owner_id,
        capture_label_repo=capture_label_repo,
        decision_analysis_repo=decision_analysis_repo,
    )


def build_fish_controller(
    *,
    player_name: str,
    state_machine,
    game_id=None,
    owner_id=None,
    capture_label_repo=None,
    decision_analysis_repo=None,
) -> TieredBotController:
    """Build a casino fish as a tiered `calling_station` (the unified engine).

    Fish used to be RuleBotController(`fish`) bots; they now run through the
    tiered engine, where their loose-passive anchors classify as `calling_station`
    and pick up the station width-tier table (a true caller: VPIP ~45 / PFR ~16 /
    pays off). Expression (LLM) is OFF — fish make no LLM calls, exactly like the
    rule bot — and the per-decision equity Monte Carlo is skipped (analyzer-only,
    not the table decision), so the fish stay table-lookup fast.

    A fish's deliberate tell is carried as a `spot_tendencies` entry in its
    PERSONALITY CONFIG (e.g. `"spot_tendencies": [["sticky", 0.85]]`), which the
    controller reads natively via `_effective_spot_tendencies` on EVERY build path
    — sit, live-fill, and cold-load restore alike — so the leak survives a restart
    (no sit-only override). To convert a legacy `fish_leak` name to its tendency,
    see `poker.strategy.fish_loadout.fish_spot_tendencies` (the authoring helper).
    See docs/plans/FISH_AS_CALLING_STATION.md.
    """
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
    controller.skip_equity_in_analysis = True
    return controller
