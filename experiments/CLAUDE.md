# Experiments Module - CLAUDE.md

This module provides tools for running AI-only poker tournaments to test different configurations, models, and strategies.

## Quick Reference

### Running Experiments

```bash
# Run a simple tournament
docker compose exec backend python -m experiments.run_ai_tournament \
    --experiment my_test --tournaments 1 --hands 50

# Run with parallel execution
docker compose exec backend python -m experiments.run_ai_tournament \
    --experiment parallel_test --tournaments 5 --parallel 5

# Run from config file
docker compose exec backend python -m experiments.run_from_config \
    experiments/configs/my_config.json
```

### Experiment Statuses

| Status | Description | Can Resume? |
|--------|-------------|-------------|
| `running` | Currently executing | No (pause first) |
| `paused` | Manually paused | Yes |
| `interrupted` | Server restarted while running | Yes |
| `failed` | All tournaments failed | Yes |
| `completed` | Finished successfully | No |

**Note**: Experiment is marked `failed` only when ALL tournaments fail. If at least one succeeds, it's `completed`.

### Managing Stalled Variants

Experiments track heartbeats per variant. If a variant stops updating (API timeout, crash, etc.), it's detected as "stalled" after 5 minutes.

```bash
# List stalled variants for an experiment
python -m experiments.resume_stalled -e <experiment_id> --list

# Resume all stalled variants
python -m experiments.resume_stalled -e <experiment_id> --resume-all

# Resume a specific variant by game_id
python -m experiments.resume_stalled -e <experiment_id> -g <game_id>

# Custom stall threshold (default: 5 minutes)
python -m experiments.resume_stalled -e <experiment_id> --list --threshold 10
```

## Key Files

| File | Purpose |
|------|---------|
| `run_ai_tournament.py` | Main tournament runner and ExperimentConfig dataclass |
| `pause_coordinator.py` | Pause/resume coordination across threads |
| `resume_stalled.py` | CLI for detecting and resuming stalled variants |
| `run_from_config.py` | Run experiments from JSON config files |
| `variant_config.py` | Variant configuration utilities |
| `configs/` | Example experiment configuration files |
| `results/` | Default output directory for tournament results |

## Architecture

```
AITournamentRunner
├── ExperimentConfig     - Configuration (players, model, etc.)
├── GamePersistence      - Database operations
├── PokerStateMachine    - Game flow control
├── AIPlayerController[] - AI decision making per player
├── AIMemoryManager      - Hand tracking & persistence
└── ThreadPoolExecutor   - Parallel tournament execution
```

### Heartbeat Tracking

The system tracks variant health via heartbeats stored in `experiment_games`:

- **state**: Current state (`idle`, `calling_api`, `processing`)
- **last_heartbeat_at**: Last activity timestamp
- **last_api_call_started_at**: When the current API call started
- **process_id**: PID of the process running this variant

A variant is considered "stalled" when:
- `state='calling_api'` AND `last_api_call_started_at` > threshold ago
- `state='processing'` AND `last_heartbeat_at` > threshold ago
- Not already completed (no entry in `tournament_results`)

### Resume Flow (Race Prevention)

1. User initiates resume (UI/CLI/API)
2. System acquires pessimistic lock: `resume_lock_acquired_at = NOW()`
3. If lock acquired, new process starts resuming
4. Original process (if alive) checks `resume_lock_acquired_at > last_heartbeat_at`
5. If superseded, original process exits gracefully via `TournamentSupersededException`
6. Resume process continues from saved checkpoint

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/experiments/<id>/stalled` | GET | List stalled variants |
| `/api/experiments/<id>/variants/<game_id>/resume` | POST | Resume specific variant |
| `/api/experiments/<id>/pause` | POST | Pause experiment |
| `/api/experiments/<id>/resume` | POST | Resume entire experiment |

## Database Tables

| Table | Purpose |
|-------|---------|
| `experiments` | Experiment metadata and config |
| `experiment_games` | Links games to experiments, heartbeat tracking |
| `tournament_results` | Final standings per tournament |
| `player_decision_analysis` | Per-decision quality metrics |
| `api_usage` | LLM call tracking and costs |

## Common Tasks

### Check experiment status
```bash
python3 scripts/dbq.py "SELECT id, name, status FROM experiments ORDER BY id DESC LIMIT 5"
```

### View stalled variants
```bash
python -m experiments.resume_stalled -e <id> --list
```

### Force status update
```bash
docker compose exec backend python -c "
import sqlite3
conn = sqlite3.connect('/app/data/poker_games.db')
conn.execute('UPDATE experiments SET status = \"paused\" WHERE id = <id>')
conn.commit()
"
```

### View experiment games with heartbeat status
```sql
SELECT id, game_id, variant, state, last_heartbeat_at, process_id
FROM experiment_games
WHERE experiment_id = <id>
ORDER BY id;
```

## Testing

```bash
# Run experiment-related tests
python3 scripts/test.py "test_experiment"

# Run specific tournament tests
python3 scripts/test.py "test_run_ai_tournament"
```
