"""
Equity Tracker Service.

Calculates and persists equity for all players at all streets.
Used for equity-based pressure event detection and analytics.
"""

import logging
from typing import Dict, List, Optional, Set

from .equity_calculator import EquityCalculator
from .equity_snapshot import EquitySnapshot, HandEquityHistory, STREET_ORDER
from .memory.hand_history import HandInProgress, RecordedHand

logger = logging.getLogger(__name__)


class EquityTracker:
    """Service for tracking and persisting equity across all streets."""

    # Monte Carlo iterations per street (fewer for early streets = faster)
    ITERATIONS_BY_STREET = {
        'PRE_FLOP': 1000,
        'FLOP': 2000,
        'TURN': 3000,
        'RIVER': 5000,  # More accurate for final street
    }

    def __init__(
        self,
        equity_calculator: Optional[EquityCalculator] = None,
        default_iterations: int = 2000,
    ):
        """Initialize the equity tracker.

        Args:
            equity_calculator: Optional pre-configured EquityCalculator
            default_iterations: Default Monte Carlo iterations if not specified per-street
        """
        self.calculator = equity_calculator or EquityCalculator(
            monte_carlo_iterations=default_iterations
        )

    def calculate_hand_equity_history(
        self,
        hand: HandInProgress,
        folded_players: Optional[Set[str]] = None,
    ) -> HandEquityHistory:
        """
        Calculate equity for all players at all streets that were played.

        Args:
            hand: HandInProgress with hole_cards and _phase_community populated
            folded_players: Optional set of player names who folded (for was_active tracking)

        Returns:
            HandEquityHistory with snapshots for all players at all streets
        """
        if not hand.hole_cards:
            logger.warning(f"No hole cards available for hand #{hand.hand_number}")
            return HandEquityHistory.empty(hand.game_id, hand.hand_number)

        folded_players = folded_players or set()
        snapshots = []

        # Track which players have folded by street
        players_folded_by_street = self._get_folded_players_by_street(hand)

        # Calculate equity for each street
        for street in STREET_ORDER:
            board_cards = self._get_board_cards_at_street(hand, street)

            # Skip if this street wasn't played (e.g., preflop all-in)
            if street != 'PRE_FLOP' and not board_cards:
                continue

            # Calculate equity for all players at this street
            street_snapshots = self._calculate_street_equity(
                hole_cards=hand.hole_cards,
                board_cards=board_cards,
                street=street,
                folded_at_street=players_folded_by_street.get(street, set()),
            )
            snapshots.extend(street_snapshots)

        return HandEquityHistory(
            hand_history_id=None,  # Set by caller after saving to hand_history
            game_id=hand.game_id,
            hand_number=hand.hand_number,
            snapshots=tuple(snapshots),
        )

    def calculate_from_recorded_hand(
        self,
        recorded_hand: RecordedHand,
        hand_history_id: Optional[int] = None,
    ) -> HandEquityHistory:
        """
        Calculate equity from a completed RecordedHand.

        Args:
            recorded_hand: Completed hand record
            hand_history_id: Database ID of the hand_history record

        Returns:
            HandEquityHistory with snapshots for all players at all streets
        """
        if not recorded_hand.hole_cards:
            logger.warning(f"No hole cards in recorded hand #{recorded_hand.hand_number}")
            return HandEquityHistory.empty(recorded_hand.game_id, recorded_hand.hand_number)

        snapshots = []

        # Determine which players folded at each street from actions
        players_folded_by_street = self._get_folded_from_actions(recorded_hand)

        # Build cumulative board cards
        community_cards = list(recorded_hand.community_cards)

        for street in STREET_ORDER:
            board_cards = self._get_board_for_street_from_community(community_cards, street)

            # Skip if this street wasn't played
            if street != 'PRE_FLOP' and not board_cards:
                continue

            street_snapshots = self._calculate_street_equity(
                hole_cards=recorded_hand.hole_cards,
                board_cards=board_cards,
                street=street,
                folded_at_street=players_folded_by_street.get(street, set()),
            )
            snapshots.extend(street_snapshots)

        return HandEquityHistory(
            hand_history_id=hand_history_id,
            game_id=recorded_hand.game_id,
            hand_number=recorded_hand.hand_number,
            snapshots=tuple(snapshots),
        )

    def _calculate_street_equity(
        self,
        hole_cards: Dict[str, List[str]],
        board_cards: List[str],
        street: str,
        folded_at_street: Set[str],
    ) -> List[EquitySnapshot]:
        """
        Calculate equity for all players at a specific street.

        Args:
            hole_cards: Dict of player_name -> [card1, card2]
            board_cards: Community cards visible at this street
            street: Street name
            folded_at_street: Set of player names who had folded by this street

        Returns:
            List of EquitySnapshot for each player
        """
        iterations = self.ITERATIONS_BY_STREET.get(street, 2000)

        # Get active players (not folded)
        active_hole_cards = {
            name: cards
            for name, cards in hole_cards.items()
            if name not in folded_at_street
        }

        # Calculate equity for active players
        active_equities = {}
        if len(active_hole_cards) >= 2:
            try:
                result = self.calculator.calculate_equity(
                    players_hands=active_hole_cards,
                    board=board_cards,
                    iterations=iterations,
                )
                if result:
                    active_equities = result.equities
            except Exception as e:
                logger.error(f"Equity calculation failed for street {street}: {e}")
                # Fallback: equal equity for active players
                num_active = len(active_hole_cards)
                if num_active > 0:
                    active_equities = {name: 1.0 / num_active for name in active_hole_cards}
        elif len(active_hole_cards) == 1:
            # Only one player left - they have 100% equity
            active_equities = {list(active_hole_cards.keys())[0]: 1.0}

        # Build snapshots for ALL players (active and folded)
        snapshots = []
        for player_name, cards in hole_cards.items():
            was_active = player_name not in folded_at_street
            equity = active_equities.get(player_name, 0.0)

            snapshots.append(EquitySnapshot(
                player_name=player_name,
                street=street,
                equity=equity,
                hole_cards=tuple(cards),
                board_cards=tuple(board_cards),
                was_active=was_active,
                sample_count=iterations if len(board_cards) < 5 else None,
            ))

        return snapshots

    def _get_board_cards_at_street(
        self, hand: HandInProgress, street: str
    ) -> List[str]:
        """Get cumulative board cards at a specific street from HandInProgress."""
        if street == 'PRE_FLOP':
            return []

        board = []
        if hand._phase_community.get('FLOP'):
            board.extend(hand._phase_community['FLOP'])

        if street == 'FLOP':
            return board

        if hand._phase_community.get('TURN'):
            board.extend(hand._phase_community['TURN'])

        if street == 'TURN':
            return board

        if hand._phase_community.get('RIVER'):
            board.extend(hand._phase_community['RIVER'])

        return board

    def _get_board_for_street_from_community(
        self, community_cards: List[str], street: str
    ) -> List[str]:
        """Get board cards at a specific street from a flat community cards list."""
        if street == 'PRE_FLOP':
            return []
        elif street == 'FLOP':
            return community_cards[:3] if len(community_cards) >= 3 else []
        elif street == 'TURN':
            return community_cards[:4] if len(community_cards) >= 4 else []
        elif street == 'RIVER':
            return community_cards[:5] if len(community_cards) >= 5 else []
        return []

    def _get_folded_players_by_street(
        self, hand: HandInProgress
    ) -> Dict[str, Set[str]]:
        """
        Track which players had folded by each street.

        Returns:
            Dict mapping street -> set of player names who had folded by that street
        """
        folded_by_street: Dict[str, Set[str]] = {
            'PRE_FLOP': set(),
            'FLOP': set(),
            'TURN': set(),
            'RIVER': set(),
        }

        cumulative_folded: Set[str] = set()

        for action in hand.actions:
            if action.action == 'fold':
                cumulative_folded.add(action.player_name)

            # Update folded set for current and subsequent streets
            action_street = action.phase
            if action_street in STREET_ORDER:
                idx = STREET_ORDER.index(action_street)
                # Include current street (idx) since fold happened during this street
                for i in range(idx, len(STREET_ORDER)):
                    folded_by_street[STREET_ORDER[i]] = cumulative_folded.copy()

        return folded_by_street

    def _get_folded_from_actions(
        self, recorded_hand: RecordedHand
    ) -> Dict[str, Set[str]]:
        """
        Track which players had folded by each street from RecordedHand.

        Returns:
            Dict mapping street -> set of player names who had folded by that street
        """
        folded_by_street: Dict[str, Set[str]] = {
            'PRE_FLOP': set(),
            'FLOP': set(),
            'TURN': set(),
            'RIVER': set(),
        }

        cumulative_folded: Set[str] = set()

        for action in recorded_hand.actions:
            if action.action == 'fold':
                cumulative_folded.add(action.player_name)

            # Update folded set for current and subsequent streets
            action_street = action.phase
            if action_street in STREET_ORDER:
                idx = STREET_ORDER.index(action_street)
                # Include current street (idx) since fold happened during this street
                for i in range(idx, len(STREET_ORDER)):
                    folded_by_street[STREET_ORDER[i]] = cumulative_folded.copy()

        return folded_by_street
