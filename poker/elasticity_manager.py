"""
Elasticity Manager for dynamic personality traits in poker AI players.

This module handles the elasticity system that allows AI personalities to change
dynamically during gameplay while maintaining their core identity.
"""

import json
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ElasticTrait:
    """Represents a single elastic personality trait."""
    value: float
    anchor: float
    elasticity: float
    pressure: float = 0.0
    
    @property
    def min(self) -> float:
        """Minimum possible value for this trait."""
        return max(0.0, self.anchor - self.elasticity)
    
    @property
    def max(self) -> float:
        """Maximum possible value for this trait."""
        return min(1.0, self.anchor + self.elasticity)
    
    def apply_pressure(self, amount: float, pressure_threshold: float = 0.1) -> None:
        """Apply pressure to the trait and update value if threshold exceeded."""
        self.pressure += amount
        
        # Always apply some immediate effect for dramatic moments
        immediate_change = amount * self.elasticity * 0.3
        self.value = max(self.min, min(self.max, self.value + immediate_change))
        
        if abs(self.pressure) > pressure_threshold:
            # Calculate additional change from accumulated pressure
            change = self.pressure * self.elasticity * 0.5
            new_value = self.anchor + change
            
            # Clamp to min/max
            self.value = max(self.min, min(self.max, new_value))
            
            # Reduce pressure after application
            self.pressure *= 0.7
    
    def recover(self, recovery_rate: float = 0.1) -> None:
        """Gradually return trait to anchor value."""
        if self.value != self.anchor:
            diff = self.anchor - self.value
            self.value += diff * recovery_rate
        
        # Decay pressure over time
        self.pressure *= 0.9
    
    def to_dict(self) -> Dict[str, float]:
        """Convert to dictionary for serialization."""
        return {
            'value': self.value,
            'anchor': self.anchor,
            'elasticity': self.elasticity,
            'pressure': self.pressure
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, float]) -> 'ElasticTrait':
        """Create from dictionary."""
        return cls(
            value=data['value'],
            anchor=data['anchor'],
            elasticity=data['elasticity'],
            pressure=data.get('pressure', 0.0)
        )


@dataclass
class ElasticPersonality:
    """Manages elastic personality traits for an AI player."""
    name: str
    traits: Dict[str, ElasticTrait] = field(default_factory=dict)
    mood_elasticity: float = 0.4
    recovery_rate: float = 0.1
    current_mood: Optional[str] = None
    
    def apply_pressure_event(self, event_name: str) -> None:
        """Apply pressure from a specific game event."""
        pressure_events = self._load_pressure_events()
        
        if event_name in pressure_events:
            pressures = pressure_events[event_name]
            for trait_name, pressure_amount in pressures.items():
                if trait_name in self.traits:
                    self.traits[trait_name].apply_pressure(pressure_amount)
    
    def recover_traits(self, recovery_rate: Optional[float] = None) -> None:
        """Apply recovery to all traits."""
        rate = recovery_rate if recovery_rate is not None else self.recovery_rate
        for trait in self.traits.values():
            trait.recover(rate)
    
    def get_current_mood(self) -> str:
        """Get current mood based on trait values and pressure."""
        mood_vocab = self._load_mood_vocabulary()
        
        if self.name not in mood_vocab:
            return self.current_mood or "neutral"
        
        personality_moods = mood_vocab[self.name]
        
        # Determine mood based on average pressure
        avg_pressure = sum(t.pressure for t in self.traits.values()) / len(self.traits)
        
        if avg_pressure > 0.3:
            mood_type = "high_pressure"
        elif avg_pressure < -0.3:
            mood_type = "negative_pressure"
        elif avg_pressure > 0.1:
            mood_type = "positive_pressure"
        else:
            mood_type = "low_pressure"
        
        # Get appropriate mood from vocabulary
        confidence_moods = personality_moods.get("confidence_moods", {})
        if mood_type in confidence_moods:
            moods = confidence_moods[mood_type]
            if isinstance(moods, list) and moods:
                import random
                return random.choice(moods)
            elif isinstance(moods, str):
                return moods
        
        return confidence_moods.get("base", "neutral")
    
    def get_trait_value(self, trait_name: str) -> float:
        """Get current value of a trait."""
        if trait_name in self.traits:
            return self.traits[trait_name].value
        return 0.5  # Default neutral value
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'name': self.name,
            'traits': {name: trait.to_dict() for name, trait in self.traits.items()},
            'mood_elasticity': self.mood_elasticity,
            'recovery_rate': self.recovery_rate,
            'current_mood': self.current_mood
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ElasticPersonality':
        """Create from dictionary."""
        personality = cls(
            name=data['name'],
            mood_elasticity=data.get('mood_elasticity', 0.4),
            recovery_rate=data.get('recovery_rate', 0.1),
            current_mood=data.get('current_mood')
        )
        
        for trait_name, trait_data in data.get('traits', {}).items():
            personality.traits[trait_name] = ElasticTrait.from_dict(trait_data)
        
        return personality
    
    @classmethod
    def from_base_personality(cls, name: str, personality_config: Dict[str, Any],
                            elasticity_config: Optional[Dict[str, Any]] = None) -> 'ElasticPersonality':
        """Create elastic personality from base personality configuration."""
        # Check if elasticity config is embedded in personality config
        if not elasticity_config and 'elasticity_config' in personality_config:
            elasticity_config = personality_config['elasticity_config']
        
        # Default elasticity values (fallback for old personalities)
        default_elasticities = {
            'bluff_tendency': 0.3,
            'aggression': 0.5,
            'chattiness': 0.8,
            'emoji_usage': 0.2
        }
        
        # Override with personality-specific elasticity if provided
        if elasticity_config:
            trait_elasticity = elasticity_config.get('trait_elasticity', {})
            # Update defaults with personality-specific values
            for trait, value in trait_elasticity.items():
                default_elasticities[trait] = value
        
        personality = cls(
            name=name,
            mood_elasticity=elasticity_config.get('mood_elasticity', 0.4) if elasticity_config else 0.4,
            recovery_rate=elasticity_config.get('recovery_rate', 0.1) if elasticity_config else 0.1
        )
        
        # Create elastic traits from base personality traits
        base_traits = personality_config.get('personality_traits', {})
        for trait_name, base_value in base_traits.items():
            elasticity = default_elasticities.get(trait_name, 0.5)
            personality.traits[trait_name] = ElasticTrait(
                value=base_value,
                anchor=base_value,
                elasticity=elasticity
            )
        
        return personality
    
    def _load_pressure_events(self) -> Dict[str, Dict[str, float]]:
        """Load pressure event configurations."""
        # For now, return hardcoded events. Could be moved to JSON later.
        return {
            "big_win": {
                "aggression": 0.2,
                "chattiness": 0.3,
                "bluff_tendency": 0.1
            },
            "big_loss": {
                "aggression": -0.3,
                "chattiness": -0.2,
                "emoji_usage": -0.1
            },
            "successful_bluff": {
                "bluff_tendency": 0.3,
                "aggression": 0.2
            },
            "bluff_called": {
                "bluff_tendency": -0.4,
                "aggression": -0.1
            },
            "friendly_chat": {
                "chattiness": 0.2,
                "emoji_usage": 0.1
            },
            "rivalry_trigger": {
                "aggression": 0.4,
                "bluff_tendency": 0.2
            },
            "eliminated_opponent": {
                "aggression": 0.3,
                "chattiness": 0.2,
                "bluff_tendency": 0.15
            },
            "bad_beat": {
                "aggression": -0.2,
                "bluff_tendency": -0.3,
                "chattiness": -0.1
            },
            "fold_under_pressure": {
                "aggression": -0.15,
                "bluff_tendency": -0.1,
                "chattiness": -0.05
            },
            "aggressive_bet": {
                "aggression": 0.25,
                "bluff_tendency": 0.15,
                "chattiness": 0.1
            }
        }
    
    def _load_mood_vocabulary(self) -> Dict[str, Dict[str, Any]]:
        """Load mood vocabulary for personalities."""
        # For now, return hardcoded moods. Could be moved to JSON later.
        return {
            "Eeyore": {
                "confidence_moods": {
                    "base": "pessimistic",
                    "high_pressure": ["hopeless", "defeated", "miserable"],
                    "low_pressure": ["pessimistic", "melancholy", "resigned"],
                    "positive_pressure": ["doubtful", "uncertain"]
                },
                "attitude_moods": {
                    "base": "gloomy",
                    "variations": ["depressed", "gloomy", "morose", "dejected"]
                }
            },
            "Donald Trump": {
                "confidence_moods": {
                    "base": "supreme",
                    "high_pressure": ["supreme", "unstoppable", "dominant"],
                    "low_pressure": ["irritated", "frustrated", "angry"],
                    "negative_pressure": ["vengeful", "determined", "aggressive"]
                },
                "attitude_moods": {
                    "base": "domineering",
                    "variations": ["boastful", "commanding", "aggressive", "confrontational"]
                }
            },
            "Gordon Ramsay": {
                "confidence_moods": {
                    "base": "intense",
                    "high_pressure": ["furious", "explosive", "volcanic"],
                    "low_pressure": ["intense", "focused", "critical"],
                    "positive_pressure": ["passionate", "energized", "fierce"]
                },
                "attitude_moods": {
                    "base": "critical",
                    "variations": ["harsh", "demanding", "perfectionist", "unforgiving"]
                }
            },
            "Bob Ross": {
                "confidence_moods": {
                    "base": "serene",
                    "high_pressure": ["concerned", "worried", "thoughtful"],
                    "low_pressure": ["serene", "peaceful", "content"],
                    "positive_pressure": ["joyful", "delighted", "cheerful"]
                },
                "attitude_moods": {
                    "base": "peaceful",
                    "variations": ["gentle", "encouraging", "optimistic", "nurturing"]
                }
            },
            "Batman": {
                "confidence_moods": {
                    "base": "stoic",
                    "high_pressure": ["determined", "relentless", "unwavering"],
                    "low_pressure": ["stoic", "calculating", "focused"],
                    "negative_pressure": ["brooding", "dark", "intense"]
                },
                "attitude_moods": {
                    "base": "focused",
                    "variations": ["vigilant", "strategic", "disciplined", "serious"]
                }
            },
            "A Mime": {
                "confidence_moods": {
                    "base": "enigmatic",
                    "high_pressure": ["frantic", "exaggerated", "dramatic"],
                    "low_pressure": ["enigmatic", "mysterious", "playful"],
                    "positive_pressure": ["gleeful", "animated", "expressive"]
                },
                "attitude_moods": {
                    "base": "playful",
                    "variations": ["theatrical", "whimsical", "mischievous", "artistic"]
                }
            },
            "Ace Ventura": {
                "confidence_moods": {
                    "base": "overconfident",
                    "high_pressure": ["manic", "wild", "unhinged"],
                    "low_pressure": ["overconfident", "zany", "eccentric"],
                    "positive_pressure": ["euphoric", "triumphant", "unstoppable"]
                },
                "attitude_moods": {
                    "base": "manic",
                    "variations": ["hyperactive", "outrageous", "unpredictable", "energetic"]
                }
            }
        }


class ElasticityManager:
    """Manages elasticity for all AI players in a game."""
    
    def __init__(self):
        self.personalities: Dict[str, ElasticPersonality] = {}
    
    def add_player(self, name: str, personality_config: Dict[str, Any],
                   elasticity_config: Optional[Dict[str, Any]] = None) -> None:
        """Add a player with elastic personality."""
        self.personalities[name] = ElasticPersonality.from_base_personality(
            name, personality_config, elasticity_config
        )
    
    def apply_game_event(self, event_name: str, player_names: Optional[list] = None) -> None:
        """Apply a game event to specified players or all players."""
        if player_names is None:
            player_names = list(self.personalities.keys())
        
        for name in player_names:
            if name in self.personalities:
                self.personalities[name].apply_pressure_event(event_name)
    
    def recover_all(self) -> None:
        """Apply recovery to all players."""
        for personality in self.personalities.values():
            personality.recover_traits()
    
    def get_player_traits(self, player_name: str) -> Dict[str, float]:
        """Get current trait values for a player."""
        if player_name in self.personalities:
            personality = self.personalities[player_name]
            return {
                trait_name: trait.value
                for trait_name, trait in personality.traits.items()
            }
        return {}
    
    def get_player_mood(self, player_name: str) -> str:
        """Get current mood for a player."""
        if player_name in self.personalities:
            return self.personalities[player_name].get_current_mood()
        return "neutral"
    
    def apply_pressure(self, player_name: str, trait_name: str, pressure: float) -> None:
        """Apply pressure to a specific trait for a player."""
        if player_name in self.personalities:
            personality = self.personalities[player_name]
            if trait_name in personality.traits:
                personality.traits[trait_name].apply_pressure(pressure)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'personalities': {
                name: personality.to_dict()
                for name, personality in self.personalities.items()
            }
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ElasticityManager':
        """Create from dictionary."""
        manager = cls()
        for name, personality_data in data.get('personalities', {}).items():
            manager.personalities[name] = ElasticPersonality.from_dict(personality_data)
        return manager