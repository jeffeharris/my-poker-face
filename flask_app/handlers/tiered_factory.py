"""Shared factory for constructing TieredBotController / BaselineSolverBot instances.

Used by `api_new_game()` (creating a new live game) and
`restore_ai_controllers()` (rehydrating after server restart).
Mirrors the canonical pattern in experiments/run_ai_tournament.py.
"""

import logging
import threading
from typing import Optional

from core.llm import CallType, LLMClient
from poker.strategy.expression_generator import ExpressionGenerator
from poker.strategy.skill_tiers import DEFAULT_SKILL_TIER, apply_skill_tier
from poker.strategy.strategy_table import (
    StrategyTable,
    load_archetype_preflop_tables,
    load_depth_strategy_tables,
    load_hu_strategy_table,
    load_strategy_table,
)
from poker.tiered_bot_controller import BaselineSolverBot, TieredBotController

logger = logging.getLogger(__name__)


# Strategy tables are immutable for the process lifetime. Load them once and
# share across every controller instead of re-reading the JSON from disk on each
# build_tiered_controller call (cold game start / restore). Mirrors the
# memoization in cash_mode/full_sim.py, extended to all four tables this factory
# uses. (~20-50 ms + filesystem hit saved per game build.)
_strategy_tables_lock = threading.Lock()
_strategy_tables_loaded = False
_strategy_table: Optional[StrategyTable] = None
_hu_strategy_table: Optional[StrategyTable] = None
_depth_strategy_tables: dict = {}
_archetype_preflop_tables: dict = {}


def _get_strategy_tables() -> tuple[StrategyTable, Optional[StrategyTable], dict, dict]:
    """Lazy-load + memoize the four strategy tables (base preflop, heads-up,
    depth-keyed, archetype-width). Returns the shared, immutable instances."""
    global _strategy_tables_loaded, _strategy_table, _hu_strategy_table
    global _depth_strategy_tables, _archetype_preflop_tables
    if not _strategy_tables_loaded:  # slow path only until warm; no lock once loaded
        with _strategy_tables_lock:
            if not _strategy_tables_loaded:
                _strategy_table = load_strategy_table()
                _hu_strategy_table = load_hu_strategy_table()  # None if file missing
                _depth_strategy_tables = load_depth_strategy_tables()  # {} if missing
                _archetype_preflop_tables = load_archetype_preflop_tables()  # {} if missing
                _strategy_tables_loaded = True
    assert _strategy_table is not None  # set above; narrows Optional for callers
    return (
        _strategy_table,
        _hu_strategy_table,
        _depth_strategy_tables,
        _archetype_preflop_tables,
    )


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
    skill: str = DEFAULT_SKILL_TIER,
) -> TieredBotController:
    """Build a TieredBotController, optionally with the Layer 3 expression generator wired.

    Args:
        baseline: If True, instantiate BaselineSolverBot (pure solver, no personality
            distortion, no expression layer regardless of expression_enabled).
        skill: Named skill tier (see poker.strategy.skill_tiers). Default is the
            no-op ceiling tier, so behavior is unchanged until a weaker tier is
            assigned (Phase 4 roster work). Sets the bot's exploitation/river-bluff/
            stab-defense/overbet intensities post-construction.
    """
    llm_config = llm_config or {}
    # Memoized, process-shared immutable tables (see _get_strategy_tables).
    # Width-tier preflop charts (loose/station/tight) keyed by archetype are {}
    # if files missing → every archetype uses the base table; BaselineSolverBot
    # classifies as 'baseline' (not in the map) so it always uses the base.
    (
        strategy_table,
        hu_strategy_table,
        depth_strategy_tables,
        archetype_preflop_tables,
    ) = _get_strategy_tables()
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

    # Apply the skill tier post-construction (same seam the fish path uses for
    # _deviation_profile). No-op for the default ceiling tier — see skill_tiers.
    apply_skill_tier(controller, skill)

    # Baseline solver intentionally skips the expression layer — it's the
    # "pure GTO, no personality" option.
    if expression_enabled and not baseline:
        from core.llm.config import INGAME_LLM_TIMEOUT_SECONDS
        from core.llm.settings import get_default_model, get_default_provider

        # Resolve provider/model as a coherent pair from the default tier when
        # the config omits them. The old `provider='openai', model=None` default
        # was a landmine: OpenAIProvider's own fallback model is the Groq
        # `llama-3.1-8b-instant`, so an empty config sent a Groq model name to
        # OpenAI → guaranteed 404. Defaulting both together (groq + llama, the
        # cheap narration tier) keeps an under-specified config working instead.
        llm_client = LLMClient(
            provider=llm_config.get('provider') or get_default_provider(),
            model=llm_config.get('model') or get_default_model(),
            # Sharp-bot narration is pure flavor (the action is already
            # solver-locked). minimal reasoning keeps it snappy on a reasoning
            # DEFAULT model (gpt-5-mini → minimal effort; grok → non-reasoning
            # variant; llama → no-op).
            reasoning_effort="minimal",
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
    'casebot': 'case_based_v2',  # promoted: value-extraction beats v1 4-12x vs clones
    'regplus': 'reg_plus',  # disciplined value-extractor; beats casebot, robust vs bots
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
    stake_label: Optional[str] = None,
    default_strategy: Optional[str] = None,
    skill: str = DEFAULT_SKILL_TIER,
):
    """Construct the AI controller for a given ``bot_type``.

    This is the single, canonical dispatch over bot type — the union of the
    branch-sets that were previously copy-pasted across the new-game route,
    the cash sit route, and the restore/refill paths. It ONLY constructs and
    returns the controller object; all call-site bookkeeping (registering the
    controller, stamping ``bot_types`` / ``llm_configs``, ``assign_bot``,
    fish detection, memory wiring, logging) stays at the call site.

    Dispatch:
        - ``'fish'``        → build_fish_controller(...) — a tiered calling_station
                              (unified off the rule bot); ``stake_label`` forces the
                              weak_fish loadout at the $2 bottom tier
        - ``'sharp'``       → build_tiered_controller(...)
        - ``'baseline_solver'`` → build_tiered_controller(..., baseline=True)
        - ``'casebot'`` / ``'regplus'`` / ``'gto_lite'`` → RuleBotController with
                              the mapped rule strategy (case_based_v2 / reg_plus /
                              pot_odds_robot)
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
        fish_leak: Legacy kwarg, now IGNORED — the fish's tell rides on its
            persona ``spot_tendencies``. Kept on the signature for back-compat.
        stake_label: Forwarded to ``build_fish_controller`` on the ``'fish'``
            branch; selects the weak_fish loadout at the $2 bottom tier. When
            None, build_fish_controller reverse-looks-it-up from the big blind.
        debug_logging: Forwarded to ``build_tiered_controller`` (sharp /
            baseline_solver branches).
        skill: Named skill tier forwarded to the ``'sharp'`` branch (see
            poker.strategy.skill_tiers). Defaults to the no-op ceiling tier.
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
            stake_label=stake_label,
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
            skill=skill,
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
    stake_label: Optional[str] = None,
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

    At the WEAK_FISH_STAKES bottom tier ($2) the fish is forced to the `weak_fish`
    loadout (weak_station table + can't-fold + sticky/over_bluff + position_blind)
    — an explicit profile not reachable from anchors — so the $2 tables keep a
    strong bottom trickle; higher tiers stay the realistic calling_station.
    `stake_label` defaults to a reverse-lookup of the game's big_blind.
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
    from cash_mode.stakes_ladder import WEAK_FISH_STAKES, stake_label_for_big_blind

    if stake_label is None:
        gs = getattr(state_machine, 'game_state', None)
        # The engine stores the big blind as `current_ante`.
        big_blind = getattr(gs, 'current_ante', None) or getattr(gs, 'big_blind', None)
        stake_label = stake_label_for_big_blind(big_blind)
    if stake_label in WEAK_FISH_STAKES:
        # Force the weak_fish loadout (explicit profile, not anchor-reachable).
        # _table_archetype_key reverse-looks-up this profile → weak_station table.
        from poker.strategy.deviation_profiles import DEVIATION_PROFILES

        controller._deviation_profile = DEVIATION_PROFILES['weak_fish']
    return controller
