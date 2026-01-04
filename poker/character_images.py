"""
Character Image Service for AI Poker Players.

Manages character avatar images for different emotional states.
Images are stored in the SQLite database as BLOBs.
Supports on-demand generation for personalities without existing images.
"""

import io
import logging
import os
import urllib.request
from pathlib import Path
from typing import Optional, List, Dict, Any, TYPE_CHECKING

from core.llm import LLMClient, CallType

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

# Prompt template for fictional characters (can use names directly)
PROMPT_TEMPLATE_FICTIONAL = """A stylized cartoon portrait of {character} playing poker at a casino table.
Expression: {emotion_detail}.
Style: Bold outlines, vibrant flat colors, exaggerated expressive features, Pixar-inspired 3D cartoon look.
Chest-up view, facing viewer, centered composition.
Background: solid black background, clean and minimal."""

# Prompt template for real people (uses descriptions to avoid content policy blocks)
PROMPT_TEMPLATE_DESCRIPTION = """A cartoon caricature of {description} playing poker at a casino table.
Expression: {emotion_detail}.
Style: Bold outlines, vibrant flat colors, exaggerated expressive features, Pixar-inspired 3D cartoon look.
Chest-up view, facing viewer, centered composition.
Background: solid black background, clean and minimal."""

# Emotion descriptions for image generation
EMOTION_DETAILS = {
    "confident": "confident and assured, slight knowing smirk, relaxed posture, in control",
    "happy": "genuinely happy, big warm smile, eyes crinkling with joy, celebrating",
    "thinking": "deep in thought, furrowed brow, hand on chin, calculating, focused",
    "nervous": "nervous and sweating, wide anxious eyes, biting lip, visibly stressed",
    "angry": "furious and tilted, red-faced, clenched jaw, intense glare, veins showing",
    "shocked": "completely surprised, jaw dropped, wide eyes, eyebrows raised high",
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

    def generate_images(
        self,
        personality_name: str,
        emotions: Optional[List[str]] = None,
        api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate images for a personality and save to database.

        Args:
            personality_name: Name of the personality
            emotions: List of emotions to generate (default: all missing)
            api_key: OpenAI API key (uses env var if not provided)

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
        llm_client = LLMClient()

        results = {"generated": 0, "failed": 0, "skipped": 0, "errors": []}

        for emotion in emotions:
            if emotion not in EMOTIONS:
                results["skipped"] += 1
                continue

            try:
                # Generate the image and get raw bytes
                raw_image_bytes = self._generate_single_image(llm_client, personality_name, emotion)

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

    def _generate_description_for_celebrity(self, llm_client: LLMClient, name: str) -> str:
        """Use GPT to generate a safe description for a real person."""
        logger.info(f"Auto-generating description for {name}")
        prompt = (
            f"Describe {name}'s appearance for a Pixar-style 3D cartoon caricature in 20-25 words. "
            f"Include: gender, build, hair style/color, skin tone, and 2-3 distinctive features. "
            f"Style: bold outlines, vibrant colors, exaggerated expressive features. "
            f"Setting: playing poker, black background. "
            f"Format: 'a [detailed description] character'. Do NOT use their name."
        )
        response = llm_client.complete(
            messages=[{"role": "user", "content": prompt}],
            call_type=CallType.IMAGE_DESCRIPTION,
            player_name=name
        )
        description = response.content.strip()
        logger.info(f"Generated description for {name}: {description}")
        return description

    def _get_description(self, personality_name: str) -> Optional[str]:
        """Get description for a personality from PersonalityGenerator."""
        if self._personality_generator:
            return self._personality_generator.get_avatar_description(personality_name)
        return None

    def _generate_single_image(self, llm_client: LLMClient, personality_name: str, emotion: str) -> bytes:
        """Generate a single image using DALL-E and return raw bytes.

        Args:
            llm_client: LLMClient for tracked API calls
            personality_name: Name of the personality
            emotion: Emotion to generate

        Returns:
            Raw image bytes (1024x1024 PNG)
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
                size="1024x1024",
                call_type=CallType.IMAGE_GENERATION,
                player_name=personality_name
            )
            if image_response.is_error:
                raise Exception(image_response.error_code or "Image generation failed")
            image_url = image_response.url
        except Exception as e:
            # Check if this is a content policy violation
            if "content_policy_violation" in str(e) and not description:
                logger.info(f"Content policy blocked {personality_name}, generating description...")
                # Generate a description and retry
                description = self._generate_description_for_celebrity(llm_client, personality_name)

                # Save the generated description to PersonalityGenerator
                if self._personality_generator:
                    self._personality_generator.set_avatar_description(personality_name, description)

                prompt = PROMPT_TEMPLATE_DESCRIPTION.format(
                    description=description,
                    emotion_detail=emotion_detail
                )
                image_response = llm_client.generate_image(
                    prompt=prompt,
                    size="1024x1024",
                    call_type=CallType.IMAGE_GENERATION,
                    player_name=personality_name
                )
                if image_response.is_error:
                    raise Exception(image_response.error_code or "Image generation failed")
                image_url = image_response.url
            else:
                raise  # Re-raise if not content policy or already tried description

        # Download image to bytes (not to filesystem)
        with urllib.request.urlopen(image_url) as response_data:
            image_bytes = response_data.read()

        logger.debug(f"Downloaded image for {personality_name} - {emotion} ({len(image_bytes)} bytes)")
        return image_bytes

    def _process_to_icon_and_save(self, personality_name: str, emotion: str, raw_image_bytes: bytes) -> bytes:
        """Process raw image bytes to a circular icon and save to database.

        Args:
            personality_name: Name of the personality
            emotion: Emotion name
            raw_image_bytes: Raw 1024x1024 image bytes

        Returns:
            Processed icon bytes (256x256 circular PNG)
        """
        from PIL import Image, ImageDraw

        # Load image from bytes
        img = Image.open(io.BytesIO(raw_image_bytes))

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

        # Save to bytes buffer
        buffer = io.BytesIO()
        output.save(buffer, 'PNG')
        icon_bytes = buffer.getvalue()

        # Save to database
        self._persistence.save_avatar_image(
            personality_name=personality_name,
            emotion=emotion,
            image_data=icon_bytes,
            width=ICON_SIZE,
            height=ICON_SIZE
        )

        logger.debug(f"Saved icon to database: {personality_name} - {emotion} ({len(icon_bytes)} bytes)")
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
    api_key: Optional[str] = None
) -> Dict[str, Any]:
    """Generate images for a personality and save to database."""
    return get_character_image_service().generate_images(personality_name, emotions, api_key)


def load_avatar_image(personality_name: str, emotion: str) -> Optional[bytes]:
    """Load avatar image bytes for a personality and emotion."""
    return get_character_image_service().load_avatar_image(personality_name, emotion)
