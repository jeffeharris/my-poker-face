#!/usr/bin/env python3
"""
Validate postflop tiered bot behavior via simulated decisions.

Simulates postflop decision points across all 6 archetypes, tracking
c-bet%, check-raise%, aggression factor, and fold-to-bet% per archetype.
Asserts directional correctness (LAG > TAG > Rock, etc).

Usage:
    python -m experiments.validate_postflop --hands 10000
    python -m experiments.validate_postflop --hands 1000 --verbose
"""

import argparse
import os
import random
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from poker.bounded_options import EmotionalShift
from poker.psychology_model import PersonalityAnchors
from poker.strategy.deviation_profiles import DEVIATION_PROFILES
from poker.strategy.personality_modifier import (
    apply_river_bluff_guardrail,
    categorize_action,
    modify_strategy,
)
from poker.strategy.strategy_table import load_strategy_table

# ── Archetype definitions (same as preflop validation) ──────────────────

ARCHETYPES = {
    'Nit': {
        'profile': 'nit',
        'arch_name': 'nit',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.15,
            baseline_looseness=0.15,
            ego=0.2,
            poise=0.9,
            expressiveness=0.2,
            risk_identity=0.2,
            adaptation_bias=0.3,
            baseline_energy=0.3,
            recovery_rate=0.2,
        ),
    },
    'Rock': {
        'profile': 'rock',
        'arch_name': 'rock',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.3,
            baseline_looseness=0.25,
            ego=0.3,
            poise=0.8,
            expressiveness=0.3,
            risk_identity=0.3,
            adaptation_bias=0.3,
            baseline_energy=0.4,
            recovery_rate=0.2,
        ),
    },
    'TAG': {
        'profile': 'tag',
        'arch_name': 'tag',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.7,
            baseline_looseness=0.35,
            ego=0.5,
            poise=0.7,
            expressiveness=0.4,
            risk_identity=0.5,
            adaptation_bias=0.5,
            baseline_energy=0.5,
            recovery_rate=0.15,
        ),
    },
    'Calling Station': {
        'profile': 'calling_station',
        'arch_name': 'calling_station',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.3,
            baseline_looseness=0.75,
            ego=0.4,
            poise=0.5,
            expressiveness=0.5,
            risk_identity=0.4,
            adaptation_bias=0.3,
            baseline_energy=0.5,
            recovery_rate=0.15,
        ),
    },
    'LAG': {
        'profile': 'lag',
        'arch_name': 'lag',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.8,
            baseline_looseness=0.7,
            ego=0.6,
            poise=0.5,
            expressiveness=0.6,
            risk_identity=0.6,
            adaptation_bias=0.5,
            baseline_energy=0.7,
            recovery_rate=0.15,
        ),
    },
    'Maniac': {
        'profile': 'maniac',
        'arch_name': 'maniac',
        'anchors': PersonalityAnchors(
            baseline_aggression=0.9,
            baseline_looseness=0.85,
            ego=0.7,
            poise=0.3,
            expressiveness=0.8,
            risk_identity=0.8,
            adaptation_bias=0.3,
            baseline_energy=0.8,
            recovery_rate=0.1,
        ),
    },
}

TEXTURES = [
    'dry_high',
    'dry_low_static',
    'monotone',
    'two_tone_broadway',
    'two_tone_connected',
    'wet_rainbow',
]
POSITIONS = ['IP', 'OOP']
STREETS = ['flop', 'turn', 'river']
HAND_CLASSES = ['nuts', 'strong_made', 'medium_made', 'weak_made', 'air_strong_draw', 'air_no_draw']

# Representative (made_tier, draw_modifier) for each simplified class
CLASS_REPRESENTATIVES = {
    'nuts': ('nuts', 'no_draw'),
    'strong_made': ('strong_made', 'no_draw'),
    'medium_made': ('medium_made', 'no_draw'),
    'weak_made': ('weak_made', 'no_draw'),
    'air_strong_draw': ('air', 'strong_draw'),
    'air_no_draw': ('air', 'no_draw'),
}


# ── Stats tracker ────────────────────────────────────────────────────────


@dataclass
class PostflopStats:
    """Track postflop stats for one archetype."""

    decisions: int = 0
    # Unopened decisions
    unopened_decisions: int = 0
    bet_count: int = 0  # bet_33 + bet_67 + bet_100
    check_count: int = 0
    # Facing bet decisions
    facing_bet_decisions: int = 0
    fold_to_bet: int = 0
    call_bet: int = 0
    raise_bet: int = 0  # raise_67 + raise_150 + jam
    # River bluff guardrail
    river_guardrail_fires: int = 0
    river_decisions: int = 0
    # Per-texture tracking
    cbet_by_texture: Dict[str, List[int]] = field(
        default_factory=lambda: defaultdict(lambda: [0, 0])  # [bet, total]
    )

    @property
    def cbet_pct(self) -> float:
        return self.bet_count / self.unopened_decisions if self.unopened_decisions > 0 else 0

    @property
    def fold_to_bet_pct(self) -> float:
        return self.fold_to_bet / self.facing_bet_decisions if self.facing_bet_decisions > 0 else 0

    @property
    def check_raise_pct(self) -> float:
        return self.raise_bet / self.facing_bet_decisions if self.facing_bet_decisions > 0 else 0

    @property
    def aggression_factor(self) -> float:
        """(bets + raises) / calls."""
        aggressive = self.bet_count + self.raise_bet
        passive = self.call_bet
        return aggressive / passive if passive > 0 else float('inf')

    @property
    def guardrail_rate(self) -> float:
        return self.river_guardrail_fires / self.river_decisions if self.river_decisions > 0 else 0


# ── Simulation ───────────────────────────────────────────────────────────


def simulate_postflop_decisions(
    archetype_name: str,
    n_hands: int,
    strategy_table,
    seed: int = 42,
) -> PostflopStats:
    """Simulate N postflop decisions for a given archetype."""
    config = ARCHETYPES[archetype_name]
    profile = DEVIATION_PROFILES[config['profile']]
    anchors = config['anchors']
    arch_name = config['arch_name']
    emotional_state = EmotionalShift(state='composed', severity='none', intensity=0.0)
    rng = random.Random(seed)

    stats = PostflopStats()

    for _ in range(n_hands):
        stats.decisions += 1

        # Random parameters
        street = rng.choice(STREETS)
        position = rng.choice(POSITIONS)
        texture = rng.choice(TEXTURES)
        hand_class = rng.choice(HAND_CLASSES)
        made_tier, draw_modifier = CLASS_REPRESENTATIVES[hand_class]

        # 60% unopened, 35% facing_bet, 5% facing_raise
        facing_roll = rng.random()
        if facing_roll < 0.60:
            facing_action = 'unopened'
            legal = ['check', 'raise', 'all_in']
        elif facing_roll < 0.95:
            facing_action = 'facing_bet'
            legal = ['fold', 'call', 'raise', 'all_in']
        else:
            facing_action = 'facing_raise'
            legal = ['fold', 'call', 'all_in']

        # Build PostflopNode directly
        from poker.strategy.nodes import PostflopNode

        node = PostflopNode(
            street=street,
            position=position,
            pot_type='SRP',
            board_texture=texture,
            made_tier=made_tier,
            draw_modifier=draw_modifier,
            facing_action=facing_action,
            spr_bucket='high',
        )

        # Lookup base strategy
        base = strategy_table.lookup_postflop_with_fallback(node, legal)

        # Apply personality distortion
        modified = modify_strategy(
            base=base,
            legal_actions=legal,
            anchors=anchors,
            emotional_state=emotional_state,
            deviation_profile=profile,
        )

        # River bluff guardrail
        if street == 'river':
            stats.river_decisions += 1
            pre_guardrail = modified
            modified = apply_river_bluff_guardrail(modified, hand_class, arch_name)
            if modified.action_probabilities != pre_guardrail.action_probabilities:
                stats.river_guardrail_fires += 1

        # Sample action
        action = modified.sample_action(rng)
        cat = categorize_action(action)

        # Track stats
        if facing_action == 'unopened':
            stats.unopened_decisions += 1
            if cat == 'aggressive':
                stats.bet_count += 1
                stats.cbet_by_texture[texture][0] += 1
            else:
                stats.check_count += 1
            stats.cbet_by_texture[texture][1] += 1

        elif facing_action == 'facing_bet':
            stats.facing_bet_decisions += 1
            if action == 'fold':
                stats.fold_to_bet += 1
            elif action == 'call':
                stats.call_bet += 1
            elif cat == 'aggressive':
                stats.raise_bet += 1

    return stats


def run_validation(n_hands: int, seed: int, verbose: bool = False):
    """Run full postflop validation across all archetypes."""
    strategy_table = load_strategy_table()

    print(f"\nPostflop validation: {n_hands} decisions per archetype, seed={seed}")
    print(f"Postflop strategy table: {strategy_table.postflop_size} entries loaded")
    print("=" * 90)

    results: Dict[str, PostflopStats] = {}
    for name in ARCHETYPES:
        stats = simulate_postflop_decisions(name, n_hands, strategy_table, seed)
        results[name] = stats

    # Print results table
    print(
        f"\n{'Archetype':<18} {'C-bet%':>8} {'FoldBet%':>10} {'ChkRaise%':>10} "
        f"{'AggFactor':>10} {'RvrGuard%':>10}"
    )
    print("-" * 70)
    for name in ['Nit', 'Rock', 'TAG', 'Calling Station', 'LAG', 'Maniac']:
        s = results[name]
        af = f"{s.aggression_factor:.2f}" if s.aggression_factor != float('inf') else "inf"
        print(
            f"{name:<18} "
            f"{s.cbet_pct*100:>7.1f}% "
            f"{s.fold_to_bet_pct*100:>9.1f}% "
            f"{s.check_raise_pct*100:>9.1f}% "
            f"{af:>10} "
            f"{s.guardrail_rate*100:>9.1f}%"
        )

    # Per-texture breakdown
    if verbose:
        print(f"\n{'='*90}")
        print("PER-TEXTURE C-BET RATES (unopened)")
        print(f"{'='*90}")
        print(f"\n{'Archetype':<18}", end='')
        for t in TEXTURES:
            print(f" {t[:12]:>12}", end='')
        print()
        print("-" * (18 + 13 * len(TEXTURES)))

        for name in ['Nit', 'Rock', 'TAG', 'Calling Station', 'LAG', 'Maniac']:
            s = results[name]
            print(f"{name:<18}", end='')
            for t in TEXTURES:
                bets, total = s.cbet_by_texture[t]
                pct = bets / total * 100 if total > 0 else 0
                print(f" {pct:>11.1f}%", end='')
            print()

    # ── Assertions ────────────────────────────────────────────────────
    print(f"\n{'='*90}")
    print("VALIDATION CHECKS")
    print("=" * 90)

    all_pass = True

    def check(condition, msg):
        nonlocal all_pass
        status = 'PASS' if condition else 'FAIL'
        if not condition:
            all_pass = False
        print(f"  [{status}] {msg}")

    # 1. C-bet ordering: LAG > TAG > Rock
    check(
        results['LAG'].cbet_pct > results['TAG'].cbet_pct > results['Rock'].cbet_pct,
        f"C-bet order: LAG ({results['LAG'].cbet_pct:.3f}) > "
        f"TAG ({results['TAG'].cbet_pct:.3f}) > Rock ({results['Rock'].cbet_pct:.3f})",
    )

    # 2. Maniac most aggressive
    check(
        results['Maniac'].cbet_pct > results['LAG'].cbet_pct,
        f"Maniac c-bet ({results['Maniac'].cbet_pct:.3f}) > "
        f"LAG c-bet ({results['LAG'].cbet_pct:.3f})",
    )

    # 3. Calling Station calls more, raises less than TAG
    check(
        results['Calling Station'].fold_to_bet_pct < results['TAG'].fold_to_bet_pct,
        f"Calling Station fold% ({results['Calling Station'].fold_to_bet_pct:.3f}) < "
        f"TAG fold% ({results['TAG'].fold_to_bet_pct:.3f})",
    )

    # 4. Nit folds most when facing bet
    check(
        results['Nit'].fold_to_bet_pct > results['Rock'].fold_to_bet_pct,
        f"Nit fold-to-bet ({results['Nit'].fold_to_bet_pct:.3f}) > "
        f"Rock fold-to-bet ({results['Rock'].fold_to_bet_pct:.3f})",
    )

    # 5. No degenerate behavior: c-bet between 10% and 90%
    for name, s in results.items():
        check(0.10 <= s.cbet_pct <= 0.90, f"{name}: c-bet ({s.cbet_pct:.3f}) in [10%, 90%]")

    # 6. No degenerate fold-to-bet: between 5% and 90%
    for name, s in results.items():
        check(
            0.05 <= s.fold_to_bet_pct <= 0.90,
            f"{name}: fold-to-bet ({s.fold_to_bet_pct:.3f}) in [5%, 90%]",
        )

    # 7. River guardrail should fire sometimes but not constantly
    for name, s in results.items():
        check(
            s.guardrail_rate < 0.50, f"{name}: river guardrail rate ({s.guardrail_rate:.3f}) < 50%"
        )

    # 8. Aggression ordering: Maniac > LAG > TAG > Rock
    # Use c-bet as proxy since aggression_factor can be inf
    check(
        results['Maniac'].cbet_pct >= results['LAG'].cbet_pct >= results['TAG'].cbet_pct,
        "Aggression order: Maniac >= LAG >= TAG (c-bet proxy)",
    )

    print(f"\n{'ALL CHECKS PASSED' if all_pass else 'SOME CHECKS FAILED'}")
    return 0 if all_pass else 1


def main():
    parser = argparse.ArgumentParser(description='Validate postflop tiered bot behavior')
    parser.add_argument(
        '--hands',
        type=int,
        default=10000,
        help='Number of decisions per archetype (default: 10000)',
    )
    parser.add_argument('--seed', type=int, default=42, help='Random seed (default: 42)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show per-texture breakdown')
    args = parser.parse_args()

    sys.exit(run_validation(args.hands, args.seed, args.verbose))


if __name__ == '__main__':
    main()
