# Image Prompt Factory

The Image Prompt Factory is a structured system for generating consistent, controllable avatar images for AI poker players. It replaces the old hardcoded prompt templates with a data-driven approach where each personality carries visual identity data that feeds into prompt assembly.

This is the first **PromptFactory** model — designed to be extended to text prompts, decision prompts, and other prompt types.

## Overview

```
Personality Config ──> ImagePromptConfig ──> assemble_prompt() ──> Image Provider
    (visual_identity)     (dataclass)          (prompt string)      (DALL-E/Runware/etc)
```

Each personality now stores a `visual_identity` object alongside behavioral traits:

```json
{
    "play_style": "aggressive and theatrical",
    "personality_traits": { ... },
    "visual_identity": {
        "identity": "Cleopatra, the legendary Egyptian pharaoh queen",
        "appearance": "striking regal woman, dark braided hair, kohl-lined eyes, olive skin, young",
        "apparel": "gold and lapis Egyptian royal gown, ornate collar necklace, cobra crown"
    }
}
```

## Visual Identity Fields

| Field | Purpose | Example |
|-------|---------|---------|
| `identity` | Name + who they are. Gives image models context beyond just a name. | `"Lizzo, a bold energetic plus-size pop star and flutist"` |
| `appearance` | Physical features (10-15 words): build, hair, face, skin tone, age. | `"curvy athletic build, long pink hair, warm brown skin, bright smile, 30s"` |
| `apparel` | Clothing and accessories (8-12 words), in character. | `"glittery tracksuit with neon accents, oversized sunglasses, bold jewelry"` |

The `identity` field always includes the character's name. Image models often don't recognize names alone (especially for less famous people), so the description provides enough context to render the right person.

## ImagePromptConfig Dataclass

Located in `poker/image_prompt_config.py`.

### Fields

**From personality (per-character):**
- `identity` — who they are
- `appearance` — what they look like (optional)
- `apparel` — what they're wearing (optional)

**Scene defaults (overridable):**
- `background` — default: `"black background"`
- `framing` — default: `"chest-up portrait, centered"`
- `setting` — default: `"playing poker"`

**Style defaults (overridable):**
- `artistic_style` — default: `"animated style with clean bold outlines, cel-shaded"`
- `proportions` — default: `"realistic proportions, stylized aesthetic"`
- `modesty` — default: `"fully clothed"`

**Per-image:**
- `emotion_detail` — e.g., `"confident and assured with a slight knowing smirk,"`

### Usage

```python
from poker.image_prompt_config import ImagePromptConfig
from poker.character_images import EMOTION_DETAILS

# From personality config (typical path)
config = ImagePromptConfig.from_personality(personality, "Batman", EMOTION_DETAILS["confident"])
prompt = config.assemble_prompt()

# Manual construction (testing/overrides)
config = ImagePromptConfig(
    identity="Batman, the dark knight vigilante of Gotham City",
    appearance="muscular athletic build, chiseled jaw, dark hair, piercing eyes",
    apparel="black armored suit with cape and cowl, utility belt",
    emotion_detail="confident and assured with a slight knowing smirk,",
)
prompt = config.assemble_prompt()
```

### Assembled Prompt

`assemble_prompt()` composes parts in order (background first for DALL-E 2 compatibility):

```
black background. confident and assured with a slight knowing smirk,
Batman, the dark knight vigilante of Gotham City playing poker.
muscular athletic build, chiseled jaw, dark hair, piercing eyes.
black armored suit with cape and cowl, utility belt.
animated style with clean bold outlines, cel-shaded, realistic proportions,
stylized aesthetic, fully clothed. chest-up portrait, centered.
```

## How It Integrates

### Personality Generation

`PersonalityGenerator.GENERATION_PROMPT` now requests `visual_identity` as part of personality creation. When a new AI player is created, the LLM generates behavioral traits AND visual identity in a single call.

If the LLM doesn't return valid visual identity fields, the system falls back to using the character name as the identity with no appearance/apparel data.

### Image Generation

`CharacterImageService._generate_single_image()` flow:

1. Load personality config from `PersonalityGenerator`
2. Create `ImagePromptConfig.from_personality(config, name, emotion_detail)`
3. Call `config.assemble_prompt()` to build the prompt string
4. Send to image provider with negative prompt
5. If content policy blocks → generate archetype identity (drops name) and retry
6. Process to 512x512 full image + 256x256 circular icon, save to DB

### Content Policy Fallback

When an image provider blocks a character name:

1. `_generate_archetype_identity()` calls a text LLM to create an archetype description (e.g., `"a tall distinguished 19th-century statesman"` instead of `"Abraham Lincoln"`)
2. The archetype replaces the `visual_identity` in the personality config and is saved to the database
3. The prompt is reassembled with the archetype and retried
4. Future emotion generations for the same personality use the saved archetype automatically

### Legacy Fallback

If no `PersonalityGenerator` is available (e.g., standalone usage), the system falls back to the old string templates (`PROMPT_TEMPLATE_FICTIONAL` / `PROMPT_TEMPLATE_DESCRIPTION`).

## Backfill Script

For existing personalities that were created before visual identity was added:

```bash
# See what would be updated (no LLM calls)
python3 scripts/backfill_visual_identities.py --dry-run

# Backfill all personalities
python3 scripts/backfill_visual_identities.py

# Backfill a single personality
python3 scripts/backfill_visual_identities.py --name "Bob Ross"

# Inside Docker
docker compose exec backend python3 -m scripts.backfill_visual_identities --dry-run
```

The script skips personalities that already have complete visual identity data. Safe to run multiple times.

## Key Files

| File | Purpose |
|------|---------|
| `poker/image_prompt_config.py` | ImagePromptConfig dataclass, factory method, prompt assembly |
| `poker/character_images.py` | Image generation service, uses ImagePromptConfig |
| `poker/personality_generator.py` | Extended generation prompt with visual_identity |
| `scripts/backfill_visual_identities.py` | Batch backfill for existing personalities |

## Extending to Other Prompt Types

The ImagePromptConfig pattern — dataclass with structured fields, factory method from config, assembly method — can be applied to other prompt types:

- **Decision prompts**: Replace the string concatenation in `controllers.py` with a structured config
- **Chat/commentary prompts**: Structured assembly from personality + context
- **Theme variations**: Override style defaults for seasonal themes, special events, etc.

The scene and style defaults are intentionally configurable for this reason.
