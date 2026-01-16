"""
Pressure Event Detection for Elastic Personality System.

This module detects game events that should trigger pressure changes
in AI player personalities.
"""

from typing import Dict, List, Optional, Tuple, Any
from .poker_game import PokerGameState, Player
from .elasticity_manager import ElasticityManager
from .hand_evaluator import HandEvaluator


class PressureEventDetector:
    """Detects and triggers pressure events based on game outcomes."""
    
    def __init__(self, elasticity_manager: ElasticityManager):
        self.elasticity_manager = elasticity_manager
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
        
        # More reasonable threshold - pot > 0.75x average stack is considered "big"
        is_big_pot = pot_total > avg_stack * 0.75
        
        # Get active players who showed cards
        active_players = [p for p in game_state.players if not p.is_folded]
        
        # Detect successful bluff (weak hand wins big pot)
        if winner_name and winner_hand_rank >= 8 and is_big_pot and len(active_players) == 1:
            # Winner bluffed everyone out
            events.append(("successful_bluff", [winner_name]))
            # Other players feel pressure from being bluffed
            other_players = [p.name for p in game_state.players if p.name != winner_name]
            events.append(("bluff_called", other_players))
        
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
    
    def apply_detected_events(self, events: List[Tuple[str, List[str]]]) -> None:
        """Apply all detected pressure events to affected players."""
        for event_name, affected_players in events:
            self.elasticity_manager.apply_game_event(event_name, affected_players)
    
    def apply_recovery(self) -> None:
        """Apply trait recovery to all players."""
        self.elasticity_manager.recover_all()
    
    def get_pressure_summary(self) -> Dict[str, Dict[str, float]]:
        """Get current pressure levels for all players."""
        summary = {}
        for name, personality in self.elasticity_manager.personalities.items():
            summary[name] = {
                'avg_pressure': sum(t.pressure for t in personality.traits.values()) / len(personality.traits),
                'mood': personality.get_current_mood(),
                'traits': {
                    trait_name: {
                        'value': trait.value,
                        'pressure': trait.pressure,
                        'deviation': abs(trait.value - trait.anchor)
                    }
                    for trait_name, trait in personality.traits.items()
                }
            }
        return summary