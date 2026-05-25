#!/usr/bin/env python3
"""
Validate preflop tiered bot behavior via bot-vs-bot simulation.

Runs N hands of TieredBot vs TieredBot with different archetypes,
tracks VPIP, PFR, 3-bet% per player, and asserts directional correctness.

Usage:
    python experiments/validate_preflop.py --hands 1000
    python experiments/validate_preflop.py --hands 10000 --seed 42
"""

import argparse
import os
import random
import sys
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Dict

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from poker.bounded_options import EmotionalShift
from poker.psychology_model import PersonalityAnchors
from poker.strategy.deviation_profiles import DEVIATION_PROFILES
from poker.strategy.personality_modifier import modify_strategy
from poker.strategy.strategy_table import load_strategy_table

# ── Archetype definitions ────────────────────────────────────────────────

ARCHETYPES = {
    'Rock': {
        'profile': 'rock',
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
    'LAG': {
        'profile': 'lag',
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
    'Calling Station': {
        'profile': 'calling_station',
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
    'Maniac': {
        'profile': 'maniac',
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
    'Nit': {
        'profile': 'nit',
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
}

# All 169 canonical hands
PAIRS = [f'{r}{r}' for r in 'AKQJT98765432']
SUITED = [f'{r1}{r2}s' for i, r1 in enumerate('AKQJT98765432') for r2 in 'AKQJT98765432'[i + 1 :]]
OFFSUIT = [f'{r1}{r2}o' for i, r1 in enumerate('AKQJT98765432') for r2 in 'AKQJT98765432'[i + 1 :]]
ALL_HANDS = PAIRS + SUITED + OFFSUIT

# 6-max positions
POSITIONS = ['UTG', 'HJ', 'CO', 'BTN', 'SB', 'BB']


# ── Stats tracker ────────────────────────────────────────────────────────


@dataclass
class PlayerStats:
    """Track preflop stats for one player."""

    hands_dealt: int = 0
    vpip_count: int = 0  # Voluntarily put $ in pot (call or raise, not from blinds)
    pfr_count: int = 0  # Preflop raise
    three_bet_opportunities: int = 0
    three_bet_count: int = 0

    @property
    def vpip(self) -> float:
        return self.vpip_count / self.hands_dealt if self.hands_dealt > 0 else 0

    @property
    def pfr(self) -> float:
        return self.pfr_count / self.hands_dealt if self.hands_dealt > 0 else 0

    @property
    def three_bet_pct(self) -> float:
        return (
            self.three_bet_count / self.three_bet_opportunities
            if self.three_bet_opportunities > 0
            else 0
        )


# ── Mock game state builder ─────────────────────────────────────────────


def _build_mock_game_state(
    positions: Dict[str, str],
    current_player_idx: int,
    raises_this_round: int,
    highest_bet: int,
    big_blind: int = 100,
    player_bets: Dict[int, int] = None,
):
    """Build a minimal mock game state for preflop simulation."""
    num_players = len(positions)
    players = []
    for i in range(num_players):
        name = f'Player{i}'
        bet = (player_bets or {}).get(i, 0)
        players.append(
            SimpleNamespace(
                name=name,
                stack=10000,
                bet=bet,
                hand=(),
                is_human=False,
                is_folded=False,
                is_all_in=False,
            )
        )

    # Map position names to player names
    table_positions = {}
    pos_keys = [
        'button',
        'small_blind_player',
        'big_blind_player',
        'under_the_gun',
        'middle_position_1',
        'cutoff',
    ]
    for i, key in enumerate(pos_keys[:num_players]):
        table_positions[key] = players[i].name

    return SimpleNamespace(
        players=players,
        current_player_idx=current_player_idx,
        current_player=players[current_player_idx],
        current_ante=big_blind,
        highest_bet=highest_bet,
        last_raise_amount=big_blind,
        raises_this_round=raises_this_round,
        community_cards=(),
        pot={'total': 150},
        table_positions=table_positions,
        current_player_options=['fold', 'call', 'raise', 'all_in'],
    )


# ── Simulation ───────────────────────────────────────────────────────────


def simulate_preflop_hands(
    archetype_name: str,
    n_hands: int,
    strategy_table,
    seed: int = 42,
) -> PlayerStats:
    """Simulate N preflop decisions for a given archetype.

    For each hand:
    1. Pick a random canonical hand
    2. Pick a random position
    3. Randomly determine scenario (50% RFI, 35% vs_open, 15% vs_3bet)
    4. Look up base strategy, apply personality, sample action
    5. Track stats
    """
    config = ARCHETYPES[archetype_name]
    profile = DEVIATION_PROFILES[config['profile']]
    anchors = config['anchors']
    emotional_state = EmotionalShift(state='composed', severity='none', intensity=0.0)
    rng = random.Random(seed)

    stats = PlayerStats()

    for _ in range(n_hands):
        stats.hands_dealt += 1

        # Random hand and position
        hand = rng.choice(ALL_HANDS)
        position = rng.choice(POSITIONS)

        # Random scenario (50% RFI, 35% vs_open, 15% vs_3bet)
        # More vs_open scenarios allow VPIP vs PFR separation (call = VPIP but not PFR)
        scenario_roll = rng.random()
        if scenario_roll < 0.50:
            scenario = 'rfi'
            opener = ''
            raises = 0
            legal = ['fold', 'raise', 'all_in']
        elif scenario_roll < 0.85:
            scenario = 'vs_open'
            # Random opener from earlier positions
            pos_idx = POSITIONS.index(position)
            if pos_idx > 0:
                opener = rng.choice(POSITIONS[:pos_idx])
            else:
                opener = 'UTG'
            raises = 1
            legal = ['fold', 'call', 'raise', 'all_in']
        else:
            scenario = 'vs_3bet'
            # Opener is someone after us (we opened, they 3-bet)
            pos_idx = POSITIONS.index(position)
            if pos_idx < len(POSITIONS) - 1:
                opener = rng.choice(POSITIONS[pos_idx + 1 :])
            else:
                opener = 'BB'
            raises = 2
            legal = ['fold', 'call', 'raise', 'all_in']

        # BB can check in some scenarios
        if position == 'BB' and scenario == 'rfi':
            legal = ['check', 'raise', 'all_in']

        # Build node
        from poker.strategy.nodes import PreflopNode

        node = PreflopNode(
            hand=hand,
            position=position,
            scenario=scenario,
            opener_position=opener,
        )

        # Lookup base strategy
        base = strategy_table.lookup_with_fallback(node, legal)

        # Apply personality distortion
        modified = modify_strategy(
            base=base,
            legal_actions=legal,
            anchors=anchors,
            emotional_state=emotional_state,
            deviation_profile=profile,
        )

        # Sample action
        action = modified.sample_action(rng)

        # Track stats
        is_voluntary = action not in ('fold', 'check')
        is_raise = action not in ('fold', 'call', 'check')

        if is_voluntary:
            stats.vpip_count += 1
        if is_raise:
            stats.pfr_count += 1

        # 3-bet tracking
        if scenario == 'vs_open':
            stats.three_bet_opportunities += 1
            if is_raise:
                stats.three_bet_count += 1

    return stats


def run_validation(n_hands: int, seed: int):
    """Run full validation across all archetypes."""
    strategy_table = load_strategy_table()

    print(f"\nRunning preflop validation: {n_hands} hands per archetype, seed={seed}")
    print("=" * 80)

    results: Dict[str, PlayerStats] = {}
    for name in ARCHETYPES:
        stats = simulate_preflop_hands(name, n_hands, strategy_table, seed)
        results[name] = stats

    # Print results table
    print(f"\n{'Archetype':<20} {'VPIP%':>8} {'PFR%':>8} {'3-bet%':>8} {'PFR>VPIP':>10}")
    print("-" * 60)
    for name, stats in sorted(results.items(), key=lambda x: x[1].vpip):
        pfr_violation = 'FAIL' if stats.pfr > stats.vpip else 'ok'
        print(
            f"{name:<20} "
            f"{stats.vpip*100:>7.1f}% "
            f"{stats.pfr*100:>7.1f}% "
            f"{stats.three_bet_pct*100:>7.1f}% "
            f"{pfr_violation:>10}"
        )

    # ── Assertions ────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("VALIDATION CHECKS")
    print("=" * 80)

    all_pass = True

    # 1. Directional correctness: LAG VPIP > TAG VPIP > Rock VPIP
    def check(name, condition, msg):
        nonlocal all_pass
        status = 'PASS' if condition else 'FAIL'
        if not condition:
            all_pass = False
        print(f"  [{status}] {msg}")

    check(
        'vpip_order',
        results['LAG'].vpip > results['TAG'].vpip > results['Rock'].vpip,
        f"VPIP order: LAG ({results['LAG'].vpip:.3f}) > TAG ({results['TAG'].vpip:.3f}) > Rock ({results['Rock'].vpip:.3f})",
    )

    check(
        'pfr_order',
        results['LAG'].pfr > results['TAG'].pfr > results['Rock'].pfr,
        f"PFR order: LAG ({results['LAG'].pfr:.3f}) > TAG ({results['TAG'].pfr:.3f}) > Rock ({results['Rock'].pfr:.3f})",
    )

    check(
        'maniac_vpip',
        results['Maniac'].vpip > results['LAG'].vpip,
        f"Maniac VPIP ({results['Maniac'].vpip:.3f}) > LAG VPIP ({results['LAG'].vpip:.3f})",
    )

    check(
        'nit_vpip',
        results['Nit'].vpip < results['Rock'].vpip,
        f"Nit VPIP ({results['Nit'].vpip:.3f}) < Rock VPIP ({results['Rock'].vpip:.3f})",
    )

    # 2. No PFR > VPIP violations
    for name, stats in results.items():
        check(
            f'pfr_leq_vpip_{name}',
            stats.pfr <= stats.vpip + 0.001,  # tiny epsilon for float
            f"{name}: PFR ({stats.pfr:.3f}) <= VPIP ({stats.vpip:.3f})",
        )

    # 3. VPIP within guardrails [5%, 85%]
    for name, stats in results.items():
        check(
            f'vpip_range_{name}',
            0.05 <= stats.vpip <= 0.85,
            f"{name}: VPIP ({stats.vpip:.3f}) in [5%, 85%]",
        )

    print("\n" + ("ALL CHECKS PASSED" if all_pass else "SOME CHECKS FAILED"))
    return 0 if all_pass else 1


def main():
    parser = argparse.ArgumentParser(description='Validate preflop tiered bot behavior')
    parser.add_argument(
        '--hands', type=int, default=1000, help='Number of hands per archetype (default: 1000)'
    )
    parser.add_argument('--seed', type=int, default=42, help='Random seed (default: 42)')
    args = parser.parse_args()

    sys.exit(run_validation(args.hands, args.seed))


if __name__ == '__main__':
    main()
