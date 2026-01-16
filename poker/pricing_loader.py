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


def sync_pricing_from_yaml(
    db_path: Optional[str] = None,
    config_path: Optional[str] = None
) -> dict:
    """Seed pricing from YAML if SKU doesn't exist in DB.

    This function is idempotent - safe to call on every app startup.
    It only inserts pricing for SKUs that have NO existing entries,
    preserving any dashboard edits or prior seeds.

    Args:
        db_path: Path to SQLite database (default: auto-detect)
        config_path: Path to pricing.yaml (default: auto-detect)

    Returns:
        Dict with stats: {"seeded": N, "skipped": M, "errors": E}
    """
    db_path = db_path or get_default_db_path()
    config_path = config_path or get_default_config_path()

    stats = {"seeded": 0, "skipped": 0, "errors": 0}

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
                for model, units in models.items():
                    for unit, cost in units.items():
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
    print(f"Result: {result}")
