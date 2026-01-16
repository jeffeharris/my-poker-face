"""
Opponent Modeling System.

Tracks opponent tendencies and memorable hands for AI learning.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional, Any

from ..config import (
    OPPONENT_SUMMARY_TOKENS,
    MEMORABLE_HAND_THRESHOLD,
    MIN_HANDS_FOR_STYLE_LABEL,
    MIN_HANDS_FOR_SUMMARY,
    VPIP_TIGHT_THRESHOLD,
    VPIP_LOOSE_THRESHOLD,
    VPIP_VERY_SELECTIVE,
    AGGRESSION_FACTOR_HIGH,
    AGGRESSION_FACTOR_VERY_HIGH,
    AGGRESSION_FACTOR_LOW,
)


@dataclass
class OpponentTendencies:
    """Statistical model of an opponent's play style."""
    hands_observed: int = 0

    # Core stats
    vpip: float = 0.5           # Voluntarily put in pot % (how often they enter pots)
    pfr: float = 0.5            # Pre-flop raise % (how often they raise pre-flop)
    aggression_factor: float = 1.0  # (bet+raise) / call ratio
    fold_to_cbet: float = 0.5   # Fold to continuation bet %
    bluff_frequency: float = 0.3    # Estimated bluff rate
    showdown_win_rate: float = 0.5  # Win rate at showdown

    # Trend tracking
    recent_trend: str = 'stable'    # 'tightening', 'loosening', 'stable'

    # Action counters (for calculating stats)
    _vpip_count: int = 0        # Hands where player voluntarily put money in pot
    _pfr_count: int = 0         # Hands where player raised pre-flop
    _bet_raise_count: int = 0   # Total bets and raises
    _call_count: int = 0        # Total calls
    _fold_to_cbet_count: int = 0
    _cbet_faced_count: int = 0
    _showdowns: int = 0
    _showdowns_won: int = 0

    # Per-hand tracking (reset each new hand)
    _vpip_this_hand: bool = False
    _pfr_this_hand: bool = False

    def update_from_action(self, action: str, phase: str, is_voluntary: bool = True, count_hand: bool = True):
        """Update stats based on observed action.

        Args:
            action: The action taken ('fold', 'check', 'call', 'raise', 'bet')
            phase: Game phase ('PRE_FLOP', 'FLOP', 'TURN', 'RIVER')
            is_voluntary: Whether this was a voluntary action (not forced blind)
            count_hand: Whether to increment hands_observed (only once per hand)
        """
        if count_hand:
            self.hands_observed += 1
            # Reset per-hand flags for new hand
            self._vpip_this_hand = False
            self._pfr_this_hand = False

        # Track VPIP (voluntary pot entry) - only count ONCE per hand
        if phase == 'PRE_FLOP' and is_voluntary and not self._vpip_this_hand:
            if action in ('call', 'raise', 'bet'):
                self._vpip_count += 1
                self._vpip_this_hand = True

        # Track PFR (pre-flop raise) - only count ONCE per hand
        if phase == 'PRE_FLOP' and action == 'raise' and not self._pfr_this_hand:
            self._pfr_count += 1
            self._pfr_this_hand = True

        # Track aggression
        if action in ('bet', 'raise'):
            self._bet_raise_count += 1
        elif action == 'call':
            self._call_count += 1

        # Recalculate stats
        self._recalculate_stats()

    def update_showdown(self, won: bool):
        """Update showdown statistics."""
        self._showdowns += 1
        if won:
            self._showdowns_won += 1
        self._recalculate_stats()

    def update_fold_to_cbet(self, folded: bool):
        """Update fold to continuation bet stats."""
        self._cbet_faced_count += 1
        if folded:
            self._fold_to_cbet_count += 1
        self._recalculate_stats()

    def _recalculate_stats(self):
        """Recalculate derived statistics."""
        if self.hands_observed > 0:
            self.vpip = self._vpip_count / max(self.hands_observed, 1)
            self.pfr = self._pfr_count / max(self.hands_observed, 1)

        total_actions = self._bet_raise_count + self._call_count
        if total_actions == 0:
            # No actions observed yet; use neutral default
            self.aggression_factor = 1.0
        elif self._call_count == 0:
            # All observed actions are bets/raises; treat as maximal aggression
            self.aggression_factor = float(self._bet_raise_count)
        else:
            self.aggression_factor = self._bet_raise_count / self._call_count

        if self._cbet_faced_count > 0:
            self.fold_to_cbet = self._fold_to_cbet_count / self._cbet_faced_count

        if self._showdowns > 0:
            self.showdown_win_rate = self._showdowns_won / self._showdowns

    def get_play_style_label(self) -> str:
        """Returns play style classification.

        Returns one of:
        - 'tight-aggressive' (TAG)
        - 'loose-aggressive' (LAG)
        - 'tight-passive' (Rock)
        - 'loose-passive' (Calling Station)
        - 'unknown'
        """
        if self.hands_observed < MIN_HANDS_FOR_STYLE_LABEL:
            return 'unknown'

        is_tight = self.vpip < VPIP_TIGHT_THRESHOLD
        is_aggressive = self.aggression_factor > AGGRESSION_FACTOR_HIGH

        if is_tight and is_aggressive:
            return 'tight-aggressive'
        elif not is_tight and is_aggressive:
            return 'loose-aggressive'
        elif is_tight and not is_aggressive:
            return 'tight-passive'
        else:
            return 'loose-passive'

    def get_summary(self) -> str:
        """Generate human-readable summary for AI prompts."""
        if self.hands_observed < MIN_HANDS_FOR_SUMMARY:
            return "Not enough data"

        style = self.get_play_style_label()
        parts = [f"{style}"]

        if self.vpip > VPIP_LOOSE_THRESHOLD:
            parts.append("plays many hands")
        elif self.vpip < VPIP_VERY_SELECTIVE:
            parts.append("very selective")

        if self.aggression_factor > AGGRESSION_FACTOR_VERY_HIGH:
            parts.append("very aggressive")
        elif self.aggression_factor < AGGRESSION_FACTOR_LOW:
            parts.append("passive")

        if self.bluff_frequency > 0.5:
            parts.append("bluffs often")
        elif self.bluff_frequency < 0.2:
            parts.append("rarely bluffs")

        if self.fold_to_cbet > 0.7:
            parts.append("folds to pressure")
        elif self.fold_to_cbet < 0.3:
            parts.append("calls often")

        return ", ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'hands_observed': self.hands_observed,
            'vpip': self.vpip,
            'pfr': self.pfr,
            'aggression_factor': self.aggression_factor,
            'fold_to_cbet': self.fold_to_cbet,
            'bluff_frequency': self.bluff_frequency,
            'showdown_win_rate': self.showdown_win_rate,
            'recent_trend': self.recent_trend,
            '_vpip_count': self._vpip_count,
            '_pfr_count': self._pfr_count,
            '_bet_raise_count': self._bet_raise_count,
            '_call_count': self._call_count,
            '_fold_to_cbet_count': self._fold_to_cbet_count,
            '_cbet_faced_count': self._cbet_faced_count,
            '_showdowns': self._showdowns,
            '_showdowns_won': self._showdowns_won
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OpponentTendencies':
        tendencies = cls(
            hands_observed=data.get('hands_observed', 0),
            vpip=data.get('vpip', 0.5),
            pfr=data.get('pfr', 0.5),
            aggression_factor=data.get('aggression_factor', 1.0),
            fold_to_cbet=data.get('fold_to_cbet', 0.5),
            bluff_frequency=data.get('bluff_frequency', 0.3),
            showdown_win_rate=data.get('showdown_win_rate', 0.5),
            recent_trend=data.get('recent_trend', 'stable')
        )
        tendencies._vpip_count = data.get('_vpip_count', 0)
        tendencies._pfr_count = data.get('_pfr_count', 0)
        tendencies._bet_raise_count = data.get('_bet_raise_count', 0)
        tendencies._call_count = data.get('_call_count', 0)
        tendencies._fold_to_cbet_count = data.get('_fold_to_cbet_count', 0)
        tendencies._cbet_faced_count = data.get('_cbet_faced_count', 0)
        tendencies._showdowns = data.get('_showdowns', 0)
        tendencies._showdowns_won = data.get('_showdowns_won', 0)
        return tendencies


@dataclass
class MemorableHand:
    """A specific hand worth remembering."""
    hand_id: int
    memory_type: str          # 'bluff_caught', 'hero_call', 'big_loss', 'bad_beat', etc.
    opponent_name: str
    impact_score: float       # 0-1, how memorable
    narrative: str            # AI-generated or template description
    hand_summary: str         # Brief summary of what happened
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'hand_id': self.hand_id,
            'memory_type': self.memory_type,
            'opponent_name': self.opponent_name,
            'impact_score': self.impact_score,
            'narrative': self.narrative,
            'hand_summary': self.hand_summary,
            'timestamp': self.timestamp.isoformat()
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MemorableHand':
        return cls(
            hand_id=data['hand_id'],
            memory_type=data['memory_type'],
            opponent_name=data['opponent_name'],
            impact_score=data['impact_score'],
            narrative=data['narrative'],
            hand_summary=data['hand_summary'],
            timestamp=datetime.fromisoformat(data['timestamp']) if isinstance(data['timestamp'], str) else data['timestamp']
        )


class OpponentModel:
    """Tracks observations about a specific opponent.

    Combines statistical tendencies with AI-generated narrative observations
    for richer opponent modeling.
    """

    def __init__(self, observer: str, opponent: str):
        self.observer = observer
        self.opponent = opponent
        self.tendencies = OpponentTendencies()
        self.memorable_hands: List[MemorableHand] = []
        self.narrative_observations: List[str] = []  # AI-generated insights about this opponent
        self._last_hand_counted: Optional[int] = None  # Track which hand we last counted

    def observe_action(self, action: str, phase: str, is_voluntary: bool = True, hand_number: int = None):
        """Record an observed action from this opponent."""
        # Only count hands_observed once per hand
        new_hand = hand_number is not None and hand_number != self._last_hand_counted
        if new_hand:
            self._last_hand_counted = hand_number
        self.tendencies.update_from_action(action, phase, is_voluntary, count_hand=new_hand)

    def observe_showdown(self, won: bool, bluffed: bool = False):
        """Record a showdown observation."""
        self.tendencies.update_showdown(won)
        if bluffed and not won:
            # Caught bluffing - update bluff frequency estimate
            current_bluffs = self.tendencies.bluff_frequency * self.tendencies._showdowns
            self.tendencies.bluff_frequency = (current_bluffs + 1) / max(self.tendencies._showdowns, 1)

    def observe_fold_to_cbet(self, folded: bool):
        """Record fold/call response to continuation bet."""
        self.tendencies.update_fold_to_cbet(folded)

    def add_narrative_observation(self, observation: str) -> None:
        """Add an AI-generated observation about this opponent.

        Keeps the most recent observations (up to 5) as a sliding window.
        These observations are included in prompts so the AI can remember
        and refine its understanding of opponents over time.

        Args:
            observation: A narrative insight about the opponent (e.g.,
                "Folds to aggression on scary boards", "Overvalues top pair")
        """
        if not observation or not observation.strip():
            return

        observation = observation.strip()

        # Avoid exact duplicates
        if observation in self.narrative_observations:
            return

        self.narrative_observations.append(observation)

        # Keep only most recent 5
        if len(self.narrative_observations) > 5:
            self.narrative_observations = self.narrative_observations[-5:]

    def get_narrative_observations_text(self) -> str:
        """Get narrative observations formatted for prompts.

        Returns a concise string suitable for injection into AI prompts.
        """
        if not self.narrative_observations:
            return ""

        # Return most recent observation for prompt efficiency
        return self.narrative_observations[-1]

    def add_memorable_hand(self, hand_id: int, memory_type: str,
                          impact_score: float, narrative: str, hand_summary: str):
        """Add a memorable hand if impact is high enough."""
        if impact_score >= MEMORABLE_HAND_THRESHOLD:
            self.memorable_hands.append(MemorableHand(
                hand_id=hand_id,
                memory_type=memory_type,
                opponent_name=self.opponent,
                impact_score=impact_score,
                narrative=narrative,
                hand_summary=hand_summary
            ))
            # Keep only most memorable hands
            self.memorable_hands.sort(key=lambda h: h.impact_score, reverse=True)
            self.memorable_hands = self.memorable_hands[:5]

    def get_prompt_summary(self, max_tokens: int = 100) -> str:
        """Generate summary for AI prompt.

        Combines statistical analysis with narrative observations for
        a richer opponent profile.
        """
        parts = [f"{self.opponent}: {self.tendencies.get_summary()}"]

        # Add narrative observation if available
        narrative = self.get_narrative_observations_text()
        if narrative:
            parts.append(f"Notes: {narrative}")

        # Add most memorable hand if any
        if self.memorable_hands:
            most_memorable = self.memorable_hands[0]
            parts.append(f"Remember: {most_memorable.narrative}")

        result = ". ".join(parts)

        # Rough token limit
        estimated_tokens = len(result) / 4
        if estimated_tokens > max_tokens:
            # Fall back to just style + observation
            if narrative:
                result = f"{self.opponent}: {self.tendencies.get_play_style_label()}. Notes: {narrative}"
            else:
                result = f"{self.opponent}: {self.tendencies.get_play_style_label()}"

        return result

    def get_recent_memorable_hands(self, limit: int = 3) -> List[MemorableHand]:
        """Get most recent memorable hands."""
        sorted_by_time = sorted(self.memorable_hands, key=lambda h: h.timestamp, reverse=True)
        return sorted_by_time[:limit]

    def to_dict(self) -> Dict[str, Any]:
        return {
            'observer': self.observer,
            'opponent': self.opponent,
            'tendencies': self.tendencies.to_dict(),
            'memorable_hands': [h.to_dict() for h in self.memorable_hands],
            'narrative_observations': self.narrative_observations
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OpponentModel':
        model = cls(observer=data['observer'], opponent=data['opponent'])
        model.tendencies = OpponentTendencies.from_dict(data.get('tendencies', {}))
        model.memorable_hands = [
            MemorableHand.from_dict(h) for h in data.get('memorable_hands', [])
        ]
        model.narrative_observations = data.get('narrative_observations', [])
        return model


class OpponentModelManager:
    """Manages opponent models for all AI players."""

    def __init__(self):
        # observer_name -> opponent_name -> OpponentModel
        self.models: Dict[str, Dict[str, OpponentModel]] = {}

    def get_model(self, observer: str, opponent: str) -> OpponentModel:
        """Get or create an opponent model."""
        if observer not in self.models:
            self.models[observer] = {}

        if opponent not in self.models[observer]:
            self.models[observer][opponent] = OpponentModel(observer, opponent)

        return self.models[observer][opponent]

    def observe_action(self, observer: str, opponent: str, action: str,
                      phase: str, is_voluntary: bool = True, hand_number: int = None):
        """Record an action observation."""
        if observer == opponent:
            return  # Don't model yourself
        model = self.get_model(observer, opponent)
        model.observe_action(action, phase, is_voluntary, hand_number=hand_number)

    def get_table_summary(self, observer: str, opponents: List[str],
                         max_tokens: int = OPPONENT_SUMMARY_TOKENS) -> str:
        """Get summary of all opponents at the table."""
        if observer not in self.models:
            return ""

        summaries = []
        tokens_per_opponent = max_tokens // max(len(opponents), 1)

        for opponent in opponents:
            if opponent in self.models[observer]:
                model = self.models[observer][opponent]
                if model.tendencies.hands_observed >= MIN_HANDS_FOR_SUMMARY:
                    summaries.append(model.get_prompt_summary(tokens_per_opponent))

        return "\n".join(summaries)

    def get_all_models_for_observer(self, observer: str) -> Dict[str, OpponentModel]:
        """Get all opponent models for an observer."""
        return self.models.get(observer, {})

    def to_dict(self) -> Dict[str, Any]:
        result = {}
        for observer, opponents in self.models.items():
            result[observer] = {
                opponent: model.to_dict()
                for opponent, model in opponents.items()
            }
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OpponentModelManager':
        manager = cls()
        for observer, opponents in data.items():
            manager.models[observer] = {
                opponent: OpponentModel.from_dict(model_data)
                for opponent, model_data in opponents.items()
            }
        return manager
