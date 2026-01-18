# Repository Layer Migration Guide

This guide documents how to migrate from the monolithic `GamePersistence` class to the new domain-specific repository architecture.

## Overview

The new architecture replaces the 2,972-line `poker/persistence.py` with:
- 10 domain-specific SQLite repositories
- Clean Protocol interfaces
- RepositoryFactory for dependency injection
- Preserved historical data (api_usage, prompt_captures, etc.)

## Prerequisites

- Code deployed with commit `60801d7` or later (contains the new repository architecture)
- Access to the database file (`poker_games.db`)

## Migration Steps

### Development Environment

```bash
# 1. Rebuild containers with new code
docker compose down && docker compose up -d --build

# 2. Dry run - see what will be migrated
docker compose exec backend python tools/migrate_to_new_schema.py \
  --source /app/data/poker_games.db \
  --target /app/data/poker_games_v2.db \
  --dry-run

# 3. Run actual migration
docker compose exec backend python tools/migrate_to_new_schema.py \
  --source /app/data/poker_games.db \
  --target /app/data/poker_games_v2.db \
  --include-optional

# 4. Verify migration
docker compose exec backend python tools/verify_migration.py \
  --old /app/data/poker_games.db \
  --new /app/data/poker_games_v2.db

# 5. Swap databases (if verification passes)
docker compose exec backend bash -c "
  cd /app/data && \
  cp poker_games.db poker_games_backup_\$(date +%Y%m%d).db && \
  mv poker_games.db poker_games_old.db && \
  mv poker_games_v2.db poker_games.db
"

# 6. Restart backend
docker compose restart backend

# 7. Test the application - start a game, play a hand
```

### Production Environment

```bash
# 1. SSH to production and create backup
ssh root@178.156.202.136 "cd /opt/poker && cp data/poker_games.db data/poker_games_backup_\$(date +%Y%m%d).db"

# 2. Deploy new code (from local machine)
./deploy.sh

# 3. Dry run
ssh root@178.156.202.136 "cd /opt/poker && docker compose -f docker-compose.prod.yml exec backend python tools/migrate_to_new_schema.py --source /app/data/poker_games.db --target /app/data/poker_games_v2.db --dry-run"

# 4. Run migration
ssh root@178.156.202.136 "cd /opt/poker && docker compose -f docker-compose.prod.yml exec backend python tools/migrate_to_new_schema.py --source /app/data/poker_games.db --target /app/data/poker_games_v2.db --include-optional"

# 5. Verify
ssh root@178.156.202.136 "cd /opt/poker && docker compose -f docker-compose.prod.yml exec backend python tools/verify_migration.py --old /app/data/poker_games.db --new /app/data/poker_games_v2.db"

# 6. Swap databases
ssh root@178.156.202.136 "cd /opt/poker/data && mv poker_games.db poker_games_old.db && mv poker_games_v2.db poker_games.db"

# 7. Restart backend
ssh root@178.156.202.136 "cd /opt/poker && docker compose -f docker-compose.prod.yml restart backend"

# 8. Verify site works
curl https://mypokerfacegame.com/health
```

## Data Preserved During Migration

The migration script preserves these critical tables:

| Table | Description | Typical Rows |
|-------|-------------|--------------|
| `api_usage` | LLM cost tracking history | 100-1000+ |
| `prompt_captures` | AI decision debugging | 100-500+ |
| `model_pricing` | Pricing SKUs | ~100 |
| `enabled_models` | Model configuration | ~20 |
| `player_decision_analysis` | Decision quality analysis | 100+ |
| `personalities` | Generated AI personalities | 20-100 |
| `avatar_images` | Generated character avatars | 20-100 |
| `player_career_stats` | Career statistics | varies |

With `--include-optional`, also migrates:
- `tournament_results`
- `tournament_standings`
- `users`
- `app_settings`

## Rollback Procedure

If issues arise after migration:

### Development
```bash
docker compose exec backend bash -c "
  cd /app/data && \
  mv poker_games.db poker_games_failed.db && \
  mv poker_games_old.db poker_games.db
"
docker compose restart backend
```

### Production
```bash
ssh root@178.156.202.136 "cd /opt/poker/data && mv poker_games.db poker_games_failed.db && mv poker_games_old.db poker_games.db"
ssh root@178.156.202.136 "cd /opt/poker && docker compose -f docker-compose.prod.yml restart backend"
```

## Verification Checklist

After migration, verify:

- [ ] `/health` endpoint returns OK
- [ ] Can start a new game
- [ ] Can play a hand (AI players respond)
- [ ] Existing personalities appear in selection
- [ ] Avatar images load correctly
- [ ] API usage tracking works (check after a few AI calls)

## Architecture Notes

### Backward Compatibility

The new code is **backward compatible**. Both systems run simultaneously:
- `persistence` (GamePersistence) - legacy, still works
- `repository_factory` (RepositoryFactory) - new, preferred

New code should use `get_repository_factory()` from `flask_app.extensions`.

### Repository Structure

```
poker/repositories/
├── database.py              # DatabaseContext
├── protocols.py             # Domain entities & interfaces
├── serialization.py         # Card/state helpers
├── factory.py               # RepositoryFactory
├── migrations/
│   └── schema/*.sql         # 10 schema files
└── sqlite/
    ├── game_repository.py
    ├── ai_memory_repository.py
    ├── personality_repository.py
    ├── emotional_state_repository.py
    ├── hand_history_repository.py
    ├── tournament_repository.py
    ├── llm_tracking_repository.py
    ├── debug_repository.py
    ├── experiment_repository.py
    └── config_repository.py
```

### Using the New Repositories

```python
from flask_app.extensions import get_repository_factory

# Get factory
factory = get_repository_factory()

# Use repositories
personality = factory.personality.find_by_name("Batman")
factory.llm_tracking.save_usage(usage_entity)
captures = factory.debug.list_prompt_captures(game_id="abc123")
```

## Troubleshooting

### "Table does not exist" errors
The schema wasn't initialized. For fresh databases, use:
```python
factory = RepositoryFactory(db_path, initialize_schema=True)
```

### Row count mismatch after migration
Check if columns differ between old and new schemas. The migration only copies columns that exist in both tables.

### Import errors
Ensure numpy is installed: `pip install numpy`
