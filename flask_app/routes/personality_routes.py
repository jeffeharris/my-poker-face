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


def _validate_theme_game_settings(settings: dict) -> dict:
    """Clamp LLM-generated game settings to valid ranges."""
    validated = {}
    if 'description' in settings and isinstance(settings['description'], str):
        validated['description'] = settings['description'][:200]
    if 'game_mode' in settings:
        mode = str(settings['game_mode']).lower()
        # Never allow pro mode from themed games
        if mode in ('casual', 'standard', 'competitive'):
            validated['game_mode'] = mode
    if 'starting_stack' in settings:
        try:
            validated['starting_stack'] = max(500, min(20000, int(settings['starting_stack'])))
        except (ValueError, TypeError):
            pass
    if 'big_blind' in settings:
        try:
            validated['big_blind'] = max(10, min(200, int(settings['big_blind'])))
        except (ValueError, TypeError):
            pass
    if 'blind_growth' in settings:
        try:
            growth = float(settings['blind_growth'])
            valid_growths = [1.25, 1.5, 2]
            validated['blind_growth'] = min(valid_growths, key=lambda x: abs(x - growth))
        except (ValueError, TypeError):
            pass
    if 'blinds_increase' in settings:
        try:
            increase = int(settings['blinds_increase'])
            valid_increases = [4, 6, 8, 10]
            validated['blinds_increase'] = min(valid_increases, key=lambda x: abs(x - increase))
        except (ValueError, TypeError):
            pass
    if 'max_blind' in settings:
        try:
            validated['max_blind'] = max(0, min(5000, int(settings['max_blind'])))
        except (ValueError, TypeError):
            pass
    return validated


def _validate_theme_personalities(personalities_list: list, personality_sample: list) -> list:
    """Validate LLM-returned personalities, supporting both string and object formats."""
    ALLOWED_PLAYER_MODES = {'casual', 'standard', 'competitive'}
    valid = []
    personality_set = set(personality_sample)
    for p in personalities_list:
        if isinstance(p, str):
            if p in personality_set:
                valid.append(p)
        elif isinstance(p, dict):
            name = p.get('name', '')
            if name in personality_set:
                entry = {'name': name}
                mode = str(p.get('game_mode', '')).lower()
                if mode in ALLOWED_PLAYER_MODES:
                    entry['game_mode'] = mode
                valid.append(entry)
    return valid


@personality_bp.route('/api/generate-theme', methods=['POST'])
@limiter.limit(config.RATE_LIMIT_GENERATE_THEME)
def generate_theme():
    """Generate a themed game with appropriate personalities and game settings."""
    try:
        data = request.json
        theme = data.get('theme')
        theme_name = data.get('themeName')
        description = data.get('description')

        if not theme:
            return jsonify({'error': 'Theme is required'}), 400

        # Load personality names from database (source of truth), fall back to hardcoded list
        db_personalities = persistence.list_personalities(limit=200)
        if db_personalities:
            all_personalities = [p['name'] for p in db_personalities]
        else:
            all_personalities = list(get_celebrities())
        sample_size = min(100, len(all_personalities))
        personality_sample = random.sample(all_personalities, sample_size)

        prompt = f"""You are designing a themed poker game: "{theme_name}" - {description}

Available personalities: {', '.join(personality_sample)}

Design a complete game setup. First, write a short punchy description of the game. Then pick 3-5 personalities and choose game settings that fit the theme.

Return a JSON object with this structure:
{{
  "description": "Short, punchy description of this game — one sentence, conversational tone",
  "personalities": [
    {{"name": "Name1", "game_mode": "casual|standard|competitive"}},
    {{"name": "Name2", "game_mode": "casual|standard|competitive"}},
    {{"name": "Name3"}}
  ],
  "game_mode": "casual|standard|competitive",
  "starting_stack": <500-20000>,
  "big_blind": <10-200>,
  "blind_growth": <1.25|1.5|2>,
  "blinds_increase": <4|6|8|10>,
  "max_blind": <0-5000, 0 means no cap>
}}

Example descriptions (match this tone — short, fun, direct):
- "Mad scientists with deep pockets and slow blinds. Expect long, calculated battles."
- "Trash-talking legends in a fast, brutal shootout. Don't blink."
- "A chill game with history's biggest personalities. Sit back and enjoy the banter."
- "Total chaos. Random cast, random settings. Good luck."

Each personality can optionally have its own game_mode override. If omitted, the table-level game_mode applies.
Mixing modes creates interesting dynamics — e.g., a competitive villain at a casual table.

Example presets for reference:
- Lightning (fast & intense): 500 stack, 50 blind, competitive, 2x growth, every 4 hands, 800 cap
- 1v1 (heads-up duel): 1000 stack, 50 blind, competitive, 1.5x growth, every 6 hands, no cap
- Classic (relaxed): 1000 stack, 50 blind, casual, 1.25x growth, every 8 hands, no cap

Difficulty guidance:
- Lower starting_stack relative to big_blind = harder, faster games
- Higher blind_growth and lower blinds_increase = more pressure, quicker escalation
- competitive mode = tougher AI opponents; casual = more personality-driven, forgiving play
- standard mode = balanced middle ground

Guidelines:
- Choose personalities that match the theme and create interesting dynamics
- For "surprise" theme, pick an eclectic, unexpected mix AND randomize the game settings too — make it unpredictable
- Match game settings to the theme's energy:
  - Action themes (villains, sports) → shorter stacks, faster blinds, competitive mode
  - Cerebral themes (science, history) → deeper stacks, slower blinds, standard mode
  - Fun/casual themes (comedy, music) → moderate stacks, casual mode
  - Surprise → anything goes, be creative
- Ensure good variety in play styles among the personalities
- Do NOT use "pro" game mode — only casual, standard, or competitive

Return ONLY the JSON object, no other text."""

        # Get owner_id for tracking
        current_user = auth_manager.get_current_user()
        owner_id = current_user.get('id') if current_user else None

        client = LLMClient(model=config.get_assistant_model(), provider=config.get_assistant_provider())
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

            result = json.loads(response_text)

            # Handle both old (array) and new (object) response formats
            if isinstance(result, list):
                personalities_list = result
                game_settings = {}
            else:
                personalities_list = result.get('personalities', [])
                game_settings = {
                    k: result[k] for k in ('description', 'game_mode', 'starting_stack', 'big_blind', 'blind_growth', 'blinds_increase', 'max_blind')
                    if k in result
                }

            valid_personalities = _validate_theme_personalities(personalities_list, personality_sample)

            if len(valid_personalities) < 3:
                logger.warning(f"Theme generation returned insufficient valid personalities ({len(valid_personalities)}), using random fallback")
                valid_personalities = random.sample(personality_sample, min(4, len(personality_sample)))

            game_settings = _validate_theme_game_settings(game_settings)

            return jsonify({
                'success': True,
                'personalities': valid_personalities[:5],
                **game_settings
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
