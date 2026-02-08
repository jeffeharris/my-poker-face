"""
Phase 8: Tone & Strategy Framing Demo

Demonstrates energy-variant zone templates and energy labels in zone headers.

Run with: python -m experiments.phase8_tone_framing_demo
"""

import sys
sys.path.insert(0, '.')

from poker.player_psychology import (
    build_zone_guidance, get_zone_effects, ZoneContext,
    ENERGY_MANIFESTATION_LABELS, PlayerPsychology
)
from poker.prompt_manager import PromptManager


def print_header(text):
    """Print a formatted section header."""
    print()
    print("=" * 70)
    print(text)
    print("=" * 70)


def demo_energy_manifestation_labels():
    """Show the energy labels for each zone."""
    print_header("ENERGY MANIFESTATION LABELS")
    print()
    print("Each zone has different energy labels reflecting its character:\n")

    for zone, labels in ENERGY_MANIFESTATION_LABELS.items():
        print(f"  {zone.upper()}:")
        print(f"    Low energy:  {labels['low_energy'] or '(none)'}")
        print(f"    Balanced:    {labels['balanced'] or '(none - clean header)'}")
        print(f"    High energy: {labels['high_energy']}")
        print()


def demo_zone_templates():
    """Test zone guidance with different energy levels."""
    print_header("ZONE GUIDANCE WITH ENERGY VARIANTS")

    prompt_manager = PromptManager()

    # Test cases: (confidence, composure, energy, expected_zone, expected_label)
    test_cases = [
        # Poker Face zone
        (0.52, 0.72, 0.2, 'poker_face', 'Measured'),
        (0.52, 0.72, 0.5, 'poker_face', ''),
        (0.52, 0.72, 0.8, 'poker_face', 'Running hot'),

        # Guarded zone
        (0.28, 0.72, 0.2, 'guarded', 'Measured'),
        (0.28, 0.72, 0.8, 'guarded', 'Alert'),

        # Commanding zone
        (0.78, 0.78, 0.2, 'commanding', 'Composed'),
        (0.78, 0.78, 0.8, 'commanding', 'Dominant'),

        # Aggro zone
        (0.68, 0.48, 0.2, 'aggro', 'Watchful'),
        (0.68, 0.48, 0.8, 'aggro', 'Hunting'),
    ]

    context = ZoneContext(
        opponent_stats="Villain: folds 35% to river bets",
        opponent_analysis="Villain folds to river bets 70%",
        weak_player_note="Villain appears nervous",
        equity_vs_ranges="Your equity: 62% vs their range",
    )

    for conf, comp, energy, expected_zone, expected_label in test_cases:
        print(f"\n--- {expected_zone.upper()} (conf={conf}, comp={comp}, energy={energy}) ---")
        print(f"Expected label: '{expected_label or '(none)'}'\n")

        zone_effects = get_zone_effects(conf, comp, energy)
        guidance = build_zone_guidance(zone_effects.to_dict(), context, prompt_manager)

        if guidance:
            # Print first 3 lines or less
            lines = guidance.strip().split('\n')
            for line in lines[:3]:
                print(f"  {line}")
            if len(lines) > 3:
                print("  ...")
        else:
            print("  (no guidance generated)")

        # Verify label is in header
        if expected_label:
            if expected_label in guidance:
                print(f"\n  [OK] Label '{expected_label}' found in header")
            else:
                print(f"\n  [WARN] Label '{expected_label}' NOT found in header!")
        else:
            print(f"\n  [OK] No energy label (balanced)")


def demo_blended_zones():
    """Test zone blending with energy labels."""
    print_header("BLENDED ZONES WITH ENERGY")

    prompt_manager = PromptManager()

    # Position near boundary between Poker Face and Commanding
    conf = 0.65
    comp = 0.75

    context = ZoneContext(
        opponent_stats="Villain: folds 35% to river bets",
        equity_vs_ranges="Your equity: 62% vs their range",
    )

    for energy in [0.2, 0.5, 0.8]:
        print(f"\n--- Blended zone (conf={conf}, comp={comp}, energy={energy}) ---\n")

        zone_effects = get_zone_effects(conf, comp, energy)
        print(f"  Sweet spots: {zone_effects.sweet_spots}")
        print(f"  Primary: {zone_effects.primary_sweet_spot}")
        print(f"  Manifestation: {zone_effects.manifestation}")
        print()

        guidance = build_zone_guidance(zone_effects.to_dict(), context, prompt_manager)
        if guidance:
            lines = guidance.strip().split('\n')
            for line in lines[:2]:
                print(f"  {line}")
        else:
            print("  (no guidance)")


def demo_penalty_energy_flavor():
    """Test penalty bad advice with energy flavor."""
    print_header("PENALTY ZONE BAD ADVICE WITH ENERGY FLAVOR")

    # Create a player to test penalty strategy
    config = {
        'anchors': {
            'baseline_aggression': 0.5,
            'baseline_looseness': 0.4,
            'ego': 0.5,
            'poise': 0.3,
            'expressiveness': 0.5,
            'risk_identity': 0.5,
            'adaptation_bias': 0.5,
            'baseline_energy': 0.5,
            'recovery_rate': 0.15,
        }
    }

    # Test cases: (composure, energy, description)
    test_cases = [
        (0.15, 0.2, "Tilted + Low energy"),
        (0.15, 0.5, "Tilted + Balanced energy"),
        (0.15, 0.8, "Tilted + High energy"),
    ]

    for comp, energy, description in test_cases:
        print(f"\n--- {description} (composure={comp}, energy={energy}) ---\n")

        psych = PlayerPsychology.from_personality_config('TestPlayer', config)
        psych.axes = psych.axes.update(composure=comp, energy=energy)

        zone_effects = get_zone_effects(psych.confidence, comp, energy)
        print(f"  Penalties: {zone_effects.penalties}")
        print(f"  Manifestation: {zone_effects.manifestation}")

        # Apply penalty strategy
        test_prompt = "What is your move?"
        modified = psych._add_penalty_strategy(test_prompt, zone_effects)

        # Extract the mindset part
        if "[Current mindset:" in modified:
            mindset = modified.split("[Current mindset:")[1].split("]")[0].strip()
            print(f"  Mindset: {mindset}")
        else:
            print("  (no mindset advice)")


def demo_all_templates_exist():
    """Verify all expected templates exist in decision.yaml."""
    print_header("TEMPLATE VERIFICATION")

    prompt_manager = PromptManager()
    template = prompt_manager.get_template('decision')

    # Expected templates
    base_templates = [
        'zone_poker_face_gto', 'zone_poker_face_balance', 'zone_poker_face_equity',
        'zone_guarded_trap', 'zone_guarded_patience', 'zone_guarded_control',
        'zone_commanding_value', 'zone_commanding_pressure', 'zone_commanding_initiative',
        'zone_aggro_awareness', 'zone_aggro_analyze', 'zone_aggro_target',
    ]

    print()
    print("Checking base templates and energy variants:")
    print()

    all_ok = True
    for base in base_templates:
        variants = [base, f"{base}_low", f"{base}_high"]
        for variant in variants:
            exists = variant in template.sections
            status = "OK" if exists else "MISSING"
            if not exists:
                all_ok = False
            print(f"  [{status}] {variant}")

    print()
    if all_ok:
        print("All 36 templates (12 base + 24 variants) present!")
    else:
        print("WARNING: Some templates are missing!")


def main():
    print("\n" + "=" * 70)
    print("PHASE 8: TONE & STRATEGY FRAMING DEMO")
    print("=" * 70)

    demo_energy_manifestation_labels()
    demo_all_templates_exist()
    demo_zone_templates()
    demo_blended_zones()
    demo_penalty_energy_flavor()

    print()
    print("=" * 70)
    print("DEMO COMPLETE")
    print("=" * 70)


if __name__ == '__main__':
    main()
