---
name: experiment
description: Design and run AI poker tournament experiments. Use when the user wants to create, configure, run, monitor, or troubleshoot experiments comparing AI models, prompt configs, or strategies.
argument-hint: [description of experiment to run]
disable-model-invocation: true
user-invocable: true
---

# Poker Experiment Skill

Help the user design and run AI-only poker tournament experiments. Use the arguments and conversation context to understand what they want to test.

## Workflow

1. **Clarify the goal**: What are they comparing? (models, prompt configs, strategies, bots)
2. **Build the config**: Generate a valid experiment JSON config
3. **Estimate cost**: Calculate approximate API costs before running
4. **Run it**: Execute via the appropriate method (API, CLI, or Web UI)
5. **Monitor**: Check status and help troubleshoot issues

## Running Experiments

### Via API (preferred for programmatic use)
```bash
curl -X POST http://localhost:5005/api/experiments \
  -H "Content-Type: application/json" \
  -d '{"config": { ... }}'
```

### Via CLI
```bash
docker compose exec backend python -m experiments.run_ai_tournament \
    --experiment <name> --tournaments <N> --hands <N> --players <N>
```

### Via Config File
```bash
docker compose exec backend python -m experiments.run_from_config \
    experiments/configs/<config>.json
```

## Config Structure

### Basic Experiment (no A/B test)
```json
{
  "name": "descriptive_snake_case_name",
  "description": "What this tests",
  "num_tournaments": 3,
  "hands_per_tournament": 50,
  "num_players": 4,
  "model": "gpt-5-nano",
  "provider": "openai"
}
```

### A/B Test Structure
```json
{
  "name": "experiment_name",
  "num_tournaments": 5,
  "hands_per_tournament": 100,
  "reset_on_elimination": true,
  "model": "gpt-5-nano",
  "provider": "openai",
  "control": {
    "label": "Control Label"
  },
  "variants": [
    {
      "label": "Variant Label",
      "model": "gemini-2.0-flash",
      "provider": "google"
    }
  ]
}
```

Control uses experiment-level model/provider. Variants can override model, provider, prompt_config, reasoning_effort, game_mode, enable_psychology, enable_commentary.

### Key Parameters

| Parameter | Default | Notes |
|-----------|---------|-------|
| `num_tournaments` | 1 | 3-5 min, 10+ for reliable stats |
| `hands_per_tournament` | 100 | 50+ recommended |
| `num_players` | 4 | 2-8 |
| `starting_stack` | 10000 | |
| `big_blind` | 100 | |
| `reset_on_elimination` | false | Set `true` for A/B tests (exact hand counts) |
| `parallel_tournaments` | 1 | Set to variant count for speed |
| `stagger_start_delay` | 0.0 | 2.0+ to avoid rate limits |

### Available Models

| Provider | Models | Cost/Hand (4p) |
|----------|--------|----------------|
| `openai` | gpt-5-nano, gpt-4o, gpt-4o-mini | $0.002-$0.05 |
| `anthropic` | claude-sonnet-4-20250514, claude-opus-4 | ~$0.06 |
| `google` | gemini-2.0-flash, gemini-2.5-flash | $0.002-$0.01 |
| `groq` | llama-3.1-8b-instant | ~$0 |
| `mistral` | mistral-small-latest, mistral-medium-latest | ~$0.003 |
| `xai` | grok-4-fast, grok-3-mini | |
| `deepseek` | deepseek, deepseek-chat, deepseek-reasoner | |

### Prompt Config Toggles

Use in `prompt_config` within control/variants:

| Toggle | Default | Description |
|--------|---------|-------------|
| `pot_odds` | true | Pot odds guidance |
| `hand_strength` | true | Hand strength evaluation |
| `gto_equity` | false | Equity vs random + opponent ranges |
| `gto_verdict` | false | Explicit +EV/-EV verdict |
| `situational_guidance` | true | Coaching prompts |
| `include_personality` | true | Personality system prompt |
| `use_simple_response_format` | false | Simple JSON vs rich format |
| `session_memory` | true | Session stats |
| `opponent_intel` | true | Opponent tendencies |
| `strategic_reflection` | true | Past reflections |
| `emotional_state` | true | Emotional narrative |
| `tilt_effects` | true | Tilt modifications |

### Game Modes (presets)

| Mode | Effect |
|------|--------|
| `casual` | Default PromptConfig |
| `standard` | `gto_equity=true` |
| `pro` | `gto_equity=true, gto_verdict=true, chattiness=false, dramatic_sequence=false` |
| `competitive` | `gto_equity=true, gto_verdict=true` (with personality) |

### Rule-Based Bots

Add bots via `personalities` + `player_types`:

| Strategy | Bot Name | Description |
|----------|----------|-------------|
| `always_fold` | FoldBot | Folds everything |
| `always_call` | CallStation | Calls any bet |
| `always_raise` | AggBot | Raises max |
| `always_all_in` | YOLOBot | All-in every hand |
| `abc` | ABCBot | Simple ABC poker |
| `position_aware` | PositionBot | Position-based play |
| `pot_odds_robot` | GTO-Lite | Pure pot odds math |
| `maniac` | ManiacBot | Hyper-aggressive |
| `bluffbot` | BluffBot | Selective river bluffs |
| `case_based` | CaseBot | Case-based reasoning with opponent modeling |

Bot config example:
```json
{
  "personalities": ["Batman", "Tyler Durden", "Gordon Ramsay", "CaseBot"],
  "player_types": {
    "CaseBot": {"type": "rule_bot", "strategy": "case_based"}
  }
}
```

## Cost Estimation

```
Base cost = hands x players x 2 x (input_cost x ~1000 + output_cost x ~200) / 1M
With psychology: Base x 2
With commentary: Base x 2.5
```

Quick reference: 50 hands, 4 players, gpt-5-nano = ~$0.80/tournament

Always estimate costs before running and present to the user.

## Monitoring & Troubleshooting

### Check status
```bash
python3 scripts/dbq.py "SELECT id, name, status FROM experiments ORDER BY id DESC LIMIT 5"
```

### Check stalled variants
```bash
docker compose exec backend python -m experiments.resume_stalled -e <id> --list
```

### Resume experiment
```bash
curl -X POST http://localhost:5005/api/experiments/<id>/resume
```

### Common issues
- **"Only one variant ran"**: Check `parallel_tournaments` (default 1)
- **"Experiment stuck"**: Server may have restarted. Pause then resume.
- **"Rate limited"**: Add `stagger_start_delay`, reduce `parallel_tournaments`
- **"Tournament ended early"**: Players eliminated. Use `reset_on_elimination: true`

## Guidelines

- Always use `reset_on_elimination: true` for A/B tests
- Start with 1 tournament, 20 hands to verify config works
- Use cheap models (gpt-5-nano, gemini-2.0-flash) for iteration
- Run 5+ tournaments for meaningful comparisons
- Change ONE thing at a time between control and variant
- Use `stagger_start_delay: 2.0` with parallel execution

For full documentation, see `experiments/README.md`.
