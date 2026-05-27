"""Acquire, process, and persist a human user's avatar image.

Four acquisition paths feed one processing + storage pipeline:

    store_from_bytes()     a file the user uploaded
    store_from_url()       an image URL the user pasted
    generate_from_prompt() text -> image (LLM)
    generate_from_photo()  photo -> stylized image (LLM img2img)

They all converge on ``_store``: validate the format, run the shared avatar
pipeline (square full + circular icon), persist via ``UserAvatarRepository``,
and return the public ``/api/user-avatar/<public_id>`` URL the rest of the app
embeds. Image generation reuses the same provider/model and negative prompt as
the AI personality avatars.
"""

from __future__ import annotations

import base64
import ipaddress
import logging
import re
import socket
import urllib.request
from typing import Optional
from urllib.parse import urlparse

from core.llm import CallType, LLMClient
from core.llm.settings import get_image_model, get_image_provider

from .character_images import NEGATIVE_PROMPT
from .image_processing import detect_image_mimetype, process_avatar_image
from .repositories.user_avatar_repository import UserAvatarRepository

logger = logging.getLogger(__name__)

AVATAR_IMAGE_SIZE = "512x512"

# Cap on fetched/uploaded image size. Mirrors the route-level Content-Length
# gate, but enforced on the actual bytes for the server-side URL fetch (where
# the client-declared Content-Length can't be trusted).
MAX_AVATAR_BYTES = 8 * 1024 * 1024  # 8 MB

# Timeout for any server-side image download (URL fetch + generated-image pull).
_DOWNLOAD_TIMEOUT_SECONDS = 20

# Default instruction for the img2img path when the user doesn't type one — the
# uploaded photo carries the likeness; this just sets the rendering style.
DEFAULT_PHOTO_PROMPT = "a stylized portrait of this person"

_INVALID_FORMAT_MSG = "Invalid image format. Supported: PNG, JPEG, GIF, WebP."


def avatar_url_for(public_id: str) -> str:
    """The public, identity-free URL for a stored avatar."""
    return f"/api/user-avatar/{public_id}"


def _validate_public_url(url: str) -> None:
    """Reject non-HTTP(S) and internal/private URLs (SSRF guard).

    Resolves the hostname and blocks any address that is loopback, private,
    link-local, or otherwise non-global so a user can't point the server-side
    fetch at the cloud metadata endpoint or an internal service. (DNS rebinding
    after this check is a residual risk; this blocks the common cases.)
    """
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        raise UserAvatarError("Image URL must start with http:// or https://.")
    host = parsed.hostname
    if not host:
        raise UserAvatarError("Image URL is missing a host.")
    try:
        infos = socket.getaddrinfo(host, parsed.port or 80, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise UserAvatarError(f"Could not resolve that URL's host: {e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_loopback or ip.is_link_local:
            raise UserAvatarError("That URL points to a non-public address.")


def _read_capped(response) -> bytes:
    """Read a streamed `requests` response, raising if it exceeds the cap."""
    chunks = []
    total = 0
    for chunk in response.iter_content(64 * 1024):
        total += len(chunk)
        if total > MAX_AVATAR_BYTES:
            raise UserAvatarError("Image is too large (max 8 MB).")
        chunks.append(chunk)
    return b''.join(chunks)


class UserAvatarError(ValueError):
    """A user-correctable avatar failure (bad format, fetch/generation failed).

    Routes map this to a 400 with the message shown to the user, distinct from
    unexpected 500s.
    """


class UserAvatarService:
    """Stateless service over `UserAvatarRepository` + the image LLM."""

    def __init__(self, avatar_repo: UserAvatarRepository):
        self._repo = avatar_repo

    # --- acquisition paths -------------------------------------------------

    def store_from_bytes(self, user_id: str, image_bytes: bytes, source: str = 'upload') -> str:
        """Process and store raw uploaded bytes. Returns the avatar URL."""
        return self._store(user_id, image_bytes, source)

    def store_from_url(self, user_id: str, url: str) -> str:
        """Fetch ``url`` (with SSRF guard + size cap), process, and store it."""
        _validate_public_url(url)
        try:
            import requests

            with requests.get(url, timeout=_DOWNLOAD_TIMEOUT_SECONDS, stream=True) as response:
                response.raise_for_status()
                image_bytes = _read_capped(response)
        except UserAvatarError:
            raise
        except Exception as e:
            raise UserAvatarError(f"Could not fetch image from that URL: {e}") from e
        return self._store(user_id, image_bytes, source='url')

    def generate_from_prompt(
        self, user_id: str, prompt: str, *, game_id: Optional[str] = None
    ) -> str:
        """Text-to-image generation from the user's description."""
        if not (prompt and prompt.strip()):
            raise UserAvatarError("Describe how you want your avatar to look.")
        raw = self._generate(prompt, player_name=user_id, game_id=game_id)
        return self._store(user_id, raw, source='generated')

    def generate_from_photo(
        self,
        user_id: str,
        photo_bytes: bytes,
        *,
        prompt: Optional[str] = None,
        strength: float = 0.6,
        game_id: Optional[str] = None,
    ) -> str:
        """Img2img: stylize the user's uploaded photo into an avatar."""
        mime = detect_image_mimetype(photo_bytes)
        if not mime:
            raise UserAvatarError(_INVALID_FORMAT_MSG)
        seed_image_url = f"data:{mime};base64,{base64.b64encode(photo_bytes).decode('ascii')}"
        raw = self._generate(
            prompt or DEFAULT_PHOTO_PROMPT,
            player_name=user_id,
            game_id=game_id,
            seed_image_url=seed_image_url,
            strength=strength,
        )
        return self._store(user_id, raw, source='img2img')

    # --- queries / mutations ----------------------------------------------

    def get_avatar_url(self, user_id: str) -> Optional[str]:
        """Return the user's avatar URL (with a cache-busting version token).

        The ``?v=`` token is derived from ``updated_at`` so the URL changes on
        every re-upload — important because ``public_id`` is intentionally
        stable, so without it browsers/CDNs would serve a stale image for up to
        the ``Cache-Control`` max-age after a change.
        """
        descriptor = self._repo.get_avatar_descriptor(user_id)
        if not descriptor:
            return None
        token = re.sub(r'\D', '', str(descriptor.get('updated_at') or ''))[-14:] or '1'
        return f"{avatar_url_for(descriptor['public_id'])}?v={token}"

    def delete(self, user_id: str) -> bool:
        """Remove the user's avatar. Returns True if one existed."""
        return self._repo.delete(user_id)

    # --- internals ---------------------------------------------------------

    def _build_prompt(self, user_prompt: str) -> str:
        """Wrap the user's free text into a coherent poker-table portrait prompt.

        Same framing as the AI personality avatars (black background, chest-up,
        cel-shaded) so the human's portrait sits visually alongside them.
        """
        return (
            f"Black background, {user_prompt.strip()}, playing poker. "
            "Animated style with clean bold outlines, cel-shaded, realistic "
            "proportions, stylized aesthetic. Fully clothed, chest-up portrait, centered."
        )

    def _generate(
        self,
        prompt: str,
        *,
        player_name: str,
        game_id: Optional[str] = None,
        seed_image_url: Optional[str] = None,
        strength: float = 0.75,
    ) -> bytes:
        """Run image generation and download the result to raw bytes."""
        client = LLMClient(provider=get_image_provider(), model=get_image_model())
        response = client.generate_image(
            prompt=self._build_prompt(prompt),
            size=AVATAR_IMAGE_SIZE,
            call_type=CallType.IMAGE_GENERATION,
            game_id=game_id,
            player_name=player_name,
            prompt_template='user_avatar_generation',
            seed_image_url=seed_image_url,
            strength=strength,
            negative_prompt=NEGATIVE_PROMPT,
        )
        if response.is_error:
            raise UserAvatarError(
                response.error_message or response.error_code or "Image generation failed."
            )
        if not response.url:
            raise UserAvatarError("Image generation returned no image.")
        with urllib.request.urlopen(response.url, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as data:
            return data.read(MAX_AVATAR_BYTES + 1)

    def _store(self, user_id: str, image_bytes: bytes, source: str) -> str:
        """Validate -> process -> persist. Returns the avatar URL."""
        if not image_bytes:
            raise UserAvatarError("No image data was provided.")
        if detect_image_mimetype(image_bytes) is None:
            raise UserAvatarError(_INVALID_FORMAT_MSG)
        try:
            icon_bytes, full_bytes, _, _ = process_avatar_image(image_bytes)
        except Exception as e:
            raise UserAvatarError(f"Could not process that image: {e}") from e
        public_id = self._repo.upsert_avatar(
            user_id=user_id,
            icon_data=icon_bytes,
            full_data=full_bytes,
            content_type='image/png',
            source=source,
        )
        logger.info("Stored %s avatar for user %s (public_id=%s)", source, user_id, public_id)
        # Return the versioned URL (cache-busting token) so the caller's
        # response carries a fresh URL immediately.
        return self.get_avatar_url(user_id)
