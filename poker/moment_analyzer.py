"""
Moment Analysis for Drama Detection.

Provides consistent logic for determining if a game moment is dramatic/memorable.
Used by both pre-decision (response intensity) and post-hand (personality pressure) systems.
"""
from dataclasses import dataclass
from typing import List, Optional
from .poker_game import PokerGameState, Player


@dataclass
class MomentAnalysis:
    """Analysis of a game moment's dramatic significance."""
    level: str  # 'routine' | 'notable' | 'high_stakes' | 'climactic'
    factors: List[str]  # What makes it dramatic
    tone: str = 'neutral'  # 'neutral' | 'confident' | 'desperate' | 'triumphant'

    @property
    def is_dramatic(self) -> bool:
        return self.level in ('high_stakes', 'climactic')


class MomentAnalyzer:
    """Analyzes game moments for dramatic significance."""

    # Thresholds (single source of truth)
    BIG_POT_RATIO = 0.5  # Pot > 50% of player's stack
    BIG_POT_AVG_RATIO = 0.75  # Pot > 75% of average stack (for multi-player)
    SHORT_STACK_BB = 3  # Less than 3 BB is desperate
    BIG_BET_BB = 10  # Bet > 10 BB is significant
    HUGE_RAISE_POT_MULTIPLIER = 3.0  # Raise > 3x pot is dramatic
    LATE_STAGE_PLAYERS = 3  # 3 or fewer players
    LATE_STAGE_AVG_BB = 15  # Average stack < 15 BB

    @classmethod
    def analyze(
        cls,
        game_state: PokerGameState,
        player: Optional[Player] = None,
        cost_to_call: int = 0,
        big_blind: int = 250,
        last_raise_amount: int = 0,
        hand_equity: float = 0.0
    ) -> MomentAnalysis:
        """Analyze current moment for drama level.

        Args:
            game_state: Current game state
            player: Current player (optional)
            cost_to_call: Amount player needs to call
            big_blind: Current big blind amount
            last_raise_amount: Size of last raise made (for huge_raise detection)
            hand_equity: Player's hand equity 0.0-1.0 (for tone determination)
        """
        factors = []

        # Get stack info
        player_stack = player.stack if player else 0
        active_players = [p for p in game_state.players if not p.is_folded and p.stack > 0]
        avg_stack = sum(p.stack for p in active_players) / len(active_players) if active_players else 1000
        pot_total = game_state.pot.get('total', 0) if isinstance(game_state.pot, dict) else 0

        # Factor detection
        if cls.is_all_in_situation(player_stack, cost_to_call, big_blind):
            factors.append('all_in')

        if cls.is_big_pot(pot_total, player_stack, avg_stack):
            factors.append('big_pot')

        if cls.is_big_bet(cost_to_call, big_blind):
            factors.append('big_bet')

        if cls.is_showdown(game_state):
            factors.append('showdown')

        if cls.is_heads_up(active_players):
            factors.append('heads_up')

        if cls.is_huge_raise(last_raise_amount, pot_total):
            factors.append('huge_raise')

        if cls.is_late_stage(active_players, big_blind):
            factors.append('late_stage')

        # Determine level
        level = cls._determine_level(factors)

        # Determine emotional tone based on context
        is_short_stack = player_stack <= big_blind * cls.SHORT_STACK_BB if player else False
        tone = cls._determine_tone(level, factors, hand_equity, is_short_stack)

        return MomentAnalysis(level=level, factors=factors, tone=tone)

    @classmethod
    def is_all_in_situation(cls, player_stack: int, cost_to_call: int, big_blind: int) -> bool:
        """Player is going all-in or facing all-in."""
        return cost_to_call >= player_stack or player_stack <= big_blind * cls.SHORT_STACK_BB

    @classmethod
    def is_big_pot(cls, pot_total: int, player_stack: int, avg_stack: int) -> bool:
        """Pot is significant relative to stacks."""
        # Use player stack if available, otherwise average
        if player_stack > 0:
            return pot_total > player_stack * cls.BIG_POT_RATIO
        return pot_total > avg_stack * cls.BIG_POT_AVG_RATIO

    @classmethod
    def is_big_bet(cls, cost_to_call: int, big_blind: int) -> bool:
        """Facing a large bet."""
        return cost_to_call > big_blind * cls.BIG_BET_BB

    @classmethod
    def is_showdown(cls, game_state: PokerGameState) -> bool:
        """On the river (all community cards dealt)."""
        return len(game_state.community_cards) == 5

    @classmethod
    def is_heads_up(cls, active_players: list) -> bool:
        """Only two players remain."""
        return len(active_players) == 2

    @classmethod
    def is_huge_raise(cls, raise_amount: int, pot_total: int) -> bool:
        """Opponent made an unusually large raise (3x+ pot)."""
        if pot_total <= 0:
            return False
        return raise_amount > pot_total * cls.HUGE_RAISE_POT_MULTIPLIER

    @classmethod
    def is_late_stage(cls, active_players: list, big_blind: int) -> bool:
        """Late stage tournament pressure - few players, shallow stacks."""
        if len(active_players) > cls.LATE_STAGE_PLAYERS:
            return False
        if not active_players or big_blind <= 0:
            return False
        avg_stack = sum(p.stack for p in active_players) / len(active_players)
        avg_bb = avg_stack / big_blind
        return avg_bb < cls.LATE_STAGE_AVG_BB

    @classmethod
    def _determine_tone(
        cls,
        level: str,
        factors: List[str],
        hand_equity: float,
        is_short_stack: bool
    ) -> str:
        """Determine emotional tone based on hand strength context.

        Args:
            level: Drama level ('routine', 'notable', 'high_stakes', 'climactic')
            factors: List of drama factors present
            hand_equity: Player's hand equity 0.0-1.0
            is_short_stack: Whether player is short-stacked

        Returns:
            Tone string: 'neutral', 'confident', 'desperate', or 'triumphant'
        """
        # Triumphant: Strong hand in climactic moment
        if level == 'climactic' and hand_equity >= 0.7:
            return 'triumphant'

        # Desperate: Short stack or weak hand in high-stakes moment
        if (is_short_stack and hand_equity < 0.5) or (level in ('high_stakes', 'climactic') and hand_equity < 0.3):
            return 'desperate'

        # Confident: Good hand in notable+ moment
        if hand_equity >= 0.5 and level in ('notable', 'high_stakes', 'climactic'):
            return 'confident'

        return 'neutral'

    @classmethod
    def _determine_level(cls, factors: List[str]) -> str:
        """Determine drama level from factors.

        Single source of truth for level determination, used by both
        live-game analysis and post-hand commentary.
        """
        if 'all_in' in factors:
            return 'climactic'
        if 'big_pot' in factors and 'showdown' in factors:
            return 'climactic'
        if len(factors) >= 2:
            return 'high_stakes'
        if factors:
            return 'notable'
        return 'routine'
