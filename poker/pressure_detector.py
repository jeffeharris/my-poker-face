"""
Pressure Event Detection for Elastic Personality System.

This module detects game events that should trigger pressure changes
in AI player personalities.
"""

from typing import Dict, List, Optional, Tuple, Any, Set, TYPE_CHECKING
from .poker_game import PokerGameState, Player
from .hand_evaluator import HandEvaluator
from .moment_analyzer import MomentAnalyzer

if TYPE_CHECKING:
    from .equity_snapshot import HandEquityHistory


class PressureEventDetector:
    """Detects pressure events based on game outcomes.

    This class is detection-only. It returns events but does not apply them.
    Callers are responsible for routing events to the appropriate systems
    (e.g., controller.psychology.apply_pressure_event()).
    """

    def __init__(self):
        self.last_pot_size = 0
        self.player_hand_history: Dict[str, List[int]] = {}  # Track hand strengths
        
    def detect_showdown_events(self, game_state: PokerGameState, 
                             winner_info: Dict[str, Any]) -> List[Tuple[str, List[str]]]:
        """
        Detect pressure events from showdown results.
        
        Returns list of (event_name, affected_players) tuples.
        """
        events = []
        
        # Extract winner details from pot_breakdown (split-pot support)
        winner_names = []
        pot_breakdown = winner_info.get('pot_breakdown', [])
        for pot in pot_breakdown:
            for winner in pot.get('winners', []):
                if winner['name'] not in winner_names:
                    winner_names.append(winner['name'])

        # Fallback to old 'winnings' format if pot_breakdown not available
        if not winner_names:
            winnings = winner_info.get('winnings', {})
            winner_names = list(winnings.keys()) if winnings else []

        winner_name = winner_names[0] if winner_names else None
        
        # Get hand rank from hand evaluation if available
        # Use winning_hand_values (numeric) for comparisons, not winning_hand (display strings)
        winner_hand = winner_info.get('winning_hand_values', winner_info.get('winning_hand', []))
        winner_hand_rank = winner_info.get('hand_rank', 10)  # Use actual hand rank if available
        if winner_hand and winner_hand_rank == 10:
            # Fallback heuristic if hand_rank not provided
            first_val = winner_hand[0] if winner_hand else 0
            if isinstance(first_val, int) and first_val >= 14:  # Ace high or better
                winner_hand_rank = 8 if len(set(winner_hand[:2])) > 1 else 7  # High card vs pair
            
        pot_total = game_state.pot.get('total', 0) if isinstance(game_state.pot, dict) else 0
        
        # Calculate pot size relative to stacks
        active_stacks = [p.stack for p in game_state.players if p.stack > 0]
        avg_stack = sum(active_stacks) / len(active_stacks) if active_stacks else 1000
        
        # Use shared threshold from MomentAnalyzer (single source of truth)
        is_big_pot = MomentAnalyzer.is_big_pot(pot_total, 0, avg_stack)
        
        # Get active players who showed cards
        active_players = [p for p in game_state.players if not p.is_folded]
        
        # Detect successful bluff (weak hand wins big pot when everyone folds)
        if winner_name and winner_hand_rank >= 8 and is_big_pot and len(active_players) == 1:
            # Winner bluffed everyone out - only reward the bluffer
            events.append(("successful_bluff", [winner_name]))
            # Note: bluff_called should only fire when a bluffer LOSES at showdown,
            # not when folders escape. Folders made correct decisions and shouldn't be penalized.

        # Always track wins (not just big wins)
        if winner_names and pot_total > 0:
            # Track any win for stats
            events.append(("win", winner_names))

            # Additionally track big wins/losses
            if is_big_pot:
                events.append(("big_win", winner_names))
                losers = [p.name for p in active_players if p.name not in winner_names]
                if losers:
                    events.append(("big_loss", losers))

        # Track heads-up record (when only 2 players have chips)
        players_with_chips = [p for p in game_state.players if p.stack > 0 or p.name in winner_names]
        if len(players_with_chips) == 2 and winner_names:
            events.append(("headsup_win", winner_names))
            loser_names = [p.name for p in players_with_chips if p.name not in winner_names]
            if loser_names:
                events.append(("headsup_loss", loser_names))
        
        # Detect bad beat (strong hand loses)
        if len(active_players) > 1 and winner_names:
            # Find second-best hand
            losers_with_hands = []
            for player in active_players:
                if player.name not in winner_names:
                    # Convert cards to proper format for HandEvaluator
                    player_cards = []
                    for card in player.hand:
                        if hasattr(card, 'to_dict'):
                            player_cards.append(card)
                        else:
                            # Convert dict to Card object
                            from core.card import Card
                            player_cards.append(Card(card['rank'], card['suit']))
                    
                    community_cards = []
                    for card in game_state.community_cards:
                        if hasattr(card, 'to_dict'):
                            community_cards.append(card)
                        else:
                            from core.card import Card
                            community_cards.append(Card(card['rank'], card['suit']))
                    
                    hand_result = HandEvaluator(
                        player_cards + community_cards
                    ).evaluate_hand()
                    losers_with_hands.append((player.name, hand_result['hand_rank']))
            
            if losers_with_hands:
                # Sort by hand rank (lower is better)
                losers_with_hands.sort(key=lambda x: x[1])
                second_best_name, second_best_rank = losers_with_hands[0]
                
                # Bad beat: very strong hand loses
                if second_best_rank <= 4 and winner_hand_rank > second_best_rank:
                    events.append(("bad_beat", [second_best_name]))
        
        return events
    
    def detect_fold_events(self, game_state: PokerGameState, 
                          folding_player: Player,
                          remaining_players: List[Player]) -> List[Tuple[str, List[str]]]:
        """Detect pressure events from a fold action."""
        events = []
        
        # If only one player remains after fold, check for bluff
        if len(remaining_players) == 1:
            winner = remaining_players[0]
            pot_total = game_state.pot.get('total', 0) if isinstance(game_state.pot, dict) else 0
            avg_stack = sum(p.stack for p in game_state.players if p.stack > 0) / len(
                [p for p in game_state.players if p.stack > 0]
            )
            
            # Only count as potential bluff if pot is significant
            if pot_total > avg_stack * 0.5:
                # We can't know for sure without seeing cards, but high aggression
                # suggests possible bluff
                if hasattr(winner, 'elastic_personality'):
                    aggression = winner.elastic_personality.get_trait_value('aggression')
                    if aggression > 0.7:
                        events.append(("successful_bluff", [winner.name]))
        
        return events
    
    def detect_elimination_events(self, game_state: PokerGameState,
                                eliminated_players: List[str]) -> List[Tuple[str, List[str]]]:
        """Detect pressure events from player eliminations."""
        events = []
        
        if eliminated_players:
            # Surviving players feel empowered
            survivors = [p.name for p in game_state.players 
                        if p.name not in eliminated_players and p.stack > 0]
            for eliminated in eliminated_players:
                events.append(("eliminated_opponent", survivors))
        
        return events
    
    def detect_chat_events(self, sender: str, message: str, 
                          recipients: List[str]) -> List[Tuple[str, List[str]]]:
        """Detect pressure events from chat interactions."""
        events = []
        
        # Simple sentiment detection
        friendly_words = ['nice', 'good', 'great', 'love', 'thanks', 'appreciate']
        aggressive_words = ['scared', 'weak', 'donkey', 'fool', 'terrible', 'stupid']
        
        message_lower = message.lower()
        
        if any(word in message_lower for word in friendly_words):
            events.append(("friendly_chat", recipients))
        elif any(word in message_lower for word in aggressive_words):
            events.append(("rivalry_trigger", recipients))

        return events

    # Equity-based event detection thresholds
    COOLER_MIN_EQUITY = 0.30       # Both players had 30%+ equity on flop
    SUCKOUT_THRESHOLD = 0.40      # Winner was <40% equity on turn
    GOT_SUCKED_OUT_THRESHOLD = 0.60  # Loser was >60% equity on turn
    BAD_BEAT_EQUITY_THRESHOLD = 0.70  # Loser was >70% favorite on flop

    def detect_equity_events(
        self,
        game_state: PokerGameState,
        winner_info: Dict[str, Any],
        equity_history: 'HandEquityHistory',
        pot_size: Optional[int] = None,
    ) -> List[Tuple[str, List[str]]]:
        """
        Detect equity-based pressure events: cooler, suckout, got_sucked_out.

        These events require equity history data calculated at showdown.

        Args:
            game_state: Current game state
            winner_info: Winner information from showdown
            equity_history: HandEquityHistory with equity snapshots for all streets
            pot_size: Pot size before award (pass explicitly since game_state.pot may be 0)

        Returns:
            List of (event_name, affected_players) tuples
        """
        events = []

        if not equity_history or not equity_history.snapshots:
            return events

        # Extract winner names from pot_breakdown
        winner_names = []
        pot_breakdown = winner_info.get('pot_breakdown', [])
        for pot in pot_breakdown:
            for winner in pot.get('winners', []):
                if winner['name'] not in winner_names:
                    winner_names.append(winner['name'])

        if not winner_names:
            return events

        # Get players who made it to showdown (not folded)
        showdown_players = [p.name for p in game_state.players if not p.is_folded]

        if len(showdown_players) < 2:
            return events  # Need 2+ players for equity-based events

        # Check for big pot (only apply some events to big pots)
        # Use provided pot_size since game_state.pot may be 0 after awarding
        pot_total = pot_size if pot_size is not None else (
            game_state.pot.get('total', 0) if isinstance(game_state.pot, dict) else 0
        )
        active_stacks = [p.stack for p in game_state.players if p.stack > 0]
        avg_stack = sum(active_stacks) / len(active_stacks) if active_stacks else 1000
        is_big_pot = MomentAnalyzer.is_big_pot(pot_total, 0, avg_stack)

        # Detect cooler
        cooler_events = self._detect_cooler(showdown_players, winner_names, equity_history)
        events.extend(cooler_events)

        # Detect suckout (only for big pots - small pots don't matter much)
        if is_big_pot:
            suckout_events = self._detect_suckout(winner_names, equity_history)
            events.extend(suckout_events)

            got_sucked_out_events = self._detect_got_sucked_out(
                showdown_players, winner_names, equity_history
            )
            events.extend(got_sucked_out_events)

        # Equity-based bad beat (replaces rank-based detection)
        bad_beat_events = self._detect_bad_beat_equity(
            showdown_players, winner_names, equity_history
        )
        events.extend(bad_beat_events)

        return events

    def _detect_cooler(
        self,
        showdown_players: List[str],
        winner_names: List[str],
        equity_history: 'HandEquityHistory',
    ) -> List[Tuple[str, List[str]]]:
        """
        Detect cooler: Both players had strong hands (>30% equity on flop).

        A cooler is an unavoidable loss - both players had legitimate hands.
        This is less tilting than a bad beat since the loser "did nothing wrong."
        """
        events = []
        flop_equities = equity_history.get_active_street_equities('FLOP')

        if not flop_equities or len(flop_equities) < 2:
            return events

        # Find losers who had strong equity on the flop
        for player_name in showdown_players:
            if player_name in winner_names:
                continue  # Winners don't get cooler event

            player_equity = flop_equities.get(player_name, 0)
            winner_equity = max(
                flop_equities.get(w, 0) for w in winner_names if w in flop_equities
            ) if any(w in flop_equities for w in winner_names) else 0

            # Both had strong flop equity
            if player_equity >= self.COOLER_MIN_EQUITY and winner_equity >= self.COOLER_MIN_EQUITY:
                events.append(("cooler", [player_name]))

        return events

    def _detect_suckout(
        self,
        winner_names: List[str],
        equity_history: 'HandEquityHistory',
    ) -> List[Tuple[str, List[str]]]:
        """
        Detect suckout: Winner was behind (<40% equity) on turn but won.

        This means the winner got lucky - they were losing and caught a card.
        """
        events = []
        turn_equities = equity_history.get_active_street_equities('TURN')

        if not turn_equities:
            # Try flop if turn not available
            turn_equities = equity_history.get_active_street_equities('FLOP')

        if not turn_equities:
            return events

        suckout_winners = []
        for winner_name in winner_names:
            winner_equity = turn_equities.get(winner_name, 1.0)
            if winner_equity < self.SUCKOUT_THRESHOLD:
                suckout_winners.append(winner_name)

        if suckout_winners:
            events.append(("suckout", suckout_winners))

        return events

    def _detect_got_sucked_out(
        self,
        showdown_players: List[str],
        winner_names: List[str],
        equity_history: 'HandEquityHistory',
    ) -> List[Tuple[str, List[str]]]:
        """
        Detect got_sucked_out: Loser was ahead (>60% equity) on turn but lost.

        This is very tilting - the loser was winning and got unlucky.
        """
        events = []
        turn_equities = equity_history.get_active_street_equities('TURN')

        if not turn_equities:
            # Try flop if turn not available
            turn_equities = equity_history.get_active_street_equities('FLOP')

        if not turn_equities:
            return events

        victims = []
        for player_name in showdown_players:
            if player_name in winner_names:
                continue  # Winners didn't get sucked out

            player_equity = turn_equities.get(player_name, 0)
            if player_equity > self.GOT_SUCKED_OUT_THRESHOLD:
                victims.append(player_name)

        if victims:
            events.append(("got_sucked_out", victims))

        return events

    def _detect_bad_beat_equity(
        self,
        showdown_players: List[str],
        winner_names: List[str],
        equity_history: 'HandEquityHistory',
    ) -> List[Tuple[str, List[str]]]:
        """
        Detect equity-based bad beat: Loser had >70% equity on flop but lost.

        This replaces the rank-based bad beat detection with equity-based.
        More accurate since equity accounts for draws and actual win probability.
        """
        events = []
        flop_equities = equity_history.get_active_street_equities('FLOP')

        if not flop_equities:
            return events

        bad_beat_victims = []
        for player_name in showdown_players:
            if player_name in winner_names:
                continue  # Winners don't get bad beat

            player_equity = flop_equities.get(player_name, 0)
            if player_equity > self.BAD_BEAT_EQUITY_THRESHOLD:
                bad_beat_victims.append(player_name)

        if bad_beat_victims:
            # Note: This may overlap with got_sucked_out - that's OK,
            # they're related but bad_beat is specifically about flop equity
            events.append(("bad_beat", bad_beat_victims))

        return events

    # === Streak-based event detection ===

    def detect_streak_events(
        self,
        player_name: str,
        session_stats: Dict[str, Any]
    ) -> List[Tuple[str, List[str]]]:
        """Detect winning/losing streak events from session statistics.

        Args:
            player_name: The player to check
            session_stats: Dict from hand_history_repo.get_player_session_stats()
                containing 'current_streak' and 'streak_count' keys

        Returns:
            List of (event_name, [player_name]) tuples
        """
        events = []
        streak_count = session_stats.get('streak_count', 0)
        current_streak = session_stats.get('current_streak', 'neutral')

        if streak_count >= 3:
            if current_streak == 'winning':
                events.append(("winning_streak", [player_name]))
            elif current_streak == 'losing':
                events.append(("losing_streak", [player_name]))

        return events

    # === Stack-based event detection ===

    # Stack event thresholds
    SHORT_STACK_BB = 10      # Below 10 BB is short-stacked
    CRIPPLED_LOSS_PCT = 0.75  # Lost 75%+ of stack = crippled
    DOUBLE_UP_MULTIPLIER = 2  # Ended with 2x+ starting stack

    def detect_stack_events(
        self,
        game_state: PokerGameState,
        winner_names: List[str],
        hand_start_stacks: Dict[str, int],
        was_short_stack: Set[str],
        big_blind: int = 100
    ) -> Tuple[List[Tuple[str, List[str]]], Set[str]]:
        """Detect stack-based pressure events: double_up, crippled, short_stack.

        Args:
            game_state: Current game state after hand completion
            winner_names: List of players who won the hand
            hand_start_stacks: Dict mapping player names to stack at hand start
            was_short_stack: Set of player names who were short-stacked last hand
            big_blind: Big blind amount for short-stack calculation

        Returns:
            Tuple of (events, current_short_stack_set)
            - events: List of (event_name, [player_names]) tuples
            - current_short_stack_set: Updated set of short-stacked players
        """
        events = []
        current_short = set()

        for player in game_state.players:
            if player.stack <= 0:
                continue

            start_stack = hand_start_stacks.get(player.name)
            current_stack = player.stack

            # Double up: ended with 2x+ starting stack (only for winners)
            if (start_stack and start_stack > 0 and
                player.name in winner_names and
                current_stack >= self.DOUBLE_UP_MULTIPLIER * start_stack):
                events.append(("double_up", [player.name]))

            # Crippled: lost 75%+ of stack this hand (only for non-winners)
            elif (start_stack and start_stack > 0 and
                  player.name not in winner_names):
                loss_pct = (start_stack - current_stack) / start_stack
                if loss_pct >= self.CRIPPLED_LOSS_PCT:
                    events.append(("crippled", [player.name]))

            # Track short stack status (below threshold BB)
            if current_stack < self.SHORT_STACK_BB * big_blind:
                current_short.add(player.name)

        # Only fire short_stack for NEW short stacks (transition detection)
        newly_short = current_short - was_short_stack
        for player_name in newly_short:
            events.append(("short_stack", [player_name]))

        return events, current_short

    # === Nemesis-based event detection ===

    def detect_nemesis_events(
        self,
        winner_names: List[str],
        loser_names: List[str],
        player_nemesis_map: Dict[str, str]
    ) -> List[Tuple[str, List[str]]]:
        """Detect nemesis win/loss events.

        Fires when a player wins/loses a pot that their nemesis was also
        involved in. Works in multiway pots, not just heads-up.

        Args:
            winner_names: List of players who won the hand
            loser_names: List of players who lost the hand (didn't fold, didn't win)
            player_nemesis_map: Dict mapping player names to their nemesis name

        Returns:
            List of (event_name, [player_name]) tuples
        """
        events = []

        for player_name, nemesis in player_nemesis_map.items():
            if not nemesis:
                continue

            # Player won and their nemesis lost in the same pot
            if player_name in winner_names and nemesis in loser_names:
                events.append(("nemesis_win", [player_name]))
            # Player lost and their nemesis won
            elif player_name in loser_names and nemesis in winner_names:
                events.append(("nemesis_loss", [player_name]))

        return events

