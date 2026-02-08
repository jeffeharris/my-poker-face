"""
Phase 2 Energy + Expression Demo

Quick experiment to show the new dynamic energy and expression filtering behavior.
Run with: python -m experiments.phase2_energy_demo
"""

import sys
sys.path.insert(0, '.')

from poker.player_psychology import PlayerPsychology, EmotionalAxes
from poker.expression_filter import calculate_visibility, dampen_emotion, get_expression_guidance


def create_test_personalities():
    """Create contrasting personalities for demo."""

    # Gordon Ramsay type: High expressiveness, low poise, volatile
    ramsay_config = {
        'anchors': {
            'baseline_aggression': 0.85,
            'baseline_looseness': 0.7,
            'ego': 0.85,
            'poise': 0.20,
            'expressiveness': 0.90,  # Very expressive
            'risk_identity': 0.75,
            'adaptation_bias': 0.5,
            'baseline_energy': 0.7,
            'recovery_rate': 0.12,
        }
    }

    # Batman type: Low expressiveness, high poise, stoic
    batman_config = {
        'anchors': {
            'baseline_aggression': 0.5,
            'baseline_looseness': 0.35,
            'ego': 0.30,
            'poise': 0.90,
            'expressiveness': 0.15,  # Poker face
            'risk_identity': 0.5,
            'adaptation_bias': 0.5,
            'baseline_energy': 0.4,
            'recovery_rate': 0.15,
        }
    }

    return {
        'Gordon Ramsay': PlayerPsychology.from_personality_config('Gordon Ramsay', ramsay_config),
        'Batman': PlayerPsychology.from_personality_config('Batman', batman_config),
    }


def print_state(name, psych):
    """Print current psychological state."""
    visibility = calculate_visibility(psych.anchors.expressiveness, psych.energy)
    true_emotion = psych._get_true_emotion()
    displayed = psych.get_display_emotion()

    print(f"  {name}:")
    print(f"    Confidence: {psych.confidence:.2f} | Composure: {psych.composure:.2f} | Energy: {psych.energy:.2f}")
    print(f"    Quadrant: {psych.quadrant.value}")
    print(f"    Visibility: {visibility:.2f} (expressiveness {psych.anchors.expressiveness:.1f} x energy {psych.energy:.2f})")
    print(f"    True emotion: {true_emotion} -> Displayed: {displayed}")
    print(f"    Consecutive folds: {psych.consecutive_folds}")
    print()


def demo_energy_events():
    """Demo how energy changes with events."""
    print("=" * 60)
    print("DEMO 1: Energy Changes from Events")
    print("=" * 60)
    print()

    players = create_test_personalities()

    print("Initial State:")
    for name, psych in players.items():
        print_state(name, psych)

    # Apply all-in moment
    print("-" * 40)
    print("EVENT: All-in moment (both players)")
    print("-" * 40)
    for name, psych in players.items():
        psych.apply_pressure_event('all_in_moment')
    for name, psych in players.items():
        print_state(name, psych)

    # Apply big win to Ramsay, bad beat to Batman
    print("-" * 40)
    print("EVENT: Ramsay wins big, Batman suffers bad beat")
    print("-" * 40)
    players['Gordon Ramsay'].apply_pressure_event('big_win')
    players['Batman'].apply_pressure_event('bad_beat')
    for name, psych in players.items():
        print_state(name, psych)


def demo_consecutive_folds():
    """Demo consecutive fold tracking and energy drain."""
    print("=" * 60)
    print("DEMO 2: Consecutive Folds -> Energy Drain")
    print("=" * 60)
    print()

    players = create_test_personalities()

    print("Initial State:")
    for name, psych in players.items():
        print_state(name, psych)

    # Simulate folding streak for both
    for i in range(1, 6):
        print("-" * 40)
        print(f"Both players fold (fold #{i})")
        print("-" * 40)
        for name, psych in players.items():
            events = psych.on_action_taken('fold')
            if events:
                print(f"  {name} triggered: {events}")
        print()
        for name, psych in players.items():
            print_state(name, psych)

    # Now one plays a hand
    print("-" * 40)
    print("Batman raises (breaks fold streak)")
    print("-" * 40)
    players['Batman'].on_action_taken('raise')
    for name, psych in players.items():
        print_state(name, psych)


def demo_energy_recovery():
    """Demo energy recovery with edge springs."""
    print("=" * 60)
    print("DEMO 3: Energy Recovery with Edge Springs")
    print("=" * 60)
    print()

    players = create_test_personalities()

    # Push Ramsay to high energy, Batman to low energy
    players['Gordon Ramsay'].axes = players['Gordon Ramsay'].axes.update(energy=0.95)
    players['Batman'].axes = players['Batman'].axes.update(energy=0.05)

    print("After extreme events:")
    for name, psych in players.items():
        print_state(name, psych)

    # Apply recovery 5 times
    for i in range(1, 6):
        print("-" * 40)
        print(f"Recovery #{i}")
        print("-" * 40)
        for psych in players.values():
            psych.recover()
        for name, psych in players.items():
            print_state(name, psych)


def demo_expression_filtering():
    """Demo how visibility affects displayed emotions."""
    print("=" * 60)
    print("DEMO 4: Expression Filtering (Visibility)")
    print("=" * 60)
    print()

    players = create_test_personalities()

    # Push both into OVERHEATED quadrant (high conf, low comp)
    for psych in players.values():
        psych.axes = psych.axes.update(confidence=0.8, composure=0.3)

    print("Both players in OVERHEATED quadrant (should feel angry):")
    print()

    # Test at different energy levels
    for energy_level in [0.9, 0.5, 0.2]:
        print(f"--- Energy = {energy_level} ---")
        for name, psych in players.items():
            psych.axes = psych.axes.update(energy=energy_level)
            visibility = calculate_visibility(psych.anchors.expressiveness, energy_level)
            true_emotion = psych._get_true_emotion()
            displayed = psych.get_display_emotion()

            print(f"  {name}:")
            print(f"    Visibility: {visibility:.2f}")
            print(f"    True: {true_emotion} -> Shows: {displayed}")

            # Get prompt guidance
            guidance = get_expression_guidance(psych.anchors.expressiveness, energy_level)
            print(f"    Prompt guidance: {guidance.split(chr(10))[0]}")  # First line only
        print()


def main():
    print("\n" + "=" * 60)
    print("PSYCHOLOGY SYSTEM PHASE 2 - ENERGY + EXPRESSION DEMO")
    print("=" * 60 + "\n")

    demo_energy_events()
    print("\n")

    demo_consecutive_folds()
    print("\n")

    demo_energy_recovery()
    print("\n")

    demo_expression_filtering()

    print("\n" + "=" * 60)
    print("DEMO COMPLETE")
    print("=" * 60)


if __name__ == '__main__':
    main()
