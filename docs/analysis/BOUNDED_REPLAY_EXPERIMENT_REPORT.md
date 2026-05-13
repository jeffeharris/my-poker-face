---
purpose: Results from bounded replay experiment comparing option-framing variants on identical decision points
type: analysis
created: 2026-02-14
last_updated: 2026-02-15
---

# Bounded Replay Experiment Report

**Date:** February 14-15, 2026
**Experiment IDs:** 19 (gpt-5-nano), 22 (groq/llama-3.1-8b), 23 (gemini-2.0-flash)
**Source Data:** Experiment 16 (nudge_test), 817 preflop captures
**Models:** gpt-5-nano, llama-3.1-8b-instant (Groq), gemini-2.0-flash (Google)
**Total LLM Calls:** 36,765 (12,255 per model)
**Cost:** ~$0.50 (gpt-5-nano $0.48, Groq/Gemini negligible)

---

## Executive Summary

We replayed 817 frozen preflop decision points from experiment 16 through three option-framing variants, with 5 LLM samples per decision point, to isolate the effect of option presentation on AI player behavior. This eliminates game-state noise (different cards, seating, betting) that confounded our earlier live A/B tests.

| Metric | raw-ev | nudges | nudges+rangegate |
|--------|--------|--------|------------------|
| Archetype spread | 16.7pp | 22.1pp | **23.3pp** |
| Overall fold rate | 55% | 56% | 57% |
| Call rate | 15% | 7% | 7% |
| Raise rate | 26% | 31% | 30% |
| Sample agreement | 65% | 71% | **72%** |

**Conclusion:** Nudge phrases amplify archetype differentiation by 40% compared to raw EV labels on gpt-5-nano, reduce flat-calling by half, and produce more decisive LLM responses. The effect generalizes to Gemini Flash (1.30x amplification) but not to Llama-3.1-8b (no amplification, too noisy). Nudges+rangegate is the recommended production config for gpt-5-nano.

---

## Background

### The Problem

In experiment 16, we ran a 3-way A/B test comparing option-framing configs (raw-ev, nudges, nudges+rangegate) across live poker tournaments. Results showed different VPIP by archetype per variant, but we couldn't determine causality: were differences due to the option framing itself, or from noise in the different game states each variant encountered?

### The Solution: Bounded Replay

Replay the **same** 817 preflop decision points through **all three** option-framing configs. For each decision point:

1. **Keep constant:** Cards, hand classification, street/stack/pot, action history, style hint, system prompt, model
2. **Vary:** The numbered options section (EV labels vs nudge phrases, range gate biasing)
3. **Sample:** 5 independent LLM calls per variant to measure consistency

This isolates the option-framing effect from all other variables.

---

## Experimental Design

### Variants

| Variant | style_aware | composed_nudges | preflop_range_gate | Description |
|---------|------------|-----------------|-------------------|-------------|
| **raw-ev** | Yes | No | No | Options show EV brackets and math rationale: `1. CALL  [+EV]  Call 2.0 BB - clearly profitable` |
| **nudges** | Yes | Yes | No | Options show personality-colored phrases: `1. CALL — Easy call.` |
| **nudges+rangegate** | Yes | Yes | Yes | Same as nudges, but out-of-range hands get biased EV labels pushing toward fold |

### Controls

- **No emotional shift** — removed to eliminate noise variable
- **No option shuffle** — deterministic ordering to isolate framing effect
- **Same system prompt** — `LEAN_SYSTEM_PROMPT` used for all calls
- **Same prompts across models** — all three models see identical prompts per variant

### Players

| Player | Style Profile | Description |
|--------|--------------|-------------|
| Abraham Lincoln | tight_passive | Folds most hands, calls with strong holdings |
| Sun Tzu | tight_aggressive | Selective but aggressive when entering |
| Mark Twain | default | Balanced/default thresholds |
| Blackbeard | loose_aggressive | Wide range, pressures with raises |

---

## Results

### VPIP by Player x Variant

| Player | Profile | raw-ev | nudges | nudges+rangegate |
|--------|---------|--------|--------|------------------|
| Abraham Lincoln | tight_passive | 38.5% | 38.1% | 37.6% |
| Sun Tzu | tight_aggressive | 41.2% | 36.3% | 34.6% |
| Mark Twain | default | 44.4% | 43.2% | 42.4% |
| Blackbeard | loose_aggressive | 55.2% | 58.4% | 57.9% |

Sample sizes: ~1,000 per cell (5 samples x ~200 unique captures per player).

### Archetype Spread

The gap between the tightest and loosest player's VPIP:

| Variant | Spread | Range |
|---------|--------|-------|
| raw-ev | 16.7pp | 38.5% - 55.2% |
| nudges | 22.1pp | 36.3% - 58.4% |
| nudges+rangegate | **23.3pp** | 34.6% - 57.9% |

Nudges widen the spread by **40%** over raw-ev by pushing tight players tighter and loose players looser simultaneously.

### Archetype Ordering

| Variant | Ordering (VPIP low to high) |
|---------|---------------------------|
| raw-ev | TP (38.5%) < TAG (41.2%) < default (44.4%) < LAG (55.2%) |
| nudges | TAG (36.3%) < TP (38.1%) < default (43.2%) < LAG (58.4%) |
| nudges+rangegate | TAG (34.6%) < TP (37.6%) < default (42.4%) < LAG (57.9%) |

Under raw-ev, the expected poker archetype ordering holds: TP < TAG < default < LAG. Under nudges, Sun Tzu (TAG) drops below Lincoln (TP) — the nudge phrases amplify his aggression-driven selectivity so much that he becomes the tightest player. This is arguably correct behavior: a TAG *should* be very selective preflop.

### Action Distribution

| Action | raw-ev | nudges | nudges+rangegate |
|--------|--------|--------|------------------|
| fold | 55% | 56% | 57% |
| call | **15%** | 7% | 7% |
| raise | 26% | **31%** | 30% |
| all_in | 3% | **5%** | 4% |

Nudges cut flat-calling in half (15% to 7%) and redirect that volume into raises (26% to 31%) and all-ins (3% to 5%). This aligns with poker strategy — "raise or fold, avoid flat calls" — and suggests nudge phrases provide better strategic framing than raw EV labels.

### Per-Player Action Breakdown

**Blackbeard (LAG):**
- raw-ev: 45% fold, 8% call, 46% raise
- nudges: 42% fold, 1% call, 55% raise
- Nudges nearly eliminate his flat-calls, converting them to raises — ideal LAG behavior

**Sun Tzu (TAG):**
- raw-ev: 59% fold, 9% call, 27% raise
- nudges: 64% fold, 2% call, 26% raise
- Nudges increase his fold rate and eliminate calls, making him more selective

**Abraham Lincoln (TP):**
- raw-ev: 62% fold, 16% call, 16% raise
- nudges: 62% fold, 7% call, 23% raise
- Fold rate stays the same but calls convert to raises — more decisive

**Mark Twain (default):**
- raw-ev: 56% fold, 26% call, 15% raise
- nudges: 57% fold, 18% call, 20% raise
- Modest shift from calls to raises

### Sample Agreement (Consistency)

How often all 5 samples for a given capture x variant chose the same action:

| Variant | Unanimous | Rate |
|---------|-----------|------|
| raw-ev | 531/817 | 65% |
| nudges | 578/817 | 71% |
| nudges+rangegate | 588/817 | **72%** |

Nudge phrases produce 7pp higher agreement than raw-ev labels. This means nudges create less ambiguity for the LLM — the personality-colored language makes the "right" choice more obvious than mathematical EV labels, which the LLM may interpret inconsistently.

---

## Analysis

### Why Nudges Amplify Differentiation

Raw EV labels present objective math: `[+EV] Call 2.0 BB - clearly profitable`. Every archetype sees the same math and tends to follow it similarly, compressing VPIP differences. The LLM treats EV labels as authoritative recommendations.

Nudge phrases present subjective guidance colored by the player's style profile. A tight_aggressive profile gets `Disciplined fold.` where a loose_aggressive gets `Not worth it.` — same semantic meaning but different emotional weight. The LLM leans into the personality-congruent framing, amplifying behavioral differences.

### The Call-to-Raise Conversion

The most striking behavioral shift is the 50% reduction in flat-calling under nudges. This likely happens because:

1. **Raw EV format** presents calls as rational: `[+EV] Call 2.0 BB - clearly profitable` — the word "profitable" signals the call is good
2. **Nudge format** presents calls ambiguously: `Easy call.` or `Gamble.` — less authoritative than the math-based framing
3. **Nudge raises** use action-oriented language: `Keep the heat on.`, `Make them pay.` — more compelling than `Value bet (62% equity)`

The LLM interprets nudge-framed raises as more appealing than nudge-framed calls, shifting volume toward aggression.

### Range Gate: Modest but Consistent

The range gate adds 1-2pp of tightening across all players. Its effect is smaller than expected, likely because:

1. Many of the 817 captures already involve in-range hands (the source games used range-aware configs)
2. The nudge framing already pushes tight players to fold marginal hands
3. The biasing effect (shifting EV labels for out-of-range hands) is subtle compared to the wholesale rationale replacement that nudges perform

### Sun Tzu's Raw-EV Leak

Sun Tzu shows a 6.6pp VPIP gap between raw-ev (41.2%) and nudges+rangegate (34.6%). Under raw-ev, he's calling marginal hands when the math says "+EV" — even though a TAG should be folding these for range discipline. The nudge framing corrects this by replacing math justification with personality-congruent language that reinforces selectivity.

---

## Multi-Model Comparison

We re-ran the identical 12,255-call replay on Groq/Llama-3.1-8b and Gemini 2.0 Flash to test whether the nudge framing effect generalizes beyond gpt-5-nano.

### VPIP by Model (nudges+rangegate)

| Player | Profile | gpt-5-nano | Gemini Flash | Groq/Llama |
|--------|---------|------------|-------------|------------|
| Abraham Lincoln | tight_passive | 37.6% | 39.8% | 73.3% |
| Sun Tzu | tight_aggressive | 34.6% | 34.2% | 64.9% |
| Mark Twain | default | 42.4% | 37.7% | 62.8% |
| Blackbeard | loose_aggressive | 57.9% | 52.2% | 89.1% |

### Key Metrics by Model

| Metric | gpt-5-nano | Gemini Flash | Groq/Llama |
|--------|------------|-------------|------------|
| Archetype spread (nudges+rg) | 23.3pp | 18.0pp | 26.2pp |
| Nudge amplification | **1.40x** | **1.30x** | 0.99x |
| Sample agreement | 72% | **81%** | 24% |
| Parse errors | 0.7% | **0.0%** | 12.6% |
| Fold rate (nudges+rg) | 57% | 59% | 27% |

### Archetype Ordering (nudges+rangegate)

| Model | Ordering (VPIP low to high) |
|-------|---------------------------|
| gpt-5-nano | TAG (34.6%) < TP (37.6%) < default (42.4%) < LAG (57.9%) |
| Gemini Flash | TAG (34.2%) < default (37.7%) < TP (39.8%) < LAG (52.2%) |
| Groq/Llama | default (62.8%) < TAG (64.9%) < TP (73.3%) < LAG (89.1%) |

### Nudge Amplification by Model

How much does switching from raw-ev to nudges+rangegate widen the archetype spread?

| Model | raw-ev spread | nudges+rg spread | Amplification |
|-------|--------------|------------------|---------------|
| gpt-5-nano | 16.7pp | 23.3pp | **1.40x** |
| Gemini Flash | 13.9pp | 18.0pp | **1.30x** |
| Groq/Llama | 26.5pp | 26.2pp | 0.99x (none) |

### Model-Specific Findings

**Gemini 2.0 Flash — Strong instruction-follower, weak archetype ordering**

Gemini produces the cleanest output: 0% parse errors, 81% sample agreement (highest of all three). Its nudge amplification (1.30x) is close to gpt-5-nano. The fold rate (59%) and action distribution are nearly identical to gpt-5-nano. However, it swaps the TP and default archetypes — Lincoln (tight_passive) plays looser than Mark Twain (default), suggesting Gemini doesn't differentiate the style hint text between these two profiles as well.

**Groq/Llama-3.1-8b — Too loose, too noisy, no amplification**

Llama produces fundamentally different behavior: only 27% fold rate (vs 57-59% for the other models), 12.6% parse errors, and only 24% sample agreement. It shows zero nudge amplification — the framing variants make almost no difference to its behavior.

The core problem is visible in the reasoning text. When gpt-5-nano sees a weak hand with a "Disciplined fold" nudge, it folds. When Llama sees the same hand with the same nudge, it acknowledges the hand is bad but rationalizes a raise anyway: *"This is a weak unconnected hand... but pre-flop aggression will keep my opponent honest."* It treats nudge phrases as suggestions rather than directives, defaulting to generic poker clichés like "apply pressure" and "test the waters."

The archetype ordering is also broken: default < TAG < TP < LAG, with the tightest player (Mark Twain/default at 62.8%) still playing more hands than Blackbeard does on gpt-5-nano (57.9%).

### Model Ranking for Bounded Options

1. **gpt-5-nano** — Best nudge amplification (1.40x), reasonable ordering, good agreement (72%)
2. **Gemini 2.0 Flash** — Cleanest JSON (0% errors), highest agreement (81%), good amplification (1.30x), but TP/default archetype swap
3. **Groq/Llama-3.1-8b** — Too loose, too noisy, no nudge amplification, broken ordering — not viable for personality-driven poker

---

## Methodology Notes

### Prompt Reconstruction

Each replay prompt was built by:
1. Extracting the header from the original captured `user_message` (everything above the numbered options)
2. Regenerating the options section using `generate_bounded_options()` with the variant's config
3. Optionally applying `apply_composed_nudges()` for nudge variants
4. Combining header + options into a new `user_message`

### Missing Metadata Handling

Captures from experiment 16 predated the enriched metadata fields (position, big_blind, canonical_hand, etc.). The `reconstruct_rule_context()` function derives missing fields:
- `big_blind` from `player_stack / stack_bb`
- `canonical_hand` from `player_hand` via `_get_canonical_hand()`
- `position` defaults to `'button'` with a warning (most permissive, known bias)
- `num_opponents` defaults to 3 (4-player game)

### Limitations

1. **Position defaulting**: All captures without position metadata default to 'button', which may overstate the range gate's permissiveness
2. **Preflop only**: This analysis covers only preflop decisions; postflop framing effects may differ
3. **No equity recalculation**: Replay uses the original equity values, not recalculated ones
4. **Groq string-typed choice**: Llama returns `"choice": "1"` (string) instead of `"choice": 1` (integer), requiring coercion. 12.6% of responses had genuinely malformed JSON (typos like `"choise"`, missing fields)

---

## Recommendations

1. **Ship nudges+rangegate as default config for gpt-5-nano.** It produces the best archetype differentiation (23.3pp spread), highest sample agreement (72%), and healthiest action distribution (low flat-call rate).

2. **Gemini Flash is a viable alternative.** Strong instruction-following (81% agreement, 0% parse errors) and meaningful nudge amplification (1.30x). The TP/default archetype swap could potentially be fixed with stronger style hint differentiation.

3. **Do not use Llama-3.1-8b for personality-driven poker.** It lacks the instruction-following fidelity to respect bounded option framing, defaulting to generic "be aggressive" poker advice regardless of archetype or nudge text.

4. **Retire raw-ev for personality-driven games.** Raw EV labels compress archetype differences and encourage flat-calling across all models. Only use raw-ev for analytical/debugging purposes.

5. **Investigate postflop framing.** This experiment only tested preflop. The 105 FLOP + 48 TURN + 35 RIVER captures from experiment 16 could be replayed to test postflop framing effects.

6. **Test Claude Haiku.** With two data points showing nudge amplification scales with instruction-following quality, Claude Haiku (known for strong instruction adherence) may produce even better results.

---

## Reproduction

```bash
# Dry run (shows capture count and cost estimate)
docker compose exec backend python -m experiments.run_bounded_replay \
    experiments/configs/bounded_replay_template.json --dry-run

# Full run
docker compose exec backend python -m experiments.run_bounded_replay \
    experiments/configs/bounded_replay_template.json

# Query results
python3 scripts/dbq.py "SELECT variant, new_action, COUNT(*) FROM bounded_replay_results WHERE experiment_id = 19 GROUP BY variant, new_action"
```

### Config

```json
{
  "name": "bounded-replay-preflop",
  "source_experiment_id": 16,
  "capture_filters": {"phase": "PRE_FLOP"},
  "variants": [
    {"label": "raw-ev", "style_aware_options": true, "composed_nudges": false, "preflop_range_gate": false},
    {"label": "nudges", "style_aware_options": true, "composed_nudges": true, "preflop_range_gate": false},
    {"label": "nudges+rangegate", "style_aware_options": true, "composed_nudges": true, "preflop_range_gate": true}
  ],
  "samples_per_variant": 5,
  "model": "gpt-5-nano",
  "provider": "openai",
  "max_workers": 10
}
```

### Multi-Model Configs

```bash
# Groq/Llama
docker compose exec backend python -m experiments.run_bounded_replay \
    experiments/configs/bounded_replay_groq.json

# Gemini Flash
docker compose exec backend python -m experiments.run_bounded_replay \
    experiments/configs/bounded_replay_gemini.json
```

### Database Tables

- **bounded_replay_results**: 36,765 rows across 3 experiments (schema v77)
- **prompt_captures**: Source captures from experiment 16 (817 preflop)
- **experiments**: Experiment 19 (gpt-5-nano), 22 (groq), 23 (gemini)
