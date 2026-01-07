"""
Decision Analyzer for AI Player Quality Monitoring.

Analyzes AI decisions inline (called after every decision) to track
decision quality and difficulty metrics without storing full prompts.
"""

import json
import time
import logging
from dataclasses import dataclass, asdict
from typing import Optional, List

logger = logging.getLogger(__name__)

# Import equity calculator - gracefully degrade if not available
try:
    from poker.equity_calculator import EquityCalculator, EVAL7_AVAILABLE
except ImportError:
    EVAL7_AVAILABLE = False
    EquityCalculator = None


@dataclass
class DecisionAnalysis:
    """Analysis result for a single AI decision."""

    # Identity
    game_id: str
    player_name: str
    hand_number: Optional[int] = None
    phase: Optional[str] = None
    request_id: Optional[str] = None
    capture_id: Optional[int] = None

    # Game state
    pot_total: int = 0
    cost_to_call: int = 0
    player_stack: int = 0
    num_opponents: int = 1
    player_hand: Optional[str] = None  # JSON string
    community_cards: Optional[str] = None  # JSON string

    # Decision
    action_taken: Optional[str] = None
    raise_amount: Optional[int] = None

    # Equity analysis
    equity: Optional[float] = None
    required_equity: float = 0
    ev_call: Optional[float] = None

    # Quality
    optimal_action: Optional[str] = None
    decision_quality: str = "unknown"
    ev_lost: float = 0

    # Hand strength
    hand_rank: Optional[int] = None
    relative_strength: Optional[float] = None

    # Metadata
    analyzer_version: str = "1.0"
    processing_time_ms: Optional[int] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for persistence."""
        return asdict(self)


class DecisionAnalyzer:
    """
    Analyzes AI decisions inline (called after every decision).

    Usage:
        analyzer = DecisionAnalyzer()
        analysis = analyzer.analyze(
            game_id="game_123",
            player_name="Batman",
            hand_number=5,
            phase="FLOP",
            player_hand=["As", "Kd"],
            community_cards=["Jh", "2d", "5s"],
            pot_total=100,
            cost_to_call=20,
            player_stack=500,
            num_opponents=2,
            action_taken="call",
        )
        # analysis.decision_quality = "correct" or "mistake"
        # analysis.ev_lost = 0 if correct, else EV difference
    """

    VERSION = "1.0"

    def __init__(self, iterations: int = 2000):
        """
        Initialize the analyzer.

        Args:
            iterations: Monte Carlo iterations for equity calculation.
                       Lower = faster but less accurate. 2000 ≈ 20ms.
        """
        self.iterations = iterations
        self._calculator = None

    @property
    def calculator(self):
        """Lazy-load the equity calculator."""
        if self._calculator is None and EVAL7_AVAILABLE:
            self._calculator = EquityCalculator(self.iterations)
        return self._calculator

    def analyze(
        self,
        game_id: str,
        player_name: str,
        hand_number: Optional[int],
        phase: Optional[str],
        player_hand: List[str],
        community_cards: List[str],
        pot_total: int,
        cost_to_call: int,
        player_stack: int,
        num_opponents: int,
        action_taken: str,
        raise_amount: Optional[int] = None,
        request_id: Optional[str] = None,
        capture_id: Optional[int] = None,
    ) -> DecisionAnalysis:
        """
        Analyze a decision and return analysis result.

        Args:
            game_id: Game identifier
            player_name: AI player name
            hand_number: Current hand number
            phase: Game phase (PRE_FLOP, FLOP, TURN, RIVER)
            player_hand: List of hole cards as strings (e.g., ["As", "Kd"])
            community_cards: List of community cards as strings
            pot_total: Total pot size
            cost_to_call: Amount needed to call
            player_stack: Player's remaining chips
            num_opponents: Number of opponents still in hand
            action_taken: The action the AI chose
            raise_amount: Amount raised (if action is raise)
            request_id: Link to api_usage table
            capture_id: Link to prompt_captures table

        Returns:
            DecisionAnalysis with equity and quality assessment
        """
        start_time = time.time()

        analysis = DecisionAnalysis(
            game_id=game_id,
            player_name=player_name,
            hand_number=hand_number,
            phase=phase,
            request_id=request_id,
            capture_id=capture_id,
            pot_total=pot_total,
            cost_to_call=cost_to_call,
            player_stack=player_stack,
            num_opponents=num_opponents,
            player_hand=json.dumps(player_hand) if player_hand else None,
            community_cards=json.dumps(community_cards) if community_cards else None,
            action_taken=action_taken,
            raise_amount=raise_amount,
            analyzer_version=self.VERSION,
        )

        # Calculate equity if we have cards and calculator
        if player_hand and self.calculator and num_opponents > 0:
            try:
                # Calculate equity vs random opponent hands using Monte Carlo
                analysis.equity = self._calculate_equity_vs_random(
                    player_hand, community_cards or [], num_opponents
                )
            except Exception as e:
                logger.debug(f"Equity calculation failed: {e}")

        # Calculate required equity and EV
        if cost_to_call > 0 and pot_total > 0:
            analysis.required_equity = cost_to_call / (pot_total + cost_to_call)
            if analysis.equity is not None:
                # EV(call) = (equity * pot) - ((1-equity) * call_cost)
                analysis.ev_call = (analysis.equity * pot_total) - (
                    (1 - analysis.equity) * cost_to_call
                )
        else:
            # Free check - no cost to see more cards
            analysis.required_equity = 0
            analysis.ev_call = 0

        # Evaluate decision quality
        self._evaluate_quality(analysis)

        analysis.processing_time_ms = int((time.time() - start_time) * 1000)
        return analysis

    def _calculate_equity_vs_random(
        self,
        player_hand: List[str],
        community_cards: List[str],
        num_opponents: int
    ) -> Optional[float]:
        """Calculate equity vs random opponent hands using Monte Carlo.

        Args:
            player_hand: Hero's hole cards as strings ['Ah', 'Kd']
            community_cards: Board cards as strings
            num_opponents: Number of opponents to simulate

        Returns:
            Win probability (0.0-1.0) or None if calculation fails
        """
        try:
            import eval7
            import random

            # Parse hero's hand
            hero_hand = [eval7.Card(c) for c in player_hand]
            board = [eval7.Card(c) for c in community_cards] if community_cards else []

            # Build deck excluding known cards
            all_known = set(hero_hand + board)
            deck = [c for c in eval7.Deck().cards if c not in all_known]

            wins = 0
            iterations = self.iterations

            for _ in range(iterations):
                # Shuffle remaining deck
                random.shuffle(deck)
                deck_idx = 0

                # Deal random hands to opponents
                opponent_hands = []
                for _ in range(num_opponents):
                    opp_hand = [deck[deck_idx], deck[deck_idx + 1]]
                    opponent_hands.append(opp_hand)
                    deck_idx += 2

                # Deal remaining board cards
                cards_needed = 5 - len(board)
                sim_board = board + deck[deck_idx:deck_idx + cards_needed]

                # Evaluate hands
                hero_score = eval7.evaluate(hero_hand + sim_board)

                # Check if hero beats all opponents
                hero_wins = True
                for opp_hand in opponent_hands:
                    opp_score = eval7.evaluate(opp_hand + sim_board)
                    if opp_score > hero_score:  # Higher is better in eval7
                        hero_wins = False
                        break

                if hero_wins:
                    wins += 1

            return wins / iterations

        except Exception as e:
            logger.debug(f"Equity vs random calculation failed: {e}")
            return None

    def _evaluate_quality(self, analysis: DecisionAnalysis) -> None:
        """
        Evaluate decision quality based on EV.

        Sets optimal_action, decision_quality, and ev_lost on the analysis.
        """
        if analysis.ev_call is None:
            analysis.decision_quality = "unknown"
            return

        # Determine optimal action based on EV
        if analysis.ev_call > 0:
            analysis.optimal_action = "call"
        else:
            analysis.optimal_action = "fold"

        action = analysis.action_taken
        if action == "fold" and analysis.ev_call > 0:
            # Folded a +EV spot
            analysis.decision_quality = "mistake"
            analysis.ev_lost = analysis.ev_call
        elif action in ("call", "raise", "all_in") and analysis.ev_call < 0:
            # Called/raised a -EV spot
            analysis.decision_quality = "mistake"
            analysis.ev_lost = -analysis.ev_call
        else:
            analysis.decision_quality = "correct"
            analysis.ev_lost = 0


# Singleton instance for reuse
_analyzer_instance: Optional[DecisionAnalyzer] = None


def get_analyzer(iterations: int = 2000) -> DecisionAnalyzer:
    """Get or create the singleton analyzer instance."""
    global _analyzer_instance
    if _analyzer_instance is None:
        _analyzer_instance = DecisionAnalyzer(iterations)
    return _analyzer_instance
