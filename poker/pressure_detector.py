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

    # Cooldown thresholds
    DISCIPLINED_FOLD_COOLDOWN = 2   # Max once per 2 hands per player
    SHORT_STACK_SURVIVAL_COOLDOWN = 5  # Max once per 5 hands per player
    SHORT_STACK_SURVIVAL_HANDS = 3    # Must survive 3+ hands while short without all-in

    # Self-reported bluff_likelihood >= this counts as "was bluffing"
    BLUFF_LIKELIHOOD_THRESHOLD = 50

    def __init__(self):
        self.last_pot_size = 0
        self.player_hand_history: Dict[str, List[int]] = {}  # Track hand strengths

        # Disciplined fold cooldown: player_name -> hand_number of last fire
        self._disciplined_fold_last_hand: Dict[str, int] = {}

        # Short-stack survival tracking
        # player_name -> number of consecutive hands survived while short without all-in
        self._short_stack_hands_survived: Dict[str, int] = {}
        # player_name -> hand_number of last short_stack_survival fire
        self._short_stack_survival_last_hand: Dict[str, int] = {}
        
    def _was_bluffing(self, player_name: str, hand_rank: int,
                      player_bluff_likelihoods: Optional[Dict[str, int]] = None) -> bool:
        """Check if a player was bluffing via hand rank OR self-reported likelihood."""
        if hand_rank >= 9:  # One pair or high card
            return True
        if player_bluff_likelihoods:
            return player_bluff_likelihoods.get(player_name, 0) >= self.BLUFF_LIKELIHOOD_THRESHOLD
        return False

    def detect_showdown_events(self, game_state: PokerGameState,
                             winner_info: Dict[str, Any],
                             player_bluff_likelihoods: Optional[Dict[str, int]] = None) -> List[Tuple[str, List[str]]]:
        """
        Detect pressure events from showdown results.

        Args:
            player_bluff_likelihoods: Optional dict of player_name -> max bluff_likelihood (0-100)
                from their self-reported LLM responses this hand.

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
        # Priority system: only ONE win-type event per winner (successful_bluff > big_win > win)
        # Triggers on weak hand rank (pair or worse) OR self-reported bluff_likelihood >= 50
        bluff_winners = set()
        if winner_name and is_big_pot and len(active_players) == 1:
            if self._was_bluffing(winner_name, winner_hand_rank, player_bluff_likelihoods):
                events.append(("successful_bluff", [winner_name]))
                bluff_winners.add(winner_name)

        # Track wins — only ONE of successful_bluff / big_win / win per winner
        if winner_names and pot_total > 0:
            non_bluff_winners = [w for w in winner_names if w not in bluff_winners]

            if is_big_pot:
                # Big win for non-bluff winners (bluff winners already got successful_bluff)
                if non_bluff_winners:
                    events.append(("big_win", non_bluff_winners))
                # Regular win for any remaining winners not covered by big_win or bluff
                # (none — big_win already covers them)
            else:
                # Small pot: regular win
                if non_bluff_winners:
                    events.append(("win", non_bluff_winners))

            # Losses
            if is_big_pot:
                losers = [p.name for p in active_players if p.name not in winner_names]
                if losers:
                    events.append(("big_loss", losers))
            else:
                # Small pot losers (showdown losers who aren't winners)
                losers = [p.name for p in active_players if p.name not in winner_names]
                if losers:
                    events.append(("loss", losers))

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

                # Bluff called: loser was bluffing (weak hand OR self-reported bluff_likelihood >= 50)
                bluff_called_players = [
                    name for name, rank in losers_with_hands
                    if self._was_bluffing(name, rank, player_bluff_likelihoods)
                ]
                if bluff_called_players:
                    events.append(("bluff_called", bluff_called_players))

        return events
    
    def detect_fold_events(self, game_state: PokerGameState,
                          folding_player: Player,
                          remaining_players: List[Player],
                          winner_bluff_likelihood: int = 0) -> List[Tuple[str, List[str]]]:
        """Detect pressure events from a fold action.

        Args:
            winner_bluff_likelihood: Self-reported bluff_likelihood (0-100) from the
                winner's LLM responses this hand. Used alongside aggression heuristic.
        """
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
                # Self-reported bluff likelihood is the strongest signal (no cards shown)
                if winner_bluff_likelihood >= self.BLUFF_LIKELIHOOD_THRESHOLD:
                    events.append(("successful_bluff", [winner.name]))
                # Fallback: high aggression trait suggests possible bluff
                elif hasattr(winner, 'elastic_personality'):
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

    # Weighted-delta equity shock detection thresholds
    EQUITY_SHOCK_THRESHOLD = 0.30      # Minimum weighted delta to trigger an event
    BAD_BEAT_EQUITY_MIN = 0.80         # Loser had 80%+ equity at worst swing
    COOLER_EQUITY_MIN = 0.60           # Loser had 60-80% equity at worst swing
    POT_SIGNIFICANCE_MIN = 0.15        # Ignore swings in trivial pots

    # Street weights (later streets feel worse)
    STREET_WEIGHTS = {
        'FLOP': 1.0,
        'TURN': 1.2,
        'RIVER': 1.4,
    }

    def detect_equity_shock_events(
        self,
        equity_history: 'HandEquityHistory',
        winner_names: List[str],
        pot_size: int,
        hand_start_stacks: Dict[str, int],
    ) -> List[Tuple[str, List[str]]]:
        """
        Detect equity shock events using a weighted-delta model.

        For each player, tracks equity swings across streets weighted by
        pot significance and street weight. Fires at most ONE event per player.

        Priority: bad_beat > got_sucked_out > cooler > suckout

        Args:
            equity_history: HandEquityHistory with equity snapshots for all streets
            winner_names: List of players who won the hand
            pot_size: Total pot size
            hand_start_stacks: Dict mapping player names to stack at hand start

        Returns:
            List of (event_name, [player_name]) tuples
        """
        from .equity_snapshot import STREET_ORDER

        events = []

        if not equity_history or not equity_history.snapshots:
            return events

        for player_name in equity_history.get_player_names():
            # Calculate pot significance for this player
            player_stack = hand_start_stacks.get(player_name, 0)
            if player_stack <= 0:
                continue
            pot_significance = pot_size / player_stack
            if pot_significance < self.POT_SIGNIFICANCE_MIN:
                continue

            history = equity_history.get_player_history(player_name)
            if len(history) < 2:
                continue

            # Only consider active snapshots
            active_history = [s for s in history if s.was_active]
            if len(active_history) < 2:
                continue

            # Find largest positive and negative weighted deltas
            max_positive_wd = 0.0
            max_negative_wd = 0.0
            worst_swing_prev_equity = 0.0  # Equity before the worst negative swing

            for i in range(len(active_history) - 1):
                prev = active_history[i]
                next_ = active_history[i + 1]

                delta = next_.equity - prev.equity
                street_weight = self.STREET_WEIGHTS.get(next_.street, 1.0)
                weighted_delta = delta * pot_significance * street_weight

                if weighted_delta > max_positive_wd:
                    max_positive_wd = weighted_delta
                if weighted_delta < max_negative_wd:
                    max_negative_wd = weighted_delta
                    worst_swing_prev_equity = prev.equity

            # Determine event (at most one per player, by priority)
            player_won = player_name in winner_names
            player_lost = not player_won

            event = None

            # bad_beat: lost AND had 80%+ equity at worst swing AND big negative delta
            if (player_lost
                    and worst_swing_prev_equity >= self.BAD_BEAT_EQUITY_MIN
                    and max_negative_wd <= -self.EQUITY_SHOCK_THRESHOLD):
                event = 'bad_beat'
            # cooler: lost AND had 60-80% equity at worst swing AND big negative delta
            elif (player_lost
                  and self.COOLER_EQUITY_MIN <= worst_swing_prev_equity < self.BAD_BEAT_EQUITY_MIN
                  and max_negative_wd <= -self.EQUITY_SHOCK_THRESHOLD):
                event = 'cooler'
            # got_sucked_out: lost AND big negative delta (no equity constraint)
            elif (player_lost
                  and max_negative_wd <= -self.EQUITY_SHOCK_THRESHOLD):
                event = 'got_sucked_out'
            # suckout: won AND big positive delta (they got lucky)
            elif (player_won
                  and max_positive_wd >= self.EQUITY_SHOCK_THRESHOLD):
                event = 'suckout'

            if event:
                events.append((event, [player_name]))

        return events

    # === Action-based event detection ===

    def detect_action_events(
        self,
        game_state: PokerGameState,
        player_name: str,
        action: str,
        amount: int = 0,
        hand_number: int = 0,
    ) -> List[Tuple[str, List[str]]]:
        """Detect pressure events from player actions (all_in_moment, disciplined_fold).

        Args:
            game_state: Current game state
            player_name: Name of the player who acted
            action: The action taken (e.g., 'all_in', 'raise', 'call', 'fold')
            amount: Bet/raise amount
            hand_number: Current hand number (for cooldown tracking)

        Returns:
            List of (event_name, [player_name]) tuples
        """
        events = []

        # All-in moment: player went all-in
        if action == 'all_in':
            events.append(("all_in_moment", [player_name]))
            # Reset short-stack survival counter on all-in
            self._short_stack_hands_survived.pop(player_name, None)

        # Disciplined fold: fold on turn/river with decent equity in a significant pot
        if action == 'fold':
            fold_event = self._detect_disciplined_fold(
                game_state, player_name, hand_number
            )
            if fold_event:
                events.append(fold_event)

        return events

    # Disciplined fold detection thresholds
    DISCIPLINED_FOLD_MIN_EQUITY = 0.25
    DISCIPLINED_FOLD_MIN_POT_SIGNIFICANCE = 0.15
    DISCIPLINED_FOLD_MIN_COMMUNITY_CARDS = 4  # Turn or later

    def _detect_disciplined_fold(
        self,
        game_state: PokerGameState,
        player_name: str,
        hand_number: int,
    ) -> Optional[Tuple[str, List[str]]]:
        """Detect a disciplined fold: folding a decent hand in a significant pot.

        Fires when:
        - Player folds on turn or river (4+ community cards)
        - Player's estimated equity >= 0.25
        - Pot significance (pot / player_stack) >= 0.15
        - Cooldown: at most once per 2 hands per player
        """
        # Check cooldown
        last_hand = self._disciplined_fold_last_hand.get(player_name, -999)
        if hand_number - last_hand < self.DISCIPLINED_FOLD_COOLDOWN:
            return None

        # Must be on turn or river
        community_cards = game_state.community_cards
        if len(community_cards) < self.DISCIPLINED_FOLD_MIN_COMMUNITY_CARDS:
            return None

        # Must be facing a bet (cost to call > 0)
        player = next(
            (p for p in game_state.players if p.name == player_name), None
        )
        if not player:
            return None

        cost_to_call = game_state.highest_bet - player.bet
        if cost_to_call <= 0:
            return None

        # Check pot significance
        pot_total = game_state.pot.get('total', 0) if isinstance(game_state.pot, dict) else 0
        player_stack = player.stack + player.bet  # Effective stack
        if player_stack <= 0:
            return None
        pot_significance = pot_total / player_stack
        if pot_significance < self.DISCIPLINED_FOLD_MIN_POT_SIGNIFICANCE:
            return None

        # Calculate equity
        equity = self._calculate_fold_equity(player.hand, community_cards)
        if equity is None or equity < self.DISCIPLINED_FOLD_MIN_EQUITY:
            return None

        # All conditions met
        self._disciplined_fold_last_hand[player_name] = hand_number
        return ("disciplined_fold", [player_name])

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

        # Fire only at milestone thresholds (not every hand the streak persists)
        if streak_count in (3, 6):
            if current_streak == 'winning':
                events.append(("winning_streak", [player_name]))
            elif current_streak == 'losing':
                events.append(("losing_streak", [player_name]))

        return events

    # === Stack-based event detection ===

    # Stack event thresholds
    SHORT_STACK_BB = 10      # Below 10 BB is short-stacked
    CRIPPLED_LOSS_PCT = 0.75  # Lost 75%+ of stack = crippled

    def detect_stack_events(
        self,
        game_state: PokerGameState,
        winner_names: List[str],
        hand_start_stacks: Dict[str, int],
        was_short_stack: Set[str],
        big_blind: int = 100
    ) -> Tuple[List[Tuple[str, List[str]]], Set[str]]:
        """Detect stack-based pressure events: crippled, short_stack.

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

            # Crippled: lost 75%+ of stack this hand (only for non-winners)
            if (start_stack and start_stack > 0 and
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
        player_nemesis_map: Dict[str, str],
        is_big_pot: bool = True,
    ) -> List[Tuple[str, List[str]]]:
        """Detect nemesis win/loss events.

        Fires when a player wins/loses a pot that their nemesis was also
        involved in. Works in multiway pots, not just heads-up.

        Args:
            winner_names: List of players who won the hand
            loser_names: List of players who lost the hand (didn't fold, didn't win)
            player_nemesis_map: Dict mapping player names to their nemesis name
            is_big_pot: If False, skip nemesis events (small pots don't warrant them)

        Returns:
            List of (event_name, [player_name]) tuples
        """
        if not is_big_pot:
            return []

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

    # === Disciplined fold equity calculation ===

    def _calculate_fold_equity(self, hole_cards, community_cards, num_simulations: int = 200) -> Optional[float]:
        """Quick equity estimate for disciplined fold detection.

        Uses eval7 Monte Carlo simulation against one random opponent.
        Returns equity as 0.0-1.0, or None if calculation fails.
        """
        try:
            import eval7
            from .card_utils import normalize_card_string
        except ImportError:
            return None

        if not hole_cards or not community_cards:
            return None

        try:
            hand = [eval7.Card(normalize_card_string(str(c))) for c in hole_cards]
            board = [eval7.Card(normalize_card_string(str(c))) for c in community_cards]

            wins = 0
            for _ in range(num_simulations):
                deck = eval7.Deck()
                for c in hand + board:
                    deck.cards.remove(c)
                deck.shuffle()

                opp_hand = list(deck.deal(2))
                remaining = 5 - len(board)
                full_board = board + list(deck.deal(remaining))

                hero_score = eval7.evaluate(hand + full_board)
                opp_score = eval7.evaluate(opp_hand + full_board)

                if hero_score > opp_score:
                    wins += 1
                elif hero_score == opp_score:
                    wins += 0.5

            return wins / num_simulations
        except Exception:
            return None

    # === Short-stack survival event detection ===

    def detect_short_stack_survival_events(
        self,
        current_short_stack: Set[str],
        hand_number: int,
    ) -> List[Tuple[str, List[str]]]:
        """Detect short_stack_survival: player stayed short-stacked without going all-in.

        Called once per hand completion. Increments the survival counter for
        players who are currently short-stacked, and fires the event when
        the threshold is met (subject to cooldown).

        Args:
            current_short_stack: Set of player names currently short-stacked
            hand_number: Current hand number (for cooldown tracking)

        Returns:
            List of (event_name, [player_name]) tuples
        """
        events = []

        # Remove players who are no longer short-stacked
        for player_name in list(self._short_stack_hands_survived.keys()):
            if player_name not in current_short_stack:
                self._short_stack_hands_survived.pop(player_name, None)

        # Increment survival counter for current short-stack players
        for player_name in current_short_stack:
            count = self._short_stack_hands_survived.get(player_name, 0) + 1
            self._short_stack_hands_survived[player_name] = count

            # Check if threshold met and cooldown expired
            if count >= self.SHORT_STACK_SURVIVAL_HANDS:
                last_fired = self._short_stack_survival_last_hand.get(player_name, -999)
                if hand_number - last_fired >= self.SHORT_STACK_SURVIVAL_COOLDOWN:
                    events.append(("short_stack_survival", [player_name]))
                    self._short_stack_survival_last_hand[player_name] = hand_number
                    # Reset counter after firing
                    self._short_stack_hands_survived[player_name] = 0

        return events

