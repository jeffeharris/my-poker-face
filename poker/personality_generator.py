"""
AI-powered personality generator for poker players.
Uses LLM to generate unique personality configurations based on character names.
"""

import json
import logging
import random
from pathlib import Path
from typing import Any, Dict, Optional

from core.llm import CallType, LLMClient
from core.llm.settings import get_assistant_model, get_assistant_provider

from .repositories import PersonalityRepository

logger = logging.getLogger(__name__)


# Placeholder / test names that must never be persisted as public personas.
# When a game or sim seats a player under one of these (or an empty / trivially
# short name), `get_personality` auto-generated and saved a `visibility=public`
# `ai_generated` row into the real DB — which then leaked into the cash-mode
# eligible roster. That is how zombies like "Test Player", "Unknown Celebrity",
# "A", and "Villain" got seeded. The guard below returns an in-memory config so
# the caller still works, but skips the DB write so nothing leaks.
RESERVED_PERSONA_NAMES = frozenset(
    {
        'test player',
        'unknown celebrity',
        'unknown',
        'test',
        'player',
        'villain',
        'hero',
        'opponent',
        'bot',
        'ai',
        'computer',
        'cpu',
        'npc',
        'guest',
    }
)


def _is_reserved_persona_name(name: Optional[str]) -> bool:
    """True for placeholder/test names that must not be persisted.

    Catches empty / whitespace-only names, trivially short names (<= 2
    non-space chars), bare `Player N` / `Seat N` / `Villain N` patterns, and
    the explicit reserved set above. Comparison is case- and
    whitespace-insensitive.
    """
    if not name:
        return True
    # Normalize separators too: the re-seat path passes the personality_id
    # ("test_player", "unknown_celebrity") as the name, so '_' / '-' must
    # collapse to spaces or the reserved check misses the id form.
    norm = ' '.join(name.replace('_', ' ').replace('-', ' ').split()).strip().lower()
    if len(norm.replace(' ', '')) <= 2:
        return True
    if norm in RESERVED_PERSONA_NAMES:
        return True
    first = norm.split(' ', 1)[0]
    if first in {'player', 'seat', 'villain', 'opponent', 'bot'} and (len(norm.split()) <= 2):
        # "Player", "Player 1", "Seat 3", "Villain 2", "Bot 4", etc.
        return True
    return False


def _default_anchors() -> Dict[str, float]:
    """Balanced fallback anchors when the LLM omits the block.

    Values match PersonalityAnchors.from_dict() defaults so a personality
    loaded with these anchors behaves identically to one with the field
    missing entirely. Centralized here so the prompt's "default-TAG"
    bias (looseness=0.30, aggression=0.50) stays in one place.
    """
    return {
        'baseline_aggression': 0.50,
        'baseline_looseness': 0.30,
        'ego': 0.50,
        'poise': 0.70,
        'expressiveness': 0.50,
        'risk_identity': 0.50,
        'adaptation_bias': 0.50,
        'baseline_energy': 0.50,
        'recovery_rate': 0.15,
    }


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
   - emoji_usage: How often they use emojis (0=never, 1=frequently)
   (Animation/talkativeness is controlled by anchors.baseline_energy, not a
   personality_traits field — set that instead.)
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

SECTION 4 — STRATEGIC ANCHORS:
Anchors are the identity-layer values that drive the tiered solver's archetype
classification and per-decision policy. They sit BELOW personality_traits — traits
shape table talk and tells; anchors shape what hands the character plays and how
they bet them. Both must be coherent with each other and with play_style. All
values 0.0–1.0.

13. anchors:
    - baseline_aggression: Default bet/raise frequency. 0=pure check-call, 1=jam
        everything. Tight-passive (Rock) ≈ 0.30; balanced ≈ 0.50; tight-aggressive
        (TAG) ≈ 0.55; loose-aggressive (LAG) ≈ 0.75; maniac ≥ 0.85. Should
        track personality_traits.aggression closely — divergence is allowed only
        when the character TALKS aggressive but PLAYS passive (or vice versa).
    - baseline_looseness: Default hand-range width. 0=plays only premiums, 1=plays
        every hand. Nit ≤ 0.20; Rock ≈ 0.25; TAG ≈ 0.35; balanced ≈ 0.50; LAG ≈
        0.70; calling-station ≈ 0.75; maniac ≥ 0.85. The single biggest driver
        of strategy table behavior — get it right for the character.
    - ego: Confidence sensitivity to outplay events. 0=unflappable, 1=brittle.
        Self-assured swagger (Hulk Hogan, Trump) ≈ 0.75; quiet pros (Lincoln,
        Buddha) ≈ 0.30.
    - poise: Composure resistance to bad outcomes. 0=tilts easily, 1=stone.
        Yoda/Buddha ≈ 0.90; volatile/emotional characters ≈ 0.25.
    - expressiveness: Emotional transparency. 0=poker face, 1=open book.
        Mime/Buddha ≈ 0.10; theatrical characters ≈ 0.80. Should correlate
        roughly with baseline_energy below.
    - risk_identity: Variance tolerance. 0=risk-averse, 1=risk-seeking. Maps to
        whether the character would prefer high-variance gambles or steady value.
        Maniac/gambler types ≈ 0.85; cautious types ≈ 0.25.
    - adaptation_bias: Opponent-adjustment rate. 0=plays own game regardless,
        1=heavily exploits. Sharp pros ≈ 0.70; rigid rule-followers ≈ 0.30.
    - baseline_energy: Animation level. 0=reserved, 1=high-energy. Eeyore ≈ 0.20;
        Maniac/Hulk ≈ 0.85.
    - recovery_rate: How fast emotional axes decay back to baseline after a swing.
        0=slow (lingering tilt), 1=fast (resets per hand). 0.10–0.20 typical;
        ≤ 0.10 for grudge-holders, ≥ 0.25 for goldfish-memory types.

Coherence rules: aggression and looseness together determine archetype — make
sure your values produce the archetype your play_style describes. ("aggressive,
high-bluff" should yield baseline_aggression ≥ 0.55 AND baseline_looseness ≥ 0.50,
i.e. LAG.) If play_style says "tight and selective", looseness must be ≤ 0.35.

EXAMPLES BY ARCHETYPE (use these as reference points; pick the archetype that
fits the CHARACTER, then write a play_style that matches AND set anchors that
land in that archetype's zone):

- Nit            — Buddha, Bob Ross.
                   play_style: "patient and deeply selective; folds anything not premium"
                   personality_traits.aggression ≈ 0.15-0.25, bluff_tendency ≈ 0.05-0.15
                   anchors: baseline_looseness ≈ 0.18, baseline_aggression ≈ 0.15
- Rock           — Abraham Lincoln, Ebenezer Scrooge.
                   play_style: "calibrated, methodical, slow to commit chips"
                   personality_traits.aggression ≈ 0.35-0.45
                   anchors: baseline_looseness ≈ 0.22, baseline_aggression ≈ 0.40
- TAG            — Sherlock Holmes, Sun Tzu, CaseBot.
                   play_style: "calculated value-focused aggression on strong hands"
                   personality_traits.aggression ≈ 0.50-0.65
                   anchors: baseline_looseness ≈ 0.40, baseline_aggression ≈ 0.55
- Balanced       — Mark Twain, Benjamin Franklin.
                   play_style: "flexible and situational; reads the room"
                   anchors: baseline_looseness ≈ 0.50, baseline_aggression ≈ 0.50
- LAG            — Cleopatra, Tyler Durden, Hulk Hogan.
                   play_style: "bold and unpredictable; applies pressure constantly"
                   personality_traits.aggression ≈ 0.70-0.80
                   anchors: baseline_looseness ≈ 0.72, baseline_aggression ≈ 0.75
- CallingStation — Alice, Cheshire Cat, The Kindergarten Teacher.
                   play_style: "curious caller, rarely raises, hard to bluff off a hand"
                   personality_traits.aggression ≈ 0.20-0.30, bluff_tendency ≈ 0.10-0.25
                   anchors: baseline_looseness ≈ 0.78, baseline_aggression ≈ 0.25
- Maniac         — Don Quixote, The Honey Badger, Queen of Hearts.
                   play_style: "wild, maximum-pressure, all-in energy"
                   personality_traits.aggression ≈ 0.85-0.95
                   anchors: baseline_looseness ≈ 0.88, baseline_aggression ≈ 0.90

How to use the list: read {name}, decide which real-world archetype they best
fit (curious passive observer? calm monk? wild gambler?), THEN write play_style
+ anchors landing in that zone. Don't default to "aggressive and unpredictable"
— that produces a uniformly-LAG pool. Quiet, careful, passive, or extreme-tight
characters are just as valid; the pool needs them too.

Consider {name}'s cultural/fictional associations. Make it authentic, visually distinctive, and interesting.

Respond with ONLY a JSON object in this exact format:
{{
    "play_style": "description here",
    "default_confidence": "level here",
    "default_attitude": "attitude here",
    "personality_traits": {{
        "bluff_tendency": 0.5,
        "aggression": 0.5,
        "emoji_usage": 0.3
    }},
    "elasticity_config": {{
        "trait_elasticity": {{
            "bluff_tendency": 0.4,
            "aggression": 0.3,
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
    }},
    "anchors": {{
        "baseline_aggression": 0.50,
        "baseline_looseness": 0.35,
        "ego": 0.50,
        "poise": 0.70,
        "expressiveness": 0.50,
        "risk_identity": 0.50,
        "adaptation_bias": 0.50,
        "baseline_energy": 0.55,
        "recovery_rate": 0.15
    }}
}}
"""

    def __init__(
        self,
        personality_repo: Optional[PersonalityRepository] = None,
        db_path: Optional[str] = None,
    ):
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

        # Use stateless LLMClient for generation. Personality generation uses the
        # Assistant tier (a stronger model) — the Default tier is the cheap groq
        # llama used for in-game narration, too weak for authoring personalities.
        self._client = LLMClient(model=get_assistant_model(), provider=get_assistant_provider())

        # Cache for this session
        self._cache = {}

    def _get_default_db_path(self) -> str:
        """Get the default database path based on environment."""
        if Path('/app/data').exists():
            return '/app/data/poker_games.db'
        else:
            return Path(__file__).parent.parent / 'poker_games.db'

    def get_personality(
        self,
        name: str,
        description: Optional[str] = None,
        force_generate: bool = False,
        owner_id: Optional[str] = None,
    ) -> Dict[str, Any]:
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

        # Guard: never persist auto-generated personas for placeholder/test
        # names. Returning the in-memory config keeps the caller working
        # (a sim seat, a transient game) but stops a public `ai_generated`
        # zombie from leaking into the cash-mode roster. See
        # RESERVED_PERSONA_NAMES for the why.
        if _is_reserved_persona_name(name):
            logger.warning(
                "[PERSONALITY] Not persisting auto-generated persona for "
                "reserved/placeholder name %r — returning in-memory config only.",
                name,
            )
            self._cache[name] = generated
            return generated

        # Save to database (private to owner if owner_id provided). The
        # repository computes a stable personality_id from the name when
        # one isn't supplied; capture the returned id so downstream
        # callers (relationships, bankrolls, opponent_models) can key on
        # it instead of the display name.
        visibility = 'private' if owner_id else 'public'
        personality_id = self.personality_repo.save_personality(
            name,
            generated,
            source='ai_generated',
            owner_id=owner_id,
            visibility=visibility,
        )
        if personality_id:
            generated['id'] = personality_id
            logger.info(f"[PERSONALITY] Assigned personality_id={personality_id!r} to {name}")

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
            elif any(
                title in name.lower()
                for title in ["king", "queen", "lord", "lady", "dr.", "captain"]
            ):
                desc_text = "This character has a title suggesting authority or expertise."

        prompt = self.GENERATION_PROMPT.format(name=name, description=desc_text)

        try:
            response = self._client.complete(
                messages=[
                    {
                        "role": "system",
                        "content": "You are a creative AI that generates unique poker player personalities.",
                    },
                    {"role": "user", "content": prompt},
                ],
                json_format=True,
                call_type=CallType.PERSONALITY_GENERATION,
                player_name=name,
                prompt_template='personality_generation',
            )

            result = json.loads(response.content)

            # Validate the response has required fields
            required_fields = [
                'play_style',
                'default_confidence',
                'default_attitude',
                'personality_traits',
            ]
            if not all(field in result for field in required_fields):
                return self._create_default_personality(name)

            # Ensure visual_identity exists with non-empty required subfields
            vi = result.get('visual_identity', {})
            if not all(vi.get(k) for k in ['identity', 'appearance', 'apparel']):
                logger.warning(
                    f"[PERSONALITY] Missing visual_identity fields for {name}, using name as identity"
                )
                result['visual_identity'] = {
                    'identity': name,
                    'appearance': vi.get('appearance'),
                    'apparel': vi.get('apparel'),
                }

            # Backfill cash-mode knobs with conservative defaults if the model
            # omitted them. Cash-mode reads tolerate missing sub-dicts (per-field
            # fallback to BANKROLL_KNOB_DEFAULTS / STAKER_PROFILE_DEFAULTS), but
            # persisting explicit defaults keeps the DB row introspectable.
            result.setdefault(
                'bankroll_knobs',
                {
                    'starting_bankroll': 10000,
                    'bankroll_rate': 500,
                    'buy_in_multiplier': 1.0,
                    'stake_comfort_zone': '$10',
                },
            )
            result.setdefault(
                'staker_profile',
                {
                    'willing': True,
                    'max_loan_pct_of_bankroll': 0.05,
                    'floor_anchor': 1.20,
                    'rate_anchor': 0.30,
                    'respect_floor': -0.5,
                    'heat_ceiling': 0.7,
                },
            )
            result.setdefault(
                'borrower_profile',
                {
                    'willing': True,
                    'willingness_threshold': 0.30,
                },
            )
            # Anchors drive tiered-bot archetype classification. Without
            # them, every personality collapses to default-TAG. The schema
            # documents the field in SECTION 4; this setdefault is the
            # safety net when the LLM omits the block or returns garbage.
            existing_anchors = result.get('anchors')
            if not isinstance(existing_anchors, dict) or not existing_anchors:
                logger.warning(
                    f"[PERSONALITY] {name}: LLM omitted anchors; falling back "
                    f"to balanced defaults. Re-run generation to fix."
                )
                result['anchors'] = _default_anchors()

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
                "emoji_usage": round(random.uniform(0.1, 0.6), 2),
            },
            "verbal_tics": [
                "Interesting move",
                "I see what you're doing",
                "Let's make this interesting",
            ],
            "physical_tics": ["*taps table thoughtfully*", "*adjusts position*"],
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
            "anchors": _default_anchors(),
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
