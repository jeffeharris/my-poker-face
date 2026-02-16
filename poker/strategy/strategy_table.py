"""
Strategy table: lookup and legal-action masking for solver-derived baselines.

Loads preflop strategy data from JSON, keyed by PreflopNode.key strings.
Provides exact lookup, fallback defaults, and action masking/renormalization
to bridge abstract strategy actions to the game engine's legal action set.
"""

import json
import os
from typing import Dict, List, Optional

from .nodes import PreflopNode
from .strategy_profile import StrategyProfile

# Abstract actions that correspond to a game-engine "raise" or "all_in"
_RAISE_ACTIONS = frozenset({
    'raise_2.5bb', 'raise_3bb', 'raise_3x', 'raise_4x', 'raise_2.2x',
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


class StrategyTable:
    """In-memory lookup table for preflop solver baselines."""

    def __init__(self, preflop_data: Dict[str, StrategyProfile]):
        """Initialize from parsed preflop data keyed by PreflopNode.key strings."""
        self._preflop: Dict[str, StrategyProfile] = dict(preflop_data)

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

    @property
    def size(self) -> int:
        """Number of entries in the preflop table."""
        return len(self._preflop)


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


def load_strategy_table(json_path: str = None) -> StrategyTable:
    """Load strategy table from JSON file.

    Default path: poker/strategy/data/preflop_100bb_6max.json
    """
    if json_path is None:
        json_path = os.path.join(
            os.path.dirname(__file__), 'data', 'preflop_100bb_6max.json',
        )
    with open(json_path) as f:
        data = json.load(f)
    preflop_data = _parse_json_to_preflop_data(data)
    return StrategyTable(preflop_data)
