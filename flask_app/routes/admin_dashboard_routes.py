"""Admin dashboard routes for LLM usage analysis, model management, and debug tools."""

import logging
import os
import re
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request

from ..services import game_state_service
from ..extensions import llm_repo, personality_repo, settings_repo, prompt_capture_repo, game_repo
from core.llm import UsageTracker
from poker.authorization import require_permission

logger = logging.getLogger(__name__)

admin_dashboard_bp = Blueprint('admin_dashboard', __name__, url_prefix='/admin')


def _get_date_modifier(range_param: str) -> str:
    """Convert range parameter to SQLite datetime modifier for parameterized queries.

    Returns a modifier string to be used with datetime('now', ?).
    This approach prevents SQL injection by using parameterized queries.
    """
    modifiers = {
        '24h': '-1 day',
        '7d': '-7 days',
        '30d': '-30 days',
        'all': '-100 years',  # Effectively all time
    }
    return modifiers.get(range_param, '-7 days')


# Decorator alias for admin-only routes using RBAC
_admin_required = require_permission('can_access_admin_tools')

# Keep old decorator name as alias for backwards compatibility
_dev_only = _admin_required


# =============================================================================
# Dashboard Root - Redirect to React Admin
# =============================================================================

@admin_dashboard_bp.route('/')
@_dev_only
def dashboard():
    """Redirect to React admin dashboard."""
    return jsonify({
        'message': 'Admin dashboard has moved to React UI',
        'redirect': '/?view=admin'
    })


# =============================================================================
# API Endpoints (for AJAX updates)
# =============================================================================

@admin_dashboard_bp.route('/api/summary')
@_dev_only
def api_summary():
    """JSON endpoint for dashboard summary data."""
    range_param = request.args.get('range', '7d')
    date_modifier = _get_date_modifier(range_param)

    try:
        summary = llm_repo.get_usage_summary(date_modifier)
        return jsonify({'success': True, 'summary': summary})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# Cost Analysis - Redirect to React Admin
# =============================================================================

@admin_dashboard_bp.route('/costs')
@_dev_only
def costs():
    """Redirect to React admin dashboard."""
    return jsonify({
        'message': 'Cost analysis has moved to React UI',
        'redirect': '/?view=admin'
    })


# =============================================================================
# Performance Metrics - Redirect to React Admin
# =============================================================================

@admin_dashboard_bp.route('/performance')
@_dev_only
def performance():
    """Redirect to React admin dashboard."""
    return jsonify({
        'message': 'Performance metrics has moved to React UI',
        'redirect': '/?view=admin'
    })


# =============================================================================
# Prompt Viewer - Redirect to React Admin
# =============================================================================

@admin_dashboard_bp.route('/prompts')
@_dev_only
def prompts():
    """Redirect to React admin dashboard."""
    return jsonify({
        'message': 'Prompt viewer has moved to React UI',
        'redirect': '/?view=admin'
    })


# =============================================================================
# Models Manager - Redirect to React Admin
# =============================================================================

@admin_dashboard_bp.route('/models')
@_dev_only
def models():
    """Redirect to React admin dashboard."""
    return jsonify({
        'message': 'Model manager has moved to React UI',
        'redirect': '/?view=admin'
    })


@admin_dashboard_bp.route('/api/models/<int:model_id>/toggle', methods=['POST'])
@_dev_only
def api_toggle_model(model_id):
    """Toggle a model's enabled or user_enabled status.

    Request body:
        field: 'enabled' or 'user_enabled' (default: 'enabled')
        enabled: boolean - the new value

    Cascade logic:
        - If field=user_enabled and enabled=true: also set enabled=1 (System must be ON for User to be ON)
        - If field=enabled and enabled=false: also set user_enabled=0 (User must be OFF if System is OFF)
    """
    data = request.get_json()
    field = data.get('field', 'enabled')
    enabled = data.get('enabled', False)

    # Validate field parameter
    if field not in ('enabled', 'user_enabled'):
        return jsonify({'success': False, 'error': 'Invalid field. Must be "enabled" or "user_enabled"'}), 400

    try:
        result = llm_repo.toggle_model(model_id, field, enabled)
        return jsonify({'success': True, **result})
    except ValueError as e:
        status = 404 if 'not found' in str(e).lower() else 400
        return jsonify({'success': False, 'error': str(e)}), status
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/models', methods=['GET'])
@_dev_only
def api_list_models():
    """List all models with their enabled status."""
    try:
        models = llm_repo.list_all_models_full()
        return jsonify({'success': True, 'models': models})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# Pricing Manager - Redirect to React Admin
# Note: The JSON API is at /admin/pricing with GET method (see list_pricing below)
# =============================================================================

# =============================================================================
# Prompt Playground API
# =============================================================================

@admin_dashboard_bp.route('/api/playground/captures')
@_dev_only
def api_playground_captures():
    """List captured prompts for the playground.

    Query params:
        call_type: Filter by call type (e.g., 'commentary', 'personality_generation')
        provider: Filter by LLM provider
        limit: Max results (default 50)
        offset: Pagination offset (default 0)
        date_from: Filter by start date (ISO format)
        date_to: Filter by end date (ISO format)
    """
    try:
        result = prompt_capture_repo.list_playground_captures(
            call_type=request.args.get('call_type'),
            provider=request.args.get('provider'),
            limit=int(request.args.get('limit', 50)),
            offset=int(request.args.get('offset', 0)),
            date_from=request.args.get('date_from'),
            date_to=request.args.get('date_to'),
        )

        stats = prompt_capture_repo.get_playground_capture_stats()

        return jsonify({
            'success': True,
            'captures': result['captures'],
            'total': result['total'],
            'stats': stats,
        })

    except Exception as e:
        logger.error(f"Playground captures error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/playground/captures/<int:capture_id>')
@_dev_only
def api_playground_capture(capture_id):
    """Get a single playground capture by ID."""
    try:
        capture = prompt_capture_repo.get_prompt_capture(capture_id)

        if not capture:
            return jsonify({'success': False, 'error': 'Capture not found'}), 404

        return jsonify({
            'success': True,
            'capture': capture,
        })

    except Exception as e:
        logger.error(f"Playground capture error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/playground/captures/<int:capture_id>/replay', methods=['POST'])
@_dev_only
def api_playground_replay(capture_id):
    """Replay a captured prompt with optional modifications.

    Request body:
        system_prompt: Modified system prompt (optional)
        user_message: Modified user message (optional)
        conversation_history: Modified history (optional)
        use_history: Whether to include history (default: True)
        provider: LLM provider to use (optional)
        model: Model to use (optional)
        reasoning_effort: Reasoning effort (optional)
    """
    from core.llm import LLMClient, CallType

    try:
        capture = prompt_capture_repo.get_prompt_capture(capture_id)
        if not capture:
            return jsonify({'success': False, 'error': 'Capture not found'}), 404

        data = request.get_json() or {}

        # Use modified prompts or originals
        system_prompt = data.get('system_prompt', capture.get('system_prompt', ''))
        user_message = data.get('user_message', capture.get('user_message', ''))
        provider = data.get('provider', capture.get('provider', 'openai')).lower()
        model = data.get('model', capture.get('model'))
        reasoning_effort = data.get('reasoning_effort', capture.get('reasoning_effort', 'minimal'))

        # Handle conversation history
        use_history = data.get('use_history', True)
        conversation_history = data.get('conversation_history', capture.get('conversation_history', []))

        # Create LLM client
        client = LLMClient(provider=provider, model=model, reasoning_effort=reasoning_effort)

        # Build messages array
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        if use_history and conversation_history:
            for msg in conversation_history:
                messages.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", "")
                })

        messages.append({"role": "user", "content": user_message})

        # Check if JSON format requested
        combined_text = (system_prompt or '') + (user_message or '')
        use_json_format = 'json' in combined_text.lower()

        response = client.complete(
            messages=messages,
            json_format=use_json_format,
            call_type=CallType.DEBUG_REPLAY,
        )

        return jsonify({
            'success': True,
            'original_response': capture.get('ai_response', ''),
            'new_response': response.content,
            'provider_used': response.provider,
            'model_used': response.model,
            'reasoning_effort_used': reasoning_effort,
            'input_tokens': response.input_tokens,
            'output_tokens': response.output_tokens,
            'latency_ms': response.latency_ms,
            'messages_count': len(messages),
            'used_history': use_history and bool(conversation_history),
        })

    except Exception as e:
        logger.error(f"Playground replay error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/playground/stats')
@_dev_only
def api_playground_stats():
    """Get aggregate statistics for playground captures."""
    try:
        stats = prompt_capture_repo.get_playground_capture_stats()
        return jsonify({'success': True, 'stats': stats})

    except Exception as e:
        logger.error(f"Playground stats error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/playground/cleanup', methods=['POST'])
@_dev_only
def api_playground_cleanup():
    """Delete old playground captures.

    Request body:
        retention_days: Delete captures older than this many days (default: from config)
    """
    from core.llm.capture_config import get_retention_days

    try:
        data = request.get_json() or {}
        retention_days = data.get('retention_days', get_retention_days())

        if retention_days <= 0:
            return jsonify({
                'success': True,
                'message': 'Unlimited retention configured, no cleanup performed',
                'deleted': 0,
            })

        deleted = prompt_capture_repo.cleanup_old_captures(retention_days)

        return jsonify({
            'success': True,
            'message': f'Deleted {deleted} captures older than {retention_days} days',
            'deleted': deleted,
        })

    except Exception as e:
        logger.error(f"Playground cleanup error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# Image Playground API (capture viewing, replay, reference images, avatars)
# =============================================================================

@admin_dashboard_bp.route('/api/reference-images', methods=['POST'])
@_dev_only
def api_upload_reference_image():
    """Upload a reference image for image-to-image generation.

    Accepts: multipart/form-data with 'file' or JSON with 'url'
    Returns: { reference_id, width, height }
    """
    import uuid
    import requests as http_requests

    try:
        image_data = None
        content_type = 'image/png'
        source = 'upload'
        original_url = None
        width = None
        height = None

        # Check for file upload
        if 'file' in request.files:
            file = request.files['file']
            if file.filename:
                image_data = file.read()
                content_type = file.content_type or 'image/png'
                source = 'upload'
        else:
            # Check for URL in JSON body
            data = request.get_json() or {}
            url = data.get('url')
            if url:
                # Download the image from URL
                response = http_requests.get(url, timeout=30)
                response.raise_for_status()
                image_data = response.content
                content_type = response.headers.get('Content-Type', 'image/png')
                source = 'url'
                original_url = url

        if not image_data:
            return jsonify({'success': False, 'error': 'No image provided'}), 400

        # Validate image magic bytes
        IMAGE_SIGNATURES = {
            b'\x89PNG\r\n\x1a\n': 'image/png',
            b'\xff\xd8\xff': 'image/jpeg',
            b'GIF87a': 'image/gif',
            b'GIF89a': 'image/gif',
        }
        detected_type = None
        for signature, mime_type in IMAGE_SIGNATURES.items():
            if image_data[:len(signature)] == signature:
                detected_type = mime_type
                break
        # WebP uses RIFF container — verify both RIFF header and WEBP marker
        if detected_type is None and len(image_data) >= 12:
            if image_data[:4] == b'RIFF' and image_data[8:12] == b'WEBP':
                detected_type = 'image/webp'
        if detected_type is None:
            return jsonify({'success': False, 'error': 'Invalid image format. Supported: PNG, JPEG, GIF, WebP'}), 400
        content_type = detected_type

        # Try to get image dimensions
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(image_data))
            width, height = img.size
        except ImportError:
            # PIL not installed; skip dimension extraction but allow upload to proceed
            pass
        except Exception as e:
            logger.debug(f"Could not get image dimensions: {e}")

        # Generate unique ID
        reference_id = str(uuid.uuid4())

        # Store in database
        personality_repo.save_reference_image(
            reference_id, image_data, width, height, content_type, source, original_url
        )

        return jsonify({
            'success': True,
            'reference_id': reference_id,
            'width': width,
            'height': height,
        })

    except Exception as e:
        logger.error(f"Reference image upload error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/reference-images/<reference_id>')
@_dev_only
def api_get_reference_image(reference_id: str):
    """Serve a reference image by ID.

    Returns the raw image data with appropriate content-type header.
    """
    from flask import Response

    try:
        result = personality_repo.get_reference_image(reference_id)
        if not result:
            return jsonify({'success': False, 'error': 'Reference image not found'}), 404
        return Response(
            result['image_data'],
            mimetype=result['content_type'] or 'image/png',
            headers={'Cache-Control': 'max-age=31536000'}
        )
    except Exception as e:
        logger.error(f"Reference image fetch error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/playground/captures/<int:capture_id>/replay-image', methods=['POST'])
@_dev_only
def api_playground_replay_image(capture_id: int):
    """Replay an image capture with modifications.

    Request body:
        prompt: Modified prompt
        provider: Image provider to use
        model: Model to use
        size: Image size (e.g., "512x512")
        reference_image_id: Optional reference image

    Returns: {
        original_image_url,
        new_image_url,  # base64 data URL
        provider_used,
        model_used,
        latency_ms,
        estimated_cost
    }
    """
    from core.llm import LLMClient, CallType
    import base64

    try:
        capture = prompt_capture_repo.get_prompt_capture(capture_id)
        if not capture:
            return jsonify({'success': False, 'error': 'Capture not found'}), 404

        # Verify it's an image capture
        if not capture.get('is_image_capture'):
            return jsonify({'success': False, 'error': 'Not an image capture'}), 400

        data = request.get_json() or {}

        # Use modified values or originals
        prompt = data.get('prompt', capture.get('image_prompt', ''))
        provider = data.get('provider', capture.get('provider', 'pollinations')).lower()
        model = data.get('model', capture.get('model'))
        size = data.get('size', capture.get('image_size', '512x512'))
        reference_image_id = data.get('reference_image_id')

        # Check if model supports img2img when reference image is provided
        seed_image_url = None
        if reference_image_id:
            supports_img2img = llm_repo.check_model_supports_img2img(provider, model)
            if not supports_img2img:
                return jsonify({
                    'success': False,
                    'error': f'Model "{model}" does not support image-to-image generation. Please select a model that supports img2img, or remove the reference image.',
                }), 400

            ref_result = personality_repo.get_reference_image(reference_image_id)
            if ref_result and ref_result['image_data']:
                content_type = ref_result['content_type'] or 'image/png'
                b64_data = base64.b64encode(ref_result['image_data']).decode('utf-8')
                seed_image_url = f"data:{content_type};base64,{b64_data}"
                logger.info(f"Using reference image for img2img: {reference_image_id} ({len(b64_data)} bytes base64)")
            else:
                logger.warning(f"Reference image not found: {reference_image_id}")

        # Create LLM client for the provider
        client = LLMClient(provider=provider, model=model)

        # Generate the new image
        response = client.generate_image(
            prompt=prompt,
            size=size,
            call_type=CallType.DEBUG_REPLAY,
            seed_image_url=seed_image_url,
            reference_image_id=reference_image_id,
        )

        if response.is_error:
            return jsonify({
                'success': False,
                'error': response.error_message or 'Image generation failed',
            }), 500

        # Download the new image and convert to base64 data URL
        new_image_url = None
        if response.url:
            try:
                import requests as http_requests
                img_response = http_requests.get(response.url, timeout=30)
                img_response.raise_for_status()
                img_data = img_response.content
                content_type = img_response.headers.get('Content-Type', 'image/png')
                b64_data = base64.b64encode(img_data).decode('utf-8')
                new_image_url = f"data:{content_type};base64,{b64_data}"
            except Exception as e:
                logger.warning(f"Failed to download new image: {e}")
                new_image_url = response.url  # Fall back to URL

        # Get original image as base64 if available
        original_image_url = None
        if capture.get('image_data'):
            content_type = 'image/png'
            b64_data = base64.b64encode(capture['image_data']).decode('utf-8')
            original_image_url = f"data:{content_type};base64,{b64_data}"
        elif capture.get('image_url'):
            original_image_url = capture['image_url']

        return jsonify({
            'success': True,
            'original_image_url': original_image_url,
            'new_image_url': new_image_url,
            'provider_used': response.provider,
            'model_used': response.model,
            'latency_ms': int(response.latency_ms) if response.latency_ms else None,
            'size_used': size,
        })

    except Exception as e:
        logger.error(f"Image replay error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/playground/captures/<int:capture_id>/assign-avatar', methods=['POST'])
@_dev_only
def api_assign_avatar_from_capture(capture_id: int):
    """Assign a captured/replayed image as a personality avatar.

    Request body:
        personality_name: Target personality
        emotion: Target emotion
        use_replayed: True to use replayed image, False for original
        replayed_image_data: Base64 image data (if use_replayed)
    """
    import base64

    try:
        capture = prompt_capture_repo.get_prompt_capture(capture_id)
        if not capture:
            return jsonify({'success': False, 'error': 'Capture not found'}), 404

        data = request.get_json() or {}
        personality_name = data.get('personality_name')
        emotion = data.get('emotion', 'neutral')
        use_replayed = data.get('use_replayed', False)
        replayed_image_data = data.get('replayed_image_data')

        if not personality_name:
            return jsonify({'success': False, 'error': 'personality_name is required'}), 400

        # Get the image data
        if use_replayed and replayed_image_data:
            # Extract base64 data from data URL if needed
            if replayed_image_data.startswith('data:'):
                # Parse data URL: data:image/png;base64,xxxxx
                parts = replayed_image_data.split(',', 1)
                if len(parts) == 2:
                    image_data = base64.b64decode(parts[1])
                else:
                    return jsonify({'success': False, 'error': 'Invalid image data format'}), 400
            else:
                image_data = base64.b64decode(replayed_image_data)
        elif capture.get('image_data'):
            image_data = capture['image_data']
        else:
            return jsonify({'success': False, 'error': 'No image data available'}), 400

        # Save to avatar_images table
        personality_repo.assign_avatar(personality_name, emotion, image_data)

        return jsonify({
            'success': True,
            'message': f'Avatar assigned for {personality_name} ({emotion})',
            'personality_name': personality_name,
            'emotion': emotion,
        })

    except Exception as e:
        logger.error(f"Avatar assignment error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/image-providers')
@_dev_only
def api_get_image_providers():
    """Get list of enabled image providers with their models and size presets.

    Returns providers that support image generation (supports_image_gen=1).
    """
    try:
        image_models = llm_repo.get_enabled_image_models()

        providers = {}
        for row in image_models:
            provider = row['provider']
            if provider not in providers:
                providers[provider] = {
                    'id': provider,
                    'name': provider.title(),
                    'models': [],
                    'size_presets': _get_size_presets(provider),
                }
            providers[provider]['models'].append({
                'id': row['model'],
                'name': row['display_name'] or row['model'],
                'supports_img2img': bool(row['supports_img2img']),
            })

        return jsonify({
            'success': True,
            'providers': list(providers.values()),
        })
    except Exception as e:
        logger.error(f"Image providers error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def _get_size_presets(provider: str) -> list:
    """Get recommended size presets for a provider."""
    # Common presets that work across providers
    common_presets = [
        {'label': '1:1 Small (512x512)', 'value': '512x512', 'cost': '$'},
        {'label': '1:1 Medium (1024x1024)', 'value': '1024x1024', 'cost': '$$'},
    ]

    provider_presets = {
        'openai': [
            {'label': '1:1 (1024x1024)', 'value': '1024x1024', 'cost': '$$'},
            {'label': 'Portrait (1024x1792)', 'value': '1024x1792', 'cost': '$$$'},
            {'label': 'Landscape (1792x1024)', 'value': '1792x1024', 'cost': '$$$'},
        ],
        'pollinations': common_presets + [
            {'label': '16:9 (1024x576)', 'value': '1024x576', 'cost': '$$'},
            {'label': '9:16 (576x1024)', 'value': '576x1024', 'cost': '$$'},
        ],
        'runware': common_presets + [
            {'label': '16:9 (1024x576)', 'value': '1024x576', 'cost': '$$'},
            {'label': '9:16 (576x1024)', 'value': '576x1024', 'cost': '$$'},
        ],
        'xai': [
            {'label': '1:1 (1024x1024)', 'value': '1024x1024', 'cost': '$$'},
        ],
    }

    return provider_presets.get(provider, common_presets)


# =============================================================================
# Prompt Template Management
# =============================================================================

@admin_dashboard_bp.route('/api/prompts/templates')
@_dev_only
def api_list_templates():
    """List all prompt templates.

    Returns:
        JSON with list of template summaries (name, version, section_count, hash)
    """
    from poker.prompt_manager import PromptManager
    from poker.prompts import extract_variables

    try:
        manager = PromptManager()
        templates = []

        for name in sorted(manager.list_templates()):
            template = manager.get_template(name)
            # Extract variables from all sections
            all_content = '\n'.join(template.sections.values())
            variables = extract_variables(all_content)

            templates.append({
                'name': template.name,
                'version': template.version,
                'section_count': len(template.sections),
                'hash': template.template_hash,
                'variables': variables,
            })

        return jsonify({
            'success': True,
            'templates': templates,
            'total': len(templates),
        })

    except Exception as e:
        logger.error(f"Error listing templates: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/prompts/templates/<template_name>')
@_dev_only
def api_get_template(template_name: str):
    """Get a single template with full content.

    Args:
        template_name: Name of the template

    Returns:
        JSON with full template details including all sections
    """
    from poker.prompt_manager import PromptManager
    from poker.prompts import validate_template_name, extract_variables

    # Security: validate template name
    if not validate_template_name(template_name):
        return jsonify({'success': False, 'error': 'Invalid template name'}), 400

    try:
        manager = PromptManager()
        template = manager.get_template(template_name)

        # Extract variables from all sections
        all_content = '\n'.join(template.sections.values())
        variables = extract_variables(all_content)

        return jsonify({
            'success': True,
            'template': {
                'name': template.name,
                'version': template.version,
                'sections': template.sections,
                'hash': template.template_hash,
                'variables': variables,
            }
        })

    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 404
    except Exception as e:
        logger.error(f"Error getting template {template_name}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/prompts/templates/<template_name>', methods=['PUT'])
@_dev_only
def api_update_template(template_name: str):
    """Update a template by saving to its YAML file.

    Args:
        template_name: Name of the template

    Request body:
        {
            "sections": {"section_name": "content", ...},
            "version": "1.0.1" (optional)
        }

    Returns:
        JSON with success status and new hash
    """
    from poker.prompt_manager import PromptManager
    from poker.prompts import validate_template_name, validate_template_schema

    # Security: validate template name
    if not validate_template_name(template_name):
        return jsonify({'success': False, 'error': 'Invalid template name'}), 400

    try:
        data = request.get_json()
        if not data or 'sections' not in data:
            return jsonify({'success': False, 'error': 'Missing sections'}), 400

        sections = data['sections']
        version = data.get('version')

        # Validate sections is a dict of strings
        if not isinstance(sections, dict):
            return jsonify({'success': False, 'error': 'sections must be a dict'}), 400

        for key, value in sections.items():
            if not isinstance(key, str) or not isinstance(value, str):
                return jsonify({'success': False, 'error': 'Section keys and values must be strings'}), 400

        # Validate schema (required sections)
        is_valid, error = validate_template_schema(template_name, sections)
        if not is_valid:
            return jsonify({'success': False, 'error': error}), 400

        # Save the template
        manager = PromptManager()

        # Verify template exists
        try:
            manager.get_template(template_name)
        except ValueError:
            return jsonify({'success': False, 'error': f"Template '{template_name}' not found"}), 404

        # Save to YAML file
        success = manager.save_template(template_name, sections, version)

        if success:
            # Get the new hash
            updated = manager.get_template(template_name)
            return jsonify({
                'success': True,
                'message': 'Template updated',
                'new_hash': updated.template_hash,
                'new_version': updated.version,
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to save template'}), 500

    except Exception as e:
        logger.error(f"Error updating template {template_name}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/prompts/templates/<template_name>/preview', methods=['POST'])
@_dev_only
def api_preview_template(template_name: str):
    """Preview a template render with sample variables.

    Args:
        template_name: Name of the template

    Request body:
        {
            "sections": {"section_name": "content", ...} (optional, uses current if not provided),
            "variables": {"var_name": "value", ...}
        }

    Returns:
        JSON with rendered output and any missing variables
    """
    from poker.prompt_manager import PromptManager, PromptTemplate
    from poker.prompts import validate_template_name, extract_variables

    # Security: validate template name
    if not validate_template_name(template_name):
        return jsonify({'success': False, 'error': 'Invalid template name'}), 400

    try:
        data = request.get_json() or {}
        variables = data.get('variables', {})
        custom_sections = data.get('sections')

        manager = PromptManager()

        # Get the template (or use custom sections)
        if custom_sections:
            template = PromptTemplate(
                name=template_name,
                sections=custom_sections
            )
        else:
            template = manager.get_template(template_name)

        # Find all variables needed
        all_content = '\n'.join(template.sections.values())
        required_vars = set(extract_variables(all_content))
        provided_vars = set(variables.keys())
        missing_vars = required_vars - provided_vars

        # Render with provided variables (fill missing with placeholders)
        render_vars = {var: f'[{var}]' for var in required_vars}
        render_vars.update(variables)

        try:
            rendered = template.render(**render_vars)
            render_error = None
        except Exception as e:
            rendered = None
            render_error = str(e)

        return jsonify({
            'success': True,
            'rendered': rendered,
            'render_error': render_error,
            'required_variables': sorted(required_vars),
            'missing_variables': sorted(missing_vars),
        })

    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 404
    except Exception as e:
        logger.error(f"Error previewing template {template_name}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# Pricing Management API
# =============================================================================

@admin_dashboard_bp.route('/pricing', methods=['GET'])
def list_pricing():
    """List all pricing entries, optionally filtered.

    Query params:
        provider: Filter by provider (e.g., 'openai')
        model: Filter by model (e.g., 'gpt-4o')
        current_only: If 'true', only show currently valid prices
    """
    provider = request.args.get('provider')
    model = request.args.get('model')
    current_only = request.args.get('current_only', 'false').lower() == 'true'

    try:
        rows = llm_repo.list_pricing(provider, model, current_only)
        return jsonify({'success': True, 'count': len(rows), 'pricing': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/pricing', methods=['POST'])
def add_pricing():
    """Add a new pricing entry, expiring any current price for the same SKU.

    Body (JSON):
        provider: Provider name (required)
        model: Model name (required)
        unit: Pricing unit (required) - e.g., 'input_tokens_1m', 'image_1024x1024'
        cost: Cost in USD (required)
        valid_from: When effective (optional, default: now)
        notes: Optional notes
    """
    data = request.get_json()

    required = ['provider', 'model', 'unit', 'cost']
    missing = [f for f in required if f not in data]
    if missing:
        return jsonify({'success': False, 'error': f'Missing required fields: {missing}'}), 400

    provider = data['provider']
    model = data['model']
    unit = data['unit']
    try:
        cost = float(data['cost'])
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Invalid cost value: must be a number'}), 400
    valid_from = data.get('valid_from') or datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    notes = data.get('notes')

    try:
        llm_repo.add_pricing(provider, model, unit, cost, valid_from, notes)
        UsageTracker.get_default().invalidate_pricing_cache()
        return jsonify({
            'success': True,
            'message': f'Added pricing for {provider}/{model}/{unit}: ${cost}'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/pricing/bulk', methods=['POST'])
def bulk_add_pricing():
    """Add multiple pricing entries at once.

    Body (JSON):
        entries: List of {provider, model, unit, cost, notes?}
        expire_existing: If true, expire existing prices (default: true)
    """
    data = request.get_json()
    entries = data.get('entries', [])
    expire_existing = data.get('expire_existing', True)

    if not entries:
        return jsonify({'success': False, 'error': 'No entries provided'}), 400

    try:
        added, errors = llm_repo.bulk_add_pricing(entries, expire_existing)
        if added > 0:
            UsageTracker.get_default().invalidate_pricing_cache()
        return jsonify({'success': True, 'added': added, 'errors': errors})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/pricing/<int:pricing_id>', methods=['DELETE'])
def delete_pricing(pricing_id: int):
    """Delete a pricing entry by ID."""
    try:
        deleted = llm_repo.delete_pricing(pricing_id)
        if not deleted:
            return jsonify({'success': False, 'error': 'Not found'}), 404
        UsageTracker.get_default().invalidate_pricing_cache()
        return jsonify({'success': True, 'message': f'Deleted pricing entry {pricing_id}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/pricing/providers', methods=['GET'])
def list_providers():
    """List all providers with model/SKU counts."""
    try:
        providers = llm_repo.list_providers_with_counts()
        return jsonify({'success': True, 'providers': providers})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/pricing/models/<provider>', methods=['GET'])
def list_models_for_provider(provider: str):
    """List all models for a provider."""
    # Validate provider: alphanumeric, hyphens, underscores, max 64 chars
    if not provider or len(provider) > 64 or not re.match(r'^[\w-]+$', provider):
        return jsonify({'success': False, 'error': 'Invalid provider format'}), 400

    try:
        models = llm_repo.list_models_for_provider(provider)
        return jsonify({
            'success': True,
            'provider': provider,
            'models': models
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================================================================
# Debug Tools - Redirect to React Admin
# =============================================================================

@admin_dashboard_bp.route('/debug')
@_dev_only
def debug_page():
    """Redirect to React admin dashboard."""
    return jsonify({
        'message': 'Debug tools has moved to React UI',
        'redirect': '/?view=admin'
    })


# =============================================================================
# App Settings API

VALID_SETTING_KEYS = {
    'LLM_PROMPT_CAPTURE', 'LLM_PROMPT_RETENTION_DAYS',
    'DEFAULT_PROVIDER', 'DEFAULT_MODEL',
    'FAST_PROVIDER', 'FAST_MODEL',
    'IMAGE_PROVIDER', 'IMAGE_MODEL',
    'ASSISTANT_PROVIDER', 'ASSISTANT_MODEL',
}
# =============================================================================

@admin_dashboard_bp.route('/api/settings')
@_dev_only
def api_get_settings():
    """Get all configurable app settings with current values and metadata.

    Returns settings for:
    - LLM_PROMPT_CAPTURE: Capture mode (disabled, all, all_except_decisions)
    - LLM_PROMPT_RETENTION_DAYS: Days to keep captures (0 = unlimited)
    - DEFAULT_PROVIDER/DEFAULT_MODEL: Default LLM for general use
    - IMAGE_PROVIDER/IMAGE_MODEL: Model for avatar generation
    - ASSISTANT_PROVIDER/ASSISTANT_MODEL: Reasoning model for experiment assistant
    """
    from core.llm.capture_config import (
        get_capture_mode, get_retention_days, get_env_defaults,
        CAPTURE_DISABLED, CAPTURE_ALL, CAPTURE_ALL_EXCEPT_DECISIONS
    )
    from core.llm.config import (
        DEFAULT_MODEL, ASSISTANT_MODEL, ASSISTANT_PROVIDER,
        FAST_MODEL, FAST_PROVIDER,
    )

    try:
        # Get env defaults for display
        env_defaults = get_env_defaults()

        # Get current values (DB if exists, else env)
        current_capture_mode = get_capture_mode()
        current_retention_days = get_retention_days()

        # Get DB values directly to show if overridden
        db_settings = settings_repo.get_all_settings()

        # System model settings - get from DB or fall back to env/defaults
        default_provider = settings_repo.get_setting('DEFAULT_PROVIDER', '') or 'openai'
        default_model = settings_repo.get_setting('DEFAULT_MODEL', '') or DEFAULT_MODEL
        image_provider = settings_repo.get_setting('IMAGE_PROVIDER', '') or os.environ.get('IMAGE_PROVIDER', 'openai')
        image_model = settings_repo.get_setting('IMAGE_MODEL', '') or os.environ.get('IMAGE_MODEL', '')
        fast_provider = settings_repo.get_setting('FAST_PROVIDER', '') or FAST_PROVIDER
        fast_model = settings_repo.get_setting('FAST_MODEL', '') or FAST_MODEL
        assistant_provider = settings_repo.get_setting('ASSISTANT_PROVIDER', '') or ASSISTANT_PROVIDER
        assistant_model = settings_repo.get_setting('ASSISTANT_MODEL', '') or ASSISTANT_MODEL

        settings = {
            'LLM_PROMPT_CAPTURE': {
                'value': current_capture_mode,
                'options': [CAPTURE_DISABLED, CAPTURE_ALL, CAPTURE_ALL_EXCEPT_DECISIONS],
                'description': 'Controls which LLM calls are captured for debugging',
                'env_default': env_defaults['capture_mode'],
                'is_db_override': 'LLM_PROMPT_CAPTURE' in db_settings,
            },
            'LLM_PROMPT_RETENTION_DAYS': {
                'value': str(current_retention_days),
                'type': 'number',
                'description': 'Days to keep captures (0 = unlimited)',
                'env_default': str(env_defaults['retention_days']),
                'is_db_override': 'LLM_PROMPT_RETENTION_DAYS' in db_settings,
            },
            # System model settings
            'DEFAULT_PROVIDER': {
                'value': default_provider,
                'description': 'Default LLM provider for general use',
                'env_default': 'openai',
                'is_db_override': 'DEFAULT_PROVIDER' in db_settings,
            },
            'DEFAULT_MODEL': {
                'value': default_model,
                'description': 'Default LLM model for personality generation, commentary, game support',
                'env_default': DEFAULT_MODEL,
                'is_db_override': 'DEFAULT_MODEL' in db_settings,
            },
            'FAST_PROVIDER': {
                'value': fast_provider,
                'description': 'Provider for chat suggestions, categorization, quick tasks',
                'env_default': FAST_PROVIDER,
                'is_db_override': 'FAST_PROVIDER' in db_settings,
            },
            'FAST_MODEL': {
                'value': fast_model,
                'description': 'Fast model for chat suggestions, categorization, quick tasks',
                'env_default': FAST_MODEL,
                'is_db_override': 'FAST_MODEL' in db_settings,
            },
            'IMAGE_PROVIDER': {
                'value': image_provider,
                'description': 'Provider for generating AI player avatars',
                'env_default': os.environ.get('IMAGE_PROVIDER', 'openai'),
                'is_db_override': 'IMAGE_PROVIDER' in db_settings,
            },
            'IMAGE_MODEL': {
                'value': image_model,
                'description': 'Model for generating AI player avatars',
                'env_default': os.environ.get('IMAGE_MODEL', ''),
                'is_db_override': 'IMAGE_MODEL' in db_settings,
            },
            'ASSISTANT_PROVIDER': {
                'value': assistant_provider,
                'description': 'Provider for experiment design, analysis, theme generation',
                'env_default': ASSISTANT_PROVIDER,
                'is_db_override': 'ASSISTANT_PROVIDER' in db_settings,
            },
            'ASSISTANT_MODEL': {
                'value': assistant_model,
                'description': 'Model for experiment design, analysis, theme generation',
                'env_default': ASSISTANT_MODEL,
                'is_db_override': 'ASSISTANT_MODEL' in db_settings,
            },
        }

        return jsonify({
            'success': True,
            'settings': settings,
        })

    except Exception as e:
        logger.error(f"Error getting settings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/settings', methods=['POST'])
@_dev_only
def api_update_setting():
    """Update a single app setting.

    Request body:
        key: Setting key (e.g., 'LLM_PROMPT_CAPTURE')
        value: New value
    """
    from core.llm.capture_config import CAPTURE_DISABLED, CAPTURE_ALL, CAPTURE_ALL_EXCEPT_DECISIONS

    try:
        data = request.get_json()
        if not data or 'key' not in data or 'value' not in data:
            return jsonify({'success': False, 'error': 'Missing key or value'}), 400

        key = data['key']
        value = str(data['value'])

        # Validate setting key and value
        if key not in VALID_SETTING_KEYS:
            return jsonify({'success': False, 'error': f'Unknown setting: {key}'}), 400

        # Validate values based on key
        if key == 'LLM_PROMPT_CAPTURE':
            valid_modes = [CAPTURE_DISABLED, CAPTURE_ALL, CAPTURE_ALL_EXCEPT_DECISIONS]
            if value.lower() not in valid_modes:
                return jsonify({
                    'success': False,
                    'error': f'Invalid capture mode. Must be one of: {valid_modes}'
                }), 400
            value = value.lower()

        elif key == 'LLM_PROMPT_RETENTION_DAYS':
            try:
                days = int(value)
                if days < 0:
                    return jsonify({
                        'success': False,
                        'error': 'Retention days must be >= 0'
                    }), 400
            except ValueError:
                return jsonify({
                    'success': False,
                    'error': 'Retention days must be a number'
                }), 400

        # Save the setting
        descriptions = {
            'LLM_PROMPT_CAPTURE': 'Controls which LLM calls are captured for debugging',
            'LLM_PROMPT_RETENTION_DAYS': 'Days to keep captures (0 = unlimited)',
            'DEFAULT_PROVIDER': 'Default LLM provider for general use',
            'DEFAULT_MODEL': 'Default LLM model for personality generation, commentary, game support',
            'FAST_PROVIDER': 'Provider for chat suggestions, categorization, quick tasks',
            'FAST_MODEL': 'Fast model for chat suggestions, categorization, quick tasks',
            'IMAGE_PROVIDER': 'Provider for generating AI player avatars',
            'IMAGE_MODEL': 'Model for generating AI player avatars',
            'ASSISTANT_PROVIDER': 'Provider for experiment design, analysis, theme generation',
            'ASSISTANT_MODEL': 'Model for experiment design, analysis, theme generation',
        }

        success = settings_repo.set_setting(key, value, descriptions.get(key))

        if success:
            return jsonify({
                'success': True,
                'message': f'Setting {key} updated to {value}',
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to save setting'}), 500

    except Exception as e:
        logger.error(f"Error updating setting: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/settings/reset', methods=['POST'])
@_dev_only
def api_reset_settings():
    """Reset settings to environment variable defaults.

    Request body (optional):
        key: Specific setting to reset (if not provided, resets all)
    """
    try:
        data = request.get_json() or {}
        key = data.get('key')

        if key:
            # Reset specific setting
            if key not in VALID_SETTING_KEYS:
                return jsonify({'success': False, 'error': f'Unknown setting: {key}'}), 400

            success = settings_repo.delete_setting(key)
            return jsonify({
                'success': True,
                'message': f'Setting {key} reset to environment default',
                'deleted': success,
            })
        else:
            # Reset all settings
            deleted_count = 0
            for k in VALID_SETTING_KEYS:
                if settings_repo.delete_setting(k):
                    deleted_count += 1

            return jsonify({
                'success': True,
                'message': f'Reset {deleted_count} settings to environment defaults',
                'deleted': deleted_count,
            })

    except Exception as e:
        logger.error(f"Error resetting settings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/active-games')
@_dev_only
def api_active_games():
    """Get list of games (active in memory + recent saved games).

    Returns:
        List of games with game_id, owner_name, player names, phase, etc.
        Active games are marked with is_active=True
    """
    import json as json_module

    try:
        all_games = []
        seen_game_ids = set()

        # First, get active (in-memory) games
        for game_id in game_state_service.list_game_ids():
            game_data = game_state_service.get_game(game_id)
            if not game_data:
                continue

            state_machine = game_data.get('state_machine')
            owner_name = game_data.get('owner_name', 'Unknown')

            game_info = {
                'game_id': game_id,
                'owner_name': owner_name,
                'players': [],
                'phase': None,
                'hand_number': None,
                'is_active': True,  # In memory = active
            }

            if state_machine:
                game_state = state_machine.game_state
                if game_state:
                    game_info['phase'] = state_machine.current_phase.value if hasattr(state_machine, 'current_phase') else None
                    game_info['hand_number'] = game_state.hand_number if hasattr(game_state, 'hand_number') else None

                    # Get player names
                    if hasattr(game_state, 'players'):
                        for player in game_state.players:
                            player_info = {
                                'name': player.name,
                                'chips': player.stack,
                                'is_human': getattr(player, 'is_human', True),
                                'is_active': not player.is_folded and player.stack > 0,
                            }
                            game_info['players'].append(player_info)

            all_games.append(game_info)
            seen_game_ids.add(game_id)

        # Then, add recent saved games from database (not already in memory)
        try:
            saved_games = game_repo.list_games(limit=20)
            for saved_game in saved_games:
                if saved_game.game_id in seen_game_ids:
                    continue  # Already added from memory

                game_info = {
                    'game_id': saved_game.game_id,
                    'owner_name': saved_game.owner_name or 'Unknown',
                    'players': [],
                    'phase': saved_game.phase,
                    'hand_number': None,
                    'is_active': False,  # Saved but not in memory
                    'num_players': saved_game.num_players,
                }

                # Try to extract player names from saved game state
                try:
                    state_dict = json_module.loads(saved_game.game_state_json)
                    if 'players' in state_dict:
                        for p in state_dict['players']:
                            game_info['players'].append({
                                'name': p.get('name', 'Unknown'),
                                'chips': p.get('stack', 0),
                                'is_human': p.get('is_human', False),
                                'is_active': not p.get('is_folded', False) and p.get('stack', 0) > 0,
                            })
                    if 'hand_number' in state_dict:
                        game_info['hand_number'] = state_dict['hand_number']
                except (json_module.JSONDecodeError, KeyError):
                    pass

                all_games.append(game_info)
                seen_game_ids.add(saved_game.game_id)

        except Exception as e:
            logger.warning(f"Could not load saved games: {e}")

        return jsonify({
            'success': True,
            'games': all_games,
            'count': len(all_games),
            'active_count': sum(1 for g in all_games if g.get('is_active')),
        })

    except Exception as e:
        logger.error(f"Error getting active games: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_dashboard_bp.route('/api/settings/storage')
@_dev_only
def api_storage_stats():
    """Get database storage statistics.

    Returns storage breakdown by category:
    - total: Total database size
    - captures: prompt_captures, player_decision_analysis
    - api_usage: api_usage table
    - game_data: games, game_messages, hand_history, etc.
    - ai_state: ai_player_state, controller_state, opponent_models, etc.
    - config: personalities, enabled_models, model_pricing, etc.
    """
    try:
        # Define table categories
        categories = {
            'captures': ['prompt_captures', 'player_decision_analysis'],
            'api_usage': ['api_usage'],
            'game_data': [
                'games', 'game_messages', 'hand_history', 'hand_commentary',
                'tournament_results', 'tournament_standings', 'tournament_tracker'
            ],
            'ai_state': [
                'ai_player_state', 'controller_state', 'emotional_state',
                'opponent_models', 'memorable_hands', 'personality_snapshots',
                'pressure_events', 'player_career_stats'
            ],
            'config': [
                'personalities', 'enabled_models', 'model_pricing',
                'app_settings', 'schema_version'
            ],
            'assets': ['avatar_images'],
        }

        storage = llm_repo.get_storage_stats(categories)
        return jsonify({'success': True, 'storage': storage})
    except Exception as e:
        logger.error(f"Error getting storage stats: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# The following HTML was removed - all admin pages now use React UI
_LEGACY_DEBUG_HTML = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Debug Tools - Admin Dashboard</title>
        <style>
            * {{ box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #1a1a2e;
                color: #eee;
                margin: 0;
                padding: 0;
            }}
            .sidebar {{
                width: 200px;
                background: #16213e;
                position: fixed;
                height: 100%;
                padding: 20px;
            }}
            .sidebar h2 {{
                color: #00d4ff;
                margin: 0 0 30px 0;
                font-size: 1.2em;
            }}
            .sidebar nav a {{
                display: block;
                color: #aaa;
                text-decoration: none;
                padding: 10px 15px;
                margin: 5px 0;
                border-radius: 6px;
                transition: all 0.2s;
            }}
            .sidebar nav a:hover {{
                background: #0f3460;
                color: #eee;
            }}
            .sidebar nav a.active {{
                background: #4ecca3;
                color: #1a1a2e;
            }}
            .content {{
                margin-left: 220px;
                padding: 30px;
            }}
            h1 {{
                color: #00d4ff;
                margin: 0 0 10px 0;
            }}
            .subtitle {{
                color: #888;
                margin-bottom: 30px;
            }}
            h2 {{
                color: #ff6b6b;
                margin-top: 30px;
                font-size: 1.2em;
            }}
            .section {{
                background: #16213e;
                padding: 20px;
                border-radius: 8px;
                margin: 15px 0;
            }}
            .endpoint {{
                margin: 10px 0;
                padding: 15px;
                background: #0f3460;
                border-radius: 4px;
            }}
            .method {{
                color: #ff9f1c;
                font-weight: bold;
                font-family: monospace;
            }}
            .url {{
                color: #4ecca3;
                font-family: monospace;
            }}
            .desc {{
                color: #aaa;
                font-size: 0.9em;
                margin: 5px 0;
            }}
            input, select {{
                background: #0f3460;
                color: #eee;
                border: 1px solid #4ecca3;
                padding: 8px 12px;
                border-radius: 4px;
                margin: 5px;
            }}
            button {{
                background: #4ecca3;
                color: #1a1a2e;
                border: none;
                padding: 10px 20px;
                border-radius: 4px;
                cursor: pointer;
                font-weight: bold;
            }}
            button:hover {{
                background: #3db892;
            }}
            pre {{
                background: #0f3460;
                padding: 15px;
                border-radius: 4px;
                overflow-x: auto;
                font-family: monospace;
                font-size: 0.85em;
            }}
            .game-id {{
                background: #0f3460;
                padding: 5px 10px;
                border-radius: 4px;
                margin: 5px;
                display: inline-block;
                font-family: monospace;
            }}
            a {{
                color: #4ecca3;
            }}
        </style>
    </head>
    <body>
        <div class="sidebar">
            <h2>Admin Dashboard</h2>
            <nav>
                <a href="/admin/">Dashboard</a>
                <a href="/admin/costs">Cost Analysis</a>
                <a href="/admin/performance">Performance</a>
                <a href="/admin/prompts">Prompts</a>
                <a href="/admin/models">Models</a>
                <a href="/admin/pricing">Pricing</a>
                <a href="/admin/debug" class="active">Debug Tools</a>
            </nav>
        </div>
        <div class="content">
            <h1>Debug Tools</h1>
            <p class="subtitle">Game debugging and AI system inspection tools</p>

            <div class="section">
                <h2>Active Games</h2>
                <div style="margin: 10px 0;">
                    {games_html}
                </div>
                <p><a href="/games">View saved games</a></p>
            </div>

            <div class="section">
                <h2>Tilt System Debug</h2>
                <p class="desc">Test the tilt modifier system that affects AI decision-making</p>

                <div class="endpoint">
                    <span class="method">GET</span>
                    <span class="url">/api/game/{{game_id}}/tilt-debug</span>
                    <p class="desc">View tilt state for all AI players</p>
                    <input type="text" id="tilt-game-id" placeholder="game_id" style="width: 300px;">
                    <button onclick="fetchTilt()">Fetch Tilt States</button>
                </div>

                <div class="endpoint">
                    <span class="method">POST</span>
                    <span class="url">/api/game/{{game_id}}/tilt-debug/{{player_name}}</span>
                    <p class="desc">Set tilt state for testing</p>
                    <input type="text" id="set-tilt-game-id" placeholder="game_id" style="width: 200px;">
                    <input type="text" id="set-tilt-player" placeholder="player_name" style="width: 150px;">
                    <br>
                    <select id="tilt-level">
                        <option value="0">None (0.0)</option>
                        <option value="0.3">Mild (0.3)</option>
                        <option value="0.5">Moderate (0.5)</option>
                        <option value="0.8" selected>Severe (0.8)</option>
                        <option value="1.0">Maximum (1.0)</option>
                    </select>
                    <select id="tilt-source">
                        <option value="bad_beat">Bad Beat</option>
                        <option value="bluff_called">Bluff Called</option>
                        <option value="big_loss">Big Loss</option>
                        <option value="losing_streak">Losing Streak</option>
                    </select>
                    <input type="text" id="tilt-nemesis" placeholder="nemesis (optional)" style="width: 150px;">
                    <button onclick="setTilt()">Set Tilt</button>
                </div>
                <pre id="tilt-result">Results will appear here...</pre>
            </div>

            <div class="section">
                <h2>Memory System Debug</h2>
                <div class="endpoint">
                    <span class="method">GET</span>
                    <span class="url">/api/game/{{game_id}}/memory-debug</span>
                    <p class="desc">View AI memory state (session memory, opponent models)</p>
                    <input type="text" id="memory-game-id" placeholder="game_id" style="width: 300px;">
                    <button onclick="fetchMemory()">Fetch Memory</button>
                </div>
                <pre id="memory-result">Results will appear here...</pre>
            </div>

            <div class="section">
                <h2>Elasticity System Debug</h2>
                <div class="endpoint">
                    <span class="method">GET</span>
                    <span class="url">/api/game/{{game_id}}/elasticity</span>
                    <p class="desc">View elastic personality traits for all AI players</p>
                    <input type="text" id="elasticity-game-id" placeholder="game_id" style="width: 300px;">
                    <button onclick="fetchElasticity()">Fetch Elasticity</button>
                </div>
                <pre id="elasticity-result">Results will appear here...</pre>
            </div>

            <div class="section">
                <h2>Pressure Stats</h2>
                <div class="endpoint">
                    <span class="method">GET</span>
                    <span class="url">/api/game/{{game_id}}/pressure-stats</span>
                    <p class="desc">View pressure events and statistics</p>
                    <input type="text" id="pressure-game-id" placeholder="game_id" style="width: 300px;">
                    <button onclick="fetchPressure()">Fetch Pressure Stats</button>
                </div>
                <pre id="pressure-result">Results will appear here...</pre>
            </div>

            <div class="section">
                <h2>Game State</h2>
                <div class="endpoint">
                    <span class="method">GET</span>
                    <span class="url">/api/game/{{game_id}}/diagnostic</span>
                    <p class="desc">Full game diagnostic info</p>
                    <input type="text" id="diag-game-id" placeholder="game_id" style="width: 300px;">
                    <button onclick="fetchDiagnostic()">Fetch Diagnostic</button>
                </div>
                <pre id="diag-result">Results will appear here...</pre>
            </div>
        </div>

        <script>
            async function fetchJson(url, options = {{}}) {{
                try {{
                    const resp = await fetch(url, options);
                    return await resp.json();
                }} catch (e) {{
                    return {{error: e.message}};
                }}
            }}

            async function fetchTilt() {{
                const gameId = document.getElementById('tilt-game-id').value;
                const result = await fetchJson(`/api/game/${{gameId}}/tilt-debug`);
                document.getElementById('tilt-result').textContent = JSON.stringify(result, null, 2);
            }}

            async function setTilt() {{
                const gameId = document.getElementById('set-tilt-game-id').value;
                const player = encodeURIComponent(document.getElementById('set-tilt-player').value);
                const data = {{
                    tilt_level: parseFloat(document.getElementById('tilt-level').value),
                    tilt_source: document.getElementById('tilt-source').value,
                    nemesis: document.getElementById('tilt-nemesis').value || null
                }};
                const result = await fetchJson(`/api/game/${{gameId}}/tilt-debug/${{player}}`, {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify(data)
                }});
                document.getElementById('tilt-result').textContent = JSON.stringify(result, null, 2);
            }}

            async function fetchMemory() {{
                const gameId = document.getElementById('memory-game-id').value;
                const result = await fetchJson(`/api/game/${{gameId}}/memory-debug`);
                document.getElementById('memory-result').textContent = JSON.stringify(result, null, 2);
            }}

            async function fetchElasticity() {{
                const gameId = document.getElementById('elasticity-game-id').value;
                const result = await fetchJson(`/api/game/${{gameId}}/elasticity`);
                document.getElementById('elasticity-result').textContent = JSON.stringify(result, null, 2);
            }}

            async function fetchPressure() {{
                const gameId = document.getElementById('pressure-game-id').value;
                const result = await fetchJson(`/api/game/${{gameId}}/pressure-stats`);
                document.getElementById('pressure-result').textContent = JSON.stringify(result, null, 2);
            }}

            async function fetchDiagnostic() {{
                const gameId = document.getElementById('diag-game-id').value;
                const result = await fetchJson(`/api/game/${{gameId}}/diagnostic`);
                document.getElementById('diag-result').textContent = JSON.stringify(result, null, 2);
            }}
        </script>
    </body>
    </html>
    '''
