"""
Tilt Prompt Modifier System.

Modifies AI prompts based on tilt state to simulate the psychological effects
of being on tilt: tunnel vision, intrusive thoughts, and poor strategic thinking.
"""

import random
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


# Intrusive thoughts injected based on tilt source
INTRUSIVE_THOUGHTS = {
    'bad_beat': [
        "You can't believe that river card. Unreal.",
        "That should have been YOUR pot.",
        "The cards are running against you tonight.",
        "How could they have called with THAT hand?",
        "The poker gods are not on your side right now.",
    ],
    'bluff_called': [
        "They're onto you. Or are they just lucky?",
        "You need to prove you can't be pushed around.",
        "Next time, make them PAY for calling.",
        "They got lucky. Your read was right.",
        "Time to switch it up and confuse them.",
    ],
    'big_loss': [
        "You NEED to win this one back. NOW.",
        "Your stack is dwindling. Do something!",
        "Stop being so passive. Take control!",
        "You're better than this. Show them.",
        "One big hand and you're back in it.",
    ],
    'losing_streak': [
        "Nothing is going your way tonight.",
        "You can't catch a break.",
        "Maybe this isn't your night...",
        "When will your luck turn around?",
        "You've been card dead for too long.",
    ],
    'revenge': [
        "{nemesis} just took your chips. Make them regret it.",
        "Show {nemesis} who the real player is here.",
        "{nemesis} thinks they have your number. Prove them wrong.",
        "You owe {nemesis} some payback.",
    ],
}

# Strategy overrides that replace careful thinking with tilted advice
TILTED_STRATEGY = {
    'mild': (
        "You're feeling the pressure. Trust your gut more than the math. "
        "Sometimes you just need to make a play."
    ),
    'moderate': (
        "Forget the textbook plays. You need to make something happen. "
        "Being passive got you here - time to take control. "
        "If you have any piece of the board, consider betting."
    ),
    'severe': (
        "You're behind and you know it. Stop playing scared. "
        "Big hands or big bluffs - that's how you get back in this. "
        "They think they have you figured out? Prove them wrong. "
        "Don't fold unless you have absolutely nothing."
    ),
}

# Normal strategic advice that gets removed when tilted
STRATEGIC_PHRASES_TO_REMOVE = [
    "Preserve your chips for when the odds are in your favor",
    "preserve your chips for stronger opportunities",
    "remember that sometimes folding or checking is the best move",
    "Balance your confidence with a healthy dose of skepticism",
]


@dataclass
class TiltState:
    """Tracks tilt information for a player."""
    tilt_level: float = 0.0  # 0.0 to 1.0
    tilt_source: str = ''    # 'bad_beat', 'bluff_called', 'big_loss', etc.
    nemesis: Optional[str] = None  # Player who caused the tilt
    recent_losses: List[Dict[str, Any]] = field(default_factory=list)
    losing_streak: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for persistence."""
        return {
            'tilt_level': self.tilt_level,
            'tilt_source': self.tilt_source,
            'nemesis': self.nemesis,
            'recent_losses': self.recent_losses,
            'losing_streak': self.losing_streak
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TiltState':
        """Deserialize from dictionary."""
        return cls(
            tilt_level=data.get('tilt_level', 0.0),
            tilt_source=data.get('tilt_source', ''),
            nemesis=data.get('nemesis'),
            recent_losses=data.get('recent_losses', []),
            losing_streak=data.get('losing_streak', 0)
        )

    def update_from_hand(self, outcome: str, amount: int, opponent: Optional[str] = None,
                         was_bad_beat: bool = False, was_bluff_called: bool = False,
                         big_blind: int = 100):
        """Update tilt state based on hand outcome.

        Args:
            outcome: 'won', 'lost', or 'folded'
            amount: Net chip change (negative for losses)
            opponent: Optional opponent who caused the outcome
            was_bad_beat: True if lost with a strong hand
            was_bluff_called: True if bluff was called and lost
            big_blind: Big blind size for relative thresholds (15 BB = big loss/win)
        """
        # Calculate relative threshold: 15 big blinds
        big_threshold = big_blind * 15

        if outcome == 'lost' or outcome == 'folded':
            # Increase tilt on losses (reduced increments for slower tilt buildup)
            if was_bad_beat:
                self.tilt_level = min(1.0, self.tilt_level + 0.15)
                self.tilt_source = 'bad_beat'
            elif was_bluff_called:
                self.tilt_level = min(1.0, self.tilt_level + 0.10)
                self.tilt_source = 'bluff_called'
            elif amount < -big_threshold:  # Big loss (relative to stakes)
                self.tilt_level = min(1.0, self.tilt_level + 0.10)
                self.tilt_source = 'big_loss'
            else:
                self.tilt_level = min(1.0, self.tilt_level + 0.02)

            self.losing_streak += 1
            if self.losing_streak >= 3:
                self.tilt_source = 'losing_streak'

            if opponent:
                self.nemesis = opponent

            self.recent_losses.append({
                'amount': amount,
                'opponent': opponent,
                'was_bad_beat': was_bad_beat
            })
            # Keep only last 5 losses
            self.recent_losses = self.recent_losses[-5:]

        elif outcome == 'won':
            # Winning reduces tilt
            self.tilt_level = max(0.0, self.tilt_level - 0.10)
            self.losing_streak = 0
            if amount > big_threshold:  # Big win provides more relief (relative to stakes)
                self.tilt_level = max(0.0, self.tilt_level - 0.15)

    def decay(self, amount: float = 0.05):
        """Natural tilt decay over time (2.5x faster than before for quicker recovery)."""
        self.tilt_level = max(0.0, self.tilt_level - amount)

    def get_tilt_category(self) -> str:
        """Get tilt severity category."""
        if self.tilt_level >= 0.7:
            return 'severe'
        elif self.tilt_level >= 0.4:
            return 'moderate'
        elif self.tilt_level >= 0.2:
            return 'mild'
        return 'none'

    def apply_pressure_event(self, event_name: str, opponent: Optional[str] = None):
        """Apply a pressure event to tilt state.

        Maps pressure detector events to tilt changes.
        Values tuned to prevent reaching full tilt too easily.
        """
        # Events that INCREASE tilt (reduced for slower tilt buildup)
        tilt_increases = {
            'bad_beat': 0.15,           # Lost with strong hand
            'bluff_called': 0.10,       # Your bluff failed
            'big_loss': 0.10,           # Lost a big pot
            'rivalry_trigger': 0.05,    # Trash talked / taunted
            'fold_under_pressure': 0.02,  # Pressured into folding
            # Equity-based events
            'got_sucked_out': 0.20,     # Was ahead but lost - very tilting
            # Heads-up events
            'headsup_loss': 0.08,       # Lost heads-up pot
        }

        # Events that DECREASE tilt (adjusted for better recovery)
        tilt_decreases = {
            'successful_bluff': 0.12,   # Successfully bluffed
            'big_win': 0.20,            # Won big pot
            'win': 0.10,                # Any win
            'eliminated_opponent': 0.20,  # Knocked someone out (big relief)
            'friendly_chat': 0.05,      # Friendly interaction
            # Equity-based events
            'suckout': 0.08,            # Lucky win - came from behind
            'cooler': 0.00,             # Unavoidable loss - no tilt change
            # Heads-up events
            'headsup_win': 0.05,        # Won heads-up pot
        }

        if event_name in tilt_increases:
            amount = tilt_increases[event_name]
            self.tilt_level = min(1.0, self.tilt_level + amount)
            self.tilt_source = event_name
            if opponent:
                self.nemesis = opponent

        elif event_name in tilt_decreases:
            amount = tilt_decreases[event_name]
            self.tilt_level = max(0.0, self.tilt_level - amount)
            if event_name in ('big_win', 'win', 'successful_bluff'):
                self.losing_streak = 0


class TiltPromptModifier:
    """Modifies AI prompts based on tilt state."""

    def __init__(self, tilt_state: TiltState):
        self.tilt_state = tilt_state

    def modify_prompt(self, base_prompt: str) -> str:
        """Apply all tilt effects to the prompt."""
        if self.tilt_state.tilt_level < 0.2:
            return base_prompt  # Not tilted enough to affect

        modified = base_prompt

        # 1. Remove strategic advice (information degradation)
        if self.tilt_state.tilt_level >= 0.4:
            modified = self._degrade_information(modified)

        # 2. Inject intrusive thoughts
        modified = self._inject_intrusive_thoughts(modified)

        # 3. Add tilted strategy advice
        if self.tilt_state.tilt_level >= 0.3:
            modified = self._add_tilted_strategy(modified)

        return modified

    def _degrade_information(self, prompt: str) -> str:
        """Remove or obscure strategic advice based on tilt level."""
        modified = prompt

        # At severe tilt, replace entire strategic section
        if self.tilt_state.tilt_level >= 0.7:
            # Replace pot odds guidance with dismissive advice
            modified = modified.replace(
                "Consider the pot odds, the amount of money in the pot, and how much you would have to risk.",
                "Don't overthink this."
            )
            # Remove all strategic phrases completely (including surrounding context)
            for phrase in STRATEGIC_PHRASES_TO_REMOVE:
                # Try with common surrounding patterns
                for pattern in [
                    f"{phrase}, and ",
                    f", and {phrase}",
                    f"{phrase}. ",
                    f" {phrase}",
                    phrase,
                ]:
                    modified = modified.replace(pattern, " ")
                    modified = modified.replace(pattern.lower(), " ")
        else:
            # Moderate tilt: just remove the phrases
            for phrase in STRATEGIC_PHRASES_TO_REMOVE:
                modified = modified.replace(phrase, "")
                modified = modified.replace(phrase.lower(), "")

        return self._cleanup_whitespace_and_punctuation(modified)

    def _cleanup_whitespace_and_punctuation(self, text: str) -> str:
        """Clean up double spaces and orphaned punctuation after modifications."""
        text = re.sub(r'\s+', ' ', text)  # Collapse whitespace
        text = re.sub(r'\s+([,.])', r'\1', text)  # Remove space before punctuation
        text = re.sub(r'([,.])\s*\1', r'\1', text)  # Remove duplicate punctuation
        text = re.sub(r',\s*\.', '.', text)  # Fix ", ." to "."
        return text

    def _inject_intrusive_thoughts(self, prompt: str) -> str:
        """Add intrusive thoughts based on tilt source."""
        thoughts = []

        # Get thoughts based on tilt source
        source = self.tilt_state.tilt_source or 'big_loss'
        if source in INTRUSIVE_THOUGHTS:
            # Number of thoughts scales with tilt level
            num_thoughts = 1 if self.tilt_state.tilt_level < 0.5 else 2
            available_thoughts = INTRUSIVE_THOUGHTS[source]
            thoughts.extend(random.sample(available_thoughts, min(num_thoughts, len(available_thoughts))))

        # Add revenge thoughts if there's a nemesis
        if self.tilt_state.nemesis and self.tilt_state.tilt_level >= 0.5:
            revenge_thoughts = INTRUSIVE_THOUGHTS['revenge']
            thought = random.choice(revenge_thoughts).format(nemesis=self.tilt_state.nemesis)
            thoughts.append(thought)

        if not thoughts:
            return prompt

        # Format the intrusive thoughts
        thought_block = "\n\n[What's running through your mind: " + " ".join(thoughts) + "]\n"

        # Insert before the "What is your move" question if present
        if "What is your move" in prompt:
            return prompt.replace("What is your move", thought_block + "What is your move")
        else:
            return prompt + thought_block

    def _add_tilted_strategy(self, prompt: str) -> str:
        """Replace or add tilted strategy advice."""
        category = self.tilt_state.get_tilt_category()
        if category == 'none':
            return prompt

        tilted_advice = TILTED_STRATEGY.get(category, TILTED_STRATEGY['mild'])

        # Add the tilted strategy as a new section
        advice_block = f"\n[Current mindset: {tilted_advice}]\n"

        return prompt + advice_block

    def get_info_to_hide(self) -> List[str]:
        """Get list of information types to hide/obscure at current tilt level.

        Returns list of keys that should be hidden or simplified in game state.
        """
        hidden = []

        if self.tilt_state.tilt_level >= 0.5:
            hidden.append('pot_odds')  # Don't calculate pot odds for them

        if self.tilt_state.tilt_level >= 0.7:
            hidden.append('opponent_stacks')  # Obscure exact opponent chip counts
            hidden.append('position_advice')  # Remove position-based advice

        return hidden
