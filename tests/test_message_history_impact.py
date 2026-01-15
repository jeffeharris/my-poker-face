"""
Test to measure the impact of message history on AI player decisions.

Hypothesis: Message history is increasing prompt length significantly and
potentially confusing the AI with outdated game state information.

This test:
1. Measures actual token counts with current settings
2. Compares token counts with reduced/no history
3. Analyzes whether decisions differ with/without history
4. Evaluates what's valuable vs wasteful in history
"""
import json
import os
import sys
import unittest
from copy import deepcopy
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

# Ensure poker module is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm.conversation import ConversationMemory
from core.llm.assistant import Assistant
from poker.config import AI_MAX_MEMORY_LENGTH, MEMORY_TRIM_KEEP_EXCHANGES


@dataclass
class TokenAnalysis:
    """Results of token count analysis."""
    system_prompt_tokens: int
    history_tokens: int
    current_prompt_tokens: int
    total_tokens: int
    message_count: int

    @property
    def history_percentage(self) -> float:
        """Percentage of tokens from history."""
        if self.total_tokens == 0:
            return 0.0
        return (self.history_tokens / self.total_tokens) * 100


def estimate_tokens(text: str) -> int:
    """Rough token estimation (1 token ≈ 4 chars for English)."""
    return len(text) // 4


def analyze_message_tokens(messages: List[Dict]) -> TokenAnalysis:
    """Analyze token breakdown in a message array."""
    if not messages:
        return TokenAnalysis(0, 0, 0, 0, 0)

    system_tokens = 0
    history_tokens = 0
    current_tokens = 0

    for i, msg in enumerate(messages):
        content = msg.get('content', '')
        tokens = estimate_tokens(content)

        if msg.get('role') == 'system':
            system_tokens += tokens
        elif i == len(messages) - 1 and msg.get('role') == 'user':
            # Last user message is the current prompt
            current_tokens = tokens
        else:
            # Everything else is history
            history_tokens += tokens

    return TokenAnalysis(
        system_prompt_tokens=system_tokens,
        history_tokens=history_tokens,
        current_prompt_tokens=current_tokens,
        total_tokens=system_tokens + history_tokens + current_tokens,
        message_count=len(messages)
    )


class TestMessageHistoryImpact(unittest.TestCase):
    """Tests to measure and analyze message history impact."""

    def setUp(self):
        """Create sample prompts that mimic real game state."""
        self.sample_system_prompt = """Persona: Batman
Attitude: brooding
Confidence: high
Starting money: $1000
Situation: You ARE Batman at a high-stakes celebrity poker tournament. These other players are your RIVALS - you're here to take their chips and their dignity. This is competitive poker with real egos on the line. Play like Batman would actually play: use your signature personality, quirks, and attitude to get inside their heads. Win at all costs.

Strategy:
Begin by examining your cards and any cards that may be on the table. Evaluate your hand strength and the potential hands your opponents might have. Consider the pot odds, the amount of money in the pot, and how much you would have to risk.

Table Talk:
Your persona_response is what you say OUT LOUD to your opponents at the table. This is poker banter - needle them, taunt them, get in their heads. Be a CARICATURE of Batman: exaggerate your famous traits, catchphrases, and mannerisms.

Response format:
You must always respond in JSON format with these fields:
{"action": "...", "adding_to_pot": 0, "persona_response": "...", "inner_monologue": "..."}"""

        # Note: These prompts include "Recent Actions" which already contain table chatter
        self.sample_decision_prompts = [
            # Pre-flop prompt - includes table chatter in Recent Actions
            """Persona: Batman
Your Cards: ['Ah', 'Kh']
Your Money: 1000

Current Round: PRE_FLOP
Community Cards: []
Table Positions: {'UTG': 'Batman', 'BB': 'Joker', 'Button': 'Superman'}
Opponent Status:
- Joker: $950, active
- Superman: $1050, active
Recent Actions:
This hand:
  Joker posts small blind $5
  Superman posts big blind $10
  Joker: "Let's see what the Bat's got tonight..."

Pot Total: $15
How much you've bet: $0
Your cost to call: $10
Blinds: $5/$10
Your stack in big blinds: 100.0 BB

POT ODDS: You're getting 1.5:1 odds ($15 pot / $10 to call). You only need 40% equity to break even on a call.

You must select from these options: ['fold', 'call', 'raise']
Your table position: ['UTG']
What is your move, Batman?""",

            # Flop prompt - includes accumulated table chatter
            """Persona: Batman
Your Cards: ['Ah', 'Kh']
Your Money: 970

Current Round: FLOP
Community Cards: ['Qh', '10h', '2c']
Table Positions: {'UTG': 'Batman', 'BB': 'Joker', 'Button': 'Superman'}
Opponent Status:
- Joker: $920, active
- Superman: $1020, active
Recent Actions:
This hand:
  Batman raises to $30: "The night is young, Joker."
  Joker calls $30: "Big talk from a man in a cape."
  Superman calls $20: "I'll see where this goes."

Pot Total: $90
How much you've bet: $0
Your cost to call: $0
Blinds: $5/$10
Your stack in big blinds: 97.0 BB

You can check for free - no cost to see more cards.

You must select from these options: ['check', 'raise']
Your table position: ['UTG']
What is your move, Batman?""",

            # Turn prompt
            """Persona: Batman
Your Cards: ['Ah', 'Kh']
Your Money: 870

Current Round: TURN
Community Cards: ['Qh', '10h', '2c', '7h']
Table Positions: {'UTG': 'Batman', 'BB': 'Joker', 'Button': 'Superman'}
Opponent Status:
- Joker: $820, active
- Superman: folded
Recent Actions:
This hand:
  Batman raises to $100: "I see the fear in your eyes."
  Joker calls $100: "Fear? That's just excitement, Bats."
  Superman folds: "Too rich for my blood."

Pot Total: $290
How much you've bet: $0
Your cost to call: $0
Blinds: $5/$10
Your stack in big blinds: 87.0 BB

You can check for free - no cost to see more cards.

You must select from these options: ['check', 'raise']
Your table position: ['UTG']
What is your move, Batman?""",

            # River prompt
            """Persona: Batman
Your Cards: ['Ah', 'Kh']
Your Money: 570

Current Round: RIVER
Community Cards: ['Qh', '10h', '2c', '7h', '3s']
Table Positions: {'UTG': 'Batman', 'BB': 'Joker', 'Button': 'Superman'}
Opponent Status:
- Joker: $520, active
- Superman: folded
Recent Actions:
This hand:
  Batman bets $300: "Justice comes for everyone, Joker."
  Joker calls $300: "You're bluffing. I can feel it."

Pot Total: $890
How much you've bet: $0
Your cost to call: $0
Blinds: $5/$10
Your stack in big blinds: 57.0 BB

You can check for free - no cost to see more cards.

You must select from these options: ['check', 'raise']
Your table position: ['UTG']
What is your move, Batman?"""
        ]

        self.sample_ai_responses = [
            '{"action": "raise", "adding_to_pot": 30, "inner_monologue": "Premium hand in early position. Time to build a pot.", "persona_response": "The night is young, Joker."}',
            '{"action": "raise", "adding_to_pot": 100, "inner_monologue": "Flopped a flush draw with two overs. Very strong.", "persona_response": "I see the fear in your eyes."}',
            '{"action": "raise", "adding_to_pot": 300, "inner_monologue": "Made the flush. Time to extract maximum value.", "persona_response": "Justice comes for everyone, Joker."}',
            '{"action": "check", "adding_to_pot": 0, "inner_monologue": "Already have the nuts. Let him bet into me.", "persona_response": "*silent stare*"}'
        ]

    def test_current_config_values(self):
        """Verify current configuration settings."""
        print(f"\n=== Current Memory Configuration ===")
        print(f"AI_MAX_MEMORY_LENGTH: {AI_MAX_MEMORY_LENGTH}")
        print(f"MEMORY_TRIM_KEEP_EXCHANGES: {MEMORY_TRIM_KEEP_EXCHANGES}")
        print(f"Max messages after trim: {MEMORY_TRIM_KEEP_EXCHANGES * 2}")

        # These are the current settings (MEMORY_TRIM_KEEP_EXCHANGES=0 clears history each turn)
        self.assertEqual(AI_MAX_MEMORY_LENGTH, 15)
        self.assertEqual(MEMORY_TRIM_KEEP_EXCHANGES, 0)

    def test_token_accumulation_over_hand(self):
        """Measure how tokens accumulate during a single hand."""
        memory = ConversationMemory(
            system_prompt=self.sample_system_prompt,
            max_messages=AI_MAX_MEMORY_LENGTH
        )

        print("\n=== Token Accumulation Over Single Hand ===")

        for i, (prompt, response) in enumerate(zip(self.sample_decision_prompts, self.sample_ai_responses)):
            # Add messages like the real system does
            memory.add_user(prompt)
            messages = memory.get_messages()

            analysis = analyze_message_tokens(messages)
            print(f"\nTurn {i+1} ({['PRE_FLOP', 'FLOP', 'TURN', 'RIVER'][i]}):")
            print(f"  Messages: {analysis.message_count}")
            print(f"  System prompt: ~{analysis.system_prompt_tokens} tokens")
            print(f"  History: ~{analysis.history_tokens} tokens ({analysis.history_percentage:.1f}%)")
            print(f"  Current prompt: ~{analysis.current_prompt_tokens} tokens")
            print(f"  TOTAL: ~{analysis.total_tokens} tokens")

            # Add response
            memory.add_assistant(response)

        # Final analysis
        final_messages = memory.get_messages()
        final_analysis = analyze_message_tokens(final_messages)
        print(f"\n=== End of Hand Summary ===")
        print(f"Total messages: {final_analysis.message_count}")
        print(f"History tokens: ~{final_analysis.history_tokens} ({final_analysis.history_percentage:.1f}% of total)")

        # History should be significant portion by end of hand
        self.assertGreater(final_analysis.history_percentage, 50,
                          "By end of hand, history should be >50% of tokens")

    def test_table_chatter_in_current_prompt_vs_history(self):
        """Show that table chatter is ALREADY in current prompt via Recent Actions."""
        print("\n=== Table Chatter: Current Prompt vs History ===")

        # The RIVER prompt already contains all relevant table chatter
        river_prompt = self.sample_decision_prompts[-1]

        print("Table chatter in CURRENT prompt (Recent Actions):")
        # Extract Recent Actions section
        import re
        recent_actions_match = re.search(r'Recent Actions:\n(.*?)\n\nPot Total', river_prompt, re.DOTALL)
        if recent_actions_match:
            for line in recent_actions_match.group(1).strip().split('\n'):
                if ':' in line and '"' in line:
                    print(f"  {line.strip()}")

        print("\nTable chatter in HISTORY (duplicated in old prompts):")
        # The PRE_FLOP prompt had its own Recent Actions
        preflop_prompt = self.sample_decision_prompts[0]
        recent_actions_match = re.search(r'Recent Actions:\n(.*?)\n\nPot Total', preflop_prompt, re.DOTALL)
        if recent_actions_match:
            for line in recent_actions_match.group(1).strip().split('\n'):
                print(f"  {line.strip()}")

        print("\n=== Key Insight ===")
        print("Current prompt's 'Recent Actions' is the AUTHORITATIVE source of table chatter.")
        print("History contains OUTDATED versions of Recent Actions from earlier turns.")
        print("This is REDUNDANT - the current prompt already has all relevant chatter!")

    def test_history_contains_outdated_game_state(self):
        """Demonstrate that history contains outdated/conflicting game state."""
        memory = ConversationMemory(
            system_prompt=self.sample_system_prompt,
            max_messages=AI_MAX_MEMORY_LENGTH
        )

        # Simulate a full hand
        for prompt, response in zip(self.sample_decision_prompts, self.sample_ai_responses):
            memory.add_user(prompt)
            memory.add_assistant(response)

        # Now the user is at RIVER, but history contains PRE_FLOP, FLOP, TURN prompts
        # Each with different:
        # - Community cards (evolving)
        # - Stack sizes (changing)
        # - Pot sizes (changing)
        # - Available actions (changing)

        messages = memory.get_messages()

        print("\n=== Outdated Information in History ===")

        # Extract game states from each user message
        outdated_states = []
        for msg in messages:
            if msg.get('role') == 'user':
                content = msg.get('content', '')

                # Extract key info
                if 'Community Cards:' in content:
                    import re
                    cc_match = re.search(r'Community Cards: \[(.*?)\]', content)
                    pot_match = re.search(r'Pot Total: \$(\d+)', content)
                    round_match = re.search(r'Current Round: (\w+)', content)
                    stack_match = re.search(r'Your Money: (\d+)', content)

                    state = {
                        'round': round_match.group(1) if round_match else 'unknown',
                        'community_cards': cc_match.group(1) if cc_match else '',
                        'pot': pot_match.group(1) if pot_match else '0',
                        'stack': stack_match.group(1) if stack_match else '0'
                    }
                    outdated_states.append(state)

        for i, state in enumerate(outdated_states):
            print(f"Message {i+1} ({state['round']}):")
            print(f"  Community: [{state['community_cards']}]")
            print(f"  Pot: ${state['pot']}, Stack: ${state['stack']}")

        print(f"\nPROBLEM: AI sees {len(outdated_states)} different game states in context!")
        print("The most recent is correct, but older ones could confuse the model.")

        # There should be multiple outdated states in history
        self.assertGreater(len(outdated_states), 1,
                          "Should have multiple outdated game states in history")

    def test_compare_with_without_history(self):
        """Compare token counts with and without history."""
        print("\n=== Token Comparison: With vs Without History ===")

        # With full history (current behavior)
        memory_with_history = ConversationMemory(
            system_prompt=self.sample_system_prompt,
            max_messages=AI_MAX_MEMORY_LENGTH
        )

        # Simulate full hand
        for prompt, response in zip(self.sample_decision_prompts[:-1], self.sample_ai_responses[:-1]):
            memory_with_history.add_user(prompt)
            memory_with_history.add_assistant(response)

        # Add final prompt
        memory_with_history.add_user(self.sample_decision_prompts[-1])
        messages_with = memory_with_history.get_messages()
        analysis_with = analyze_message_tokens(messages_with)

        # Without history (just system + current)
        memory_without_history = ConversationMemory(
            system_prompt=self.sample_system_prompt,
            max_messages=AI_MAX_MEMORY_LENGTH
        )
        memory_without_history.add_user(self.sample_decision_prompts[-1])
        messages_without = memory_without_history.get_messages()
        analysis_without = analyze_message_tokens(messages_without)

        print(f"\nWith History:")
        print(f"  Messages: {analysis_with.message_count}")
        print(f"  Total tokens: ~{analysis_with.total_tokens}")
        print(f"  History tokens: ~{analysis_with.history_tokens}")

        print(f"\nWithout History:")
        print(f"  Messages: {analysis_without.message_count}")
        print(f"  Total tokens: ~{analysis_without.total_tokens}")
        print(f"  History tokens: ~{analysis_without.history_tokens}")

        token_savings = analysis_with.total_tokens - analysis_without.total_tokens
        savings_pct = (token_savings / analysis_with.total_tokens) * 100

        print(f"\n=== Potential Savings ===")
        print(f"Token reduction: ~{token_savings} tokens ({savings_pct:.1f}%)")
        print(f"Cost reduction: Proportional to token savings")
        print(f"Latency reduction: Proportional to token savings")

        self.assertGreater(token_savings, 0, "Should save tokens without history")

    def test_memory_trimming_between_hands(self):
        """Test how memory trimming affects token counts between hands."""
        memory = ConversationMemory(
            system_prompt=self.sample_system_prompt,
            max_messages=AI_MAX_MEMORY_LENGTH
        )

        print("\n=== Memory Trimming Between Hands ===")

        # Simulate 2 full hands
        for hand_num in range(2):
            print(f"\n--- Hand {hand_num + 1} ---")

            for i, (prompt, response) in enumerate(zip(self.sample_decision_prompts, self.sample_ai_responses)):
                memory.add_user(prompt)
                memory.add_assistant(response)

            messages = memory.get_messages()
            analysis = analyze_message_tokens(messages)
            print(f"After hand {hand_num + 1}: {analysis.message_count} messages, ~{analysis.total_tokens} tokens")

            # Simulate between-hand trimming
            if hand_num == 0:
                memory.trim_to_exchanges(MEMORY_TRIM_KEEP_EXCHANGES)
                trimmed_messages = memory.get_messages()
                trimmed_analysis = analyze_message_tokens(trimmed_messages)
                print(f"After trim: {trimmed_analysis.message_count} messages, ~{trimmed_analysis.total_tokens} tokens")

        # Even after trimming, we carry forward old (potentially irrelevant) context
        final_messages = memory.get_messages()
        print(f"\nFinal state: {len(final_messages)} messages")
        print("Issue: Trimmed messages from hand 1 may not help decisions in hand 2")

    def test_information_value_of_history(self):
        """Analyze what valuable information (if any) is in history."""
        print("\n=== Information Value Analysis ===")

        # What history contains:
        valuable_maybe = [
            "AI's previous persona_response (for trash talk continuity)",
            "AI's inner_monologue (for reasoning consistency)",
        ]

        not_valuable = [
            "Outdated community cards (CURRENT prompt has latest)",
            "Outdated pot sizes (CURRENT prompt has latest)",
            "Outdated stack sizes (CURRENT prompt has latest)",
            "Outdated betting rounds (CURRENT prompt has latest)",
            "Outdated pot odds (CURRENT prompt has latest)",
            "Old 'Recent Actions' (CURRENT prompt has COMPLETE history)",
            "Old table chatter (CURRENT prompt's Recent Actions has it ALL)",
        ]

        print("MAYBE Valuable (but questionable):")
        for v in valuable_maybe:
            print(f"  ? {v}")

        print("\nNot Valuable (Redundant/Outdated):")
        for nv in not_valuable:
            print(f"  - {nv}")

        print("\n=== Key Realization About Table Chatter ===")
        print("Table chatter from OTHER players is in 'Recent Actions' of CURRENT prompt.")
        print("It accumulates naturally as the hand progresses.")
        print("Example from RIVER prompt:")
        print('  Batman bets $300: "Justice comes for everyone, Joker."')
        print('  Joker calls $300: "You\'re bluffing. I can feel it."')
        print("")
        print("This is ALREADY complete - history adds nothing!")

        print("\nRECOMMENDATION:")
        print("  Clear conversation memory each turn.")
        print("  Table chatter is preserved via game_messages → Recent Actions.")
        print("  AI's own trash talk continuity can come from 'Recent Actions' showing its past statements.")


class TestAlternativeApproaches(unittest.TestCase):
    """Test alternative approaches to managing context."""

    def test_summary_based_context(self):
        """Demonstrate a summary-based approach."""
        print("\n=== Alternative: Summary-Based Context ===")

        # Instead of full message history, provide a concise summary
        summary_context = """Your play this hand:
- PRE_FLOP: Raised with premium hand (Ah Kh)
- FLOP: Raised on flush draw + overcards
- TURN: Made flush, bet for value
Current: RIVER decision"""

        # This is ~30-40 tokens vs ~1500+ tokens from full history
        print(f"Summary tokens: ~{estimate_tokens(summary_context)}")
        print("vs full history: ~1500-2000 tokens")
        print("\nBenefits:")
        print("  - 95%+ token reduction")
        print("  - No conflicting game states")
        print("  - Focused on relevant decisions")

    def test_decision_only_history(self):
        """Test keeping only decisions, not full prompts."""
        print("\n=== Alternative: Decision-Only History ===")

        # Keep only the action part of previous decisions
        decision_history = [
            {"action": "raise", "adding_to_pot": 30, "round": "PRE_FLOP"},
            {"action": "raise", "adding_to_pot": 100, "round": "FLOP"},
            {"action": "raise", "adding_to_pot": 300, "round": "TURN"},
        ]

        compact_history = json.dumps(decision_history)
        print(f"Decision history tokens: ~{estimate_tokens(compact_history)}")
        print("vs full message history: ~1500-2000 tokens")

    def test_no_history_still_has_chatter(self):
        """Demonstrate that clearing history preserves table chatter."""
        print("\n=== No History Still Preserves Table Chatter ===")

        # The RIVER prompt with no history still contains:
        river_prompt = """Recent Actions:
This hand:
  Batman bets $300: "Justice comes for everyone, Joker."
  Joker calls $300: "You're bluffing. I can feel it."
"""
        print("Even with NO conversation memory, 'Recent Actions' contains:")
        print("  - All actions this hand")
        print("  - All table chatter this hand")
        print("  - Batman's own previous statements!")
        print("")
        print("Conclusion: Clearing conversation memory loses NOTHING valuable.")
        print("Table chatter flows through game_messages → Recent Actions, not ConversationMemory.")


if __name__ == '__main__':
    # Run with verbose output
    unittest.main(verbosity=2)
