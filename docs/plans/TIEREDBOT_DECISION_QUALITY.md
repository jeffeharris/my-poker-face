---
purpose: Improve TieredBot postflop decision quality via board-aware classification, price-sensitive defense, and refined passive-opponent profiles
type: design
created: 2026-05-14
last_updated: 2026-05-14
---

# TieredBot Decision Quality

## TL;DR

TieredBot's CaseBot deficit is not one bug. It is a mix of noisy
hand-strength classification and real overfolding in
price-sensitive spots. Fixing it should be framed as **"make
TieredBot more board-aware and pot-odds disciplined,"** not
**"special-case CaseBot."**

The next iteration should produce better hand-strength labels,
prevent price-insensitive folds, and make passive-opponent
exploitation more value-heavy and bluff-light. Three things become
first-class: **board texture, price, and opponent behavior
profile.** This keeps the strategy general, testable, and easier
to debug.

## What we measured

### TAG vs CaseBot HU, 3 seeds × 500 hands

Total: **−96.8 bb/100**. Folds drive the leak.

| Bucket | Count | % | Mean Δ | bb/100 contrib |
|---|---:|---:|---:|---:|
| fold preflop | 352 | 23.5% | −58 | −13.7 |
| **fold flop** | **315** | **21.0%** | **−290** | **−60.9** |
| **fold turn** | **119** | **7.9%** | **−598** | **−47.4** |
| **fold river** | **127** | **8.5%** | **−737** | **−62.4** |
| uncontested win | 140 | 9.3% | +215 | +20.0 |
| **showdown win** | **306** | **20.4%** | **+754** | **+153.8** |
| showdown loss | 115 | 7.7% | −1,202 | −92.1 |

**Key observations:**
- Showdowns are net positive (+62 bb/100). When TAG reaches
  showdown, it wins 73% of the time (306 / 421).
- Postflop folds net **−171 bb/100**. That single line item is
  responsible for the entire loss.
- The leak isn't paying off bluffs at showdown. It's investing in
  earlier streets, then folding when CaseBot keeps betting.

### Postflop folds broken down by hand class

| Phase | Hand class | Count | Mean Δ | bb/100 contrib |
|---|---|---:|---:|---:|
| river | **nuts** | **29** | **−1753** | **−33.9** |
| river | **strong_made** | **21** | **−1700** | **−23.8** |
| turn | **strong_made** | **10** | **−2034** | **−13.6** |
| turn | **nuts** | **3** | **−1711** | **−3.4** |
| flop | strong_made | 4 | −1417 | −3.8 |
| flop | nuts | 1 | −1555 | −1.0 |
| (other classes — air, weak/medium_made — combined) | | | | ~−15 |

**TAG folds the nuts on the river 29 times in 1500 hands.** Plus 21
"strong_made" river folds and 13 strong-or-nuts turn folds.
Combined leak from these high-class postflop folds: **−79.5
bb/100** — the single biggest contributor to the loss.

### Example hand walkthroughs (5 captured)

1. **Hero 6♠Q♦ on 7♥T♥8♠2♣9♣ (river)**: hero has 10-high straight.
   Classifier calls "nuts." Any J in CaseBot's hand makes higher
   straight (J-high). CaseBot's 4-bet river → hero folds.
   **Classifier mislabel — fold itself was probably correct.**

2. **Hero A♦5♥ on T♠3♠A♥T♣ (turn)**: top pair / weak kicker on
   paired board. Classifier "strong_made." CaseBot all-in → hero
   folds at 38% pot odds. **Defensible fold; classifier label
   oversells the hand.**

3. **Hero A♠3♠ on A♦T♠8♠8♥7♠ (river)**: hero has nut spade flush.
   Paired board threatens full house. CaseBot all-in, 42% pot
   odds → hero folds. **Real leak.** CaseBot's jam range is too
   wide for this fold to be +EV.

4. **Hero T♦9♥ on 6♦A♣A♦8♦9♣ (river)**: hero has pair of 9s on
   paired-aces board. Classifier calls "nuts." Clear classifier
   bug; **fold itself is correct.**

5. **Hero K♦3♠ on 4♣Q♦J♣K♥T♠ (river)**: top pair on
   4-to-Broadway board. Classifier "strong_made." Facing small
   bet, 16.65% pot odds → hero folds. **Real leak — top pair at
   16% pot odds is a clear call.**

### The honest read

The −79.5 bb/100 "fold nuts/strong_made" figure is partly real
leak, partly classifier noise. Of 5 examples: 2 were classifier
mislabels (the fold was right), 1 was a defensible fold of an
oversold label, and **2 were real leaks** (folding nut flush
to a wide jam range; folding top pair to a tiny river bet).

Until the classifier downgrades land, we can't measure the *real*
size of the fold leak. That's why classifier fixes come first.

## What's NOT broken

These are working as designed and shouldn't be modified:

- **Showdowns**: 73% win rate at showdown, +153.8 bb/100 from
  showdown wins. The hands TAG takes to showdown are correctly
  chosen.
- **Preflop folds**: 23.5% fold rate, mean −58 chips. Normal HU
  preflop behavior.
- **Air folds**: hero correctly folds 191 air-no-draw flop spots.

Don't loosen these.

## What is broken (and the proposal)

### 1. Board-aware hand classification

The classifier (`simplify_hand_class` in
`poker/strategy/hand_classification.py`) is overly generous. It
labels:

- Non-nut straights as `nuts` (Example 1)
- One-pair hands on paired boards as `nuts` (Example 4)
- Top pair on coordinated boards as `strong_made` (Example 5)

Every downstream rule that reads `nuts` or `strong_made`
(`value_override`, `value_vs_station`, the bluff-catch override,
this proposal's defense floors) inherits the noise.

**Implementation:**

- Downgrade non-nut straights when higher straights are possible
  (4-straight on board + opponent could hold higher card).
- Prevent one-pair on paired boards from labeling as nuts.
- Downgrade top pair / two pair on highly coordinated boards.
- Track danger flags:
  - `paired_board`
  - `four_straight_board`
  - `four_flush_board`
  - `higher_straight_possible`
  - `full_house_possible`
- Add richer hand-strength output:
  ```
  hand_class: nuts | strong_made | medium_made | weak_made | air
  nut_status: actual_nuts | near_nuts | non_nut_strong | bluff_catcher
  danger_flags: {...}
  ```

**Deliverables:**

- Unit tests for straights, flushes, paired boards, 4-liner
  boards, top-pair downgrade.
- Diagnostics that distinguish raw hand type from strategic hand
  strength.

**Existing-layer interactions:**

- `_classify_postflop_hand_strength` (in `tiered_bot_controller.py`)
  currently calls `simplify_hand_class` directly. Both the bluff
  catch override (`BLUFF_CATCH_TRIGGER_CLASSES`) and the strong
  hand override (`_OVERRIDE_TRIGGER_CLASSES`) read those strings.
  The downgrade has to be done at the classifier layer, not in
  consumers, so every rule sees the same corrected value.

### 2. Price-sensitive defense floors

Hero pure-folds reasonable made hands at favorable pot odds
(Examples 3 and 5). There's no rule that says "you can't fold
strong hands when the price is cheap."

**Implementation:**

- Calculate required equity per decision:
  ```
  required_equity = call_amount / (pot + call_amount)
  ```
- Preserve minimum call frequency based on hand strength + price:
  - small price (≤20% req'd equity) + `medium_made` or better → keep call alive
  - moderate price (≤35%) + `strong_made` or better → keep call alive
  - reasonable price (≤45%) + `nuts` / `near_nuts` → strongly prefer continue
- Treat board danger as a *dampener*, not auto-fold.
- Keep all-in handling bet-size-aware:
  - tighten marginal bluff-catchers
  - preserve strong / nut-equity continues when price supports it

**Deliverables:**

- New defense-floor strategy layer between
  `_apply_exploitation` / `_apply_value_override` and the math
  floor (`apply_pot_odds_floor`). Distinct from math floor — math
  floor handles pot-committed and short-stack arithmetic; defense
  floor handles hand-class-aware bluff-catching.
- Tests for cheap, moderate, all-in calls × paired,
  coordinated, safe boards.
- Diagnostics: which decisions fired the floor, what action
  probabilities changed.

**Existing-layer interactions:**

- Phase 7.5 Item 1 `_apply_bluff_catch_override` already creates a
  pot-odds-conditional `{call, fold}` distribution for
  `MEDIUM_MADE` / `WEAK_MADE` hands vs EXTREME-tier aggressors.
  The proposed defense floor extends that pattern to `STRONG_MADE`
  / `NUTS` — same shape, different gate.
- Phase 6.5 `_apply_value_override` already replaces strategy with
  call/raise for `STRONG` vs `hyper_aggressive`. Defense floor
  should respect override's output (don't down-weight after
  override has set the distribution).

### 3. Refine passive opponent profiles

Today's `hyper_passive` pattern fires on every CaseBot decision
(VPIP > 0.60 AND AF < 0.80 trips for any passive opponent).
Phase 8.1a added `_is_passive_with_jams` and
`cbet_attempt_rate`, which together can distinguish:

- **pure_station**: high VPIP, low AF, low jam frequency, low
  cbet_attempt_rate
- **sticky_jammer**: high VPIP, low AF, **meaningful jam
  frequency** (Phase 8.1b's `_is_passive_with_jams` detector)

**Detector with sample-size confidence:**

The naive `vpip + AF + all_in_frequency` triple is the right
*shape*, but each axis needs a denominator check so cold-start
noise can't trip the detector. Recommended form:

```
sticky_jammer if:
    vpip > 0.60
    aggression_factor < 0.80
    AND one of:
      all_in_frequency  > THRESHOLD   with enough hands_observed
      all_in_per_facing_bet > THRESHOLD with enough facing_bet_opportunities
      postflop_jam_open_rate > THRESHOLD with enough postflop_open_opportunities

pure_station if:
    vpip > 0.60
    aggression_factor < 0.80
    AND none of the above jam signals trip with confidence
```

The three jam signals are different lenses on the same underlying
trait. Using `one of` (not all) makes the detector robust when one
denominator is sparse but another is well-populated.

**Note on `cbet_attempt_rate` coverage:**

`cbet_attempt_rate` (Phase 8.1a, shipped today) is useful for
identifying passive opponents who sometimes take the preflop
lead and then decline to continuation-bet. However, it only
accumulates samples when the opponent was the preflop raiser AND
sees a flop.

For near-pure callers with very low PFR (CaseBot has PFR=0.03),
this stat will remain sparse and **should not be used as the
primary station-subtype signal**. In those cases,
`all_in_frequency`, `postflop_jam_open_rate`, and
`all_in_per_facing_bet` remain the better splitters for
distinguishing pure station from sticky-jammer.

Practically:

- Use `cbet_attempt_rate` as a **supplemental** signal for
  passive opponents with enough PFR / c-bet opportunities (e.g.
  passive LAGs who raise preflop but don't fire flop bets).
- Use jam / all-in frequency signals as the **primary** splitter
  for low-PFR passive opponents.

The sticky-jammer detector above works without `cbet_attempt_rate`
because the jam signals are the core trait. `cbet_attempt_rate`
just adds precision for opponents who happen to take the preflop
lead occasionally.

**Strategy differences:**

Versus `pure_station`:
- value bet strong hands more often (Phase 8 `value_vs_station`
  already does this)
- reduce bluff frequency
- allow wider continues at good prices (new defense floor)

Versus `sticky_jammer`:
- value bet strong hands (Phase 8 `value_vs_station`)
- reduce bluff frequency
- **do NOT** expand marginal continues against large bets / jams
  — Phase 8.1b's experiment regressed by doing this
- **DO** preserve continues with strong / nut-equity hands at
  good prices (this is the gap the defense floor fills)

**Deliverables:**

- Behavior-profile detector reading existing tracked stats
  (vpip, aggression_factor, all_in_frequency, cbet_attempt_rate).
- Updated exploitation offsets keyed by profile.
- Tests for detection thresholds + action-shaping differences.

**Existing-layer interactions:**

- Phase 8.1a `cbet_attempt_rate` shipped today (commit
  `cd94a668`) — gives a clean signal for distinguishing passive
  PFRs.
- Phase 8.1b `_is_passive_with_jams` detector shipped earlier
  today but its behavior change was reverted — only the detector
  + `classify_detected_patterns` entry are live. This proposal
  uses the detector but does NOT re-enable the failed fold-mass
  suppression. Instead, the protective behavior comes from the
  defense floor (#2) which is hand-class-gated.

### 4. Bet-size-aware decisions

Bet size currently only appears in pot-odds calculations. Make it
a first-class input.

**Bet buckets:**
```
small:       required_equity ≤ 20%
medium:      20-35%
large:       35-50%
jam/overbet: >50% or all-in
```

Use buckets to guide:
- bluff-catch frequency
- fold suppression
- value override behavior
- defense floors (above)
- passive-opponent adjustments

**Deliverables:**

- Shared `classify_bet_size_bucket(call_amount, pot)` classifier
  in `poker/strategy/` (alongside `hand_classification.py`).
- Tests per bucket × hand class.
- Diagnostics showing bucket per decision.

**Existing-layer interactions:**

- `DecisionContext.bet_size_pot_ratio` already exists from Phase
  7.5 Item 1. The bucket classifier is a thin wrapper that
  consumes it.
- Phase 7.5 bluff-catch override already keys off
  `bet_size_pot_ratio` for its call-prob matrix. The bucket
  classifier should produce the same buckets the bluff-catch
  matrix uses, so analysis and behavior stay in sync.

### 5. Rebalance station exploitation

Make station exploitation explicitly value-heavy and bluff-light.

**Implementation:**

- Increase betting frequency with `strong_made` and `nuts` (Phase
  8 `value_vs_station` does this; review magnitude).
- Reduce air / semi-air bluffing when opponent's fold metrics are
  low (no current rule does this — bluffing rate is set by chart
  + personality, not opponent-aware).
- Avoid increasing call frequency for marginal hands against
  large bets (Phase 8.1b's failed approach — already reverted).
- Preserve pot-odds-driven continues for legitimate made hands
  (defense floor handles this).

**Deliverables:**

- Updated offset rules with the four constraints above.
- Tests for each:
  - strong hands gain value pressure (`bet_*` mass up)
  - air loses bluff pressure (`bet_*` mass down for `air`)
  - marginal hands do NOT gain large-bet call pressure
  - good-price made hands keep call probability

**Existing-layer interactions:**

- `value_vs_station_intensity` already adds +0.3 to bet_* (Phase
  8 v1).
- `hyper_passive_intensity` already adds +0.3 to raise-like, −0.2
  to fold (legacy). The reduction-to-bluffs is the new piece.
- Watch for stacking: if Phase 8 + hyper_passive + new "reduce
  bluffs" rule all push the same direction, total L1 shift can
  exceed clamp. Phase 7.5 three-tier clamp is the safety net but
  each new rule should respect intended magnitude.

### 6. Upgrade diagnostics and review tools

Make future analysis easier. Phase 7.6 intervention_trace already
provides the per-decision framework — extend it.

**Add per-decision fields:**

- raw made hand (current)
- strategic hand class (new — post-downgrade)
- nut status (new)
- danger flags (new)
- required equity (new)
- bet bucket (new)
- opponent profile (new — pure_station / sticky_jammer / etc.)
- active strategy layers (current via intervention_trace)
- final sampled action (current)
- probability distribution before/after each layer (current via
  intervention_trace)

**Deliverables:**

- Extend `InterventionTrace` payload or add a parallel
  `DecisionContext` extension.
- Update `experiments/casebot_breakdown.py`-style report to group
  folds/calls by:
  - hand class (post-downgrade)
  - price bucket
  - board danger
  - opponent profile
  - layer that changed the decision

### 7. Validation suite

Build a repeatable validation matrix that measures decision
quality across common poker situations.

**Scenarios:**
- paired boards
- 4-straight boards
- 4-flush boards
- nut hands on dangerous boards
- top pair at small prices
- marginal bluff-catchers facing large bets
- strong hands versus passive opponents
- air / semi-air vs low-fold opponents
- short-stack and low-SPR spots

**Track:**
- classifier accuracy (vs ground-truth hand strength)
- cheap made-hand overfold rate
- strong / nut-equity overfold rate
- marginal large-bet call rate
- value-bet frequency with strong hands
- bluff frequency into low-fold opponents
- net bb/100 across the existing rule-bot benchmark set

## Implementation order

1. **Board-aware hand classification** — must land first.
   Every other layer reads `hand_class` / `nut_status` /
   `danger_flags`. Without this, diagnostics still conflate
   classifier noise with real leaks.
2. **Bet-size classifier and required-equity diagnostics** —
   foundational input for layers 3-5.
3. **Price-sensitive defense floor** — the highest-impact
   behavior change; addresses Examples 3 and 5.
4. **Passive profile split** — adds `pure_station` vs
   `sticky_jammer` detection.
5. **Station exploitation rebalance** — bluff reduction and
   value emphasis keyed off the profile split.
6. **Expanded diagnostics / reporting** — supports validation
   and ongoing analysis.
7. **Full validation matrix** — confirms each step is net
   positive without regressing the rule-bot benchmark.

## Success criteria

**Tightened bars (each measured against an in-tree control run,
not documented baselines which are stale):**

| Metric | Target |
|---|---|
| TAG vs CaseBot HU, 1000 hands × 5 seeds | bb/100 improves by ≥ 20 vs control, CI doesn't cross zero |
| TAG vs (ABCBot + LAG + Nit + GTO-Lite + Rock) 6-max, 500 × 5 | bb/100 doesn't regress from current −17.2 |
| Folded nuts/strong_made on river | drops from 50/1500 → ≤ 15/1500 after classifier fix + defense floors |
| Showdown win rate | stays ≥ 65% (down from 73% acceptable — trading some over-folds for fewer marginal call-offs) |
| Classifier accuracy on ground-truth set | ≥ 95% correct labels on a held-out 100-hand validation set |

**Soft criteria (qualitative, helpful but not blocking):**
- Diagnostic "fold nuts/strong" separates classifier errors from
  real folds clearly.
- No increase in marginal call-offs vs passive jams.
- No regression vs ABCBot, CallStation, GTO-Lite, ManiacBot, and
  mixed 6-max tables.

## Validation framework — apples-to-apples controls

Lessons from Phase 8.1b: **always validate with the rule
disabled vs enabled in identical conditions.** Documented
baselines drift; in-tree controls do not.

`experiments/phase_8_diagnostics.py` and
`experiments/casebot_breakdown.py` are the validation harness.
Each new rule should ship with:

1. A `--disable-<rule>` flag that monkey-patches the rule's gate
   to never fire.
2. Comparison sims at multiple sample sizes (e.g., 200 / 500 /
   1000 hands × 3-5 seeds).
3. The minimum sample size needed for the rule's claimed delta to
   exceed the variance band.

## What's measured vs. projected

**Measured (data supports these claims):**

- The −96.8 bb/100 fold-driven leak in TAG vs CaseBot HU.
- The classifier labels "nuts" for non-nut straights and pair on
  paired boards (Examples 1, 4).
- The leak is concentrated on the river and at large bet sizes.
- 73% showdown win rate when TAG reaches showdown.
- The Phase 8.1b global fold-mass suppression regressed bb/100
  when active.

**Projected (plausible but unmeasured until each phase lands):**

- The proposed classifier downgrades will recover ~half of the
  "fold nuts" diagnostic to "correct fold of mislabeled hand."
- The price-sensitive defense floor will recover the other half
  by keeping legitimate strong-hand continues alive.
- The pure_station / sticky_jammer split will reduce bluff
  frequency vs CaseBot-like opponents without spreading
  marginals into jams.
- Bet-bucket awareness will improve consistency across
  bluff-catch / defense-floor / station-exploit layers.

**Risks:**

- Each new rule adds offset magnitude. Even with the Phase 7.5
  three-tier clamp, stacking effects could push behavior outside
  the intended envelope. Per-rule offset budgets need explicit
  enforcement.
- "Don't fold marginals to jams" and "do call strong hands at
  reasonable prices" are similar in shape but opposite in
  intent. Conflating them was the Phase 8.1b mistake; hand-class
  gating is what keeps them distinct.
- The classifier downgrades affect existing rules (`value_override`,
  `bluff_catch`, `value_vs_station`) — those rules' trigger rates
  will change, which is a behavior shift independent of the new
  rules. Apples-to-apples controls for the new rules need to be
  measured *after* the classifier fix lands, not before.

## Bottom line

Make TieredBot more **board-aware**, more **pot-odds-aware**, and
more **opponent-profile-aware** — in that priority order. Don't
make it more aggressive globally. Specifically:

- Value bet stations more.
- Bluff stations less.
- Fold marginal hands to big passive aggression.
- Call strong hands when the price is right.
- Stop trusting coarse hand labels on dangerous boards.

The benchmark target is the existing rule-bot suite, validated
with in-tree controls. The work breaks into 7 sequenced phases
with clear success criteria per phase.
