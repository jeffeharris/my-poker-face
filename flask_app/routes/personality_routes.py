"""Personality management routes."""

import json
import logging
import random
from pathlib import Path

from flask import Blueprint, jsonify, redirect, request

from cash_mode.bankroll import BANKROLL_KNOB_DEFAULTS, BankrollKnobs
from core.llm import CallType, LLMClient
from core.moderation import moderate_text
from poker.authorization import get_authorization_service, require_permission
from poker.guest_limits import is_guest
from poker.utils import get_celebrities

from .. import config, extensions
from ..extensions import limiter

logger = logging.getLogger(__name__)

personality_bp = Blueprint('personality', __name__)


def _moderation_error(text: str):
    """Return a (response, 400) tuple if user-supplied `text` is flagged (PRH-27).

    Personality/theme names + descriptions are interpolated into generation
    prompts and shown back; screen them. Fail-open on outage (see
    core.moderation). Returns None when allowed.
    """
    if text and moderate_text(text).flagged:
        return jsonify(
            {
                'success': False,
                'error': 'That text was flagged by our content filter. Please rephrase.',
                'code': 'MODERATION_REJECTED',
            }
        ), 400
    return None


def _personality_image_text(config) -> str:
    """Collect the user-supplied image-input free text from a personality config.

    ``avatar_description`` and the ``visual_identity`` subfields
    (identity/appearance/apparel) are interpolated into the image-generation
    prompt (see ``poker/image_prompt_config.py``), so they are user UGC that
    reaches a paid generation pipeline — screen them like names (PRH-27).
    Returns a space-joined string of the present fields (empty if none).
    """
    if not isinstance(config, dict):
        return ''
    parts = [config.get('avatar_description')]
    vi = config.get('visual_identity')
    if isinstance(vi, dict):
        parts.extend(vi.get(k) for k in ('identity', 'appearance', 'apparel'))
    return ' '.join(p.strip() for p in parts if isinstance(p, str) and p.strip())


@personality_bp.route('/personalities')
def personalities_page():
    """Deprecated: Personality manager page now in React."""
    return redirect('/api/personalities')


@personality_bp.route('/api/personalities', methods=['GET'])
def get_personalities():
    """Get all personalities visible to the current user."""
    try:
        current_user = extensions.auth_manager.get_current_user()
        if not current_user:
            return jsonify(
                {'success': False, 'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}
            ), 401

        user_id = current_user.get('id')
        auth_service = get_authorization_service()
        is_admin = auth_service and auth_service.has_permission(user_id, 'can_access_admin_tools')

        # Players see the circulating pool + their own personas; demoted
        # sim/test zombies (public but circulating=0) are hidden from the
        # opponent picker. Admins see everything for curation. Mirrors the
        # cash circuit (v123 / list_eligible_for_cash_mode).
        db_personalities = extensions.personality_repo.list_personalities(
            limit=200,
            user_id=user_id,
            include_disabled=is_admin,
            circulating_only=not is_admin,
        )

        personalities = {}
        metadata = {}
        categories = {'standard': [], 'mine': []}
        if is_admin:
            categories['disabled'] = []

        for p in db_personalities:
            name = p['name']
            config_data = extensions.personality_repo.load_personality(name)
            if config_data:
                personalities[name] = config_data

                visibility = p.get('visibility', 'public')
                owner_id = p.get('owner_id')

                metadata[name] = {
                    'visibility': visibility,
                    'owner_id': owner_id,
                }

                if visibility == 'disabled':
                    categories.get('disabled', []).append(name)
                elif owner_id == user_id and visibility == 'private':
                    categories['mine'].append(name)
                else:
                    categories['standard'].append(name)

        return jsonify(
            {
                'success': True,
                'personalities': personalities,
                'categories': categories,
                'metadata': metadata,
                'is_admin': bool(is_admin),
                'user_id': user_id,
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@personality_bp.route('/api/personality/<name>', methods=['GET'])
def get_personality(name):
    """Get a specific personality."""
    try:
        current_user = extensions.auth_manager.get_current_user()
        if not current_user:
            return jsonify(
                {'success': False, 'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}
            ), 401

        user_id = current_user.get('id')
        auth_service = get_authorization_service()
        is_admin = auth_service and auth_service.has_permission(user_id, 'can_access_admin_tools')
        owner_id = extensions.personality_repo.get_personality_owner(name)
        if owner_id and owner_id != user_id and not is_admin:
            return jsonify({'success': False, 'error': 'Permission denied'}), 403

        db_personality = extensions.personality_repo.load_personality(name)
        if db_personality:
            return jsonify({'success': True, 'personality': db_personality, 'name': name})

        personalities_file = Path(__file__).parent.parent.parent / 'poker' / 'personalities.json'
        try:
            with open(personalities_file) as f:
                data = json.load(f)
            catalog = data['personalities']
        except (OSError, json.JSONDecodeError, KeyError) as e:
            # A missing/corrupt catalog is a server fault — surface it as a
            # 500 so it's diagnosable, instead of swallowing it and reporting
            # every built-in character as "not found".
            logger.error("Failed to load personalities catalog %s: %s", personalities_file, e)
            return jsonify({'success': False, 'error': 'Personality catalog unavailable'}), 500

        if name in catalog:
            return jsonify({'success': True, 'personality': catalog[name], 'name': name})

        return jsonify({'success': False, 'error': 'Personality not found'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@personality_bp.route('/api/personality', methods=['POST'])
def create_personality():
    """Create a new personality owned by the current user.

    Saves to database only (database is the source of truth).
    """
    try:
        current_user = extensions.auth_manager.get_current_user()
        if not current_user:
            return jsonify(
                {'success': False, 'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}
            ), 401

        data = request.json
        name = data.get('name')

        if not name:
            return jsonify({'success': False, 'error': 'Name is required'})

        # Screen the name + image-input free text (avatar_description /
        # visual_identity) before it reaches the paid image pipeline (PRH-27).
        flagged = _moderation_error(' '.join(filter(None, [name, _personality_image_text(data)])))
        if flagged:
            return flagged

        # Check for name collision
        existing = extensions.personality_repo.load_personality(name)
        if existing:
            return jsonify(
                {'success': False, 'error': 'A personality with this name already exists'}
            ), 409

        personality_config = {k: v for k, v in data.items() if k != 'name'}

        if 'anchors' not in personality_config:
            personality_config['anchors'] = {
                "baseline_aggression": 0.5,
                "baseline_looseness": 0.3,
                "ego": 0.5,
                "poise": 0.7,
                "expressiveness": 0.5,
                "risk_identity": 0.5,
                "adaptation_bias": 0.5,
                "baseline_energy": 0.5,
                "recovery_rate": 0.15,
            }

        # save_personality assigns a stable personality_id (slug of name,
        # collision-suffixed if needed) and returns it. Surface it in the
        # response so clients can persist relationship state, bankrolls,
        # etc. keyed on the stable id rather than the display name.
        personality_id = extensions.personality_repo.save_personality(
            name,
            personality_config,
            source='user_created',
            owner_id=current_user['id'],
            visibility='private',
        )

        return jsonify(
            {
                'success': True,
                'message': f'Personality {name} created successfully',
                'personality_id': personality_id or None,
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@personality_bp.route('/api/personality/<name>', methods=['PUT'])
def update_personality(name):
    """Update a personality. Only the owner or admin can edit.

    Saves to database only (database is the source of truth).
    """
    try:
        current_user = extensions.auth_manager.get_current_user()
        if not current_user:
            return jsonify(
                {'success': False, 'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}
            ), 401

        user_id = current_user['id']
        owner_id = extensions.personality_repo.get_personality_owner(name)
        auth_service = get_authorization_service()
        is_admin = auth_service and auth_service.has_permission(user_id, 'can_access_admin_tools')

        if owner_id and owner_id != user_id and not is_admin:
            return jsonify({'success': False, 'error': 'Permission denied'}), 403
        # System/built-in personalities have no owner (owner_id is None); the
        # guard above short-circuits on them, so only admins may overwrite the
        # shared catalog. Mirrors update_avatar_description / update_reference_image.
        if not owner_id and not is_admin:
            return jsonify(
                {'success': False, 'error': 'Only admins can edit system personalities'}
            ), 403

        personality_config = request.json

        # Screen edited image-input free text (avatar_description /
        # visual_identity) before it reaches the paid image pipeline (PRH-27).
        flagged = _moderation_error(_personality_image_text(personality_config))
        if flagged:
            return flagged

        # Use update method that preserves owner_id and visibility
        updated = extensions.personality_repo.update_personality_config(
            name, personality_config, source='user_edited'
        )
        if not updated:
            # Personality doesn't exist yet (e.g., manual create) — create it
            extensions.personality_repo.save_personality(
                name,
                personality_config,
                source='user_created',
                owner_id=user_id,
                visibility='private',
            )

        return jsonify({'success': True, 'message': f'Personality {name} updated successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


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
        current_user = extensions.auth_manager.get_current_user()
        if not current_user:
            return jsonify(
                {'success': False, 'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}
            ), 401

        user_id = current_user.get('id')
        auth_service = get_authorization_service()
        is_admin = auth_service and auth_service.has_permission(user_id, 'can_access_admin_tools')
        owner_id = extensions.personality_repo.get_personality_owner(name)
        if owner_id and owner_id != user_id and not is_admin:
            return jsonify({'success': False, 'error': 'Permission denied'}), 403
        if not owner_id and not is_admin:
            return jsonify(
                {'success': False, 'error': 'Only admins can edit system personalities'}
            ), 403

        data = request.json
        avatar_description = data.get('avatar_description', '').strip()

        if not avatar_description:
            return jsonify({'success': False, 'error': 'avatar_description is required'}), 400

        # Screen the description before it reaches the paid image pipeline (PRH-27).
        flagged = _moderation_error(avatar_description)
        if flagged:
            return flagged

        # Check if personality exists
        personality_config = extensions.personality_generator.get_personality(name)
        if not personality_config:
            return jsonify({'success': False, 'error': f'Personality {name} not found'}), 404

        # Update avatar_description via personality_generator (handles both cache and persistence)
        extensions.personality_generator.set_avatar_description(name, avatar_description)

        return jsonify(
            {
                'success': True,
                'message': f'Avatar description updated for {name}',
                'avatar_description': avatar_description,
            }
        )
    except Exception as e:
        logger.error(f"Error updating avatar description for {name}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@personality_bp.route('/api/personality/<name>', methods=['DELETE'])
def delete_personality(name):
    """Delete a personality. Only the owner or admin can delete.

    Deletes from database only (database is the source of truth).
    Note: Also deletes associated avatar images from database.
    """
    try:
        current_user = extensions.auth_manager.get_current_user()
        if not current_user:
            return jsonify(
                {'success': False, 'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}
            ), 401

        user_id = current_user['id']
        owner_id = extensions.personality_repo.get_personality_owner(name)
        auth_service = get_authorization_service()
        is_admin = auth_service and auth_service.has_permission(user_id, 'can_access_admin_tools')

        if owner_id and owner_id != user_id and not is_admin:
            return jsonify({'success': False, 'error': 'Permission denied'}), 403
        # System/built-in personalities have no owner (owner_id is None); the
        # guard above short-circuits on them, so only admins may delete from the
        # shared catalog (delete also cascades to avatar images below).
        if not owner_id and not is_admin:
            return jsonify(
                {'success': False, 'error': 'Only admins can delete system personalities'}
            ), 403

        # Deletion integrity: before dropping the persona, settle what it holds so
        # nothing is stranded. Two composed, best-effort, gated halves (never block
        # the delete):
        #   - CHIPS (Phase 5): return its bankroll (every sandbox) to the bank pool
        #     so the chips recycle (the zombie-persona drift class).
        #   - PRESENCE (R3b): open any casino seat it occupies (drives
        #     RETURN_TO_POOL/GO_OFFLINE) so the seat can't outlive the persona —
        #     what lets _reclaim_zombie_casino_seats retire.
        try:
            pid = extensions.personality_repo.resolve_name_to_personality_id(name)
            if pid:
                from cash_mode.bankroll import settle_ai_bankroll_to_pool_on_delete
                from cash_mode.presence_sweep import sweep_presence_on_persona_delete

                sweep_presence_on_persona_delete(
                    personality_id=pid,
                    repos={
                        'entity_presence_repo': getattr(extensions, 'entity_presence_repo', None),
                        'cash_table_repo': getattr(extensions, 'cash_table_repo', None),
                        'chip_ledger_repo': getattr(extensions, 'chip_ledger_repo', None),
                    },
                )
                returned = settle_ai_bankroll_to_pool_on_delete(
                    pid,
                    bankroll_repo=getattr(extensions, 'bankroll_repo', None),
                    chip_ledger_repo=getattr(extensions, 'chip_ledger_repo', None),
                )
                if returned:
                    logger.info(
                        "[CASH] persona delete %r (pid=%s): returned %d chips to pool",
                        name, pid, returned,
                    )
        except Exception as e:
            logger.warning("[CASH] persona-delete settle failed for %r: %s", name, e)

        # Delete associated avatar images
        extensions.personality_repo.delete_avatar_images(name)

        # Delete the personality
        deleted = extensions.personality_repo.delete_personality(name)

        if not deleted:
            return jsonify({'success': False, 'error': f'Personality {name} not found'})

        return jsonify({'success': True, 'message': f'Personality {name} deleted successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


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
        personality_config = extensions.personality_generator.get_personality(name)
        if not personality_config:
            return jsonify({'success': False, 'error': f'Personality {name} not found'}), 404

        reference_image_id = extensions.personality_generator.get_reference_image_id(name)

        return jsonify({'success': True, 'reference_image_id': reference_image_id})
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
        current_user = extensions.auth_manager.get_current_user()
        if not current_user:
            return jsonify(
                {'success': False, 'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}
            ), 401

        user_id = current_user.get('id')
        auth_service = get_authorization_service()
        is_admin = auth_service and auth_service.has_permission(user_id, 'can_access_admin_tools')
        owner_id = extensions.personality_repo.get_personality_owner(name)
        if owner_id and owner_id != user_id and not is_admin:
            return jsonify({'success': False, 'error': 'Permission denied'}), 403
        if not owner_id and not is_admin:
            return jsonify(
                {'success': False, 'error': 'Only admins can edit system personalities'}
            ), 403

        data = request.json
        reference_image_id = data.get('reference_image_id')

        # Check if personality exists
        personality_config = extensions.personality_generator.get_personality(name)
        if not personality_config:
            return jsonify({'success': False, 'error': f'Personality {name} not found'}), 404

        # Update reference image ID
        extensions.personality_generator.set_reference_image_id(name, reference_image_id)

        return jsonify(
            {
                'success': True,
                'message': f'Reference image {"set" if reference_image_id else "cleared"} for {name}',
                'reference_image_id': reference_image_id,
            }
        )
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
        # PRH-7: theme generation uses the expensive ASSISTANT tier and
        # previously required no real account (a guest qualified). Require a
        # signed-in, non-guest user.
        gen_user = extensions.auth_manager.get_current_user()
        if not gen_user or not gen_user.get('id'):
            return jsonify({'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401
        if is_guest(gen_user):
            return jsonify(
                {'error': 'Sign in to generate themed games', 'code': 'GUEST_FORBIDDEN'}
            ), 403

        data = request.json
        theme = data.get('theme')
        theme_name = data.get('themeName')
        description = data.get('description')

        if not theme:
            return jsonify({'error': 'Theme is required'}), 400

        # PRH-27: all three are user free text fed into the ASSISTANT-tier
        # generation prompt — moderate the combined input.
        flagged = _moderation_error(' '.join(filter(None, [theme, theme_name, description])))
        if flagged:
            return flagged

        # Load personality names from database filtered by user visibility
        current_user = extensions.auth_manager.get_current_user()
        user_id = current_user.get('id') if current_user else None
        # A themed game auto-rosters from this pool, so it must only draw
        # circulating personas (+ the user's own) — never a demoted sim/test
        # zombie (v123). Same gate as the opponent picker above.
        db_personalities = extensions.personality_repo.list_personalities(
            limit=200, user_id=user_id, circulating_only=True
        )
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
        current_user = extensions.auth_manager.get_current_user()
        owner_id = current_user.get('id') if current_user else None

        client = LLMClient(
            model=config.get_assistant_model(), provider=config.get_assistant_provider()
        )
        messages = [
            {
                "role": "system",
                "content": "You are a game designer selecting personalities for themed poker games.",
            },
            {"role": "user", "content": prompt},
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
                    k: result[k]
                    for k in (
                        'description',
                        'game_mode',
                        'starting_stack',
                        'big_blind',
                        'blind_growth',
                        'blinds_increase',
                        'max_blind',
                    )
                    if k in result
                }

            valid_personalities = _validate_theme_personalities(
                personalities_list, personality_sample
            )

            if len(valid_personalities) < 3:
                logger.warning(
                    f"Theme generation returned insufficient valid personalities ({len(valid_personalities)}), using random fallback"
                )
                valid_personalities = random.sample(
                    personality_sample, min(4, len(personality_sample))
                )

            game_settings = _validate_theme_game_settings(game_settings)

            return jsonify(
                {'success': True, 'personalities': valid_personalities[:5], **game_settings}
            )

        except json.JSONDecodeError as e:
            logger.error(
                f"Failed to parse theme generation response: {e}. Response was: {response_content}"
            )
            logger.warning("Theme generation using random fallback due to JSON parse error")
            personalities = random.sample(personality_sample, min(4, len(personality_sample)))
            return jsonify({'success': True, 'personalities': personalities, 'fallback': True})

    except Exception as e:
        logger.error(f"Error generating theme: {e}")
        logger.warning("Theme generation using random fallback due to exception")
        try:
            all_personalities = list(get_celebrities())
            personalities = random.sample(all_personalities, min(4, len(all_personalities)))
            return jsonify({'success': True, 'personalities': personalities, 'fallback': True})
        except Exception:
            return jsonify({'error': 'Failed to generate theme'}), 500


@personality_bp.route('/api/generate_personality', methods=['POST'])
@limiter.limit(config.RATE_LIMIT_GENERATE_PERSONALITY)
def generate_personality():
    """Generate a new personality using AI, owned by the current user.

    Generated personalities are saved to the database (source of truth).
    """
    try:
        from poker.personality_generator import PersonalityGenerator

        current_user = extensions.auth_manager.get_current_user()
        if not current_user:
            return jsonify(
                {'success': False, 'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}
            ), 401
        # PRH-7: personality generation uses the expensive ASSISTANT tier.
        # Guests qualified before (a fresh cookie minted a fresh quota), so
        # require a real signed-in account.
        if is_guest(current_user):
            return jsonify(
                {
                    'success': False,
                    'error': 'Sign in to generate personalities',
                    'code': 'GUEST_FORBIDDEN',
                }
            ), 403

        data = request.json
        name = data.get('name', '').strip()

        if not name:
            return jsonify({'success': False, 'error': 'Name is required'})

        flagged = _moderation_error(name)
        if flagged:
            return flagged

        force_generate = data.get('force', False)

        # Check for name collision (skip if force-regenerating existing personality)
        if not force_generate:
            existing = extensions.personality_repo.load_personality(name)
            if existing:
                return jsonify(
                    {'success': False, 'error': 'A personality with this name already exists'}
                ), 409

        generator = PersonalityGenerator()

        # This generates and saves to database automatically
        personality_config = generator.get_personality(
            name=name,
            force_generate=force_generate,
            owner_id=current_user['id'],
        )

        return jsonify(
            {
                'success': True,
                'personality': personality_config,
                'name': name,
                'message': f'Successfully generated personality for {name}',
            }
        )

    except Exception as e:
        return jsonify(
            {
                'success': False,
                'error': str(e),
                'message': 'Failed to generate personality. Please check your OpenAI API key.',
            }
        )


@personality_bp.route('/api/personality/<name>/visibility', methods=['PUT'])
def update_personality_visibility(name):
    """Set visibility for a personality.

    PRH-27: publishing is admin-only at this stage. A non-admin owner may set
    their own personality to 'private'; only admins can set 'public' or
    'disabled' (a public personality's user-chosen name + LLM-generated bio is
    cross-user content, so it goes through admin review rather than self-serve).
    """
    try:
        current_user = extensions.auth_manager.get_current_user()
        if not current_user:
            return jsonify(
                {'success': False, 'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}
            ), 401

        user_id = current_user.get('id')
        auth_service = get_authorization_service()
        is_admin = auth_service and auth_service.has_permission(user_id, 'can_access_admin_tools')

        data = request.json
        visibility = data.get('visibility')

        if visibility not in ('public', 'private', 'disabled'):
            return jsonify(
                {
                    'success': False,
                    'error': 'Invalid visibility. Must be public, private, or disabled.',
                }
            ), 400

        # PRH-27: publishing (public) and disabling are admin-only. Non-admin
        # owners can only set their own personality back to 'private'.
        if visibility in ('public', 'disabled') and not is_admin:
            return jsonify(
                {
                    'success': False,
                    'error': (
                        'Publishing personalities is admin-only right now. '
                        'You can set your own to private.'
                    ),
                    'code': 'ADMIN_REQUIRED_FOR_PUBLIC',
                }
            ), 403

        # Check ownership: must be owner or admin
        owner_id = extensions.personality_repo.get_personality_owner(name)
        if not is_admin and owner_id != user_id:
            return jsonify(
                {
                    'success': False,
                    'error': 'You can only change visibility of your own personalities',
                }
            ), 403

        updated = extensions.personality_repo.set_visibility(name, visibility)
        if not updated:
            return jsonify({'success': False, 'error': f'Personality {name} not found'}), 404

        return jsonify(
            {'success': True, 'message': f'Personality {name} visibility set to {visibility}'}
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# --- Bankroll knobs (cash mode) ---


def _resolve_personality_id_or_404(name):
    """Look up the stable personality_id for a display name.

    Returns (personality_id, None) on success or (None, (json_body, status))
    on failure so the caller can short-circuit with the error tuple.
    """
    pid = extensions.personality_repo.resolve_name_to_personality_id(name)
    if not pid:
        return None, ({'success': False, 'error': f'Personality {name} not found'}, 404)
    return pid, None


def _knobs_to_dict(knobs: BankrollKnobs) -> dict:
    """Serialize BankrollKnobs to the JSON shape consumed by the admin UI."""
    return {
        'starting_bankroll': knobs.starting_bankroll,
        'bankroll_rate': knobs.bankroll_rate,
        'buy_in_multiplier': knobs.buy_in_multiplier,
        'stake_comfort_zone': knobs.stake_comfort_zone,
    }


@personality_bp.route('/api/personality/<name>/bankroll-knobs', methods=['GET'])
def get_bankroll_knobs(name):
    """GET the per-personality bankroll knobs.

    Returns the knobs (with defaults filled in for any missing keys)
    plus the AI's current live bankroll (projection applied) so the
    admin UI can show actual state alongside the editable knobs.

    Admin-only — knobs control the cash-mode economy globally and
    aren't a per-user personality field.
    """
    try:
        current_user = extensions.auth_manager.get_current_user()
        if not current_user:
            return jsonify(
                {'success': False, 'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}
            ), 401

        user_id = current_user.get('id')
        auth_service = get_authorization_service()
        is_admin = auth_service and auth_service.has_permission(user_id, 'can_access_admin_tools')
        if not is_admin:
            return jsonify({'success': False, 'error': 'Admin access required'}), 403

        pid, err = _resolve_personality_id_or_404(name)
        if err is not None:
            return jsonify(err[0]), err[1]

        knobs = extensions.bankroll_repo.load_personality_knobs(pid)
        # Admin route — sum stored chips across every sandbox this
        # personality has a bankroll row in. `current_bankroll` here
        # is the cross-sandbox view, not a single save-file's value.
        # Per-sandbox bankroll inspection lives on the future
        # per-sandbox admin surface (Phase 2.5+).
        import sqlite3

        with sqlite3.connect(extensions.persistence_db_path) as _conn:
            row = _conn.execute(
                "SELECT COALESCE(SUM(chips), 0) FROM ai_bankroll_state " "WHERE personality_id = ?",
                (pid,),
            ).fetchone()
            current_bankroll = int(row[0] or 0) if row else None

        return jsonify(
            {
                'success': True,
                'name': name,
                'personality_id': pid,
                'knobs': _knobs_to_dict(knobs),
                'defaults': _knobs_to_dict(BANKROLL_KNOB_DEFAULTS),
                'current_bankroll': current_bankroll,  # None if no row yet
            }
        )
    except Exception as e:
        logger.exception("Error loading bankroll knobs for %r", name)
        return jsonify({'success': False, 'error': str(e)}), 500


@personality_bp.route('/api/personality/<name>/bankroll-knobs', methods=['PUT'])
def update_bankroll_knobs(name):
    """PUT a partial knob update; merges into config_json.bankroll_knobs.

    Request body: a JSON object containing any subset of:
      - starting_bankroll (int)
      - bankroll_rate (int)
      - buy_in_multiplier (float)
      - stake_comfort_zone (str)

    Missing keys fall back to the current stored knob value (which
    itself falls back to BANKROLL_KNOB_DEFAULTS), so a partial PUT
    only touches the fields the client sent.

    Admin-only.
    """
    try:
        current_user = extensions.auth_manager.get_current_user()
        if not current_user:
            return jsonify(
                {'success': False, 'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}
            ), 401

        user_id = current_user.get('id')
        auth_service = get_authorization_service()
        is_admin = auth_service and auth_service.has_permission(user_id, 'can_access_admin_tools')
        if not is_admin:
            return jsonify({'success': False, 'error': 'Admin access required'}), 403

        pid, err = _resolve_personality_id_or_404(name)
        if err is not None:
            return jsonify(err[0]), err[1]

        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify({'success': False, 'error': 'Request body must be a JSON object'}), 400

        current = extensions.bankroll_repo.load_personality_knobs(pid)

        # Validate types per-field. Anything not provided keeps the
        # current stored value — partial updates are the norm.
        try:
            new_knobs = BankrollKnobs(
                starting_bankroll=int(payload.get('starting_bankroll', current.starting_bankroll)),
                bankroll_rate=int(payload.get('bankroll_rate', current.bankroll_rate)),
                buy_in_multiplier=float(
                    payload.get('buy_in_multiplier', current.buy_in_multiplier)
                ),
                stake_comfort_zone=str(
                    payload.get('stake_comfort_zone', current.stake_comfort_zone)
                ),
            )
        except (TypeError, ValueError) as e:
            return jsonify(
                {
                    'success': False,
                    'error': f'Invalid knob value: {e}',
                }
            ), 400

        # Light range validation — keep the floor at 0 for caps/rates so
        # we don't produce negative-debit math, and require multipliers > 0.
        if new_knobs.starting_bankroll < 0:
            return jsonify({'success': False, 'error': 'starting_bankroll must be >= 0'}), 400
        if new_knobs.bankroll_rate < 0:
            return jsonify({'success': False, 'error': 'bankroll_rate must be >= 0'}), 400
        if new_knobs.buy_in_multiplier <= 0:
            return jsonify({'success': False, 'error': 'buy_in_multiplier must be > 0'}), 400

        saved = extensions.bankroll_repo.save_personality_knobs(pid, new_knobs)
        if not saved:
            return jsonify(
                {
                    'success': False,
                    'error': f'Personality {name} not found in database',
                }
            ), 404

        return jsonify(
            {
                'success': True,
                'name': name,
                'personality_id': pid,
                'knobs': _knobs_to_dict(new_knobs),
            }
        )
    except Exception as e:
        logger.exception("Error updating bankroll knobs for %r", name)
        return jsonify({'success': False, 'error': str(e)}), 500


# --- Borrower profile (staking system, Phase 5) ---


def _read_personality_config(pid):
    """Helper — load the raw config dict for a personality. Returns
    {} on any error so callers can default-fill missing pieces."""
    import sqlite3

    with sqlite3.connect(extensions.persistence_db_path) as conn:
        row = conn.execute(
            "SELECT config_json FROM personalities WHERE personality_id = ?",
            (pid,),
        ).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row[0]) or {}
        except (TypeError, ValueError):
            return {}


@personality_bp.route('/api/personality/<name>/borrower-profile', methods=['GET'])
def get_borrower_profile(name):
    """GET the per-personality borrower profile + ego-derivation context.

    Returns:
      - `willing`: does this personality accept stakes at all?
      - `willingness_threshold`: the EFFECTIVE value the system uses
        (either the explicit override or the ego-derived default).
      - `willingness_threshold_explicit`: the override value if set in
        config_json, else null. Lets the UI distinguish "this AI has
        a hand-tuned threshold" from "this is auto-derived from ego."
      - `ego_derived_threshold`: what the ego anchor would yield. Used
        for the "Reset to ego-derived" UI affordance.
      - `ego`: the anchor value — surfaced for context so admins can
        see why the derived value lands where it does.
      - `defaults`: the BORROWER_PROFILE_DEFAULTS shape for hard fallback.

    Admin-only.
    """
    try:
        current_user = extensions.auth_manager.get_current_user()
        if not current_user:
            return jsonify(
                {'success': False, 'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}
            ), 401

        user_id = current_user.get('id')
        auth_service = get_authorization_service()
        is_admin = auth_service and auth_service.has_permission(user_id, 'can_access_admin_tools')
        if not is_admin:
            return jsonify({'success': False, 'error': 'Admin access required'}), 403

        pid, err = _resolve_personality_id_or_404(name)
        if err is not None:
            return jsonify(err[0]), err[1]

        from cash_mode.staker_profile import (
            BORROWER_PROFILE_DEFAULTS,
            compute_default_willingness_threshold,
        )

        config = _read_personality_config(pid)
        anchors = config.get('anchors') if isinstance(config, dict) else None
        ego = 0.5
        if isinstance(anchors, dict):
            try:
                ego = float(anchors.get('ego', 0.5))
            except (TypeError, ValueError):
                ego = 0.5

        bp_sub = config.get('borrower_profile') if isinstance(config, dict) else None
        explicit = None
        if isinstance(bp_sub, dict) and 'willingness_threshold' in bp_sub:
            try:
                explicit = float(bp_sub['willingness_threshold'])
            except (TypeError, ValueError):
                explicit = None

        # Effective value (mirrors load_borrower_profile's derivation
        # logic) so the admin UI sees exactly what the staking engine
        # would use at evaluation time.
        profile = extensions.bankroll_repo.load_borrower_profile(pid)
        ego_derived = compute_default_willingness_threshold(ego)

        return jsonify(
            {
                'success': True,
                'name': name,
                'personality_id': pid,
                'willing': profile.willing,
                'willingness_threshold': profile.willingness_threshold,
                'willingness_threshold_explicit': explicit,
                'ego_derived_threshold': ego_derived,
                'ego': ego,
                'defaults': {
                    'willing': BORROWER_PROFILE_DEFAULTS.willing,
                    'willingness_threshold': BORROWER_PROFILE_DEFAULTS.willingness_threshold,
                },
            }
        )
    except Exception as e:
        logger.exception("Error loading borrower profile for %r", name)
        return jsonify({'success': False, 'error': str(e)}), 500


@personality_bp.route('/api/personality/<name>/borrower-profile', methods=['PUT'])
def update_borrower_profile(name):
    """PUT the per-personality borrower profile.

    Request body:
      - `willing` (bool, required)
      - `willingness_threshold` (float | null):
          - omitted or null → clear the explicit override; loader
            falls back to ego-derivation
          - float in [0.0, 1.0] → store as explicit override

    Returns the same shape as GET so the UI can swap state in place
    without a re-fetch.

    Admin-only.
    """
    try:
        current_user = extensions.auth_manager.get_current_user()
        if not current_user:
            return jsonify(
                {'success': False, 'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}
            ), 401

        user_id = current_user.get('id')
        auth_service = get_authorization_service()
        is_admin = auth_service and auth_service.has_permission(user_id, 'can_access_admin_tools')
        if not is_admin:
            return jsonify({'success': False, 'error': 'Admin access required'}), 403

        pid, err = _resolve_personality_id_or_404(name)
        if err is not None:
            return jsonify(err[0]), err[1]

        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify({'success': False, 'error': 'Request body must be a JSON object'}), 400
        if 'willing' not in payload:
            return jsonify({'success': False, 'error': 'willing is required'}), 400
        if not isinstance(payload['willing'], bool):
            return jsonify({'success': False, 'error': 'willing must be a boolean'}), 400

        # `willingness_threshold` is tri-state: missing key → keep
        # current stored override; explicit null → clear; float → set.
        if 'willingness_threshold' in payload:
            wt = payload['willingness_threshold']
            if wt is None:
                threshold_override = None
            else:
                try:
                    threshold_override = float(wt)
                except (TypeError, ValueError):
                    return jsonify(
                        {
                            'success': False,
                            'error': 'willingness_threshold must be a number or null',
                        }
                    ), 400
                if threshold_override < 0.0 or threshold_override > 1.0:
                    return jsonify(
                        {
                            'success': False,
                            'error': 'willingness_threshold must lie in [0.0, 1.0]',
                        }
                    ), 400
        else:
            # Read current explicit value so we don't accidentally
            # clear it on a partial PUT that only set `willing`.
            config = _read_personality_config(pid)
            bp_sub = config.get('borrower_profile') if isinstance(config, dict) else None
            if isinstance(bp_sub, dict) and 'willingness_threshold' in bp_sub:
                try:
                    threshold_override = float(bp_sub['willingness_threshold'])
                except (TypeError, ValueError):
                    threshold_override = None
            else:
                threshold_override = None

        saved = extensions.bankroll_repo.save_borrower_profile(
            pid,
            willing=bool(payload['willing']),
            willingness_threshold=threshold_override,
        )
        if not saved:
            return jsonify(
                {
                    'success': False,
                    'error': f'Personality {name} not found in database',
                }
            ), 404

        # Echo the post-save state — same shape as GET.
        from cash_mode.staker_profile import (
            BORROWER_PROFILE_DEFAULTS,
            compute_default_willingness_threshold,
        )

        config = _read_personality_config(pid)
        anchors = config.get('anchors') if isinstance(config, dict) else None
        ego = 0.5
        if isinstance(anchors, dict):
            try:
                ego = float(anchors.get('ego', 0.5))
            except (TypeError, ValueError):
                ego = 0.5
        profile = extensions.bankroll_repo.load_borrower_profile(pid)
        ego_derived = compute_default_willingness_threshold(ego)

        return jsonify(
            {
                'success': True,
                'name': name,
                'personality_id': pid,
                'willing': profile.willing,
                'willingness_threshold': profile.willingness_threshold,
                'willingness_threshold_explicit': threshold_override,
                'ego_derived_threshold': ego_derived,
                'ego': ego,
                'defaults': {
                    'willing': BORROWER_PROFILE_DEFAULTS.willing,
                    'willingness_threshold': BORROWER_PROFILE_DEFAULTS.willingness_threshold,
                },
            }
        )
    except Exception as e:
        logger.exception("Error updating borrower profile for %r", name)
        return jsonify({'success': False, 'error': str(e)}), 500


@personality_bp.route('/api/personality/<name>/staker-profile', methods=['GET'])
def get_staker_profile(name):
    """GET the per-personality staker profile (the lender side of the
    backing system — what this AI offers when OTHERS ask for a stake).

    Returns the effective profile (per-field fallback to
    STAKER_PROFILE_DEFAULTS), the explicit-override sub-dict from
    config_json so the UI can distinguish "hand-tuned" from "defaulted",
    and the defaults block for UI hint text.

    Admin-only.
    """
    try:
        current_user = extensions.auth_manager.get_current_user()
        if not current_user:
            return jsonify(
                {'success': False, 'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}
            ), 401

        user_id = current_user.get('id')
        auth_service = get_authorization_service()
        is_admin = auth_service and auth_service.has_permission(user_id, 'can_access_admin_tools')
        if not is_admin:
            return jsonify({'success': False, 'error': 'Admin access required'}), 403

        pid, err = _resolve_personality_id_or_404(name)
        if err is not None:
            return jsonify(err[0]), err[1]

        from cash_mode.staker_profile import STAKER_PROFILE_DEFAULTS

        profile = extensions.bankroll_repo.load_staker_profile(pid)
        config = _read_personality_config(pid)
        # `explicit_sub`: the staker_profile sub-dict actually stored
        # in config_json. Lets the UI render "(default)" badges next
        # to fields the personality hasn't tuned yet.
        explicit_sub = None
        if isinstance(config, dict):
            sp = config.get('staker_profile')
            if isinstance(sp, dict):
                explicit_sub = sp

        return jsonify(
            {
                'success': True,
                'name': name,
                'personality_id': pid,
                'profile': {
                    'willing': profile.willing,
                    'max_loan_pct_of_bankroll': profile.max_loan_pct_of_bankroll,
                    'floor_anchor': profile.floor_anchor,
                    'rate_anchor': profile.rate_anchor,
                    'respect_floor': profile.respect_floor,
                    'heat_ceiling': profile.heat_ceiling,
                },
                'explicit': explicit_sub,
                'defaults': {
                    'willing': STAKER_PROFILE_DEFAULTS.willing,
                    'max_loan_pct_of_bankroll': STAKER_PROFILE_DEFAULTS.max_loan_pct_of_bankroll,
                    'floor_anchor': STAKER_PROFILE_DEFAULTS.floor_anchor,
                    'rate_anchor': STAKER_PROFILE_DEFAULTS.rate_anchor,
                    'respect_floor': STAKER_PROFILE_DEFAULTS.respect_floor,
                    'heat_ceiling': STAKER_PROFILE_DEFAULTS.heat_ceiling,
                },
            }
        )
    except Exception as e:
        logger.exception("Error loading staker profile for %r", name)
        return jsonify({'success': False, 'error': str(e)}), 500


@personality_bp.route('/api/personality/<name>/staker-profile', methods=['PUT'])
def update_staker_profile(name):
    """PUT the per-personality staker profile (full replacement).

    Request body — all six fields required (no partial updates; the
    UI sends the full effective profile back). Validates field ranges
    so the admin surface can't push values the offer engine doesn't
    expect:

      - `willing` (bool)
      - `max_loan_pct_of_bankroll` (float, [0.0, 1.0])
      - `floor_anchor` (float, [0.5, 3.0])  — repayment multiple
      - `rate_anchor` (float, [0.0, 1.0])  — sponsor's cut after floor
      - `respect_floor` (float, [-1.0, 1.0])
      - `heat_ceiling` (float, [0.0, 1.0])

    Returns the same shape as GET so the UI can swap state in place.

    Admin-only.
    """
    try:
        current_user = extensions.auth_manager.get_current_user()
        if not current_user:
            return jsonify(
                {'success': False, 'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}
            ), 401

        user_id = current_user.get('id')
        auth_service = get_authorization_service()
        is_admin = auth_service and auth_service.has_permission(user_id, 'can_access_admin_tools')
        if not is_admin:
            return jsonify({'success': False, 'error': 'Admin access required'}), 403

        pid, err = _resolve_personality_id_or_404(name)
        if err is not None:
            return jsonify(err[0]), err[1]

        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify({'success': False, 'error': 'Request body must be a JSON object'}), 400
        if 'willing' not in payload or not isinstance(payload['willing'], bool):
            return jsonify({'success': False, 'error': 'willing (bool) is required'}), 400

        # Validation ranges intentionally a bit wider than the
        # "typical" ranges in the generator prompt — admins should
        # be able to push edge cases for testing without the route
        # second-guessing intent.
        validators = (
            ('max_loan_pct_of_bankroll', 0.0, 1.0),
            ('floor_anchor', 0.5, 3.0),
            ('rate_anchor', 0.0, 1.0),
            ('respect_floor', -1.0, 1.0),
            ('heat_ceiling', 0.0, 1.0),
        )
        coerced = {}
        for field, lo, hi in validators:
            if field not in payload:
                return jsonify({'success': False, 'error': f'{field} is required'}), 400
            try:
                value = float(payload[field])
            except (TypeError, ValueError):
                return jsonify({'success': False, 'error': f'{field} must be a number'}), 400
            if value < lo or value > hi:
                return jsonify(
                    {
                        'success': False,
                        'error': f'{field} must lie in [{lo}, {hi}]',
                    }
                ), 400
            coerced[field] = value

        from cash_mode.staker_profile import STAKER_PROFILE_DEFAULTS, StakerProfile

        new_profile = StakerProfile(
            willing=bool(payload['willing']),
            max_loan_pct_of_bankroll=coerced['max_loan_pct_of_bankroll'],
            floor_anchor=coerced['floor_anchor'],
            rate_anchor=coerced['rate_anchor'],
            respect_floor=coerced['respect_floor'],
            heat_ceiling=coerced['heat_ceiling'],
        )
        saved = extensions.bankroll_repo.save_staker_profile(pid, new_profile)
        if not saved:
            return jsonify(
                {
                    'success': False,
                    'error': f'Personality {name} not found in database',
                }
            ), 404

        # Echo post-save state — same shape as GET.
        profile = extensions.bankroll_repo.load_staker_profile(pid)
        config = _read_personality_config(pid)
        explicit_sub = config.get('staker_profile') if isinstance(config, dict) else None

        return jsonify(
            {
                'success': True,
                'name': name,
                'personality_id': pid,
                'profile': {
                    'willing': profile.willing,
                    'max_loan_pct_of_bankroll': profile.max_loan_pct_of_bankroll,
                    'floor_anchor': profile.floor_anchor,
                    'rate_anchor': profile.rate_anchor,
                    'respect_floor': profile.respect_floor,
                    'heat_ceiling': profile.heat_ceiling,
                },
                'explicit': explicit_sub if isinstance(explicit_sub, dict) else None,
                'defaults': {
                    'willing': STAKER_PROFILE_DEFAULTS.willing,
                    'max_loan_pct_of_bankroll': STAKER_PROFILE_DEFAULTS.max_loan_pct_of_bankroll,
                    'floor_anchor': STAKER_PROFILE_DEFAULTS.floor_anchor,
                    'rate_anchor': STAKER_PROFILE_DEFAULTS.rate_anchor,
                    'respect_floor': STAKER_PROFILE_DEFAULTS.respect_floor,
                    'heat_ceiling': STAKER_PROFILE_DEFAULTS.heat_ceiling,
                },
            }
        )
    except Exception as e:
        logger.exception("Error updating staker profile for %r", name)
        return jsonify({'success': False, 'error': str(e)}), 500
