"""Character image routes.

Serves avatar images from the database (primary) with filesystem fallback.
"""

import logging
import time
from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory, Response

from core.llm.config import POLLINATIONS_RATE_LIMIT_DELAY

from poker.character_images import (
    has_character_images,
    generate_character_images,
    load_avatar_image,
    load_full_avatar_image,
    regenerate_avatar_emotion,
    get_available_emotions,
    get_character_image_service,
    EMOTIONS,
)
from poker.persistence import GamePersistence
from ..config import get_db_path

logger = logging.getLogger(__name__)

image_bp = Blueprint('image', __name__)


def _detect_image_mimetype(image_data: bytes) -> str:
    """Detect image mimetype from binary data."""
    if image_data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    elif image_data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    elif image_data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    elif image_data[:4] == b'RIFF' and image_data[8:12] == b'WEBP':
        return 'image/webp'
    # Default to PNG if unknown
    return 'image/png'

GENERATED_IMAGES_DIR = Path(__file__).parent.parent.parent / 'generated_images'

# Initialize persistence for avatar lookups
def _get_persistence() -> GamePersistence:
    """Get persistence instance for avatar operations."""
    return GamePersistence(get_db_path())


@image_bp.route('/api/character-images', methods=['GET'])
def list_character_images():
    """List all generated character images."""
    try:
        if not GENERATED_IMAGES_DIR.exists():
            return jsonify({'images': [], 'icons': []})

        images = []
        icons = []

        for f in GENERATED_IMAGES_DIR.glob('*.png'):
            parts = f.stem.split('_')
            emotion = parts[-1] if len(parts) > 1 else 'unknown'
            character = ' '.join(parts[:-1]) if len(parts) > 1 else parts[0]
            images.append({
                'filename': f.name,
                'character': character,
                'emotion': emotion,
                'url': f'/api/character-images/{f.name}'
            })

        icons_dir = GENERATED_IMAGES_DIR / 'icons'
        if icons_dir.exists():
            for f in icons_dir.glob('*.png'):
                stem = f.stem.replace('_icon', '')
                parts = stem.split('_')
                style = parts[-1] if len(parts) > 1 else 'default'
                character = ' '.join(parts[:-1]) if len(parts) > 1 else parts[0]
                icons.append({
                    'filename': f.name,
                    'character': character,
                    'style': style,
                    'url': f'/api/character-images/icons/{f.name}'
                })

        return jsonify({'images': images, 'icons': icons})
    except Exception as e:
        logger.error(f"Error listing character images: {e}")
        return jsonify({'error': str(e)}), 500


@image_bp.route('/api/character-images/<filename>')
def serve_character_image(filename):
    """Serve a generated character image."""
    try:
        if not GENERATED_IMAGES_DIR.exists():
            return jsonify({'error': 'Images directory not found'}), 404
        return send_from_directory(GENERATED_IMAGES_DIR, filename)
    except Exception as e:
        logger.error(f"Error serving character image: {e}")
        return jsonify({'error': str(e)}), 404


@image_bp.route('/api/character-images/icons/<filename>')
def serve_character_icon(filename):
    """Serve a circular character icon."""
    try:
        icons_dir = GENERATED_IMAGES_DIR / 'icons'
        if not icons_dir.exists():
            return jsonify({'error': 'Icons directory not found'}), 404
        return send_from_directory(icons_dir, filename)
    except Exception as e:
        logger.error(f"Error serving character icon: {e}")
        return jsonify({'error': str(e)}), 404


@image_bp.route('/api/character-grid')
def get_character_grid():
    """Get grid images organized by character and emotion."""
    try:
        grid_dir = GENERATED_IMAGES_DIR / 'grid'
        icons_dir = grid_dir / 'icons'

        if not icons_dir.exists():
            return jsonify({'characters': [], 'emotions': []})

        data = {}
        emotions_set = set()

        for f in icons_dir.glob('*.png'):
            parts = f.stem.split('_')
            emotion = parts[-1]
            character = ' '.join(parts[:-1])

            emotions_set.add(emotion)
            if character not in data:
                data[character] = {}
            data[character][emotion] = f'/api/character-grid/icons/{f.name}'

        characters = sorted(data.keys())
        emotions = list(EMOTIONS)

        return jsonify({
            'characters': characters,
            'emotions': emotions,
            'grid': data
        })
    except Exception as e:
        logger.error(f"Error getting character grid: {e}")
        return jsonify({'error': str(e)}), 500


@image_bp.route('/api/character-grid/icons/<filename>')
def serve_grid_icon(filename):
    """Serve a grid icon from database or filesystem.

    Supports filename format: personality_name_emotion.png
    Example: bob_ross_confident.png
    """
    try:
        # Parse personality name and emotion from filename
        # Format: personality_name_emotion.png
        stem = filename.rsplit('.', 1)[0]  # Remove .png extension
        parts = stem.rsplit('_', 1)  # Split on last underscore

        if len(parts) == 2:
            personality_slug, emotion = parts
            # Convert slug back to name (e.g., bob_ross -> Bob Ross)
            personality_name = ' '.join(word.title() for word in personality_slug.split('_'))

            # Try to load from database first
            image_data = load_avatar_image(personality_name, emotion)
            if image_data:
                mimetype = _detect_image_mimetype(image_data)
                return Response(
                    image_data,
                    mimetype=mimetype,
                    headers={'Cache-Control': 'public, max-age=86400'}
                )

        # Fall back to filesystem
        icons_dir = GENERATED_IMAGES_DIR / 'grid' / 'icons'
        return send_from_directory(icons_dir, filename)
    except Exception as e:
        logger.error(f"Error serving grid icon {filename}: {e}")
        return jsonify({'error': str(e)}), 404


@image_bp.route('/api/avatar/<personality_name>/<emotion>')
def serve_avatar(personality_name: str, emotion: str):
    """Serve avatar image from database.

    This is the primary endpoint for database-stored avatars.

    Args:
        personality_name: Name of the personality (URL encoded)
        emotion: Emotion name (confident, happy, thinking, nervous, angry, shocked)

    Returns:
        PNG image or 404 if not found
    """
    try:
        # Normalize emotion
        emotion = emotion.lower()
        if emotion not in EMOTIONS:
            emotion = 'confident'

        # Load from database (primary) or filesystem (fallback)
        image_data = load_avatar_image(personality_name, emotion)

        if image_data:
            mimetype = _detect_image_mimetype(image_data)
            return Response(
                image_data,
                mimetype=mimetype,
                headers={'Cache-Control': 'public, max-age=86400'}
            )

        return jsonify({'error': f'Avatar not found for {personality_name} - {emotion}'}), 404

    except Exception as e:
        logger.error(f"Error serving avatar for {personality_name}/{emotion}: {e}")
        return jsonify({'error': str(e)}), 500


@image_bp.route('/api/avatar/<personality_name>/<emotion>/full')
def serve_full_avatar(personality_name: str, emotion: str):
    """Serve full uncropped avatar image from database.

    This endpoint serves the full-size image for CSS-based cropping on the frontend.

    Args:
        personality_name: Name of the personality (URL encoded)
        emotion: Emotion name (confident, happy, thinking, nervous, angry, shocked)

    Returns:
        Full PNG image or 404 if not found
    """
    try:
        # Normalize emotion
        emotion = emotion.lower()
        if emotion not in EMOTIONS:
            emotion = 'confident'

        # Load full image from database
        image_data = load_full_avatar_image(personality_name, emotion)

        if image_data:
            mimetype = _detect_image_mimetype(image_data)
            return Response(
                image_data,
                mimetype=mimetype,
                headers={'Cache-Control': 'public, max-age=86400'}
            )

        # Fall back to regular avatar if full not available
        image_data = load_avatar_image(personality_name, emotion)
        if image_data:
            mimetype = _detect_image_mimetype(image_data)
            return Response(
                image_data,
                mimetype=mimetype,
                headers={'Cache-Control': 'public, max-age=86400'}
            )

        return jsonify({'error': f'Full avatar not found for {personality_name} - {emotion}'}), 404

    except Exception as e:
        logger.error(f"Error serving full avatar for {personality_name}/{emotion}: {e}")
        return jsonify({'error': str(e)}), 500


@image_bp.route('/api/avatar/emotions')
def list_emotions():
    """List all available emotions for avatars.

    Returns the list of emotions dynamically from the EMOTIONS constant.
    """
    return jsonify({
        'emotions': get_available_emotions()
    })


@image_bp.route('/api/avatar/<personality_name>/regenerate', methods=['POST'])
def regenerate_avatar(personality_name: str):
    """Regenerate avatar images for specific emotions.

    Request body:
        {
            "emotions": ["confident", "happy"],  // Optional, defaults to all emotions
            "reference_image_id": "uuid",        // Optional, for img2img generation
            "strength": 0.75                     // Optional, 0.0-1.0 (lower = more like reference)
        }

    Returns:
        Results for each emotion regeneration
    """
    try:
        data = request.get_json() or {}
        emotions = data.get('emotions', get_available_emotions())
        reference_image_id = data.get('reference_image_id')
        # Strength: 0.0 = keep original exactly, 1.0 = fully transform
        # Lower values = more like the reference image
        strength = data.get('strength', 0.75)
        # Clamp to valid range
        strength = max(0.0, min(1.0, float(strength)))

        logger.info(f"Regenerate avatar request for {personality_name}: emotions={emotions}, reference_image_id={reference_image_id}, strength={strength}")

        # Validate emotions
        valid_emotions = get_available_emotions()
        invalid = [e for e in emotions if e not in valid_emotions]
        if invalid:
            return jsonify({
                'success': False,
                'error': f'Invalid emotions: {invalid}. Valid: {valid_emotions}'
            }), 400

        # Validate personality exists before expensive API calls
        from ..extensions import personality_generator
        if personality_generator and not personality_generator.get_personality(personality_name):
            return jsonify({
                'success': False,
                'error': f'Personality {personality_name} not found'
            }), 404

        # If reference_image_id provided, convert to data URL for img2img
        seed_image_url = None
        if reference_image_id:
            logger.info(f"Looking up reference image: {reference_image_id}")
            seed_image_url = _get_reference_image_data_url(reference_image_id)
            if seed_image_url:
                logger.info(f"Found reference image, data URL length: {len(seed_image_url)}")
            else:
                logger.warning(f"Reference image {reference_image_id} not found, proceeding without it")
        else:
            logger.info("No reference_image_id provided in request")

        results = []
        success_count = 0
        error_count = 0

        for i, emotion in enumerate(emotions):
            # Add delay between requests to respect rate limits (skip first request)
            if i > 0 and POLLINATIONS_RATE_LIMIT_DELAY > 0:
                logger.info(f"Rate limit delay: waiting {POLLINATIONS_RATE_LIMIT_DELAY}s before next image")
                time.sleep(POLLINATIONS_RATE_LIMIT_DELAY)

            result = regenerate_avatar_emotion(
                personality_name,
                emotion,
                seed_image_url=seed_image_url,
                strength=strength,
                reference_image_id=reference_image_id
            )
            results.append({
                'emotion': emotion,
                'success': result.get('success', False),
                'message': result.get('message') or result.get('error')
            })
            if result.get('success'):
                success_count += 1
            else:
                error_count += 1

        # Get the current avatar_description (may have been auto-generated during regeneration)
        from ..extensions import personality_generator
        avatar_description = personality_generator.get_avatar_description(personality_name) if personality_generator else None

        return jsonify({
            'success': error_count == 0,
            'personality': personality_name,
            'generated': success_count,
            'failed': error_count,
            'results': results,
            'avatar_description': avatar_description
        })

    except Exception as e:
        logger.error(f"Error regenerating avatars for {personality_name}: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


def _get_reference_image_data_url(reference_id: str) -> str | None:
    """Convert a reference_image_id to a base64 data URL for img2img.

    Args:
        reference_id: UUID of the reference image in the database

    Returns:
        Base64 data URL (e.g., 'data:image/png;base64,...') or None if not found
    """
    import base64
    import sqlite3

    try:
        with sqlite3.connect(get_db_path()) as conn:
            cursor = conn.execute(
                "SELECT image_data, content_type FROM reference_images WHERE id = ?",
                (reference_id,)
            )
            row = cursor.fetchone()
            if row:
                image_data, content_type = row
                content_type = content_type or 'image/png'
                b64_data = base64.b64encode(image_data).decode('utf-8')
                return f"data:{content_type};base64,{b64_data}"
    except Exception as e:
        logger.error(f"Error loading reference image {reference_id}: {e}")

    return None


@image_bp.route('/api/avatar-stats')
def get_avatar_stats():
    """Get statistics about avatar images in the database."""
    try:
        persistence = _get_persistence()
        stats = persistence.get_avatar_stats()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Error getting avatar stats: {e}")
        return jsonify({'error': str(e)}), 500


@image_bp.route('/api/generate-character-images/<personality_name>', methods=['POST'])
def generate_character_images_endpoint(personality_name):
    """Generate images for a personality on-demand."""
    try:
        data = request.get_json() or {}
        emotions = data.get('emotions')
        api_key = data.get('api_key')

        if has_character_images(personality_name):
            return jsonify({
                'status': 'exists',
                'message': f'Images already exist for {personality_name}',
                'personality': personality_name
            })

        logger.info(f"Starting on-demand image generation for {personality_name}")
        result = generate_character_images(personality_name, emotions=emotions, api_key=api_key)

        if result.get('success'):
            return jsonify({
                'status': 'generated',
                'message': f'Successfully generated images for {personality_name}',
                'personality': personality_name,
                'images': result.get('images', {}),
                'errors': result.get('errors', [])
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to generate images',
                'personality': personality_name,
                'errors': result.get('errors', ['Unknown error'])
            }), 500

    except Exception as e:
        logger.error(f"Error generating character images for {personality_name}: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e),
            'personality': personality_name
        }), 500


@image_bp.route('/api/character-images/status/<personality_name>')
def character_images_status(personality_name):
    """Check if images exist for a personality."""
    try:
        has_images = has_character_images(personality_name)
        return jsonify({
            'personality': personality_name,
            'has_images': has_images
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@image_bp.route('/character-images')
def character_images_preview():
    """Serve the character images preview page."""
    return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Character Image Preview</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e;
            color: #eee;
            min-height: 100vh;
            padding: 2rem;
        }
        h1 { text-align: center; margin-bottom: 2rem; color: #4ecca3; }
        .stats { text-align: center; margin-bottom: 1rem; color: #888; }
        .controls {
            display: flex; justify-content: center; gap: 1rem;
            margin-bottom: 2rem; flex-wrap: wrap;
        }
        button {
            background: #4ecca3; color: #1a1a2e; border: none;
            padding: 0.75rem 1.5rem; border-radius: 8px; cursor: pointer;
            font-size: 1rem; font-weight: 600; transition: all 0.2s;
        }
        button:hover { background: #3db892; transform: translateY(-2px); }
        button.active { background: #e94560; }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
            gap: 1.5rem; max-width: 1400px; margin: 0 auto;
        }
        .grid.icons {
            grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
            gap: 1rem;
        }
        .card {
            background: #16213e; border-radius: 12px; overflow: hidden;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3); transition: transform 0.2s;
        }
        .card:hover { transform: translateY(-4px); }
        .card img {
            width: 100%; aspect-ratio: 1; object-fit: cover; display: block;
        }
        .card.icon { background: transparent; box-shadow: none; text-align: center; }
        .card.icon img { border-radius: 50%; }
        .card-info { padding: 1rem; }
        .card.icon .card-info { padding: 0.5rem; }
        .card-info h3 { color: #4ecca3; margin-bottom: 0.25rem; text-transform: capitalize; font-size: 0.9rem; }
        .card-info p { color: #888; font-size: 0.75rem; text-transform: capitalize; }
        .empty { text-align: center; padding: 4rem; color: #666; }
        .loading { text-align: center; padding: 2rem; color: #4ecca3; }
    </style>
</head>
<body>
    <h1>Character Image Preview</h1>
    <div class="stats" id="stats"></div>
    <div class="controls">
        <button id="btnFull" onclick="showFull()">Full Images</button>
        <button id="btnIcons" onclick="showIcons()">Circular Icons</button>
        <button onclick="loadImages()">Refresh</button>
    </div>
    <div class="loading" id="loading">Loading images...</div>
    <div class="grid" id="grid"></div>
    <div class="empty" id="empty" style="display:none">
        No images generated yet. Run the generation script to create some!
    </div>
    <script>
        const grid = document.getElementById('grid');
        const empty = document.getElementById('empty');
        const stats = document.getElementById('stats');
        const loading = document.getElementById('loading');
        const btnFull = document.getElementById('btnFull');
        const btnIcons = document.getElementById('btnIcons');

        let currentData = { images: [], icons: [] };
        let showingIcons = true;

        function showFull() {
            showingIcons = false;
            btnFull.classList.add('active');
            btnIcons.classList.remove('active');
            renderGrid();
        }

        function showIcons() {
            showingIcons = true;
            btnIcons.classList.add('active');
            btnFull.classList.remove('active');
            renderGrid();
        }

        function renderGrid() {
            const items = showingIcons ? currentData.icons : currentData.images;

            if (!items || items.length === 0) {
                grid.innerHTML = '';
                empty.style.display = 'block';
                stats.textContent = '';
                return;
            }

            empty.style.display = 'none';
            stats.textContent = items.length + (showingIcons ? ' icon(s)' : ' image(s)');
            grid.className = showingIcons ? 'grid icons' : 'grid';

            if (showingIcons) {
                grid.innerHTML = items.map(img => `
                    <div class="card icon">
                        <img src="${img.url}" alt="${img.character} - ${img.style}">
                        <div class="card-info">
                            <h3>${img.character}</h3>
                            <p>${img.style}</p>
                        </div>
                    </div>
                `).join('');
            } else {
                grid.innerHTML = items.map(img => `
                    <div class="card">
                        <img src="${img.url}" alt="${img.character} - ${img.emotion}">
                        <div class="card-info">
                            <h3>${img.character}</h3>
                            <p>${img.emotion}</p>
                        </div>
                    </div>
                `).join('');
            }
        }

        async function loadImages() {
            loading.style.display = 'block';
            grid.innerHTML = '';
            try {
                const res = await fetch('/api/character-images');
                currentData = await res.json();
                loading.style.display = 'none';
                renderGrid();
            } catch (err) {
                loading.style.display = 'none';
                empty.style.display = 'block';
                empty.textContent = 'Error loading images: ' + err.message;
            }
        }

        btnIcons.classList.add('active');
        loadImages();
    </script>
</body>
</html>'''


@image_bp.route('/character-grid')
def character_grid_preview():
    """Serve the character grid preview page."""
    return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Character Emotion Grid</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e;
            color: #eee;
            min-height: 100vh;
            padding: 2rem;
        }
        h1 { text-align: center; margin-bottom: 0.5rem; color: #4ecca3; }
        .subtitle { text-align: center; color: #888; margin-bottom: 2rem; }
        .controls {
            display: flex; justify-content: center; gap: 1rem;
            margin-bottom: 2rem;
        }
        button {
            background: #4ecca3; color: #1a1a2e; border: none;
            padding: 0.75rem 1.5rem; border-radius: 8px; cursor: pointer;
            font-size: 1rem; font-weight: 600;
        }
        button:hover { background: #3db892; }
        .grid-container {
            max-width: 900px;
            margin: 0 auto;
            background: #16213e;
            border-radius: 16px;
            padding: 1.5rem;
            overflow-x: auto;
        }
        table {
            width: 100%;
            border-collapse: collapse;
        }
        th {
            color: #4ecca3;
            font-size: 1rem;
            padding: 1rem;
            text-transform: capitalize;
        }
        th.emotion { font-weight: 600; }
        th.character {
            text-align: left;
            font-weight: 600;
            padding-right: 2rem;
        }
        td {
            text-align: center;
            padding: 0.75rem;
        }
        td img {
            width: 120px;
            height: 120px;
            border-radius: 50%;
            transition: transform 0.2s;
        }
        td img:hover {
            transform: scale(1.1);
        }
        .loading { text-align: center; padding: 3rem; color: #4ecca3; }
        .empty { text-align: center; padding: 3rem; color: #666; }
    </style>
</head>
<body>
    <h1>Character Emotion Grid</h1>
    <p class="subtitle">4 Characters x 6 Emotions</p>
    <div class="controls">
        <button onclick="loadGrid()">Refresh</button>
        <button onclick="location.href='/character-images'">All Images</button>
    </div>
    <div class="grid-container">
        <div class="loading" id="loading">Loading grid...</div>
        <table id="grid" style="display:none"></table>
        <div class="empty" id="empty" style="display:none">No grid images found.</div>
    </div>
    <script>
        const grid = document.getElementById('grid');
        const loading = document.getElementById('loading');
        const empty = document.getElementById('empty');

        async function loadGrid() {
            loading.style.display = 'block';
            grid.style.display = 'none';
            empty.style.display = 'none';

            try {
                const res = await fetch('/api/character-grid');
                const data = await res.json();

                loading.style.display = 'none';

                if (!data.characters || data.characters.length === 0) {
                    empty.style.display = 'block';
                    return;
                }

                let html = '<thead><tr><th></th>';
                data.emotions.forEach(e => {
                    html += `<th class="emotion">${e}</th>`;
                });
                html += '</tr></thead><tbody>';

                data.characters.forEach(char => {
                    html += `<tr><th class="character">${char}</th>`;
                    data.emotions.forEach(emotion => {
                        const url = data.grid[char]?.[emotion] || '';
                        if (url) {
                            html += `<td><img src="${url}" alt="${char} ${emotion}"></td>`;
                        } else {
                            html += `<td>-</td>`;
                        }
                    });
                    html += '</tr>';
                });
                html += '</tbody>';

                grid.innerHTML = html;
                grid.style.display = 'table';
            } catch (err) {
                loading.style.display = 'none';
                empty.style.display = 'block';
                empty.textContent = 'Error: ' + err.message;
            }
        }

        loadGrid();
    </script>
</body>
</html>'''
