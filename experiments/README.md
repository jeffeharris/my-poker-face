# AI Tournament Experiments

This module provides tools for running AI-only poker tournaments to test different configurations, models, and strategies.

## Table of Contents

- [Quick Start](#quick-start)
- [Web UI (Recommended)](#web-ui-recommended)
- [Configuration Reference](#configuration-reference)
- [A/B Testing](#ab-testing)
- [Parallel Execution](#parallel-execution)
- [Hand-Based Experiments](#hand-based-experiments)
- [Psychology Systems](#psychology-systems)
- [Best Practices](#best-practices)
- [Limitations & Gotchas](#limitations--gotchas)
- [Cost Estimation](#cost-estimation)
- [Querying Results](#querying-results)
- [Troubleshooting](#troubleshooting)

---

## Quick Start

### Via Web UI (Recommended)

1. Navigate to the Admin Dashboard → Experiments
2. Click "New Experiment"
3. Use the AI assistant to help design your experiment
4. Configure variants and run

### Via CLI

```bash
# Run a simple tournament from Docker
docker compose exec backend python -m experiments.run_ai_tournament \
    --experiment my_test \
    --tournaments 1 \
    --hands 50 \
    --players 4

# Run with parallel execution
docker compose exec backend python -m experiments.run_ai_tournament \
    --experiment parallel_test \
    --tournaments 5 \
    --parallel 5 \
    --hands 100
```

### Via API

```bash
curl -X POST http://localhost:5005/api/experiments \
  -H "Content-Type: application/json" \
  -d '{
    "config": {
      "name": "my_experiment",
      "num_tournaments": 3,
      "hands_per_tournament": 50,
      "num_players": 4,
      "model": "gpt-5-nano",
      "provider": "openai"
    }
  }'
```

---

## Web UI (Recommended)

The Experiment Designer provides an AI-assisted interface for creating experiments.

### Features

- **AI Assistant**: Describe what you want to test in natural language
- **Config Preview**: Real-time preview of experiment configuration
- **Validation**: Catches errors before you run
- **Live Monitoring**: Watch experiments progress in real-time
- **Cost Tracking**: See API costs per variant

### AI Assistant Tips

The assistant understands these requests:
- "Compare GPT-5 vs Claude for poker decisions"
- "Test if enabling psychology improves play"
- "A/B test pot_odds enabled vs disabled"
- "Run a quick sanity check with 1 tournament"
- "Compare all fast/cheap models"

---

## Configuration Reference

### Basic Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | string | required | Unique identifier (snake_case) |
| `description` | string | "" | What the experiment tests |
| `hypothesis` | string | "" | Expected outcome |
| `tags` | string[] | [] | Categories for filtering |
| `num_tournaments` | int | 1 | Tournaments per variant (1-20) |
| `hands_per_tournament` | int | 100 | Hands per tournament (5-500) |
| `num_players` | int | 4 | Players per tournament (2-8) |
| `starting_stack` | int | 10000 | Starting chips |
| `big_blind` | int | 100 | Big blind amount |
| `reset_on_elimination` | bool | false | Reset stacks when one player remains (ensures exact hand count) |

### LLM Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `provider` | string | "openai" | LLM provider |
| `model` | string | "gpt-5-nano" | Model name |
| `reasoning_effort` | string | "low" | Reasoning level: minimal, low, medium, high |

**Available Providers & Models**:

| Provider | Models | Notes |
|----------|--------|-------|
| `openai` | gpt-5-nano, gpt-4o, gpt-4o-mini | reasoning_effort supported |
| `anthropic` | claude-sonnet-4-20250514, claude-opus-4 | |
| `groq` | llama-3.1-8b-instant | Very fast, no reasoning |
| `google` | gemini-2.0-flash, gemini-2.5-flash | |
| `mistral` | mistral-small-latest, mistral-medium-latest | |
| `xai` | grok-4-fast, grok-3-mini | minimal → no reasoning |
| `deepseek` | deepseek, deepseek-chat, deepseek-reasoner | |

### Execution Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `parallel_tournaments` | int | 1 | Concurrent tournaments (1 = sequential) |
| `stagger_start_delay` | float | 0.0 | Seconds between starting workers |
| `capture_prompts` | bool | true | Save prompts for debugging |
| `personalities` | string[] | null | Specific personalities (random if null) |

### Prompt Config Options

Control which information is included in AI decision prompts:

| Option | Default | Description |
|--------|---------|-------------|
| `pot_odds` | true | Pot odds and equity calculations |
| `hand_strength` | true | Hand strength evaluation |
| `session_memory` | true | Session stats (win rate, streaks) |
| `opponent_intel` | true | Opponent tendencies |
| `strategic_reflection` | true | Past strategic reflections |
| `chattiness` | true | Chattiness guidance |
| `emotional_state` | true | Emotional state narrative |
| `tilt_effects` | true | Tilt-based modifications |
| `mind_games` | true | Mind games instruction |
| `persona_response` | true | Persona response instruction |
| `situational_guidance` | true | Coaching prompts (pot-committed, short-stack, made hand) |
| `memory_keep_exchanges` | 0 | Conversation exchanges to retain |

**GTO Foundation Options** (math-based decision support):

| Option | Default | Description |
|--------|---------|-------------|
| `gto_equity` | false | Show equity comparison (vs random + vs opponent ranges) for all decisions |
| `gto_verdict` | false | Show explicit +EV/-EV verdict ("CALL is +EV", "FOLD is correct") |
| `use_enhanced_ranges` | true | Use PFR/action-based range estimation (vs simpler VPIP-only) |
| `include_personality` | true | Include personality system prompt (set to false for baseline testing) |
| `use_simple_response_format` | false | Use simple JSON response format instead of rich format |

---

## A/B Testing

For comparing models, prompts, or configurations, use the **control/variants** structure.

### Structure

```json
{
  "name": "my_ab_test",
  "num_tournaments": 5,
  "model": "gpt-5-nano",
  "provider": "openai",
  "control": {
    "label": "Baseline"
  },
  "variants": [
    {
      "label": "Challenger",
      "model": "gemini-2.0-flash",
      "provider": "google"
    }
  ]
}
```

This runs **5 tournaments with control** (using top-level model/provider) AND **5 tournaments with each variant**.

**Note**: Control always uses the experiment-level `model`/`provider` settings. Variants can override these to test different models.

### Control Fields

| Field | Required | Description |
|-------|----------|-------------|
| `label` | Yes | Display name in results |
| `game_mode` | No | Preset mode: `casual`, `standard`, `pro`, or `competitive` |
| `prompt_config` | No | Override prompt settings (overrides game_mode) |
| `enable_psychology` | No | Enable tilt/emotional state |
| `enable_commentary` | No | Enable commentary generation |

**Note**: Control uses experiment-level `model`/`provider` - these cannot be overridden in control.

### Variant Fields

| Field | Required | Description |
|-------|----------|-------------|
| `label` | Yes | Display name in results |
| `model` | No | Override model (inherits from experiment) |
| `provider` | No | Override provider (inherits from experiment) |
| `game_mode` | No | Preset mode: `casual`, `standard`, `pro`, or `competitive` (inherits from control) |
| `prompt_config` | No | Override prompt settings (overrides game_mode) |
| `reasoning_effort` | No | Override reasoning level |
| `enable_psychology` | No | Enable tilt/emotional state (inherits from control) |
| `enable_commentary` | No | Enable commentary generation (inherits from control) |

### Example: Model Comparison

```json
{
  "name": "gpt_vs_claude_vs_gemini",
  "num_tournaments": 3,
  "model": "gpt-5-nano",
  "provider": "openai",
  "control": {
    "label": "GPT-5 Nano"
  },
  "variants": [
    {
      "label": "Claude Sonnet",
      "provider": "anthropic",
      "model": "claude-sonnet-4-20250514"
    },
    {
      "label": "Gemini Flash",
      "provider": "google",
      "model": "gemini-2.0-flash"
    }
  ]
}
```

### Example: Prompt Ablation

```json
{
  "name": "pot_odds_ablation",
  "num_tournaments": 5,
  "model": "gpt-5-nano",
  "provider": "openai",
  "control": {
    "label": "With Pot Odds",
    "prompt_config": {"pot_odds": true}
  },
  "variants": [
    {
      "label": "No Pot Odds",
      "prompt_config": {"pot_odds": false}
    }
  ]
}
```

### Example: Situational Guidance Ablation

Test the impact of coaching prompts (pot-committed, short-stack, made hand warnings):

```json
{
  "name": "situational_guidance_impact",
  "num_tournaments": 5,
  "hands_per_tournament": 100,
  "reset_on_elimination": true,
  "model": "gpt-5-nano",
  "provider": "openai",
  "control": {
    "label": "With Coaching",
    "prompt_config": {"situational_guidance": true}
  },
  "variants": [
    {
      "label": "No Coaching",
      "prompt_config": {"situational_guidance": false}
    }
  ]
}
```

### Example: GTO Guidance Impact

Test if showing equity calculations and verdicts reduces fold mistakes:

```json
{
  "name": "gto_guidance_impact",
  "description": "Test if GTO foundation reduces EV-losing decisions",
  "num_tournaments": 5,
  "hands_per_tournament": 50,
  "reset_on_elimination": true,
  "model": "gpt-5-nano",
  "provider": "openai",
  "control": {
    "label": "No GTO Guidance",
    "prompt_config": {
      "gto_equity": false,
      "gto_verdict": false
    }
  },
  "variants": [
    {
      "label": "With GTO Guidance",
      "prompt_config": {
        "gto_equity": true,
        "gto_verdict": true
      }
    }
  ]
}
```

### Game Modes

Instead of manually specifying `prompt_config` fields, you can use the `game_mode` preset:

| Mode | Effect |
|------|--------|
| `casual` | Default PromptConfig (personality-driven fun poker) |
| `standard` | `gto_equity=true` (balanced personality + GTO awareness) |
| `pro` | `gto_equity=true, gto_verdict=true, chattiness=false, persona_response=false` (GTO-focused analytical) |
| `competitive` | `gto_equity=true, gto_verdict=true` (full GTO guidance with personality and trash talk) |

**Inheritance**: `variant.game_mode` → `control.game_mode` → `None` (defaults)

**Priority**: Explicit `prompt_config` fields override `game_mode` settings

### Example: Game Mode Comparison

```json
{
  "name": "casual_vs_pro",
  "description": "Compare personality-driven vs GTO-focused play",
  "num_tournaments": 5,
  "hands_per_tournament": 100,
  "reset_on_elimination": true,
  "model": "gpt-5-nano",
  "provider": "openai",
  "control": {
    "label": "Casual Mode",
    "game_mode": "casual"
  },
  "variants": [
    {
      "label": "Pro Mode",
      "game_mode": "pro"
    },
    {
      "label": "Standard Mode",
      "game_mode": "standard"
    }
  ]
}
```

### Example: Game Mode with Custom Overrides

You can combine `game_mode` with `prompt_config` to start from a preset and customize:

```json
{
  "name": "pro_with_tilt",
  "control": {
    "label": "Pure Pro",
    "game_mode": "pro"
  },
  "variants": [
    {
      "label": "Pro + Tilt Effects",
      "game_mode": "pro",
      "prompt_config": {"tilt_effects": true}
    }
  ]
}
```

---

## Parallel Execution

Run multiple tournaments concurrently to speed up experiments.

### Configuration

```json
{
  "name": "parallel_experiment",
  "num_tournaments": 1,
  "parallel_tournaments": 7,
  "stagger_start_delay": 2.0,
  "control": { "label": "A" },
  "variants": [
    { "label": "B" },
    { "label": "C" }
  ]
}
```

### How It Works

- `parallel_tournaments: 7` runs up to 7 tournaments simultaneously
- Each variant runs in its own thread with isolated state
- `stagger_start_delay: 2.0` waits 2 seconds between starting workers (helps avoid rate limits)

### Recommendations

| Scenario | parallel_tournaments | stagger_start_delay |
|----------|---------------------|---------------------|
| Single provider | 2-3 | 1.0 |
| Multiple providers | N (one per variant) | 2.0 |
| Rate limit concerns | 1-2 | 5.0 |
| Fast providers (Groq) | 5+ | 0.5 |

---

## Tournament Behavior: reset_on_elimination

The `reset_on_elimination` parameter determines whether hand counts are exact or maximum.

### The Problem

Default tournament behavior ends when one player has all chips. This creates unequal hand counts between experiments, making A/B comparisons difficult.

### How reset_on_elimination Works

| Config | Behavior |
|--------|----------|
| `reset_on_elimination: false` (default) | Tournament ends when one player wins OR hits hand limit (variable hands) |
| `reset_on_elimination: true` | Stacks reset on elimination, always plays EXACTLY hands_per_tournament |

### Example: Variable Hands (default)

```json
{
  "name": "quick_tournament",
  "num_tournaments": 3,
  "hands_per_tournament": 100,
  "num_players": 4
}
```

**Behavior**:
- Runs 3 tournaments of UP TO 100 hands each
- Tournaments end early if one player wins all chips
- Total hands varies based on game flow

### Example: Exact Hands (for fair A/B comparisons)

```json
{
  "name": "fair_comparison_test",
  "num_tournaments": 1,
  "hands_per_tournament": 200,
  "reset_on_elimination": true,
  "num_players": 4,
  "control": { "label": "Model A" },
  "variants": [{ "label": "Model B" }]
}
```

**Behavior**:
- Runs EXACTLY 200 hands per variant
- When one player eliminates others, all stacks reset to `starting_stack`
- Tracks "round winners" (who had most chips at each reset)
- Each variant gets identical sample size for fair comparison

### When to Use Each

| Scenario | Recommendation |
|----------|----------------|
| A/B testing model quality | `reset_on_elimination: true` with desired hand count |
| Equal data points per variant | `reset_on_elimination: true` |
| Natural tournament flow | `reset_on_elimination: false` (default) |
| Quick tests | `reset_on_elimination: false` (default) |

### Results Tracking

With `reset_on_elimination: true`, results include:
- `round_winners`: List of players who had most chips at each reset
- `total_resets`: How many times stacks were reset

---

## Psychology Systems

Enable psychological feedback systems for richer AI behavior.

### Flags

| Flag | LLM Cost/Hand | Description |
|------|---------------|-------------|
| `enable_psychology` | ~4 calls | Tilt tracking + emotional state generation |
| `enable_commentary` | ~4 calls | Commentary generation + session reflections |

### What They Do

**enable_psychology**:
- Tracks pressure events (big wins, bad beats, bluffs)
- Updates tilt state after each hand
- Generates emotional state narrative via LLM
- Emotional state influences decision prompts

**enable_commentary**:
- Generates post-hand commentary per player
- Stores reflections in session memory
- Can affect future decision context

### Example: Psychology Impact Test

```json
{
  "name": "psychology_impact",
  "num_tournaments": 5,
  "control": {
    "label": "No Psychology",
    "enable_psychology": false
  },
  "variants": [
    {
      "label": "With Psychology",
      "enable_psychology": true
    }
  ]
}
```

### Cost Warning

With psychology enabled, each hand makes ~8 additional LLM calls (4 players × 2 calls for emotional state + categorization). For a 50-hand tournament with 4 players, that's ~400 extra calls.

---

## Best Practices

### Statistical Validity

1. **Run enough tournaments**: Poker has high variance. 3-5 tournaments minimum, 10+ for reliable conclusions.

2. **Control for randomness**: Use `random_seed` for reproducible personality selection:
   ```json
   {"random_seed": 42}
   ```

3. **Same personalities across variants**: When comparing models, ensure the same personalities are used. Either specify them explicitly or use a seed.

4. **Sufficient hands**: Tournaments should run long enough for skill to matter. 50+ hands recommended, 100+ for reliable stats.

### Experiment Design

1. **Change one thing at a time**: Don't compare different models AND different prompts simultaneously.

2. **Use descriptive names**: `gpt_vs_claude_reasoning_low` is better than `test_1`.

3. **Document your hypothesis**: Future you will thank you.

4. **Tag experiments**: Use tags like `["model_comparison", "production_candidate"]` for filtering.

### Performance

1. **Use parallel execution**: For multi-variant tests, set `parallel_tournaments` equal to your variant count.

2. **Choose fast models for iteration**: Use `gpt-5-nano` or `gemini-2.0-flash` for quick tests, save expensive models for final validation.

3. **Start small**: Run 1 tournament with 20 hands first to verify everything works.

4. **Mind rate limits**: Stagger parallel workers with `stagger_start_delay: 2.0` to avoid 429 errors.

### Cost Management

1. **Estimate before running**: See [Cost Estimation](#cost-estimation) section.

2. **Disable psychology for initial tests**: It doubles+ your API costs.

3. **Use cheap models first**: Validate experiment design with `gpt-5-nano` before using `gpt-4o`.

---

## Limitations & Gotchas

### Known Limitations

1. **No live games**: Experiments are AI-only. No human players.

2. **Sequential hands within tournament**: Hands within a single tournament run sequentially (poker can't be parallelized mid-game).

3. **Server restart kills workers**: If Flask restarts (code changes in dev), running experiments stop. Resume via API or dashboard.

4. **Memory grows with history**: Long tournaments with `memory_keep_exchanges > 0` can accumulate large prompt contexts.

### Common Gotchas

1. **"Only one variant ran"**: Check `parallel_tournaments`. Default is 1 (sequential).

2. **"Experiment stuck"**: Check if server restarted. Status may show "running" but worker is dead. Pause then resume.

3. **"Missing decision stats"**: GTO analyzer runs async. Wait a moment after tournament completes, or check `player_decision_analysis` table.

4. **"Rate limited"**: Add `stagger_start_delay`, reduce `parallel_tournaments`, or use providers with higher limits (Groq is generous).

5. **"Results seem random"**: Poker has variance. Run more tournaments. 3 is minimum, 10+ for confidence.

6. **"Tournament ended early"**: Players eliminated (reached 0 chips). This is normal. Check `total_hands` vs `hands_per_tournament`. Use `reset_on_elimination: true` to run exact hand counts.

### Database Column Names

The `experiment_games` table uses `variant_config_json` (not `variant_config`). This has caused bugs - if you write custom queries, use the correct column name.

---

## Cost Estimation

### Per-Decision Costs (approximate)

| Model | Input $/M | Output $/M | ~Cost/Hand (4 players) |
|-------|-----------|------------|------------------------|
| gpt-5-nano | $0.10 | $0.40 | $0.002 |
| gpt-4o-mini | $0.15 | $0.60 | $0.003 |
| gpt-4o | $2.50 | $10.00 | $0.05 |
| claude-sonnet-4 | $3.00 | $15.00 | $0.06 |
| gemini-2.0-flash | $0.10 | $0.40 | $0.002 |
| gemini-2.5-flash | $0.30 | $2.50 | $0.01 |
| groq llama-3.1-8b | Free tier | Free tier | ~$0 |
| mistral-small | $0.20 | $0.60 | $0.003 |

### Formula

```
Base cost = hands × players × 2 × (input_cost × ~1000 + output_cost × ~200) / 1M
With psychology: Base cost × 2
With commentary: Base cost × 2.5
```

### Example Calculation

50-hand tournament, 4 players, GPT-5 Nano, no psychology:
- ~8 decisions/hand × 50 hands = 400 decisions
- ~$0.002/decision × 400 = **~$0.80 per tournament**

Same with psychology enabled:
- **~$1.60 per tournament**

A/B test with 5 tournaments × 2 variants:
- **~$8.00 total** (or ~$16 with psychology)

---

## Querying Results

### Key Tables

- `experiments` - Experiment metadata and config
- `experiment_games` - Links games to experiments
- `tournament_results` - Final standings per tournament
- `player_decision_analysis` - Per-decision quality metrics
- `api_usage` - LLM call tracking and costs

### Useful Queries

```sql
-- Experiment summary
SELECT name, status, created_at,
       json_extract(summary_json, '$.total_tournaments') as tournaments,
       json_extract(summary_json, '$.total_hands') as hands
FROM experiments
ORDER BY created_at DESC;

-- Results by variant
SELECT eg.variant,
       COUNT(*) as tournaments,
       AVG(tr.total_hands) as avg_hands,
       SUM(CASE WHEN tr.winner_name IS NOT NULL THEN 1 ELSE 0 END) as completed
FROM experiment_games eg
LEFT JOIN tournament_results tr ON eg.game_id = tr.game_id
WHERE eg.experiment_id = ?
GROUP BY eg.variant;

-- Decision quality by variant
SELECT eg.variant,
       COUNT(*) as decisions,
       ROUND(100.0 * SUM(CASE WHEN pda.decision_quality = 'correct' THEN 1 ELSE 0 END) / COUNT(*), 1) as correct_pct,
       ROUND(AVG(COALESCE(pda.ev_lost, 0)), 4) as avg_ev_lost
FROM player_decision_analysis pda
JOIN experiment_games eg ON pda.game_id = eg.game_id
WHERE eg.experiment_id = ?
GROUP BY eg.variant;

-- API costs by variant
SELECT eg.variant,
       au.provider,
       au.model,
       COUNT(*) as calls,
       SUM(au.estimated_cost) as total_cost
FROM api_usage au
JOIN experiment_games eg ON au.game_id = eg.game_id
WHERE eg.experiment_id = ?
GROUP BY eg.variant, au.provider, au.model;
```

---

## Experiment Statuses

| Status | Description | Can Resume? |
|--------|-------------|-------------|
| `pending` | Created but not started | No |
| `running` | Currently executing tournaments | No (pause first) |
| `paused` | Manually paused by user | Yes |
| `interrupted` | Server restarted while running | Yes |
| `failed` | All tournaments failed (e.g., API errors, migration issues) | Yes |
| `completed` | Finished successfully (at least one tournament completed) | No |

**When does an experiment become `failed`?**
- When ALL tournaments fail (none complete successfully)
- Error message shows the first 3 failure reasons
- Common causes: missing DB migrations, invalid API keys, rate limits on all providers

---

## Troubleshooting

### Experiment Won't Start

1. Check validation errors in the response
2. Ensure name is unique and snake_case
3. Verify API keys are configured for the provider

### Experiment Stuck

1. Check server logs: `docker compose logs backend --tail 100`
2. If server restarted, pause then resume the experiment
3. Check for rate limit errors (429)

### No Results After Completion

1. Wait for async processing to complete
2. Check `tournament_results` table has entries
3. Verify `experiment_games` links are correct

### Rate Limiting

1. Reduce `parallel_tournaments`
2. Add `stagger_start_delay: 5.0`
3. Switch to a provider with higher limits (Groq, Google)
4. The system uses fallback actions automatically but this affects data quality

### Resuming Failed Experiments

```bash
# Via API
curl -X POST http://localhost:5005/api/experiments/{id}/resume

# If status is stuck on "running"
docker compose exec backend python -c "
import sqlite3
conn = sqlite3.connect('/app/data/poker_games.db')
conn.execute('UPDATE experiments SET status = \"paused\" WHERE id = {id}')
conn.commit()
"
# Then resume via API
```

### Resuming Stalled Variants

Experiments track heartbeats per variant. If a variant stops updating (API timeout, crash, etc.), it's detected as "stalled" after 5 minutes.

**Via Web UI**:
- Running experiments show a "Stalled" badge on affected variant cards
- Click "Resume" button on the stalled variant card

**Via CLI**:
```bash
# List stalled variants for an experiment
python -m experiments.resume_stalled -e 42 --list

# Resume all stalled variants
python -m experiments.resume_stalled -e 42 --resume-all

# Resume a specific variant by game_id
python -m experiments.resume_stalled -e 42 -g <game_id>

# Custom stall threshold (default: 5 minutes)
python -m experiments.resume_stalled -e 42 --list --threshold 10
```

**Via API**:
```bash
# Get stalled variants
curl http://localhost:5005/api/experiments/42/stalled?threshold_minutes=5

# Resume a specific variant (game_id from stalled list)
curl -X POST http://localhost:5005/api/experiments/42/variants/<variant_id>/resume
```

**How it works**:
- Each variant updates a heartbeat before/after API calls
- The system detects variants stuck in `calling_api` or `processing` state beyond the threshold
- Resume uses pessimistic locking to prevent race conditions with the original process
- If the original process is still alive, it exits gracefully when it detects it's been superseded

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     AITournamentRunner                          │
├─────────────────────────────────────────────────────────────────┤
│  ExperimentConfig     →  Configuration (players, model, etc.)   │
│  GamePersistence      →  Database operations                    │
│  PokerStateMachine    →  Game flow control                      │
│  AIPlayerController[] →  AI decision making per player          │
│  AIMemoryManager      →  Hand tracking & persistence            │
│  ThreadPoolExecutor   →  Parallel tournament execution          │
└─────────────────────────────────────────────────────────────────┘
```

### Tournament Flow

1. **Initialization**: Create game state, controllers, memory manager
2. **Hand Loop**: Advance state machine, get AI decisions, apply actions
3. **Psychology** (if enabled): Detect events, update tilt, generate emotional state
4. **Commentary** (if enabled): Generate reflections, update session memory
5. **Completion**: Record results, compute standings, aggregate stats

### Key Files

- `experiments/run_ai_tournament.py` - Main runner and config
- `experiments/pause_coordinator.py` - Pause/resume coordination
- `experiments/resume_stalled.py` - CLI for stalled variant detection and resume
- `flask_app/routes/experiment_routes.py` - API endpoints
- `poker/controllers.py` - AIPlayerController
- `poker/player_psychology.py` - Tilt and emotional state
