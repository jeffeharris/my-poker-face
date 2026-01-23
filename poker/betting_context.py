"""
Betting context abstraction for centralized raise/bet logic.

This module provides a single source of truth for all betting constraints,
eliminating the "raise TO" vs "raise BY" confusion by standardizing on
"raise TO" semantics everywhere.
"""

from dataclasses import dataclass
from typing import Dict, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .poker_game import PokerGameState


@dataclass(frozen=True)
class BettingContext:
    """
    Immutable betting constraints for the current player's turn.

    All amounts are absolute values (chips), not relative to anything.
    Uses "raise TO" semantics - amounts represent total bet amounts.
    """
    player_stack: int
    player_current_bet: int
    highest_bet: int
    pot_total: int
    min_raise_amount: int  # Minimum raise INCREMENT (from game state)
    available_actions: Tuple[str, ...]  # Use tuple for immutability

    @property
    def cost_to_call(self) -> int:
        """Amount the current player needs to add to match highest bet."""
        return max(0, self.highest_bet - self.player_current_bet)

    @property
    def min_raise_to(self) -> int:
        """
        Minimum total amount to raise TO.

        This is the smallest valid raise: highest_bet + min_raise_amount.
        """
        return self.highest_bet + self.min_raise_amount

    @property
    def max_raise_to(self) -> int:
        """
        Maximum total amount to raise TO (all-in).

        This is the player's total possible bet: current_bet + stack.
        """
        return self.player_current_bet + self.player_stack

    @property
    def effective_stack(self) -> int:
        """Stack available for betting (after calling)."""
        return max(0, self.player_stack - self.cost_to_call)

    def validate_and_sanitize(self, raise_to_amount: int) -> Tuple[int, str]:
        """
        Validate and auto-correct a raise TO amount.

        Args:
            raise_to_amount: The total amount the player wants to bet to.

        Returns:
            Tuple of (sanitized_amount, message)
            - sanitized_amount: The corrected amount to use
            - message: Description of any corrections made (empty if no correction)
        """
        original = raise_to_amount
        sanitized = raise_to_amount
        message = ""

        # Can't raise more than all-in
        if raise_to_amount > self.max_raise_to:
            sanitized = self.max_raise_to
            message = f"Amount ${original} exceeds stack, converting to all-in ${sanitized}"

        # All-in is always valid, even if below min raise
        if sanitized == self.max_raise_to:
            return (sanitized, message)

        # Must meet minimum raise unless going all-in
        if sanitized < self.min_raise_to:
            # If they can't meet min raise, it must be all-in
            if self.max_raise_to < self.min_raise_to:
                sanitized = self.max_raise_to
                message = f"Can't meet min raise, all-in at ${sanitized}"
            else:
                sanitized = self.min_raise_to
                message = f"Amount ${original} below minimum, adjusted to ${sanitized}"

        return (sanitized, message)

    def get_raise_by_amount(self, raise_to_amount: int) -> int:
        """
        Convert a "raise TO" amount to a "raise BY" amount.

        This is the increment above the current highest bet.
        """
        return max(0, raise_to_amount - self.highest_bet)

    def get_call_and_raise_breakdown(self, raise_to_amount: int) -> Dict[str, int]:
        """
        Break down a raise TO amount into call portion and raise portion.

        Returns:
            Dict with 'call_portion', 'raise_portion', and 'stack_after'.
        """
        call_portion = self.cost_to_call
        total_to_add = raise_to_amount - self.player_current_bet
        raise_portion = total_to_add - call_portion
        stack_after = self.player_stack - total_to_add

        return {
            'call_portion': max(0, call_portion),
            'raise_portion': max(0, raise_portion),
            'total_to_add': max(0, total_to_add),
            'stack_after': max(0, stack_after),
        }

    def to_dict(self) -> Dict:
        """Convert to dictionary for API response."""
        return {
            'player_stack': self.player_stack,
            'player_current_bet': self.player_current_bet,
            'highest_bet': self.highest_bet,
            'pot_total': self.pot_total,
            'min_raise_amount': self.min_raise_amount,
            'available_actions': list(self.available_actions),
            # Computed properties
            'cost_to_call': self.cost_to_call,
            'min_raise_to': self.min_raise_to,
            'max_raise_to': self.max_raise_to,
            'effective_stack': self.effective_stack,
        }

    @staticmethod
    def from_game_state(game_state: 'PokerGameState') -> 'BettingContext':
        """
        Create a BettingContext from the current game state.

        Args:
            game_state: The current PokerGameState

        Returns:
            A BettingContext with all betting constraints for current player.
        """
        player = game_state.current_player

        return BettingContext(
            player_stack=player.stack,
            player_current_bet=player.bet,
            highest_bet=game_state.highest_bet,
            pot_total=game_state.pot.get('total', 0),
            min_raise_amount=game_state.min_raise_amount,
            available_actions=tuple(game_state.current_player_options),
        )
