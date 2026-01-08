"""
Equity Calculator for Poker Hands.

Uses eval7 for fast equity calculation to identify dramatic moments
during showdowns and gameplay.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import logging

try:
    import eval7
    EVAL7_AVAILABLE = True
except ImportError:
    EVAL7_AVAILABLE = False
    eval7 = None

logger = logging.getLogger(__name__)


@dataclass
class EquityResult:
    """Equity calculation result for all players."""
    equities: Dict[str, float]  # player_name -> win probability (0-1)
    tie_probability: float
    sample_count: int


@dataclass
class SwingEvent:
    """Represents a dramatic equity swing."""
    player_name: str
    card_revealed: str
    equity_before: float
    equity_after: float
    delta: float
    is_dramatic: bool  # True if swing > threshold

    @property
    def direction(self) -> str:
        return "gained" if self.delta > 0 else "lost"

    @property
    def description(self) -> str:
        pct_before = int(self.equity_before * 100)
        pct_after = int(self.equity_after * 100)
        pct_delta = int(abs(self.delta) * 100)
        return f"{self.player_name}: {pct_before}% → {pct_after}% ({'+' if self.delta > 0 else '-'}{pct_delta}%)"


class EquityCalculator:
    """
    Calculate poker hand equity and detect dramatic moments.

    Usage:
        calc = EquityCalculator()

        # Calculate equity for all players
        result = calc.calculate_equity(
            players_hands={'Batman': ['As', 'Kd'], 'Snoop': ['Qh', 'Qc']},
            board=['Jh', '2d', '5s']
        )
        # result.equities = {'Batman': 0.43, 'Snoop': 0.57}

        # Detect swings between two equity states
        swings = calc.detect_swings(before_equities, after_equities, card='Ah')
    """

    # Thresholds for drama detection
    DRAMATIC_SWING_THRESHOLD = 0.25  # 25% equity change
    NOTABLE_SWING_THRESHOLD = 0.15   # 15% equity change
    CLOSE_EQUITY_THRESHOLD = 0.10    # Within 10% = close/tense

    def __init__(self, monte_carlo_iterations: int = 10000):
        """
        Initialize the equity calculator.

        Args:
            monte_carlo_iterations: Number of iterations for Monte Carlo simulation.
                                   Higher = more accurate but slower.
        """
        self.iterations = monte_carlo_iterations

        if not EVAL7_AVAILABLE:
            logger.warning("eval7 not available - equity calculations will be disabled")

    def _parse_card(self, card_str: str) -> 'eval7.Card':
        """
        Convert card string to eval7.Card.

        Accepts formats:
            - 'As', 'Kd', 'Qh', 'Jc', '10s', 'Ts', '2h'
            - {'rank': 'A', 'suit': 'Spades'} dict format
        """
        if isinstance(card_str, dict):
            rank = card_str.get('rank', '')
            suit = card_str.get('suit', '')[0].lower()  # 'Spades' -> 's'
            # Handle 10 -> T
            if rank == '10':
                rank = 'T'
            card_str = f"{rank}{suit}"
        else:
            # Handle '10s' -> 'Ts'
            if card_str.startswith('10'):
                card_str = 'T' + card_str[2:]

        return eval7.Card(card_str)

    def _parse_cards(self, cards: List) -> List['eval7.Card']:
        """Convert list of card strings/dicts to eval7.Card objects."""
        return [self._parse_card(c) for c in cards]

    def calculate_equity(
        self,
        players_hands: Dict[str, List],
        board: Optional[List] = None,
        iterations: Optional[int] = None
    ) -> Optional[EquityResult]:
        """
        Calculate equity for all players given their hands and the board.

        Args:
            players_hands: Dict mapping player_name to list of hole cards.
                          Cards can be strings ('As', 'Kd') or dicts ({'rank': 'A', 'suit': 'Spades'})
            board: List of community cards (0-5 cards)
            iterations: Override default Monte Carlo iterations

        Returns:
            EquityResult with each player's win probability, or None if eval7 unavailable
        """
        if not EVAL7_AVAILABLE:
            return None

        if not players_hands:
            return None

        iterations = iterations or self.iterations
        board = board or []

        try:
            # Parse all hands
            parsed_hands = {
                name: self._parse_cards(cards)
                for name, cards in players_hands.items()
            }
            parsed_board = self._parse_cards(board)

            player_names = list(parsed_hands.keys())
            hands_list = [parsed_hands[name] for name in player_names]

            # If board is complete (5 cards), calculate exact winner
            if len(parsed_board) == 5:
                return self._calculate_exact_equity(player_names, hands_list, parsed_board)

            # Otherwise use Monte Carlo
            return self._calculate_monte_carlo_equity(
                player_names, hands_list, parsed_board, iterations
            )

        except Exception as e:
            logger.error(f"Equity calculation failed: {e}")
            return None

    def _calculate_exact_equity(
        self,
        player_names: List[str],
        hands: List[List['eval7.Card']],
        board: List['eval7.Card']
    ) -> EquityResult:
        """Calculate exact equity when board is complete."""
        # Evaluate each hand
        scores = []
        for hand in hands:
            score = eval7.evaluate(hand + board)
            scores.append(score)

        # Higher score is better in eval7
        best_score = max(scores)
        winners = [i for i, s in enumerate(scores) if s == best_score]

        equities = {}
        for i, name in enumerate(player_names):
            if i in winners:
                equities[name] = 1.0 / len(winners)  # Split for ties
            else:
                equities[name] = 0.0

        tie_prob = 1.0 / len(winners) if len(winners) > 1 else 0.0

        return EquityResult(
            equities=equities,
            tie_probability=tie_prob,
            sample_count=1
        )

    def _calculate_monte_carlo_equity(
        self,
        player_names: List[str],
        hands: List[List['eval7.Card']],
        board: List['eval7.Card'],
        iterations: int
    ) -> EquityResult:
        """Calculate equity using Monte Carlo simulation."""
        # Build deck excluding known cards
        all_known = set()
        for hand in hands:
            all_known.update(hand)
        all_known.update(board)

        deck = [c for c in eval7.Deck().cards if c not in all_known]

        wins = {name: 0 for name in player_names}
        ties = 0
        cards_needed = 5 - len(board)

        import random
        for _ in range(iterations):
            # Sample remaining board cards
            sampled = random.sample(deck, cards_needed)
            full_board = board + sampled

            # Evaluate all hands
            scores = [eval7.evaluate(hand + full_board) for hand in hands]

            # Find winner(s) - higher score is better in eval7
            best_score = max(scores)
            winner_indices = [i for i, s in enumerate(scores) if s == best_score]

            if len(winner_indices) == 1:
                wins[player_names[winner_indices[0]]] += 1
            else:
                ties += 1
                # Partial credit for ties
                for idx in winner_indices:
                    wins[player_names[idx]] += 1.0 / len(winner_indices)

        equities = {name: count / iterations for name, count in wins.items()}
        tie_prob = ties / iterations

        return EquityResult(
            equities=equities,
            tie_probability=tie_prob,
            sample_count=iterations
        )

    def detect_swings(
        self,
        before: Dict[str, float],
        after: Dict[str, float],
        card_revealed: str = "",
        threshold: float = None
    ) -> List[SwingEvent]:
        """
        Detect equity swings between two states.

        Args:
            before: Player equities before the card
            after: Player equities after the card
            card_revealed: String representation of the revealed card
            threshold: Override default dramatic swing threshold

        Returns:
            List of SwingEvent for players with notable equity changes
        """
        threshold = threshold or self.DRAMATIC_SWING_THRESHOLD
        swings = []

        for player in before:
            if player not in after:
                continue

            delta = after[player] - before[player]

            if abs(delta) >= self.NOTABLE_SWING_THRESHOLD:
                swings.append(SwingEvent(
                    player_name=player,
                    card_revealed=card_revealed,
                    equity_before=before[player],
                    equity_after=after[player],
                    delta=delta,
                    is_dramatic=abs(delta) >= threshold
                ))

        # Sort by absolute delta (biggest swings first)
        swings.sort(key=lambda s: abs(s.delta), reverse=True)
        return swings

    def is_close_equity(self, equities: Dict[str, float]) -> bool:
        """Check if equity is close (tense situation)."""
        if len(equities) < 2:
            return False
        values = list(equities.values())
        return max(values) - min(values) < self.CLOSE_EQUITY_THRESHOLD * 2

    def get_leader(self, equities: Dict[str, float]) -> Tuple[str, float]:
        """Get the current equity leader."""
        if not equities:
            return ("", 0.0)
        leader = max(equities, key=equities.get)
        return (leader, equities[leader])


# Quick test
if __name__ == "__main__":
    calc = EquityCalculator(monte_carlo_iterations=5000)

    # Test: AK vs QQ on a J-high board
    result = calc.calculate_equity(
        players_hands={
            'Batman': ['As', 'Kd'],
            'Snoop': ['Qh', 'Qc']
        },
        board=['Jh', '2d', '5s']
    )

    if result:
        print("Equity calculation:")
        for player, eq in result.equities.items():
            print(f"  {player}: {eq*100:.1f}%")

        # Simulate turn card: Ah (helps Batman)
        result_after = calc.calculate_equity(
            players_hands={
                'Batman': ['As', 'Kd'],
                'Snoop': ['Qh', 'Qc']
            },
            board=['Jh', '2d', '5s', 'Ah']
        )

        if result_after:
            print("\nAfter Ah:")
            for player, eq in result_after.equities.items():
                print(f"  {player}: {eq*100:.1f}%")

            swings = calc.detect_swings(
                result.equities,
                result_after.equities,
                card_revealed='Ah'
            )

            print("\nSwings detected:")
            for swing in swings:
                dramatic = "DRAMATIC!" if swing.is_dramatic else ""
                print(f"  {swing.description} {dramatic}")
    else:
        print("eval7 not available")
