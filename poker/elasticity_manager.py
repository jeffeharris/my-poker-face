"""
Elasticity Manager for dynamic personality traits in poker AI players.

NOTE: This module is DEPRECATED in Psychology System v2.1.
The new system uses PlayerPsychology with PersonalityAnchors and EmotionalAxes.

This module is kept for backward compatibility with existing saved games
and will be removed in a future version.

Legacy 5-trait poker-native model:
- tightness: Range selectivity (0=loose, 1=tight)
- aggression: Bet frequency (0=passive, 1=aggressive)
- confidence: Sizing/commitment (0=scared, 1=fearless)
- composure: Decision quality (0=tilted, 1=focused)
- table_talk: Chat frequency (0=silent, 1=chatty)
"""

import json
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path

# Legacy trait names (for backward compat)
NEW_TRAIT_NAMES = ['tightness', 'aggression', 'confidence', 'composure', 'table_talk']


def detect_trait_format(traits: Dict[str, Any]) -> str:
    """Detect whether traits are in old or new format."""
    if not traits:
        return 'unknown'
    trait_names = set(traits.keys())
    new_indicators = {'tightness', 'composure', 'table_talk'}
    if trait_names & new_indicators:
        return 'new'
    old_indicators = {'bluff_tendency', 'chattiness', 'emoji_usage'}
    if trait_names & old_indicators:
        return 'old'
    if 'aggression' in trait_names:
        return 'old'
    return 'unknown'


def get_default_elasticity_config() -> Dict[str, Any]:
    """Get default elasticity configuration."""
    return {
        'trait_elasticity': {
            'tightness': 0.3,
            'aggression': 0.5,
            'confidence': 0.4,
            'composure': 0.4,
            'table_talk': 0.6,
        },
        'mood_elasticity': 0.4,
        'recovery_rate': 0.1,
    }


def convert_old_to_new_traits(old_traits: Dict[str, float]) -> Dict[str, float]:
    """Convert 4-trait model to 5-trait poker-native model.

    Derives composure from traits to create personality differentiation:
    - Lower chattiness → higher composure (stoic types)
    - Lower aggression → higher composure (calm types)
    - Lower bluff_tendency → higher composure (straightforward types)
    """
    bluff = old_traits.get('bluff_tendency', 0.5)
    agg = old_traits.get('aggression', 0.5)
    chat = old_traits.get('chattiness', 0.5)
    emoji = old_traits.get('emoji_usage', 0.3)

    looseness = bluff * 0.5 + agg * 0.3 + 0.2
    tightness = 1.0 - looseness
    confidence = 0.5 + agg * 0.2 + bluff * 0.1
    table_talk = chat * 0.8 + emoji * 0.2

    # Derive composure: calmer personalities (low chat, low aggression) have higher composure
    # Range: ~0.45 (high chat + high agg) to ~0.85 (low chat + low agg)
    composure = 0.85 - chat * 0.25 - agg * 0.15

    return {
        'tightness': round(max(0, min(1, tightness)), 2),
        'aggression': round(max(0, min(1, agg)), 2),
        'confidence': round(max(0, min(1, confidence)), 2),
        'composure': round(max(0, min(1, composure)), 2),
        'table_talk': round(max(0, min(1, table_talk)), 2),
    }


def convert_old_elasticity_config(old_config: Dict[str, Any]) -> Dict[str, Any]:
    """Convert old elasticity config to new format."""
    if not old_config:
        return get_default_elasticity_config()
    old_elasticity = old_config.get('trait_elasticity', {})
    bluff_e = old_elasticity.get('bluff_tendency', 0.3)
    agg_e = old_elasticity.get('aggression', 0.5)
    chat_e = old_elasticity.get('chattiness', 0.8)
    return {
        'trait_elasticity': {
            'tightness': round(bluff_e, 2),
            'aggression': round(agg_e, 2),
            'confidence': round((agg_e + bluff_e) / 2, 2),
            'composure': 0.4,
            'table_talk': round(chat_e, 2),
        },
        'mood_elasticity': old_config.get('mood_elasticity', 0.4),
        'recovery_rate': old_config.get('recovery_rate', 0.1),
    }


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
        
        # Determine mood based on average pressure with more sensitive thresholds
        avg_pressure = sum(t.pressure for t in self.traits.values()) / len(self.traits) if self.traits else 0
        
        # Adjusted thresholds to be more sensitive to actual pressure values
        if avg_pressure > 0.05:
            mood_type = "high_pressure"
        elif avg_pressure < -0.05:
            mood_type = "negative_pressure"
        elif avg_pressure > 0.02:
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

    def apply_learned_adjustment(self, opponent_tendencies: Dict[str, float]) -> None:
        """Adjust traits based on learned opponent patterns.

        This allows AI players to strategically adapt their play style
        based on what they've learned about their opponents.

        Args:
            opponent_tendencies: Dict with keys like 'aggression_factor', 'bluff_frequency', 'vpip', etc.
        """
        # If opponent is very aggressive, become more cautious (tighten up)
        aggression = opponent_tendencies.get('aggression_factor', 1.0)
        if aggression > 2.0:
            if 'aggression' in self.traits:
                self.traits['aggression'].apply_pressure(-0.1)
            if 'tightness' in self.traits:
                self.traits['tightness'].apply_pressure(0.1)

        # If opponent bluffs a lot, tighten up and increase confidence to call them down
        bluff_freq = opponent_tendencies.get('bluff_frequency', 0.3)
        if bluff_freq > 0.5:
            if 'tightness' in self.traits:
                self.traits['tightness'].apply_pressure(0.1)
            if 'confidence' in self.traits:
                self.traits['confidence'].apply_pressure(0.1)

        # If opponent is very tight (low VPIP), can loosen up against them
        vpip = opponent_tendencies.get('vpip', 0.5)
        if vpip < 0.2:
            if 'tightness' in self.traits:
                self.traits['tightness'].apply_pressure(-0.1)
            if 'aggression' in self.traits:
                self.traits['aggression'].apply_pressure(0.1)

        # If opponent folds a lot to pressure, be more aggressive
        fold_to_cbet = opponent_tendencies.get('fold_to_cbet', 0.5)
        if fold_to_cbet > 0.7 and 'aggression' in self.traits:
            self.traits['aggression'].apply_pressure(0.15)

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
        """Create elastic personality from base personality configuration.

        Auto-detects old 4-trait format and converts to new 5-trait model.
        """
        # Check if elasticity config is embedded in personality config
        if not elasticity_config and 'elasticity_config' in personality_config:
            elasticity_config = personality_config['elasticity_config']

        # Get base traits and detect format
        base_traits = personality_config.get('personality_traits', {})
        trait_format = detect_trait_format(base_traits)

        # Convert if old format detected
        if trait_format == 'old':
            base_traits = convert_old_to_new_traits(base_traits)
            elasticity_config = convert_old_elasticity_config(elasticity_config)

        # Default elasticity values for new 5-trait model
        default_elasticities = {
            'tightness': 0.3,      # Moderate - playing style shifts under pressure
            'aggression': 0.5,     # High - aggression shifts significantly
            'confidence': 0.4,     # Moderate - confidence varies with results
            'composure': 0.4,      # Moderate - composure affected by bad beats
            'table_talk': 0.6,     # High - chattiness varies with mood
        }

        # Override with personality-specific elasticity if provided
        if elasticity_config:
            trait_elasticity = elasticity_config.get('trait_elasticity', {})
            # Update defaults with personality-specific values
            for trait, value in trait_elasticity.items():
                if trait in default_elasticities:
                    default_elasticities[trait] = value

        personality = cls(
            name=name,
            mood_elasticity=elasticity_config.get('mood_elasticity', 0.4) if elasticity_config else 0.4,
            recovery_rate=elasticity_config.get('recovery_rate', 0.1) if elasticity_config else 0.1
        )

        # Create elastic traits from converted/new traits
        for trait_name, base_value in base_traits.items():
            elasticity = default_elasticities.get(trait_name, 0.5)
            personality.traits[trait_name] = ElasticTrait(
                value=base_value,
                anchor=base_value,
                elasticity=elasticity
            )

        # Ensure all 5 traits exist (fill in missing with defaults)
        for trait_name in NEW_TRAIT_NAMES:
            if trait_name not in personality.traits:
                default_value = 0.7 if trait_name == 'composure' else 0.5
                elasticity = default_elasticities.get(trait_name, 0.5)
                personality.traits[trait_name] = ElasticTrait(
                    value=default_value,
                    anchor=default_value,
                    elasticity=elasticity
                )

        return personality
    
    def _load_pressure_events(self) -> Dict[str, Dict[str, float]]:
        """Load pressure event configurations for 5-trait poker-native model.

        Traits affected:
        - tightness: Range selectivity (increases under pressure/losses)
        - aggression: Bet frequency
        - confidence: Sizing/commitment
        - composure: Decision quality (drops with bad beats, rises with wins)
        - table_talk: Chat frequency
        """
        return {
            # === Win Events ===
            "big_win": {
                "confidence": 0.20,
                "composure": 0.15,
                "aggression": 0.10,
                "tightness": -0.05,    # Play slightly looser after wins
                "table_talk": 0.15,
            },
            "win": {
                "confidence": 0.08,
                "composure": 0.05,
                "table_talk": 0.05,
            },
            "successful_bluff": {
                "confidence": 0.20,
                "composure": 0.10,
                "aggression": 0.15,
                "tightness": -0.10,    # Looser after successful bluff
                "table_talk": 0.10,
            },
            "suckout": {
                # Lucky win - came from behind
                "confidence": 0.15,
                "composure": 0.05,
                "aggression": 0.10,
                "tightness": -0.15,    # Play looser when running good
                "table_talk": 0.20,
            },

            # === Loss Events ===
            "big_loss": {
                "confidence": -0.15,
                "composure": -0.25,
                "tightness": 0.10,     # Tighten up after losses
                "table_talk": -0.10,
            },
            "bluff_called": {
                "confidence": -0.20,
                "composure": -0.15,
                "aggression": -0.15,
                "tightness": 0.15,     # Play tighter after failed bluff
                "table_talk": -0.05,
            },
            "bad_beat": {
                # Lost with a strong hand
                "composure": -0.30,
                "confidence": -0.10,
                "tightness": 0.10,
                "table_talk": -0.15,
            },
            "got_sucked_out": {
                # Was ahead but lost - very tilting
                "composure": -0.35,
                "confidence": -0.15,
                "table_talk": -0.15,
            },
            "cooler": {
                # Both had strong hands - unavoidable, less tilting
                "composure": 0.0,      # No composure hit - unavoidable
                "confidence": -0.05,
            },

            # === Social Events ===
            "friendly_chat": {
                "table_talk": 0.10,
                "composure": 0.05,
            },
            "rivalry_trigger": {
                "aggression": 0.15,
                "composure": -0.10,
                "table_talk": 0.10,    # More trash talk
            },

            # === Elimination Events ===
            "eliminated_opponent": {
                "confidence": 0.15,
                "composure": 0.10,
                "aggression": 0.10,
                "table_talk": 0.15,
            },

            # === Pressure/Fold Events ===
            "fold_under_pressure": {
                "confidence": -0.10,
                "composure": -0.05,
                "aggression": -0.10,
            },
            "aggressive_bet": {
                "aggression": 0.15,
                "confidence": 0.10,
            },

            # === Heads-up Events ===
            "headsup_win": {
                "confidence": 0.10,
                "composure": 0.05,
                "aggression": 0.08,
                "table_talk": 0.08,
            },
            "headsup_loss": {
                "confidence": -0.08,
                "composure": -0.10,
                "table_talk": -0.08,
            },

            # === Streak Events ===
            "winning_streak": {
                "confidence": 0.15,
                "composure": 0.10,
                "aggression": 0.10,
                "tightness": -0.10,    # Looser on winning streaks
                "table_talk": 0.15,
            },
            "losing_streak": {
                "composure": -0.20,
                "confidence": -0.15,
                "tightness": 0.15,     # Tighter on losing streaks
                "table_talk": -0.15,
            },

            # === Stack Events ===
            "double_up": {
                "confidence": 0.25,
                "composure": 0.15,
                "aggression": 0.10,
                "tightness": -0.10,
                "table_talk": 0.20,
            },
            "crippled": {
                "composure": -0.20,
                "confidence": -0.20,
                "table_talk": -0.15,
            },
            "short_stack": {
                "confidence": -0.15,
                "composure": -0.10,
                "tightness": -0.20,    # Forced to play looser (push/fold)
            },

            # === Nemesis Events ===
            "nemesis_win": {
                "confidence": 0.15,
                "composure": 0.10,
                "aggression": 0.10,
                "table_talk": 0.15,
            },
            "nemesis_loss": {
                "composure": -0.15,
                "confidence": -0.10,
                "table_talk": -0.10,
            },
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


