"""User profile routes — the human player's avatar and AI-visible bio.

Two surfaces:

* Authenticated, current-user-scoped CRUD under ``/api/profile`` — set/clear a
  bio, upload an image, paste an image URL, generate an avatar from text, or
  stylize an uploaded photo (img2img). Works for guests and Google users alike
  (avatars are keyed by the auth user id, which guests have via their signed
  cookie).
* A public, identity-free serve endpoint ``/api/user-avatar/<public_id>`` (and
  ``/full``) that returns the stored PNG. The ``public_id`` is an opaque UUID,
  so embedding it in game state never leaks a user id to other players.
"""

import logging
from functools import wraps

from flask import Blueprint, Response, g, jsonify, request

from core.moderation import moderate_text

from .. import config, extensions
from ..extensions import limiter

logger = logging.getLogger(__name__)

profile_bp = Blueprint('profile', __name__)

# Reject obviously oversized uploads before reading the whole body into memory.
MAX_AVATAR_UPLOAD_BYTES = 8 * 1024 * 1024  # 8 MB


def _auth_required(f):
    """Require any authenticated user (guest or Google); stash them on `g`.

    Handlers read ``g.profile_user``. Guests count as authenticated (they have
    a signed-cookie identity), so they can set avatars/bios too.
    """

    @wraps(f)
    def wrapped(*args, **kwargs):
        user = extensions.auth_manager.get_current_user() if extensions.auth_manager else None
        if not user:
            return jsonify({'success': False, 'error': 'Not authenticated'}), 401
        g.profile_user = user
        return f(*args, **kwargs)

    return wrapped


def _too_large() -> bool:
    """True if the request declares a body larger than the upload cap."""
    return bool(request.content_length and request.content_length > MAX_AVATAR_UPLOAD_BYTES)


def _moderation_error(text: str):
    """Return a (response, 400) tuple if `text` is flagged, else None (PRH-27).

    The bio is AI-visible and shown to the table; the avatar prompt drives image
    generation. Both are user free text, so screen them. Fail-open: a moderation
    outage doesn't block the save (see core.moderation).
    """
    if moderate_text(text).flagged:
        return jsonify(
            {
                'success': False,
                'error': 'That text was flagged by our content filter. Please rephrase.',
                'code': 'MODERATION_REJECTED',
            }
        ), 400
    return None


@profile_bp.route('/api/profile', methods=['GET'])
@_auth_required
def get_profile():
    """Return the current user's avatar URL and bio."""

    user_id = g.profile_user['id']
    return jsonify(
        {
            'success': True,
            'avatar_url': extensions.user_avatar_service.get_avatar_url(user_id),
            'bio': extensions.user_prefs_repo.get_bio(user_id),
        }
    )


@profile_bp.route('/api/profile/bio', methods=['PUT'])
@_auth_required
def set_bio():
    """Set (or clear) the current user's AI-visible self-description."""

    data = request.get_json(silent=True) or {}
    bio = data.get('bio', '')
    if not isinstance(bio, str):
        return jsonify({'success': False, 'error': 'bio must be a string'}), 400

    flagged = _moderation_error(bio)
    if flagged:
        return flagged

    stored = extensions.user_prefs_repo.set_bio(g.profile_user['id'], bio)
    return jsonify({'success': True, 'bio': stored})


@profile_bp.route('/api/profile/avatar/upload', methods=['POST'])
@limiter.limit(config.RATE_LIMIT_REGENERATE_AVATAR)
@_auth_required
def upload_avatar():
    """Upload an avatar image file, or fetch one from a pasted URL.

    Rate-limited because the URL branch fetches a user-supplied URL
    server-side (an outbound request per call).
    """
    from poker.user_avatar_service import UserAvatarError

    if _too_large():
        return jsonify({'success': False, 'error': 'Image is too large (max 8 MB)'}), 413

    user_id = g.profile_user['id']
    try:
        if 'file' in request.files and request.files['file'].filename:
            image_bytes = request.files['file'].read()
            avatar_url = extensions.user_avatar_service.store_from_bytes(user_id, image_bytes)
        else:
            url = (request.get_json(silent=True) or {}).get('url')
            if not url:
                return jsonify({'success': False, 'error': 'No image file or URL provided'}), 400
            avatar_url = extensions.user_avatar_service.store_from_url(user_id, url)
        return jsonify({'success': True, 'avatar_url': avatar_url})
    except UserAvatarError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Avatar upload failed for {user_id}: {e}")
        return jsonify({'success': False, 'error': 'Could not save avatar'}), 500


@profile_bp.route('/api/profile/avatar/generate', methods=['POST'])
@limiter.limit(config.RATE_LIMIT_GENERATE_IMAGES)
@_auth_required
def generate_avatar():
    """Generate an avatar from a text description."""
    from poker.user_avatar_service import UserAvatarError

    user_id = g.profile_user['id']
    prompt = (request.get_json(silent=True) or {}).get('prompt', '')
    flagged = _moderation_error(prompt)
    if flagged:
        return flagged
    try:
        avatar_url = extensions.user_avatar_service.generate_from_prompt(user_id, prompt)
        return jsonify({'success': True, 'avatar_url': avatar_url})
    except UserAvatarError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Avatar generation failed for {user_id}: {e}")
        return jsonify({'success': False, 'error': 'Image generation failed'}), 500


@profile_bp.route('/api/profile/avatar/generate-photo', methods=['POST'])
@limiter.limit(config.RATE_LIMIT_GENERATE_IMAGES)
@_auth_required
def generate_avatar_from_photo():
    """Generate an avatar by stylizing an uploaded photo (img2img)."""
    from poker.user_avatar_service import UserAvatarError

    if _too_large():
        return jsonify({'success': False, 'error': 'Image is too large (max 8 MB)'}), 413
    if 'file' not in request.files or not request.files['file'].filename:
        return jsonify({'success': False, 'error': 'No photo provided'}), 400

    user_id = g.profile_user['id']
    photo_bytes = request.files['file'].read()
    prompt = request.form.get('prompt') or None
    if prompt:
        flagged = _moderation_error(prompt)
        if flagged:
            return flagged
    try:
        strength = float(request.form.get('strength', 0.6))
    except (TypeError, ValueError):
        strength = 0.6

    try:
        avatar_url = extensions.user_avatar_service.generate_from_photo(
            user_id, photo_bytes, prompt=prompt, strength=strength
        )
        return jsonify({'success': True, 'avatar_url': avatar_url})
    except UserAvatarError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Avatar photo generation failed for {user_id}: {e}")
        return jsonify({'success': False, 'error': 'Image generation failed'}), 500


@profile_bp.route('/api/profile/avatar', methods=['DELETE'])
@_auth_required
def delete_avatar():
    """Remove the current user's custom avatar."""

    extensions.user_avatar_service.delete(g.profile_user['id'])
    return jsonify({'success': True})


@profile_bp.route('/api/user-avatar/<public_id>', methods=['GET'])
@limiter.exempt
def serve_user_avatar(public_id: str):
    """Public serve of a user's circular avatar icon by opaque id."""
    return _serve(public_id, full=False)


@profile_bp.route('/api/user-avatar/<public_id>/full', methods=['GET'])
@limiter.exempt
def serve_user_avatar_full(public_id: str):
    """Public serve of a user's square ("full") avatar by opaque id."""
    return _serve(public_id, full=True)


def _serve(public_id: str, *, full: bool):
    record = extensions.user_avatar_repo.get_image_by_public_id(public_id, full=full)
    if not record:
        return jsonify({'error': 'Avatar not found'}), 404
    return Response(
        record['image_data'],
        mimetype=record['content_type'] or 'image/png',
        headers={'Cache-Control': 'public, max-age=86400'},
    )
