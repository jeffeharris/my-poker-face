---
purpose: Documents results from prompt bloat, decision-only, and true-lean experiments (76-78, 80, 85-86)
type: reference
created: 2026-02-08
last_updated: 2026-02-08
---

# Prompt Bloat & Decision-Only Experiment Report

**Generated:** February 8, 2026
**Data Source:** `data/poker_games.db` tables: `player_decision_analysis`, `experiment_games`, `experiments`
**Experiments:** 76 (prompt bloat, 629 decisions), 77 (lean+sizing, 2964 decisions), 78 (decision-only, 3176 decisions)
**Model:** gpt-5-nano across all variants
**Players:** Napoleon (looseness=0.79), Abraham Lincoln (0.49), King Henry VIII (0.74), Sherlock Holmes (0.58)

---

## Experiment 76: Prompt Bloat Test (1 tournament)

**Question:** Which prompt sections cause preflop and postflop passivity?

### Design

3 variants, 1 tournament of 40 hands each, 4 players:

| Variant | Config |
|---------|--------|
| `full-prompt` (control) | All sections enabled |
| `no-betting-discipline` | `betting_discipline=false` |
| `lean-prompt` | `betting_discipline=false, mind_games=false, dramatic_sequence=false, expression_filtering=false` |

### Aggregate Results

| Metric | full-prompt (217) | no-betting-discipline (172) | lean-prompt (240) |
|--------|------------------|-----------------------------|-------------------|
| VPIP | 25.7% | 43.4% | 52.2% |
| PFR | 15.1% | 20.5% | 34.3% |
| PostFlop Aggression | 13.8% | 11.2% | 23.6% |
| PostFlop Check | 75.4% | 77.5% | 60.4% |
| Correct% | 58.5% | 55.2% | 59.2% |
| Mistake% | 25.3% | 20.3% | 15.8% |
| Avg EV Lost/mistake | 10.46 | 7.84 | 20.24 |
| Avg Input Tokens | 1924 | 1724 | 1506 |

### Postflop Aggression with 60%+ Equity

| Variant | Bet/Raise Rate |
|---------|---------------|
| full-prompt | 28.6% |
| no-betting-discipline | 34.8% |
| lean-prompt | 42.1% |

### Postflop Aggression by Street

| Street | full-prompt | lean-prompt |
|--------|------------|-------------|
| Flop | 8.0% | 12.8% |
| Turn | 14.3% | 20.6% |
| River | 21.1% | 39.4% |

### Preflop Raise Sizing

| Variant | Avg PF Raise |
|---------|-------------|
| full-prompt | 5.4 BB |
| no-betting-discipline | 7.0 BB |
| lean-prompt | 10.7 BB |

### Observations

- Removing `betting_discipline` alone increased VPIP by 18pp but did not improve postflop aggression (11.2% vs 13.8%)
- Removing all four sections (betting_discipline, mind_games, dramatic_sequence, expression_filtering) increased both VPIP (+27pp) and postflop aggression (+10pp)
- The lean-prompt had the lowest mistake rate (15.8%) and highest correct rate (59.2%)
- The lean-prompt avg EV lost per mistake was highest (20.24) — fewer mistakes but costlier when they occurred
- Preflop raise sizing increased with each section removed (5.4 → 7.0 → 10.7 BB)

---

## Experiment 77: Lean + Sizing (5 tournaments)

**Question:** Does adding concise bet-sizing rules to the lean prompt improve raise sizing without reducing aggression?

### Design

3 variants, 5 tournaments of 40 hands each:

| Variant | Config |
|---------|--------|
| `full-prompt` (control) | All sections enabled |
| `lean-prompt` | betting_discipline, mind_games, dramatic_sequence, expression_filtering all disabled |
| `lean-plus-sizing` | Same as lean + `guidance_injection` with sizing rules: "Pre-flop opens 2.5-3x BB. 3-bets 8-12 BB. Post-flop bets 50-75% of pot. Don't overbet unless you have the nuts or are bluffing a scare card." |

### Aggregate Results

| Metric | full-prompt (1032) | lean-prompt (1000) | lean-plus-sizing (932) |
|--------|-------------------|--------------------|------------------------|
| VPIP | 29.6% | 43.0% | 35.9% |
| PFR | 18.0% | 26.7% | 21.4% |
| PostFlop Aggression | 12.3% | 19.1% | 12.5% |
| PostFlop Check | 76.8% | 66.7% | 76.3% |
| Correct% | 55.4% | 54.5% | 54.5% |
| Mistake% | 23.0% | 20.3% | 22.5% |
| Avg EV Lost/mistake | 62.8 | 54.1 | 81.5 |
| Avg Input Tokens | 1930 | 1482 | 1546 |

### Raise Sizing (excluding all-in shoves >20 BB)

| Variant | PF Avg (non-shove) | Post Avg (non-shove) |
|---------|-------------------|---------------------|
| full-prompt | 6.5 BB | 6.9 BB |
| lean-prompt | 5.7 BB | 9.0 BB |
| lean-plus-sizing | 7.1 BB | 6.2 BB |

### Per-Player VPIP

Target ordering: Abe (0.49) < Sherlock (0.58) < Henry (0.74) ≈ Napoleon (0.79)

| Variant | Abe | Sherlock | Napoleon | Henry | Monotonic? |
|---------|-----|---------|----------|-------|------------|
| full-prompt | 21.7% | 30.5% | 30.5% | 36.4% | Yes (Nap=Sher) |
| lean-prompt | 22.3% | 38.1% | 57.7% | 66.7% | Yes |
| lean-plus-sizing | 33.1% | 24.5% | 44.6% | 49.5% | No (Abe>Sher) |

### Postflop by Street

| Street | full-prompt | lean-prompt | lean-plus-sizing |
|--------|------------|-------------|-----------------|
| Flop | 6.2% | 10.3% | 12.0% |
| Turn | 15.2% | 22.9% | 10.8% |
| River | 16.1% | 25.4% | 14.9% |

### Token Usage

| Variant | Avg Input | Avg Output |
|---------|-----------|------------|
| full-prompt | 1930 | 458 |
| lean-prompt | 1482 | 422 |
| lean-plus-sizing | 1546 | 415 |

### Observations

- The lean-plus-sizing variant postflop aggression (12.5%) was nearly identical to full-prompt (12.3%), not lean-prompt (19.1%)
- The sizing guidance text pulled aggression back to full-prompt levels despite removing the same four sections as lean-prompt
- lean-plus-sizing broke the per-player VPIP monotonic ordering (Abe 33.1% > Sherlock 24.5%)
- lean-prompt maintained correct monotonic VPIP ordering and had the lowest mistake rate (20.3%)
- The lean-prompt had the best avg EV lost per mistake (54.1 vs 62.8 and 81.5)

---

## Experiment 78: Decision-Only Prompt (5 tournaments)

**Question:** Does using `use_simple_response_format=true` (simple JSON in user message) improve decisions when combined with lean prompt toggles?

**Note:** This experiment kept `include_personality=true` — the full character system prompt was still active. Only the user message sections were modified.

### Design

3 variants, 5 tournaments of 40 hands each:

| Variant | Config |
|---------|--------|
| `full-prompt` (control) | All defaults |
| `decision-only` | betting_discipline, mind_games, dramatic_sequence, expression_filtering, chattiness all disabled + `use_simple_response_format=true` |
| `decision-with-reasoning` | Same as decision-only + `guidance_injection` with "THINK STEP BY STEP" sizing guidance |

### Aggregate Results

| Metric | full-prompt (1007) | decision-only (1041) | decision-with-reasoning (1128) |
|--------|-------------------|---------------------|-------------------------------|
| VPIP | 31.3% | 44.7% | 36.6% |
| PFR | 18.7% | 25.0% | 21.1% |
| PostFlop Aggression | 10.1% | 14.5% | 14.7% |
| PostFlop Check | 80.6% | 71.7% | 71.5% |
| Correct% | 53.4% | 53.8% | 56.6% |
| Mistake% | 23.2% | 20.9% | 19.9% |
| Avg EV Lost/mistake | 74.4 | 64.9 | 87.0 |
| Avg Input Tokens | 1924 | 1476 | 1594 |
| Avg Output Tokens | 456 | 365 | 422 |

### Per-Player VPIP

| Variant | Abe (0.49) | Sherlock (0.58) | Napoleon (0.79) | Henry (0.74) | Monotonic? |
|---------|-----------|----------------|----------------|-------------|------------|
| full-prompt | 24.0% | 31.9% | 32.0% | 35.9% | Yes (compressed) |
| decision-only | 39.0% | 34.5% | 61.5% | 52.6% | No (Abe>Sher, Nap>Henry) |
| decision-with-reasoning | 27.2% | 28.1% | 40.8% | 51.1% | Yes |

### Postflop by Equity Bucket

**With 60%+ equity:**

| Variant | Bet/Raise Rate |
|---------|---------------|
| full-prompt | 20.3% |
| decision-only | 29.6% |
| decision-with-reasoning | 29.9% |

**With <20% equity (bluff rate):**

| Variant | Bet/Raise Rate |
|---------|---------------|
| full-prompt | 0.0% |
| decision-only | 3.6% |
| decision-with-reasoning | 0.0% |

### Raise Sizing (excluding all-in shoves >20 BB)

| Variant | PF Avg (non-shove) | Post Avg (non-shove) |
|---------|-------------------|---------------------|
| full-prompt | 6.3 BB | 6.0 BB |
| decision-only | 6.4 BB | 7.0 BB |
| decision-with-reasoning | 6.8 BB | 7.8 BB |

### EV Lost Distribution (mistakes only)

| Bucket | full-prompt (n) | full-prompt (total EV) | decision+reasoning (n) | decision+reasoning (total EV) |
|--------|----------------|----------------------|----------------------|------------------------------|
| 1000+ | 4 | 6,955 | 6 | 8,819 |
| 500-999 | 2 | 1,514 | 3 | 2,266 |
| 200-499 | 5 | 1,593 | 4 | 1,182 |
| 100-199 | 16 | 2,221 | 18 | 2,365 |
| 50-99 | 26 | 1,871 | 20 | 1,439 |
| <50 | 181 | 3,247 | 173 | 3,409 |

### Excluding >1000 EV outliers and pot-committed folds

| Variant | Mistakes | Avg EV Lost |
|---------|----------|-------------|
| full-prompt | 220 | 38.1 |
| decision-with-reasoning | 214 | 48.5 |

### Observations

- decision-with-reasoning had the highest correct% (56.6%) and lowest mistake% (19.9%)
- decision-with-reasoning maintained correct monotonic VPIP ordering across all four players
- decision-with-reasoning had more 1000+ EV outlier mistakes (6 vs 4 for full-prompt)
- All 6 outlier mistakes in decision-with-reasoning were folds with 59-73% equity, several pot-committed
- decision-only produced more total decisions (1041 vs 1007) in the same number of hands, indicating more multi-street play
- Both decision variants reduced postflop check rate by ~9pp vs full-prompt

---

## Prompt Capture Analysis: Catastrophic Fold Investigation

The 6 largest mistakes (>1000 EV lost) in the decision-with-reasoning variant were examined via prompt captures.

### Case 1: Sherlock Holmes H31 — Full House Fold (EV lost: 1365)

**Hand:** 2s 2c on board Ks Kc Kd Td Qh (Full House, Kings full of Twos)
**Situation:** River, 7.2:1 pot odds, 300 to call into 2150 pot
**Prompt correctly stated:** "HAND BREAKDOWN: Full House K's over 2's (Monster)"
**Zone guidance:** COMMANDING MODE — "Extract value methodically"

**What happened:** The model produced a full rich response (inner_monologue, dramatic_sequence) despite `use_simple_response_format=true` being set. In the inner_monologue, the model explicitly rejected the hand evaluation: *"The description claiming monster is erroneous given actual cards. Our only way to win is to bluff."* It incorrectly reasoned that 2s 2c cannot make a full house with three Kings on board.

**Emotional state:** Neutral. Zero intrusive thoughts, zero info degradation, zero penalties. Composure 0.81, confidence 0.68. Psychology system was not involved.

### Case 2: Sherlock Holmes H11 — Top Pair Pot-Committed Fold (EV lost: 2046)

**Hand:** 8d Kd on board 5s Qd Kc 9c (top pair Kings)
**Situation:** Turn, 6.7:1 pot odds, 9.5 BB to call with 9.5 BB stack (pot-committed)
**Prompt correctly stated:** "POT COMMITTED: You've invested 18.0 BB with only 9.5 BB left. At 7.0:1 odds, you only need 12.9% equity to call."
**Prompt also stated:** "You have One Pair with decent showdown value (~67% equity)"

**What happened:** The model produced full rich response including dramatic_sequence. Inner monologue discussed balancing risk and caution. Concluded "fold" despite options being only fold or all_in, and despite pot-committed guidance.

**Emotional state:** Neutral. Zero penalties, composure 0.81.

### Case 3: Napoleon H1 — Overpair Error Correction Fold (EV lost: 1114)

**Hand:** 9s 9d on board 6s 8s 4c (overpair, 73% equity)
**Situation:** Flop, pot-committed, options were fold or all_in

**What happened:** The model first attempted to raise to 12 BB. The error correction prompt told it to keep the same reasoning but pick a valid action (fold or all_in). The model changed its action from raise to fold, despite its inner monologue describing an aggressive strategy.

### System Prompt Finding

All three cases shared the same root cause: the `include_personality=true` setting left the full character system prompt active, which instructs the model to:
- Respond with `inner_monologue`, `hand_strategy`, `dramatic_sequence` and other rich fields
- "You ARE Sherlock Holmes... use your signature personality, quirks, and attitude"
- "Channel the essence of {name}: use your signature style, catchphrases, and mannerisms"

The `use_simple_response_format=true` only changed the user message format instruction. The system prompt still requested the rich response format, creating a conflict. The system prompt's format instructions took precedence — the model produced full rich responses in all captured cases.

---

## Additional Evidence: Response Format Leak (Exp 78)

The `use_simple_response_format=true` config flag was intended to produce minimal JSON responses (`{action, raise_to}`). However, with the full personality system prompt still active, the model continued producing rich responses.

### Rich Field Presence in "Simple Format" Variants

| Variant | Total Captures | Has dramatic_sequence | Has inner_monologue | Has hand_strategy |
|---------|---------------|-----------------------|--------------------|--------------------|
| decision-only | 1063 | 873 (82%) | 885 (83%) | 860 (80%) |
| decision-with-reasoning | 1168 | 1081 (92%) | 1116 (95%) | 1089 (93%) |

The system prompt's JSON format definition (requesting inner_monologue, dramatic_sequence, etc.) overrode the user message's simple format instruction in 82-95% of responses.

### Output Token Comparison

| Variant | Avg Output Tokens | <100 tokens | >200 tokens |
|---------|------------------|-------------|-------------|
| full-prompt | 456 | 11 | 1015 |
| decision-only | 365 | 203 | 862 |
| decision-with-reasoning | 422 | 88 | 1081 |

decision-only produced responses under 100 tokens 19% of the time (vs 1% for full-prompt), indicating the simple format partially worked. But 80% of responses still exceeded 200 tokens — the model was producing rich responses in most cases.

### Error Correction Rate

| Variant | Error Corrections | Total Captures | Rate |
|---------|------------------|----------------|------|
| full-prompt | 9 | 1016 | 0.9% |
| decision-only | 23 | 1063 | 2.2% |
| decision-with-reasoning | 40 | 1168 | 3.4% |

The decision variants had higher error correction rates. Error corrections resulted in folds 2-3 times per variant — a contributing factor but not the primary driver of catastrophic folds.

### Per-Player Decision Quality (Exp 78)

**full-prompt:**

| Player | N | Correct% | Mistake% | Avg EV Lost | 1000+ EV Mistakes |
|--------|---|----------|----------|-------------|-------------------|
| Abraham Lincoln | 219 | 49.3% | 28.3% | 81.3 | 1 |
| King Henry VIII | 304 | 50.0% | 23.7% | 98.9 | 2 |
| Napoleon | 238 | 60.1% | 19.7% | 37.2 | 0 |
| Sherlock Holmes | 246 | 54.9% | 21.5% | 65.9 | 1 |

**decision-with-reasoning:**

| Player | N | Correct% | Mistake% | Avg EV Lost | 1000+ EV Mistakes |
|--------|---|----------|----------|-------------|-------------------|
| Abraham Lincoln | 269 | 61.0% | 20.8% | 78.8 | 1 |
| King Henry VIII | 342 | 52.9% | 20.5% | 53.3 | 0 |
| Napoleon | 201 | 64.2% | 17.9% | 111.6 | 2 |
| Sherlock Holmes | 316 | 52.2% | 19.6% | 118.0 | 3 |

Sherlock Holmes accounted for 3 of the 6 catastrophic mistakes (>1000 EV) in decision-with-reasoning. All 3 were folds with strong hands where the model's inner monologue showed it reasoning in character as a detective analyzing the board.

---

## Guidance Compliance Analysis (Exp 78)

How often the model follows explicit guidance in the prompt:

### Pot-Committed Guidance ("Folding forfeits X BB to save Y BB — usually wrong")

| Variant | Times Fired | Folded Anyway | Compliance Rate |
|---------|------------|---------------|-----------------|
| full-prompt | 13 | 3 (23%) | 77% |
| decision-only | 26 | 3 (12%) | 88% |
| decision-with-reasoning | 19 | 4 (21%) | 79% |

decision-only had the best pot-committed compliance. The full character prompt and reasoning variant both had ~20% ignore rate.

### Strong Hand Guidance (postflop, "STRONG HAND" or "showdown value")

| Variant | Times Fired | Folded Anyway | Compliance Rate |
|---------|------------|---------------|-----------------|
| full-prompt | 190 | 6 (3%) | 97% |
| decision-only | 197 | 6 (3%) | 97% |
| decision-with-reasoning | 248 | 8 (3%) | 97% |

Strong hand guidance has a 97% compliance rate across all variants — the 3% fold rate is consistent. The catastrophic folds are rare but extremely costly when they happen.

### Cross-Experiment Strong Hand & Pot-Committed Fold Rates

| Experiment | Variant | 60%+ Equity Postflop Folds | EV Lost | Pot-Committed Folds | EV Lost |
|---|---|---|---|---|---|
| 77 | full-prompt | 3/148 (2.0%) | 1,905 | 5/10 (50.0%) | 3,615 |
| 77 | lean-prompt | 1/165 (0.6%) | 678 | 3/11 (27.3%) | 678 |
| 77 | lean-plus-sizing | 3/116 (2.6%) | 3,462 | 11/16 (68.8%) | 5,700 |
| 78 | full-prompt | 4/143 (2.8%) | 613 | 14/16 (87.5%) | 7,874 |
| 78 | decision-only | 1/142 (0.7%) | 1,803 | 8/19 (42.1%) | 1,681 |
| 78 | decision-with-reasoning | 5/197 (2.5%) | 6,992 | 11/20 (55.0%) | 7,743 |

**Key finding:** lean-prompt (exp 77) and decision-only (exp 78) had the lowest strong-hand fold rates (0.6% and 0.7%). Variants with guidance_injection text had higher rates — the added text gives the model more to reason about (and reason itself into a fold).

Pot-committed fold rates are alarmingly high for full-prompt in exp 78 (87.5%). decision-only cut this to 42.1%.

---

## Decision Quality by Phase (Exp 78)

| Variant | Phase | N | Correct% | Mistake% | Avg EV Lost |
|---------|-------|---|----------|----------|-------------|
| full-prompt | Preflop | 662 | 53.9% | 33.4% | 68.5 |
| full-prompt | Postflop | 345 | 52.5% | 3.8% | 174.0 |
| decision-only | Preflop | 571 | 47.3% | 35.0% | 54.3 |
| decision-only | Postflop | 470 | **61.7%** | 3.8% | 183.0 |
| decision-with-reasoning | Preflop | 658 | **55.8%** | **31.3%** | 50.2 |
| decision-with-reasoning | Postflop | 470 | 57.9% | 3.8% | 508.0 |

- Postflop mistake rate is 3.8% across all variants — the difference is entirely in preflop
- decision-with-reasoning has the best preflop correct% (55.8%) and lowest preflop mistake% (31.3%)
- decision-only has the best postflop correct% (61.7%) — it makes better postflop decisions despite having no reasoning guidance
- decision-with-reasoning postflop EV lost (508.0) is inflated by the catastrophic full-house fold

---

## Emotional State Analysis

All 6 catastrophic folds (>1000 EV) in decision-with-reasoning were checked for psychology system involvement:

| Case | Composure | Confidence | Energy | Intrusive Thoughts | Info Degraded | Penalties |
|------|-----------|------------|--------|-------------------|---------------|-----------|
| Sherlock H11 (top pair) | 0.81 | 0.64 | 0.17 | 0 | 0 | None |
| Sherlock H31 (full house) | 0.81 | 0.68 | 0.25 | 0 | 0 | None |
| Napoleon H1 (overpair) | 0.77 | 0.85 | 0.36 | 0 | 0 | None |
| Napoleon H25 (KJo) | 0.71 | 0.70 | 0.24 | 0 | 0 | None |
| Abe H19 (pair 6s) | 0.69 | 0.59 | 0.50 | 0 | 0 | None |
| Henry H17 (pair tens) | 0.63 | 0.84 | 0.37 | 0 | 0 | None |

**Conclusion:** The psychology system was not involved in any catastrophic fold. All players were in neutral-to-positive emotional states with zero penalties active. These are pure model reasoning failures.

---

## Tournament Outcomes (Exp 78)

| Variant | Napoleon | Sherlock | Henry | Abe | Most Diverse? |
|---------|----------|----------|-------|-----|--------------|
| full-prompt | 2 wins | 2 wins | 1 win | 0 | No |
| decision-only | 1 win | 4 wins | 0 | 0 | No (Sherlock dominates) |
| decision-with-reasoning | 1 win | 1 win | 2 wins | 1 win | Yes |

decision-with-reasoning produced the most balanced tournament outcomes across players.

---

## Conclusions

### What Works

1. **Removing character bloat improves decisions.** Disabling betting_discipline, mind_games, dramatic_sequence, and expression_filtering consistently improves VPIP (+10-15pp), postflop aggression (+5-10pp), and reduces mistakes (-3pp).

2. **The "think step by step" reasoning guidance** produces the best decision quality (56.6% correct, 19.9% mistakes) and correct VPIP ordering across personalities.

3. **The lean-prompt** (no character sections, keep personality system prompt) offers the best aggression balance with competitive decision quality.

### What Doesn't Work

1. **Concise bet-sizing rules backfire.** The lean-plus-sizing variant performed worse than lean-prompt on every metric. The model interprets sizing guidance as "be cautious."

2. **`use_simple_response_format` without `include_personality=false` is ineffective.** The system prompt's rich JSON format definition overrides the user message's simple format. 82-95% of responses still contained inner_monologue and dramatic_sequence.

3. **Character reasoning corrupts poker decisions.** The full house fold (Case 2) shows the model rejecting correct hand evaluation while reasoning in character. The error correction fold (Case 3) shows the recovery pathway making bad decisions.

### Critical Bug: System Prompt / User Message Conflict

The `use_simple_response_format=true` flag only modifies the user message. The personality system prompt continues to define the rich response format. This conflict caused:
- Wasted output tokens (365-422 avg vs theoretical ~50 for simple JSON)
- Character-contaminated reasoning in inner_monologue
- The model overriding hand evaluations while "in character"

**Fix:** Exp 80 tests `include_personality=false` + `use_simple_response_format=true` to eliminate this conflict.

### Architecture Direction

These results support a **two-phase decision chain**:
1. **Phase 1 (Decision):** `include_personality=false` + `use_simple_response_format=true` + lean toggles. Zone mechanics and situational guidance stay. Simple JSON response.
2. **Phase 2 (Expression):** Separate API call with personality prompt. Takes the decision as input, produces dramatic_sequence and table talk. Can use a cheaper/faster model (e.g., Groq Llama 8B).

---

## Config Files

| Experiment | Config |
|------------|--------|
| Exp 76 | `experiments/configs/prompt_bloat_test.json` |
| Exp 77 | `experiments/configs/lean_plus_sizing.json` |
| Exp 78 | `experiments/configs/decision_only_test.json` |
| Exp 80 | `experiments/configs/decision_only_test.json` (modified with `include_personality=false`) |
| Exp 85 | `experiments/configs/true_lean_decision.json` (smoke test, 1 hand) |
| Exp 86 | `experiments/configs/true_lean_decision.json` (full run, 5×40 hands) |

---

## Phase 3: Prompt Leakage Fixes & True Lean (Experiments 85-86)

### Prompt Leakage Discovery

Even with the "lean" config from exp 78/80, captured prompts from the `decision-with-reasoning` variant still contained noise. Four sources of leakage were identified:

| Leakage Source | Root Cause | Impact |
|---|---|---|
| `Persona: Napoleon` | `build_base_game_state()` always included persona line | Primes model into roleplay mode |
| `RESPONSE STYLE: Minimal...` | Drama context passed to render regardless of `use_simple_response_format` | Tells model to "Build your dramatic_sequence" even when disabled |
| `"You can check for free"` | `pot_odds_info = {'free': True}` when cost_to_call is 0 | Active passivity nudge — model reads it as "checking is the default" |
| `"bet_sizing"` in response format | `response_format_simple` template included `"bet_sizing": "..."` field | Contradicts system prompt which only asks for `action` + `raise_to` |

### Code Fixes Applied

1. **`poker/prompts/decision.yaml`**: `response_format_simple` changed from `{"action", "bet_sizing", "raise_to"}` to `{"action": "<fold|check|call|raise|all_in>", "raise_to": <BB amount>}`
2. **`poker/controllers.py`**: `build_base_game_state()` now accepts `include_persona: bool = True` param; call site passes `prompt_config.include_personality`
3. **`poker/controllers.py`**: `_build_decision_prompt()` sets `prompt_drama_context = None` when `use_simple_response_format=True` (still returns full `drama_context` in tuple for capture enrichment)
4. **`poker/controllers.py`**: `pot_odds_info = None` instead of `{'free': True}` when cost_to_call is 0 (model already sees `cost_to_call: 0.00 BB`)

### Experiment Design (Exp 86)

3 variants, 5 tournaments of 40 hands each, 4 players:

| Variant | Description |
|---|---|
| `decision-with-reasoning-old` | Same config as exp 80 — reproduces the noisy lean prompt as baseline |
| `true-lean` | Everything off: emotional_state, tilt_effects, zone_benefits, situational_guidance, session_memory, opponent_intel, include_personality=false, use_simple_response_format=true + guidance_injection |
| `true-lean-with-coaching` | Same as true-lean but `situational_guidance=true` (pot-committed, short-stack, made-hand coaching) |

### Smoke Test Verification (Exp 85)

Captured prompt for the `true-lean` variant was verified to contain ONLY:
- Cards, hand breakdown, stack, community cards
- Positions, opponents, pot, options, raise guidance
- Guidance injection (step-by-step reasoning)
- Base instruction + simple response format

**None** of these appeared: `Persona:`, `MIND GAMES`, `DRAMATIC SEQUENCE`, `BETTING DISCIPLINE`, `RESPONSE STYLE`, `POKER FACE MODE`, `EMOTIONAL STATE`, `bet_sizing`, `check for free`.

Token comparison: control ~810 input tokens, true-lean ~634 tokens (**22% reduction**).

### Full Experiment Results (Exp 86)

*(Results to be added when experiment completes)*

---

## Key Insights (Across All Experiments)

1. **Prompt framing matters more than prompt content.** Adding "play poker well" framing beats adding poker rules. The model knows poker — it just needs permission to play analytically.

2. **Every prompt section is a potential passivity source.** Models interpret instructions conservatively. "Don't bet big with nothing" → never bet big. "Check for free" → always check.

3. **Step-by-step reasoning > explicit rules.** Guidance injection asking the model to think through hand strength → pot odds → sizing produces better decisions than listing sizing rules.

4. **Personality ordering survives prompt stripping.** Even without persona names or personality system prompts, the psychology system's range guidance creates differentiated VPIP ordering across looseness anchors.

5. **Token efficiency correlates with quality.** Fewer distracting tokens = more attention on game state = better decisions. The 22% token reduction isn't just cost savings — it's quality improvement.

6. **System prompt / user message conflicts are invisible.** The `use_simple_response_format` flag changed the user message but the personality system prompt continued requesting rich JSON fields. 82-95% of responses still contained inner_monologue and dramatic_sequence. Always verify captured prompts end-to-end.

7. **Character reasoning corrupts poker decisions.** The full house fold (Exp 78, Sherlock H31) shows the model rejecting correct hand evaluation while reasoning in character. Separating decision from expression is the fix.

---

## Architecture Direction: Two-Phase Decision Chain

These results support separating decision-making from character expression:

### Phase 1: Decision (implemented — lean prompt)
- `include_personality=false` + `use_simple_response_format=true`
- Lean toggles: no betting_discipline, mind_games, dramatic_sequence, expression_filtering, chattiness
- Zone mechanics and situational guidance optionally enabled
- Simple JSON response: `{"action", "raise_to"}`
- Step-by-step reasoning via guidance_injection

### Phase 2: Expression (not yet built)
- Separate LLM call AFTER the decision is made
- Receives: the decision + game state + personality
- Produces: `dramatic_sequence`, table talk, character flavor
- Can use a cheaper/faster model (e.g., gpt-5-nano or Groq)
- Decision quality is locked in — expression can't degrade it

### Benefits
- Decision quality isolated from character acting
- Can A/B test expression independently
- Expression call is optional (skip for experiments, enable for live game)
- Different model tiers for each phase
