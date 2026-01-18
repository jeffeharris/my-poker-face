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

## Pre-Deployment Schema Fixes

**IMPORTANT:** Before deploying the repository migration to production, run these SQL statements to add missing columns that the new code expects but weren't in the original schema.

### 1. opponent_models table (required for experiment runner)

The experiment runner queries `opponent_models` with explicit column names. Add these columns:

```sql
-- Run these in production before deployment
ALTER TABLE opponent_models ADD COLUMN hands_observed INTEGER DEFAULT 0;
ALTER TABLE opponent_models ADD COLUMN vpip REAL DEFAULT 0.5;
ALTER TABLE opponent_models ADD COLUMN pfr REAL DEFAULT 0.5;
ALTER TABLE opponent_models ADD COLUMN aggression_factor REAL DEFAULT 1.0;
ALTER TABLE opponent_models ADD COLUMN fold_to_cbet REAL DEFAULT 0.5;
ALTER TABLE opponent_models ADD COLUMN bluff_frequency REAL DEFAULT 0.3;
ALTER TABLE opponent_models ADD COLUMN showdown_win_rate REAL DEFAULT 0.5;
ALTER TABLE opponent_models ADD COLUMN recent_trend TEXT;
```

### 2. api_usage table (required for LLM tracking)

The LLM tracking module writes prompt metadata. Add these columns:

```sql
ALTER TABLE api_usage ADD COLUMN prompt_template TEXT;
ALTER TABLE api_usage ADD COLUMN prompt_version TEXT;
```

### Quick Fix Script

#### Production (run BEFORE deploying new code)
```bash
ssh root@178.156.202.136 "cd /opt/poker && docker compose -f docker-compose.prod.yml exec backend python -c '
import sqlite3
conn = sqlite3.connect(\"/app/data/poker_games.db\")

om_cols = [
    (\"hands_observed\", \"INTEGER DEFAULT 0\"),
    (\"vpip\", \"REAL DEFAULT 0.5\"),
    (\"pfr\", \"REAL DEFAULT 0.5\"),
    (\"aggression_factor\", \"REAL DEFAULT 1.0\"),
    (\"fold_to_cbet\", \"REAL DEFAULT 0.5\"),
    (\"bluff_frequency\", \"REAL DEFAULT 0.3\"),
    (\"showdown_win_rate\", \"REAL DEFAULT 0.5\"),
    (\"recent_trend\", \"TEXT\"),
]

for col, typedef in om_cols:
    try:
        conn.execute(f\"ALTER TABLE opponent_models ADD COLUMN {col} {typedef}\")
        print(f\"Added opponent_models.{col}\")
    except Exception as e:
        if \"duplicate\" in str(e).lower():
            print(f\"Already exists: opponent_models.{col}\")
        else:
            print(f\"Error: {e}\")

for col in [\"prompt_template TEXT\", \"prompt_version TEXT\"]:
    try:
        conn.execute(f\"ALTER TABLE api_usage ADD COLUMN {col}\")
        print(f\"Added api_usage.{col.split()[0]}\")
    except Exception as e:
        if \"duplicate\" in str(e).lower():
            print(f\"Already exists: api_usage.{col.split()[0]}\")
        else:
            print(f\"Error: {e}\")

conn.commit()
print(\"Schema fixes complete\")
'"
```

#### Development
```bash
docker compose exec backend python -c "
import sqlite3
conn = sqlite3.connect('/app/data/poker_games.db')

# opponent_models columns
om_cols = [
    ('hands_observed', 'INTEGER DEFAULT 0'),
    ('vpip', 'REAL DEFAULT 0.5'),
    ('pfr', 'REAL DEFAULT 0.5'),
    ('aggression_factor', 'REAL DEFAULT 1.0'),
    ('fold_to_cbet', 'REAL DEFAULT 0.5'),
    ('bluff_frequency', 'REAL DEFAULT 0.3'),
    ('showdown_win_rate', 'REAL DEFAULT 0.5'),
    ('recent_trend', 'TEXT'),
]

for col, typedef in om_cols:
    try:
        conn.execute(f'ALTER TABLE opponent_models ADD COLUMN {col} {typedef}')
        print(f'Added opponent_models.{col}')
    except Exception as e:
        if 'duplicate' in str(e).lower():
            print(f'Already exists: opponent_models.{col}')
        else:
            print(f'Error: {e}')

# api_usage columns
for col in ['prompt_template TEXT', 'prompt_version TEXT']:
    try:
        conn.execute(f'ALTER TABLE api_usage ADD COLUMN {col}')
        print(f'Added api_usage.{col.split()[0]}')
    except Exception as e:
        if 'duplicate' in str(e).lower():
            print(f'Already exists: api_usage.{col.split()[0]}')
        else:
            print(f'Error: {e}')

conn.commit()
print('Schema fixes complete')
"
```

---

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

### "no such column: hands_observed" in experiments
Run the schema fix script above. The experiment runner's debug queries expect explicit columns on `opponent_models`.

### "AIPlayerController got unexpected keyword argument 'persistence'"
The code was updated to use `repository_factory` instead of `persistence`. Ensure you have the latest `experiments/run_ai_tournament.py`.

### "AIMemoryManager has no attribute 'set_persistence'"
Same fix - the memory manager now uses `set_repository_factory()`. Update `experiments/run_ai_tournament.py`.
