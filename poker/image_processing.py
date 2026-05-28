"""Shared avatar image post-processing.

Turns an arbitrary raw image (any size, any supported format) into the two PNG
artifacts the app stores for every avatar: a square "full" image for CSS
cropping and a circular icon with transparency outside the circle. Both the AI
personality avatars (`poker/character_images.py`) and the human user avatars
(`poker/user_avatar_service.py`) run through here so the two pipelines stay
pixel-identical.
"""

import io
from typing import Optional, Tuple

from PIL import Image, ImageDraw

# Default output sizes, matching the AI avatar pipeline.
ICON_SIZE = 256
FULL_SIZE = 512

# Magic-byte signatures for the formats we accept on upload. WebP is checked
# separately because it lives inside a RIFF container.
_IMAGE_SIGNATURES = {
    b'\x89PNG\r\n\x1a\n': 'image/png',
    b'\xff\xd8\xff': 'image/jpeg',
    b'GIF87a': 'image/gif',
    b'GIF89a': 'image/gif',
}


def detect_image_mimetype(image_data: bytes) -> Optional[str]:
    """Return the MIME type for ``image_data`` by inspecting magic bytes.

    Returns ``None`` for anything that isn't a PNG, JPEG, GIF, or WebP — the
    caller should treat that as an invalid upload. This is a format gate, not a
    full decode; ``process_avatar_image`` does the real (PIL) validation.
    """
    for signature, mime_type in _IMAGE_SIGNATURES.items():
        if image_data[: len(signature)] == signature:
            return mime_type
    if len(image_data) >= 12 and image_data[:4] == b'RIFF' and image_data[8:12] == b'WEBP':
        return 'image/webp'
    return None


def process_avatar_image(
    raw_image_bytes: bytes,
    icon_size: int = ICON_SIZE,
    full_size: int = FULL_SIZE,
) -> Tuple[bytes, bytes, int, int]:
    """Process raw image bytes into ``(icon_bytes, full_bytes, full_w, full_h)``.

    ``full`` is a ``full_size`` square PNG (center-cropped then resized when the
    source isn't already square at that size). ``icon`` is an ``icon_size``
    circular RGBA PNG, transparent outside the circle. Both are always PNG
    regardless of the input format.
    """
    img = Image.open(io.BytesIO(raw_image_bytes))
    original_width, original_height = img.size

    # Normalize to a square `full_size` image (handles non-square img2img output).
    if original_width != original_height or original_width != full_size:
        if original_width != original_height:
            crop_size = min(original_width, original_height)
            left = (original_width - crop_size) // 2
            top = (original_height - crop_size) // 2
            img = img.crop((left, top, left + crop_size, top + crop_size))
        img = img.resize((full_size, full_size), Image.Resampling.LANCZOS)

    full_width, full_height = img.size
    buffer = io.BytesIO()
    img.save(buffer, 'PNG')
    full_bytes = buffer.getvalue()

    # Center-crop to square (img is already square here) and resize to the icon.
    size = min(img.size)
    left = (img.width - size) // 2
    top = (img.height - size) // 2
    cropped = img.crop((left, top, left + size, top + size))
    resized = cropped.resize((icon_size, icon_size), Image.Resampling.LANCZOS).convert('RGBA')

    # Circular transparency mask.
    mask = Image.new('L', (icon_size, icon_size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, icon_size, icon_size), fill=255)
    output = Image.new('RGBA', (icon_size, icon_size), (0, 0, 0, 0))
    output.paste(resized, (0, 0), mask)

    buffer = io.BytesIO()
    output.save(buffer, 'PNG')
    icon_bytes = buffer.getvalue()

    return icon_bytes, full_bytes, full_width, full_height
