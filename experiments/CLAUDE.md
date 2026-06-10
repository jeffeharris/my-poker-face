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

## Prompt Config & A/B Testing

Experiments control AI behavior through `PromptConfig` toggles in `prompt_config` sections. Key toggles:

| Toggle | Default | What it controls |
|--------|---------|-----------------|
| `include_personality` | `true` | Personality system prompt (false = generic poker player) |
| `use_simple_response_format` | `false` | Simple `{"action", "raise_to"}` JSON vs rich format |
| `pot_odds` | `true` | Pot odds guidance in prompt |
| `hand_strength` | `true` | Hand strength evaluation |
| `gto_equity` | `false` | Equity vs random + opponent ranges |
| `gto_verdict` | `false` | Explicit +EV/-EV verdict |
| `situational_guidance` | `true` | Coaching (pot-committed, short-stack, made hand) |

To run a baseline test (no personality, simple format):
```json
{"prompt_config": {"include_personality": false, "use_simple_response_format": true}}
```

See `experiments/README.md` for full config reference, A/B testing structure, game modes, cost estimation, and querying results.

## Variant Config Options

These options control experiment-level behavior (not per-prompt):

| Option | Default | What it controls |
|--------|---------|-----------------|
| `enable_telemetry` | `true` | Saves hand equity data to `hand_equity` table for analytics |
| `enable_psychology` | `false` | Enables pressure detection and emotional state updates |
| `enable_commentary` | `false` | Generates AI commentary after each hand |

To disable telemetry (saves compute on large experiments):
```json
{"variants": [{"enable_telemetry": false}]}
```

## Key Files

| File | Purpose |
|------|---------|
| `run_ai_tournament.py` | Main tournament runner and ExperimentConfig dataclass |
| `pause_coordinator.py` | Pause/resume coordination across threads |
| `resume_stalled.py` | CLI for detecting and resuming stalled variants |
| `run_from_config.py` | Run experiments from JSON config files |
| `run_minimal_prompt_test.py` | Quick baseline prompt test script |
| `champion_challenger.py` | Champion-vs-challenger A/B harness — runs the bot's current vs changed strategy head-to-head at one table (the EVAL_HARNESS_PLAN §P0 gate; immune to station-inflation) |
| `sng_runner.py` | Single-table winner-take-all SNG eval (EVAL_HARNESS_PLAN §P1) — escalating blinds, elimination, play-to-one-winner, win-rate. `--mode field` (which archetype wins) or `--mode champion_challenger` (the **cut-grade gate**, hardened per docs/plans/SNG_RUNNER_HARDENING.md §P0-P4: **antithetic role-swap** per seed-block, bootstrap CI over blocks, outcome accounting that refuses a verdict on silent dropouts). Calibrated via `--change null` (A-A → exactly 50%) + `cripple_challenger`/`cripple_champion` (known-extreme sign check). `--sngs` counts seed-blocks (×2 role-swapped SNGs each); use ≥500 blocks for slice-sized effects. **`--opponent-model`** attaches + feeds a per-hero `OpponentModelManager` across the tournament so the opponent-modeling **exploitation** layer fires (it no-ops without one — this is the path no prior eval exercised); **`--backdrop A,B,C,D`** fills the non-A/B seats with FIXED opponents (identical across both arms), e.g. exploitable stations for the `exploitation` A/B to detect. Backdrop wins are excluded from the win-rate; null = `n_challenger / (seats − backdrop)`. With no backdrop + no `--opponent-model` it is byte-identical to the bare gate. **Exploitation A/B recipe:** `--change exploitation --archetype TAG --seats 6 --challenger-seats 1 --backdrop CallStation,CallStation,FoldyBot,FoldyBot` (needs a non-Baseline archetype — Baseline has `anchors=None` so the layer no-ops; `--opponent-model` auto-enabled for `exploitation`). Run it again with a competent backdrop (`GTO-Lite,GTO-Lite,ABCBot,ABCBot`) — a win vs the fish backdrop that vanishes vs the competent one is station-overfit, not a real edge |
| `exploit_bb100.py` | **bb/100 exploitation gate** — the sensitive instrument the WTA-SNG gate isn't. Seats one exploit-ON hero (`exploitation_strength` 1.0) + one exploit-OFF twin (0.0) of the same archetype at a table with a fixed exploitable backdrop; stacks reset per hand (the station never busts) but controllers + the shared opponent model persist so reads mature to full confidence. Headline = **paired per-hand edge** (CHAL−CHMP bb/100) + CI. Built because `--change exploitation` on the SNG gate measured null but win-rate is coarse (±2pp hides a real bb/100 edge) and elimination busts the fish before reads mature. `--archetype TAG --backdrop CallStation,CallStation,FoldyBot,FoldyBot --hands 40000 --seeds 42,142,242`; run again with a competent backdrop (`GTO-Lite,GTO-Lite,ABCBot,ABCBot`) as the station-overfit control |
| `measure_passivity.py` | Tier-A passivity eval; `--opponents jeff` (station) / `punisher` (aggressive reg) for the EVAL_HARNESS_PLAN §P0.5 absolute checks |
| `clone_profiles/` | Frozen human-clone profiles (`jeff.json` station, `punisher.json` reg) usable DB-free via `--clone-profile` |
| `measure_zone_distribution.py` | Emotional-zone REACHABILITY over the full roster, driving REAL production psychology (no re-derived formulas). Reports per-poise-band %time-tilted + median episode length, and FITs the persistence drag. Absolute %time is event-model-dependent; `LOSS_MIX` is calibrated to the live sweep (see `tilt_live_sweep.py`). Spread SHAPE is the robust signal. |
| `tilt_live_sweep.py` | Multi-seed LIVE tilt anchor: runs `tilt_persistence_check.json` across N base seeds per arm (subprocess per arm for flag-env isolation, `--seed` varies the decks) and reports per-archetype mean ± sd %time-tilted. Built because single 1,200-hand runs are too noisy (hothead swung 8–16%). `--arms off/on/both --seeds 42,142,…`; `--hands/--tournaments` for quick wiring checks. NOTE on/off are desynced trajectories — the across-arm diff is NOT a clean persistence effect. |
| `tilt_signature_probe.py` | Within-spot PAIRED probe for the §4 tilt signature (`TILT_SIGNATURE_ENABLED`): runs both flag arms through the real `modify_strategy` on the SAME spot (trajectory-free), reports aggression-mass Δ (direction: collapse/spew) + KL-from-baseline (exploitability budget). |
| `tilt_ev_probe.py` | Phase-1 of the tilt EV harness (`docs/plans/TILT_EV_HARNESS.md`): `tilt_signature_probe` + an eval7-priced EV estimator (fold-equity model, fish vs competent backdrop) → paired ΔEV in bb. Prices the COLLAPSE direction; the SPEW direction awaits range-aware eq-when-called (Phase-2 — `eq` is vs random today, so HU aggression reads spuriously +EV). |
| `variant_config.py` | Variant configuration utilities |
| `configs/` | Example experiment configuration files |
| `results/` | Default output directory for tournament results |
| `README.md` | Full documentation (config reference, A/B testing, cost estimation) |

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
