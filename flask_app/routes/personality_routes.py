"""Personality management routes."""

import json
import logging
import random
from pathlib import Path

from flask import Blueprint, jsonify, request, redirect

from poker.utils import get_celebrities
from core.llm import LLMClient, CallType

from ..extensions import persistence, limiter, auth_manager, personality_generator
from .. import config

logger = logging.getLogger(__name__)

personality_bp = Blueprint('personality', __name__)


@personality_bp.route('/personalities')
def personalities_page():
    """Deprecated: Personality manager page now in React."""
    return redirect('/api/personalities')


@personality_bp.route('/api/personalities', methods=['GET'])
def get_personalities():
    """Get all personalities."""
    try:
        db_personalities = persistence.list_personalities(limit=200)

        personalities = {}
        for p in db_personalities:
            name = p['name']
            config_data = persistence.load_personality(name)
            if config_data:
                personalities[name] = config_data

        try:
            personalities_file = Path(__file__).parent.parent.parent / 'poker' / 'personalities.json'
            with open(personalities_file, 'r') as f:
                data = json.load(f)
                for name, config_data in data.get('personalities', {}).items():
                    if name not in personalities:
                        personalities[name] = config_data
        except:
            pass

        return jsonify({
            'success': True,
            'personalities': personalities
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@personality_bp.route('/api/personality/<name>', methods=['GET'])
def get_personality(name):
    """Get a specific personality."""
    try:
        db_personality = persistence.load_personality(name)
        if db_personality:
            return jsonify({
                'success': True,
                'personality': db_personality,
                'name': name
            })

        try:
            personalities_file = Path(__file__).parent.parent.parent / 'poker' / 'personalities.json'
            with open(personalities_file, 'r') as f:
                data = json.load(f)

            if name in data['personalities']:
                return jsonify({
                    'success': True,
                    'personality': data['personalities'][name],
                    'name': name
                })
        except:
            pass

        return jsonify({'success': False, 'error': 'Personality not found'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@personality_bp.route('/api/personality', methods=['POST'])
def create_personality():
    """Create a new personality.

    Saves to database only (database is the source of truth).
    """
    try:
        data = request.json
        name = data.get('name')

        if not name:
            return jsonify({'success': False, 'error': 'Name is required'})

        personality_config = {k: v for k, v in data.items() if k != 'name'}

        if 'personality_traits' not in personality_config:
            personality_config['personality_traits'] = {
                "bluff_tendency": 0.5,
                "aggression": 0.5,
                "chattiness": 0.5,
                "emoji_usage": 0.3
            }

        persistence.save_personality(name, personality_config, source='user_created')

        return jsonify({
            'success': True,
            'message': f'Personality {name} created successfully'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@personality_bp.route('/api/personality/<name>', methods=['PUT'])
def update_personality(name):
    """Update a personality.

    Saves to database only (database is the source of truth).
    """
    try:
        personality_config = request.json

        persistence.save_personality(name, personality_config, source='user_edited')

        return jsonify({
            'success': True,
            'message': f'Personality {name} updated successfully'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@personality_bp.route('/api/personality/<name>/avatar-description', methods=['PUT'])
def update_avatar_description(name):
    """Update the avatar description for a personality.

    The avatar description is used for image generation when the character name
    might be blocked by content policies (e.g., real celebrities).

    Request body:
        {
            "avatar_description": "A stern-faced man with graying hair..."
        }
    """
    try:
        data = request.json
        avatar_description = data.get('avatar_description', '').strip()

        if not avatar_description:
            return jsonify({
                'success': False,
                'error': 'avatar_description is required'
            }), 400

        # Check if personality exists
        personality_config = personality_generator.get_personality(name)
        if not personality_config:
            return jsonify({
                'success': False,
                'error': f'Personality {name} not found'
            }), 404

        # Update avatar_description via personality_generator (handles both cache and persistence)
        personality_generator.set_avatar_description(name, avatar_description)

        return jsonify({
            'success': True,
            'message': f'Avatar description updated for {name}',
            'avatar_description': avatar_description
        })
    except Exception as e:
        logger.error(f"Error updating avatar description for {name}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@personality_bp.route('/api/personality/<name>', methods=['DELETE'])
def delete_personality(name):
    """Delete a personality.

    Deletes from database only (database is the source of truth).
    Note: Also deletes associated avatar images from database.
    """
    try:
        # Delete associated avatar images
        persistence.delete_avatar_images(name)

        # Delete the personality
        deleted = persistence.delete_personality(name)

        if not deleted:
            return jsonify({
                'success': False,
                'error': f'Personality {name} not found'
            })

        return jsonify({
            'success': True,
            'message': f'Personality {name} deleted successfully'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@personality_bp.route('/api/personality/<name>/reference-image', methods=['GET'])
def get_reference_image(name):
    """Get the reference image ID for a personality.

    The reference image is used for img2img generation to create
    consistent avatar images based on a user-provided photo.

    Returns:
        {
            "success": true,
            "reference_image_id": "uuid-or-null"
        }
    """
    try:
        # Check if personality exists
        personality_config = personality_generator.get_personality(name)
        if not personality_config:
            return jsonify({
                'success': False,
                'error': f'Personality {name} not found'
            }), 404

        reference_image_id = personality_generator.get_reference_image_id(name)

        return jsonify({
            'success': True,
            'reference_image_id': reference_image_id
        })
    except Exception as e:
        logger.error(f"Error getting reference image for {name}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@personality_bp.route('/api/personality/<name>/reference-image', methods=['PUT'])
def update_reference_image(name):
    """Set or clear the reference image ID for a personality.

    The reference image is used for img2img generation to create
    consistent avatar images based on a user-provided photo.

    Request body:
        {
            "reference_image_id": "uuid-or-null"
        }

    Set to null to clear the reference image.
    """
    try:
        data = request.json
        reference_image_id = data.get('reference_image_id')

        # Check if personality exists
        personality_config = personality_generator.get_personality(name)
        if not personality_config:
            return jsonify({
                'success': False,
                'error': f'Personality {name} not found'
            }), 404

        # Update reference image ID
        personality_generator.set_reference_image_id(name, reference_image_id)

        return jsonify({
            'success': True,
            'message': f'Reference image {"set" if reference_image_id else "cleared"} for {name}',
            'reference_image_id': reference_image_id
        })
    except Exception as e:
        logger.error(f"Error updating reference image for {name}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@personality_bp.route('/api/generate-theme', methods=['POST'])
def generate_theme():
    """Generate a themed game with appropriate personalities."""
    try:
        data = request.json
        theme = data.get('theme')
        theme_name = data.get('themeName')
        description = data.get('description')

        if not theme:
            return jsonify({'error': 'Theme is required'}), 400

        all_personalities = list(get_celebrities())
        sample_size = min(100, len(all_personalities))
        personality_sample = random.sample(all_personalities, sample_size)

        prompt = f"""Given these available personalities: {', '.join(personality_sample)}

Please select 3-5 personalities that would fit the theme: "{theme_name}" - {description}

Selection criteria:
- Choose personalities that match the theme
- Create an interesting mix of personalities that would have fun dynamics
- For "surprise" theme, pick an eclectic, unexpected mix
- Ensure good variety in play styles

Return ONLY a JSON array of personality names, like:
["Name1", "Name2", "Name3", "Name4"]

No other text or explanation."""

        # Get owner_id for tracking
        current_user = auth_manager.get_current_user()
        owner_id = current_user.get('id') if current_user else None

        client = LLMClient(model=config.FAST_AI_MODEL)
        messages = [
            {"role": "system", "content": "You are a game designer selecting personalities for themed poker games."},
            {"role": "user", "content": prompt}
        ]

        response = client.complete(
            messages=messages,
            json_format=True,
            call_type=CallType.THEME_GENERATION,
            owner_id=owner_id,
            prompt_template='theme_generation',
        )
        response_content = response.content or ""

        try:
            response_text = response_content.strip()
            if response_text.startswith('```'):
                response_text = response_text.split('```')[1]
                if response_text.startswith('json'):
                    response_text = response_text[4:]

            personalities = json.loads(response_text)

            valid_personalities = []
            for name in personalities:
                if name in personality_sample:
                    valid_personalities.append(name)

            if len(valid_personalities) < 3:
                logger.warning(f"Theme generation returned insufficient valid personalities ({len(valid_personalities)}), using random fallback")
                valid_personalities = random.sample(personality_sample, min(4, len(personality_sample)))

            return jsonify({
                'success': True,
                'personalities': valid_personalities[:5]
            })

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse theme generation response: {e}. Response was: {response_content}")
            logger.warning("Theme generation using random fallback due to JSON parse error")
            personalities = random.sample(personality_sample, min(4, len(personality_sample)))
            return jsonify({
                'success': True,
                'personalities': personalities,
                'fallback': True
            })

    except Exception as e:
        logger.error(f"Error generating theme: {e}")
        logger.warning("Theme generation using random fallback due to exception")
        try:
            all_personalities = list(get_celebrities())
            personalities = random.sample(all_personalities, min(4, len(all_personalities)))
            return jsonify({
                'success': True,
                'personalities': personalities,
                'fallback': True
            })
        except:
            return jsonify({'error': 'Failed to generate theme'}), 500


@personality_bp.route('/api/generate_personality', methods=['POST'])
@limiter.limit(config.RATE_LIMIT_GENERATE_PERSONALITY)
def generate_personality():
    """Generate a new personality using AI.

    Generated personalities are saved to the database (source of truth).
    """
    try:
        from poker.personality_generator import PersonalityGenerator

        data = request.json
        name = data.get('name', '').strip()

        if not name:
            return jsonify({'success': False, 'error': 'Name is required'})

        force_generate = data.get('force', False)

        generator = PersonalityGenerator()

        # This generates and saves to database automatically
        personality_config = generator.get_personality(
            name=name,
            force_generate=force_generate
        )

        return jsonify({
            'success': True,
            'personality': personality_config,
            'name': name,
            'message': f'Successfully generated personality for {name}'
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'message': 'Failed to generate personality. Please check your OpenAI API key.'
        })
