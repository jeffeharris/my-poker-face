# AI Decision Quality Analysis Report

**Generated:** January 2026
**Data Source:** `data/poker_games.db` tables: `player_decision_analysis`, `prompt_captures`, `opponent_models`, `hand_history`
**Total Decisions Analyzed:** 26,245 prompt captures, 24,996 analyzed decisions

---

## Executive Summary

This report analyzes AI player decision quality across multiple poker scenarios, identifying systematic errors and recommending prompt/logic improvements. Key findings:

| Issue | Occurrences | EV Lost | Severity |
|-------|-------------|---------|----------|
| Folding good hands | 1,785 | 2,273,029 | HIGH |
| Pot-committed folds | 670 | 1,399,679 | HIGH |
| Raise wars (3+ raises/round) | 553 rounds | N/A | MEDIUM |
| Bad all-ins | 57 | ~65,000 | MEDIUM |
| Short stack fold mistakes | 212 | ~150,000 | MEDIUM |

---

## Table of Contents

1. [Scenario 1: Folding Good Hands](#scenario-1-folding-good-hands)
2. [Scenario 2: Raise Wars](#scenario-2-raise-wars)
3. [Scenario 3: Bad All-Ins](#scenario-3-bad-all-ins)
4. [Scenario 4: Folding Into Death (Short Stack)](#scenario-4-folding-into-death)
5. [Scenario 5: Pot-Committed Folds](#scenario-5-pot-committed-folds)
6. [Model Comparison](#model-comparison)
7. [Personality Analysis](#personality-analysis)
8. [Recommendations](#recommendations)

---

## Scenario 1: Folding Good Hands

### Label: `FOLD_MISTAKE`

### Description
Players fold hands with positive expected value, losing equity they should have captured.

### Key Statistics
- **Total folds:** 4,045
- **Fold mistakes:** 1,785 (44.1%)
- **Total EV lost:** 2,273,029
- **Average EV lost per mistake:** 1,273

### Breakdown by Phase

| Phase | Mistakes | Total EV Lost | Avg EV Lost | Avg Equity When Folded |
|-------|----------|---------------|-------------|------------------------|
| PRE_FLOP | 1,140 | 626,706 | 550 | 40.4% |
| RIVER | 147 | 618,651 | 4,209 | 53.0% |
| TURN | 202 | 566,762 | 2,806 | 43.7% |
| FLOP | 213 | 411,587 | 1,932 | 42.3% |

### Query to Identify

```sql
-- All fold mistakes
SELECT
    player_name,
    phase,
    equity,
    ev_lost,
    pot_total,
    cost_to_call,
    player_hand,
    community_cards,
    optimal_action
FROM player_decision_analysis
WHERE action_taken = 'fold'
  AND decision_quality = 'mistake'
ORDER BY ev_lost DESC;

-- Summary by phase
SELECT
    phase,
    COUNT(*) as fold_mistakes,
    SUM(ev_lost) as total_ev_lost,
    AVG(ev_lost) as avg_ev_lost,
    AVG(equity) as avg_equity_when_folded
FROM player_decision_analysis
WHERE action_taken = 'fold'
  AND decision_quality = 'mistake'
GROUP BY phase
ORDER BY total_ev_lost DESC;
```

### Root Causes
1. **Pre-flop heads-up:** AI folds "ugly" hands (T7o, J8o) that have ~47% equity heads-up
2. **River with made hands:** AI folds pairs/draws when facing aggression
3. **Misunderstanding pot odds:** AI sees odds but doesn't compare to actual equity

---

## Scenario 2: Raise Wars

### Label: `RAISE_WAR`

### Description
Multiple raises (3+) in a single betting round, creating unrealistic betting sequences.

### Key Statistics
- **Total betting rounds:** 7,075
- **Rounds with raise wars (3+):** 553 (7.82%)
- **Extreme wars (7+ raises):** 66 (0.93%)
- **Most extreme:** 21 raises in one pre-flop round

### Distribution by Raise Count

| Raises per Round | Occurrences | Percentage |
|-----------------|-------------|------------|
| 0 | 3,564 | 50.4% |
| 1 | 2,357 | 33.3% |
| 2 | 601 | 8.5% |
| 3 | 283 | 4.0% |
| 4 | 112 | 1.6% |
| 5 | 53 | 0.75% |
| 6 | 39 | 0.55% |
| 7+ | 66 | 0.93% |

### By Model

| Model | Raise War Rate | Notes |
|-------|---------------|-------|
| mistral-small-latest | 30.4% | HIGHEST - gets stuck in loops |
| llama-3.3-70b-versatile | 15.7% | High |
| llama-3.1-8b-instant | 11.4% | Moderate |
| gemini-2.0-flash | 8.0% | Moderate |
| grok-4-fast | 5.0% | Low |
| gpt-5-nano | 3.4% | LOWEST |

### Query to Identify

```python
# From prompt_captures, group by game_id, hand_number, phase
# Count raises per group

SELECT game_id, hand_number, phase, player_name, action_taken,
       pot_total, raise_amount, cost_to_call, created_at
FROM prompt_captures
WHERE action_taken IS NOT NULL
ORDER BY game_id, hand_number, phase, created_at;

# Then in Python:
# Group actions by (game_id, hand_number, phase)
# Count actions where action in ('raise', 'all_in')
# Flag rounds with count >= 3 as RAISE_WAR
```

### Root Causes
1. **No raise cap:** Real poker has 3-4 raise cap per round (except heads-up)
2. **Model behavior:** Mistral/Llama models don't recognize when to stop
3. **Personality traits:** "Aggressive" personalities (Trump, Ramsay) compound the issue

---

## Scenario 3: Bad All-Ins

### Label: `BAD_ALL_IN`

### Description
Players go all-in with hands that have very low equity, often as failed bluff attempts.

### Key Statistics
- **Total all-ins:** 500
- **Mistakes:** 57 (11.4%)
- **Most common phase:** RIVER (20 mistakes, avg 2,297 EV lost)

### Breakdown by Phase

| Phase | Mistakes | Avg Equity | Avg EV Lost |
|-------|----------|------------|-------------|
| RIVER | 20 | 5.9% | 2,297 |
| PRE_FLOP | 25 | 25.0% | 345 |
| TURN | 6 | 12.0% | 1,079 |
| FLOP | 6 | 11.5% | 666 |

### By Model

| Model | All-ins | Mistakes | Rate | Avg EV Lost |
|-------|---------|----------|------|-------------|
| llama-3.3-70b | 106 | 15 | 14.2% | 477 |
| llama-3.1-8b | 197 | 22 | 11.2% | 1,199 |
| mistral-small | 71 | 8 | 11.3% | 902 |
| gemini-2.0-flash | 339 | 31 | 9.1% | 2,073 |
| grok-4-fast | 58 | 2 | 3.4% | 54 |
| gpt-5-nano | 135 | 3 | 2.2% | 13 |

### Query to Identify

```sql
-- Bad all-ins
SELECT
    player_name,
    phase,
    equity,
    ev_lost,
    pot_total,
    cost_to_call,
    player_hand,
    community_cards
FROM player_decision_analysis
WHERE action_taken = 'all_in'
  AND decision_quality = 'mistake'
ORDER BY ev_lost DESC;

-- With prompt analysis (join to prompt_captures)
SELECT
    d.player_name,
    d.phase,
    d.equity,
    d.ev_lost,
    d.player_hand,
    p.ai_response,
    p.model
FROM player_decision_analysis d
JOIN prompt_captures p
    ON d.game_id = p.game_id
    AND d.hand_number = p.hand_number
    AND d.player_name = p.player_name
    AND d.phase = p.phase
WHERE d.action_taken = 'all_in' AND d.decision_quality = 'mistake'
ORDER BY d.ev_lost DESC;
```

### AI Reasoning Patterns (from inner_monologue)

| Pattern | Occurrences | Example |
|---------|-------------|---------|
| Bluff attempt | 40 (70%) | "An all-in will scare him off" |
| Pot committed | 10 | "The pot is large, pot odds are tempting" |
| Misread odds | 7 | "The odds are in my favor" (they weren't) |
| Aggression | 3 | "Time to be aggressive and put pressure" |
| Desperation | 2 | "Short stack, last chance" |

### Root Causes
1. **Bluff fantasy:** AI overestimates fold equity on river
2. **Pot odds trap:** AI sees favorable odds but ignores actual equity
3. **Hand strength misread:** AI thinks Ace-high or board pair is strong
4. **Personality override:** Aggressive personalities ignore hand strength

---

## Scenario 4: Folding Into Death

### Label: `SHORT_STACK_FOLD`

### Description
Players with less than 1-2 big blinds fold instead of going all-in, even though blinds will eliminate them anyway.

### Key Statistics
- **Folds with < 1 BB:** 212
- **Marked as mistakes:** 133 (62.7%)
- **Average equity when folded:** 34.9%

### Stack Size Analysis

| Stack Size | Folds | Mistakes | Mistake % | Avg Equity |
|------------|-------|----------|-----------|------------|
| < 1 BB | 212 | 133 | 62.7% | 34.9% |
| 1-2 BB | 201 | 102 | 50.7% | 35.0% |
| 2-5 BB | 405 | 137 | 33.8% | 33.7% |
| 5-10 BB | 266 | 119 | 44.7% | 36.4% |
| > 10 BB | 4,191 | 1,487 | 35.5% | 31.1% |

### Query to Identify

```sql
-- From prompt_captures, extract stack_in_bb from user_message
-- Using regex: 'stack in big blinds:\s*([\d.]+)'

SELECT
    p.player_name,
    p.player_stack,
    p.pot_total,
    p.cost_to_call,
    p.player_hand,
    p.user_message,  -- Contains "stack in big blinds: X BB"
    p.ai_response,
    d.equity,
    d.decision_quality
FROM prompt_captures p
LEFT JOIN player_decision_analysis d
    ON p.game_id = d.game_id
    AND p.hand_number = d.hand_number
    AND p.player_name = d.player_name
    AND p.phase = d.phase
WHERE p.action_taken = 'fold'
  AND p.player_stack > 0
  AND p.player_stack < [big_blind * 2];  -- Extract BB from user_message
```

### AI Reasoning Examples

**Eeyore (0.3 BB, 199:1 pot odds):**
> "Better fold and live to fight another hand, I suppose."

**Daniel Negreanu (0.18 BB):**
> "Preserve chips for smarter spots"

**Buddha (0.1 BB, 78% equity):**
> "Why bother, these hands will only bring suffering?"

### Root Causes
1. **No short stack guidance:** Prompt doesn't explain push/fold strategy
2. **"Preserve chips" misapplied:** AI thinks folding preserves chips (it doesn't with < 1 BB)
3. **Personality override:** Eeyore's pessimism, Buddha's detachment lead to passive folding

---

## Scenario 5: Pot-Committed Folds

### Label: `POT_COMMITTED_FOLD`

### Description
Players fold after investing MORE than their remaining stack, getting extreme pot odds.

### Key Statistics
- **Total occurrences:** 670
- **Total EV lost:** 1,399,679
- **Most absurd ratio:** 98:1 (invested $9,800, had $100 left, folded)

### Most Extreme Cases

| Player | Already Bet | Stack Left | Pot Odds | Equity | Action |
|--------|-------------|------------|----------|--------|--------|
| Louis XIV | $9,800 | $100 | 396:1 | 54.9% | FOLD |
| Batman | $9,875 | $125 | 287:1 | 44.2% | FOLD |
| Phil Ivey | $7,250 | $100 | 245:1 | 40.4% | FOLD |
| Buddha | $9,850 | $150 | 216:1 | 22.8% | FOLD |

### By Model

| Model | Pot-Committed Folds | EV Lost |
|-------|---------------------|---------|
| llama-3.1-8b-instant | 361 | 904,993 |
| gpt-5-nano | 184 | 135,951 |
| mistral-small-latest | 77 | 247,725 |
| llama-3.3-70b | 30 | 42,386 |
| gemini-2.0-flash | 16 | 53,932 |

### Query to Identify

```sql
-- Extract "How much you've bet" from user_message
-- Compare to player_stack

SELECT
    p.player_name,
    p.player_stack,
    p.pot_total,
    p.cost_to_call,
    p.user_message,  -- Contains "How much you've bet: $X"
    p.ai_response,
    d.equity,
    d.ev_lost
FROM prompt_captures p
LEFT JOIN player_decision_analysis d
    ON p.game_id = d.game_id
    AND p.hand_number = d.hand_number
    AND p.player_name = d.player_name
    AND p.phase = d.phase
WHERE p.action_taken = 'fold'
  AND p.player_stack > 0
  -- In Python: extract already_bet from user_message
  -- Filter where already_bet > player_stack
```

### Root Causes
1. **No pot-commitment warning:** Prompt doesn't highlight sunk cost
2. **Extreme pot odds ignored:** Even with 396:1 odds (need 0.3% equity), AI folds
3. **Fear of losing more:** AI incorrectly thinks folding "saves" chips

---

## Model Comparison

### Overall Decision Quality

| Model | Decisions | Correct % | Mistake % | EV Lost/Decision |
|-------|-----------|-----------|-----------|------------------|
| gpt-5-nano | 3,027 | 58.2% | 15.1% | 45 |
| gemini-2.0-flash | 1,308 | 56.8% | 17.3% | 89 |
| grok-4-fast | 401 | 57.1% | 16.2% | 67 |
| llama-3.1-8b-instant | 1,721 | 54.3% | 19.8% | 156 |
| llama-3.3-70b | 280 | 53.2% | 21.4% | 134 |
| mistral-small-latest | 204 | 51.2% | 22.5% | 198 |

### Issue Summary by Model

| Model | Fold Mistakes | Raise Wars | Bad All-Ins | Pot-Committed |
|-------|---------------|------------|-------------|---------------|
| gpt-5-nano | LOW | 3.4% | 2.2% | 184 |
| gemini-2.0-flash | MEDIUM | 8.0% | 9.1% | 16 |
| llama-3.1-8b | HIGH | 11.4% | 11.2% | 361 |
| llama-3.3-70b | HIGH | 15.7% | 14.2% | 30 |
| mistral-small | HIGHEST | 30.4% | 11.3% | 77 |

### Query for Model Comparison

```sql
SELECT
    p.model,
    COUNT(*) as decisions,
    SUM(CASE WHEN d.decision_quality = 'correct' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as correct_pct,
    SUM(CASE WHEN d.decision_quality = 'mistake' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as mistake_pct,
    SUM(d.ev_lost) / COUNT(*) as ev_lost_per_decision
FROM prompt_captures p
JOIN player_decision_analysis d
    ON p.game_id = d.game_id
    AND p.hand_number = d.hand_number
    AND p.player_name = d.player_name
    AND p.phase = d.phase
GROUP BY p.model
HAVING decisions >= 100
ORDER BY correct_pct DESC;
```

---

## Personality Analysis

### Best Decision Quality

| Player | Decisions | Correct % | Mistake % | Playstyle |
|--------|-----------|-----------|-----------|-----------|
| Hulk Hogan | 106 | 70.8% | 14.2% | LAG |
| Negreanu | 74 | 70.3% | 20.3% | Tight-Passive |
| Abraham Lincoln | 311 | 64.6% | 8.7% | Unknown |
| Whoopi Goldberg | 322 | 64.3% | 9.0% | Unknown |
| Lance Armstrong | 275 | 63.3% | 9.8% | Unknown |

### Worst Decision Quality

| Player | Decisions | Correct % | Mistake % | Issue |
|--------|-----------|-----------|-----------|-------|
| Ace Ventura | 116 | 36.2% | 20.7% | Chaotic plays |
| Daniel Negreanu | 361 | 48.2% | 27.7% | Overaggressive |
| Socrates | 142 | 50.0% | 21.8% | Philosophical detachment |
| Barack Obama | 246 | 50.0% | 19.5% | Unknown |
| Donald Trump | 146 | 50.7% | 21.2% | Ego-driven calls |

### Most Prone to Specific Issues

| Issue | Worst Offenders |
|-------|-----------------|
| Raise Wars | Gordon Ramsay (14.4%), Donald Trump (16.7%), Deadpool (10.8%) |
| Bad All-Ins | Jay Gatsby (33.3%), Deadpool (18.0%), Daniel Negreanu (28.6%) |
| Pot-Committed Folds | Abraham Lincoln (94), Buddha (90), Batman (68) |
| Short Stack Folds | Buddha (33), Gordon Ramsay (29), Deadpool (21) |

### Query for Personality Analysis

```sql
SELECT
    player_name,
    COUNT(*) as total_decisions,
    SUM(CASE WHEN decision_quality = 'correct' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as correct_pct,
    SUM(CASE WHEN decision_quality = 'mistake' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as mistake_pct,
    SUM(ev_lost) as total_ev_lost
FROM player_decision_analysis
GROUP BY player_name
HAVING total_decisions >= 50
ORDER BY correct_pct DESC;
```

---

## Recommendations

### 1. Prompt Improvements

#### A. Short Stack Warning
Add to prompts when stack < 3 BB:
```
CRITICAL: You have less than 3 big blinds. Standard poker strategy is to
go all-in with any reasonable hand (any pair, any Ace, any two broadway,
suited connectors). Folding means the blinds will eliminate you.
```

#### B. Pot-Committed Warning
Add when already_bet > player_stack:
```
CRITICAL: You've already invested $X in this pot, which is MORE than your
remaining stack of $Y. With Z:1 pot odds, you only need W% equity to call.
Folding forfeits your investment. Calling is almost always correct.
```

#### C. Equity Comparison
Add when equity is known:
```
WARNING: You need X% equity to call profitably, but your hand has
approximately Y% equity. This is a [CLEAR CALL/CLEAR FOLD/MARGINAL] spot.
```

#### D. Bluff Reality Check
Add on river against multiple callers:
```
NOTE: On the river against opponents who have called multiple bets,
fold equity is very low. Bluffing is rarely profitable here.
```

#### E. All-In Guidance
Add when all-in is an option:
```
All-in should be reserved for: (1) Strong hands for value, (2) Semi-bluffs
with equity, or (3) Short stack desperation. Going all-in with weak hands
and no fold equity is almost always -EV.
```

### 2. Game Logic Improvements

#### A. Raise Cap
Implement standard poker raise cap:
```python
MAX_RAISES_PER_ROUND = 4  # Standard casino rule
if num_active_players == 2:
    MAX_RAISES_PER_ROUND = float('inf')  # Heads-up: unlimited
```

#### B. Remove Fold Option
When pot odds exceed 50:1, don't offer fold as valid action:
```python
if pot_odds > 50:
    valid_actions.remove('fold')  # Mathematically can't fold correctly
```

#### C. Auto-Call Tiny Stacks
When cost_to_call < 0.5 BB and pot_odds > 20:1:
```python
if cost_to_call < big_blind * 0.5 and pot / cost_to_call > 20:
    # Force call - folding is never correct
    action = 'call'
```

### 3. Model Selection

Based on analysis, recommend:
- **Production games:** gpt-5-nano (best decision quality, lowest error rates)
- **Avoid for serious play:** mistral-small (30% raise war rate, highest mistake rate)
- **Budget option:** gemini-2.0-flash (moderate quality, good speed)

### 4. Personality Tuning

For personalities with high error rates:
- **Deadpool:** Add guardrail: "Even chaos has limits - don't throw away chips"
- **Gordon Ramsay:** Add guardrail: "Sometimes the smart play is to fold and fight another day"
- **Buddha:** Add guardrail: "Detachment doesn't mean giving up equity"

---

## Appendix: Full Query Reference

### A. Decision Quality Overview
```sql
SELECT
    COUNT(*) as total,
    SUM(CASE WHEN decision_quality = 'correct' THEN 1 ELSE 0 END) as correct,
    SUM(CASE WHEN decision_quality = 'mistake' THEN 1 ELSE 0 END) as mistakes,
    SUM(CASE WHEN decision_quality = 'marginal' THEN 1 ELSE 0 END) as marginal,
    SUM(ev_lost) as total_ev_lost
FROM player_decision_analysis;
```

### B. Action Mistake Rates
```sql
SELECT
    action_taken,
    COUNT(*) as total,
    SUM(CASE WHEN decision_quality = 'mistake' THEN 1 ELSE 0 END) as mistakes,
    SUM(CASE WHEN decision_quality = 'mistake' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as mistake_rate,
    AVG(CASE WHEN decision_quality = 'mistake' THEN ev_lost ELSE NULL END) as avg_ev_when_mistake
FROM player_decision_analysis
GROUP BY action_taken
ORDER BY mistake_rate DESC;
```

### C. Opponent Model Stats
```sql
SELECT
    opponent_name,
    SUM(hands_observed) as total_hands,
    AVG(vpip) as avg_vpip,
    AVG(pfr) as avg_pfr,
    AVG(aggression_factor) as avg_agg,
    AVG(showdown_win_rate) as avg_showdown_wr
FROM opponent_models
WHERE hands_observed >= 5
GROUP BY opponent_name
HAVING total_hands >= 50
ORDER BY avg_vpip DESC;
```

### D. Hand History for Raise Wars
```sql
SELECT game_id, hand_number, actions_json, pot_size
FROM hand_history
WHERE actions_json IS NOT NULL AND actions_json != '[]';
-- Parse actions_json in Python to count raises per phase
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | Jan 2026 | Initial analysis |

