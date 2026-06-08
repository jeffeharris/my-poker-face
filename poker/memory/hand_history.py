"""
Hand History Recording System.

Records complete hand data for analysis, learning, and memory.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecordedAction:
    """Single action within a hand (immutable)."""

    player_name: str
    action: str  # 'fold', 'check', 'call', 'raise', 'all_in'
    amount: int  # Amount added to pot (0 for fold/check)
    phase: str  # 'PRE_FLOP', 'FLOP', 'TURN', 'RIVER'
    pot_after: int  # Pot total after this action
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'player_name': self.player_name,
            'action': self.action,
            'amount': self.amount,
            'phase': self.phase,
            'pot_after': self.pot_after,
            'timestamp': self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RecordedAction':
        return cls(
            player_name=data['player_name'],
            action=data['action'],
            amount=data['amount'],
            phase=data['phase'],
            pot_after=data['pot_after'],
            timestamp=datetime.fromisoformat(data['timestamp'])
            if isinstance(data['timestamp'], str)
            else data['timestamp'],
        )


@dataclass(frozen=True)
class PlayerHandInfo:
    """Information about a player in a hand."""

    name: str
    starting_stack: int
    position: str  # 'BTN', 'SB', 'BB', 'UTG', etc.
    is_human: bool
    # Stack at hand end. None when not captured (older rows / paths that
    # don't pass it). final_stack <= 0 means the player busted this hand —
    # the KNOCKOUT relationship detector keys on it.
    final_stack: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'final_stack': self.final_stack,
            'starting_stack': self.starting_stack,
            'position': self.position,
            'is_human': self.is_human,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PlayerHandInfo':
        return cls(
            name=data['name'],
            starting_stack=data['starting_stack'],
            position=data['position'],
            is_human=data['is_human'],
            final_stack=data.get('final_stack'),
        )


@dataclass(frozen=True)
class WinnerInfo:
    """Information about a winner."""

    name: str
    amount_won: int
    hand_name: Optional[str]  # 'Pair of Aces', etc. (None if didn't show)
    hand_rank: Optional[int]  # 1-10 hand ranking (None if didn't show)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'amount_won': self.amount_won,
            'hand_name': self.hand_name,
            'hand_rank': self.hand_rank,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'WinnerInfo':
        return cls(
            name=data['name'],
            amount_won=data['amount_won'],
            hand_name=data.get('hand_name'),
            hand_rank=data.get('hand_rank'),
        )


@dataclass(frozen=True)
class RecordedHand:
    """Immutable record of a completed hand."""

    game_id: str
    hand_number: int
    timestamp: datetime
    players: tuple  # Tuple[PlayerHandInfo, ...]
    hole_cards: Dict[str, List[str]]  # {player_name: ['Ah', 'Kd']}
    community_cards: tuple  # Tuple of card strings
    actions: tuple  # Tuple[RecordedAction, ...]
    winners: tuple  # Tuple[WinnerInfo, ...]
    pot_size: int
    was_showdown: bool
    deck_seed: Optional[int] = None
    community_cards_by_phase: Dict[str, List[str]] = field(default_factory=dict)

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
            'was_showdown': self.was_showdown,
            'deck_seed': self.deck_seed,
            'community_cards_by_phase': self.community_cards_by_phase,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RecordedHand':
        return cls(
            game_id=data['game_id'],
            hand_number=data['hand_number'],
            timestamp=datetime.fromisoformat(data['timestamp'])
            if isinstance(data['timestamp'], str)
            else data['timestamp'],
            players=tuple(PlayerHandInfo.from_dict(p) for p in data['players']),
            hole_cards=data['hole_cards'],
            community_cards=tuple(data['community_cards']),
            actions=tuple(RecordedAction.from_dict(a) for a in data['actions']),
            winners=tuple(WinnerInfo.from_dict(w) for w in data['winners']),
            pot_size=data['pot_size'],
            was_showdown=data['was_showdown'],
            deck_seed=data.get('deck_seed'),
            community_cards_by_phase=data.get('community_cards_by_phase', {}),
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

    def get_player_contributions(self) -> Dict[str, int]:
        """Total chips committed to the pot per player across the hand.

        ``RecordedAction.amount`` mixes two units depending on action:
        ``raise`` stores the new bet level (raise-TO snapshot, not an
        increment), while ``call`` / ``all_in`` / ``post_blind`` already
        hold the chip increment. Naively summing ``amount`` overstates
        raises by the prior committed bet. This helper normalizes by
        tracking per-phase committed bets and emitting the actual delta
        for raise actions.
        """
        contributions: Dict[str, int] = {}
        # Per-(player, phase) committed bet within the current betting
        # round. Resets implicitly when a new phase starts because
        # downstream code never reads cross-phase deltas.
        committed_in_phase: Dict[tuple, int] = {}

        for action in self.actions:
            name = action.player_name
            phase = action.phase
            key = (name, phase)
            prior = committed_in_phase.get(key, 0)

            if action.action in ('raise', 'bet'):
                # amount is the new bet level — increment is the delta.
                delta = max(0, action.amount - prior)
                committed_in_phase[key] = action.amount
            elif action.action in ('call', 'post_blind'):
                # amount already represents the chip increment.
                delta = max(0, action.amount)
                committed_in_phase[key] = prior + delta
            elif action.action == 'all_in':
                # all_in amount is the player's remaining stack at the
                # time of the shove — the chip increment, regardless of
                # whether it functions as a raise or a call.
                delta = max(0, action.amount)
                committed_in_phase[key] = prior + delta
            else:
                # fold / check / anything else — no chips committed.
                delta = 0

            contributions[name] = contributions.get(name, 0) + delta

        return contributions

    def bet_fraction_by_action(self) -> Dict[int, float]:
        """Map each aggressive action to how big it was relative to the pot
        BEFORE it — `increment / pot_before` — keyed by ``id(action)``.

        This is the "bettor sized big" signal for sizing-aware modeling
        (SIZING_AWARE_OPPONENT_MODELING.md Phase A). It reuses the same
        amount-semantics replay as ``get_player_contributions`` (raise = new
        bet level so the increment is the delta; call/all_in = the increment),
        then derives ``pot_before = pot_after - increment``. Only ``bet`` /
        ``raise`` actions are emitted (the polarization tell is about the
        bettor's chosen size); calls/checks/folds are omitted. Actions whose
        ``pot_before`` is non-positive (e.g. the first blind) are skipped.

        Keyed by ``id(action)`` because ``RecordedAction`` is a value object
        (equal actions would collide as dict keys); the showdown machine holds
        the very same instances, so identity lookup is exact.
        """
        fractions: Dict[int, float] = {}
        committed_in_phase: Dict[tuple, int] = {}
        for action in self.actions:
            key = (action.player_name, action.phase)
            prior = committed_in_phase.get(key, 0)
            if action.action in ('raise', 'bet'):
                increment = max(0, action.amount - prior)
                committed_in_phase[key] = action.amount
                pot_before = action.pot_after - increment
                if increment > 0 and pot_before > 0:
                    fractions[id(action)] = increment / pot_before
            elif action.action in ('call', 'post_blind', 'all_in'):
                committed_in_phase[key] = prior + max(0, action.amount)
        return fractions

    def get_summary(self) -> str:
        """Generate a brief summary of the hand."""
        winner_names = [w.name for w in self.winners]
        if self.was_showdown and self.winners and self.winners[0].hand_name:
            return f"Hand #{self.hand_number}: {', '.join(winner_names)} won ${self.pot_size} with {self.winners[0].hand_name}"
        else:
            return f"Hand #{self.hand_number}: {', '.join(winner_names)} won ${self.pot_size}"


class HandInProgress:
    """Mutable hand being recorded (converted to RecordedHand when complete)."""

    def __init__(self, game_id: str, hand_number: int, deck_seed: Optional[int] = None):
        self.game_id = game_id
        self.hand_number = hand_number
        self.deck_seed = deck_seed
        self.timestamp = datetime.now()
        self.players: List[PlayerHandInfo] = []
        self.hole_cards: Dict[str, List[str]] = {}
        self.community_cards: List[str] = []
        self.actions: List[RecordedAction] = []
        self._phase_community: Dict[str, List[str]] = {'FLOP': [], 'TURN': [], 'RIVER': []}

    def add_player(self, name: str, starting_stack: int, position: str, is_human: bool):
        """Add a player to the hand."""
        self.players.append(
            PlayerHandInfo(
                name=name, starting_stack=starting_stack, position=position, is_human=is_human
            )
        )

    def set_hole_cards(self, player_name: str, cards: List[str]):
        """Set a player's hole cards."""
        self.hole_cards[player_name] = cards

    def add_community_cards(self, phase: str, cards: List[str]):
        """Add community cards for a phase (idempotent - won't duplicate)."""
        # Only add if not already recorded for this phase
        if phase not in self._phase_community or not self._phase_community[phase]:
            self._phase_community[phase] = cards
            self.community_cards.extend(cards)

    def record_action(self, player_name: str, action: str, amount: int, phase: str, pot_after: int):
        """Record a player action."""
        self.actions.append(
            RecordedAction(
                player_name=player_name,
                action=action,
                amount=amount,
                phase=phase,
                pot_after=pot_after,
            )
        )

    def complete(
        self,
        winners: List[WinnerInfo],
        pot_size: int,
        was_showdown: bool,
        final_stacks: Optional[Dict[str, int]] = None,
    ) -> RecordedHand:
        """Complete the hand and return an immutable RecordedHand.

        `final_stacks` (name -> end-of-hand stack), when supplied, is stamped
        onto each PlayerHandInfo so downstream detectors (e.g. KNOCKOUT) can
        tell who busted. Players absent from the map keep final_stack=None.
        """
        players = self.players
        if final_stacks:
            players = [
                PlayerHandInfo(
                    name=p.name,
                    starting_stack=p.starting_stack,
                    position=p.position,
                    is_human=p.is_human,
                    final_stack=final_stacks.get(p.name, p.final_stack),
                )
                for p in self.players
            ]
        return RecordedHand(
            game_id=self.game_id,
            hand_number=self.hand_number,
            timestamp=self.timestamp,
            players=tuple(players),
            hole_cards=self.hole_cards.copy(),
            community_cards=tuple(self.community_cards),
            actions=tuple(self.actions),
            winners=tuple(winners),
            pot_size=pot_size,
            was_showdown=was_showdown,
            deck_seed=self.deck_seed,
            community_cards_by_phase={k: list(v) for k, v in self._phase_community.items() if v},
        )


class HandHistoryRecorder:
    """Records hands as they play out."""

    def __init__(self, game_id: str):
        self.game_id = game_id
        self.current_hand: Optional[HandInProgress] = None
        self.completed_hands: List[RecordedHand] = []

    def start_hand(
        self, game_state: Any, hand_number: int, deck_seed: Optional[int] = None
    ) -> None:
        """Start recording a new hand.

        Args:
            game_state: PokerGameState with players and positions
            hand_number: The hand number in this game
        """
        self.current_hand = HandInProgress(self.game_id, hand_number, deck_seed=deck_seed)

        # Record player information
        table_positions = (
            game_state.table_positions if hasattr(game_state, 'table_positions') else {}
        )
        position_map = {name: pos for pos, name in table_positions.items()}

        for player in game_state.players:
            player_name = player.name
            is_human = getattr(player, 'is_human', True)
            position = position_map.get(player_name, 'Unknown')
            stack = player.stack if hasattr(player, 'stack') else player.money

            self.current_hand.add_player(
                name=player_name, starting_stack=stack, position=position, is_human=is_human
            )

            # Record hole cards if available
            if hasattr(player, 'hand') and player.hand:
                cards = [str(c) for c in player.hand]
                self.current_hand.set_hole_cards(player_name, cards)

    def record_action(
        self, player_name: str, action: str, amount: int, phase: str, pot_total: int
    ) -> None:
        """Record an action during the hand."""
        if self.current_hand:
            self.current_hand.record_action(
                player_name=player_name,
                action=action,
                amount=amount,
                phase=phase,
                pot_after=pot_total,
            )
        else:
            # No hand is being recorded — the action is lost. This is the
            # mechanism behind EXP_008's "garbage recap" (e.g. a $2530 tourney
            # pot stored with 1 action): actions arriving while current_hand is
            # None vanish silently, leaving the AI commentary/recap an empty,
            # confusing hand. Surface it loudly so the boundary/ordering bug
            # (on_hand_start not yet called, or complete_hand called early) is
            # observable instead of producing a silently-broken recap.
            logger.warning(
                "HandHistoryRecorder: dropped action (no current_hand) — "
                "player=%s action=%s phase=%s pot_after=%s. Recap will be "
                "incomplete; on_hand_start likely not called for this hand yet.",
                player_name,
                action,
                phase,
                pot_total,
            )

    def record_community_cards(self, phase: str, cards: List[str]) -> None:
        """Record community cards dealt for a phase."""
        if self.current_hand:
            self.current_hand.add_community_cards(phase, cards)

    def complete_hand(self, winner_info: Dict[str, Any], game_state: Any) -> RecordedHand:
        """Complete the hand recording and return the recorded hand.

        Args:
            winner_info: Dict with 'winnings', 'hand_name', 'hand_rank'
            game_state: Current game state

        Returns:
            RecordedHand: The completed hand record
        """
        if not self.current_hand:
            raise ValueError("No hand in progress to complete")

        # Build winner list from pot_breakdown structure
        winners = []
        pot_breakdown = winner_info.get('pot_breakdown', [])
        seen_winners = set()  # Avoid duplicates across pots
        for pot in pot_breakdown:
            pot_hand_name = pot.get('hand_name') or winner_info.get('hand_name')
            for w in pot.get('winners', []):
                if w['name'] not in seen_winners:
                    seen_winners.add(w['name'])
                    winners.append(
                        WinnerInfo(
                            name=w['name'],
                            amount_won=w.get('amount', 0),
                            hand_name=pot_hand_name,
                            hand_rank=winner_info.get('hand_rank'),
                        )
                    )

        # Calculate pot size
        pot = game_state.pot if hasattr(game_state, 'pot') else {}
        pot_size = pot.get('total', 0) if isinstance(pot, dict) else 0

        # Determine if it was a showdown
        active_players = [p for p in game_state.players if not p.is_folded]
        was_showdown = len(active_players) > 1

        # Capture end-of-hand stacks so KNOCKOUT can detect busts.
        final_stacks: Dict[str, int] = {}
        for p in getattr(game_state, 'players', []):
            stack = getattr(p, 'stack', None)
            if stack is None:
                stack = getattr(p, 'money', None)
            if stack is not None:
                final_stacks[p.name] = stack

        # Complete and store the hand
        recorded_hand = self.current_hand.complete(
            winners=winners,
            pot_size=pot_size,
            was_showdown=was_showdown,
            final_stacks=final_stacks,
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
            hand
            for hand in self.completed_hands
            if any(p.name == player_name for p in hand.players)
        ]

    def get_showdown_hands(self) -> List[RecordedHand]:
        """Get all hands that went to showdown."""
        return [hand for hand in self.completed_hands if hand.was_showdown]
