---
purpose: Analysis of the Hybrid Bounded-Options AI decision system
type: analysis
created: 2026-02-09
last_updated: 2026-02-14
---

## Session 5 (2026-02-14): VPIP Calibration — Style Hints, Hand Classification & Range Gate

### Problem: All Players at 85-100% VPIP

Despite the preflop equity fix (exp 8, range-based equity replacing flat 0.40), TAG players like Sherlock Holmes were still at 85-100% VPIP. Investigation revealed three compounding issues:

1. **Style hints biased toward action** — TAG hint said "Play aggressively with strong hands — bet for value, pressure opponents" but never mentioned folding. The LLM read "play aggressively" and played every hand.
2. **Nudge phrases made folds sound painful** — "Tough fold. Discipline." vs "Worth a stab. Test them." The LLM naturally gravitated toward the exciting option.
3. **No hand context** — The LLM saw `Cards: 4s 3c` with no classification. Without "Bottom 10% of starting hands," it couldn't evaluate hand quality independently.

### Fix 1: Rewrite Style Hints (bounded_options.py)

| Profile | Before | After |
|---------|--------|-------|
| tight_aggressive | "Play aggressively with strong hands — bet for value, pressure opponents." | **"Fold weak hands. When you do play, bet aggressively for value."** |
| tight_passive | "Play tight — fold marginal hands, only continue with strong holdings." | **"Fold most hands. Only continue with strong holdings."** |
| loose_aggressive | "" (empty) | **"Play many hands and apply pressure — raise or fold, avoid flat calls."** |

Key insight: TAG hint was only encoding the "A" (aggressive) and completely missing the "T" (tight).

### Fix 2: Rewrite Nudge Phrases (nudge_phrases.py)

| Category | Before | After |
|----------|--------|-------|
| fold_correct | "Save chips for a better spot" | **"Easy fold." / "Clear fold."** |
| fold_tough | "Tough fold. Discipline." | **"Not your hand. Move on."** |
| raise_probe | "Worth a stab." / "Test them." | **"Speculative raise." / "Thin value at best."** |
| raise_bluff | "Represent strength." | **"Risky bluff."** |

Philosophy: folds should sound decisive and confident; speculative raises should sound uncertain.

### Fix 3: Hand Classification in Prompt (cherry-pick from daily/2026-02-07)

Added `classify_preflop_hand()` output to the lean prompt. The LLM now sees:
```
Cards: 4s 3c
Hand: 43o - Unconnected cards, Bottom 10% of starting hands
Street: Pre Flop | Stack: 49 BB | Pot: 4.0 BB
Fold weak hands. When you do play, bet aggressively for value.
```

Also added: street name, action history summary, and reasoning field in LLM response.

### Fix 4: Range Gate — Position Offsets (cherry-pick from daily/2026-02-07)

The old range gate had `POSITION_CLAMPS` with hard ceilings (early=35%, button=65%). Napoleon (looseness=0.79) was clamped to 35% from early position — playing like a TAG.

Fix: replaced clamps with `POSITION_OFFSETS` (UTG=-15pp, BTN=+5pp). Extended hand tiers from TOP_35 to TOP_75 so loose archetypes have enough hands marked "in range."

### Experiment Results

#### Progression of Sherlock (TAG) VPIP across experiments:

| Experiment | Change | Sherlock VPIP |
|------------|--------|---------------|
| 8 (baseline) | Range-based equity | 85-100% |
| 9 | + Nudge phrase rework | **100%** (no effect) |
| 10 | + Style hint rewrite | **87-93%** (small improvement) |
| 11 | + Hand classification + reasoning | **35-50%** (breakthrough) |

Hand classification was the key — the LLM needs explicit hand quality context to make fold decisions.

#### Cross-Personality Validation (exp 12, different roster):

| Player | Archetype | Target VPIP | Raw-EV | Nudges |
|--------|-----------|-------------|--------|--------|
| Sun Tzu | TAG (L=0.40) | 20-25% | **29%** | **25%** |
| Abraham Lincoln | TP (L=0.22) | 10-15% | **39%** | **37%** |
| Mark Twain | Default (L=0.62) | 25-35% | **29%** | **33%** |
| Blackbeard | LAG (L=0.87) | 35-50% | **73%** | **50%** |

Correct archetype ordering: Blackbeard (LAG) >> Mark Twain >> Sun Tzu (TAG). VPIP spread 25-45pp.

#### 3-Way Comparison (exp 13): raw-ev vs nudges vs nudges+rangegate

| Player | Archetype | raw-ev | nudges | nudges+rangegate |
|--------|-----------|--------|--------|------------------|
| Abraham Lincoln | TP | 44% | 40% | **6%** |
| Mark Twain | Default | 17% | 16% | **41%** |
| Sun Tzu | TAG | 33% | 39% | **56%** |
| Blackbeard | LAG | 62% | 55% | **94%** |
| VPIP spread | | 45pp | 39pp | **88pp** |

Range gate massively amplifies differentiation (88pp spread) but overcorrects — Abe at 6% is too nitty, Blackbeard at 94% is too loose, and Sun Tzu/Mark Twain ordering flipped.

### Seating Order Confound (exp 15)

Shuffling seating order produced dramatically different VPIP numbers for the same players:

| Player | Exp 13 nudges (original seat) | Exp 15 nudges (shuffled seat) |
|--------|-------------------------------|-------------------------------|
| Sun Tzu | 39% | **6%** |
| Abraham Lincoln | 40% | **64%** |
| Blackbeard | 55% | **88%** |
| Mark Twain | 16% | **70%** |

Position is a significant confound. Added `shuffle_seating` config option to `ExperimentConfig` — when enabled, seating is shuffled per tournament using deterministic seed (`random_seed + tournament_number`) for reproducible but varied positioning.

### Commits

| Hash | Description |
|------|-------------|
| `7f45def4` | fix: use range-based equity for preflop instead of hand tier buckets |
| `4e3ddd35` | feat: enrich lean bounded prompt with street context and action history |
| `a1eab49c` | fix: rewrite style hints and nudge phrases to reduce VPIP |
| `1392b3e4` | feat: extend hand tiers to 75% and replace range clamps with position offsets |

### Open Issues

1. **Range gate overcorrects** — 88pp spread but wrong absolute values. Needs softer EV label biasing for in-range marginal hands.
2. **Abraham Lincoln (TP) too loose without range gate** — 37-40% VPIP vs 10-15% target. The "Fold most hands" hint + hand classification isn't strong enough for very tight profiles.
3. **Small sample sizes** — 10-hand tournaments with 2 per variant. Need larger runs (50+ hands, 10+ tournaments) for stable VPIP estimates.
4. **Nudges vs raw-ev parity** — Both variants give similar VPIP numbers, suggesting hand classification is doing the heavy lifting and nudge phrasing has minimal incremental impact.

---

## Session 4 (2026-02-13): Post-Flop Check Rate Analysis, Archetype Rebalance & Centralized Classification

### Post-Flop Check Rate: Mostly Legitimate

The bounded options v2 merge and option ordering experiments (113850) showed a 34.6% post-flop check rate with default ordering. Deep analysis of all 807 post-flop checks revealed:

| Category | Count | % of checks |
|---|---|---|
| **Legitimate** (no +EV raise available) | 744 | 92.2% |
| Missed value (neutral check, +EV raise available) | 63 | 7.8% |

Of the 63 missed-value checks, 37 (36%) only had ALL_IN as the +EV raise (reasonable to skip in a small pot). The remaining **66 genuine leaks represent only 2.1% of all postflop actions** — a properly-sized +EV raise was available but the LLM checked instead.

### Discovery: Leak is Archetype-Driven, Not Personality-Specific

The 66 missed value bets were concentrated in one player:

| Player | Archetype | Missed value | Total postflop | Leak % |
|---|---|---|---|---|
| Bob Ross | tight_passive (0.10/0.38) | 49 | 1136 | **4.3%** |
| Batman | default | 8 | 671 | 1.2% |
| Sherlock Holmes | LAG (0.60/0.58) | 6 | 1009 | 0.6% |
| Napoleon | LAG (0.80/0.79) | 5 | 531 | 0.9% |

Experiment **113851** tested different characters in the same archetype slots:

| Archetype | Original | Leak% | Swap | Leak% |
|---|---|---|---|---|
| tight_passive | Bob Ross | **4.3%** | Ebenezer Scrooge | **3.9%** |
| LAG (high) | Napoleon | 0.9% | Joan of Arc | 0.6% |
| LAG (mid) | Sherlock Holmes | 0.6% | Winston Churchill | 0.9% |
| default | Batman | 1.2% | Nikola Tesla | 0.7% |

**Conclusion:** The ~4% missed-value leak is baked into the tight-passive profile's interaction with nano, not caused by any specific personality prompt. This is acceptable — passive players *should* leave some value on the table.

### Roster Audit: 72% of Characters Were LAG

| Archetype | Count | % |
|---|---|---|
| loose_aggressive | 36 | 72% |
| tight_passive | 7 | 14% |
| loose_passive | 7 | 14% |
| **tight_aggressive** | **0** | **0%** |

Zero TAG characters. Zero Nits, Rocks, Maniacs, or Calling Stations. The personality generator had defaulted to aggr >= 0.5 and loose >= 0.5 for all "interesting" characters.

### Fix: Centralized Archetype Module + Personality Rebalance

**Part A: Created `poker/archetypes.py`** as single source of truth for classification thresholds. Previously 4 separate systems used inconsistent boundaries:

| System | Tight boundary | Aggr boundary |
|---|---|---|
| HybridAI | looseness < 0.45 | aggr < 0.5 |
| RangeGuidance | tightness > 0.5 (loose < 0.5) | aggr > 0.5 |
| OpponentModel | VPIP < 0.3 | AF > 1.5 |
| CoachEngine | VPIP < 0.3 | AF > 1.5 |

Now all import from `poker/archetypes.py`.

**Part B: Rebalanced 24 personalities** in `personalities.json`:

| Archetype | Before | After | Key characters |
|---|---|---|---|
| LAG | 36 | 19 | Napoleon, Dracula, Cleopatra |
| Balanced | — | 10 | Tesla, Dr. Seuss, Jesus Christ |
| TAG | 0 | 9 | Sherlock, Joan of Arc, Churchill, Machiavelli |
| Fish/Station | 0 | 7 | Alice, Lucille Ball, Cheshire Cat, Houdini |
| Rock/Nit | 0 | 5 | Buddha, Bob Ross, Scrooge, Librarian, Lincoln |

### Experiment 113852: Archetype Spectrum (4 archetypes, no CaseBot)

| Player | Archetype | Wins (of 10) | Raise% | Fold% | Avg Stack |
|---|---|---|---|---|---|
| Joan of Arc | TAG | **4 (40%)** | 29.3% | 9.5% | 7,053 (+41%) |
| Alice | Station | 3 (30%) | 26.4% | 8.7% | 7,181 (+44%) |
| Buddha | Rock | 2 (20%) | 22.1% | 11.6% | 5,967 (+19%) |
| Napoleon | LAG | 1 (10%) | 43.9% | 6.1% | 5,524 (+10%) |

**Findings:** TAG dominates (40% win rate). Station survives longest (highest avg stack). LAG burns out fastest despite highest aggression. Matches real poker dynamics: TAG > Station > Rock > LAG.

Post-flop aggression shows clear differentiation:
- Napoleon (LAG): 52.6% raise — bets over half the time
- Joan of Arc (TAG): 36.6% raise — solid value betting
- Buddha (Rock): 26.4% raise, 45.7% check — very passive
- Alice (Station): 30.6% raise, 45.9% check — check-call pattern

### Experiment 113853: Archetype Spectrum + CaseBot

| Player | Archetype | Wins (of 10) | Raise% | Fold% | Avg Stack |
|---|---|---|---|---|---|
| **CaseBot** | RuleBot | **3 (30%)** | — | — | — |
| Napoleon | LAG | 2 (20%) | 42.8% | 8.6% | 6,185 |
| Alice | Station | 2 (20%) | 21.5% | 10.7% | 7,790 |
| Buddha | Rock | 2 (20%) | 16.5% | 13.8% | 6,748 |
| Joan of Arc | TAG | 1 (10%) | 19.7% | 13.5% | 6,432 |

**Findings:** CaseBot is the strongest individual performer (30% win rate in a 5-player field vs 20% expected). Adding CaseBot compressed all LLM archetypes downward — TAG dropped from 40% to 10% wins. CaseBot's deterministic strategy exploits the LLM players' remaining leaks. Alice (Station) has highest avg stack (7,790), confirming calling stations are hard to bluff out even for rule-based play.

### Option Ordering Experiment (113850, completed)

3-way comparison of option presentation order (10 tournaments each):

| Variant | raise% | call% | check% | fold% | all_in% |
|---|---|---|---|---|---|
| **default** | **37.0%** | 28.1% | 23.8% | **9.6%** | 2.4% |
| ev_descending | 37.0% | 17.1% | 21.5% | 22.8% | 1.6% |
| shuffle | 29.1% | 18.7% | 21.8% | 26.4% | 4.1% |

**Conclusion:** Default ordering is best. ev_descending barely changes raise rate but doubles fold rate (9.6→22.8%) because +EV fold moves first too. Shuffle is worst — drops raise, increases fold. No ordering change needed.

### Bugs Fixed

1. **+EV promotion append-to-end bug** — promotion code removed best option and appended at end, moving fold to position 4. Fixed to replace in-place.
2. **`randomize_option_order` replaced with `option_order` field** — now supports 'default', 'shuffle', 'ev_descending'.
3. **test_prompt_config.py** — updated stale reference to `randomize_option_order`.

### Relevant Experiments

| ID | Name | Status | Key finding |
|---|---|---|---|
| 113845 | nudge_test | interrupted | Composed nudges too aggressive for nano (raise 30→60%) |
| 113847 | range_gate_test | interrupted | Range gate improves preflop discipline (fold 13→20%) |
| 113848 | postflop_tuning_v2 | completed | Baseline: 24% raise, 37% check postflop |
| 113850 | option_order_test | completed | Default ordering wins — no change needed |
| 113851 | archetype_leak_test | completed | Confirmed ~4% leak is archetype-driven |
| 113852 | archetype_spectrum_test | completed | TAG dominates 4-way (40% wins) |
| 113853 | archetype_vs_casebot_test | completed | CaseBot 30%, LLMs compressed to 10-20% each |

### Previous Session Plans (Completed/Resolved)

The post-flop value betting tasks from the previous session plan are now resolved:

- **"Analyze value betting patterns more deeply"** — Done. 92% of checks are legitimate. 2.1% genuine leak.
- **"Option C: Order options by EV"** — Tested (113850). Default ordering already best.
- **"Option D: Change style tags based on hand strength"** — Implemented via STYLE_PROFILES (check_promotion, check_penalty_threshold).
- The remaining ~4% leak is archetype-appropriate behavior for passive players, not a system defect.

## Session 2 (2026-02-10): Telemetry Fix & VPIP Analysis

### Bugs Fixed

**1. Telemetry Bug - hand_number NULL**

Hybrid controller wasn't passing telemetry data to captures. Fixed by:
- Adding `hand_number=self.current_hand_number` to `chat_full()` call
- Adding `prompt_template='decision_bounded'` for tracking
- Adding `_on_captured` callback to enricher to track capture ID
- Calling `update_prompt_capture()` after decision to set `action_taken`

**File**: `poker/hybrid_ai_controller.py` lines 116-121, 358-386

**2. action_taken NULL**

Hybrid controller wasn't updating captures with final action. Fixed by:
- Tracking capture ID via `_on_captured` callback in enricher
- Calling `update_prompt_capture(capture_id, action_taken=action, raise_amount=...)` after decision

### VPIP Analysis

Previous data showed hybrid VPIP ~10% (extremely tight). After equity bug fix:

**New Experiment (113817 - hybrid_vs_casebot_1v1):**
| Player | Hands | VPIP |
|--------|-------|------|
| Batman (hybrid) | 495 | **35.6%** |

This is a **3.5x improvement** from previous 10% VPIP. The fix to use `calculate_equity_vs_ranges()` instead of quick equity made the difference.

**Tournament Results:**
- CaseBot: 4 wins
- Batman (hybrid): 1 win

### Current Experiments Running

| ID | Config | Status |
|----|--------|--------|
| 113818 | hybrid_1_regular_4_casebot_1 | Running |

Mixed experiment: 1 hybrid (Batman) vs 4 regular AI vs 1 CaseBot to compare VPIP and fold behavior across player types.

### Mixed Experiment Results (113818)

**Tournament Results:**
| Winner | Wins |
|--------|------|
| Gordon Ramsay (regular) | 1 |
| Bob Ross (regular) | 1 |

**VPIP by Player:**
| Player | Type | Hands | VPIP |
|--------|------|-------|------|
| Batman | Hybrid | 16 | 75.0% |
| Snoop Dogg | Regular | 61 | 14.8% |
| Gordon Ramsay | Regular | 268 | 13.1% |
| Bob Ross | Regular | 233 | 12.0% |
| Oprah Winfrey | Regular | 89 | 11.2% |

**Key Observations:**
1. Hybrid VPIP (75%) is significantly higher than regular AI (11-15%)
2. Sample size for hybrid is small (16 hands) due to early eliminations
3. Regular AI VPIP is very tight (11-15%), suggesting prompt may need adjustment
4. The equity bug fix improved hybrid VPIP from ~10% to 35-75%

### Summary

The telemetry fixes and equity calculation improvements have significantly changed hybrid behavior:
- **Before**: ~10% VPIP (too tight)
- **After 1v1 fix**: 35.6% VPIP
- **After mixed experiment**: 75% VPIP (possibly too loose, small sample)

---

## Session 3 (2026-02-11): Root Cause Analysis & Marginal Zone Fix

### Root Cause: Why Hybrid Lost So Quickly

Investigation of experiment 113818 revealed hybrid (Batman) had only **7.9% fold rate** vs 37-55% for regular AI. The core issue:

**Conflicting Signals in Prompts:**

| Signal | Says |
|--------|------|
| Hand strength guidance | "you should fold this" |
| Bounded options EV label | CALL = **+EV** |

The LLM followed the mathematical framing (+EV) over natural language advice.

**Example: J5s facing 800 into 1950**
- Prompt: "J5s - well below your range, you should fold this"
- Options: CALL = +EV "meets pot odds", FOLD = neutral
- LLM picks: CALL (the +EV option)

### The EV Threshold Problem

The call EV threshold was too loose:

```python
# OLD: Call is +EV when equity >= required * 1.2
# 40% equity >= 29% * 1.2 = 34.8% → +EV (too generous)
```

This labeled marginal calls as "+EV", overriding hand guidance.

### Fix: Three-Zone EV Labeling

Changed from binary (+EV/neutral) to three zones:

| Zone | Condition | Label |
|------|-----------|-------|
| +EV | equity >= required × 1.7 | "Clearly profitable" |
| Marginal | equity >= required × 0.85 | "Close - your call" |
| -EV | equity < required × 0.85 | "Below required odds" |

**File**: `poker/bounded_options.py` lines 207-218, 240-246

### Validation Results (Experiment 113819)

| Metric | Before (113817) | After (113819) | Change |
|--------|-----------------|----------------|--------|
| VPIP | 60% | **40%** | -20% |
| Fold rate | 10% | **17%** | +7% |
| Tournament wins | 1/5 | 1/4 | Similar |

**Key Findings:**
1. Marginal zone removes +EV bias on borderline calls
2. LLM now follows hand guidance when options are neutral
3. VPIP dropped to healthier range without becoming too tight
4. Tournament performance unchanged vs CaseBot

### Code Changes

**bounded_options.py** - Three-zone EV labeling:
```python
if equity >= required_equity * 1.7:
    call_ev = "+EV"  # Clearly profitable
elif equity >= required_equity * 0.85:
    call_ev = "marginal"  # Close - defer to guidance
else:
    call_ev = "-EV"
```

**hybrid_ai_controller.py** - Telemetry fixes:
- Pass `hand_number` to `chat_full()`
- Track capture ID via `_on_captured` callback
- Update capture with `action_taken` after decision

---

## Session 3 Continued: Post-Flop Value Betting Issue

### New Finding: LLM Not Value Betting Strong Hands

After fixing preflop calling, a new issue emerged: **LLM checks value hands instead of betting**.

**Post-Flop Aggression Stats:**

| Metric | Before (113817) | After (113819) |
|--------|-----------------|----------------|
| Raises/All-in | 25.7% | 27.6% |
| Calls | 17.5% | 17.9% |
| Checks | 50.8% | 50.2% |
| Aggression Factor | 1.47 | 1.54 |

AF is reasonable (~1.5), but inspection of specific hands shows missed value.

### Evidence: Inner Monologue Analysis

**Example 1: TT on Q-Q-7-3-K board**
- LLM says: "I have two pair (Queens and Tens)"
- Reality: TT is pocket pair, board has QQ - not "two pair" in the traditional sense
- Action: CHECK (should consider value bet)

**Example 2: Q3 on Q-6-9 board**
- LLM says: "top pair with a decent kicker"
- Reality: Q3 is top pair with **terrible** kicker
- Action: CHECK with "pot control" rationalization

**Example 3: KJ on K-3-7 board**
- LLM says: "protect my hand... checking keeps control"
- Reality: Top pair good kicker is a clear value bet
- "Protect" in poker means BET, not check

### Root Cause

The LLM fundamentally misunderstands poker concepts:

1. **Misreads hand strength** - Confuses board pairs with made hands
2. **Rationalizes passive play** - Uses "pot control" as excuse not to value bet
3. **Misuses poker terminology** - "Protect" = check instead of bet

### This Is Different From Preflop Issue

| Issue | Problem | Fix Applied |
|-------|---------|-------------|
| Preflop overcalling | Options labeled +EV incorrectly | ✅ Marginal zone |
| Post-flop passive play | LLM misunderstands hand strength | ❌ Not yet fixed |

### Potential Fixes (Not Yet Implemented)

1. **Block check for value hands** - Don't offer CHECK when equity > 60%
2. **Order options by EV** - Put +EV options first in list
3. **Add explicit hand strength guidance** - "You have TOP PAIR GOOD KICKER - bet for value"
4. **Change style tags** - Don't mark CHECK as "conservative" with strong hands

---

# Hybrid Bounded-Options AI Analysis

## Problem Statement

LLM-based AI players consistently make mathematically catastrophic decisions:
- Folding monster hands (quads, full houses, flushes)
- Folding +EV spots with strong equity
- Missing value bets with strong holdings

Analysis of 240,000+ decisions in `player_decision_analysis` reveals:

| Issue | Count | Total EV Lost |
|-------|-------|---------------|
| Folded when should have raised | 7,637 | $3,865,705 |
| Folded with 90%+ equity | 12 | $140,000+ |
| Folded with 100% equity (nuts) | 10+ | $80,000+ |

### Notorious Examples

| Player | Hand | Board | Equity | Action | EV Lost |
|--------|------|-------|--------|--------|---------|
| Dave Chappelle | J♥J♠ | 3♥J♣T♥J♦J♠ | 100% (quads) | Fold | $21,250 |
| Batman | K♥4♥ | 7♥Q♦9♥6♥T♠ | 99% (flush) | Fold | $11,910 |
| Player3 | T♥7♦ | 9♦J♠T♠T♣T♦ | 100% (quads) | Fold | $7,932 |
| Buddha | K♠6♥ | K♦6♠K♣8♥ | 98% (full house) | Fold | $38,068 |

## Solution: Hybrid Bounded-Options

The hybrid system combines rule-based mathematical rigor with LLM personality expression:

```
Game State → Rule Engine → 2-4 Bounded Options → LLM Choice + Narrative → Action
```

### Key Components

1. **BoundedOption dataclass** (`poker/bounded_options.py`)
   - `action`: fold, check, call, raise, all_in
   - `raise_to`: amount (0 if not raising)
   - `rationale`: explanation for LLM
   - `ev_estimate`: "+EV", "neutral", "-EV"
   - `style_tag`: "conservative", "standard", "aggressive", "trappy"

2. **Blocking Logic**
   - Block fold when equity > 2× required equity
   - Block fold when equity ≥ 90% (monster hands)
   - Block fold when pot-committed (already_bet > remaining_stack)
   - Block call when equity < 5% (drawing dead)

3. **HybridAIController** (`poker/hybrid_ai_controller.py`)
   - Extends AIPlayerController
   - Overrides `_get_ai_decision()` to present bounded options
   - Falls back to best +EV option if LLM fails

## Verification Results

### Test 1: Historical Bad Folds

Tested against 10 worst historical folds (all 100% equity):

| Player | Equity | EV Lost | Fold Blocked? |
|--------|--------|---------|---------------|
| Dave Chappelle | 100% | $21,250 | ✓ YES |
| Batman | 99% | $11,910 | ✓ YES |
| Player3 | 100% | $7,932 | ✓ YES |
| Gordon Ramsay | 100% | $6,712 | ✓ YES |
| The Rock | 100% | $4,550 | ✓ YES |
| ... | ... | ... | ✓ YES |

**Result: 10/10 catastrophic folds blocked**

### Test 2: Correct Folds Still Allowed

Tested weak hands that SHOULD be foldable:

| Scenario | Equity | Required | Fold Allowed? |
|----------|--------|----------|---------------|
| Weak hand, big bet | 15% | 44% | ✓ YES |
| Drawing dead | 3% | 20% | ✓ YES |
| Marginal vs all-in | 25% | 62% | ✓ YES |
| Bluff catcher | 35% | 50% | ✓ YES |

**Result: 4/4 correct folds allowed**

### Test 3: Live LLM Integration

Tested with actual GPT-5-nano calls:

| Hand | Options Offered | LLM Choice | Personality |
|------|-----------------|------------|-------------|
| 3♣8♥ (weak) | fold, call, raise, all_in | Fold | ✓ Correct |
| A♥A♠ (aces) | call, raise, all_in (NO FOLD) | Raise to 200 | "I'm serving it raw!" |

**Result: Fold blocked for aces, personality preserved**

### Test 4: Raise Encouragement

For "folded when should have raised" cases:

| Metric | Count |
|--------|-------|
| Fold blocked | 10/10 |
| Raise offered | 5/10 |
| Raise marked +EV | 4/10 |

For cases where raise isn't offered: Player is pot-committed (stack = cost to call),
so call is the only valid option. This is **correct behavior**.

## Experiment Results (2026-02-10)

Three experiments were run to validate the hybrid system in live gameplay:

### Experiment Configuration

| Experiment | Setup | Tournaments | Hands |
|------------|-------|-------------|-------|
| hybrid_vs_casebot_1v1 | Batman (hybrid) vs CaseBot | 5 | 500 |
| hybrid_vs_casebot_4v1 | 4 hybrid AIs vs 1 CaseBot | 3 | 300 |
| hybrid_vs_regular_3v3 | 3 hybrid vs 3 regular AI | 3 | 300 |

### Tournament Results

| Player Type | Tournament Wins |
|-------------|-----------------|
| **Hybrid AI** | 5 wins |
| CaseBot (pure rules) | 3 wins |
| Regular AI | 2 wins |

**Breakdown by experiment:**
- **1v1**: Batman (Hybrid) 3 - CaseBot 2
- **4v1**: Bob Ross (Hybrid) 2 - CaseBot 1
- **3v3**: Oprah 1, Elon Musk 1 (2/3 tournaments completed)

### Key Metrics

| Metric | Value |
|--------|-------|
| Total AI decisions | 1,185 |
| Decisions with fold blocked | 431 (36.4%) |
| High-equity folds (>70%) | **0** |
| Historical high-equity folds | 26 |

### Findings

1. **Fold Blocking Works**
   - 36.4% of decisions had fold removed as an option
   - Zero high-equity folds detected in experiments
   - Historical data shows 26 such catastrophic folds before hybrid system

2. **Hybrid AI is Competitive**
   - Won 5 of 8 completed tournaments against pure rule-based play
   - Personality expression preserved through option selection + narrative

3. **Prompt Format Validated**
   Example from Gordon Ramsay decision:
   ```
   === YOUR OPTIONS ===
   Given the math (equity: 62%, pot odds: 9.0:1),
   your sensible choices are:

   1. CALL - great pot odds [+EV, standard]
   2. RAISE to 500 - Small probe/value bet [+EV, conservative]
   3. RAISE to 603 - Standard value bet [+EV, standard]
   ```

4. **Action Distribution**
   | Action | Count | Percentage |
   |--------|-------|------------|
   | fold | 605 | 51.1% |
   | check | 362 | 30.5% |
   | raise | 111 | 9.4% |
   | call | 105 | 8.9% |
   | all_in | 2 | 0.2% |

### Gap Identified & Fixed

Hand equity was not being logged during experiments, preventing detailed fold-by-equity analysis.

**Root Cause** (fixed 2026-02-10):
1. Equity only calculated when `enable_psychology=True` in experiment config
2. Even when calculated, equity was not saved to `hand_equity` table

**Fix Applied** (`experiments/run_ai_tournament.py`):
- Added `enable_telemetry` config option (defaults to `True`)
- Equity now calculated when either `enable_psychology` OR `enable_telemetry` is enabled
- Equity history saved to database after `on_hand_complete()` for analytics

Future experiments will have full equity data in `hand_equity` table for analysis.

## Known Issue: Equity Calculator Bug (2026-02-10)

**Flagged for investigation via replay experiments.**

During live testing, Batman (hybrid) lost all-in with JJ vs CaseBot's flush:
- Board: 3♠ K♥ 5♣ 5♥ A♥ (3 hearts)
- Batman: J♦J♣ (overpair, no flush draw)
- CaseBot: 2♥10♥ (completed flush)

The bounded options showed **59% equity** when actual equity was **0%** (drawing dead).

**Capture IDs for replay**: 363233, 363234, 363235, 363236

**To investigate**:
```bash
python -m experiments.replay_with_guidance --capture-id 363236 --all-variants
```

**Root Cause**: Hybrid controller uses wrong equity function:
- **Current**: `calculate_quick_equity()` - Monte Carlo vs **random hands**
- **Should use**: `calculate_equity_vs_ranges()` - Monte Carlo vs **position/action-based ranges**

The range-based calculator (`poker/hand_ranges.py:945`) accounts for:
1. Opponent position (UTG tight, button wide)
2. Action-based narrowing (all-in = strong range)
3. PFR/VPIP observed stats
4. Board-connection weighting

**Fix**: Update `hybrid_ai_controller.py` line 156 to use `calculate_equity_vs_ranges()`
with opponent info from game state.

## Prompt Format

The LLM receives bounded options in this format:

```
=== YOUR OPTIONS ===
Given the math (equity: 50%, pot odds: 3.0:1),
your sensible choices are:

1. CALL
   Call 0.5 BB - great pot odds
   [+EV, standard]

2. RAISE to 200
   Small probe/value bet
   [neutral, conservative]

3. ALL_IN
   All-in - maximum commitment
   [neutral, aggressive]

Pick the option that fits your personality and the moment.

Respond with JSON:
{
  "choice": <option number 1-3>,
  "inner_monologue": "your reasoning",
  "dramatic_sequence": ["*action*", "speech", ...]
}
```

## Files Created/Modified

| File | Purpose |
|------|---------|
| `poker/bounded_options.py` | BoundedOption dataclass, generate_bounded_options() |
| `poker/hybrid_ai_controller.py` | HybridAIController extending AIPlayerController |
| `poker/prompts/decision_bounded.yaml` | Choice prompt template |
| `flask_app/handlers/game_handler.py` | Added hybrid controller support |
| `scripts/create_casebot_game.py` | Added --hybrid flag |
| `tests/test_bounded_options.py` | 31 unit tests |

## Usage

```bash
# Create a hybrid game
docker compose exec backend python scripts/create_casebot_game.py --hybrid

# Or specify individual hybrid bots
docker compose exec backend python scripts/create_casebot_game.py \
  --bots "HybridBot:hybrid" "CaseBot:case_based"
```

## Benefits

1. **No catastrophic folds** - Rules mathematically block bad decisions
2. **Personality preserved** - LLM still picks between options and provides narrative
3. **Exploitability hidden** - Varied LLM choices prevent pattern recognition
4. **Graceful degradation** - Falls back to best +EV if LLM fails

## Limitations

1. **Raise options depend on context** - When pot-committed (stack ≤ cost_to_call), only call is offered (correct behavior)
2. **Pre-flop equity estimates** - Uses hand tier heuristics, not Monte Carlo
3. **No bluff detection** - Options based on raw equity, not opponent modeling

## Future Enhancements

1. **Personality-weighted options** - Aggressive personalities see more raise options
2. **LLM for table talk only** - Simplify to just action + narrative
3. **Learning** - Track which options LLM picks, refine based on outcomes
4. **Opponent modeling integration** - Adjust options based on opponent tendencies

## Appendix: Blocking Logic Details

### `_should_block_fold()`

```python
def _should_block_fold(context: Dict) -> bool:
    equity = context.get('equity', 0.5)
    cost_to_call = context.get('cost_to_call', 0)
    pot_total = context.get('pot_total', 0)

    # No cost = check, not fold
    if cost_to_call <= 0:
        return True

    # Calculate required equity
    required = cost_to_call / (pot_total + cost_to_call)

    # Block if equity >> required (2x threshold)
    if required > 0 and equity > required * 2:
        return True

    # Block monster hands (90%+ equity)
    if equity >= 0.90:
        return True

    # Block pot-committed with decent equity
    already_bet = context.get('already_bet', 0)
    remaining_stack = context.get('player_stack', 0)
    if already_bet > remaining_stack and equity >= 0.25:
        return True

    return False
```

### `_should_block_call()`

```python
def _should_block_call(context: Dict) -> bool:
    equity = context.get('equity', 0.5)
    cost_to_call = context.get('cost_to_call', 0)

    # No cost = can always check
    if cost_to_call <= 0:
        return False

    # Block when drawing dead (<5% equity)
    if equity < 0.05:
        return True

    return False
```
