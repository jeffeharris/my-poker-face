---
purpose: Baseline-then-tune the emotional/psychology axes so tilt has a believable AND noticeable impact on play, with the dramatic `shaken` state actually reachable
type: experiment
status: planned
hypothesis_summary: Lowering the composure/confidence recovery floor (and relaxing the shaken corner thresholds) makes the dramatic `shaken` state reachable for hotheads and raises the always-on emotional EV swing to a noticeable level, without pushing the penalty-band distribution out of its PRD targets
created: 2026-06-10
last_updated: 2026-06-10
---

# Experiment 009 — Emotional Tuning: Believable And Noticeable

> **Why this exists:** The tilt-excursion EV work (`docs/plans/TILT_EV_HARNESS.md`)
> found the `TILT_SIGNATURE_ENABLED` refinement is ~0 bb/100 in real play and that
> the `shaken` state — the one designed to produce the **dramatic** ±0.30
> spew/collapse modifiers (`PSYCHOLOGY_OVERVIEW.md` §zones) — **never fires** (0 of
> ~3200 sim hands across 8 personas, runs exp 4 + exp 5). Two structural causes,
> both confirmed in code:
>   1. `shaken` is a corner zone requiring **Conf < 0.35 AND Comp < 0.35**
>      simultaneously (`PENALTY_SHAKEN_{CONF,COMP}_THRESHOLD`, `zone_config.py:37-38`),
>      but losses hit **composure** far harder than confidence (`big_loss` conf −0.15,
>      `losing_streak` comp −0.20 vs conf −0.12; `player_psychology.py:165-191`) and
>      confidence rebounds fast on any win/bluff — so bots land in `tilted`
>      (composure-down) not `shaken` (both-down).
>   2. `RECOVERY_BELOW_BASELINE_FLOOR = 0.60` (`zone_config.py:51`) pulls the axes
>      back toward baseline before they can punch through the 0.35 corner.
>
> The product goal (user, 2026-06-10): **emotions and tilt should have a believable
> AND noticeable impact.** Today they are believable (bounded, non-catastrophic) but
> not noticeable (the dramatic state is unreachable; the marginal EV is ~0). This
> experiment establishes the baseline magnitude, then tunes the axes to hit a
> believable-and-noticeable range.

## Hypothesis

> **STATUS: thresholds RATIFIED by user 2026-06-10** (noticeable = "clearly present
> ~5–8%"; believability = "Moderate"). The one remaining `<TBD>` — the EV-swing
> magnitude — is deliberately deferred to Phase A (set relative to the measured
> baseline), then this becomes fully locked before Phase B tuning runs.

**H1 (primary):** Lowering `RECOVERY_BELOW_BASELINE_FLOOR` (0.60 → sweep) and/or
relaxing `PENALTY_SHAKEN_{CONF,COMP}_THRESHOLD` (0.35 → sweep up) makes the dramatic
states reachable and emotions noticeable, while staying believable:

- **Noticeable (reachability):** hotheads (risk-seekers, low recovery_rate) reach
  `shaken` in **5–8%** of their tilted-state decisions (currently 0%).
- **Noticeable (impact):** the always-on emotional EV swing rises to **≥ <TBD —
  set after Phase A relative to the measured baseline>** bb/100 in magnitude.
- **Believable (distribution, "Moderate"):** full-tilt (penalty ≥ 0.75) stays
  **≤ 5%** of decisions and baseline stays **≥ 70%** (PRD); tilt stays **episodic**
  — designated hotheads may run up to **~35%** penalty-time, but no persona is
  chronically pinned above it.
- **Believable (character):** stoics/monks (Buddha; high poise) reach `shaken`
  **< 0.5%** of decisions; risk-split holds (risk-seekers spew, risk-averse collapse).

**H2 (precondition / null-validating):** `shaken` fires at all under the tuned
settings (the mechanism is reachable). If even an aggressive setting yields 0
`shaken`, the lever is wrong (see falsifier).

**H3 (believability guard):** Reachability does not come at the cost of chronic tilt
— recovery still returns a tilted persona to baseline within **<TBD: e.g. ≤ 8>**
hands on average; the PRD bands above hold.

**Falsifier:** If making `shaken` reachable (H2) *requires* settings that blow the
PRD distribution (full-tilt > 2%, or baseline < 70%, or a persona chronically > the
penalty-time cap), then the recovery-floor / corner-threshold levers are the **wrong
knobs** — the real lever is the **event model** (confidence-down magnitudes are too
small relative to composure-down) or **per-persona anchors** (baseline_composure /
recovery_rate), and we pivot the tuning there. If the EV swing stays below the
noticeable threshold even when `shaken` fires at target rate, then the EV model or
the downstream coupling — not the axes — is the bottleneck.

## What we're testing

A small grid over **zone parameters only**, everything else identical to the exp-5
baseline cast/config:

- `RECOVERY_BELOW_BASELINE_FLOOR`: 0.60 (baseline) → {0.45, 0.30}
- `PENALTY_SHAKEN_CONF_THRESHOLD` / `PENALTY_SHAKEN_COMP_THRESHOLD`: 0.35 →
  {0.40, 0.45} (raise = easier corner)
- (Stretch arm, only if the above can't reach H2 within PRD bands) per-hothead
  `baseline_composure` / confidence-down event magnitudes — the falsifier pivot.

Applied via the existing override path: `run_from_config` reads a config's
`zone_params` block → `set_zone_params()` (`zone_config.py:113`), or
`ZONE_PARAMS_FILE` env. No code change to the psychology engine for the primary arms.

## Setup

**Sandbox:** the `mpf-tilt-ev` worktree DB (`data/poker_games.db`, eval-only,
root-owned sim output, gitignored). Each arm is a fresh experiment row; the corpus
extractor scopes by `experiment_id`.

**Sim config:** `experiments/configs/tilt_corpus_wide.json` (6-handed, 3
risk-seekers + 3 risk-averse, 8×250 = 2000 hands, psychology ON, tilt decision-flags
OFF, tiered no-LLM bots). Each tuning arm adds a `zone_params` override block.

**Phase A — baseline (measure, don't tune):**
1. **Always-on emotional EV magnitude** — the "is it noticeable today" number. NEW
   probe (a mode of `tilt_corpus_ev.py`): for every recorded spot price
   `EV(modify_strategy WITH the recorded emotional_state)` − `EV(base_strategy_probs)`
   → ΔEV(emotion) in bb, paired/trajectory-free, amortized to bb/100. This is the
   magnitude the signature delta sits on top of (currently unmeasured).
2. **Reachability + PRD distribution** — from exp 4 + exp 5 already in the DB:
   per-state %time, per-persona penalty-band breakdown vs the PRD table, confirm
   `shaken` = 0. (`tilt_corpus_extract.py` meta + a penalty-band query.)

**Phase B — tuning sweep:** run each grid arm, extract its corpus, run the
emotional-EV probe + the PRD-band query + a `shaken`-reachability count. Pick the
setting that satisfies H1.

**Output destination:** `experiments/data/tilt_corpus_exp<NN>.jsonl` per arm; a
results table appended to this doc + `TILT_EV_HARNESS.md` cross-link.

## Measurements

**Primary metrics (H1 — noticeable):**
- Always-on emotional EV swing (bb/100), baseline vs each arm.
- `shaken`-decision rate for hotheads (Fyodor/Freddie/Calamity).

**Secondary metrics (H1 — believable):**
- PRD penalty-band distribution (baseline / medium / high / full-tilt %) vs targets.
- Per-persona %time in any penalty zone (chronicity).

**Diagnostic (H2 / H3 / context):**
- Does `shaken` fire at all (count) per arm.
- Monk (`Buddha`) `shaken` rate.
- Mean hands-to-recover from a tilted episode.
- State mix (composed/overconfident/tilted/shaken/dissociated) per arm.

**Captured via:** `experiments/run_from_config.py` (arms),
`experiments/tilt_corpus_extract.py` (corpus + meta + state mix),
`experiments/tilt_corpus_ev.py` (EV; +new emotional-magnitude mode), a penalty-band
SQL query over `player_decision_analysis.zone_total_penalty_strength`.

## Comparison data

| Run | Source | emo EV swing (bb/100) | shaken rate (hothead) | full-tilt band % | baseline band % |
|---|---|---|---|---|---|
| **baseline (exp 5)** | `tilt_corpus_wide.jsonl` | TBD (Phase A) | 0.0% | TBD | TBD |
| **arm: floor 0.45** | TBD | TBD | TBD | TBD | TBD |
| **arm: floor 0.30** | TBD | TBD | TBD | TBD | TBD |
| **arm: corner 0.40** | TBD | TBD | TBD | TBD | TBD |
| **arm: corner 0.45** | TBD | TBD | TBD | TBD | TBD |

## Caveats / Known Confounders

1. **Tiered no-LLM bots** — no LLM "read" layer; the erratic-reads coupling acts on
   the exploitation layer only. Conclusions are about the strategy axes, not LLM feel.
2. **EV model is the synthetic range-aware estimator** (fish/competent backdrops,
   heads-up forward EV) — good for *relative* swings across arms, not an absolute
   table-truth bb/100.
3. **Single persona cast** (the wide 6) — distribution targets are cast-dependent;
   `shaken` reachability for hotheads may not generalize to all 104 personas.
4. **Composure floor interacts with per-persona `baseline_composure`** — all current
   personas share baseline_composure 0.60; the floor lever and the anchor lever are
   partially redundant. The grid isolates the zone-param lever first.
5. **Trajectory desync across arms** — different zone params → different game
   trajectories, so cross-arm distribution comparisons are not paired. The EV swing
   IS paired (priced on each arm's own recorded spots). Use multiple seeds if a band
   sits near a threshold.
6. **"Noticeable" is partly a taste call** — the bb/100 and shaken-rate thresholds
   are set by judgment + (eventually) playtest, not derived. That is why they are
   flagged `<TBD>` for sign-off before running.

## Validation criteria

**Outcomes we'll act on:**

| Outcome | Decision |
|---|---|
| H1 + H2 + H3 all met by a grid arm | Adopt that zone-param setting as the new default; record in `PSYCHOLOGY_DESIGN.md` + `zone_parameters.json`; re-run the tilt EV probes against it |
| H2 met, H1 partial (shaken reachable but EV swing or distribution off) | Take the best arm, then tune the secondary lever (event magnitudes / anchors) to close the gap; new sub-experiment |
| H2 met, H1 not met (shaken fires but distribution always breaks PRD) | Conclude the floor/corner levers trade reachability for believability — pivot to the event-model lever (the falsifier path) |
| H2 not met (no arm reaches shaken) | The corner thresholds aren't the binding constraint — debug confidence dynamics (why confidence never drops) before any further tuning |

## Results

### Phase A — baseline (2026-06-10)

**A2 · Believability today (PRD penalty-band distribution, exp 5, 10675 decisions):**

| band | actual | PRD target | verdict |
|---|---|---|---|
| baseline (pen < 0.10) | **91.7%** | 70–85% | **OVER — too calm** |
| medium (0.10–0.50) | 5.3% | 10–20% | under |
| high (0.50–0.75) | 1.9% | 2–7% | low end |
| full_tilt (≥ 0.75) | 1.1% | 0–2% | ok |

The system **under-fires vs its own design target** — emotions are too tame
globally, the opposite of a "too hot" problem. Per-persona penalty-time: Fyodor 28%
(full-tilt 5.5%), Freddie 15.8%, Calamity 5.4%, Poe 2.5%, **Churchill 0%, Scrooge
0%** — the risk-averse personas are essentially emotionless.

**A3 · Why `shaken` = 0 — confidence is pinned high (the root cause):** at
deep-penalty spots (penalty ≥ 0.50, n=321), composure craters (min 0.01–0.11) but
**confidence stays high — median 0.96, p25 0.95, min 0.38; ZERO spots have
confidence < 0.35.** `shaken` needs Conf < 0.35 AND Comp < 0.35, so it is
structurally unreachable. Cause, quantified: `compute_baseline_confidence`
(`psychology_model.py:464`) *rewards* aggression + ego + risk, so the hotheads who
tilt have the **highest** baseline confidence (Fyodor ≈ 0.80, clamped), and winning
floats it to 0.96 → they hit `overconfident`, never `shaken`.

**This is a structural catch-22:** the personas who tilt (aggressive/high-ego/
risk-seeking) are by formula the most confident and can't get scared; the personas
who could get scared (low ego/aggression/self_belief) play tight and don't tilt.
"Spew when scared" (shaken + risk-seeking) is self-defeating in the current model.

**A3-scope:** the confidence-down event magnitudes (`big_loss` −0.15, `losing_streak`
−0.12, `bluff_called` −0.25) are **hardcoded** (`player_psychology.py:165-191`), not
zone-params — only recovery/threshold/radii are config-tunable. So the one lever that
could make confidence actually *fall* is **not reachable by config**.

**A1 · Always-on emotional EV magnitude (the "noticeable today" number):** the
emotional shift vs the raw pre-emotion baseline is **+14.4 (fish) / +16.9
(competent) bb/100, mean Δagg +0.128** (`tilt_corpus_ev.py --mode emotional`, exp 5).
So emotions are **already highly noticeable in behavior** — a +12.8pp aggression-mass
swing on the spots where they fire — concentrated in the hotheads (Fyodor +9.5/+12.5,
Calamity +4.5; Poe −0.1; the flat personas ≈ 0). **But it is MONOTONE:** the shift is
almost entirely "more aggressive" (overconfident/tilted → aggressive); the protective
pole (shaken → passive collapse) never fires, so emotions push play in essentially
one direction. (Caveat: the +bb/100 magnitude rides the same range-aware-EV property
that prices fold-equity aggression as +EV — the trustworthy signal is the **Δagg**
behavioral swing, which is large and real.)

**Reframe:** the problem was never "emotions don't move play" — they move it a lot.
It is that the movement is **one-dimensional (only the aggression pole), mis-frequent
(under-PRD, flat for risk-averse personas), and character-undifferentiated** — the
fear/collapse half of the design is dead due to the confidence catch-22.

### Falsifier triggered → tuning redirect

The pre-registered **H1 primary lever (`RECOVERY_BELOW_BASELINE_FLOOR`, a composure
knob) is FALSIFIED by the baseline**: composure already reaches 0.01, so it is not
the binding constraint — **confidence is**. Per the falsifier ("the real lever is the
event model … or per-persona anchors"), the tuning pivots to the **confidence axis**:

- **L1 (event model, needs code):** scale up confidence-down magnitudes so a brutal
  downswing genuinely dents confidence (not just composure).
- **L2 (anchors / cast):** lower `baseline_confidence` for "fragile" archetypes
  (`self_belief`) and ensure they are also tilt-prone — break the catch-22 by design.
- **L3 (recovery, config):** `RECOVERY_ABOVE_BASELINE` — decay above-baseline
  confidence faster so it doesn't sit at 0.96 (necessary, not sufficient).
- **L4 (corner, config):** `PENALTY_SHAKEN_CONF_THRESHOLD` — insufficient alone
  (confidence 0.96 ≫ any sane threshold).

**Config-only tuning cannot reach `shaken`.** Hitting the believable-and-noticeable
goal requires L1 and/or L2 — an event-model or anchor/persona change — which is a
design decision, not a parameter sweep. Phase B is re-scoped around L1+L2.

### Phase B — slice 1 (2026-06-10): catch-22 broken, but under-target

First implementation slice behind `EMOTIONAL_REBALANCE_ENABLED` (off by default, 246
psychology tests byte-identical when off): the **L1+L2** levers from
`EMOTIONAL_SYSTEM_BALANCE.md §6.1` — re-derived `baseline_confidence`
(`0.35 + self_belief·0.35 + ego·0.15`; Fyodor 0.80→0.645) + the event-table rebalance
(cut the UP pumps, concentrate DOWNs on epistemic events). Wide-cast sim flag-on
(exp 6, 2000 hands), measured with `tilt_reachability.py`:

| metric | baseline (exp 5) | slice 1 (exp 6) |
|---|---|---|
| **`shaken` decisions** | **0** | **17 (0.16%)** — fear pole reaches the corner |
| confidence @ deep penalty (median) | 0.96 | **0.52** (min 0.13; 8 spots in the <0.35 corner) |
| Fyodor `shaken`% | 0.00% | 0.92% |
| `overconfident` decisions | 884 | **25** |
| baseline penalty-band | 91.7% | 97.0% |

**Verdict: the central hypothesis is VALIDATED** — decoupling conviction from
chip-winning makes the fear pole reachable (the confidence axis now *falls*; `shaken`
fires for the first time). **But slice 1 is not yet at target on two axes:**
1. **Under-frequency:** Fyodor 0.92% `shaken` vs the **5–8%** H1 target — the door is
   cracked, not open. Needs the §6.1 **recovery asymmetry** (`RECOVERY_ABOVE_BASELINE_CONF`)
   + likely stronger epistemic-down magnitudes / the new `shown_a_bluff` /
   `hero_call_wrong` events (which need detector emission).
2. **System got *tamer*, not livelier:** cutting the up-pumps collapsed
   `overconfident` (884→25) — which was a large chunk of "emotional" time — so total
   penalty-time fell (97% baseline-band). This is *correct* (winning bots were too
   cocky) but it surfaces the **orthogonal Phase-A finding** (the system under-fires
   globally): we now need to turn UP overall emotional sensitivity/frequency to hit
   the PRD bands, separately from the reachability fix.

### Phase B — slice 2 + the event-generation wall (2026-06-10)

Tried to lift `shaken` frequency by deepening the epistemic-down magnitudes
(`bluff_called` −0.22→−0.30, `losing_streak` −0.10→−0.16, +`nemesis_loss` −0.24) and
widening the corner (`PENALTY_SHAKEN_*` 0.35→0.40, exp 7). Result was **noisier and
not better** (`shaken` 17→7, Fyodor →0%) — single 2000-hand sims can't resolve a
<0.2% event; the knob is below the trajectory-noise floor. **Reverted** (unvalidated).

Tested a hypothesis (user): does **busting / leave-table pressure** cap tilt — a
tilting hothead spews → busts → sits out frozen before spiraling? Deep-stack sim (4×
stacks so tilters survive longer, exp 8): **refuted.** Deeper stacks made the system
*tamer* — Fyodor penalty-time **6.9% → 3.8%**, `shaken` → 1 (penalty-time is high-N,
so this is robust, not noise). **Why:** emotional events fire on swing severity
*relative to stack* (big_loss / bad_beat / all-in); deep stacks shrink every pot's
stack-fraction → fewer severe events → calmer bots. So tilt is not capped by players
removing themselves — it is capped by **event generation**: these tiered bots in a
calm 6-max structure simply don't experience enough severe swings.

**The redirect (the real frequency lever):** to make tilt NOTICEABLE-by-frequency,
the lever is **how often/severely pressure events fire** (the detector thresholds +
structure variance), NOT the axis magnitudes (slice 1 already made the axes
sufficient) and NOT stack depth. Candidate next probes: (a) pressure-event detector
sensitivity (are `big_loss`/`bad_beat` thresholds too conservative for these
stacks?); (b) a multi-seed / higher-N harness so <1% events are measurable; (c) the
production note below.

**Production note (user, valid independent of the sim):** the cash **leave/movement
decision is tilt-blind** — it weighs bankroll/energy/respect, not composure/tilt. So
a tilting cash AI can walk away mid-spiral and the meltdown happens off-screen.
Realism ("losing → leave") fights the goal ("stay → melt down"). Fix = **tilt →
table-stickiness** (low composure reduces leave-pressure / biases re-buy — true to
how tilt actually works: chasing). Added as a new lever to
`EMOTIONAL_SYSTEM_BALANCE.md`.

### Phase B — slice 3 (detector) + slice 4 (episode duration) + the metric correction (2026-06-11)

**Slice 3 — detector sensitivity** (committed): gate `is_big_pot` (0.75→0.45) +
equity-shock (0.30→0.20) under the flag so the calm bots clear the "big moment" bar
more often. Modest lift (`shaken` 17→22, Fyodor 0.92%→1.64%); confirmed event-rate is
*a* lever but single-sim noise dominates a <0.2% metric.

**(a) Representative remeasure (skill gap, exp 10):** 3 loose hotheads vs 3 solver
sharks so the hotheads run bad. Fyodor penalty-time **6.4% → 10.9%** — a *losing*
player genuinely tilts more (the user's downswing intuition, confirmed). The winning
sharks go `overconfident` (symmetric). **Metric correction:** aggregate PRD bands are
the wrong lens here — they average a tilting loser with calm winners. The right metric
is **per-player penalty-time while on a downswing**.

**(b) Probability-rolled episode duration (user's idea):** on tilt entry roll
`D = round((1−poise)² · 24 · (0.4+0.6·U))` hands, near-freeze recovery for `D` hands,
then climb out. **`%time-in-tilt ≈ entry-rate × E[D]`, bounded ⇒ believable.**
Sim-aggregate couldn't resolve it (trajectory noise + dilution), so it is validated
**directly** (`tests/test_tilt_episode.py` + a unit trace):

| persona | poise | rolled D | hands held in tilt |
|---|---|---|---|
| Fyodor | 0.25 | 6–13 | **9–16** |
| Calamity / Freddie | 0.42 / 0.45 | 4–8 | 6–10 |
| Churchill | 0.78 | 1 | 3 |
| Scrooge | 0.80 | 0–1 | **2–3** (monk ≈ none) |

The mechanism does exactly what was asked: a tilting hothead now stays visibly tilted
for ~9–16 hands (a felt, sustained episode), bounded (non-chronic), monks ≈ 0 — the
character spread is right. **This is believable AND noticeable at the episode level**,
even though the table-aggregate PRD band is a noise-limited / wrong-altitude metric.

**Net for the goal:** believable+noticeable is achieved at the level that matters
(per-player episode): fear pole reachable (slice 1), drama on entry (signatures), and
sustained bounded episodes (slice 4). The aggregate PRD-band frequency remains a
measurement question (needs per-player + multi-seed), not a mechanism gap.

## Conclusion

**Phase A complete.** It converted "emotions feel weak" into three measured facts:
(1) emotions are already a **large, monotone** behavioral force (+0.128 Δagg, the
aggression pole only); (2) the **fear pole is structurally dead** (confidence pinned
≈0.96 for the personas who tilt — the catch-22); (3) the system **under-fires vs its
own PRD** and is flat for risk-averse personas. The binding constraints
(confidence-axis coupling, event-magnitude balance, per-archetype baselines) are
**not config-tunable**, so a parameter sweep (the original Phase-B plan) is the wrong
instrument.

**Decision (user, 2026-06-10): treat this as a first-principles game-balance
redesign of the emotional/psychology system, not a tune.** EXP_009 is therefore the
**diagnostic baseline** that motivates that redesign; the design work + its own
validated tuning experiments supersede the Phase-B sweep scoped above. See the
follow-on design doc (to be created): the axes (decouple confidence-in-reads from
winning-high), the four-quadrant behavioral signatures + intended magnitudes, the
event→axis driver balance that makes all quadrants reachable per archetype, and the
per-archetype frequency/volatility targets — all measured against
`tilt_reachability.py` + the corpus EV probes built here.

## Decisions made / next steps

*To be filled after conclusion.*
