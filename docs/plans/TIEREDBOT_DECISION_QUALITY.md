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

**Implied real-leak floor.** If the 2/5 rate generalizes, the
recoverable real-leak portion of the "fold nuts/strong_made"
contribution is roughly **40% × −79.5 ≈ −32 bb/100**, not the
full −79.5. The remaining ~60% is classifier noise that goes
away once §1 lands (the "fold" is correct; the *label* was
wrong). The 5-example sample is small, so this number is a
working estimate, not a hard floor — Phase 1 of the validation
suite (§7) should re-derive it on a larger labeled set after
the classifier downgrades ship.

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

### 1.5. Unified opponent-archetype classifier

Today's exploitation logic detects opponent traits **piecewise**:
multiple independent `_is_<pattern>` functions in
`poker/strategy/exploitation.py` (`_is_hyper_aggressive`,
`_is_hyper_passive`, `_is_passive_with_jams`, `_is_tight_nit`,
`_is_high_fold_to_cbet`) each consume aggregate stats. An
opponent can match multiple patterns simultaneously
(`['hyper_passive', 'passive_with_jams']`), and each strategy rule
independently calls the detectors it cares about.

Separately, `OpponentTendencies.get_play_style_label()` produces a
4-quadrant label (`tight-aggressive` / `loose-aggressive` /
`tight-passive` / `loose-passive` / `unknown`), but **no strategy
rule reads it** — it's used only for human-readable AI prompts,
and its thresholds aren't aligned with the exploitation detectors.

This creates three problems:

1. **Threshold drift**: each detector defines its own constants.
   When a tuning change is needed, it touches multiple files. No
   single source of truth for "what counts as a station."
2. **Stacking surprises**: rules that gate on independent
   patterns can compound (Phase 8 `value_vs_station` and legacy
   `hyper_passive` both fire on CaseBot, both push `bet_*`
   positive). Phase 7.5's three-tier clamp catches gross
   over-shifts but per-rule budgets are implicit, not enforced.
3. **Diagnostic noise**: counters surface `detected_hyper_passive`
   and `detected_passive_with_jams` separately. Operator has to
   join them mentally to ask "what archetype was this opponent?"

**The proposal:** introduce a single
`classify_opponent_archetype(stats) -> str | None` that returns
one label per opponent. Strategy rules gate off the single
label; the existing `_is_<pattern>` detectors stay as
implementation details (the unified classifier composes them).

**Phased delivery.** To keep the migration auditable, split this
into two sub-phases:

- **§1.5a — Behavior-faithful refactor (ships first).** The
  classifier returns only the labels existing strategy rules
  already gate on: `hyper_aggressive`, `pure_station`,
  `sticky_jammer`, `None`. Every consumer (`value_override`,
  `value_vs_station`, §3 application, §5 rebalance) migrates to
  read the unified label. Validation target: zero behavior delta
  on apples-to-apples sims. This is a refactor, measured as one.
- **§1.5b — Extended taxonomy (ships only when a consumer needs
  it).** The additional labels (`maniac`, `lag`, `tag`, `nit`,
  `rock`, `balanced`) are added incrementally, each gated by an
  actual rule that consumes them. Until then they would be dead
  code in a strategy classifier, which accumulates calibration
  debt. The taxonomy below documents the *target* shape, not
  what ships in §1.5a.

```
classify_opponent_archetype(stats) -> Optional[str]
    # Returns one of:
    #   'maniac'         — hyper_aggressive with sticky calling
    #   'lag'            — loose, aggressive, balanced bluff/value mix
    #   'tag'            — tight, aggressive, solver-style
    #   'nit'            — very tight, low aggression
    #   'rock'           — tight-passive (lower VPIP than station)
    #   'pure_station'   — loose, passive, low jam frequency
    #   'sticky_jammer'  — loose, passive, meaningful jam frequency
    #   'balanced'       — none of the above match (GTO-style)
    #   None             — cold-start, insufficient sample
```

**Detector composition — §1.5a (ships first, behavior-faithful):**

```
if hands_observed < MIN_HANDS_FOR_LABEL:    return None
if _is_hyper_aggressive(stats):              return 'hyper_aggressive'
if _is_passive_with_jams(stats):             return 'sticky_jammer'
if _is_hyper_passive(stats):                 return 'pure_station'
return None  # all other opponents — strategy rules no-op
```

This is the only form that ships in §1.5a. Returning `None` for
unmatched opponents (rather than `'balanced'`) keeps existing
rules that gate on the legacy detectors no-ops in the same
spots they're no-ops today.

**Detector composition — §1.5b target shape (deferred):**

```
if hands_observed < MIN_HANDS_FOR_LABEL:        return None
if _is_hyper_aggressive(stats):
    if vpip > VPIP_LOOSE: return 'maniac'
    else: return 'lag'  # maniac with low VPIP shouldn't happen but treat as LAG
if _is_passive_with_jams(stats):                 return 'sticky_jammer'
if _is_hyper_passive(stats):                     return 'pure_station'
if _is_tight_nit(stats):
    if aggression_factor < 1.0: return 'nit'
    else: return 'rock'  # tight + non-passive but not aggressive enough
                          # for TAG could be 'rock' or 'tag' — calibrate
if vpip < VPIP_TAG and aggression_factor > AF_TAG:
    return 'tag'
if vpip > VPIP_LAG_FLOOR and aggression_factor > AF_LAG:
    return 'lag'
return 'balanced'
```

This is the target shape, **not what ships in §1.5a**. Each new
label here lands incrementally as a consumer rule needs it.

Exact thresholds are a tuning question (and need to align with
the existing `HYPER_PASSIVE_VPIP_THRESHOLD`,
`TIGHT_NIT_VPIP_THRESHOLD`, etc.). Key design points:

- **Single source of truth.** Threshold changes happen in one
  file — `poker/strategy/exploitation.py`, which already owns
  `HYPER_PASSIVE_VPIP_THRESHOLD`, `TIGHT_NIT_VPIP_THRESHOLD`,
  and the other detector constants. The unified classifier reads
  these constants directly rather than defining its own, so the
  existing `_is_<pattern>` detectors and the new
  `classify_opponent_archetype` cannot drift.
- **Single label per opponent.** Removes the "multiple patterns
  fire simultaneously" surprise.
- **Sample-size gating built in.** Returns `None` (not a default
  label) when sample is below `MIN_HANDS_FOR_LABEL` — strategy
  rules can no-op on `None` instead of accidentally exploiting
  cold-start noise. Starting value: reuse the existing
  `MIN_HANDS_DEFAULT = 15` cold-start floor in
  `poker/strategy/exploitation.py` rather than introducing a new
  constant. Calibrate against the validation suite (§7) before
  locking in — archetype labels may want a higher bar than the
  per-detector gate.
- **Each axis has a denominator check.** Pattern detectors that
  use opportunity-normalized stats (`all_in_per_facing_bet`,
  `cbet_attempt_rate`, `postflop_jam_open_rate`) check their own
  per-axis sample. See §3 for the
  one-of-three-jam-signals-with-confidence pattern.

**`cbet_attempt_rate` coverage caveat** (repeated here because
the classifier is where it most matters): a near-pure caller
like CaseBot has PFR=0.03, so `cbet_attempt_rate` won't
accumulate samples. The classifier's `sticky_jammer` path uses
jam-frequency signals (which DO accumulate against pure callers),
with `cbet_attempt_rate` as supplemental refinement for passive
PFRs only. See §3 for the detector form.

**Deliverables:**

- `classify_opponent_archetype` in
  `poker/strategy/exploitation.py` (alongside the existing
  detectors).
- Migration: existing rules that today call
  `_is_<pattern>(stats)` switch to reading the unified
  archetype. The piecewise detectors stay as
  building blocks (they're still useful for the classifier's
  internals and for backwards-compat diagnostics).
- Per-archetype diagnostic counters:
  `archetype_classified_<label>_<hero_archetype>` so operators
  can see "across this run, hero saw X% pure_station / Y%
  sticky_jammer / Z% hyper_aggressive / W% unmatched (None)."
  Additional labels (`balanced`, `tag`, etc.) light up when
  §1.5b ships them.
- Tests:
  - threshold boundary tests per archetype label
  - sample-size gate (returns `None` below threshold)
  - mutual exclusion (no opponent matches two archetypes
    simultaneously by construction)
  - faithfulness: existing `_is_<pattern>` detectors still
    return the same booleans they always did (the classifier
    composes them but doesn't replace them).

**Existing-layer interactions:**

- **§3 (passive profile split)** consumes the classifier directly.
  Strategy adjustments key off `archetype == 'pure_station'` vs
  `archetype == 'sticky_jammer'` instead of independently calling
  `_is_passive_with_jams`.
- **§5 (station exploitation rebalance)** keys off the unified
  classifier for the value-bet / reduce-bluff direction.
- **Phase 6.5 `value_override`** currently fires on
  `'hyper_aggressive' in classify_detected_patterns(stats)`. After
  §1.5a lands, it simplifies to
  `classify_opponent_archetype(stats) == 'hyper_aggressive'`.
  The behavior change should be zero — the new classifier composes
  the same `_is_hyper_aggressive` detector. If §1.5b later splits
  `hyper_aggressive` into `'maniac'` and `'lag'`, this gate
  expands to `in {'hyper_aggressive', 'maniac', 'lag'}` at that
  point (not before).
- **Phase 7.5 `bluff_catch_override`** currently consumes the
  three-tier clamp signal (`_determine_clamp`) which keys off
  `AF_postflop`, `all_in_per_facing_bet`, `postflop_jam_open_rate`.
  That stays independent — the clamp is about confidence in
  extreme-tier classification, not about archetype labeling. The
  two systems coexist.
- **Phase 8 `value_vs_station`** currently uses the spot-driven
  `compute_value_vs_station_intensity` which already operates on
  per-opponent station detection. Migration deferred — see the
  "precedence limitation" note below. Stays on the direct
  `_is_hyper_passive` check until §1.5b's richer taxonomy ships.

**Precedence limitation (discovered during §1.5a implementation):**

`_is_hyper_aggressive` and `_is_hyper_passive` are *not*
globally disjoint — an opponent with high VPIP, low AF, AND
`all_in_frequency > 0.30` (a passive-but-jammy caller) satisfies
both detectors. The unified classifier's single-label precedence
returns `'hyper_aggressive'` first and *hides* the
`_is_hyper_passive` signal for these compound cases. Legacy
callers reading the detectors directly still see both signals.

Consequence: only callers whose semantics align with the
hyper_aggressive-first precedence can be migrated faithfully in
§1.5a. That's `value_override._should_apply_value_override` (the
hyper_aggressive branch fires first in both old and new paths).
The `value_vs_station` station filter and
`detect_passive_with_jams_in_field` cannot — migrating them
loses the compound-case classification and changes behavior.
Those sites stay on direct detector calls until §1.5b introduces
a label that captures the compound case (likely something
like `'maniac_station'` or by splitting `_is_hyper_aggressive`'s
disjunction into separate detectors).

**Risks:**

- Migrating existing rules to the unified classifier is a
  behavior-change opportunity even when the intent is to preserve
  behavior. Each migration step needs an apples-to-apples sim
  comparison to confirm zero regression.
- Calibration: the proposed archetype labels need ground-truth
  validation. Running the classifier across known opponents (TAG
  / LAG / Nit / Rock / GTO-Lite / CaseBot / ManiacBot / Calling
  Station / Maniac archetype) and confirming each gets its own
  intended label is the minimum bar.

### 2. Price-sensitive defense floors

Hero pure-folds reasonable made hands at favorable pot odds
(Examples 3 and 5). There's no rule that says "you can't fold
strong hands when the price is cheap."

**Implementation:**

- Calculate required equity per decision:
  ```
  required_equity = call_amount / (pot + call_amount)
  ```
- Floor matrix gates on the **joint `(hand_class, nut_status)`
  pair** from §1, not the raw made hand. §1 defines two
  orthogonal dimensions: `hand_class ∈ {nuts, strong_made,
  medium_made, weak_made, air}` and `nut_status ∈ {actual_nuts,
  near_nuts, non_nut_strong, bluff_catcher}`. The floor reads
  both. After §1 lands, Example 1's 10-high straight becomes
  `(strong_made, non_nut_strong)` (or `(medium_made,
  non_nut_strong)` on more dangerous coordination), and top pair
  on 4-Broadway becomes `(medium_made, bluff_catcher)`. The
  matrix:

  | Price (req. equity) | Gate: `(hand_class, nut_status)` | Floor behavior |
  |---|---|---|
  | any   | `hand_class == air` | no floor (explicit exit) |
  | any   | `nut_status == bluff_catcher` (incl. paired-board pair, top pair on 4-liner) | no floor; defer to bluff-catch override §7.5 Item 1 (explicit exit) |
  | ≤ 45% | `nut_status ∈ {near_nuts, actual_nuts}` | strongly prefer continue |
  | ≤ 35% | `hand_class ∈ {strong_made, nuts}` OR `nut_status == non_nut_strong` | keep call alive |
  | ≤ 20% | `hand_class ∈ {medium_made, strong_made, nuts}` | keep call alive |

  Rows are evaluated top-down at decision time; the first
  matching row wins. The `air` and `bluff_catcher` exits come
  **first** so §7.5's bluff-catch override stays authoritative
  for marginal hands — without that ordering, a
  `(strong_made, bluff_catcher)` would incorrectly match the
  35% row's OR clause and trigger a floor.

- Treat board danger as a *dampener*, not auto-fold. Danger
  flags from §1 (`paired_board`, `four_straight_board`, etc.)
  reduce the floor magnitude by a fixed factor rather than
  zeroing it.
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

### 3. Refine passive opponent profiles (application)

§1.5 introduces the unified classifier that separates
`pure_station` from `sticky_jammer`. This section is about how
strategy *behavior* changes based on that label. The detector
itself lives in §1.5 — don't re-implement detection here.

**Scope.** §3 covers only the passive archetypes
(`pure_station`, `sticky_jammer`). Behavior vs
`hyper_aggressive` opponents stays out of §3 scope and is
handled by Phase 6.5 `value_override` plus the Phase 7.5
bluff-catch override / three-tier clamp, which already cover
that case.

**Behavior deltas, by archetype:**

| Behavior | `pure_station` | `sticky_jammer` |
|---|---|---|
| Value bet strong hands | ↑ (Phase 8 `value_vs_station`) | ↑ (Phase 8 `value_vs_station`) |
| Bluff frequency | ↓ (§5) | ↓ (§5) |
| Marginal continues vs large bets / jams | allow wider at good prices (§2 floor) | **no change** — Phase 8.1b regressed when this expanded |
| Strong / nut-equity continues at good prices | preserve (§2 floor) | preserve (§2 floor) — this is the gap the floor fills |

**Deliverables:**

- ~~Updated exploitation offsets keyed off
  `classify_opponent_archetype(stats)`.~~ **No new strategy code
  required.** Per the audit done during implementation, every
  in-scope cell of the behavior table is already satisfied by
  shipped rules: row 1 (value bet ↑) by Phase 8
  `value_vs_station` which gates on `_is_hyper_passive` (True
  for both passive archetypes); row 2 (bluff freq ↓) is §5's
  work, deferred; row 3 (marginal at large/jam — no widening)
  is enforced by §2's matrix structure (rows 3-4 require
  `near_nuts`/strong+/`non_nut_strong`; row 5 requires ≤20%
  req) — §2 simply doesn't have a row that fires for marginals
  at large prices; row 4 (strong/nut continues at good prices)
  by §2 rows 3-4. The "updated offsets" deliverable was
  predicated on §1.5a migrating `value_vs_station`'s gate to
  the unified classifier, which was deferred per the §1.5
  precedence limitation. The migration is parked for §1.5b.
- Tests for the behavior table per archetype —
  `tests/test_strategy/test_section_3_passive_archetype_behavior.py`
  exercises each row of the table with canonical
  `pure_station` and `sticky_jammer` fixtures and verifies the
  rules cover the right cells. Includes a regression guard
  against Phase 8.1b: medium hands at large bets and
  bluff_catcher-routed hands on dangerous boards do NOT widen.

**Existing-layer interactions:**

- Phase 8.1b `_is_passive_with_jams` detector is the basis for
  the `sticky_jammer` archetype in §1.5. Its behavior change
  (fold-mass suppression) was reverted; the detector remains as
  an input to the unified classifier.
- The protective behavior for marginal hands vs jams comes from
  the defense floor (§2), which is hand-class-gated. It does
  NOT re-enable the failed fold-mass suppression.
- `value_vs_station`'s station detection stays on the direct
  `_is_hyper_passive` detector rather than the unified
  classifier label, because the §1.5a precedence finding showed
  the classifier's single-label output drops the `_is_hyper_passive`
  signal for opponents that *also* satisfy `_is_hyper_aggressive`
  via the `all_in_frequency > 0.30` disjunction. Migration is
  parked for §1.5b's richer taxonomy.

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

### 5.5. Per-rule offset budgets

Stacking is a known risk: `value_vs_station` (+0.3 bet),
`hyper_passive_intensity` (+0.3 raise, −0.2 fold), and the new
§5 "reduce bluffs vs stations" rule can all push the same
direction on the same decision. The Phase 7.5 three-tier clamp
is the safety net, but it's a *gross-shift* check at the
distribution level; it doesn't enforce per-rule envelopes.

**Implementation:**

- Each strategy rule that mutates the action distribution
  declares an offset budget (e.g.,
  `MAX_L1_SHIFT_value_vs_station = 0.30`,
  `MAX_L1_SHIFT_reduce_bluffs = 0.15`).
- Apply rules sequentially through a small accumulator that
  tracks per-rule shift magnitude. If a rule's effective shift
  (after clamping by remaining headroom) is reduced, log it as
  a diagnostic counter `budget_clamped_<rule_name>`.
- Total budget across all opponent-aware rules ≤ the three-tier
  clamp's envelope **for the current decision's clamp tier**
  (not a static global ceiling). Phase 7.5's clamp varies by
  confidence/tier/spot, so §5.5's accumulator queries the active
  envelope at decision time and reserves headroom against
  *that* number. If a decision lands in a tighter tier, the
  per-rule envelopes proportionally shrink; rules that already
  applied earlier in the pipeline don't get retroactively
  budget-clamped, but later rules absorb the tighter envelope.
  The clamp remains the outermost safety net but rarely fires.

**Deliverables:**

- Per-rule constants alongside each rule's gate.
- Sequential application order spec'd in
  `tiered_bot_controller.py` (or wherever the offset pipeline
  lives) — order matters for headroom accounting.
- Diagnostic counters per rule: how often it was budget-clamped
  vs applied at full intent.
- Tests: stacking scenario where 3 rules want to push the same
  direction; assert total shift ≤ envelope and each rule's
  individual budget held.

**Existing-layer interactions:**

- The three-tier clamp (`_determine_clamp`) stays as-is and acts
  as the final wrapper. Per-rule budgets are a tighter inner
  bound.
- Phase 8.1b's regression was partly stacking-shaped (multiple
  rules suppressing folds in the same spot). Explicit budgets
  give us a primitive to prevent that class of bug from
  recurring without having to add ad-hoc "rule X disables rule
  Y" conditionals.

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
- opponent archetype (new — the §1.5
  `classify_opponent_archetype` label, including `None` for
  cold-start)
- active strategy layers (current via intervention_trace)
- final sampled action (current)
- probability distribution before/after each layer (current via
  intervention_trace)

**Deliverables:**

- ~~Extend `InterventionTrace` payload~~ — the
  per-decision fields are already snapshotted on the
  controller's `_last_pipeline_snapshot` dict piecewise across
  §1 (`hand_strength`, `nut_status`, `danger_flags`), §4
  (`bet_bucket`, `required_equity`), and §6
  (`opponent_archetype` — added inside
  `_tally_exploitation_event`). The `InterventionTrace`
  extension is deferred until a consumer needs traces to carry
  the decision context (the casebot_breakdown report reads
  directly from the snapshot for now). Future work: promote the
  snapshot to a per-decision Trace payload if cross-replay
  analytics need it.
- ✅ Multi-axis fold breakdown in
  `experiments/casebot_breakdown.py`:
  - `print_multi_axis_breakdown` groups postflop folds by
    `(phase, hand_class, nut_status, bet_bucket)` so the
    "where are folds concentrated" question gets a one-table
    answer (immediately surfaces e.g. that bluff_catcher hands
    dominate fold counts).
  - `print_archetype_breakdown` groups by opponent archetype
    (`pure_station` / `sticky_jammer` / `hyper_aggressive` /
    `unmatched` / `cold_start`) — answers "do we fold more vs
    sticky_jammer than pure_station?".
  - The per-example captured-hand printout now includes
    `nut_status`, `bet_bucket`, `required_equity`,
    `opponent_archetype`, and `danger_flags` so individual
    fold inspection has the full context.
- Layer-attribution grouping (which layer changed the
  decision) is **deferred** — `_last_intervention_trace`
  already provides this per decision, and the existing
  `casebot_breakdown` captured-hand action-sequence dump
  shows the final action chain; a dedicated "fold-by-layer"
  aggregation is future work when the existing reports prove
  insufficient.

### 7. Validation suite

Validation is shipped piecewise alongside §1-§6: each scenario
listed in the original plan ended up exercised by per-section
unit tests (deterministic, no sim variance) and the
`casebot_breakdown` multi-axis report (sim-level aggregation,
variance-bounded). A dedicated cross-scenario validation
framework was scoped and consciously deferred — the existing
coverage is sufficient for the §1-§6 ship and the marginal
cost of a new framework didn't justify the effort.

**Scenario coverage (audit done during §7 implementation):**

| Scenario | Test file(s) |
|---|---|
| Paired boards | `test_hand_classification.py` (TestDangerFlags, TestMadeTierDowngrades) |
| 4-straight boards | `test_hand_classification.py` (TestNutStatus, TestMadeTierDowngrades) |
| 4-flush boards | `test_hand_classification.py` (TestDangerFlags) |
| Nut hands on dangerous boards | `test_defense_floor.py` + `test_section_3_passive_archetype_behavior.py` |
| Top pair at small prices | `test_defense_floor.py::TestPlanExamples::test_example_5_top_pair_at_16_pct_pot_odds` (documents the known §1/§2 routing gap) |
| Marginal bluff-catchers vs large bets | `test_section_3_passive_archetype_behavior.py::TestDefenseFloorDoesNotWidenMarginalsAtLargeBets` |
| Strong hands vs passive opponents | `test_section_3_passive_archetype_behavior.py::TestDefenseFloorFiresForStrongHandsRegardlessOfArchetype` + `TestValueVsStationFiresForBothPassiveArchetypes` |
| Air / semi-air vs low-fold opponents | `test_bluff_reduction.py` |
| Short-stack / low-SPR spots | `test_short_stack.py` (pre-existing Phase 6 Step B) |

**Tracked metrics — where each is captured:**

| Metric | Captured via |
|---|---|
| Classifier accuracy (vs ground truth) | `test_hand_classification.py` — fixture-level asserts on `(hand_class, nut_status, danger_flags)` for canonical hand×board cases |
| Cheap made-hand overfold rate | `experiments/casebot_breakdown.py` multi-axis report — filterable by `(hand_class, bet_bucket)` |
| Strong / nut-equity overfold rate | Same multi-axis report |
| Marginal large-bet call rate | Same — `(medium_made, large/jam bucket)` rows |
| Net bb/100 vs rule-bot suite | `casebot_breakdown` aggregate report |
| Value-bet frequency with strong hands | **Soft gap** — visible in per-decision traces but not aggregated across runs |
| Bluff frequency into low-fold opponents | **Soft gap** — `bluff_reduction` unit tests verify offset direction; sim-level aggregate isn't reported |

**Soft gaps (deferred as follow-up work):**

The two metrics flagged above (sim-level value-bet / bluff
frequency aggregates) are not currently reported by
`casebot_breakdown`. Adding them would require new per-decision
aggregation in the sim harness. The unit tests already verify
each rule's *direction* (e.g., `bluff_reduction` reduces bet_*
mass; `value_vs_station` increases it); the gap is just the
aggregated cross-run report. Adding these aggregates is a
self-contained 1-2 hour follow-up.

A scenario-replay framework that captures snapshots from live
sims and replays them as fixtures was scoped during §7
implementation (the `replay_strategy_pipeline` infrastructure
in `poker/strategy/replay.py` makes this tractable) but
deferred — the per-section unit tests + casebot_breakdown
multi-axis report cover the immediate validation needs.
Captured-fixture replay becomes valuable when §1.5b or
post-ship tuning needs cross-iteration regression checking.

## Implementation order

1. **Board-aware hand classification** — must land first.
   Every other layer reads `hand_class` / `nut_status` /
   `danger_flags`. Without this, diagnostics still conflate
   classifier noise with real leaks.
1.5a. **Behavior-faithful archetype classifier** — composes
   existing `_is_<pattern>` detectors into a single
   `classify_opponent_archetype` that returns only the labels
   existing rules consume (`hyper_aggressive`, `pure_station`,
   `sticky_jammer`, `None`). Migrates `value_override` to gate
   off the unified label; `value_vs_station` and
   `detect_passive_with_jams_in_field` stay on direct detectors
   due to the precedence limitation (see §1.5 above).
   Validation: unit-level grid invariance test proves the
   `value_override` migration is behavior-faithful across the
   stats input space. Sim-level validation deferred because
   variance dominates at feasible sample sizes.
2. **Bet-size classifier and required-equity diagnostics** —
   foundational input for layers 3-5.
3. **Price-sensitive defense floor** — the highest-impact
   behavior change; addresses Examples 3 and 5. Floor matrix
   keyed on post-§1 labels.
4. **Passive profile split application** — uses §1.5a's labels
   to shape strategy per `pure_station` vs `sticky_jammer`.
   Shipped as audit + regression tests only; no new strategy
   code (§2 + Phase 8 already cover the in-scope table cells).
5. **Station exploitation rebalance** — bluff reduction and
   value emphasis keyed off the unified archetype.
5.5. **Per-rule offset budgets** — lands alongside or
   immediately after §5, before stacking effects compound.
   Cannot ship after §5 without first auditing the existing
   stacked rules.
6. **Expanded diagnostics / reporting** — supports validation
   and ongoing analysis.
7. **Full validation matrix** — shipped as a coverage matrix
   referencing per-section unit tests + `casebot_breakdown`'s
   multi-axis report. Dedicated cross-scenario framework
   deferred; sim-aggregated value-bet / bluff frequency
   metrics flagged as a 1-2 hour follow-up.

**Deferred (not on the critical path):**

- **§1.5b — Extended archetype taxonomy.** `maniac`, `lag`,
  `tag`, `nit`, `rock`, `balanced` labels added incrementally,
  each gated by a consumer rule that actually needs them. Until
  a consumer exists, the labels are dead code in a strategy
  classifier and accumulate calibration debt.

## Success criteria

**Tightened bars (each measured against an in-tree control run,
not documented baselines which are stale):**

| Metric | Target |
|---|---|
| TAG vs CaseBot HU, 1000 hands × 5 seeds | bb/100 improves by ≥ 20 vs control; 95% CI on the **paired delta** (treatment − control, per-seed pairing) doesn't cross zero |
| TAG vs (ABCBot + LAG + Nit + GTO-Lite + Rock) 6-max, 500 × 5 | bb/100 doesn't regress from current −17.2 (see note below) |
| Folded nuts/strong_made on river, **using post-§1 labels** | drops from 50/1500 → ≤ 15/1500 after classifier fix + defense floors |
| Showdown win rate | stays ≥ 65% (down from 73% acceptable — trading some over-folds for fewer marginal call-offs) |
| Classifier accuracy on ground-truth set | ≥ 95% correct labels on a held-out 100-hand validation set (see caveat below) |

**Notes on the bars:**

- **River fold target uses post-§1 labels.** The 50/1500
  baseline counts folds against the *current* (noisy)
  classifier. With ~60% of those folds estimated to be
  classifier mislabels (per the honest-read math), the
  post-§1 baseline will already be closer to ~20/1500 before
  any defense floors land. The ≤15 target is therefore a
  modest gain on top of the re-classification, not on top of
  the legacy count. Don't move the goalposts by counting both
  effects in the same row.
- **6-max scope.** HU is prioritized because (a) the −96.8
  bb/100 deficit there is the largest concentrated leak, and
  (b) the failure mode (postflop folding to a wide-jam
  opponent) is structurally clean to attribute. 6-max
  improvements are out of scope for this work; "doesn't
  regress" is the bar, not the ceiling. A follow-up plan
  should set positive 6-max improvement targets once the HU
  fixes ship.
- **Classifier validation set.** Ground truth for *strategic*
  hand strength is partly subjective (top pair on 4-Broadway
  can reasonably be `medium_made` or `weak_made` depending on
  philosophy). The 95% bar may need recalibration once the
  validation set is built and reviewed. If reasonable
  reviewers disagree on > 5% of labels, the bar should be
  expressed as "≥ 95% agreement with the modal label" rather
  than a hard accuracy number.

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

- Each new rule adds offset magnitude. The §5.5 per-rule offset
  budgets are the proposed mitigation; without them, the Phase
  7.5 three-tier clamp is the only safety net and it operates
  on the combined shift, not per-rule.
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
