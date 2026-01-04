"""
Chattiness Manager for AI poker players.
Determines when players should speak based on personality traits and context.
"""
import random
import logging
from typing import Dict, Optional, List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ConversationContext:
    """Tracks conversation flow at the table."""
    turns_since_last_spoke: Dict[str, int] = field(default_factory=dict)
    last_speaker: Optional[str] = None
    consecutive_silent_turns: int = 0
    recent_speakers: List[str] = field(default_factory=list)
    recent_messages: List[Dict] = field(default_factory=list)  # Recent chat messages
    addressed_players: Dict[str, int] = field(default_factory=dict)  # Player -> turns since addressed

    def update(self, player_name: str, did_speak: bool):
        """Update conversation tracking after a player's turn."""
        if did_speak:
            self.last_speaker = player_name
            self.consecutive_silent_turns = 0
            self.turns_since_last_spoke[player_name] = 0
            self.recent_speakers.append(player_name)
            if len(self.recent_speakers) > 5:
                self.recent_speakers.pop(0)
        else:
            self.consecutive_silent_turns += 1

        # Increment silence counter for all players
        for name in self.turns_since_last_spoke:
            if name != player_name or not did_speak:
                self.turns_since_last_spoke[name] += 1

        # Increment addressed counter (decays over turns)
        for name in list(self.addressed_players.keys()):
            self.addressed_players[name] += 1
            if self.addressed_players[name] > 3:  # Forget after 3 turns
                del self.addressed_players[name]

    def record_message(self, sender: str, message: str, all_player_names: List[str]):
        """Record a chat message and check for player mentions."""
        self.recent_messages.append({
            'sender': sender,
            'message': message
        })
        # Keep only last 10 messages
        if len(self.recent_messages) > 10:
            self.recent_messages.pop(0)

        # Check if any player names are mentioned in the message
        message_lower = message.lower()
        for player_name in all_player_names:
            # Check for full name or first name
            name_parts = player_name.lower().split()
            if player_name.lower() in message_lower or \
               (name_parts and name_parts[0] in message_lower):
                # Mark this player as addressed (reset counter to 0)
                self.addressed_players[player_name] = 0
                logger.debug(f"Player {player_name} was addressed in message: {message[:50]}...")

    def was_addressed(self, player_name: str) -> bool:
        """Check if player was recently addressed by name."""
        return player_name in self.addressed_players


class ChattinessManager:
    """Manages speaking probability for AI players based on traits and context."""
    
    # Base modifiers for different situations
    CONTEXT_MODIFIERS = {
        'just_won_big': 0.3,      # Winners tend to talk
        'just_lost_big': -0.2,    # Losers might go quiet
        'big_pot': 0.2,           # Big pots generate excitement
        'all_in': 0.4,            # All-ins are dramatic moments
        'bluffing': -0.1,         # Might stay quiet when bluffing
        'strong_hand': 0.1,       # Confidence breeds conversation
        'weak_hand': -0.1,        # Might be quieter with bad cards
        'addressed_directly': 0.5, # Almost always respond when spoken to
        'long_silence': 0.2,      # Break awkward silences
        'just_joined': 0.3,       # New players often announce themselves
        'heads_up': 0.2,          # More talk in 1v1 situations
        'multi_way_pot': -0.1,    # Less talk with many players
        'showdown': 0.3,          # Showdowns prompt reactions
    }
    
    # Personality-specific overrides
    PERSONALITY_ADJUSTMENTS = {
        'Gordon Ramsay': {'min_probability': 0.7, 'multiplier': 1.2},
        'Eeyore': {'max_probability': 0.4, 'multiplier': 0.5},
        'Silent Bob': {'max_probability': 0.1, 'multiplier': 0.1},
        'Donald Trump': {'min_probability': 0.8, 'multiplier': 1.3},
        'Bob Ross': {'base_boost': 0.2, 'multiplier': 1.1},
        'Batman': {'max_probability': 0.5, 'multiplier': 0.7},
    }
    
    def __init__(self):
        self.conversation_context = ConversationContext()
        self._last_decisions = {}  # Track decisions for testing
    
    def should_speak(self, player_name: str, chattiness: float, 
                    game_context: Optional[Dict] = None) -> bool:
        """
        Determine if a player should speak this turn.
        
        Args:
            player_name: Name of the player
            chattiness: Base chattiness trait (0.0-1.0)
            game_context: Current game situation
            
        Returns:
            bool: True if player should speak
        """
        game_context = game_context or {}
        
        # Calculate probability
        probability = self.calculate_speaking_probability(
            player_name, chattiness, game_context
        )
        
        # Make decision
        should_speak = random.random() < probability
        
        # Track for debugging/testing
        self._last_decisions[player_name] = {
            'chattiness': chattiness,
            'probability': probability,
            'spoke': should_speak,
            'context': game_context.copy()
        }
        
        # Update conversation tracking
        self.conversation_context.update(player_name, should_speak)
        
        logger.debug(f"{player_name} (chattiness={chattiness:.2f}): "
                    f"probability={probability:.2f}, speaking={should_speak}")
        
        return should_speak
    
    def calculate_speaking_probability(self, player_name: str, 
                                     base_chattiness: float,
                                     context: Dict) -> float:
        """
        Calculate the probability of speaking based on all factors.
        
        Args:
            player_name: Name of the player
            base_chattiness: Base chattiness trait (0.0-1.0)
            context: Game context dictionary
            
        Returns:
            float: Probability of speaking (0.0-1.0)
        """
        # Special case for true silence (0.0 = mime/silent character)
        if base_chattiness == 0.0:
            # Mimes can still gesture on dramatic moments
            if context.get('all_in', False) or context.get('showdown', False):
                return 0.5  # 50% chance to make gestures on big moments
            return 0.0  # Otherwise truly silent

        # Use exponential curve with lower base for more realistic chattiness
        # 0.1 -> 12%, 0.3 -> 20%, 0.5 -> 31%, 0.7 -> 45%, 0.9 -> 61%
        probability = 0.10 + (base_chattiness ** 1.5) * 0.6

        # Apply contextual modifiers with a cap to prevent stacking abuse
        total_modifier = sum(
            modifier for condition, modifier in self.CONTEXT_MODIFIERS.items()
            if context.get(condition, False)
        )
        capped_modifier = min(total_modifier, 0.3)  # Cap at +30%
        probability += capped_modifier
        if total_modifier > 0:
            logger.debug(f"Applied modifiers: {total_modifier:+.2f} (capped to {capped_modifier:+.2f})")

        # Rate limiting: penalize back-to-back speaking
        player_silence = self.conversation_context.turns_since_last_spoke.get(
            player_name, 99  # Default to "long time" for new players
        )
        if player_silence < 2:
            probability *= 0.3  # Heavy penalty for speaking again immediately
            logger.debug(f"Applied back-to-back penalty: *0.3 (silence={player_silence})")
        elif player_silence < 3:
            probability *= 0.6  # Moderate penalty
            logger.debug(f"Applied recent-speech penalty: *0.6 (silence={player_silence})")

        # Small bonus for breaking table-wide silence (keeps some social flow)
        if self.conversation_context.consecutive_silent_turns > 3:
            probability += 0.1  # Break extended table-wide silence
            logger.debug("Applied silence-breaker bonus: +0.1")
        
        # Apply personality-specific adjustments
        if player_name in self.PERSONALITY_ADJUSTMENTS:
            adjustments = self.PERSONALITY_ADJUSTMENTS[player_name]
            
            if 'base_boost' in adjustments:
                probability += adjustments['base_boost']
            
            if 'multiplier' in adjustments:
                probability *= adjustments['multiplier']
            
            if 'min_probability' in adjustments:
                probability = max(probability, adjustments['min_probability'])
            
            if 'max_probability' in adjustments:
                probability = min(probability, adjustments['max_probability'])
        
        # Clamp to valid range
        return max(0.0, min(1.0, probability))
    
    def get_speaking_context(self, player_name: str) -> Dict:
        """
        Get contextual information about speaking patterns.
        
        Args:
            player_name: Name of the player
            
        Returns:
            Dict: Context about conversation flow
        """
        return {
            'turns_since_spoke': self.conversation_context.turns_since_last_spoke.get(
                player_name, 0
            ),
            'was_last_speaker': self.conversation_context.last_speaker == player_name,
            'table_silent_turns': self.conversation_context.consecutive_silent_turns,
            'recent_speakers': self.conversation_context.recent_speakers.copy(),
            'was_addressed': self.conversation_context.was_addressed(player_name)
        }
    
    def get_last_decision(self, player_name: str) -> Optional[Dict]:
        """Get the last speaking decision for a player (for debugging)."""
        return self._last_decisions.get(player_name)
    
    def reset_conversation(self):
        """Reset conversation tracking (for new game/hand)."""
        self.conversation_context = ConversationContext()
        self._last_decisions = {}

    def record_chat_message(self, sender: str, message: str, all_player_names: List[str]):
        """
        Record a chat message and detect player mentions.

        This should be called when a player sends a chat message to track
        who is being addressed. AI players who are mentioned will have
        increased probability of responding.

        Args:
            sender: Name of the message sender
            message: The chat message content
            all_player_names: List of all player names in the game
        """
        self.conversation_context.record_message(sender, message, all_player_names)
    
    def suggest_speaking_style(self, player_name: str, probability: float) -> str:
        """
        Suggest how a player might speak based on probability.
        
        Args:
            player_name: Name of the player
            probability: Speaking probability
            
        Returns:
            str: Suggestion for speaking style
        """
        if probability == 0.0:
            return "Silent character: use only *gestures* and *actions*, no spoken words"
        elif probability < 0.2:
            return "If speaking, keep it very brief: '...', 'Hmm.', or just gestures"
        elif probability < 0.4:
            return "If speaking, be concise: short phrases or reactions"
        elif probability < 0.6:
            return "Moderate speech: normal table talk"
        elif probability < 0.8:
            return "Feel free to express yourself: full sentences and reactions"
        else:
            return "Very chatty: elaborate responses, multiple sentences"