"""
Character Image Service for AI Poker Players.

Manages character avatar images for different emotional states.
Images are stored in the SQLite database as BLOBs.
Supports on-demand generation for personalities without existing images.
"""

import io
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Optional, List, Dict, Any, TYPE_CHECKING

from core.llm import LLMClient, CallType
from core.llm.config import POLLINATIONS_RATE_LIMIT_DELAY

if TYPE_CHECKING:
    from .persistence import GamePersistence

logger = logging.getLogger(__name__)

# Directory paths (kept for filesystem fallback during migration)
BASE_DIR = Path(__file__).parent.parent
GENERATED_IMAGES_DIR = BASE_DIR / "generated_images"
GRID_DIR = GENERATED_IMAGES_DIR / "grid"
ICONS_DIR = GRID_DIR / "icons"

# Available emotions for avatars
EMOTIONS = ["confident", "happy", "thinking", "nervous", "angry", "shocked"]

# Icon size for processed images
ICON_SIZE = 256

# Image provider configuration
# Priority: 1. Database (app_settings), 2. Environment variable, 3. Default
# IMAGE_PROVIDER: "openai" (default), "pollinations", "runware", etc.
# IMAGE_MODEL: model to use (provider-specific default if not set)

def get_image_provider() -> str:
    """Get the image provider from app_settings or environment."""
    from .persistence import GamePersistence
    p = GamePersistence()
    db_value = p.get_setting('IMAGE_PROVIDER', '')
    if db_value:
        return db_value
    return os.environ.get("IMAGE_PROVIDER", "openai")


def get_image_model() -> Optional[str]:
    """Get the image model from app_settings or environment."""
    from .persistence import GamePersistence
    p = GamePersistence()
    db_value = p.get_setting('IMAGE_MODEL', '')
    if db_value:
        return db_value
    return os.environ.get("IMAGE_MODEL")


# Legacy module-level constants for backward compatibility (read at module load)
# Note: These may not reflect runtime app_settings changes
IMAGE_PROVIDER = os.environ.get("IMAGE_PROVIDER", "openai")
IMAGE_MODEL = os.environ.get("IMAGE_MODEL")

# Full image generation size (512x512 for DALL-E 2, 1024x1024 for DALL-E 3)
# The full image is stored for CSS-based cropping on the frontend
FULL_IMAGE_SIZE = "512x512"
FULL_IMAGE_DIMENSIONS = (512, 512)

# Prompt template for fictional characters (can use names directly)
# DALL-E 2 prioritizes early instructions, so put black background first
PROMPT_TEMPLATE_FICTIONAL = """Black background, cartoonish caricature of {emotion_detail} {character} playing poker.
Bold outlines, vibrant colors, exaggerated features. Chest-up view, centered."""

# Prompt template for real people (uses descriptions to avoid content policy blocks)
PROMPT_TEMPLATE_DESCRIPTION = """Black background, cartoonish caricature of {emotion_detail} {description} playing poker.
Bold outlines, vibrant colors, exaggerated features. Chest-up view, centered."""

# Emotion descriptions for image generation - grammatically complete phrases
EMOTION_DETAILS = {
    "confident": "confident and assured with a slight knowing smirk,",
    "happy": "genuinely happy with a big warm smile, eyes crinkling with joy,",
    "thinking": "thoughtful with a furrowed brow, hand on chin, focused,",
    "nervous": "nervous and sweating with wide anxious eyes, visibly stressed,",
    "angry": "angry and furious, red-faced with a clenched jaw, intense glare,",
    "shocked": "completely surprised with jaw dropped and wide eyes,",
}


class CharacterImageService:
    """Service for managing character avatar images.

    Images are stored in the SQLite database as BLOBs via the persistence layer.
    Filesystem storage is only used as a fallback for migration of existing images.
    """

    def __init__(self, personality_generator=None, persistence: Optional["GamePersistence"] = None):
        """Initialize the service.

        Args:
            personality_generator: Optional PersonalityGenerator instance for managing descriptions
            persistence: Optional GamePersistence instance for database access
        """
        self._personality_generator = personality_generator
        self._persistence = persistence

        # Initialize persistence if not provided
        if self._persistence is None:
            from .persistence import GamePersistence
            db_path = self._get_default_db_path()
            self._persistence = GamePersistence(db_path)

    def _get_default_db_path(self) -> str:
        """Get the default database path based on environment."""
        if Path('/app/data').exists():
            return '/app/data/poker_games.db'
        else:
            return str(Path(__file__).parent.parent / 'poker_games.db')

    def get_avatar_url(self, personality_name: str, emotion: str = "confident") -> Optional[str]:
        """
        Get the URL for a character's avatar image.

        Args:
            personality_name: Name of the personality (e.g., "Bob Ross")
            emotion: One of: confident, happy, thinking, nervous, angry, shocked

        Returns:
            URL path to the avatar image, or None if not available
        """
        # Normalize inputs
        emotion = emotion.lower() if emotion else "confident"
        if emotion not in EMOTIONS:
            emotion = "confident"

        # Check database first (source of truth)
        if self._persistence.has_avatar_image(personality_name, emotion):
            return f"/api/avatar/{personality_name}/{emotion}"

        # Fallback to filesystem during migration
        icon_filename = self._get_icon_filename(personality_name, emotion)
        icon_path = ICONS_DIR / icon_filename
        if icon_path.exists():
            return f"/api/character-grid/icons/{icon_filename}"

        return None

    def has_images(self, personality_name: str) -> bool:
        """Check if any images exist for a personality."""
        # Check database first
        db_emotions = self._persistence.get_available_avatar_emotions(personality_name)
        if db_emotions:
            return True

        # Fallback to filesystem during migration
        for emotion in EMOTIONS:
            icon_filename = self._get_icon_filename(personality_name, emotion)
            if (ICONS_DIR / icon_filename).exists():
                return True
        return False

    def get_available_emotions(self, personality_name: str) -> List[str]:
        """Get list of emotions that have generated images for a personality."""
        # Check database first
        db_emotions = self._persistence.get_available_avatar_emotions(personality_name)

        # Also check filesystem for migration fallback
        fs_emotions = []
        for emotion in EMOTIONS:
            icon_filename = self._get_icon_filename(personality_name, emotion)
            if (ICONS_DIR / icon_filename).exists():
                fs_emotions.append(emotion)

        # Combine both sources, remove duplicates
        return list(set(db_emotions + fs_emotions))

    def get_missing_emotions(self, personality_name: str) -> List[str]:
        """Get list of emotions that need images generated for a personality."""
        available = set(self.get_available_emotions(personality_name))
        return [e for e in EMOTIONS if e not in available]

    def load_avatar_image(self, personality_name: str, emotion: str) -> Optional[bytes]:
        """Load avatar image bytes from database or filesystem.

        Args:
            personality_name: Name of the personality
            emotion: Emotion name

        Returns:
            PNG image bytes or None if not found
        """
        # Check database first
        image_data = self._persistence.load_avatar_image(personality_name, emotion)
        if image_data:
            return image_data

        # Fallback to filesystem during migration
        icon_filename = self._get_icon_filename(personality_name, emotion)
        icon_path = ICONS_DIR / icon_filename
        if icon_path.exists():
            with open(icon_path, 'rb') as f:
                return f.read()

        return None

    def load_full_avatar_image(self, personality_name: str, emotion: str) -> Optional[bytes]:
        """Load full uncropped avatar image bytes from database.

        Args:
            personality_name: Name of the personality
            emotion: Emotion name

        Returns:
            Full PNG image bytes or None if not found
        """
        return self._persistence.load_full_avatar_image(personality_name, emotion)

    def get_full_avatar_url(self, personality_name: str, emotion: str = "confident") -> Optional[str]:
        """Get the URL for a character's full uncropped avatar image.

        Args:
            personality_name: Name of the personality (e.g., "Bob Ross")
            emotion: One of: confident, happy, thinking, nervous, angry, shocked

        Returns:
            URL path to the full avatar image, or None if not available
        """
        # Normalize inputs
        emotion = emotion.lower() if emotion else "confident"
        if emotion not in EMOTIONS:
            emotion = "confident"

        # Check if full image exists in database
        if self._persistence.has_full_avatar_image(personality_name, emotion):
            return f"/api/avatar/{personality_name}/{emotion}/full"

        return None

    def regenerate_emotion(
        self,
        personality_name: str,
        emotion: str,
        game_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Regenerate a single emotion image for a personality.

        Args:
            personality_name: Name of the personality
            emotion: Emotion to regenerate
            game_id: Optional game ID for tracking

        Returns:
            Dict with 'success', 'message', and optionally 'error'
        """
        if emotion not in EMOTIONS:
            return {
                "success": False,
                "error": f"Invalid emotion: {emotion}. Must be one of: {EMOTIONS}"
            }

        try:
            from PIL import Image, ImageDraw
        except ImportError as e:
            return {"success": False, "error": f"Missing dependency: {e}"}

        try:
            llm_client = LLMClient(provider=get_image_provider(), model=get_image_model())
            raw_image_bytes = self._generate_single_image(llm_client, personality_name, emotion, game_id=game_id)
            self._process_to_icon_and_save(personality_name, emotion, raw_image_bytes)
            return {
                "success": True,
                "message": f"Successfully regenerated {emotion} for {personality_name}"
            }
        except Exception as e:
            logger.error(f"Failed to regenerate {personality_name} - {emotion}: {e}")
            return {"success": False, "error": str(e)}

    def generate_images(
        self,
        personality_name: str,
        emotions: Optional[List[str]] = None,
        api_key: Optional[str] = None,
        game_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate images for a personality and save to database.

        Args:
            personality_name: Name of the personality
            emotions: List of emotions to generate (default: all missing)
            api_key: OpenAI API key (uses env var if not provided)
            game_id: Game ID for tracking (owner derived via JOIN)

        Returns:
            Dict with 'success', 'generated', 'failed', 'skipped' counts
        """
        try:
            from PIL import Image, ImageDraw
        except ImportError as e:
            logger.error(f"Missing dependency for image generation: {e}")
            return {
                "success": False,
                "error": f"Missing dependency: {e}",
                "generated": 0,
                "failed": 0,
                "skipped": 0
            }

        # Determine which emotions to generate
        if emotions is None:
            emotions = self.get_missing_emotions(personality_name)

        if not emotions:
            return {
                "success": True,
                "message": "All images already exist",
                "generated": 0,
                "failed": 0,
                "skipped": len(EMOTIONS)
            }

        # Initialize LLM client for tracked API calls
        # Use app_settings IMAGE_PROVIDER and IMAGE_MODEL for image generation
        image_provider = get_image_provider()
        image_model = get_image_model()
        llm_client = LLMClient(provider=image_provider, model=image_model)

        results = {"generated": 0, "failed": 0, "skipped": 0, "errors": []}

        # Check if we're using Pollinations (needs rate limiting)
        needs_rate_limit = image_provider == "pollinations" and POLLINATIONS_RATE_LIMIT_DELAY > 0

        for i, emotion in enumerate(emotions):
            if emotion not in EMOTIONS:
                results["skipped"] += 1
                continue

            # Add delay between requests to respect rate limits (skip first request)
            if needs_rate_limit and i > 0:
                logger.info(f"Rate limit delay: waiting {POLLINATIONS_RATE_LIMIT_DELAY}s before next image")
                time.sleep(POLLINATIONS_RATE_LIMIT_DELAY)

            try:
                # Generate the image and get raw bytes
                raw_image_bytes = self._generate_single_image(llm_client, personality_name, emotion, game_id=game_id)

                # Process to circular icon and save to database
                self._process_to_icon_and_save(personality_name, emotion, raw_image_bytes)

                results["generated"] += 1
                logger.info(f"Generated and saved {personality_name} - {emotion} to database")

            except Exception as e:
                results["failed"] += 1
                results["errors"].append(f"{emotion}: {str(e)}")
                logger.error(f"Failed to generate {personality_name} - {emotion}: {e}")

        results["success"] = results["failed"] == 0
        return results

    def _generate_description_for_celebrity(self, llm_client: LLMClient, name: str,
                                            game_id: Optional[str] = None) -> str:
        """Use GPT to generate a physical description for a real person.

        This generates ONLY the physical description - style, background, and emotion
        are added by the prompt template.
        """
        logger.info(f"Auto-generating physical description for {name}")
        prompt = (
            f"Describe {name}'s physical appearance in 15-20 words for a cartoon portrait. "
            f"Include: gender, build, hair style/color, skin tone, and 2-3 distinctive features. "
            f"Output ONLY the physical description, no style or setting details. "
            f"Format: 'a [physical description] character'. Do NOT use their name."
        )
        response = llm_client.complete(
            messages=[{"role": "user", "content": prompt}],
            call_type=CallType.IMAGE_DESCRIPTION,
            game_id=game_id,
            player_name=name,
            prompt_template='image_description',
        )
        description = response.content.strip()
        logger.info(f"Generated description for {name}: {description}")
        return description

    def _get_description(self, personality_name: str) -> Optional[str]:
        """Get description for a personality from PersonalityGenerator."""
        if self._personality_generator:
            return self._personality_generator.get_avatar_description(personality_name)
        return None

    def _generate_single_image(self, llm_client: LLMClient, personality_name: str, emotion: str,
                                game_id: Optional[str] = None) -> bytes:
        """Generate a single image using DALL-E and return raw bytes.

        Args:
            llm_client: LLMClient for tracked API calls
            personality_name: Name of the personality
            emotion: Emotion to generate
            game_id: Game ID for tracking (owner derived via JOIN)

        Returns:
            Raw image bytes (512x512 PNG by default, configurable via FULL_IMAGE_SIZE)
        """
        emotion_detail = EMOTION_DETAILS.get(emotion, EMOTION_DETAILS["confident"])

        # Check if we have a description (pre-defined or cached)
        description = self._get_description(personality_name)

        if description:
            prompt = PROMPT_TEMPLATE_DESCRIPTION.format(
                description=description,
                emotion_detail=emotion_detail
            )
            logger.debug(f"Using description-based prompt for {personality_name}")
        else:
            # Try with name directly (works for fictional characters)
            prompt = PROMPT_TEMPLATE_FICTIONAL.format(
                character=personality_name,
                emotion_detail=emotion_detail
            )

        try:
            image_response = llm_client.generate_image(
                prompt=prompt,
                size=FULL_IMAGE_SIZE,
                call_type=CallType.IMAGE_GENERATION,
                game_id=game_id,
                player_name=personality_name,
                prompt_template='avatar_generation',
                target_emotion=emotion,
            )
            if image_response.is_error:
                # Check for content policy violation before raising
                if image_response.error_code == "content_policy_violation" and not description:
                    logger.info(f"Content policy blocked {personality_name}, generating description...")
                    description = self._generate_description_for_celebrity(llm_client, personality_name, game_id=game_id)

                    # Save the generated description to PersonalityGenerator
                    if self._personality_generator:
                        self._personality_generator.set_avatar_description(personality_name, description)

                    # Retry with description-based prompt
                    prompt = PROMPT_TEMPLATE_DESCRIPTION.format(
                        description=description,
                        emotion_detail=emotion_detail
                    )
                    image_response = llm_client.generate_image(
                        prompt=prompt,
                        size=FULL_IMAGE_SIZE,
                        call_type=CallType.IMAGE_GENERATION,
                        game_id=game_id,
                        player_name=personality_name,
                        prompt_template='avatar_generation_fallback',
                        target_emotion=emotion,
                    )
                    if image_response.is_error:
                        logger.error(f"Second attempt also failed for {personality_name}/{emotion}: {image_response.error_message}")
                        raise Exception(f"Image generation failed for {personality_name} ({emotion}) after content policy fallback: {image_response.error_message or 'Unknown error'}")
                else:
                    raise Exception(image_response.error_message or image_response.error_code or "Image generation failed")
            image_url = image_response.url
        except Exception as e:
            # Re-raise any other exceptions
            raise

        # Download image to bytes (not to filesystem)
        with urllib.request.urlopen(image_url) as response_data:
            image_bytes = response_data.read()

        logger.debug(f"Downloaded image for {personality_name} - {emotion} ({len(image_bytes)} bytes)")
        return image_bytes

    def _process_to_icon_and_save(self, personality_name: str, emotion: str, raw_image_bytes: bytes) -> bytes:
        """Process raw image bytes to a circular icon and save both full and icon to database.

        Args:
            personality_name: Name of the personality
            emotion: Emotion name
            raw_image_bytes: Raw image bytes (512x512 by default)

        Returns:
            Processed icon bytes (256x256 circular PNG)
        """
        from PIL import Image, ImageDraw

        # Load image from bytes
        img = Image.open(io.BytesIO(raw_image_bytes))
        full_width, full_height = img.size

        # Center crop to square for icon
        size = min(img.size)
        left = (img.width - size) // 2
        top = (img.height - size) // 2
        cropped = img.crop((left, top, left + size, top + size))

        # Resize to icon size
        resized = cropped.resize((ICON_SIZE, ICON_SIZE), Image.Resampling.LANCZOS)

        # Create circular mask
        mask = Image.new('L', (ICON_SIZE, ICON_SIZE), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0, ICON_SIZE, ICON_SIZE), fill=255)

        # Apply mask for circular crop with transparency
        output = Image.new('RGBA', (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
        resized = resized.convert('RGBA')
        output.paste(resized, (0, 0), mask)

        # Save icon to bytes buffer
        buffer = io.BytesIO()
        output.save(buffer, 'PNG')
        icon_bytes = buffer.getvalue()

        # Save both to database - full image for CSS cropping, icon for backward compatibility
        self._persistence.save_avatar_image(
            personality_name=personality_name,
            emotion=emotion,
            image_data=icon_bytes,
            width=ICON_SIZE,
            height=ICON_SIZE,
            full_image_data=raw_image_bytes,
            full_width=full_width,
            full_height=full_height
        )

        logger.debug(f"Saved icon ({len(icon_bytes)} bytes) and full image ({len(raw_image_bytes)} bytes) to database: {personality_name} - {emotion}")
        return icon_bytes

    def _process_to_icon(self, personality_name: str, emotion: str):
        """Process a full-size image to a circular icon (legacy filesystem method).

        DEPRECATED: Use _process_to_icon_and_save for new images.
        This method is kept for backwards compatibility during migration.
        """
        from PIL import Image, ImageDraw

        # Load the full-size image
        image_filename = self._get_image_filename(personality_name, emotion)
        image_path = GRID_DIR / image_filename

        if not image_path.exists():
            raise FileNotFoundError(f"Source image not found: {image_path}")

        img = Image.open(image_path)

        # Center crop to square
        size = min(img.size)
        left = (img.width - size) // 2
        top = (img.height - size) // 2
        img = img.crop((left, top, left + size, top + size))

        # Resize to icon size
        img = img.resize((ICON_SIZE, ICON_SIZE), Image.Resampling.LANCZOS)

        # Create circular mask
        mask = Image.new('L', (ICON_SIZE, ICON_SIZE), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0, ICON_SIZE, ICON_SIZE), fill=255)

        # Apply mask for circular crop with transparency
        output = Image.new('RGBA', (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
        img = img.convert('RGBA')
        output.paste(img, (0, 0), mask)

        # Save icon to filesystem
        icon_filename = self._get_icon_filename(personality_name, emotion)
        icon_path = ICONS_DIR / icon_filename
        output.save(icon_path, 'PNG')
        logger.debug(f"Created icon: {icon_path}")

    def _get_image_filename(self, personality_name: str, emotion: str) -> str:
        """Get the filename for a full-size image."""
        slug = personality_name.lower().replace(' ', '_')
        return f"{slug}_{emotion}.png"

    def _get_icon_filename(self, personality_name: str, emotion: str) -> str:
        """Get the filename for an icon."""
        slug = personality_name.lower().replace(' ', '_')
        return f"{slug}_{emotion}.png"

    def get_all_personalities_with_images(self) -> List[str]:
        """Get list of all personalities that have at least one image."""
        personalities = set()
        if ICONS_DIR.exists():
            for f in ICONS_DIR.glob("*.png"):
                # Parse personality from filename: personality_name_emotion.png
                parts = f.stem.rsplit('_', 1)
                if len(parts) == 2:
                    personality = parts[0].replace('_', ' ').title()
                    personalities.add(personality)
        return sorted(personalities)


# Singleton instance
_service: Optional[CharacterImageService] = None


def get_character_image_service(
    personality_generator=None,
    persistence: Optional["GamePersistence"] = None
) -> CharacterImageService:
    """Get the singleton CharacterImageService instance.

    Args:
        personality_generator: Optional PersonalityGenerator to use for descriptions.
                              Only used on first initialization.
        persistence: Optional GamePersistence instance for database access.
                    Only used on first initialization.
    """
    global _service
    if _service is None:
        _service = CharacterImageService(personality_generator, persistence)
    return _service


def init_character_image_service(
    personality_generator=None,
    persistence: Optional["GamePersistence"] = None
) -> CharacterImageService:
    """Initialize the CharacterImageService with dependencies.

    Should be called once at app startup to enable database storage and description management.

    Args:
        personality_generator: Optional PersonalityGenerator for descriptions
        persistence: Optional GamePersistence for database access
    """
    global _service
    _service = CharacterImageService(personality_generator, persistence)
    return _service


# Convenience functions
def get_avatar_url(personality_name: str, emotion: str = "confident") -> Optional[str]:
    """Get avatar URL for a personality and emotion."""
    return get_character_image_service().get_avatar_url(personality_name, emotion)


def has_character_images(personality_name: str) -> bool:
    """Check if images exist for a personality."""
    return get_character_image_service().has_images(personality_name)


def generate_character_images(
    personality_name: str,
    emotions: Optional[List[str]] = None,
    api_key: Optional[str] = None,
    game_id: Optional[str] = None
) -> Dict[str, Any]:
    """Generate images for a personality and save to database."""
    return get_character_image_service().generate_images(personality_name, emotions, api_key, game_id=game_id)


def load_avatar_image(personality_name: str, emotion: str) -> Optional[bytes]:
    """Load avatar image bytes for a personality and emotion."""
    return get_character_image_service().load_avatar_image(personality_name, emotion)


def load_full_avatar_image(personality_name: str, emotion: str) -> Optional[bytes]:
    """Load full uncropped avatar image bytes for a personality and emotion."""
    return get_character_image_service().load_full_avatar_image(personality_name, emotion)


def get_full_avatar_url(personality_name: str, emotion: str = "confident") -> Optional[str]:
    """Get URL for full uncropped avatar image."""
    return get_character_image_service().get_full_avatar_url(personality_name, emotion)


def regenerate_avatar_emotion(
    personality_name: str,
    emotion: str,
    game_id: Optional[str] = None
) -> Dict[str, Any]:
    """Regenerate a single emotion image for a personality."""
    return get_character_image_service().regenerate_emotion(personality_name, emotion, game_id=game_id)


def get_available_emotions() -> List[str]:
    """Get the list of available emotions for avatars."""
    return EMOTIONS.copy()
