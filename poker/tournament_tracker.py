"""
Tournament tracking for poker games.
Tracks eliminations, standings, and generates final results.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any


@dataclass
class EliminationEvent:
    """Records when a player is eliminated from the tournament."""
    eliminated_player: str
    eliminator: str
    hand_number: int
    pot_size: int
    finishing_position: int


@dataclass
class PlayerStanding:
    """Individual player's tournament result."""
    player_name: str
    is_human: bool
    finishing_position: int
    eliminated_by: Optional[str] = None
    eliminated_at_hand: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'player_name': self.player_name,
            'is_human': self.is_human,
            'finishing_position': self.finishing_position,
            'eliminated_by': self.eliminated_by,
            'eliminated_at_hand': self.eliminated_at_hand
        }


@dataclass
class TournamentTracker:
    """Tracks tournament progress, eliminations, and generates final results."""

    game_id: str
    starting_players: List[Dict[str, Any]]  # List of {name, is_human}
    eliminations: List[EliminationEvent] = field(default_factory=list)
    hand_count: int = 0
    biggest_pot: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now())
    _active_players: set = field(default_factory=set, repr=False)

    def __post_init__(self):
        """Initialize active players set from starting players."""
        self._active_players = {p['name'] for p in self.starting_players}

    @property
    def active_player_count(self) -> int:
        """Number of players still in the tournament."""
        return len(self._active_players)

    @property
    def starting_player_count(self) -> int:
        """Number of players who started the tournament."""
        return len(self.starting_players)

    def on_hand_complete(self, pot_size: int) -> None:
        """Called after each hand completes.

        Args:
            pot_size: The size of the pot that was won
        """
        self.hand_count += 1
        self.biggest_pot = max(self.biggest_pot, pot_size)

    def on_player_eliminated(self, player_name: str, eliminator: str,
                             pot_size: int = 0) -> EliminationEvent:
        """Record a player elimination.

        Args:
            player_name: Name of the eliminated player
            eliminator: Name of the player who won the pot (eliminator)
            pot_size: Size of the pot (optional)

        Returns:
            EliminationEvent with the elimination details
        """
        if player_name not in self._active_players:
            raise ValueError(f"Player {player_name} is not in the tournament")

        # Finishing position is based on how many players remain
        # If 3 players remain and someone is eliminated, they get 3rd place
        finishing_position = len(self._active_players)

        self._active_players.remove(player_name)

        event = EliminationEvent(
            eliminated_player=player_name,
            eliminator=eliminator,
            hand_number=self.hand_count,
            pot_size=pot_size,
            finishing_position=finishing_position
        )
        self.eliminations.append(event)

        return event

    def is_complete(self) -> bool:
        """Check if the tournament is complete (only one player remains)."""
        return len(self._active_players) == 1

    def get_winner(self) -> Optional[str]:
        """Get the winner's name if tournament is complete."""
        if not self.is_complete():
            return None
        return next(iter(self._active_players))

    def get_human_player(self) -> Optional[Dict[str, Any]]:
        """Get the human player info."""
        for player in self.starting_players:
            if player.get('is_human', False):
                return player
        return None

    def get_standings(self) -> List[PlayerStanding]:
        """Build final standings from eliminations.

        Returns list of PlayerStanding objects ordered by finishing position
        (1st place first).
        """
        standings = []

        # Add winner (if tournament is complete)
        if self.is_complete():
            winner_name = self.get_winner()
            winner_info = next(
                (p for p in self.starting_players if p['name'] == winner_name),
                {'name': winner_name, 'is_human': False}
            )
            standings.append(PlayerStanding(
                player_name=winner_name,
                is_human=winner_info.get('is_human', False),
                finishing_position=1,
                eliminated_by=None,
                eliminated_at_hand=None
            ))

        # Add eliminated players in reverse order (most recent = 2nd place, etc.)
        for event in reversed(self.eliminations):
            player_info = next(
                (p for p in self.starting_players if p['name'] == event.eliminated_player),
                {'name': event.eliminated_player, 'is_human': False}
            )
            standings.append(PlayerStanding(
                player_name=event.eliminated_player,
                is_human=player_info.get('is_human', False),
                finishing_position=event.finishing_position,
                eliminated_by=event.eliminator,
                eliminated_at_hand=event.hand_number
            ))

        # Sort by finishing position
        standings.sort(key=lambda s: s.finishing_position)

        return standings

    def get_result(self) -> Dict[str, Any]:
        """Build complete tournament result for persistence and frontend.

        Returns dict suitable for persistence.save_tournament_result() and
        socket emission.
        """
        standings = self.get_standings()
        human_player = self.get_human_player()

        human_standing = next(
            (s for s in standings if s.is_human),
            None
        )

        return {
            'game_id': self.game_id,
            'winner_name': self.get_winner(),
            'total_hands': self.hand_count,
            'biggest_pot': self.biggest_pot,
            'starting_player_count': self.starting_player_count,
            'human_player_name': human_player['name'] if human_player else None,
            'human_finishing_position': human_standing.finishing_position if human_standing else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'standings': [s.to_dict() for s in standings]
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serialize tracker state for persistence (if needed)."""
        return {
            'game_id': self.game_id,
            'starting_players': self.starting_players,
            'eliminations': [
                {
                    'eliminated_player': e.eliminated_player,
                    'eliminator': e.eliminator,
                    'hand_number': e.hand_number,
                    'pot_size': e.pot_size,
                    'finishing_position': e.finishing_position
                }
                for e in self.eliminations
            ],
            'hand_count': self.hand_count,
            'biggest_pot': self.biggest_pot,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'active_players': list(self._active_players)
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TournamentTracker':
        """Restore tracker from serialized state."""
        tracker = cls(
            game_id=data['game_id'],
            starting_players=data['starting_players'],
            hand_count=data.get('hand_count', 0),
            biggest_pot=data.get('biggest_pot', 0),
            started_at=datetime.fromisoformat(data['started_at']) if data.get('started_at') else datetime.now()
        )

        # Restore eliminations
        for e_data in data.get('eliminations', []):
            tracker.eliminations.append(EliminationEvent(
                eliminated_player=e_data['eliminated_player'],
                eliminator=e_data['eliminator'],
                hand_number=e_data['hand_number'],
                pot_size=e_data.get('pot_size', 0),
                finishing_position=e_data['finishing_position']
            ))

        # Restore active players
        if 'active_players' in data:
            tracker._active_players = set(data['active_players'])

        return tracker
