#!/usr/bin/env python3
"""
Phase 7: Zone Benefits Demo

Tests the zone-based strategy guidance system.
Shows what guidance players receive based on their psychological zone.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from poker.player_psychology import (
    ZoneContext, ZoneStrategy, ZONE_STRATEGIES,
    select_zone_strategy, build_zone_guidance,
    get_zone_effects,
)
from poker.prompt_manager import PromptManager


def test_zone_strategy_selection():
    """Test that zone strategies are selected correctly."""
    print("=" * 60)
    print("Testing Zone Strategy Selection")
    print("=" * 60)

    # Test each zone with different contexts
    test_cases = [
        {
            'zone': 'poker_face',
            'strength': 0.7,
            'context': ZoneContext(),
            'description': 'Poker Face zone with no special context',
        },
        {
            'zone': 'poker_face',
            'strength': 0.7,
            'context': ZoneContext(equity_vs_ranges="Your equity: 58% vs their range"),
            'description': 'Poker Face zone with equity info',
        },
        {
            'zone': 'guarded',
            'strength': 0.6,
            'context': ZoneContext(),
            'description': 'Guarded zone',
        },
        {
            'zone': 'commanding',
            'strength': 0.5,
            'context': ZoneContext(opponent_stats="Villain: folds 40%, aggression 1.2"),
            'description': 'Commanding zone with opponent stats',
        },
        {
            'zone': 'aggro',
            'strength': 0.8,
            'context': ZoneContext(
                weak_player_note="Villain appears nervous",
                opponent_analysis="Villain folds to river bets 70%"
            ),
            'description': 'Aggro zone with weak player detected',
        },
        {
            'zone': 'poker_face',
            'strength': 0.15,  # Below min_strength (0.25)
            'context': ZoneContext(),
            'description': 'Low strength zone (should return no-requires strategy)',
        },
    ]

    for case in test_cases:
        print(f"\n{case['description']}:")
        print(f"  Zone: {case['zone']}, Strength: {case['strength']}")

        # Select strategy multiple times to see randomness
        strategies_selected = {}
        for _ in range(10):
            strategy = select_zone_strategy(
                case['zone'],
                case['strength'],
                case['context']
            )
            if strategy:
                name = strategy.name
                strategies_selected[name] = strategies_selected.get(name, 0) + 1

        if strategies_selected:
            print(f"  Strategies selected: {strategies_selected}")
        else:
            print("  No strategy selected (strength too low or missing context)")


def test_zone_guidance_generation():
    """Test full guidance generation for different emotional states."""
    print("\n" + "=" * 60)
    print("Testing Zone Guidance Generation")
    print("=" * 60)

    # Create a prompt manager
    prompt_manager = PromptManager()

    # Test different emotional states
    test_states = [
        {
            'confidence': 0.52,
            'composure': 0.72,
            'energy': 0.45,
            'description': 'Poker Face zone center (0.52, 0.72)',
        },
        {
            'confidence': 0.68,
            'composure': 0.48,
            'energy': 0.50,
            'description': 'Aggro zone center (0.68, 0.48)',
        },
        {
            'confidence': 0.28,
            'composure': 0.72,
            'energy': 0.50,
            'description': 'Guarded zone center (0.28, 0.72)',
        },
        {
            'confidence': 0.78,
            'composure': 0.78,
            'energy': 0.60,
            'description': 'Commanding zone center (0.78, 0.78)',
        },
        {
            'confidence': 0.50,
            'composure': 0.50,
            'energy': 0.50,
            'description': 'Neutral territory (0.50, 0.50)',
        },
        {
            'confidence': 0.65,
            'composure': 0.75,
            'energy': 0.50,
            'description': 'Between Poker Face and Commanding (blended)',
        },
    ]

    for state in test_states:
        print(f"\n{state['description']}:")

        # Get zone effects
        zone_effects = get_zone_effects(
            state['confidence'],
            state['composure'],
            state['energy']
        )
        effects_dict = zone_effects.to_dict()

        print(f"  Sweet spots: {effects_dict['sweet_spots']}")
        print(f"  Penalties: {effects_dict['penalties']}")
        print(f"  Primary zone: {zone_effects.primary_sweet_spot or 'neutral'}")

        # Build context with some data
        context = ZoneContext(
            opponent_stats="Villain: folds 35%, aggression 1.5",
            equity_vs_ranges="Your equity: 62% vs their range",
        )

        # Generate guidance
        guidance = build_zone_guidance(effects_dict, context, prompt_manager)

        if guidance:
            print(f"  Guidance:\n    {guidance.replace(chr(10), chr(10) + '    ')}")
        else:
            print("  No guidance (neutral territory or too weak)")


def test_zone_strategies_structure():
    """Verify ZONE_STRATEGIES dictionary is properly structured."""
    print("\n" + "=" * 60)
    print("Verifying Zone Strategies Structure")
    print("=" * 60)

    for zone_name, strategies in ZONE_STRATEGIES.items():
        print(f"\n{zone_name.upper()} zone ({len(strategies)} strategies):")
        total_weight = sum(s.weight for s in strategies)

        for strategy in strategies:
            print(f"  - {strategy.name}:")
            print(f"      template: {strategy.template_key}")
            print(f"      weight: {strategy.weight:.1f} ({strategy.weight/total_weight*100:.0f}%)")
            print(f"      requires: {strategy.requires or '(none)'}")
            print(f"      min_strength: {strategy.min_strength}")


def main():
    """Run all Phase 7 demo tests."""
    print("Phase 7: Zone Benefits System Demo")
    print("=" * 60)

    test_zone_strategies_structure()
    test_zone_strategy_selection()
    test_zone_guidance_generation()

    print("\n" + "=" * 60)
    print("Demo complete!")


if __name__ == '__main__':
    main()
