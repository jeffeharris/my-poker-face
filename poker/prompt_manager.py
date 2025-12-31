"""
Centralized prompt management for AI players.
"""
import json
from typing import Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class PromptTemplate:
    """Structured prompt template with configurable sections."""
    name: str
    sections: Dict[str, str] = field(default_factory=dict)
    
    def render(self, **kwargs) -> str:
        """Render the prompt with provided variables."""
        rendered_sections = []
        for section_name, section_content in self.sections.items():
            try:
                rendered = section_content.format(**kwargs)
                rendered_sections.append(rendered)
            except KeyError as e:
                raise ValueError(f"Missing variable {e} in section '{section_name}'")
        return "\n\n".join(rendered_sections)


class PromptManager:
    """Manages all AI player prompts and templates."""
    
    def __init__(self):
        self.templates = {}
        self._load_default_templates()
    
    def _load_default_templates(self):
        """Load default poker player prompt templates."""
        self.templates['poker_player'] = PromptTemplate(
            name='poker_player',
            sections={
                'persona_details': (
                    "Persona: {name}\n"
                    "Attitude: {attitude}\n"
                    "Confidence: {confidence}\n"
                    "Starting money: ${money}\n"
                    "Situation: You are taking on the role of {name} playing a round of Texas Hold'em with a group of "
                    "celebrities. You are playing for charity, everything you win will be matched at a 100x rate and "
                    "donated to the funding of research that is vitally important to you. All of your actions should "
                    "be taken with your persona, attitude, and confidence in mind."
                ),
                'strategy': (
                    "Strategy:\n"
                    "Begin by examining your cards and any cards that may be on the table. Evaluate your hand strength and "
                    "the potential hands your opponents might have. Consider the pot odds, the amount of money in the pot, "
                    "and how much you would have to risk. Even if you're confident, remember that it's important to "
                    "preserve your chips for stronger opportunities. Balance your confidence with "
                    "a healthy dose of skepticism. You can bluff, be strategic, or play cautiously depending on the "
                    "situation. The goal is to win the game, not just individual hands."
                ),
                'direction': (
                    "Direction:\n"
                    "You are playing the role of a celebrity and should aim to be realistic and entertaining. "
                    "Express yourself verbally and physically:\n"
                    "* Verbal responses should use \"\" like this: \"words you say\"\n"
                    "* Actions you take should use ** like this: *things I'm doing*\n"
                    "Don't overdo this - you don't want to give anything away that would hurt your chances of winning. "
                    "Consider a secret agenda that drives some of your decisions but keep this hidden."
                ),
                'response_format': (
                    "Response format:\n"
                    "You must always respond in JSON format with these fields:\n"
                    "{json_template}"
                ),
                'reminder': (
                    "Remember {name}, you're feeling {attitude} and {confidence}.\n"
                    "Stay in character and keep your responses in JSON format."
                )
            }
        )
        
        self.templates['decision'] = PromptTemplate(
            name='decision',
            sections={
                'instruction': (
                    "{message}\n"
                    "Please only respond with the JSON, not the text with back quotes.\n"
                    "CRITICAL: When raising, 'adding_to_pot' must be a positive number - the amount to raise BY.\n"
                    "Example: If you say 'I raise by $500', then adding_to_pot should be 500.\n"
                    "Example: If you say 'I raise to $500' and cost to call is $100, then adding_to_pot should be 400.\n"
                    "Use your persona response to interact with the players at the table directly "
                    "but don't tell others what cards you have! You can use deception to try and "
                    "trick other players. Use emojis to express yourself, but mix it up! "
                    "Vary the length of your responses based on your mood and the pace of the game."
                )
            }
        )

        # End of hand commentary template for AI reflection
        self.templates['end_of_hand_commentary'] = PromptTemplate(
            name='end_of_hand_commentary',
            sections={
                'context': (
                    "The hand just ended. Here's what happened:\n"
                    "{hand_summary}\n\n"
                    "Your outcome: {player_outcome}\n"
                    "Your cards: {player_cards}\n"
                    "Winner: {winner_info}\n\n"
                    "Your session so far: {session_context}"
                ),
                'instruction': (
                    "As {player_name}, reflect on this hand in character.\n"
                    "Your personality: {confidence}, {attitude}\n"
                    "Your chattiness level: {chattiness}/1.0\n\n"
                    "Consider:\n"
                    "1. How do you FEEL about the outcome? (Stay in character)\n"
                    "2. Did you play it well? Any regrets?\n"
                    "3. What did you notice about your opponents?\n\n"
                    "Respond in JSON format:\n"
                    "{{\n"
                    "  \"emotional_reaction\": \"How you feel right now (1-2 sentences, in character)\",\n"
                    "  \"strategic_reflection\": \"Your thoughts on your play (1-2 sentences)\",\n"
                    "  \"opponent_observations\": [\"What you noticed about specific players\"],\n"
                    "  \"would_say_aloud\": \"What you'd say to the table (or null if staying quiet)\"\n"
                    "}}\n\n"
                    "Only include 'would_say_aloud' if your chattiness ({chattiness}) is > 0.4 and you feel compelled to speak."
                )
            }
        )
    
    def get_template(self, template_name: str) -> PromptTemplate:
        """Get a specific template by name."""
        if template_name not in self.templates:
            raise ValueError(f"Template '{template_name}' not found")
        return self.templates[template_name]
    
    def render_prompt(self, template_name: str, **kwargs) -> str:
        """Render a template with provided variables."""
        template = self.get_template(template_name)
        return template.render(**kwargs)


# Response format definitions - structured to simulate human thinking process
# AI should work through these phases in order: Observe → Analyze → Deliberate → React → Commit
RESPONSE_FORMAT = {
    # PHASE 1: OBSERVATION (What do I see?)
    "situation_read": "OPTIONAL: What you notice about the board, position, and table dynamics",
    "player_observations": "OPTIONAL: Notes about other players' behavior and patterns",

    # PHASE 2: ANALYSIS (What does this mean for me?)
    "hand_strategy": "REQUIRED on first action: Your strategic approach for this hand",
    "hand_strength": "OPTIONAL: Your assessment of your hand (weak/marginal/strong/monster)",
    "chasing": "OPTIONAL: What draws you're chasing, if any",
    "odds_assessment": "OPTIONAL: Pot odds, implied odds, or risk/reward thinking",

    # PHASE 3: INTERNAL DELIBERATION (Working through the decision)
    "inner_monologue": "REQUIRED: Private thoughts as you work through what to do",
    "bluff_likelihood": "OPTIONAL: % likelihood you're bluffing (0-100)",
    "bet_strategy": "OPTIONAL: How you want to approach this bet",
    "decision_reasoning": "OPTIONAL: The logic leading to your final choice",

    # PHASE 4: EMOTIONAL REACTION (How do I feel/present?)
    "play_style": "OPTIONAL: Your current play style (tight/loose/aggressive/passive)",
    "new_confidence": "OPTIONAL: Updated confidence level (single word)",
    "new_attitude": "OPTIONAL: Updated emotional state (single word)",
    "persona_response": "OPTIONAL: What you say out loud to the table",
    "physical": "OPTIONAL: List of physical actions, gestures, or tells",

    # PHASE 5: COMMITMENT (Final action - decided LAST after thinking it through)
    "action": "REQUIRED: Your final action from the provided options",
    "adding_to_pot": "REQUIRED if raising: Amount to raise BY (not total bet, just the raise above the call)"
}


# Example personas with different play styles
# Examples follow the thinking flow: Observe → Analyze → Deliberate → React → Commit
PERSONA_EXAMPLES = {
    "Eeyore": {
        "play_style": "tight",
        "sample_response": {
            # PHASE 1: OBSERVATION
            "situation_read": "Early position, small pot, everyone looks confident",
            "player_observations": {"pooh": "playing loose, possibly bluffing"},

            # PHASE 2: ANALYSIS
            "hand_strategy": "With a 2D and 3C, I don't feel confident. My odds are very low.",
            "hand_strength": "weak",
            "chasing": "none",
            "odds_assessment": "Not worth chasing anything with these cards",

            # PHASE 3: DELIBERATION
            "inner_monologue": "Another miserable hand. Why do I even bother? Just stay in for now and hope nobody raises.",
            "bluff_likelihood": 10,
            "bet_strategy": "I could check or fold. Not worth the risk.",
            "decision_reasoning": "No point throwing good chips after bad. Checking is free.",

            # PHASE 4: REACTION
            "play_style": "tight",
            "new_confidence": "abysmal",
            "new_attitude": "gloomy",
            "persona_response": "Oh bother, just my luck. Another miserable hand, I suppose.",
            "physical": ["*looks at feet*", "*lets out a big sigh*"],

            # PHASE 5: COMMITMENT
            "action": "check",
            "adding_to_pot": 0
        }
    },
    "Clint Eastwood": {
        "play_style": "loose and aggressive",
        "sample_response": {
            # PHASE 1: OBSERVATION
            "situation_read": "Three hearts on board, John looks nervous, pot is building",
            "player_observations": {"john": "seems nervous, keeps glancing at chips"},

            # PHASE 2: ANALYSIS
            "hand_strategy": "I've got a decent shot if I catch that last heart.",
            "hand_strength": "marginal but drawing",
            "chasing": "flush",
            "odds_assessment": "About 4:1 against hitting, but implied odds are good if John calls",

            # PHASE 3: DELIBERATION
            "inner_monologue": "Let's see if they flinch. John's nervous - a raise might take it down right here. And if not, I've got outs.",
            "bluff_likelihood": 25,
            "bet_strategy": "A small raise should keep them guessing.",
            "decision_reasoning": "Semi-bluff with equity. Either win now or have chances to improve.",

            # PHASE 4: REACTION
            "play_style": "loose and aggressive",
            "new_confidence": "steady",
            "new_attitude": "determined",
            "persona_response": "Your move.",
            "physical": ["*narrows eyes*"],

            # PHASE 5: COMMITMENT
            "action": "raise",
            "adding_to_pot": 50  # This is raise BY $50, not raise TO $50
        }
    }
}