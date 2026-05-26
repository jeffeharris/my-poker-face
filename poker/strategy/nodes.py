"""
Decision node data types for the tiered bot strategy system.

PreflopNode: 169 canonical hands, keyed by scenario + position + hand.
PostflopNode: Two-axis hand classification (v2-ready stub for Phase 1).
"""

from dataclasses import dataclass, field
from typing import FrozenSet


@dataclass(frozen=True)
class PreflopNode:
    """Preflop decision point -- 169 canonical hands, not suit-exact."""

    hand: str  # 'AKs', 'AKo', 'AA', 'T9s', etc. (canonical hand)
    position: str  # 'UTG', 'HJ', 'CO', 'BTN', 'SB', 'BB'
    scenario: str  # 'rfi', 'vs_open', 'vs_3bet', 'vs_4bet'
    opener_position: str  # Position of original raiser ('' for RFI)

    @property
    def key(self) -> str:
        """Compact string key for storage and lookup."""
        return f"{self.scenario}|{self.position}|{self.opener_position}|{self.hand}"


@dataclass(frozen=True)
class PostflopNode:
    """Postflop decision point -- two-axis hand classification.

    Data model is v2-ready. Phase 1 uses check/fold fallback for all
    postflop decisions, so this node type is not actively used yet.
    """

    street: str  # 'flop', 'turn', 'river'
    position: str  # 'IP', 'OOP'
    pot_type: str  # 'SRP', '3BP'
    board_texture: str  # 'dry_high', 'monotone', 'wet_rainbow', etc.
    made_tier: str  # 'nuts', 'strong_made', 'medium_made', 'weak_made', 'air'
    draw_modifier: str  # 'no_draw', 'strong_draw', 'weak_draw', 'backdoor'
    facing_action: str  # 'unopened', 'facing_bet', 'facing_raise'
    spr_bucket: str  # 'high', 'medium', 'low'
    # Plan §1 additions: extended hand classification. NOT part of `key`,
    # so existing strategy-table lookups stay stable. Consumers that need
    # board-aware adjustments (§2 defense floor, diagnostics) read these
    # directly off the node.
    nut_status: str = 'unknown'
    danger_flags: FrozenSet[str] = field(default_factory=frozenset)

    @property
    def key(self) -> str:
        """Compact string key for storage and lookup.

        nut_status / danger_flags are intentionally excluded — the
        strategy table is keyed on the legacy axes only.
        """
        return (
            f"{self.street}|{self.position}|{self.pot_type}|{self.board_texture}"
            f"|{self.made_tier}|{self.draw_modifier}|{self.facing_action}|{self.spr_bucket}"
        )
