"""
Session Memory System.

Manages context that persists across hands within a game session.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any

from ..config import SESSION_MEMORY_HANDS, MEMORY_CONTEXT_TOKENS


@dataclass
class HandMemory:
    """Memory of a single hand that persists in session."""
    hand_number: int
    outcome: str              # 'won', 'lost', 'folded'
    pot_size: int
    amount_won_or_lost: int   # Positive for wins, negative for losses
    notable_events: List[str]  # ['caught Donald bluffing', 'hit flush on river']
    emotional_impact: float   # -1 to 1 (negative = bad, positive = good)
    timestamp: datetime = field(default_factory=datetime.now)

    def get_summary(self) -> str:
        """Get a brief summary of this hand."""
        if self.outcome == 'won':
            return f"Won ${self.amount_won_or_lost} (pot ${self.pot_size})"
        elif self.outcome == 'folded':
            return f"Folded (pot ${self.pot_size})"
        else:
            return f"Lost ${abs(self.amount_won_or_lost)} (pot ${self.pot_size})"

    def to_dict(self) -> Dict[str, Any]:
        return {
            'hand_number': self.hand_number,
            'outcome': self.outcome,
            'pot_size': self.pot_size,
            'amount_won_or_lost': self.amount_won_or_lost,
            'notable_events': self.notable_events,
            'emotional_impact': self.emotional_impact,
            'timestamp': self.timestamp.isoformat()
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'HandMemory':
        return cls(
            hand_number=data['hand_number'],
            outcome=data['outcome'],
            pot_size=data['pot_size'],
            amount_won_or_lost=data['amount_won_or_lost'],
            notable_events=data['notable_events'],
            emotional_impact=data['emotional_impact'],
            timestamp=datetime.fromisoformat(data['timestamp']) if isinstance(data['timestamp'], str) else data['timestamp']
        )


@dataclass
class SessionContext:
    """Accumulated context within a session."""
    hands_played: int = 0
    hands_won: int = 0
    total_winnings: int = 0       # Net change in chips
    biggest_pot_won: int = 0
    biggest_pot_lost: int = 0
    current_streak: str = 'neutral'  # 'winning', 'losing', 'neutral'
    streak_count: int = 0
    recent_observations: List[str] = field(default_factory=list)
    table_dynamics: str = 'normal'   # 'tight', 'loose', 'aggressive', 'passive', 'normal'

    def update_streak(self, won: bool):
        """Update the current streak based on hand outcome.

        Tracks consecutive wins or losses. The streak is reported in get_summary()
        only when streak_count >= 2, so single wins/losses don't show as streaks.
        """
        if won:
            if self.current_streak == 'winning':
                self.streak_count += 1
            else:
                self.current_streak = 'winning'
                self.streak_count = 1
        else:
            if self.current_streak == 'losing':
                self.streak_count += 1
            else:
                self.current_streak = 'losing'
                self.streak_count = 1

    def get_summary(self) -> str:
        """Get a text summary of the session context."""
        parts = []

        # Overall performance
        win_rate = (self.hands_won / self.hands_played * 100) if self.hands_played > 0 else 0
        parts.append(f"Session: {self.hands_won}/{self.hands_played} hands won ({win_rate:.0f}%)")

        # Net result
        if self.total_winnings > 0:
            parts.append(f"Up ${self.total_winnings}")
        elif self.total_winnings < 0:
            parts.append(f"Down ${abs(self.total_winnings)}")

        # Current streak
        if self.streak_count >= 2:
            parts.append(f"On a {self.streak_count}-hand {self.current_streak} streak")

        # Table dynamics
        if self.table_dynamics != 'normal':
            parts.append(f"Table is playing {self.table_dynamics}")

        return ". ".join(parts)


class SessionMemory:
    """Manages context that persists across hands within a session."""

    def __init__(self, player_name: str, max_hand_memory: int = SESSION_MEMORY_HANDS):
        self.player_name = player_name
        self.max_hand_memory = max_hand_memory
        self.hand_memories: List[HandMemory] = []
        self.context = SessionContext()

    def record_hand_outcome(self, hand_number: int, outcome: str, pot_size: int,
                           amount_won_or_lost: int, notable_events: List[str] = None) -> None:
        """Record the outcome of a completed hand.

        Args:
            hand_number: The hand number in the game
            outcome: 'won', 'lost', or 'folded'
            pot_size: Total pot size
            amount_won_or_lost: Net change for this player
            notable_events: List of notable things that happened
        """
        # Calculate emotional impact based on outcome
        emotional_impact = self._calculate_emotional_impact(outcome, pot_size, amount_won_or_lost)

        memory = HandMemory(
            hand_number=hand_number,
            outcome=outcome,
            pot_size=pot_size,
            amount_won_or_lost=amount_won_or_lost,
            notable_events=notable_events or [],
            emotional_impact=emotional_impact
        )

        self.hand_memories.append(memory)

        # Trim to max size
        if len(self.hand_memories) > self.max_hand_memory:
            self.hand_memories = self.hand_memories[-self.max_hand_memory:]

        # Update session context
        self.context.hands_played += 1
        if outcome == 'won':
            self.context.hands_won += 1
            self.context.update_streak(won=True)
            if pot_size > self.context.biggest_pot_won:
                self.context.biggest_pot_won = pot_size
        elif outcome == 'lost':
            self.context.update_streak(won=False)
            if pot_size > self.context.biggest_pot_lost:
                self.context.biggest_pot_lost = pot_size
        # Folded doesn't affect streak

        self.context.total_winnings += amount_won_or_lost

    def _calculate_emotional_impact(self, outcome: str, pot_size: int,
                                    amount_won_or_lost: int) -> float:
        """Calculate emotional impact of a hand (-1 to 1).

        Big wins = closer to 1
        Big losses = closer to -1
        Folded/small pots = closer to 0
        """
        if outcome == 'folded':
            return -0.1  # Slight negative (missed opportunity)

        # Base impact from outcome
        if outcome == 'won':
            base_impact = 0.3
        else:
            base_impact = -0.3

        # Scale by pot size (bigger pots = bigger impact)
        # Assume average pot is around 500
        pot_multiplier = min(pot_size / 500, 2.0)
        impact = base_impact * pot_multiplier

        # Cap at -1 to 1
        return max(-1.0, min(1.0, impact))

    def add_observation(self, observation: str) -> None:
        """Add an observation about the table or opponents."""
        self.context.recent_observations.append(observation)
        # Keep last 5 observations
        if len(self.context.recent_observations) > 5:
            self.context.recent_observations = self.context.recent_observations[-5:]

    def update_table_dynamics(self, assessment: str) -> None:
        """Update the perceived table dynamics."""
        self.context.table_dynamics = assessment

    def get_context_for_prompt(self, max_tokens: int = MEMORY_CONTEXT_TOKENS) -> str:
        """Generate context string for injection into AI prompts.

        Args:
            max_tokens: Approximate maximum tokens to use

        Returns:
            Formatted string with session context
        """
        parts = []

        # Session overview
        parts.append(self.context.get_summary())

        # Recent hands summary
        if self.hand_memories:
            recent = self.hand_memories[-3:]  # Last 3 hands
            hand_summaries = [f"Hand {h.hand_number}: {h.get_summary()}" for h in recent]
            parts.append("Recent: " + " | ".join(hand_summaries))

        # Notable events from recent hands
        notable = []
        for hand in self.hand_memories[-5:]:
            notable.extend(hand.notable_events)
        if notable:
            parts.append("Notable: " + ", ".join(notable[-3:]))

        # Observations
        if self.context.recent_observations:
            parts.append("Observed: " + "; ".join(self.context.recent_observations[-2:]))

        result = "\n".join(parts)

        # Rough token estimation (4 chars ~= 1 token) and trim if needed
        estimated_tokens = len(result) / 4
        if estimated_tokens > max_tokens:
            # Trim by removing older information
            result = self.context.get_summary()
            if self.hand_memories:
                last_hand = self.hand_memories[-1]
                result += f"\nLast hand: {last_hand.get_summary()}"

        return result

    def get_recent_outcomes(self, limit: int = 5) -> List[str]:
        """Get list of recent outcome strings."""
        return [h.outcome for h in self.hand_memories[-limit:]]

    def clear_for_new_session(self) -> None:
        """Clear all memory for a new session."""
        self.hand_memories = []
        self.context = SessionContext()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for persistence."""
        return {
            'player_name': self.player_name,
            'max_hand_memory': self.max_hand_memory,
            'hand_memories': [h.to_dict() for h in self.hand_memories],
            'context': {
                'hands_played': self.context.hands_played,
                'hands_won': self.context.hands_won,
                'total_winnings': self.context.total_winnings,
                'biggest_pot_won': self.context.biggest_pot_won,
                'biggest_pot_lost': self.context.biggest_pot_lost,
                'current_streak': self.context.current_streak,
                'streak_count': self.context.streak_count,
                'recent_observations': self.context.recent_observations,
                'table_dynamics': self.context.table_dynamics
            }
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SessionMemory':
        """Deserialize from dictionary."""
        memory = cls(
            player_name=data['player_name'],
            max_hand_memory=data.get('max_hand_memory', SESSION_MEMORY_HANDS)
        )
        memory.hand_memories = [HandMemory.from_dict(h) for h in data.get('hand_memories', [])]

        ctx_data = data.get('context', {})
        memory.context.hands_played = ctx_data.get('hands_played', 0)
        memory.context.hands_won = ctx_data.get('hands_won', 0)
        memory.context.total_winnings = ctx_data.get('total_winnings', 0)
        memory.context.biggest_pot_won = ctx_data.get('biggest_pot_won', 0)
        memory.context.biggest_pot_lost = ctx_data.get('biggest_pot_lost', 0)
        memory.context.current_streak = ctx_data.get('current_streak', 'neutral')
        memory.context.streak_count = ctx_data.get('streak_count', 0)
        memory.context.recent_observations = ctx_data.get('recent_observations', [])
        memory.context.table_dynamics = ctx_data.get('table_dynamics', 'normal')

        return memory
