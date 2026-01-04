"""
AI-powered personality generator for poker players.
Uses OpenAI to generate unique personality configurations based on character names.
"""
import json
import random
from typing import Dict, Any, Optional
from pathlib import Path

from core.assistants import OpenAILLMAssistant
from .persistence import GamePersistence


class PersonalityGenerator:
    """Generates unique poker player personalities using AI."""
    
    GENERATION_PROMPT = """
You are creating a personality profile for an AI poker player named "{name}".
{description}

Generate a unique personality configuration that includes:
1. play_style: A brief description of their poker playing style (e.g., "aggressive and unpredictable", "tight and mathematical")
2. default_confidence: Their baseline confidence level (e.g., "overconfident", "cautious", "steady")
3. default_attitude: Their general demeanor (e.g., "friendly", "intimidating", "mysterious")
4. personality_traits: Numeric values between 0.0 and 1.0 for:
   - bluff_tendency: How often they bluff (0=never, 1=always)
   - aggression: How aggressive their betting is (0=passive, 1=very aggressive)
   - chattiness: How much they talk/emote (0=silent, 1=very talkative)
   - emoji_usage: How often they use emojis (0=never, 1=frequently)
5. elasticity_config: How flexible each trait is:
   - trait_elasticity: How much each trait can vary during play (0.0-1.0)
     * For extreme personality traits (near 0 or 1), use lower elasticity (0.1-0.3)
     * For moderate traits (around 0.5), use higher elasticity (0.3-0.6)
     * Consider character consistency - rigid personalities get lower values
   - mood_elasticity: How reactive their mood is (typically 0.2-0.6)
   - recovery_rate: How fast they return to baseline (typically 0.05-0.2)
6. verbal_tics: List of 3-5 characteristic phrases they might say
7. physical_tics: List of 2-4 physical actions/gestures they might do (in *asterisks*)

Consider the character's name and any cultural/fictional associations. Make the personality feel authentic and interesting.

Respond with ONLY a JSON object in this exact format:
{{
    "play_style": "description here",
    "default_confidence": "level here",
    "default_attitude": "attitude here",
    "personality_traits": {{
        "bluff_tendency": 0.5,
        "aggression": 0.5,
        "chattiness": 0.5,
        "emoji_usage": 0.3
    }},
    "elasticity_config": {{
        "trait_elasticity": {{
            "bluff_tendency": 0.4,
            "aggression": 0.3,
            "chattiness": 0.5,
            "emoji_usage": 0.4
        }},
        "mood_elasticity": 0.4,
        "recovery_rate": 0.1
    }},
    "verbal_tics": ["phrase 1", "phrase 2", "phrase 3"],
    "physical_tics": ["*action 1*", "*action 2*"]
}}
"""
    
    def __init__(self, persistence: Optional[GamePersistence] = None, db_path: Optional[str] = None):
        """Initialize the personality generator.
        
        Args:
            persistence: Existing GamePersistence instance
            db_path: Path to database (used if persistence not provided)
        """
        if persistence:
            self.persistence = persistence
        else:
            db_path = db_path or self._get_default_db_path()
            self.persistence = GamePersistence(db_path)
        
        self.assistant = OpenAILLMAssistant(
            ai_temp=0.8,
            system_message="You are a creative AI that generates unique poker player personalities."
        )
        
        # Cache for this session
        self._cache = {}
    
    def _get_default_db_path(self) -> str:
        """Get the default database path based on environment."""
        if Path('/app/data').exists():
            return '/app/data/poker_games.db'
        else:
            return Path(__file__).parent.parent / 'poker_games.db'
    
    def get_personality(self, name: str, description: Optional[str] = None, force_generate: bool = False) -> Dict[str, Any]:
        """Get a personality for a character, generating if needed.

        Personality loading hierarchy:
        1. Session cache (fastest)
        2. Database (source of truth)
        3. AI generation (if not in database)

        Note: personalities.json is only used as a seed file via seed_personalities_from_json().
        It is NOT checked at runtime to ensure database is the single source of truth.

        Args:
            name: Character name
            description: Optional description for more context
            force_generate: Force generation even if exists

        Returns:
            Personality configuration dict
        """
        print(f"[PersonalityGenerator] Getting personality for: {name}")

        # Check cache first
        if name in self._cache and not force_generate:
            print(f"[PersonalityGenerator] Found {name} in cache")
            return self._cache[name]

        # Check database (source of truth) unless forcing generation
        if not force_generate:
            db_personality = self.persistence.load_personality(name)
            if db_personality:
                print(f"[PersonalityGenerator] Found {name} in database")
                self._cache[name] = db_personality
                return db_personality

        # Generate new personality via AI
        print(f"[PersonalityGenerator] Generating new personality for {name}")
        generated = self._generate_personality(name, description)

        # Save to database
        self.persistence.save_personality(name, generated, source='ai_generated')

        # Cache it
        self._cache[name] = generated

        return generated
    
    def _generate_personality(self, name: str, description: Optional[str] = None) -> Dict[str, Any]:
        """Generate a new personality using AI."""
        # Build the description part
        desc_text = ""
        if description:
            desc_text = f"Additional context: {description}"
        else:
            # Add some context based on common name patterns
            if name.lower().startswith("a "):
                # It's an animal or object
                desc_text = f"This character is literally {name}. Consider how {name} would behave at a poker table."
            elif any(title in name.lower() for title in ["king", "queen", "lord", "lady", "dr.", "captain"]):
                desc_text = "This character has a title suggesting authority or expertise."
        
        prompt = self.GENERATION_PROMPT.format(name=name, description=desc_text)
        
        try:
            response = self.assistant.get_json_response(
                messages=[{"role": "user", "content": prompt}]
            )
            
            result = json.loads(response.choices[0].message.content)
            
            # Validate the response has required fields
            required_fields = ['play_style', 'default_confidence', 'default_attitude', 'personality_traits']
            if all(field in result for field in required_fields):
                return result
            else:
                # Fall back to default if generation fails
                return self._create_default_personality(name)
                
        except Exception as e:
            print(f"Error generating personality for {name}: {e}")
            return self._create_default_personality(name)
    
    def _create_default_personality(self, name: str) -> Dict[str, Any]:
        """Create a default personality with some randomization."""
        # Add some variety to defaults
        styles = ["balanced", "careful", "unpredictable", "analytical", "instinctive"]
        confidences = ["steady", "variable", "growing", "shaky", "overconfident"]
        attitudes = ["friendly", "mysterious", "competitive", "relaxed", "focused"]
        
        return {
            "play_style": random.choice(styles),
            "default_confidence": random.choice(confidences),
            "default_attitude": random.choice(attitudes),
            "personality_traits": {
                "bluff_tendency": round(random.uniform(0.2, 0.8), 2),
                "aggression": round(random.uniform(0.3, 0.7), 2),
                "chattiness": round(random.uniform(0.3, 0.8), 2),
                "emoji_usage": round(random.uniform(0.1, 0.6), 2)
            },
            "verbal_tics": [
                f"Interesting move",
                f"I see what you're doing",
                f"Let's make this interesting"
            ],
            "physical_tics": [
                "*taps table thoughtfully*",
                "*adjusts position*"
            ]
        }
    
    def bulk_generate(self, names: list[str], save: bool = True) -> Dict[str, Dict[str, Any]]:
        """Generate personalities for multiple characters at once.

        Args:
            names: List of character names
            save: Whether to save to database

        Returns:
            Dict mapping names to personality configs
        """
        results = {}

        for name in names:
            personality = self.get_personality(name)
            results[name] = personality

        return results

    # ==================== Avatar Management ====================
    # Note: Avatar images are now stored in the avatar_images database table.
    # These methods are kept for backwards compatibility but avatar_images list
    # in personality config is no longer the source of truth.

    def get_avatar_description(self, name: str) -> Optional[str]:
        """Get avatar description for a personality.

        Args:
            name: Character name

        Returns:
            Avatar description string or None if not set
        """
        personality = self.get_personality(name)
        return personality.get('avatar_description')

    def set_avatar_description(self, name: str, description: str) -> None:
        """Set avatar description for a personality.

        Args:
            name: Character name
            description: Avatar description for image generation
        """
        # Update in cache
        if name in self._cache:
            self._cache[name]['avatar_description'] = description

        # Update in database (source of truth)
        personality = self.get_personality(name)
        personality['avatar_description'] = description
        self.persistence.save_personality(name, personality, source='updated')

    def get_avatar_images(self, name: str) -> list:
        """Get list of available avatar emotions for a personality.

        Note: This now checks the avatar_images database table for actual images.

        Args:
            name: Character name

        Returns:
            List of emotion names that have avatar images
        """
        return self.persistence.get_available_avatar_emotions(name)

    def has_avatar_image(self, name: str, emotion: str) -> bool:
        """Check if an avatar image exists for the personality and emotion.

        Args:
            name: Character name
            emotion: Emotion name

        Returns:
            True if avatar image exists in database
        """
        return self.persistence.has_avatar_image(name, emotion)