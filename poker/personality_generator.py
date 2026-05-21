"""
AI-powered personality generator for poker players.
Uses LLM to generate unique personality configurations based on character names.
"""
import json
import logging
import random
from typing import Dict, Any, Optional
from pathlib import Path

from core.llm import LLMClient, CallType
from core.llm.settings import get_default_model, get_default_provider
from .repositories import PersonalityRepository

logger = logging.getLogger(__name__)


class PersonalityGenerator:
    """Generates unique poker player personalities using AI."""
    
    GENERATION_PROMPT = """
You are creating a personality profile for an AI poker player named "{name}".
{description}

Generate a unique personality configuration with THREE sections:

SECTION 1 — BEHAVIORAL TRAITS:
1. play_style: Brief poker playing style (e.g., "aggressive and unpredictable")
2. default_confidence: Baseline confidence (e.g., "overconfident", "cautious", "steady")
3. default_attitude: General demeanor (e.g., "friendly", "intimidating", "mysterious")
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
8. nickname: (OPTIONAL) A short display name (1-2 words max) for compact UI display. Only include this if the full name is long or would look bad truncated. For example: "The Hulk" -> "Hulk", "Dr. Seuss" -> "Dr. Seuss", "Ruth Bader Ginsburg" -> "RBG". Omit this field for names that already work well as-is (e.g., "Batman", "Socrates").

SECTION 2 — VISUAL IDENTITY (for avatar image generation):
9. visual_identity: An object with three fields:
   - identity: Their name PLUS a brief description of who they are / what they're known for.
     Always include the name. Image models often don't recognize names alone, so the description
     gives the model enough context to render the right person.
     Examples:
       "Batman, the dark knight vigilante of Gotham City"
       "Lizzo, a bold energetic plus-size pop star and flutist"
       "Abraham Lincoln, the tall bearded 16th US President"
       "Zeus, the mighty king of the Greek gods"
   - appearance: Physical features in 10-15 words.
     Include: build/body type, hair style and color, facial hair, distinctive facial features, skin tone, approximate age.
     Example: "lean athletic build, short dark hair, clean-shaven, sharp angular features, medium skin tone, middle-aged"
   - apparel: Clothing and accessories in 8-12 words. Should be IN CHARACTER — not everyone in a suit!
     Include: outfit style, key colors, distinctive accessories.
     Example: "black tactical suit with armored chest plate, utility belt, dark cape"

SECTION 3 — CASH MODE KNOBS (bankroll, lending, borrowing):
Pick values that fit the character's wealth, temperament, and relationship to money. The
five stake comfort tiers are "$2", "$10", "$50", "$200", "$1000". Match starting_bankroll
to the tier (rough peer values shown below).

10. bankroll_knobs: How they handle their own cash-game roll:
    - starting_bankroll: Total chips they sit with at world-start. Anchor to their tier:
        * $2 tier:    4,000–8,000  (poor / minimalist / ascetic)
        * $10 tier:   5,000–25,000 (everyday folk, hobbyist players)
        * $50 tier:   12,000–40,000 (comfortable middle, serious amateurs)
        * $200 tier:  30,000–100,000 (wealthy, big personalities, pros)
        * $1000 tier: 90,000–250,000 (royalty, gods, ultra-rich)
    - bankroll_rate: Chips/day "income" regen toward starting_bankroll. 100–3500.
        Higher = bounces back fast (productive, gigging, royalty). Lower = slow recovery
        (retired, ascetic, has no day-job).
    - buy_in_multiplier: How much they overbuy relative to the table's min buy-in.
        1.0 = exactly min buy-in (tight/scared money). 1.5 = +50% (comfortable).
        2.0–2.5 = "I want everyone covered" (loose/aggro/ego). Tie this to aggression
        and ego — not just wealth.
    - stake_comfort_zone: The label they prefer when affordable ("$2"/"$10"/"$50"/"$200"/"$1000").

11. staker_profile: When other AIs ask THEM for a stake-up loan:
    - willing: false ONLY for principled / ascetic / outright cruel characters
      (Buddha-types refuse on principle, mob bosses refuse as a power move). Default true.
    - max_loan_pct_of_bankroll: 0.03–0.20. Fraction of their roll they'll lend at once.
        Generous/wealthy = 0.10–0.20. Cautious = 0.03–0.07.
    - floor_anchor: 1.0–1.5. Their floor multiple on repayment (1.0 = par, 1.2 = +20%, etc.).
        Saintly/generous = 1.0–1.1. Sharks/loan-sharks = 1.3–1.5.
    - rate_anchor: 0.10–0.50. Interest they expect on top of the floor. Mirror character:
        gentle souls 0.10–0.20, ruthless types 0.35–0.50.
    - respect_floor: -1.0 to 0.0. Minimum relationship-respect they need before lending
        (more negative = lends to almost anyone; near 0 = only respected peers).
    - heat_ceiling: 0.4–1.0. Max active-conflict (heat) they tolerate while lending.

12. borrower_profile: When THEY are bust and someone offers them a stake:
    - willing: DEFAULT TRUE for almost everyone — most personalities accept stakes when
      busted. Set false ONLY when there is a clear in-character reason to refuse on
      principle (NOT just pride). Examples that DO warrant false: monks/ascetics
      (Buddha), characters with an explicit anti-money ideology (Tyler Durden),
      famously stoic figures (Lincoln), Jedi-style non-attachment (Yoda). Pride,
      wealth, or ego alone are NOT sufficient reasons — encode those via a high
      willingness_threshold instead.
    - willingness_threshold: 0.15–0.50. The relationship score they need from a HUMAN
      staker before accepting. Humble/easygoing = 0.20–0.30. Proud or ego-driven = 0.40–0.50.
      Omit this field if `willing` is false.

Consider {name}'s cultural/fictional associations. Make it authentic, visually distinctive, and interesting.

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
    "physical_tics": ["*action 1*", "*action 2*"],
    "visual_identity": {{
        "identity": "Name, brief description of who they are",
        "appearance": "physical features in 10-15 words",
        "apparel": "clothing and accessories in 8-12 words"
    }},
    "bankroll_knobs": {{
        "starting_bankroll": 20000,
        "bankroll_rate": 700,
        "buy_in_multiplier": 1.5,
        "stake_comfort_zone": "$50"
    }},
    "staker_profile": {{
        "willing": true,
        "max_loan_pct_of_bankroll": 0.08,
        "floor_anchor": 1.15,
        "rate_anchor": 0.25,
        "respect_floor": -0.5,
        "heat_ceiling": 0.7
    }},
    "borrower_profile": {{
        "willing": true,
        "willingness_threshold": 0.30
    }}
}}
"""
    
    def __init__(self, personality_repo: Optional[PersonalityRepository] = None, db_path: Optional[str] = None):
        """Initialize the personality generator.

        Args:
            personality_repo: Existing PersonalityRepository instance
            db_path: Path to database (used if personality_repo not provided)
        """
        if personality_repo:
            self.personality_repo = personality_repo
        else:
            from .repositories import SchemaManager
            db_path = db_path or self._get_default_db_path()
            SchemaManager(db_path).ensure_schema()
            self.personality_repo = PersonalityRepository(db_path)

        # Use stateless LLMClient for generation
        self._client = LLMClient(model=get_default_model(), provider=get_default_provider())

        # Cache for this session
        self._cache = {}
    
    def _get_default_db_path(self) -> str:
        """Get the default database path based on environment."""
        if Path('/app/data').exists():
            return '/app/data/poker_games.db'
        else:
            return Path(__file__).parent.parent / 'poker_games.db'
    
    def get_personality(self, name: str, description: Optional[str] = None,
                        force_generate: bool = False, owner_id: Optional[str] = None) -> Dict[str, Any]:
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
            owner_id: If provided, generated personality is owned by this user (private)

        Returns:
            Personality configuration dict
        """
        logger.info(f"[PERSONALITY] Getting personality for: {name}")

        # Check cache first
        if name in self._cache and not force_generate:
            logger.info(f"[PERSONALITY] Found {name} in cache")
            return self._cache[name]

        # Check database (source of truth) unless forcing generation
        if not force_generate:
            db_personality = self.personality_repo.load_personality(name)
            if db_personality:
                logger.info(f"[PERSONALITY] Found {name} in database")
                self._cache[name] = db_personality
                return db_personality

        # Generate new personality via AI
        logger.info(f"[PERSONALITY] Generating new personality for {name}")
        generated = self._generate_personality(name, description)

        # Save to database (private to owner if owner_id provided). The
        # repository computes a stable personality_id from the name when
        # one isn't supplied; capture the returned id so downstream
        # callers (relationships, bankrolls, opponent_models) can key on
        # it instead of the display name.
        visibility = 'private' if owner_id else 'public'
        personality_id = self.personality_repo.save_personality(
            name, generated, source='ai_generated',
            owner_id=owner_id, visibility=visibility,
        )
        if personality_id:
            generated['id'] = personality_id
            logger.info(
                f"[PERSONALITY] Assigned personality_id={personality_id!r} to {name}"
            )

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
            response = self._client.complete(
                messages=[
                    {"role": "system", "content": "You are a creative AI that generates unique poker player personalities."},
                    {"role": "user", "content": prompt}
                ],
                json_format=True,
                call_type=CallType.PERSONALITY_GENERATION,
                player_name=name,
                prompt_template='personality_generation',
            )

            result = json.loads(response.content)

            # Validate the response has required fields
            required_fields = ['play_style', 'default_confidence', 'default_attitude', 'personality_traits']
            if not all(field in result for field in required_fields):
                return self._create_default_personality(name)

            # Ensure visual_identity exists with non-empty required subfields
            vi = result.get('visual_identity', {})
            if not all(vi.get(k) for k in ['identity', 'appearance', 'apparel']):
                logger.warning(f"[PERSONALITY] Missing visual_identity fields for {name}, using name as identity")
                result['visual_identity'] = {
                    'identity': name,
                    'appearance': vi.get('appearance'),
                    'apparel': vi.get('apparel'),
                }

            # Backfill cash-mode knobs with conservative defaults if the model
            # omitted them. Cash-mode reads tolerate missing sub-dicts (per-field
            # fallback to BANKROLL_KNOB_DEFAULTS / STAKER_PROFILE_DEFAULTS), but
            # persisting explicit defaults keeps the DB row introspectable.
            result.setdefault('bankroll_knobs', {
                'starting_bankroll': 10000,
                'bankroll_rate': 500,
                'buy_in_multiplier': 1.0,
                'stake_comfort_zone': '$10',
            })
            result.setdefault('staker_profile', {
                'willing': True,
                'max_loan_pct_of_bankroll': 0.05,
                'floor_anchor': 1.20,
                'rate_anchor': 0.30,
                'respect_floor': -0.5,
                'heat_ceiling': 0.7,
            })
            result.setdefault('borrower_profile', {
                'willing': True,
                'willingness_threshold': 0.30,
            })

            return result

        except Exception as e:
            logger.info(f"[PERSONALITY] Error generating personality for {name}: {e}")
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
            ],
            "bankroll_knobs": {
                "starting_bankroll": 10000,
                "bankroll_rate": 500,
                "buy_in_multiplier": 1.0,
                "stake_comfort_zone": "$10",
            },
            "staker_profile": {
                "willing": True,
                "max_loan_pct_of_bankroll": 0.05,
                "floor_anchor": 1.20,
                "rate_anchor": 0.30,
                "respect_floor": -0.5,
                "heat_ceiling": 0.7,
            },
            "borrower_profile": {
                "willing": True,
                "willingness_threshold": 0.30,
            },
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
        self.personality_repo.save_personality(name, personality, source='updated')

    def get_avatar_images(self, name: str) -> list:
        """Get list of available avatar emotions for a personality.

        Note: This now checks the avatar_images database table for actual images.

        Args:
            name: Character name

        Returns:
            List of emotion names that have avatar images
        """
        return self.personality_repo.get_available_avatar_emotions(name)

    def has_avatar_image(self, name: str, emotion: str) -> bool:
        """Check if an avatar image exists for the personality and emotion.

        Args:
            name: Character name
            emotion: Emotion name

        Returns:
            True if avatar image exists in database
        """
        return self.personality_repo.has_avatar_image(name, emotion)

    # ==================== Reference Image Management ====================

    def get_reference_image_id(self, name: str) -> Optional[str]:
        """Get reference image ID for a personality.

        The reference image is used for img2img generation to create
        consistent avatar images based on a user-provided photo.

        Args:
            name: Character name

        Returns:
            Reference image ID string or None if not set
        """
        personality = self.get_personality(name)
        return personality.get('reference_image_id')

    def set_reference_image_id(self, name: str, reference_id: Optional[str]) -> None:
        """Set reference image ID for a personality.

        Args:
            name: Character name
            reference_id: Reference image ID for img2img generation (or None to clear)
        """
        # Update in cache
        if name in self._cache:
            if reference_id:
                self._cache[name]['reference_image_id'] = reference_id
            elif 'reference_image_id' in self._cache[name]:
                del self._cache[name]['reference_image_id']

        # Update in database (source of truth)
        personality = self.get_personality(name)
        if reference_id:
            personality['reference_image_id'] = reference_id
        elif 'reference_image_id' in personality:
            del personality['reference_image_id']
        self.personality_repo.save_personality(name, personality, source='updated')