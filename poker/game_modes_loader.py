"""
Game Modes Loader - Syncs game mode presets from YAML configuration.

This module loads game mode definitions from config/game_modes.yaml and
upserts them into the prompt_presets table on application startup.

Unlike pricing sync (seed-if-missing), game mode sync always overwrites
system presets so that YAML remains the single source of truth.
User-created presets (is_system=FALSE) are never touched.
"""

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Dict, Any, Optional

import yaml

logger = logging.getLogger(__name__)


def get_default_config_path() -> str:
    """Get the default game modes config path."""
    if Path('/app/config/game_modes.yaml').exists():
        return '/app/config/game_modes.yaml'
    # Local development
    return str(Path(__file__).parent.parent / 'config' / 'game_modes.yaml')


def get_default_db_path() -> str:
    """Get the default database path based on environment."""
    if Path('/app/data').exists():
        return '/app/data/poker_games.db'
    return str(Path(__file__).parent.parent / 'poker_games.db')


def load_game_modes_yaml(config_path: Optional[str] = None) -> dict:
    """Load and parse the game modes YAML file.

    Args:
        config_path: Path to game_modes.yaml (default: auto-detect)

    Returns:
        Parsed YAML as dict, or empty dict on error
    """
    config_path = config_path or get_default_config_path()
    try:
        with open(config_path, 'r') as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning(f"Game modes config not found: {config_path}")
        return {}
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse game modes config: {e}")
        return {}


def get_preset_configs(config_path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Get preset configurations from YAML without requiring a database.

    Returns a dict mapping mode name to its prompt_config dict.
    Useful for from_mode_name() fallback when no DB is available.

    Args:
        config_path: Path to game_modes.yaml (default: auto-detect)

    Returns:
        Dict like {"casual": {}, "standard": {"show_equity_always": True}, ...}
    """
    config = load_game_modes_yaml(config_path)
    presets = config.get('presets', {})
    return {
        name: preset.get('prompt_config', {})
        for name, preset in presets.items()
    }


def sync_game_modes_from_yaml(
    db_path: Optional[str] = None,
    config_path: Optional[str] = None
) -> dict:
    """Upsert system game mode presets from YAML into prompt_presets table.

    This function always overwrites system presets (is_system=TRUE) so that
    YAML is the single source of truth. User-created presets are not touched.

    Safe to call on every app startup.

    Args:
        db_path: Path to SQLite database (default: auto-detect)
        config_path: Path to game_modes.yaml (default: auto-detect)

    Returns:
        Dict with stats: {"inserted": N, "updated": M, "errors": E}
    """
    db_path = db_path or get_default_db_path()
    config_path = config_path or get_default_config_path()

    stats = {"inserted": 0, "updated": 0, "errors": 0}

    config = load_game_modes_yaml(config_path)
    if not config:
        logger.info("No game modes config loaded, skipping sync")
        return stats

    presets = config.get('presets', {})
    if not presets:
        logger.warning("Game modes config has no presets section")
        return stats

    version = config.get('version', 'unknown')
    logger.info(f"Syncing game modes from config version {version}")

    try:
        with sqlite3.connect(db_path) as conn:
            # Check if prompt_presets table exists
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='prompt_presets'"
            )
            if not cursor.fetchone():
                logger.warning("prompt_presets table doesn't exist yet, skipping game mode sync")
                return stats

            for name, preset_data in presets.items():
                description = preset_data.get('description', '')
                prompt_config = preset_data.get('prompt_config', {})
                prompt_config_json = json.dumps(prompt_config)

                # Extract guidance_injection separately (stored in its own column)
                guidance_injection = prompt_config.pop('guidance_injection', '') if isinstance(prompt_config, dict) else ''
                # Re-serialize without guidance_injection in prompt_config
                prompt_config_json = json.dumps(prompt_config)

                try:
                    # Try insert first
                    conn.execute("""
                        INSERT INTO prompt_presets
                            (name, description, prompt_config, guidance_injection, is_system, owner_id)
                        VALUES (?, ?, ?, ?, TRUE, 'system')
                    """, (name, description, prompt_config_json, guidance_injection))
                    stats["inserted"] += 1
                    logger.info(f"Inserted system preset '{name}'")
                except sqlite3.IntegrityError:
                    # Name already exists — update if it's a system preset
                    cursor = conn.execute("""
                        UPDATE prompt_presets
                        SET description = ?,
                            prompt_config = ?,
                            guidance_injection = ?,
                            is_system = TRUE,
                            owner_id = 'system',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE name = ? AND is_system = TRUE
                    """, (description, prompt_config_json, guidance_injection, name))
                    if cursor.rowcount > 0:
                        stats["updated"] += 1
                        logger.debug(f"Updated system preset '{name}'")
                    else:
                        # Name exists but is_system=FALSE — user preset, don't touch
                        logger.debug(f"Skipped '{name}': user-created preset with same name")

            conn.commit()

    except sqlite3.Error as e:
        logger.error(f"Database error during game mode sync: {e}")
        stats["errors"] += 1

    logger.info(
        f"Game mode sync complete: {stats['inserted']} inserted, "
        f"{stats['updated']} updated, {stats['errors']} errors"
    )

    return stats


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.DEBUG)

    db = sys.argv[1] if len(sys.argv) > 1 else None
    config = sys.argv[2] if len(sys.argv) > 2 else None

    result = sync_game_modes_from_yaml(db, config)
    print(f"Game mode sync result: {result}")
