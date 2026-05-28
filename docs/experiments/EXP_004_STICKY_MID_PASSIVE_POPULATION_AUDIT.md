---
purpose: Audit whether the LLM personality pool contains a hidden sticky-mid-passive station class the current exploitation layer is blind to, using FishBot leaks as synthetic controls
type: experiment
status: complete
hypothesis_summary: ≥10% of the LLM pool lives in the sticky-mid-passive zone, the current detector misses ≥80% of them, and exploit-ON CRN gains ≥+10 bb/100 vs that subpopulation
result_summary: H1 FALSE — 0/26 personalities in the sticky-mid-passive zone (field is sticky-AGGRESSIVE, not passive); NO-GO on the detector. Bonus: global-AF threshold misses per-street maniacs.
created: 2026-05-28
last_updated: 2026-05-28
---

# Experiment 004 — Sticky Mid Passive Population Audit

> **Why this exists:** The exploitation layer's two working rules
> (`hyper_passive`, `value_vs_station`) trigger on caricature-tuned
> thresholds (VPIP > 0.70, AF < 0.80) that were calibrated against
> CallStation-class rule-bots. `docs/plans/TIEREDBOT_ARCHETYPE_EXPLOITATION.md`
> argues there's a hidden mid-range station class (the "Jeff profile":
> WtSD ≥ 0.55, postflop AF < 0.6, balanced global VPIP) that the current
> detector is blind to. The original plan was to validate that claim
> against the real human player Jeff, but production data is contaminated
> by mixed test/real play, so Jeff_clone is unreliable as ground truth.
>
> This EXP substitutes two clean populations: (a) the LLM personality
> pool (the bot's actual sim opponents) for "does this population exist
> in our field," and (b) the FishBot leaks
> (`sticky_then_pops` / `spews_bluffs` / `bets_strong_transparently`,
> shipped on `840059de`, merged to `development` as `9bda7ab0`) as
> synthetic positive/negative controls for "does the proposed detector
> fire when it should and not when it shouldn't."
>
> **Decision this EXP gates:** go/no-go on building the
> `sticky_mid_passive` detector + wiring WtSD + per-street AF into the
> live `OpponentModelManager`. If the population doesn't exist or the
> existing 2 rules already catch Sticky FishBot, the archetype doc
> closes without further investment.

## Hypothesis

**H1 (primary):** There is a hidden sticky-mid-passive population in
the LLM personality pool that the current exploitation layer is blind
to AND that the proposed detector would profitably exploit. All three
sub-claims must hold:

- **1a (prevalence):** ≥10% of the profiled LLM personalities fall in
  the sticky-mid zone, defined as `WtSD ≥ 0.55 ∧ postflop_AF < 0.6 ∧
  global VPIP between 0.25–0.50`.
- **1b (current-layer blindness):** `classify_opponent_archetype()`
  returns `None` for ≥80% of the personalities in 1a. (I.e. the
  existing detectors don't already cover them.)
- **1c (exploitable):** A CRN bb/100 A/B on `exploit_bb100.py`
  (exploit-ON vs exploit-OFF, hypothetical detector routing
  sticky-mid into `value_vs_station` + `bluff_reduction`) gains
  ≥ +10 bb/100 against the sticky-mid subpopulation, with the lower
  bound of the 95% CI > 0.

**H2 (FishBot mechanics validation):** The proposed
`sticky_mid_passive` detector behaves correctly on the synthetic
controls:

- Fires on `sticky_then_pops` (positive control — should be flagged).
- Does NOT fire on `spews_bluffs` or `bets_strong_transparently`
  (negative controls — these are aggressive / size-tell leaks, not
  sticky ones).
- Does NOT fire on baseline `Fish` (which already triggers
  `_is_hyper_passive` via VPIP > 0.70 — would-be redundant trigger
  flags an over-broad detector).

**H3 (signal computable from sim data):** WtSD and per-street AF can
be mined from sim hand logs at affordable sample sizes (~500 hands/
personality) with cross-seed stdev small enough that the detector's
triggers are stable. Quantitative: per-personality WtSD stdev across
3 seeds < 0.05.

**Falsifier:** Any one of the following kills H1 and closes the
archetype doc:

- **1a fails (<10% prevalence):** The pool is mostly balanced or
  already-covered stations. No subpopulation to attack. → Don't build.
- **1b fails (existing rules already catch ≥20% of sticky-mid
  personalities):** Re-thresholding the current detectors covers most
  of the gap. → Re-threshold, don't build new detection.
- **1c fails (CRN gain < +10 bb/100, or CI crosses zero):** Even with
  perfect detection, the rule offsets don't convert to bb/100.
  → The leak isn't actionable; don't ship the detector.
- **H2 fails on positive control (detector doesn't fire on Sticky
  FishBot):** Trigger thresholds are wrong. → Calibration loop, not
  ship.
- **H2 fails on negative control (detector misfires on Spew or
  Transparent):** Trigger is over-broad. → Calibration loop, not ship.
- **H3 fails (WtSD/per-street AF too noisy at 500 hands):** Signal
  prerequisite is infeasible at the sample sizes we can sustain in
  production. → Don't build; the live wiring would also be noisy.

## What we're testing

<the SINGLE variable change being made. NOTE: this EXP is
measurement-first, not a code-change A/B. The "change" is "compute
new signals (WtSD, per-street AF) over an existing population and
ask whether a hypothetical detector would route them productively."
No production code changes; pure observation + optional offline
detector simulation. Reference baseline: current exploitation layer
state on `development` post-merge.>

## Setup

**Sandbox:**

- **Branch setup:** Merge `development` into the current `lookup-tables`
  worktree so FishBots (`840059de`) + the merged exploitation layer
  state are both available locally. Run the EXP from this worktree
  post-merge. **Conflict handling:** unknown until attempted; per
  [[reference_pre_lint_branch_merge]] the recipe is "take theirs +
  reformat, fix any latent NameErrors the merge exposes." Surface
  any non-trivial conflicts before resolving silently.
- Sim seeds: 42 / 142 / 242 (3 seeds, matches EVAL_RUNNER convention
  for cross-seed variance estimation).
- Opponent population sources:
  1. **LLM personality pool** — full pool from `poker/personalities.json`,
     profiled individually. Personalities below a minimum sample
     threshold (TBD — likely those with <100 prior captured hands)
     get fresh sim hands generated for profiling.
  2. **FishBots** — `Fish` (baseline), `Fish-Sticky`, `Fish-Spew`,
     `Fish-Transparent` ARCHETYPES (defined in
     `experiments/simulate_bb100.py:184-198`; leak enums
     `sticky_then_pops` / `spews_bluffs` / `bets_strong_transparently`
     in `poker/rule_strategies.py`). Merged into this worktree from
     `development` (commit `840059de`) via merge `a27a90a0`.
- Per-personality sample size: 500 hands minimum for stat stability
  (WtSD CI tightens with showdown count; 500 hands ≈ ~150 showdowns
  at typical WtSD rates). Re-evaluate if H3 reports stdev > 0.05.

**Sim config / experiment parameters:**

```python
# code or config block describing exact settings
# Likely shape — to be filled:
# Axis A: stat profiling (no exploitation layer, just observation)
#   - Run each personality vs a neutral table mix, log per-hand
#     actions, mine WtSD + per-street AF + VPIP-PFR gap offline
#     (reuse poker/human_clone.py:_mine_hand_history logic).
# Axis B: existing-rule firing rate
#   - Same runs, log when classify_opponent_archetype returns
#     non-None for each personality; compute "miss rate" = fraction
#     of sticky-mid-zone personalities that come back None.
# Axis C: CRN bb/100 vs FishBot backdrops
#   - Extend exploit_bb100.py to include Sticky/Spew/Transparent
#     FishBots as backdrop options alongside CallStation/FoldyBot.
#   - Run paired exploit-ON vs exploit-OFF vs each backdrop, per
#     hero archetype.
```

**Wiring status / preconditions:**

- FishBot archetypes available on `development` (commit `840059de`).
- `_mine_hand_history()` exists in `poker/human_clone.py` (offline
  DB walk) — reusable for sim hand logs without wiring into the
  live opponent model.
- `exploit_bb100.py` CRN harness in place on `lookup-tables` and
  `development` — needs `--backdrop` extension to add FishBot
  caricatures.
- **Not required for this EXP:** WtSD / per-street AF on
  `AggregatedOpponentStats` (live wiring). This EXP measures from
  hand logs offline; live wiring is the decision this EXP gates.

**Output destination:** <where results land — file paths. Suggested:
docs/experiments/EXP_004_STICKY_MID_PASSIVE_POPULATION_AUDIT/
holding raw CSVs + summary plots>

## Measurements

**Primary metrics (used for H1):**

- Per-personality (LLM pool): VPIP, PFR, global AF, postflop AF
  (flop/turn/river averaged), per-street AF, WtSD, VPIP–PFR gap.
- Population prevalence: fraction of pool in the sticky-mid zone
  per the proposed trigger.
- Current-detector miss rate: fraction of sticky-mid-zone
  personalities for which `classify_opponent_archetype()` returns
  None.
- CRN bb/100 delta (exploit-ON vs exploit-OFF) per backdrop, per
  hero archetype.

**Secondary metrics (used for H2):**

- Per-FishBot: same stat profile.
- Detector firing matrix: would-be `sticky_mid_passive` detector
  output vs each FishBot type (positive control = Sticky should fire,
  negative controls = Spew/Transparent/baseline should not).

**Diagnostic metrics (used for H3 / context):**

- Per-personality sample-size stability: stdev of WtSD across seeds.
- Sticky FishBot CRN gain under current rules vs hypothetical
  re-thresholded rules (does re-thresholding alone close the gap, or
  does it need new detection?).

**Captured via:** <which scripts / commands produce these — likely a
new `experiments/profile_population.py` for stat axes A+B and an
extended `exploit_bb100.py --backdrop` for axis C>

## Comparison data

| Run | Source | <metric1> | <metric2> | ... |
|---|---|---|---|---|
| **CallStation baseline (caricature)** | `exploit_bb100.py` prior runs | <existing +22.5 vs rule-bots> | TBD | TBD |
| **Jeff_clone (contaminated)** | `experiments/clone_profiles/jeff.json` | <+0.0 vs human clones from prior eval> | TBD | TBD |
| **LLM pool (per personality)** | TBD | TBD | TBD | TBD |
| **Sticky FishBot** | TBD | TBD | TBD | TBD |
| **Spew FishBot** | TBD | TBD | TBD | TBD |
| **Transparent FishBot** | TBD | TBD | TBD | TBD |

## Caveats / Known Confounders

<List things ahead of time that could make the result misleading.
This is the most important section to fill in BEFORE running.
Candidates to consider:>

1. **Caricature trap (the eval's recurring lesson).** FishBots are
   hand-authored; clean detection vs Sticky FishBot does NOT prove
   the same threshold works on a real player who is *almost* sticky.
   Treat FishBot evidence as mechanics validation, not calibration.
2. **LLM personality non-determinism.** Same personality across seeds
   can vary in stat profile if the LLM's outputs drift. Need per-
   personality variance reported alongside means.
3. **Sample-size vs detector-noise floor.** WtSD needs the player to
   reach showdown N times before it stabilizes. At per-personality
   hand counts we can afford, WtSD CIs may be wide enough to mask
   the trigger.
4. **Multiway dynamics.** Per-street AF mined from full-ring sim
   hands averages over HU + multiway spots. Sticky behavior often
   intensifies multiway. The doc's threshold was theory-derived for
   HU; multiway thresholds may differ.
5. **The "no leak found" interpretation trap.** If CRN shows no
   bb/100 gain even with the new detector firing, that's a real
   negative result (don't build). But it could also mean the test
   pool isn't where the bot actually loses bb/100 in production —
   we lose Jeff as the ground-truth check on this.
6. **Branch mismatch.** Some exploitation rules were tweaked between
   `lookup-tables` and `development`. Pin the EXP to one branch and
   document which.
7. <other confounders specific to chosen setup>

## Validation criteria

**Outcomes we'll act on:**

| Outcome | Decision |
|---|---|
| H1 + H2 + H3 all met (population exists, detector mechanics work, signal computable) | Build the `sticky_mid_passive` detector + wire WtSD/per-street AF into `OpponentModelManager` live path. Route detector into existing `value_vs_station` + `bluff_reduction` rules (not new rules). |
| H2 + H3 met, H1 partial (mechanics work, population smaller than predicted) | <intermediate response — possibly: ship the detector behind a feature flag, gated to high-confidence cases only, vs deferring> |
| H2 + H3 met, H1 not met (mechanics work, no population to exploit) | Close the archetype doc. Drop the rest of the backlog (TAG/LAG/tilted/rock/3-bettor). Layer is "done" beyond housekeeping fixes. |
| H3 not met (signal too noisy or uncomputable at affordable sample size) | Debug measurement before any conclusion. If structurally noisy, the live wiring would also be noisy → don't build. |
| Existing rules already CRN-gain on Sticky FishBot | Re-threshold existing rules instead of building new ones (lower-risk move). |

## Results

Ran axis A on 2026-05-28 (`experiments/profile_population.py`) against the main
worktree DB (`/home/jeffh/projects/my-poker-face/data/poker_games.db`, read-only),
LLM-driven rows only (`capture_id` real), ≥200 hands. 26 personalities qualified.
Full table: `llm_field.csv` (this folder).

**Field distribution:**
- VPIP median **0.29** (min 0.10 Snoop/Elon/Oprah → max 0.81 Honey Badger).
- WtSD median **0.71** — corroborated by the aggregate RIVER/FLOP row ratio (0.67):
  the field genuinely reaches the river ~2/3 of flops seen. High, but real.
- Postflop AF mostly **1.0–2.5** (Queen of Hearts 2.5, Napoleon 5.7, Blackbeard 6.3).
  Only Oprah (0.57) dips below the 0.6 sticky-passive ceiling — and she's a 0.12-VPIP nit, outside the zone's VPIP floor.

**H1.1a (prevalence):** **0/26 = 0%** in the sticky-mid zone. **FALSE** (bar ≥10%).
**H1.1b (detector miss):** N/A — no in-zone players to miss.
**Current detector:** `classify_opponent_archetype` fires on **0/26** personalities.

The sticky-mid-**passive** quadrant (high WtSD ∧ *low* postflop AF) is **empty**.
The field that *is* sticky-to-showdown gets there by **betting/raising** (postflop
AF > 1), i.e. sticky-**aggressive**, the opposite of the targeted archetype.

**Bonus finding — the detector's real blind spot is maniacs, not stations.**
`_is_hyper_aggressive` reads *global* AF (>3.5). No personality clears it (max
global AF 1.93, Honey Badger) — yet Honey Badger's per-street AF is **17–30**,
Napoleon's flop AF **7.2**, Blackbeard's flop AF **10.0**. Preflop opens average
the per-street aggression down below the global trigger. This is exactly the
archetype doc's "global AF is misleading" thesis — but it hides **maniacs**, not
the stations the doc hypothesized.

## Conclusion

**H1 is FALSE — NO-GO on the `sticky_mid_passive` detector.** Sub-claim 1a failed
outright (0% vs ≥10% bar), so the falsifier fired on the first axis. Per the
validation table, that closes the build decision: there is no hidden
sticky-mid-passive population in the LLM field to exploit.

**Axes B (FishBot mechanics) and C (CRN bb/100) were not run.** They are moot for
the decision — if the population doesn't exist, whether the detector *mechanically*
fires on `Fish-Sticky` or gains bb/100 against a synthetic proxy can't justify
shipping it. Running them would only confirm mechanics for a detector we won't build.

**This corroborates [[project_exploitation_layer_eval]] and explains its "+0.0 vs
balanced" result.** The real field is *moderate* — no VPIP>0.70 stations, no global
AF>3.5 maniacs — so `classify_opponent_archetype` correctly returns None for
everyone. The layer's "+22.5 vs caricatures / +0.0 vs the real field" is the layer
doing its founding job: punish the CallStation/ManiacBot caricatures, don't deviate
against the moderate personalities. The detector isn't broken on the real field;
the *caricature* opponents it was tuned against simply don't occur there.

**Caveats:**
1. **VPIP discrepancy with the Range Explorer's "hybrid LLM ~59%."** This run's
   median is 0.29. Likely mean-vs-median (a few loose chaos bots — Honey Badger
   0.81, Lady Gaga 0.70 — pull a *mean* up) plus possible cohort/definition
   differences. It does **not** change the verdict: a looser field would push more
   players *above* the 0.50 zone ceiling → still 0 in zone. Worth reconciling
   before trusting absolute VPIP levels for any threshold work.
2. **WtSD is a reached-river/saw-flop proxy** (counts river-folders as "saw river"),
   an upper bound vs strict went-to-showdown. Corroborated by aggregate ratios but
   not identical to a HUD WtSD.
3. **LLM cohort only** (`capture_id` real). Excludes the human Jeff (0 LLM rows —
   confirming his data is human play, the contaminated outlier) and tiered play.

## Decisions made / next steps

1. **NO-GO: do not build `sticky_mid_passive`** and do not wire WtSD/per-street AF
   into the live `OpponentModelManager` *for station detection*. Close that thread
   of `TIEREDBOT_ARCHETYPE_EXPLOITATION.md`.
2. **The P2–P6 backlog (TAG/LAG/tilted/rock/3-bettor) gets no support from this
   data either** — the field doesn't cluster into exploitable caricatures. Don't
   build speculative archetype detectors against a moderate population.
3. **NEW thread surfaced (not scoped here): per-street-AF maniac detection.** The
   global-AF blindness misses genuinely aggressive personalities (Honey Badger,
   Napoleon, Blackbeard). A per-street-AF-aware aggression signal could route them
   into the existing `value_override` / `bluff_catch` machinery. Candidate for its
   own EXP if pursued. This is the one place the archetype doc's per-street thesis
   has real teeth — just pointed at aggression, not stickiness.
4. **Housekeeping (independent of all the above):** delete the dead `steal_pressure`
   rule (empty frozenset); fix the `fold_to_cbet` sim-wiring bug so the c-bet rules
   are at least measurable.
