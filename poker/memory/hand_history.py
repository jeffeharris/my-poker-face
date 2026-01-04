"""
Hand History Recording System.

Records complete hand data for analysis, learning, and memory.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional, Any


@dataclass(frozen=True)
class RecordedAction:
    """Single action within a hand (immutable)."""
    player_name: str
    action: str           # 'fold', 'check', 'call', 'raise', 'all_in'
    amount: int           # Amount added to pot (0 for fold/check)
    phase: str            # 'PRE_FLOP', 'FLOP', 'TURN', 'RIVER'
    pot_after: int        # Pot total after this action
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'player_name': self.player_name,
            'action': self.action,
            'amount': self.amount,
            'phase': self.phase,
            'pot_after': self.pot_after,
            'timestamp': self.timestamp.isoformat()
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RecordedAction':
        return cls(
            player_name=data['player_name'],
            action=data['action'],
            amount=data['amount'],
            phase=data['phase'],
            pot_after=data['pot_after'],
            timestamp=datetime.fromisoformat(data['timestamp']) if isinstance(data['timestamp'], str) else data['timestamp']
        )


@dataclass(frozen=True)
class PlayerHandInfo:
    """Information about a player in a hand."""
    name: str
    starting_stack: int
    position: str         # 'BTN', 'SB', 'BB', 'UTG', etc.
    is_human: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'starting_stack': self.starting_stack,
            'position': self.position,
            'is_human': self.is_human
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PlayerHandInfo':
        return cls(
            name=data['name'],
            starting_stack=data['starting_stack'],
            position=data['position'],
            is_human=data['is_human']
        )


@dataclass(frozen=True)
class WinnerInfo:
    """Information about a winner."""
    name: str
    amount_won: int
    hand_name: Optional[str]   # 'Pair of Aces', etc. (None if didn't show)
    hand_rank: Optional[int]   # 1-10 hand ranking (None if didn't show)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'amount_won': self.amount_won,
            'hand_name': self.hand_name,
            'hand_rank': self.hand_rank
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'WinnerInfo':
        return cls(
            name=data['name'],
            amount_won=data['amount_won'],
            hand_name=data.get('hand_name'),
            hand_rank=data.get('hand_rank')
        )


@dataclass(frozen=True)
class RecordedHand:
    """Immutable record of a completed hand."""
    game_id: str
    hand_number: int
    timestamp: datetime
    players: tuple          # Tuple[PlayerHandInfo, ...]
    hole_cards: Dict[str, List[str]]   # {player_name: ['Ah', 'Kd']}
    community_cards: tuple  # Tuple of card strings
    actions: tuple          # Tuple[RecordedAction, ...]
    winners: tuple          # Tuple[WinnerInfo, ...]
    pot_size: int
    was_showdown: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            'game_id': self.game_id,
            'hand_number': self.hand_number,
            'timestamp': self.timestamp.isoformat(),
            'players': [p.to_dict() for p in self.players],
            'hole_cards': self.hole_cards,
            'community_cards': list(self.community_cards),
            'actions': [a.to_dict() for a in self.actions],
            'winners': [w.to_dict() for w in self.winners],
            'pot_size': self.pot_size,
            'was_showdown': self.was_showdown
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RecordedHand':
        return cls(
            game_id=data['game_id'],
            hand_number=data['hand_number'],
            timestamp=datetime.fromisoformat(data['timestamp']) if isinstance(data['timestamp'], str) else data['timestamp'],
            players=tuple(PlayerHandInfo.from_dict(p) for p in data['players']),
            hole_cards=data['hole_cards'],
            community_cards=tuple(data['community_cards']),
            actions=tuple(RecordedAction.from_dict(a) for a in data['actions']),
            winners=tuple(WinnerInfo.from_dict(w) for w in data['winners']),
            pot_size=data['pot_size'],
            was_showdown=data['was_showdown']
        )

    def get_player_outcome(self, player_name: str) -> str:
        """Get the outcome for a specific player: 'won', 'lost', 'folded'."""
        # Check if player won
        for winner in self.winners:
            if winner.name == player_name:
                return 'won'

        # Check if player folded (look for fold action)
        for action in self.actions:
            if action.player_name == player_name and action.action == 'fold':
                return 'folded'

        # Player was in until showdown but lost
        return 'lost'

    def get_player_actions(self, player_name: str) -> List[RecordedAction]:
        """Get all actions for a specific player."""
        return [a for a in self.actions if a.player_name == player_name]

    def get_summary(self) -> str:
        """Generate a brief summary of the hand."""
        winner_names = [w.name for w in self.winners]
        if self.was_showdown and self.winners and self.winners[0].hand_name:
            return f"Hand #{self.hand_number}: {', '.join(winner_names)} won ${self.pot_size} with {self.winners[0].hand_name}"
        else:
            return f"Hand #{self.hand_number}: {', '.join(winner_names)} won ${self.pot_size}"


class HandInProgress:
    """Mutable hand being recorded (converted to RecordedHand when complete)."""

    def __init__(self, game_id: str, hand_number: int):
        self.game_id = game_id
        self.hand_number = hand_number
        self.timestamp = datetime.now()
        self.players: List[PlayerHandInfo] = []
        self.hole_cards: Dict[str, List[str]] = {}
        self.community_cards: List[str] = []
        self.actions: List[RecordedAction] = []
        self._phase_community: Dict[str, List[str]] = {
            'FLOP': [],
            'TURN': [],
            'RIVER': []
        }

    def add_player(self, name: str, starting_stack: int, position: str, is_human: bool):
        """Add a player to the hand."""
        self.players.append(PlayerHandInfo(
            name=name,
            starting_stack=starting_stack,
            position=position,
            is_human=is_human
        ))

    def set_hole_cards(self, player_name: str, cards: List[str]):
        """Set a player's hole cards."""
        self.hole_cards[player_name] = cards

    def add_community_cards(self, phase: str, cards: List[str]):
        """Add community cards for a phase."""
        self._phase_community[phase] = cards
        self.community_cards.extend(cards)

    def record_action(self, player_name: str, action: str, amount: int,
                      phase: str, pot_after: int):
        """Record a player action."""
        self.actions.append(RecordedAction(
            player_name=player_name,
            action=action,
            amount=amount,
            phase=phase,
            pot_after=pot_after
        ))

    def complete(self, winners: List[WinnerInfo], pot_size: int,
                 was_showdown: bool) -> RecordedHand:
        """Complete the hand and return an immutable RecordedHand."""
        return RecordedHand(
            game_id=self.game_id,
            hand_number=self.hand_number,
            timestamp=self.timestamp,
            players=tuple(self.players),
            hole_cards=self.hole_cards.copy(),
            community_cards=tuple(self.community_cards),
            actions=tuple(self.actions),
            winners=tuple(winners),
            pot_size=pot_size,
            was_showdown=was_showdown
        )


class HandHistoryRecorder:
    """Records hands as they play out."""

    def __init__(self, game_id: str):
        self.game_id = game_id
        self.current_hand: Optional[HandInProgress] = None
        self.completed_hands: List[RecordedHand] = []

    def start_hand(self, game_state: Any, hand_number: int) -> None:
        """Start recording a new hand.

        Args:
            game_state: PokerGameState with players and positions
            hand_number: The hand number in this game
        """
        self.current_hand = HandInProgress(self.game_id, hand_number)

        # Record player information
        table_positions = game_state.table_positions if hasattr(game_state, 'table_positions') else {}
        position_map = {name: pos for pos, name in table_positions.items()}

        for player in game_state.players:
            player_name = player.name
            is_human = getattr(player, 'is_human', True)
            position = position_map.get(player_name, 'Unknown')
            stack = player.stack if hasattr(player, 'stack') else player.money

            self.current_hand.add_player(
                name=player_name,
                starting_stack=stack,
                position=position,
                is_human=is_human
            )

            # Record hole cards if available
            if hasattr(player, 'hand') and player.hand:
                cards = [str(c) for c in player.hand]
                self.current_hand.set_hole_cards(player_name, cards)

    def record_action(self, player_name: str, action: str, amount: int,
                      phase: str, pot_total: int) -> None:
        """Record an action during the hand."""
        if self.current_hand:
            self.current_hand.record_action(
                player_name=player_name,
                action=action,
                amount=amount,
                phase=phase,
                pot_after=pot_total
            )

    def record_community_cards(self, phase: str, cards: List[str]) -> None:
        """Record community cards dealt for a phase."""
        if self.current_hand:
            self.current_hand.add_community_cards(phase, cards)

    def complete_hand(self, winner_info: Dict[str, Any],
                      game_state: Any) -> RecordedHand:
        """Complete the hand recording and return the recorded hand.

        Args:
            winner_info: Dict with 'winnings', 'hand_name', 'hand_rank'
            game_state: Current game state

        Returns:
            RecordedHand: The completed hand record
        """
        if not self.current_hand:
            raise ValueError("No hand in progress to complete")

        # Build winner list
        winners = []
        winnings = winner_info.get('winnings', {})
        for name, amount in winnings.items():
            winners.append(WinnerInfo(
                name=name,
                amount_won=amount,
                hand_name=winner_info.get('hand_name'),
                hand_rank=winner_info.get('hand_rank')
            ))

        # Calculate pot size
        pot = game_state.pot if hasattr(game_state, 'pot') else {}
        pot_size = pot.get('total', 0) if isinstance(pot, dict) else 0

        # Determine if it was a showdown
        active_players = [p for p in game_state.players if not p.is_folded]
        was_showdown = len(active_players) > 1

        # Complete and store the hand
        recorded_hand = self.current_hand.complete(
            winners=winners,
            pot_size=pot_size,
            was_showdown=was_showdown
        )

        self.completed_hands.append(recorded_hand)
        self.current_hand = None

        return recorded_hand

    def get_recent_hands(self, limit: int = 10) -> List[RecordedHand]:
        """Get the most recent completed hands."""
        return self.completed_hands[-limit:]

    def get_hands_with_player(self, player_name: str) -> List[RecordedHand]:
        """Get all hands where a specific player participated."""
        return [
            hand for hand in self.completed_hands
            if any(p.name == player_name for p in hand.players)
        ]

    def get_showdown_hands(self) -> List[RecordedHand]:
        """Get all hands that went to showdown."""
        return [hand for hand in self.completed_hands if hand.was_showdown]
