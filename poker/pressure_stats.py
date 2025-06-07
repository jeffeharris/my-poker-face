"""
Pressure Event Statistics Tracking.

This module tracks and aggregates pressure events for analytics and fun stats.
"""

from typing import Dict, List, Any, Optional
from datetime import datetime
from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class PressureEvent:
    """Records a single pressure event."""
    timestamp: datetime
    event_type: str
    player_name: str
    details: Dict[str, Any] = field(default_factory=dict)
    

@dataclass 
class PlayerPressureStats:
    """Tracks pressure statistics for a single player."""
    player_name: str
    events: List[PressureEvent] = field(default_factory=list)
    
    # Event counters
    wins: int = 0  # Total wins (any size)
    big_wins: int = 0
    big_losses: int = 0
    successful_bluffs: int = 0
    bluffs_caught: int = 0
    bad_beats_suffered: int = 0
    bad_beats_delivered: int = 0
    eliminations: int = 0
    fold_under_pressure: int = 0
    aggressive_bets: int = 0
    
    # Derived stats
    biggest_pot_won: int = 0
    biggest_pot_lost: int = 0
    most_tilted_moment: float = 0.0  # Max pressure level
    comeback_count: int = 0  # Times recovered from high negative pressure
    
    def add_event(self, event: PressureEvent) -> None:
        """Add an event and update counters."""
        self.events.append(event)
        
        # Update counters based on event type
        if event.event_type == "win":
            self.wins += 1
            pot_size = event.details.get('pot_size', 0)
            self.biggest_pot_won = max(self.biggest_pot_won, pot_size)
            
        elif event.event_type == "big_win":
            self.big_wins += 1
            pot_size = event.details.get('pot_size', 0)
            self.biggest_pot_won = max(self.biggest_pot_won, pot_size)
            
        elif event.event_type == "big_loss":
            self.big_losses += 1
            pot_size = event.details.get('pot_size', 0)
            self.biggest_pot_lost = max(self.biggest_pot_lost, pot_size)
            
        elif event.event_type == "successful_bluff":
            self.successful_bluffs += 1
            
        elif event.event_type == "bluff_called":
            self.bluffs_caught += 1
            
        elif event.event_type == "bad_beat":
            self.bad_beats_suffered += 1
            
        elif event.event_type == "eliminated_opponent":
            self.eliminations += 1
            
        elif event.event_type == "fold_under_pressure":
            self.fold_under_pressure += 1
            
        elif event.event_type == "aggressive_bet":
            self.aggressive_bets += 1
    
    def get_tilt_score(self) -> float:
        """Calculate current tilt level (0-1)."""
        # More losses and bad beats = higher tilt
        negative_events = self.big_losses + self.bad_beats_suffered + self.bluffs_caught
        positive_events = self.big_wins + self.successful_bluffs + self.eliminations
        
        if negative_events + positive_events == 0:
            return 0.0
            
        return negative_events / (negative_events + positive_events)
    
    def get_aggression_score(self) -> float:
        """Calculate aggression level based on events."""
        if self.aggressive_bets + self.successful_bluffs == 0:
            return 0.0
            
        total_actions = len(self.events)
        if total_actions == 0:
            return 0.0
            
        return (self.aggressive_bets + self.successful_bluffs) / total_actions
    
    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of stats."""
        return {
            'total_events': len(self.events),
            'wins': self.wins,  # Include total wins
            'big_wins': self.big_wins,
            'big_losses': self.big_losses,
            'successful_bluffs': self.successful_bluffs,
            'bluffs_caught': self.bluffs_caught,
            'bad_beats': self.bad_beats_suffered,
            'eliminations': self.eliminations,
            'biggest_pot_won': self.biggest_pot_won,
            'biggest_pot_lost': self.biggest_pot_lost,
            'tilt_score': self.get_tilt_score(),
            'aggression_score': self.get_aggression_score(),
            'signature_move': self._get_signature_move()
        }
    
    def _get_signature_move(self) -> str:
        """Determine player's signature move based on stats."""
        if self.successful_bluffs > 2:
            return "Master Bluffer"
        elif self.big_wins > 3:
            return "Pot Hunter"
        elif self.aggressive_bets > 5:
            return "Table Bully"
        elif self.fold_under_pressure > 3:
            return "Cautious Player"
        elif self.bad_beats_suffered > 2:
            return "Unlucky Hero"
        elif self.eliminations > 1:
            return "Assassin"
        else:
            return "Steady Player"


class PressureStatsTracker:
    """Tracks pressure statistics for all players in a game.
    
    Supports optional persistence to database via event repository.
    """
    
    def __init__(self, game_id: Optional[str] = None, event_repository=None):
        self.game_id = game_id
        self.event_repository = event_repository
        self.player_stats: Dict[str, PlayerPressureStats] = {}
        self.game_stats = {
            'total_events': 0,
            'biggest_pot': 0,
            'most_dramatic_showdown': None,
            'session_start': datetime.now()
        }
        
        # Load existing events from database if repository provided
        if self.event_repository and self.game_id:
            self._load_from_database()
        
    def record_event(self, event_type: str, player_names: List[str], 
                    details: Optional[Dict[str, Any]] = None) -> None:
        """Record a pressure event for one or more players."""
        if details is None:
            details = {}
            
        for player_name in player_names:
            if player_name not in self.player_stats:
                self.player_stats[player_name] = PlayerPressureStats(player_name)
            
            event = PressureEvent(
                timestamp=datetime.now(),
                event_type=event_type,
                player_name=player_name,
                details=details
            )
            
            self.player_stats[player_name].add_event(event)
            self.game_stats['total_events'] += 1
            
            # Update game-wide stats
            if 'pot_size' in details:
                self.game_stats['biggest_pot'] = max(
                    self.game_stats['biggest_pot'], 
                    details['pot_size']
                )
            
            # Save to database if repository is available
            if self.event_repository and self.game_id:
                try:
                    self.event_repository.save_event(
                        self.game_id, player_name, event_type, details
                    )
                except Exception as e:
                    # Log error but don't fail - maintain backward compatibility
                    print(f"Failed to save pressure event to database: {e}")
    
    def get_player_stats(self, player_name: str) -> Dict[str, Any]:
        """Get stats for a specific player."""
        if player_name not in self.player_stats:
            return {}
        return self.player_stats[player_name].get_summary()
    
    def get_leaderboard(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get various leaderboards."""
        players = list(self.player_stats.values())
        
        return {
            'biggest_winners': sorted(
                [{'name': p.player_name, 'wins': p.wins, 'biggest_pot': p.biggest_pot_won} 
                 for p in players if p.wins > 0],
                key=lambda x: x['wins'], reverse=True
            )[:3],
            
            'master_bluffers': sorted(
                [{'name': p.player_name, 'bluffs': p.successful_bluffs} 
                 for p in players if p.successful_bluffs > 0],
                key=lambda x: x['bluffs'], reverse=True
            )[:3],
            
            'most_aggressive': sorted(
                [{'name': p.player_name, 'aggression': p.get_aggression_score(), 
                  'aggressive_bets': p.aggressive_bets} 
                 for p in players],
                key=lambda x: x['aggression'], reverse=True
            )[:3],
            
            'bad_beat_victims': sorted(
                [{'name': p.player_name, 'bad_beats': p.bad_beats_suffered} 
                 for p in players if p.bad_beats_suffered > 0],
                key=lambda x: x['bad_beats'], reverse=True
            )[:3],
            
            'tilt_masters': sorted(
                [{'name': p.player_name, 'tilt_score': p.get_tilt_score()} 
                 for p in players if p.get_tilt_score() > 0],
                key=lambda x: x['tilt_score'], reverse=True
            )[:3]
        }
    
    def get_fun_facts(self) -> List[str]:
        """Generate fun facts from the statistics."""
        facts = []
        
        # Find most dramatic moments
        for player_name, stats in self.player_stats.items():
            if stats.successful_bluffs >= 3:
                facts.append(f"🎭 {player_name} is a master of deception with {stats.successful_bluffs} successful bluffs!")
            
            if stats.bad_beats_suffered >= 2:
                facts.append(f"💔 {player_name} has suffered {stats.bad_beats_suffered} bad beats. Ouch!")
            
            if stats.biggest_pot_won > 500:
                facts.append(f"💰 {player_name} won a massive ${stats.biggest_pot_won} pot!")
            
            if stats.get_tilt_score() > 0.7:
                facts.append(f"😤 {player_name} is on major tilt with a {stats.get_tilt_score():.0%} tilt score!")
        
        # Game-wide facts
        if self.game_stats['biggest_pot'] > 1000:
            facts.append(f"🏆 The biggest pot tonight was ${self.game_stats['biggest_pot']}!")
        
        if self.game_stats['total_events'] > 20:
            facts.append(f"🎢 What a wild game! {self.game_stats['total_events']} dramatic moments so far!")
        
        return facts
    
    def get_session_summary(self) -> Dict[str, Any]:
        """Get a complete session summary."""
        return {
            'session_duration': (datetime.now() - self.game_stats['session_start']).seconds // 60,
            'total_events': self.game_stats['total_events'],
            'biggest_pot': self.game_stats['biggest_pot'],
            'player_summaries': {
                name: stats.get_summary() 
                for name, stats in self.player_stats.items()
            },
            'leaderboards': self.get_leaderboard(),
            'fun_facts': self.get_fun_facts()
        }
    
    def _load_from_database(self) -> None:
        """Load existing events from database and rebuild stats."""
        if not self.event_repository or not self.game_id:
            return
            
        try:
            events = self.event_repository.get_events_for_game(self.game_id)
            
            for event_data in events:
                player_name = event_data['player_name']
                
                # Ensure player stats exist
                if player_name not in self.player_stats:
                    self.player_stats[player_name] = PlayerPressureStats(player_name)
                
                # Recreate event and add to player stats
                event = PressureEvent(
                    timestamp=datetime.fromisoformat(event_data['timestamp']),
                    event_type=event_data['event_type'],
                    player_name=player_name,
                    details=event_data.get('details', {})
                )
                
                self.player_stats[player_name].add_event(event)
                self.game_stats['total_events'] += 1
                
                # Update game-wide stats
                if 'pot_size' in event.details:
                    self.game_stats['biggest_pot'] = max(
                        self.game_stats['biggest_pot'], 
                        event.details['pot_size']
                    )
                    
        except Exception as e:
            print(f"Failed to load pressure events from database: {e}")