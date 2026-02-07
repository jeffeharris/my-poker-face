# Bet Sizing Analysis Report

**Generated:** February 2026
**Data Source:** `data/poker_games.db` tables: `player_decision_analysis`, `experiment_games`
**Primary Dataset:** Experiment 60 (gpt-5-nano, 1453 decisions, 133 raises, 10 all-ins)
**Secondary Dataset:** Experiment 64 (Gemini 2.0 Flash, 3022 decisions, 814 raises, 212 all-ins)
**Status:** Analysis complete — recommendations included

---

## Executive Summary

Bet sizing with gpt-5-nano is **functional but imperfect**. Pre-flop opens are reasonable (avg 4.2 BB), postflop sizing is pot-appropriate when excluding one extreme outlier, and players almost never raise with garbage hands. The main issues are:

1. **Pre-flop 3-bet sizing correlates with aggression** — working as intended
2. **Postflop raises are almost exclusively value bets** (avg equity 85%+) — there's almost no bluffing
3. **One catastrophic outlier** — Napoleon bet 460 BB (23,000 chips) into a 125-chip pot on the turn
4. **Pre-flop overbets (>6x) are too frequent** — 29% of preflop raises, mostly 3-bets that overshoot
5. **Personality differentiation is weak for sizing** — aggression affects *frequency* more than *size*

Gemini 2.0 Flash is dramatically worse: average pre-flop opens of 100 BB, 55x pot-ratio averages for aggressive players, and 212 all-ins (vs 10 for gpt-5-nano). Not worth analyzing further.

**Verdict: Sizing is "good enough" for now.** The prompt's betting discipline section prevents degenerate play. The biggest gap is the absence of bluffing on later streets, but that's a feature design question, not a sizing bug.

---

## Table of Contents

1. [Player Profiles](#player-profiles)
2. [Pre-Flop Open Sizing](#pre-flop-open-sizing)
3. [Pre-Flop 3-Bet Sizing](#pre-flop-3-bet-sizing)
4. [Postflop Sizing Patterns](#postflop-sizing-patterns)
5. [Sizing vs Hand Strength](#sizing-vs-hand-strength)
6. [All-In Decision Quality](#all-in-decision-quality)
7. [Psychology State Effects](#psychology-state-effects)
8. [Model Comparison: gpt-5-nano vs Gemini](#model-comparison-gpt-5-nano-vs-gemini)
9. [Scorecard](#scorecard)
10. [Recommendations](#recommendations)
11. [Queries for Reproduction](#queries-for-reproduction)

---

## Player Profiles

| Player | Aggression | Risk ID | Looseness | Ego | Raises | Avg BB | Avg Pot Ratio |
|--------|-----------|---------|-----------|-----|--------|--------|---------------|
| Napoleon | 0.80 | 0.80 | 0.79 | 0.86 | 45 | 16.2 | 5.96* |
| Blackbeard | 0.90 | 0.89 | 0.87 | 0.88 | 36 | 7.8 | 1.90 |
| Sherlock Holmes | 0.60 | 0.59 | 0.58 | 0.42 | 27 | 5.3 | 2.23 |
| Bob Ross | 0.10 | 0.21 | 0.38 | 0.50 | 25 | 7.0 | 1.97 |

*Napoleon's average is inflated by one 460 BB outlier. Excluding it: avg 6.4 BB.

**Key takeaway:** Raise *frequency* correlates with aggression (Napoleon 45, Blackbeard 36 vs Bob Ross 25), but raise *size* does not clearly differentiate. All four players average 5-8 BB when excluding Napoleon's outlier.

---

## Pre-Flop Open Sizing

Opens are raises when facing no prior raise (cost_to_call <= 1 BB).

| Player | Opens | Avg BB | Min | Max | 2.0 BB (min) | 3.0 BB | 3.5-4.0 BB | 5-6 BB | 7-8 BB |
|--------|-------|--------|-----|-----|---------------|--------|------------|--------|--------|
| Napoleon | 27 | 3.9 | 2.0 | 8.0 | 1 | 13 | 5 | 4 | 4 |
| Sherlock | 20 | 4.2 | 2.5 | 8.0 | 0 | 8 | 6 | 1 | 5 |
| Blackbeard | 18 | 4.3 | 2.5 | 8.0 | 0 | 6 | 5 | 4 | 4 |
| Bob Ross | 14 | 4.5 | 2.0 | 8.0 | 1 | 5 | 2 | 4 | 4 |

**Sizing Category Breakdown (103 pre-flop raises total):**

| Category | Count | % | Assessment |
|----------|-------|---|------------|
| Standard (2.5-4.0x) | 51 | 49.5% | Good |
| Large (4-6x) | 15 | 14.6% | Acceptable |
| Overbet (>6x) | 30 | 29.1% | Too many |
| Min-raise (<=2.5x) | 7 | 6.8% | Few, OK |

**Assessment: Mixed.** Half of pre-flop raises are standard (2.5-4x), which is good. The 29% overbet rate is concerning — these are mostly 7-8 BB opens and 3-bets rather than true "opens," but some are genuine 8 BB opens from the button which is too large. Min-raises are rare (7%), which is positive.

**Position awareness:** Open sizing shows weak position correlation:

| Player | BB Position | Button | SB | UTG |
|--------|------------|--------|-----|-----|
| Napoleon | 3.6 | 3.8 | 4.9 | 3.8 |
| Sherlock | 3.0 | 4.5 | 4.4 | 3.3 |
| Blackbeard | 3.0 | 4.2 | 2.8 | 8.0 |
| Bob Ross | 5.0 | 4.4 | 4.5 | N/A |

Position awareness is essentially absent — sizes are similar regardless of position. This is an area where guidance could help (smaller from button, larger from UTG).

---

## Pre-Flop 3-Bet Sizing

3-bets (re-raises when facing a prior raise):

| Player | 3-Bets | Avg BB | Assessment |
|--------|--------|--------|------------|
| Sherlock | 2 | 8.8 | ~3x the open — good |
| Bob Ross | 6 | 10.4 | Reasonable |
| Blackbeard | 8 | 13.6 | Slightly large |
| Napoleon | 8 | 15.8 | Large but fits personality |

**Assessment: Personality-correlated and working.** 3-bet sizing follows aggression ordering: Napoleon (0.80) and Blackbeard (0.90) 3-bet bigger than Sherlock (0.60) and Bob Ross (0.10). This is the clearest personality signal in the data. Values are at the high end but within reason for a 4-player game where 3-bets carry more leverage.

---

## Postflop Sizing Patterns

30 postflop raises total (6 flop, 8 turn, 16 river). Excluding Napoleon's 460 BB outlier:

| Street | Raises | Avg BB | Avg Pot Ratio | Assessment |
|--------|--------|--------|---------------|------------|
| Flop | 6 | 5.2 | 1.05x pot | Slightly large but OK |
| Turn | 7 | 10.8 | 1.02x pot | About pot-sized — fine |
| River | 16 | 7.3 | 1.10x pot | Clustered around pot — good |

**Category Breakdown (postflop, 30 raises):**

| Category | Count | % | Assessment |
|----------|-------|---|------------|
| Standard (33-75% pot) | 13 | 43% | Good |
| Large (75-120% pot) | 9 | 30% | Acceptable |
| Overbet (>120% pot) | 7 | 23% | Slightly high |
| Small (<33% pot) | 1 | 3% | Rare |

**Assessment: Reasonable.** 73% of postflop raises fall in the standard-to-large range (33-120% pot), which is solid. Overbets at 23% are slightly high but most are only marginally over (1.2-1.6x pot), not wildly degenerate.

**The Napoleon outlier:** One turn raise of 23,000 chips into a 125-chip pot (184x pot). This is the single worst sizing decision in the dataset — Napoleon had 97.9% equity and a 1,450 chip stack, so this appears to be a `raise_to` vs `raise_by` confusion or a hallucinated number. This single data point inflates Napoleon's turn average from 8.0 BB to 234 BB and his overall pot ratio from ~2.0 to 5.96.

**Per-player postflop pot ratios (excluding Napoleon outlier):**

| Player | Postflop Raises | Avg Pot Ratio | Avg Equity |
|--------|----------------|---------------|------------|
| Sherlock | 5 | 0.75 | 0.914 |
| Blackbeard | 10 | 1.10 | 0.831 |
| Bob Ross | 5 | 1.43 | 0.931 |
| Napoleon | 9* | 1.06* | 0.815* |

*Excluding the 460 BB outlier.

All four players are in a reasonable range. Sherlock is slightly conservative, Bob Ross slightly aggressive (surprising given his personality), but the sample sizes are small (5-10 raises each).

---

## Sizing vs Hand Strength

**Question:** Do players bet bigger with strong hands (value) and smaller with weak hands?

| Player | Strong (70%+) | Medium (40-70%) | Weak (<40%) |
|--------|--------------|-----------------|-------------|
| | Raises / Avg BB / Pot Ratio | Raises / Avg BB / Pot Ratio | Raises / Avg BB / Pot Ratio |
| Blackbeard | 10 / 10.4 BB / 1.14 | 19 / 7.2 BB / 2.13 | 7 / 5.6 BB / 2.39 |
| Bob Ross | 8 / 8.0 BB / 1.89 | 15 / 6.5 BB / 2.11 | 2 / 7.5 BB / 1.23 |
| Napoleon* | 12 / 42.8 BB / 16.35 | 27 / 7.2 BB / 2.18 | 6 / 3.5 BB / 2.16 |
| Sherlock | 8 / 7.1 BB / 1.43 | 16 / 4.7 BB / 2.62 | 3 / 3.3 BB / 2.22 |

*Napoleon's strong-hand average includes the 460 BB outlier. Excluding it: ~6.9 BB.

**Key findings:**

1. **BB sizing scales with hand strength for all players.** Strong hands get the biggest bets (7-10 BB avg), weak hands get the smallest (3-6 BB avg). This is correct behavior — value betting bigger.

2. **Pot ratios are inverted** because strong hands tend to occur postflop where pots are already large, while weak-hand raises are mostly preflop where the pot ratio is naturally higher.

3. **Weak-hand raises are rare.** Only 18 out of 133 raises (14%) occur with <40% equity. This means AI players almost never bluff-raise, which is conservative but not terrible for lower-stakes play.

**Postflop bluff raises (equity < 50%):**

| Player | Postflop Raises | With <50% Equity | Details |
|--------|----------------|-----------------|---------|
| Blackbeard | 10 | 1 | River 67% pot with 2.5% equity (actual bluff!) |
| Napoleon | 10 | 1 | River 1.6x pot with 36% equity (semi-value?) |
| Bob Ross | 5 | 0 | Never bluffs postflop |
| Sherlock | 5 | 0 | Never bluffs postflop |

**Assessment: Value-heavy but not broken.** Players correctly size bigger with better hands. The near-absence of postflop bluffs is a strategic gap but consistent with the prompt's "BIG BETS require hand strength" guidance. The one genuine bluff (Blackbeard, 2.5% equity river raise) shows the potential is there.

---

## All-In Decision Quality

10 all-ins in experiment 60:

| Player | Phase | Equity | Stack | Pot | SPR | Assessment |
|--------|-------|--------|-------|-----|-----|------------|
| Bob Ross | Flop | 23.5% | 25 | 450 | 0.1 | OK (pot committed) |
| Bob Ross | Flop | 33.1% | 75 | 3500 | 0.0 | OK (pot committed) |
| Blackbeard | Pre | 35.6% | 25 | 75 | 0.3 | OK (short stack) |
| Sherlock | Pre | 47.2% | 125 | 150 | 0.8 | Marginal but SPR low |
| Napoleon | Pre | 47.9% | 75 | 3725 | 0.0 | OK (pot committed) |
| Sherlock | Pre | 49.7% | 125 | 275 | 0.5 | OK (low SPR) |
| Blackbeard | Turn | 54.9% | 100 | 1950 | 0.1 | OK (pot committed) |
| Napoleon | Pre | 55.0% | 25 | 625 | 0.0 | OK (short stack) |
| Blackbeard | Pre | 60.9% | 600 | 2350 | 0.3 | OK (low SPR) |
| Napoleon | Turn | 73.6% | 75 | 1875 | 0.0 | OK (pot committed) |

**All-in equity distribution:**

| Bucket | Count | Assessment |
|--------|-------|------------|
| Strong (70%+) | 1 | Good |
| Medium (40-70%) | 6 | Acceptable (all low SPR) |
| Weak (<40%) | 3 | All pot-committed or short-stacked |

**Assessment: Excellent.** Every all-in is justified by either equity (55%+) or stack pressure (SPR < 1). The three low-equity all-ins (23-36%) are all pot-committed situations where folding would be mathematically incorrect. No degenerate deep-stack shoves with garbage hands.

---

## Psychology State Effects

**Confidence effect on sizing:**

| Confidence | Raises | Avg BB | Avg Pot Ratio |
|-----------|--------|--------|---------------|
| Low (<0.3) | 10 | 6.6 | 2.06 |
| Mid (0.3-0.7) | 64 | 6.6 | 1.90 |
| High (>0.7) | 59 | 14.2 | 5.14* |

*High-confidence bucket includes the Napoleon outlier.

**Composure effect on sizing:**

| Composure | Raises | Avg Pot Ratio |
|----------|--------|---------------|
| Mid (0.3-0.7) | 99 | 1.99 |
| High (>0.7) | 34 | 7.32* |

*High-composure bucket includes the Napoleon outlier. No low-composure raises observed.

**Energy effect on sizing:**

| Energy | Raises | Avg BB | Avg Pot Ratio |
|--------|--------|--------|---------------|
| Low (<0.3) | 52 | 6.0 | 2.08 |
| Mid (0.3-0.7) | 81 | 12.5 | 4.17* |

No high-energy raises observed.

**Assessment: Inconclusive.** The Napoleon outlier dominates all psychology buckets it falls into, making it hard to draw conclusions. After removing it, the differences are small. The one potentially real signal is that low-energy players bet slightly smaller (6.0 BB vs 12.5 BB mid-energy), which could indicate the psychology system is working — fatigued players are more conservative. But sample sizes are too small to be confident.

---

## Model Comparison: gpt-5-nano vs Gemini

| Metric | gpt-5-nano (Exp 60) | Gemini Flash (Exp 64) |
|--------|--------------------|-----------------------|
| Total decisions | 1,453 | 3,022 |
| Raises | 133 (9.2%) | 814 (26.9%) |
| All-ins | 10 (0.7%) | 212 (7.0%) |
| Avg pre-flop open | 4.2 BB | 100.3 BB |
| Avg postflop pot ratio | 1.07x | ~25x |
| Raise rate | 9.2% | 26.9% |

**Gemini player sizing (avg pot ratio):**

| Player | Raises | Avg Pot Ratio | Assessment |
|--------|--------|---------------|------------|
| Blackbeard | 245 | 55.6x | Catastrophic |
| Napoleon | 248 | 37.0x | Catastrophic |
| Sherlock | 195 | 21.9x | Very bad |
| Bob Ross | 126 | 1.5x | Reasonable! |

**Gemini sizing categories (pre-flop):**

| Category | Count | % |
|----------|-------|---|
| Standard (2.5-4x) | 114 | 30.6% |
| Min-raise (<=2.5x) | 81 | 21.8% |
| Overbet (>6x) | 151 | 40.6% |
| Large (4-6x) | 26 | 7.0% |

**Gemini all-in equity distribution:**

| Bucket | Count | % |
|--------|-------|---|
| Medium (40-70%) | 95 | 44.8% |
| Weak (<40%) | 73 | 34.4% |
| Strong (70%+) | 44 | 20.8% |

**Assessment: Gemini sizing is broken.** 40% of preflop raises are overbets, average postflop pot ratios are 20-55x for aggressive players, and 34% of all-ins have less than 40% equity. Bob Ross is the only player with reasonable sizing (1.5x pot avg), likely because his passive personality restrains the model's tendency to shove.

This confirms what the range guidance report found: Gemini Flash doesn't follow sizing instructions. The model treats raise amounts as almost arbitrary numbers, frequently shoving or making pot-ratio-irrelevant bets.

---

## Scorecard

| Metric | Target | gpt-5-nano | Grade | Gemini | Grade |
|--------|--------|-----------|-------|--------|-------|
| Pre-flop opens | 2.5-3.5x | 4.2x avg | B | 100x avg | F |
| Postflop sizing | 33-100% pot | ~100% pot | B+ | ~2500% pot | F |
| Personality in frequency | Aggressive > tight | Yes (45 vs 25) | A | Yes | B |
| Personality in sizing | Aggressive > tight | Weak signal | C | Bob Ross only | D |
| Equity correlation | Bigger = better hand | Yes (BB sizing) | A | Inverted for some | F |
| All-in quality | >50% or pot-committed | All justified | A+ | 34% with <40% eq | F |
| Min-raise rate | <10% | 6.8% | A | 21.8% | D |
| Overbet rate (preflop) | <15% | 29.1% | C | 40.6% | F |
| Postflop bluffing | Some present | 2 bluffs / 30 raises | D | N/A | N/A |

**Overall gpt-5-nano grade: B.** Sizing is functional, value-oriented, and not exploitatively bad. The main gaps are pre-flop overbet frequency and the complete absence of postflop bluffing.

**Overall Gemini grade: F.** Sizing is broken at a fundamental level. Not worth investing in sizing guidance for this model.

---

## Recommendations

### No action needed (working well)
- All-in decision quality — every shove is justified
- Value betting — bigger bets with better hands
- Raise frequency by personality — aggressive players raise more
- 3-bet sizing personality differentiation

### Could improve with guidance (low priority)
1. **Pre-flop open standardization:** Add "standard opens are 2.5-3x" to prompt. Would reduce the 29% overbet rate. Simple text addition.
2. **Position-based sizing:** "Open smaller from button (2.5x), larger from UTG (3x)." Currently no position awareness.
3. **Postflop sizing framing:** Add "size your bets relative to the pot — half-pot to pot-sized is standard." Players already do this roughly but inconsistently.

### Deferred (design decisions needed)
4. **Bluff encouragement:** The prompt actively discourages bluffing ("BIG BETS require hand strength"). To enable balanced play, we'd need to carve out exceptions for board texture, position, and bet size. This is a feature, not a bug fix.
5. **Personality sizing differentiation:** Aggressive players don't bet bigger — they bet more often. Making size vary by personality would require explicit per-personality sizing guidance, which risks conflicting with pot-relative sizing.

### Not worth pursuing
- Gemini Flash bet sizing — model can't follow instructions
- Psychology-based sizing differentiation — sample too small, effects too weak
- `bet_sizing` text field analysis — 94% empty, model doesn't use it

---

## Queries for Reproduction

### Overall raise distribution
```sql
SELECT action_taken, COUNT(*) as cnt
FROM player_decision_analysis pda
JOIN experiment_games eg ON pda.game_id = eg.game_id
WHERE eg.experiment_id = 60
GROUP BY action_taken ORDER BY cnt DESC;
```

### Pre-flop open sizes by player
```sql
SELECT pda.player_name, pda.raise_amount_bb, COUNT(*) as cnt
FROM player_decision_analysis pda
JOIN experiment_games eg ON pda.game_id = eg.game_id
WHERE eg.experiment_id = 60
    AND pda.action_taken = 'raise'
    AND pda.phase = 'PRE_FLOP'
    AND pda.cost_to_call <= 50
GROUP BY pda.player_name, pda.raise_amount_bb
ORDER BY pda.player_name, pda.raise_amount_bb;
```

### Postflop sizing with pot ratios
```sql
SELECT pda.player_name, pda.phase, pda.raise_amount, pda.pot_total,
    ROUND(1.0 * pda.raise_amount / NULLIF(pda.pot_total, 0), 2) as pot_ratio,
    pda.equity, pda.cost_to_call
FROM player_decision_analysis pda
JOIN experiment_games eg ON pda.game_id = eg.game_id
WHERE eg.experiment_id = 60
    AND pda.action_taken = 'raise'
    AND pda.phase IN ('FLOP', 'TURN', 'RIVER')
ORDER BY pda.phase, pot_ratio;
```

### Sizing vs hand strength by player
```sql
SELECT pda.player_name,
    CASE WHEN pda.equity >= 0.7 THEN 'strong' WHEN pda.equity >= 0.4 THEN 'medium' ELSE 'weak' END as bucket,
    COUNT(*) as raises,
    ROUND(AVG(pda.raise_amount_bb), 1) as avg_bb,
    ROUND(AVG(CASE WHEN pda.pot_total > 0 THEN 1.0 * pda.raise_amount / pda.pot_total END), 2) as avg_pot_ratio
FROM player_decision_analysis pda
JOIN experiment_games eg ON pda.game_id = eg.game_id
WHERE eg.experiment_id = 60 AND pda.action_taken = 'raise'
GROUP BY pda.player_name, bucket
ORDER BY pda.player_name, bucket;
```

### All-in quality check
```sql
SELECT pda.player_name, pda.phase, pda.equity, pda.player_stack, pda.pot_total,
    ROUND(1.0 * pda.player_stack / NULLIF(pda.pot_total, 0), 1) as spr
FROM player_decision_analysis pda
JOIN experiment_games eg ON pda.game_id = eg.game_id
WHERE eg.experiment_id = 60 AND pda.action_taken = 'all_in'
ORDER BY pda.equity;
```

### Model comparison summary
```sql
SELECT eg.experiment_id,
    ROUND(AVG(CASE WHEN pda.phase = 'PRE_FLOP' AND pda.cost_to_call <= 50 THEN pda.raise_amount_bb END), 1) as avg_open_bb,
    COUNT(CASE WHEN pda.action_taken = 'all_in' THEN 1 END) as allins,
    COUNT(CASE WHEN pda.action_taken = 'raise' THEN 1 END) as raises,
    COUNT(*) as total
FROM player_decision_analysis pda
JOIN experiment_games eg ON pda.game_id = eg.game_id
WHERE eg.experiment_id IN (60, 64)
GROUP BY eg.experiment_id;
```

---

## Key Files

| File | Purpose |
|------|---------|
| `scripts/dbq.py` | Query utility used for all analysis |
| `poker/prompts/decision.yaml` | Betting discipline text in prompt |
| `poker/controllers.py:2063` | Raise range guidance in prompt |
| `poker/response_validator.py:84` | Validates raise_to and bet_sizing |
| `poker/personalities.json` | Player anchor values |
| `docs/analysis/RANGE_GUIDANCE_EXPERIMENT_REPORT.md` | Related preflop analysis |
