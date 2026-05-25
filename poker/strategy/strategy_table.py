"""
Strategy table: lookup and legal-action masking for solver-derived baselines.

Loads preflop and postflop strategy data from JSON, keyed by node.key strings.
Provides exact lookup, fallback defaults, and action masking/renormalization
to bridge abstract strategy actions to the game engine's legal action set.
"""

import json
import logging
import os
from dataclasses import replace
from typing import Dict, List, Optional

from .nodes import PreflopNode, PostflopNode
from .strategy_profile import StrategyProfile

logger = logging.getLogger(__name__)

# Abstract actions that correspond to a game-engine "raise" or "all_in"
_RAISE_ACTIONS = frozenset({
    # Preflop BB-relative and multiplier raises
    'raise_2.5bb', 'raise_3bb', 'raise_3x', 'raise_4x', 'raise_2.2x',
    # Postflop pot-relative bets and raises
    'bet_33', 'bet_67', 'bet_100', 'raise_67', 'raise_150',
})
_JAM_ACTION = 'jam'


def _is_action_legal(action: str, legal_actions: List[str]) -> bool:
    """Check whether an abstract strategy action is legal given engine actions."""
    if action in ('fold', 'check', 'call'):
        return action in legal_actions
    if action in _RAISE_ACTIONS:
        return 'raise' in legal_actions or 'all_in' in legal_actions
    if action == _JAM_ACTION:
        return 'all_in' in legal_actions
    # Fallback prefix match for any future bet_/raise_ actions
    if action.startswith(('bet_', 'raise_')):
        return 'raise' in legal_actions or 'all_in' in legal_actions
    # Unknown action — treat as illegal
    return False


def _mask_and_renormalize(
    profile: StrategyProfile, legal_actions: List[str],
) -> Optional[StrategyProfile]:
    """Remove illegal actions from a profile and renormalize.

    Returns None if no actions survive the mask (caller should fall back).
    """
    surviving = {
        action: prob
        for action, prob in profile.action_probabilities.items()
        if _is_action_legal(action, legal_actions)
    }
    total = sum(surviving.values())
    if total <= 0:
        return None
    renormalized = {action: prob / total for action, prob in surviving.items()}
    return StrategyProfile(action_probabilities=renormalized)


def _conservative_default(legal_actions: List[str]) -> StrategyProfile:
    """Fallback: fold unless check is legal (BB option)."""
    if 'check' in legal_actions:
        return StrategyProfile(action_probabilities={'check': 1.0})
    return StrategyProfile(action_probabilities={'fold': 1.0})


def _postflop_conservative_default(
    facing_action: str, legal_actions: List[str],
) -> StrategyProfile:
    """Context-aware conservative default for postflop (from arch doc)."""
    if facing_action == 'unopened':
        if 'check' in legal_actions:
            return StrategyProfile(action_probabilities={'check': 1.0})
    elif facing_action == 'facing_bet':
        probs = {}
        if 'fold' in legal_actions:
            probs['fold'] = 0.7
        if 'call' in legal_actions:
            probs['call'] = 0.3
        if probs:
            total = sum(probs.values())
            return StrategyProfile(
                action_probabilities={a: p / total for a, p in probs.items()}
            )
    elif facing_action == 'facing_raise':
        probs = {}
        if 'fold' in legal_actions:
            probs['fold'] = 0.8
        if 'call' in legal_actions:
            probs['call'] = 0.2
        if probs:
            total = sum(probs.values())
            return StrategyProfile(
                action_probabilities={a: p / total for a, p in probs.items()}
            )
    # Ultimate fallback
    return _conservative_default(legal_actions)


# Texture neighbor fallback map (deterministic, one-directional)
_TEXTURE_NEIGHBOR = {
    'dry_high': 'dry_low_static',
    'dry_low_static': 'dry_high',
    'monotone': 'two_tone_connected',
    'two_tone_broadway': 'wet_rainbow',
    'two_tone_connected': 'wet_rainbow',
    'wet_rainbow': 'two_tone_connected',
}


class StrategyTable:
    """In-memory lookup table for preflop and postflop solver baselines."""

    def __init__(
        self,
        preflop_data: Dict[str, StrategyProfile],
        postflop_data: Optional[Dict[str, StrategyProfile]] = None,
    ):
        """Initialize from parsed data keyed by node.key strings."""
        self._preflop: Dict[str, StrategyProfile] = dict(preflop_data)
        self._postflop: Dict[str, StrategyProfile] = dict(postflop_data or {})

    def lookup_preflop(self, node: PreflopNode) -> Optional[StrategyProfile]:
        """Look up base strategy for a preflop node. Returns None if not found."""
        return self._preflop.get(node.key)

    def lookup_with_fallback(
        self, node: PreflopNode, legal_actions: List[str],
    ) -> StrategyProfile:
        """Look up strategy with legal action masking and fallback.

        1. Try exact lookup.
        2. If found: mask illegal actions, renormalize.
        3. If not found or all masked out: return conservative default.
        """
        profile = self.lookup_preflop(node)
        if profile is not None:
            masked = _mask_and_renormalize(profile, legal_actions)
            if masked is not None:
                return masked
        return _conservative_default(legal_actions)

    # ── Postflop lookup ─────────────────────────────────────────────

    def lookup_postflop(self, node: PostflopNode) -> Optional[StrategyProfile]:
        """Look up base strategy for a postflop node. Returns None if not found."""
        return self._postflop.get(node.key)

    def lookup_postflop_with_fallback(
        self, node: PostflopNode, legal_actions: List[str],
    ) -> StrategyProfile:
        """Look up postflop strategy with SPR + texture-neighbor fallback.

        Fallback ladder:
        1. Exact key lookup
        2. SPR fallback: the chart is populated only at spr_bucket='high', so
           a low/medium-SPR spot (short stack) retries the same node at
           spr='high'. Without this, short-stack postflop play falls all the
           way to the passive conservative default (check-100% unopened /
           fold-70% facing a bet) — the diagnosed low-SPR passivity leak.
           Commitment for genuinely-short SPR is layered on downstream
           (postflop_commit); here we just recover real strategy.
        3. Texture neighbor lookup (swap board_texture, keep everything else)
        4. Context-aware conservative default
        """
        # 1. Exact lookup
        profile = self._postflop.get(node.key)
        if profile is not None:
            masked = _mask_and_renormalize(profile, legal_actions)
            if masked is not None:
                return masked

        # 2. SPR fallback → high (the only populated bucket). All further
        # fallbacks operate on this high-SPR node.
        lookup_node = node
        if node.spr_bucket != 'high':
            lookup_node = replace(node, spr_bucket='high')
            profile = self._postflop.get(lookup_node.key)
            if profile is not None:
                masked = _mask_and_renormalize(profile, legal_actions)
                if masked is not None:
                    logger.debug(
                        f"Postflop SPR fallback: {node.spr_bucket} → high "
                        f"for {node.key}"
                    )
                    return masked

        # 3. Texture neighbor fallback
        neighbor_texture = _TEXTURE_NEIGHBOR.get(lookup_node.board_texture)
        if neighbor_texture:
            neighbor_node = replace(lookup_node, board_texture=neighbor_texture)
            profile = self._postflop.get(neighbor_node.key)
            if profile is not None:
                masked = _mask_and_renormalize(profile, legal_actions)
                if masked is not None:
                    logger.debug(
                        f"Postflop fallback: {node.board_texture} → "
                        f"{neighbor_texture} for {node.key}"
                    )
                    return masked

        # 4. Conservative default
        logger.debug(f"Postflop conservative default for {node.key}")
        return _postflop_conservative_default(node.facing_action, legal_actions)

    @property
    def size(self) -> int:
        """Number of entries in the preflop table."""
        return len(self._preflop)

    @property
    def postflop_size(self) -> int:
        """Number of entries in the postflop table."""
        return len(self._postflop)


def _parse_position_matchup(matchup: str):
    """Parse 'BB_vs_UTG' into (position='BB', opener_position='UTG')."""
    parts = matchup.split('_vs_')
    if len(parts) != 2:
        raise ValueError(f"Invalid matchup format: {matchup!r} (expected 'POS_vs_POS')")
    return parts[0], parts[1]


def _parse_json_to_preflop_data(data: dict) -> Dict[str, StrategyProfile]:
    """Parse the strategy JSON structure into PreflopNode.key -> StrategyProfile.

    Expected JSON structure:
      {
        "rfi": { position: { hand: {action: prob} } },
        "vs_open": { "BB_vs_UTG": { hand: {action: prob} } },
        "vs_3bet": { "UTG_vs_HJ": { hand: {action: prob} } },
        "vs_4bet": { "HJ_vs_UTG": { hand: {action: prob} } },
      }
    """
    result: Dict[str, StrategyProfile] = {}

    # RFI: scenario=rfi, opener_position=''
    for position, hands in data.get('rfi', {}).items():
        for hand, actions in hands.items():
            node = PreflopNode(
                hand=hand, position=position,
                scenario='rfi', opener_position='',
            )
            result[node.key] = StrategyProfile(action_probabilities=dict(actions))

    # vs_open, vs_3bet, vs_4bet: parse matchup for position + opener
    for scenario in ('vs_open', 'vs_3bet', 'vs_4bet'):
        for matchup, hands in data.get(scenario, {}).items():
            position, opener_position = _parse_position_matchup(matchup)
            for hand, actions in hands.items():
                node = PreflopNode(
                    hand=hand, position=position,
                    scenario=scenario, opener_position=opener_position,
                )
                result[node.key] = StrategyProfile(action_probabilities=dict(actions))

    return result


def _parse_postflop_json(data: dict) -> Dict[str, StrategyProfile]:
    """Parse postflop strategy JSON into PostflopNode.key -> StrategyProfile.

    Expected JSON: flat dict of PostflopNode.key -> {action: probability}.
    """
    result: Dict[str, StrategyProfile] = {}
    for key, actions in data.items():
        result[key] = StrategyProfile(action_probabilities=dict(actions))
    return result


def load_strategy_table(
    json_path: str = None,
    postflop_path: str = None,
) -> StrategyTable:
    """Load strategy table from JSON files.

    Default paths:
    - Preflop: poker/strategy/data/preflop_100bb_6max.json
    - Postflop: poker/strategy/data/postflop_strategies.json
    """
    data_dir = os.path.join(os.path.dirname(__file__), 'data')

    if json_path is None:
        json_path = os.path.join(data_dir, 'preflop_100bb_6max.json')
    with open(json_path) as f:
        preflop_raw = json.load(f)
    preflop_data = _parse_json_to_preflop_data(preflop_raw)

    # Load postflop data (optional — file may not exist yet)
    postflop_data: Dict[str, StrategyProfile] = {}
    if postflop_path is None:
        postflop_path = os.path.join(data_dir, 'postflop_strategies.json')
    if os.path.exists(postflop_path):
        with open(postflop_path) as f:
            postflop_raw = json.load(f)
        postflop_data = _parse_postflop_json(postflop_raw)

    return StrategyTable(preflop_data, postflop_data)


def load_hu_strategy_table(json_path: str = None) -> Optional[StrategyTable]:
    """Load the heads-up preflop strategy table.

    Default path: poker/strategy/data/preflop_100bb_hu.json. Returns None
    if the file does not exist (callers fall back to the 6-max table).

    The HU table reuses the same JSON schema as the 6-max table; only the
    populated scenarios differ (rfi.SB, vs_open.BB_vs_SB, vs_3bet.SB_vs_BB,
    vs_4bet.BB_vs_SB). No postflop data — HU shares the 6-max postflop
    table for now (Phase 7 scope is preflop only).

    See poker/strategy/data/hu_preflop_chart_README.md for the spec.
    """
    if json_path is None:
        data_dir = os.path.join(os.path.dirname(__file__), 'data')
        json_path = os.path.join(data_dir, 'preflop_100bb_hu.json')

    if not os.path.exists(json_path):
        return None

    with open(json_path) as f:
        preflop_raw = json.load(f)
    preflop_data = _parse_json_to_preflop_data(preflop_raw)
    return StrategyTable(preflop_data, postflop_data={})


# Depth buckets the bot adjusts its preflop game across. 100 is the base
# table (`preflop_100bb_6max.json`, loaded separately as the deep-stack
# default); 50/25 are the hand-authored shallow charts. Below ~15bb the HU
# push/fold chart + short_stack heuristic take over, so no bucket there.
DEPTH_CHART_BUCKETS = (100, 50, 25)


def load_depth_strategy_tables() -> Dict[int, "StrategyTable"]:
    """Load the shallow 6-max preflop charts keyed by depth bucket.

    Returns a dict like ``{50: StrategyTable, 25: StrategyTable}`` for every
    ``preflop_<bb>bb_6max.json`` that exists (generated by
    ``generate_depth_charts.py``). The 100bb bucket is intentionally absent —
    callers map it to the base ``strategy_table`` they already hold. Returns
    an empty dict when no shallow charts are present (callers then use the
    base table at every depth — the pre-depth-aware behavior).

    See ``poker/strategy/data/depth_charts_README.md`` for the chart rules.
    """
    data_dir = os.path.join(os.path.dirname(__file__), 'data')
    tables: Dict[int, StrategyTable] = {}
    for depth in DEPTH_CHART_BUCKETS:
        if depth == 100:
            continue  # base table, supplied by the caller
        path = os.path.join(data_dir, f'preflop_{depth}bb_6max.json')
        if not os.path.exists(path):
            continue
        with open(path) as f:
            preflop_raw = json.load(f)
        tables[depth] = StrategyTable(
            _parse_json_to_preflop_data(preflop_raw), postflop_data={}
        )
    return tables


def nearest_depth_bucket(effective_stack_bb: float, buckets=DEPTH_CHART_BUCKETS) -> int:
    """Snap an effective stack to the nearest published depth bucket.

    Mirrors ``push_fold._nearest_bucket`` semantics: clamp above the top
    bucket to the top, below the bottom bucket to the bottom, otherwise pick
    the closest (ties prefer the deeper/larger bucket — safer to flat than to
    over-jam when exactly between depths).
    """
    hi, lo = max(buckets), min(buckets)
    if effective_stack_bb >= hi:
        return hi
    if effective_stack_bb <= lo:
        return lo
    return min(buckets, key=lambda b: (abs(b - effective_stack_bb), -b))
