#!/usr/bin/env python3
"""
Replay captured prompts with modified guidance to test prompt effectiveness.

Usage:
    python -m experiments.replay_with_guidance --capture-id 4872 --guidance "strict_fold"
    python -m experiments.replay_with_guidance --capture-id 4872 --guidance-text "FOLD weak hands!"
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.llm import LLMClient, CallType

# Predefined guidance variants to test
GUIDANCE_VARIANTS = {
    "strict_fold": """
PRE-FLOP DISCIPLINE: Your hand strength rating is CRITICAL.
- "Below average starting hand" = FOLD in early/middle position
- "Bottom 25% of starting hands" = ALWAYS FOLD
- "Bottom 10% of starting hands" = NEVER play this hand
Your personality can influence HOW you play good hands, but NOT whether you play bad hands.
Even aggressive players fold garbage pre-flop - that's how they stay aggressive later.
""",

    "math_first": """
MATH BEFORE PERSONALITY: Before ANY action, check:
1. Is your hand in the top 35% of starting hands? If not, strongly consider folding.
2. Does your equity (shown in hand strength) exceed the required equity for a call?
3. In early position, only play premium hands (top 15%).
Your character's aggression applies AFTER you have a playable hand, not before.
""",

    "position_aware": """
POSITION MATTERS: Your table position determines which hands you can play.
- UTG (under the gun): Only top 10% hands (AA, KK, QQ, JJ, AKs, AKo, AQs)
- Middle position: Top 20% hands
- Button/Cutoff: Top 35% hands
- Blinds defending: Depends on pot odds
Your hand strength rating tells you where your hand ranks. FOLD if it doesn't meet position requirements.
""",

    "explicit_fold": """
FOLD THESE HANDS PRE-FLOP (regardless of personality):
- Any hand rated "Below average starting hand" - FOLD
- Any hand rated "Bottom 25%" or "Bottom 10%" - ALWAYS FOLD
- Unsuited hands with gaps (like 6-4, 7-3, 9-5) - FOLD
- Low unsuited connectors (5-4o, 6-5o, 7-6o) - FOLD in early/middle position
Your aggressive personality applies to BETTING SIZING with good hands, not to playing bad hands.
"""
}


def get_capture(db_path: str, capture_id: int) -> dict:
    """Fetch a prompt capture from the database."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('''
            SELECT pc.*, pda.equity, pda.optimal_action, pda.ev_lost
            FROM prompt_captures pc
            LEFT JOIN player_decision_analysis pda ON pc.game_id = pda.game_id
                AND pc.hand_number = pda.hand_number
                AND pc.player_name = pda.player_name
                AND pc.phase = pda.phase
            WHERE pc.id = ?
        ''', (capture_id,))

        row = cursor.fetchone()

        if not row:
            raise ValueError(f"Capture {capture_id} not found")

        return dict(row)


def inject_guidance(user_message: str, guidance_text: str) -> str:
    """Inject guidance text into the user message before the action prompt."""
    # Find a good injection point - before "What is your move"
    injection_point = user_message.find("What is your move")
    if injection_point == -1:
        # Fallback: prepend to the message
        return guidance_text + "\n\n" + user_message

    # Insert guidance before the "What is your move" line
    return (
        user_message[:injection_point] +
        "\n" + guidance_text + "\n\n" +
        user_message[injection_point:]
    )


def replay_decision(capture: dict, guidance_text: str, verbose: bool = False) -> dict:
    """Replay a captured prompt with modified guidance."""

    # Inject guidance into user message
    modified_message = inject_guidance(capture['user_message'], guidance_text)

    if verbose:
        print("\n=== MODIFIED USER MESSAGE (injection point) ===")
        # Show context around injection
        idx = modified_message.find(guidance_text)
        start = max(0, idx - 100)
        end = min(len(modified_message), idx + len(guidance_text) + 100)
        print(f"...{modified_message[start:end]}...")

    # Create LLM client and replay
    client = LLMClient(
        provider=capture['provider'],
        model=capture['model']
    )

    messages = [
        {"role": "system", "content": capture['system_prompt']},
        {"role": "user", "content": modified_message}
    ]

    response = client.complete(
        messages=messages,
        json_format=True,
        call_type=CallType.PLAYER_DECISION
    )

    # Parse response
    try:
        result = json.loads(response.content)
    except json.JSONDecodeError:
        result = {"raw": response.content, "parse_error": True}

    return {
        "action": result.get("action"),
        "adding_to_pot": result.get("adding_to_pot"),
        "inner_monologue": result.get("inner_monologue"),
        "full_response": result
    }


def main():
    parser = argparse.ArgumentParser(description="Replay captured prompts with modified guidance")
    parser.add_argument("--capture-id", type=int, required=True, help="ID of prompt capture to replay")
    parser.add_argument("--guidance", choices=list(GUIDANCE_VARIANTS.keys()),
                        help="Predefined guidance variant to use")
    parser.add_argument("--guidance-text", type=str, help="Custom guidance text to inject")
    parser.add_argument("--db", default="data/poker_games.db", help="Database path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")
    parser.add_argument("--all-variants", action="store_true",
                        help="Test all predefined guidance variants")

    args = parser.parse_args()

    if not args.guidance and not args.guidance_text and not args.all_variants:
        parser.error("Must specify --guidance, --guidance-text, or --all-variants")

    # Get the capture
    capture = get_capture(args.db, args.capture_id)

    print("=" * 60)
    print("PROMPT REPLAY TEST")
    print("=" * 60)
    print(f"Capture ID: {args.capture_id}")
    print(f"Player: {capture['player_name']}")
    print(f"Hand: {capture['player_hand']}")
    print(f"Phase: {capture['phase']}")
    print(f"Model: {capture['provider']}/{capture['model']}")
    print(f"\nOriginal action: {capture['action_taken']}")
    print(f"Optimal action: {capture['optimal_action']}")
    print(f"Equity: {capture['equity']:.1%}" if capture['equity'] else "Equity: N/A")
    print(f"EV Lost: ${capture['ev_lost']:.0f}" if capture['ev_lost'] else "EV Lost: N/A")

    # Original AI response
    try:
        original_resp = json.loads(capture['ai_response'])
        print(f"\nOriginal inner monologue: {original_resp.get('inner_monologue', 'N/A')[:200]}...")
    except:
        pass

    # Determine which variants to test
    if args.all_variants:
        variants_to_test = list(GUIDANCE_VARIANTS.items())
    elif args.guidance:
        variants_to_test = [(args.guidance, GUIDANCE_VARIANTS[args.guidance])]
    else:
        variants_to_test = [("custom", args.guidance_text)]

    # Run tests
    print("\n" + "=" * 60)
    print("REPLAY RESULTS")
    print("=" * 60)

    results = []
    for variant_name, guidance_text in variants_to_test:
        print(f"\n--- {variant_name.upper()} ---")
        print(f"Guidance: {guidance_text[:100]}...")

        try:
            result = replay_decision(capture, guidance_text, verbose=args.verbose)
            print(f"New action: {result['action']}")
            if result.get('adding_to_pot'):
                print(f"Adding to pot: {result['adding_to_pot']}")
            print(f"Inner monologue: {result.get('inner_monologue', 'N/A')[:200]}...")

            # Did it improve?
            original_action = capture['action_taken']
            optimal_action = capture['optimal_action']
            new_action = result['action']

            if new_action == optimal_action:
                print("✅ NOW CORRECT!")
            elif new_action != original_action:
                print(f"🔄 Changed from {original_action} to {new_action}")
            else:
                print(f"❌ Still {new_action} (should be {optimal_action})")

            results.append({
                "variant": variant_name,
                "new_action": new_action,
                "correct": new_action == optimal_action,
                "changed": new_action != original_action
            })

        except Exception as e:
            print(f"❌ Error: {e}")
            results.append({
                "variant": variant_name,
                "error": str(e)
            })

    # Summary
    if len(results) > 1:
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        correct = sum(1 for r in results if r.get('correct'))
        changed = sum(1 for r in results if r.get('changed'))
        print(f"Correct: {correct}/{len(results)}")
        print(f"Changed behavior: {changed}/{len(results)}")


if __name__ == "__main__":
    main()
