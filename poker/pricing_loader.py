"""
Pricing Loader - Seeds model pricing from YAML configuration.

This module loads base pricing from config/pricing.yaml into the database
on application startup. It uses a "seed-if-missing" strategy:
- If a (provider, model, unit) SKU has no entries, insert from YAML
- If entries exist (from dashboard or prior seeding), skip to preserve them

Dashboard edits take precedence via valid_from timestamps - the most recent
valid_from per SKU is used for cost calculations (waterfall lookup).
"""

import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


def get_default_db_path() -> str:
    """Get the default database path based on environment."""
    if Path('/app/data').exists():
        return '/app/data/poker_games.db'
    # Local development
    return str(Path(__file__).parent.parent / 'poker_games.db')


def get_default_config_path() -> str:
    """Get the default pricing config path."""
    if Path('/app/config/pricing.yaml').exists():
        return '/app/config/pricing.yaml'
    # Local development
    return str(Path(__file__).parent.parent / 'config' / 'pricing.yaml')


def load_pricing_yaml(config_path: str) -> dict:
    """Load and parse the pricing YAML file.

    Args:
        config_path: Path to pricing.yaml

    Returns:
        Parsed YAML as dict, or empty dict on error
    """
    try:
        with open(config_path, 'r') as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning(f"Pricing config not found: {config_path}")
        return {}
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse pricing config: {e}")
        return {}


def sku_exists(conn: sqlite3.Connection, provider: str, model: str, unit: str) -> bool:
    """Check if a pricing SKU already exists in the database.

    Args:
        conn: SQLite connection
        provider: Provider name (e.g., 'openai')
        model: Model name (e.g., 'gpt-4o')
        unit: Unit type (e.g., 'input_tokens_1m')

    Returns:
        True if any entry exists for this SKU (regardless of valid_from)
    """
    cursor = conn.execute(
        "SELECT 1 FROM model_pricing WHERE provider = ? AND model = ? AND unit = ? LIMIT 1",
        (provider, model, unit)
    )
    return cursor.fetchone() is not None


def insert_pricing(conn: sqlite3.Connection, provider: str, model: str, unit: str, cost: float) -> bool:
    """Insert a new pricing entry with NULL valid_from (base price).

    Args:
        conn: SQLite connection
        provider: Provider name
        model: Model name
        unit: Unit type
        cost: Cost value

    Returns:
        True if inserted, False on error
    """
    try:
        conn.execute(
            """
            INSERT INTO model_pricing (provider, model, unit, cost, valid_from, valid_until)
            VALUES (?, ?, ?, ?, NULL, NULL)
            """,
            (provider, model, unit, cost)
        )
        return True
    except sqlite3.IntegrityError:
        # Should not happen due to sku_exists check, but handle gracefully
        logger.debug(f"SKU already exists: {provider}/{model}/{unit}")
        return False


def sync_model_metadata(
    conn: sqlite3.Connection,
    provider: str,
    model: str,
    model_data: dict
) -> bool:
    """Sync model metadata (capabilities, display_name) to enabled_models table.

    Updates the capability columns and display_name based on model_data from YAML.

    Args:
        conn: SQLite connection
        provider: Provider name
        model: Model name
        model_data: Dict with capabilities, display_name, and pricing

    Returns:
        True if updated, False if model not found in enabled_models
    """
    capabilities = model_data.get('capabilities', {})
    display_name = model_data.get('display_name')

    # Map YAML capability names to database column names
    column_map = {
        'reasoning': 'supports_reasoning',
        'json': 'supports_json_mode',
        'img_gen': 'supports_image_gen',
        'img2img': 'supports_img2img',
    }

    # Build update values
    updates = []
    values = []

    # Add display_name if provided
    if display_name:
        updates.append("display_name = ?")
        values.append(display_name)

    # Add capabilities
    for yaml_key, db_column in column_map.items():
        if yaml_key in capabilities:
            updates.append(f"{db_column} = ?")
            values.append(1 if capabilities[yaml_key] else 0)

    if not updates:
        return False

    # Add WHERE clause values
    values.extend([provider, model])

    try:
        cursor = conn.execute(
            f"""
            UPDATE enabled_models
            SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP
            WHERE provider = ? AND model = ?
            """,
            values
        )
        return cursor.rowcount > 0
    except sqlite3.Error as e:
        logger.warning(f"Failed to update metadata for {provider}/{model}: {e}")
        return False


def sync_pricing_from_yaml(
    db_path: Optional[str] = None,
    config_path: Optional[str] = None
) -> dict:
    """Seed pricing from YAML if SKU doesn't exist in DB.

    This function is idempotent - safe to call on every app startup.
    It only inserts pricing for SKUs that have NO existing entries,
    preserving any dashboard edits or prior seeds.

    Also syncs model capabilities (reasoning, json, img_gen, img2img) to
    the enabled_models table.

    Args:
        db_path: Path to SQLite database (default: auto-detect)
        config_path: Path to pricing.yaml (default: auto-detect)

    Returns:
        Dict with stats: {"seeded": N, "skipped": M, "errors": E, "capabilities_updated": C}
    """
    db_path = db_path or get_default_db_path()
    config_path = config_path or get_default_config_path()

    stats = {"seeded": 0, "skipped": 0, "errors": 0, "capabilities_updated": 0}

    # Load YAML config
    config = load_pricing_yaml(config_path)
    if not config:
        logger.info("No pricing config loaded, skipping sync")
        return stats

    providers = config.get('providers', {})
    if not providers:
        logger.warning("Pricing config has no providers section")
        return stats

    version = config.get('version', 'unknown')
    logger.info(f"Syncing pricing from config version {version}")

    try:
        with sqlite3.connect(db_path) as conn:
            # Check if model_pricing table exists
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='model_pricing'"
            )
            if not cursor.fetchone():
                logger.warning("model_pricing table doesn't exist yet, skipping sync")
                return stats

            # Process each provider/model/unit
            for provider, models in providers.items():
                for model, model_data in models.items():
                    if not isinstance(model_data, dict):
                        continue

                    # Sync metadata (capabilities, display_name) to enabled_models
                    has_metadata = model_data.get('capabilities') or model_data.get('display_name')
                    if has_metadata:
                        if sync_model_metadata(conn, provider, model, model_data):
                            stats["capabilities_updated"] += 1
                            logger.debug(f"Updated metadata: {provider}/{model}")

                    # Process pricing units (skip non-pricing keys)
                    skip_keys = {'capabilities', 'display_name'}
                    for unit, cost in model_data.items():
                        if unit in skip_keys:
                            continue
                        if sku_exists(conn, provider, model, unit):
                            stats["skipped"] += 1
                        else:
                            if insert_pricing(conn, provider, model, unit, cost):
                                stats["seeded"] += 1
                                logger.debug(f"Seeded: {provider}/{model}/{unit} = ${cost}")
                            else:
                                stats["errors"] += 1

            conn.commit()

    except sqlite3.Error as e:
        logger.error(f"Database error during pricing sync: {e}")
        stats["errors"] += 1

    # Invalidate UsageTracker cache if any changes were made
    if stats["seeded"] > 0:
        try:
            from core.llm.tracking import UsageTracker
            UsageTracker.get_default().invalidate_pricing_cache()
            logger.info(f"Invalidated pricing cache after seeding {stats['seeded']} entries")
        except ImportError:
            logger.debug("UsageTracker not available, skipping cache invalidation")
        except Exception as e:
            logger.warning(f"Failed to invalidate pricing cache: {e}")

    logger.info(
        f"Pricing sync complete: {stats['seeded']} seeded, "
        f"{stats['skipped']} skipped, {stats['errors']} errors, "
        f"{stats['capabilities_updated']} capabilities updated"
    )

    return stats


def sync_enabled_models(
    db_path: Optional[str] = None,
    config_path: Optional[str] = None
) -> dict:
    """Sync enabled_models table with PROVIDER_MODELS config.

    Ensures all models in PROVIDER_MODELS exist in enabled_models table.
    Only adds missing models - does not remove or disable models not in config.

    Capabilities are pulled from pricing.yaml if available.

    Args:
        db_path: Path to SQLite database (default: auto-detect)
        config_path: Path to pricing.yaml for capabilities (default: auto-detect)

    Returns:
        Dict with stats: {"added": N, "skipped": M, "errors": E}
    """
    from core.llm import PROVIDER_MODELS, PROVIDER_CAPABILITIES

    db_path = db_path or get_default_db_path()
    config_path = config_path or get_default_config_path()

    stats = {"added": 0, "skipped": 0, "errors": 0}

    # Load capabilities from pricing.yaml
    config = load_pricing_yaml(config_path)
    pricing_providers = config.get('providers', {})

    try:
        with sqlite3.connect(db_path) as conn:
            # Check if enabled_models table exists
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='enabled_models'"
            )
            if not cursor.fetchone():
                logger.warning("enabled_models table doesn't exist yet, skipping sync")
                return stats

            # Get existing models
            cursor = conn.execute("SELECT provider, model FROM enabled_models")
            existing = {(row[0], row[1]) for row in cursor.fetchall()}

            # Check for supports_img2img column
            cursor = conn.execute("PRAGMA table_info(enabled_models)")
            columns = [row[1] for row in cursor.fetchall()]
            has_img2img = 'supports_img2img' in columns

            # Process each provider/model from config
            for provider, models in PROVIDER_MODELS.items():
                provider_caps = PROVIDER_CAPABILITIES.get(provider, {})
                is_image_only = provider_caps.get('image_only', False)

                for sort_order, model in enumerate(models):
                    if (provider, model) in existing:
                        stats["skipped"] += 1
                        continue

                    # Get metadata from pricing.yaml if available
                    model_data = pricing_providers.get(provider, {}).get(model, {})
                    caps = model_data.get('capabilities', {}) if isinstance(model_data, dict) else {}
                    display_name = model_data.get('display_name') if isinstance(model_data, dict) else None

                    # Determine capability values
                    supports_reasoning = 1 if caps.get('reasoning', False) else 0
                    supports_json = 1 if caps.get('json', False) else 0
                    supports_image_gen = 1 if caps.get('img_gen', False) or is_image_only else 0
                    supports_img2img = 1 if caps.get('img2img', False) else 0

                    # For text models without explicit caps, default json to provider capability
                    if not is_image_only and not caps:
                        supports_json = 1 if provider_caps.get('supports_json_mode', True) else 0
                        supports_reasoning = 1 if provider_caps.get('supports_reasoning', False) else 0

                    try:
                        if has_img2img:
                            conn.execute("""
                                INSERT INTO enabled_models
                                (provider, model, display_name, enabled, user_enabled, supports_reasoning,
                                 supports_json_mode, supports_image_gen, supports_img2img, sort_order)
                                VALUES (?, ?, ?, 1, 1, ?, ?, ?, ?, ?)
                            """, (provider, model, display_name, supports_reasoning, supports_json,
                                  supports_image_gen, supports_img2img, sort_order))
                        else:
                            conn.execute("""
                                INSERT INTO enabled_models
                                (provider, model, display_name, enabled, user_enabled, supports_reasoning,
                                 supports_json_mode, supports_image_gen, sort_order)
                                VALUES (?, ?, ?, 1, 1, ?, ?, ?, ?)
                            """, (provider, model, display_name, supports_reasoning, supports_json,
                                  supports_image_gen, sort_order))

                        stats["added"] += 1
                        logger.debug(f"Added model: {provider}/{model}")
                    except sqlite3.IntegrityError:
                        stats["skipped"] += 1
                    except sqlite3.Error as e:
                        logger.warning(f"Failed to add {provider}/{model}: {e}")
                        stats["errors"] += 1

            conn.commit()

    except sqlite3.Error as e:
        logger.error(f"Database error during enabled_models sync: {e}")
        stats["errors"] += 1

    logger.info(
        f"Enabled models sync complete: {stats['added']} added, "
        f"{stats['skipped']} skipped, {stats['errors']} errors"
    )

    return stats


if __name__ == '__main__':
    # Allow running directly for testing
    import sys
    logging.basicConfig(level=logging.DEBUG)

    db = sys.argv[1] if len(sys.argv) > 1 else None
    config = sys.argv[2] if len(sys.argv) > 2 else None

    result = sync_pricing_from_yaml(db, config)
    print(f"Pricing result: {result}")

    result = sync_enabled_models(db, config)
    print(f"Enabled models result: {result}")
