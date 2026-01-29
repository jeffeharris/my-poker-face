"""Avatar generation handler for AI player character images.

This handler manages background generation of avatar images for AI players.
Avatar images are stored in the SQLite database as BLOBs and served via
the /api/avatar/<personality>/<emotion> endpoint.

Generation strategy:
- Priority emotions (confident, thinking) are generated first per player
  so avatars appear quickly in the UI.
- Remaining emotions are generated afterward in the background.
- Socket.IO events are emitted after each individual emotion completes,
  allowing the frontend to update incrementally.
"""

import logging
import threading

from poker.character_images import (
    get_avatar_url,
    get_full_avatar_url,
    has_character_images,
    generate_character_images,
    get_character_image_service,
    EMOTIONS,
)
from ..extensions import socketio

logger = logging.getLogger(__name__)

# Emotions to generate first — these are needed immediately for display
PRIORITY_EMOTIONS = ["confident", "thinking"]


def _emit_avatar_update(game_id: str, player_name: str, emotion: str) -> None:
    """Emit a socket event after a single emotion image is generated.

    Args:
        game_id: The game identifier (socket room)
        player_name: Player whose avatar was generated
        emotion: The emotion that was generated
    """
    avatar_url = get_full_avatar_url(player_name, emotion) or get_avatar_url(player_name, emotion)
    if avatar_url:
        socketio.emit('avatar_update', {
            'player_name': player_name,
            'avatar_url': avatar_url,
            'avatar_emotion': emotion,
        }, room=game_id)


def generate_avatars_background(game_id: str, player_names: list) -> None:
    """Generate avatar images for players in the background.

    Generates priority emotions (confident, thinking) first for each player,
    emitting socket events after each emotion completes. Then generates
    remaining emotions.

    Args:
        game_id: The game identifier
        player_names: List of player names to generate avatars for
    """
    logger.info(f"Background avatar generation started for game={game_id}, players={player_names}")

    service = get_character_image_service()
    remaining_work: list[tuple[str, list[str]]] = []

    # Phase 1: Generate priority emotions for all players first
    for player_name in player_names:
        if has_character_images(player_name):
            logger.debug(f"Avatar images already exist for {player_name}")
            continue

        missing = service.get_missing_emotions(player_name)
        priority = [e for e in PRIORITY_EMOTIONS if e in missing]
        non_priority = [e for e in missing if e not in PRIORITY_EMOTIONS]

        if priority:
            logger.info(f"Generating priority avatars ({priority}) for {player_name}...")
            try:
                result = generate_character_images(player_name, emotions=priority, game_id=game_id)
                generated = result.get('generated', 0)
                if generated > 0:
                    for emotion in priority:
                        _emit_avatar_update(game_id, player_name, emotion)
                    logger.info(f"Priority avatar generation complete for {player_name} ({generated} images)")
                else:
                    logger.warning(f"Priority avatar generation failed for {player_name}: {result.get('errors', [])}")
            except Exception as e:
                logger.error(f"Error generating priority avatars for {player_name}: {e}")

        if non_priority:
            remaining_work.append((player_name, non_priority))

    # Phase 2: Generate remaining emotions for all players
    for player_name, emotions in remaining_work:
        logger.info(f"Generating remaining avatars ({emotions}) for {player_name}...")
        try:
            result = generate_character_images(player_name, emotions=emotions, game_id=game_id)
            generated = result.get('generated', 0)
            if generated > 0:
                for emotion in emotions:
                    _emit_avatar_update(game_id, player_name, emotion)
                logger.info(f"Remaining avatar generation complete for {player_name} ({generated} images)")
            else:
                logger.warning(f"Remaining avatar generation failed for {player_name}: {result.get('errors', [])}")
        except Exception as e:
            logger.error(f"Error generating remaining avatars for {player_name}: {e}")

    logger.info(f"Background avatar generation finished for game={game_id}")


def generate_single_emotion_background(game_id: str, player_name: str, emotion: str) -> None:
    """Generate a single emotion image in the background.

    Used for on-demand generation when a specific emotion is requested
    but not yet available. Emits a socket event when complete.

    Args:
        game_id: The game identifier
        player_name: Player name
        emotion: The emotion to generate
    """
    logger.info(f"On-demand avatar generation: {player_name}/{emotion} for game={game_id}")
    try:
        result = generate_character_images(player_name, emotions=[emotion], game_id=game_id)
        if result.get('generated', 0) > 0:
            _emit_avatar_update(game_id, player_name, emotion)
            logger.info(f"On-demand avatar ready: {player_name}/{emotion}")
        else:
            logger.warning(f"On-demand avatar failed for {player_name}/{emotion}: {result.get('errors', [])}")
    except Exception as e:
        logger.error(f"Error in on-demand avatar generation for {player_name}/{emotion}: {e}")


def start_background_avatar_generation(game_id: str, ai_player_names: list) -> None:
    """Start background thread to generate missing avatars.

    Args:
        game_id: The game identifier
        ai_player_names: List of AI player names to check/generate
    """
    players_needing_images = [
        name for name in ai_player_names
        if not has_character_images(name)
    ]

    if players_needing_images:
        logger.info(f"Starting background avatar generation for: {players_needing_images}")
        thread = threading.Thread(
            target=generate_avatars_background,
            args=(game_id, players_needing_images),
            daemon=True
        )
        thread.start()


def start_single_emotion_generation(game_id: str, player_name: str, emotion: str) -> None:
    """Start a background thread to generate a single missing emotion.

    Args:
        game_id: The game identifier
        player_name: Player name
        emotion: Emotion to generate
    """
    service = get_character_image_service()
    if service.get_avatar_url(player_name, emotion) is not None:
        return  # Already exists

    logger.info(f"Starting on-demand generation for {player_name}/{emotion}")
    thread = threading.Thread(
        target=generate_single_emotion_background,
        args=(game_id, player_name, emotion),
        daemon=True
    )
    thread.start()
