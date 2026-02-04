"""
Equity Snapshot Data Structures.

Immutable dataclasses for tracking equity across all streets of a poker hand.
Used for equity-based pressure event detection and analytics.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# Street order for iteration and comparison
STREET_ORDER = ('PRE_FLOP', 'FLOP', 'TURN', 'RIVER')


@dataclass(frozen=True)
class EquitySnapshot:
    """Single player's equity at a specific street."""
    player_name: str
    street: str  # 'PRE_FLOP', 'FLOP', 'TURN', 'RIVER'
    equity: float  # 0.0 to 1.0
    hole_cards: Tuple[str, ...]  # ('Ah', 'Kd')
    board_cards: Tuple[str, ...]  # () for preflop, ('Qh', 'Jh', 'Th') for flop, etc.
    was_active: bool = True  # False if player had folded by this street
    sample_count: Optional[int] = None  # Monte Carlo iterations (None = exact)

    def to_dict(self) -> Dict:
        """Serialize for database storage."""
        return {
            'player_name': self.player_name,
            'street': self.street,
            'equity': self.equity,
            'hole_cards': list(self.hole_cards),
            'board_cards': list(self.board_cards),
            'was_active': self.was_active,
            'sample_count': self.sample_count,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'EquitySnapshot':
        """Deserialize from database storage."""
        return cls(
            player_name=data['player_name'],
            street=data['street'],
            equity=data['equity'],
            hole_cards=tuple(data.get('hole_cards', [])),
            board_cards=tuple(data.get('board_cards', [])),
            was_active=data.get('was_active', True),
            sample_count=data.get('sample_count'),
        )


@dataclass(frozen=True)
class HandEquityHistory:
    """Complete equity history for a hand across all streets and players."""
    hand_history_id: Optional[int]
    game_id: str
    hand_number: int
    snapshots: Tuple[EquitySnapshot, ...]

    def get_player_equity(self, player_name: str, street: str) -> Optional[float]:
        """Get equity for a specific player at a specific street."""
        for snap in self.snapshots:
            if snap.player_name == player_name and snap.street == street:
                return snap.equity
        return None

    def get_street_equities(self, street: str) -> Dict[str, float]:
        """Get all player equities at a specific street."""
        return {
            snap.player_name: snap.equity
            for snap in self.snapshots
            if snap.street == street
        }

    def get_active_street_equities(self, street: str) -> Dict[str, float]:
        """Get equities only for players who were still active at this street."""
        return {
            snap.player_name: snap.equity
            for snap in self.snapshots
            if snap.street == street and snap.was_active
        }

    def get_player_history(self, player_name: str) -> List[EquitySnapshot]:
        """Get equity progression for a player across all streets."""
        return sorted(
            [s for s in self.snapshots if s.player_name == player_name],
            key=lambda s: STREET_ORDER.index(s.street) if s.street in STREET_ORDER else 99
        )

    def get_player_names(self) -> List[str]:
        """Get list of all players with equity data."""
        return list({snap.player_name for snap in self.snapshots})

    def was_behind_then_won(self, player_name: str, threshold: float = 0.40) -> bool:
        """Check if player was behind (<threshold) on any earlier street but won (1.0 on river)."""
        river_equity = self.get_player_equity(player_name, 'RIVER')
        if river_equity is None or river_equity < 0.99:  # Not the winner
            return False

        # Check if behind on any earlier street
        for street in ('PRE_FLOP', 'FLOP', 'TURN'):
            equity = self.get_player_equity(player_name, street)
            if equity is not None and equity < threshold:
                return True
        return False

    def was_ahead_then_lost(self, player_name: str, threshold: float = 0.60) -> bool:
        """Check if player was ahead (>threshold) on any street but lost (0.0 on river)."""
        river_equity = self.get_player_equity(player_name, 'RIVER')
        if river_equity is None or river_equity > 0.01:  # Not a loser
            return False

        # Check if ahead on any earlier street
        for street in ('PRE_FLOP', 'FLOP', 'TURN'):
            equity = self.get_player_equity(player_name, street)
            if equity is not None and equity > threshold:
                return True
        return False

    def get_max_equity_swing(self, player_name: str) -> Optional[Tuple[str, str, float]]:
        """
        Find the largest equity swing for a player.

        Returns:
            Tuple of (from_street, to_street, delta) or None if not enough data
        """
        history = self.get_player_history(player_name)
        if len(history) < 2:
            return None

        max_swing = 0.0
        result = None

        for i in range(len(history) - 1):
            delta = history[i + 1].equity - history[i].equity
            if abs(delta) > abs(max_swing):
                max_swing = delta
                result = (history[i].street, history[i + 1].street, delta)

        return result

    def to_dict(self) -> Dict:
        """Serialize for storage."""
        return {
            'hand_history_id': self.hand_history_id,
            'game_id': self.game_id,
            'hand_number': self.hand_number,
            'snapshots': [s.to_dict() for s in self.snapshots],
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'HandEquityHistory':
        """Deserialize from storage."""
        return cls(
            hand_history_id=data.get('hand_history_id'),
            game_id=data['game_id'],
            hand_number=data['hand_number'],
            snapshots=tuple(EquitySnapshot.from_dict(s) for s in data.get('snapshots', [])),
        )

    @classmethod
    def empty(cls, game_id: str = '', hand_number: int = 0) -> 'HandEquityHistory':
        """Create an empty history (for fallback cases)."""
        return cls(
            hand_history_id=None,
            game_id=game_id,
            hand_number=hand_number,
            snapshots=(),
        )
