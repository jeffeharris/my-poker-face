# AI Tournament Experiments

This module provides tools for running AI-only poker tournaments to test different configurations, models, and strategies.

## Quick Start

```bash
# Run a simple tournament from Docker
docker compose exec backend python -m experiments.run_ai_tournament \
    --experiment my_test \
    --tournaments 1 \
    --hands 50 \
    --players 3
```

## Components

### 1. `run_ai_tournament.py` - Core Tournament Runner

The main experiment runner that handles:
- Creating AI-only games with configurable players
- Running tournaments to completion
- Tracking decision quality metrics
- Persisting results to the database

#### Basic Usage

```python
from experiments.run_ai_tournament import ExperimentConfig, AITournamentRunner

config = ExperimentConfig(
    name='test_gemini_vs_gpt',
    description='Compare Gemini Flash vs GPT-4o decision quality',
    num_tournaments=5,
    max_hands_per_tournament=100,
    num_players=4,
    starting_stack=10000,
    big_blind=100,
    provider='google',  # or 'openai', 'anthropic', 'groq', etc.
    model='gemini-2.0-flash',
    personalities=['Abraham Lincoln', 'Batman', 'Sherlock Holmes'],  # Optional specific personalities
)

runner = AITournamentRunner(config)
results = runner.run_experiment()

for result in results:
    print(f"Tournament {result.tournament_id}: Winner = {result.winner}, Hands = {result.hands_played}")
    print(f"Decision stats: {result.decision_stats}")
```

#### Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `name` | required | Unique experiment name |
| `description` | "" | What you're testing |
| `hypothesis` | "" | Expected outcome |
| `tags` | None | List of tags for filtering |
| `num_tournaments` | 1 | Number of tournaments to run |
| `max_hands_per_tournament` | 100 | Hand limit per tournament |
| `num_players` | 4 | Players per tournament |
| `starting_stack` | 10000 | Starting chips |
| `big_blind` | 100 | Big blind amount |
| `provider` | "openai" | LLM provider |
| `model` | "gpt-5-nano" | Model name |
| `personalities` | None | Specific personalities (random if None) |
| `capture_prompts` | True | Save prompts for debugging |

### 2. `ab_test_demo.py` - A/B Testing Framework

Run controlled experiments comparing two configurations:

```python
from experiments.ab_test_demo import run_ab_test

results = run_ab_test(
    experiment_name='prompt_comparison',
    variant_a_config={'provider': 'google', 'model': 'gemini-2.0-flash'},
    variant_b_config={'provider': 'openai', 'model': 'gpt-4o-mini'},
    tournaments_per_variant=3,
)
```

### 3. `compare_strategies.py` - Strategy Comparison

Compare different AI strategies or prompt configurations.

## Querying Results

Results are stored in the main `poker_games.db` database. Key tables:

### `experiments` table
```sql
SELECT name, status, summary_json, created_at
FROM experiments
ORDER BY created_at DESC;
```

### `experiment_games` table
```sql
-- Get all games for an experiment
SELECT eg.game_id, eg.variant, eg.tournament_number
FROM experiment_games eg
JOIN experiments e ON eg.experiment_id = e.id
WHERE e.name = 'my_experiment';
```

### Decision quality by experiment
```sql
-- Aggregate decision stats for an experiment
SELECT
    e.name,
    COUNT(*) as total_decisions,
    SUM(CASE WHEN pda.decision_quality = 'correct' THEN 1 ELSE 0 END) as correct,
    ROUND(AVG(COALESCE(pda.ev_lost, 0)), 2) as avg_ev_lost
FROM player_decision_analysis pda
JOIN experiment_games eg ON pda.game_id = eg.game_id
JOIN experiments e ON eg.experiment_id = e.id
GROUP BY e.name;
```

### Decision stats by player
```sql
-- Per-player performance in an experiment
SELECT
    pda.player_name,
    COUNT(*) as decisions,
    ROUND(100.0 * SUM(CASE WHEN decision_quality = 'correct' THEN 1 ELSE 0 END) / COUNT(*), 1) as correct_pct
FROM player_decision_analysis pda
JOIN experiment_games eg ON pda.game_id = eg.game_id
JOIN experiments e ON eg.experiment_id = e.id
WHERE e.name = 'my_experiment'
GROUP BY pda.player_name
ORDER BY correct_pct DESC;
```

## Example Experiments

### 1. Model Comparison
```bash
# Test different models
docker compose exec backend python -c "
from experiments.run_ai_tournament import ExperimentConfig, AITournamentRunner

for provider, model in [('google', 'gemini-2.0-flash'), ('openai', 'gpt-4o-mini')]:
    config = ExperimentConfig(
        name=f'model_test_{provider}',
        num_tournaments=3,
        max_hands_per_tournament=50,
        num_players=3,
        provider=provider,
        model=model,
    )
    runner = AITournamentRunner(config)
    results = runner.run_experiment()
    print(f'{provider}/{model}: {sum(r.decision_stats.get(\"correct_pct\", 0) for r in results) / len(results):.1f}% correct')
"
```

### 2. Personality Impact
```bash
# Test aggressive vs passive personalities
docker compose exec backend python -c "
from experiments.run_ai_tournament import ExperimentConfig, AITournamentRunner

aggressive = ['The Hulk', 'Gordon Ramsay', 'Donald Trump']
passive = ['Bob Ross', 'Eeyore', 'Mr. Rogers']

for name, personalities in [('aggressive', aggressive), ('passive', passive)]:
    config = ExperimentConfig(
        name=f'personality_{name}',
        num_tournaments=2,
        personalities=personalities,
        provider='google',
        model='gemini-2.0-flash',
    )
    runner = AITournamentRunner(config)
    results = runner.run_experiment()
"
```

## Output Files

- **Results JSON**: `experiments/results/exp_<name>_<timestamp>_<tournament>.json`
- **Database**: All data persisted to `data/poker_games.db`

## Troubleshooting

### Rate Limiting
If you hit rate limits (429 errors), the runner uses fallback actions automatically. For cleaner results, add delays between tournaments or use a higher-tier API plan.

### Tournament Stuck
The runner has built-in protections:
- Pre-hand check stops when ≤1 player has chips
- Run-it-out mode auto-advances all-in scenarios
- Loop detection breaks out after 6 repeated player queries

### Missing Decision Stats
Decision analysis requires the GTO analyzer. If stats are empty, check that `player_decision_analysis` table is being populated during games.

## How It Works

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     AITournamentRunner                          │
├─────────────────────────────────────────────────────────────────┤
│  ExperimentConfig     →  Configuration (players, model, etc.)   │
│  GamePersistence      →  Database operations                    │
│  PokerStateMachine    →  Game flow control                      │
│  AIPlayerController[] →  AI decision making per player          │
│  AIMemoryManager      →  Hand tracking & persistence            │
└─────────────────────────────────────────────────────────────────┘
```

### Tournament Flow

1. **Initialization** (`create_game`)
   - Select random personalities (or use specified ones)
   - Create `PokerGameState` with AI-only players
   - Initialize `PokerStateMachine` for game flow
   - Create `AIPlayerController` for each player with LLM config
   - Set up `AIMemoryManager` for tracking

2. **Hand Loop** (`run_hand`)
   ```
   For each hand:
   ├── Check if tournament should end (≤1 player with chips)
   ├── State machine advances to next action point
   ├── If run_it_out mode (all-in): auto-advance, skip player input
   ├── If awaiting_action:
   │   ├── Get current player's controller
   │   ├── Call controller.decide_action() → LLM API call
   │   ├── Apply action via play_turn()
   │   └── Advance to next active player
   ├── Loop detection: break if same player asked 6+ times
   └── On EVALUATING_HAND phase: determine winner, award pot
   ```

3. **Tournament Completion** (`run_tournament`)
   - Track eliminations as players reach 0 chips
   - Continue until 1 player remains or hand limit reached
   - Compute final standings sorted by chip count
   - Query decision stats from `player_decision_analysis` table
   - Return `TournamentResult` with all metrics

### Key State Machine Phases

```
INITIALIZING_HAND → PRE_FLOP → DEALING_CARDS → FLOP → DEALING_CARDS
    → TURN → DEALING_CARDS → RIVER → SHOWDOWN → EVALUATING_HAND → HAND_OVER
```

The tournament runner calls `state_machine.run_until([PokerPhase.EVALUATING_HAND])` which advances through phases until:
- A player action is needed (`awaiting_action = True`), or
- The hand reaches evaluation

### Decision Quality Tracking

Each AI decision is analyzed by the GTO (Game Theory Optimal) analyzer:

```python
# In player_decision_analysis table:
{
    'game_id': 'tournament_001',
    'player_name': 'Batman',
    'hand_number': 5,
    'phase': 'FLOP',
    'action_taken': 'raise',
    'optimal_action': 'raise',
    'decision_quality': 'correct',  # correct, marginal, or mistake
    'ev_lost': 0.0,  # Expected value lost vs optimal
}
```

Quality classifications:
- **correct**: Action matches GTO recommendation
- **marginal**: Suboptimal but small EV loss (e.g., check when raise is slightly better)
- **mistake**: Significant EV loss (e.g., folding a strong hand)

### Run-It-Out Mode

When all remaining players are all-in (or only 1 can act), the game enters "run-it-out" mode:

```python
if game_state.run_it_out:
    # No player input needed - just deal remaining cards
    if current_phase == PokerPhase.RIVER:
        next_phase = PokerPhase.SHOWDOWN
    else:
        next_phase = PokerPhase.DEALING_CARDS
    # Clear flags and advance
    game_state = game_state.update(awaiting_action=False, run_it_out=False)
    state_machine.update_phase(next_phase)
```

This prevents the stuck loop where eliminated or all-in players are repeatedly asked for actions.

### Experiment Persistence

Experiments are stored in two tables:

**`experiments`** - Metadata
```sql
CREATE TABLE experiments (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE,
    description TEXT,
    config_json TEXT,      -- Full ExperimentConfig as JSON
    status TEXT,           -- 'running', 'completed', 'failed'
    summary_json TEXT,     -- Aggregated results on completion
    created_at TIMESTAMP,
    completed_at TIMESTAMP
);
```

**`experiment_games`** - Links games to experiments
```sql
CREATE TABLE experiment_games (
    experiment_id INTEGER,
    game_id TEXT,
    variant TEXT,              -- For A/B tests: 'variant_a', 'variant_b'
    variant_config_json TEXT,  -- Variant-specific config
    tournament_number INTEGER,
    FOREIGN KEY (experiment_id) REFERENCES experiments(id),
    FOREIGN KEY (game_id) REFERENCES games(game_id)
);
```

### AI Fallback Behavior

When LLM calls fail (rate limits, errors), the system uses fallback strategies:

```python
@with_ai_fallback(fallback_strategy=AIFallbackStrategy.MIMIC_PERSONALITY)
def _get_ai_decision(self, message: str, **context) -> Dict:
    # If this fails after 3 retries, fallback kicks in
    ...
```

Fallback strategies:
- **CONSERVATIVE**: Check if possible, else call/fold
- **RANDOM_VALID**: Random action from valid options
- **MIMIC_PERSONALITY**: Based on personality traits (aggression, bluff tendency)

This ensures tournaments complete even with API issues.
