"""
Character Image Service for AI Poker Players.

Manages character avatar images for different emotional states.
Supports on-demand generation for personalities without existing images.
"""

import logging
import os
import urllib.request
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# Directory paths
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
    """Service for managing character avatar images."""

    def __init__(self, personality_generator=None):
        """Initialize the service.

        Args:
            personality_generator: Optional PersonalityGenerator instance for managing descriptions
        """
        self._ensure_directories()
        self._personality_generator = personality_generator

    def _ensure_directories(self):
        """Ensure required directories exist."""
        GENERATED_IMAGES_DIR.mkdir(exist_ok=True)
        GRID_DIR.mkdir(exist_ok=True)
        ICONS_DIR.mkdir(exist_ok=True)

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

        # Check if icon exists
        icon_filename = self._get_icon_filename(personality_name, emotion)
        icon_path = ICONS_DIR / icon_filename

        if icon_path.exists():
            return f"/api/character-grid/icons/{icon_filename}"

        return None

    def has_images(self, personality_name: str) -> bool:
        """Check if any images exist for a personality."""
        for emotion in EMOTIONS:
            icon_filename = self._get_icon_filename(personality_name, emotion)
            if (ICONS_DIR / icon_filename).exists():
                return True
        return False

    def get_available_emotions(self, personality_name: str) -> List[str]:
        """Get list of emotions that have generated images for a personality."""
        available = []
        for emotion in EMOTIONS:
            icon_filename = self._get_icon_filename(personality_name, emotion)
            if (ICONS_DIR / icon_filename).exists():
                available.append(emotion)
        return available

    def get_missing_emotions(self, personality_name: str) -> List[str]:
        """Get list of emotions that need images generated for a personality."""
        available = set(self.get_available_emotions(personality_name))
        return [e for e in EMOTIONS if e not in available]

    def generate_images(
        self,
        personality_name: str,
        emotions: Optional[List[str]] = None,
        api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate images for a personality.

        Args:
            personality_name: Name of the personality
            emotions: List of emotions to generate (default: all missing)
            api_key: OpenAI API key (uses env var if not provided)

        Returns:
            Dict with 'success', 'generated', 'failed', 'skipped' counts
        """
        try:
            from openai import OpenAI
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

        # Initialize OpenAI client
        client = OpenAI(api_key=api_key) if api_key else OpenAI()

        results = {"generated": 0, "failed": 0, "skipped": 0, "errors": []}

        for emotion in emotions:
            if emotion not in EMOTIONS:
                results["skipped"] += 1
                continue

            try:
                # Generate the image
                self._generate_single_image(client, personality_name, emotion)

                # Process to circular icon
                self._process_to_icon(personality_name, emotion)

                # Track the generated image in personality data
                if self._personality_generator:
                    self._personality_generator.add_avatar_image(personality_name, emotion)

                results["generated"] += 1
                logger.info(f"Generated {personality_name} - {emotion}")

            except Exception as e:
                results["failed"] += 1
                results["errors"].append(f"{emotion}: {str(e)}")
                logger.error(f"Failed to generate {personality_name} - {emotion}: {e}")

        results["success"] = results["failed"] == 0
        return results

    def _generate_description_for_celebrity(self, client, name: str) -> str:
        """Use GPT to generate a safe description for a real person."""
        logger.info(f"Auto-generating description for {name}")
        response = client.chat.completions.create(
            model="gpt-5-mini",
            messages=[{
                "role": "user",
                "content": f"Describe {name}'s appearance for a Pixar-style 3D cartoon caricature in 20-25 words. "
                           f"Include: gender, build, hair style/color, skin tone, and 2-3 distinctive features. "
                           f"Style: bold outlines, vibrant colors, exaggerated expressive features. "
                           f"Setting: playing poker, black background. "
                           f"Format: 'a [detailed description] character'. Do NOT use their name."
            }]
        )
        description = response.choices[0].message.content.strip()
        logger.info(f"Generated description for {name}: {description}")
        return description

    def _get_description(self, client, personality_name: str) -> Optional[str]:
        """Get description for a personality from PersonalityGenerator."""
        if self._personality_generator:
            return self._personality_generator.get_avatar_description(personality_name)
        return None

    def _generate_single_image(self, client, personality_name: str, emotion: str):
        """Generate a single image using DALL-E."""
        emotion_detail = EMOTION_DETAILS.get(emotion, EMOTION_DETAILS["confident"])

        # Check if we have a description (pre-defined or cached)
        description = self._get_description(client, personality_name)

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
            response = client.images.generate(
                model="dall-e-2",
                prompt=prompt,
                n=1,
                size="1024x1024",
            )
        except Exception as e:
            # Check if this is a content policy violation
            if "content_policy_violation" in str(e) and not description:
                logger.info(f"Content policy blocked {personality_name}, generating description...")
                # Generate a description and retry
                description = self._generate_description_for_celebrity(client, personality_name)

                # Save the generated description to PersonalityGenerator
                if self._personality_generator:
                    self._personality_generator.set_avatar_description(personality_name, description)

                prompt = PROMPT_TEMPLATE_DESCRIPTION.format(
                    description=description,
                    emotion_detail=emotion_detail
                )
                response = client.images.generate(
                    model="dall-e-2",
                    prompt=prompt,
                    n=1,
                    size="1024x1024",
                )
            else:
                raise  # Re-raise if not content policy or already tried description

        # Download and save the image
        image_url = response.data[0].url
        filename = self._get_image_filename(personality_name, emotion)
        filepath = GRID_DIR / filename

        urllib.request.urlretrieve(image_url, filepath)
        logger.debug(f"Downloaded: {filepath}")

    def _process_to_icon(self, personality_name: str, emotion: str):
        """Process a full-size image to a circular icon."""
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

        # Save icon
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


def get_character_image_service(personality_generator=None) -> CharacterImageService:
    """Get the singleton CharacterImageService instance.

    Args:
        personality_generator: Optional PersonalityGenerator to use for descriptions.
                              Only used on first initialization.
    """
    global _service
    if _service is None:
        _service = CharacterImageService(personality_generator)
    return _service


def init_character_image_service(personality_generator) -> CharacterImageService:
    """Initialize the CharacterImageService with a PersonalityGenerator.

    Should be called once at app startup to enable description management.
    """
    global _service
    _service = CharacterImageService(personality_generator)
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
    """Generate images for a personality."""
    return get_character_image_service().generate_images(personality_name, emotions, api_key)
