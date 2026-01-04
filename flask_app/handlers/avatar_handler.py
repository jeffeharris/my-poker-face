"""Avatar generation handler for AI player character images.

This handler manages background generation of avatar images for AI players.
Avatar images are stored in the SQLite database as BLOBs and served via
the /api/avatar/<personality>/<emotion> endpoint.
"""

import logging
import threading

from poker.character_images import get_avatar_url, has_character_images, generate_character_images
from ..extensions import socketio

logger = logging.getLogger(__name__)


def generate_avatars_background(game_id: str, player_names: list) -> None:
    """Generate avatar images for players in the background.

    Emits avatar_update events via Socket.IO when each player's images are ready.

    Args:
        game_id: The game identifier
        player_names: List of player names to generate avatars for
    """
    logger.info(f"Background avatar generation started for game={game_id}, players={player_names}")

    for player_name in player_names:
        if has_character_images(player_name):
            logger.debug(f"Avatar images already exist for {player_name}")
            continue

        logger.info(f"Generating avatars for {player_name}...")
        try:
            result = generate_character_images(player_name)
            if result.get('success') or result.get('generated', 0) > 0:
                avatar_url = get_avatar_url(player_name, 'confident')
                logger.info(f"Avatar generation complete for {player_name}: {avatar_url}")

                # Emit avatar update to the game room
                socketio.emit('avatar_update', {
                    'player_name': player_name,
                    'avatar_url': avatar_url,
                    'avatar_emotion': 'confident'
                }, room=game_id)
            else:
                logger.warning(f"Avatar generation failed for {player_name}: {result.get('errors', [])}")
        except Exception as e:
            logger.error(f"Error generating avatars for {player_name}: {e}")

    logger.info(f"Background avatar generation finished for game={game_id}")


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
