"""
Backfill visual identity fields for existing personalities.

Generates identity/appearance/apparel via LLM for personalities that
lack visual_identity data. Safe to run multiple times — skips
personalities that already have complete visual identity.

Usage:
    # Dry run — show what would be updated
    python3 scripts/backfill_visual_identities.py --dry-run

    # Backfill all personalities
    python3 scripts/backfill_visual_identities.py

    # Backfill a single personality
    python3 scripts/backfill_visual_identities.py --name "Abraham Lincoln"

    # Run inside Docker
    docker compose exec backend python -m scripts.backfill_visual_identities
"""
import json
import logging
import sys
from pathlib import Path
from typing import Optional

# Add project root to path when run as script
project_root = str(Path(__file__).parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.llm import LLMClient, CallType
from core.llm.settings import get_default_model, get_default_provider
from poker.repositories import SchemaManager, PersonalityRepository

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

VISUAL_IDENTITY_PROMPT = """
Generate a visual identity for the character "{name}" for avatar image generation.

Respond with JSON containing three fields:
- identity: Their name PLUS a brief description of who they are / what they're known for.
  Always include the name. Image models often don't recognize names alone, so the description
  gives the model enough context to render the right person.
- appearance: Physical features in 10-15 words.
  Include: build/body type, hair style and color, facial hair, distinctive facial features, skin tone, approximate age.
- apparel: Clothing and accessories in 8-12 words. Should be IN CHARACTER — not a generic suit!
  Include: outfit style, key colors, distinctive accessories.

Examples:

For "Batman":
{{"identity": "Batman, the dark knight vigilante of Gotham City", "appearance": "muscular athletic build, chiseled jaw, dark hair, piercing eyes, medium skin tone", "apparel": "black armored suit with cape and cowl, utility belt, gauntlets"}}

For "Albert Einstein":
{{"identity": "Albert Einstein, the legendary wild-haired theoretical physicist", "appearance": "older gentleman, wild white hair, bushy mustache, kind wrinkled face, light skin tone", "apparel": "rumpled tweed jacket, wrinkled dress shirt, no tie"}}

For "Cleopatra":
{{"identity": "Cleopatra, the legendary Egyptian pharaoh queen", "appearance": "striking regal woman, dark hair in elaborate braids, kohl-lined eyes, olive skin tone, young", "apparel": "gold and lapis Egyptian royal gown, ornate collar necklace, cobra crown"}}
"""


def _get_db_path(db_path: Optional[str] = None) -> str:
    if db_path:
        return db_path
    if Path('/app/data').exists():
        return '/app/data/poker_games.db'
    return str(Path(__file__).parent.parent / 'data' / 'poker_games.db')


def _has_complete_visual_identity(config: dict) -> bool:
    vi = config.get('visual_identity', {})
    return all(vi.get(k) for k in ['identity', 'appearance', 'apparel'])


def backfill_personality(repo: PersonalityRepository, client: LLMClient,
                         name: str, config: dict) -> bool:
    """Backfill visual identity for a single personality.

    Returns True if updated, False if skipped.
    """
    if _has_complete_visual_identity(config):
        logger.info(f"  SKIP  {name} (already has visual_identity)")
        return False

    logger.info(f"  GEN   {name}...")

    try:
        prompt = VISUAL_IDENTITY_PROMPT.format(name=name)
        response = client.complete(
            messages=[
                {"role": "system", "content": "You generate visual descriptions for character avatars. Respond with JSON only."},
                {"role": "user", "content": prompt}
            ],
            json_format=True,
            call_type=CallType.PERSONALITY_GENERATION,
            player_name=name,
            prompt_template='visual_identity_backfill',
        )

        visual_identity = json.loads(response.content)

        if not all(k in visual_identity for k in ['identity', 'appearance', 'apparel']):
            logger.error(f"  FAIL  {name}: Invalid response (missing fields)")
            return False

        config['visual_identity'] = visual_identity
        repo.save_personality(name, config, source='backfill')

        logger.info(f"  OK    {name}: identity={visual_identity['identity']!r}")
        return True

    except Exception as e:
        logger.error(f"  FAIL  {name}: {e}")
        return False


def backfill_all(db_path: Optional[str] = None, dry_run: bool = False):
    """Backfill visual identities for all personalities in database."""
    db_path = _get_db_path(db_path)
    logger.info(f"Database: {db_path}")

    SchemaManager(db_path).ensure_schema()
    repo = PersonalityRepository(db_path)
    client = LLMClient(model=get_default_model(), provider=get_default_provider())

    personalities = repo.list_personalities(limit=1000, include_disabled=True)
    logger.info(f"Found {len(personalities)} personalities\n")

    updated = 0
    skipped = 0
    failed = 0

    for meta in personalities:
        name = meta['name']
        config = repo.load_personality(name)

        if not config:
            logger.warning(f"  WARN  {name}: Could not load config")
            failed += 1
            continue

        if dry_run:
            has_vi = _has_complete_visual_identity(config)
            logger.info(f"  {'SKIP' if has_vi else 'WOULD UPDATE'}  {name}")
            if has_vi:
                skipped += 1
            else:
                updated += 1
        else:
            if backfill_personality(repo, client, name, config):
                updated += 1
            else:
                skipped += 1

    logger.info(f"\nResults: {updated} updated, {skipped} skipped, {failed} failed")


def backfill_one(name: str, db_path: Optional[str] = None):
    """Backfill visual identity for a single personality."""
    db_path = _get_db_path(db_path)
    SchemaManager(db_path).ensure_schema()
    repo = PersonalityRepository(db_path)
    client = LLMClient(model=get_default_model(), provider=get_default_provider())

    config = repo.load_personality(name)
    if not config:
        logger.error(f"Personality not found: {name}")
        return

    backfill_personality(repo, client, name, config)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Backfill visual identities for avatar generation')
    parser.add_argument('--db', help='Database path')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be updated')
    parser.add_argument('--name', help='Only update a specific personality')
    args = parser.parse_args()

    if args.name:
        backfill_one(args.name, db_path=args.db)
    else:
        backfill_all(db_path=args.db, dry_run=args.dry_run)
