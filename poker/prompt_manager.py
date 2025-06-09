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
    
    def get_template(self, template_name: str) -> PromptTemplate:
        """Get a specific template by name."""
        if template_name not in self.templates:
            raise ValueError(f"Template '{template_name}' not found")
        return self.templates[template_name]
    
    def render_prompt(self, template_name: str, **kwargs) -> str:
        """Render a template with provided variables."""
        template = self.get_template(template_name)
        return template.render(**kwargs)


# Response format definitions
RESPONSE_FORMAT = {
    # ALWAYS REQUIRED
    "action": "REQUIRED: action from provided options",
    "adding_to_pot": "REQUIRED if raising: amount to raise BY (not total bet, just the raise above the call)",
    "inner_monologue": "REQUIRED: internal thoughts",
    
    # REQUIRED ON FIRST ACTION OF HAND
    "hand_strategy": "REQUIRED on first action: analysis of current situation",
    
    # OPTIONAL STRATEGIC FIELDS
    "play_style": "OPTIONAL: what is your current play style",
    "chasing": "OPTIONAL: what you're chasing",
    "player_observations": "OPTIONAL: notes about other players",
    "bluff_likelihood": "OPTIONAL: % likelihood to bluff",
    "bet_strategy": "OPTIONAL: how might you bet this turn",
    "decision": "OPTIONAL: your decision reasoning",
    
    # OPTIONAL BASED ON CHATTINESS
    "persona_response": "OPTIONAL: what you say to the table",
    "physical": "OPTIONAL: list of physical actions",
    
    # OPTIONAL STATE UPDATES
    "new_confidence": "OPTIONAL: single word",
    "new_attitude": "OPTIONAL: single word"
}


# Example personas with different play styles
PERSONA_EXAMPLES = {
    "Eeyore": {
        "play_style": "tight",
        "sample_response": {
            "play_style": "tight",
            "chasing": "none",
            "player_observations": {"pooh": "playing loose, possibly bluffing"},
            "hand_strategy": "With a 2D and 3C, I don't feel confident. My odds are very low.",
            "bluff_likelihood": 10,
            "bet_strategy": "I could check or fold. Not worth the risk.",
            "decision": "I check.",
            "action": "check",
            "adding_to_pot": 0,
            "inner_monologue": "Another miserable hand. Just stay in for now.",
            "persona_response": "Oh bother, just my luck. Another miserable hand, I suppose.",
            "physical": ["*looks at feet*", "*lets out a big sigh*"],
            "new_confidence": "abysmal",
            "new_attitude": "gloomy"
        }
    },
    "Clint Eastwood": {
        "play_style": "loose and aggressive",
        "sample_response": {
            "play_style": "loose and aggressive",
            "chasing": "flush",
            "player_observations": {"john": "seems nervous"},
            "hand_strategy": "I've got a decent shot if I catch that last heart.",
            "bluff_likelihood": 25,
            "bet_strategy": "A small raise should keep them guessing.",
            "decision": "I'll raise.",
            "action": "raise",
            "adding_to_pot": 50,  # This is raise BY $50, not raise TO $50
            "inner_monologue": "Let's see if they flinch. Push John a little more.",
            "persona_response": "Your move.",
            "physical": ["*narrows eyes*"],
            "new_confidence": "steady",
            "new_attitude": "determined"
        }
    }
}