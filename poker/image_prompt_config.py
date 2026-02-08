"""
Image Prompt Configuration for Avatar Generation.

Defines structured visual identity components and assembles them into
image generation prompts. This is the first PromptFactory model —
designed to be extended to text prompts and decision prompts later.

Usage:
    # From personality config (typical path)
    config = ImagePromptConfig.from_personality(personality, name, emotion_detail)
    prompt = config.assemble_prompt()

    # Manual construction (for testing/overrides)
    config = ImagePromptConfig(
        identity="Batman",
        appearance="muscular athletic build, chiseled jaw, dark hair",
        apparel="black armored suit with cape and cowl, utility belt",
        emotion_detail="confident and assured with a slight knowing smirk,",
    )
    prompt = config.assemble_prompt()
"""
from dataclasses import dataclass
from typing import Dict, Any, Optional


# Default values as constants for reuse
DEFAULT_BACKGROUND = "black background"
DEFAULT_FRAMING = "chest-up portrait, centered"
DEFAULT_SETTING = "playing poker"
DEFAULT_ARTISTIC_STYLE = "animated style with clean bold outlines, cel-shaded"
DEFAULT_PROPORTIONS = "realistic proportions, stylized aesthetic"
DEFAULT_MODESTY = "fully clothed"


@dataclass
class ImagePromptConfig:
    """Configuration for assembling image generation prompts.

    Visual identity fields (identity, appearance, apparel) come from
    personality config. Scene and style fields have smart defaults
    matching the current avatar look.
    """

    # Core identity — who they are in the image
    identity: str

    # Visual components from personality
    appearance: Optional[str] = None
    apparel: Optional[str] = None

    # Scene defaults (overridable)
    background: str = DEFAULT_BACKGROUND
    framing: str = DEFAULT_FRAMING
    setting: str = DEFAULT_SETTING

    # Style defaults (overridable)
    artistic_style: str = DEFAULT_ARTISTIC_STYLE
    proportions: str = DEFAULT_PROPORTIONS
    modesty: str = DEFAULT_MODESTY

    # Emotion injected per-image
    emotion_detail: str = ""

    @classmethod
    def from_personality(
        cls,
        personality_config: Dict[str, Any],
        name: str,
        emotion_detail: str = "",
    ) -> "ImagePromptConfig":
        """Create config from personality data.

        Extracts visual_identity fields if present, falls back to
        using the character name as identity.

        Args:
            personality_config: Full personality dict from database
            name: Character name (fallback identity)
            emotion_detail: Emotion description from EMOTION_DETAILS
        """
        vi = personality_config.get("visual_identity", {})

        return cls(
            identity=vi.get("identity", name),
            appearance=vi.get("appearance"),
            apparel=vi.get("apparel"),
            emotion_detail=emotion_detail,
        )

    def assemble_prompt(self) -> str:
        """Assemble the complete image generation prompt.

        Parts are ordered for DALL-E 2 compatibility (prioritizes
        early instructions). Returns a period-separated prompt string.
        """
        parts = []

        # Background first (DALL-E 2 prioritizes early text)
        parts.append(self.background)

        # Emotion + identity + setting
        subject = self.identity
        if self.emotion_detail:
            subject = f"{self.emotion_detail} {subject}"
        parts.append(f"{subject} {self.setting}")

        # Physical appearance
        if self.appearance:
            parts.append(self.appearance)

        # Clothing and accessories
        if self.apparel:
            parts.append(self.apparel)

        # Artistic style line
        parts.append(f"{self.artistic_style}, {self.proportions}, {self.modesty}")

        # Framing
        parts.append(self.framing)

        return ". ".join(parts) + "."

