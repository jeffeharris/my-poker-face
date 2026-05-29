---
purpose: Validated runbook + findings for pricing spot-tendency leaks in 6-max (multiway) using the paired-CRN attribution gate, and how multiway suppression interacts with the leak reshapes
type: guide
created: 2026-05-29
last_updated: 2026-05-29
---

# Multiway (6-max) pricing gate — validation, runbook, findings

> **Companion to** `docs/plans/PERSONALITY_PRICING_AND_VARIETY.md` (the methodology
> contract) and `experiments/ab_node_attribution.py` (the gate). This doc covers the
> **6-max** use of that gate: does it work, how to run it inside the time ceiling, the
> 6-max re-priced numbers for the known leaks, and the multiway-suppression interaction.

## TL;DR

- **The existing gate works in 6-max as-is.** Drop `--heads-up` and the `baseline`
  roster expands to 5 Baseline opponents (6-handed). **No code change was needed or made.**
- **The paired CRN is sound multiway:** control (same disable both arms) = **100%
  NO_DIVERGENCE / +0.00**, and the NO_DIVERGENCE residual is **exactly +0.000 bb/100**
  on every priced run (the trace is hero-decisions-only, so the 5 opponents acting
  between hero decisions don't pollute first-divergence attribution).
- **The core hypothesis is REFUTED.** `fit_or_fold` does **not** flip to −EV in 6-max —
  it stays free/+EV across self-play (**+3.90**), and the aggressive-reg field
  (**−1.60, CI∋0**). `give_up_turn` stays free (**−1.00** self-play, **−0.31** vs reg).
  `auto_cbet` stays free (**+3.18** self-play).
- **Recommendation: the gate is sufficient for multiway pricing as-is — no change needed.**
  The thing that doesn't generalize multiway is **the leak gates' narrowness**, not the
  instrument. See "Why fit_or_fold didn't flip."

## What works / what was validated

| Check | Result |
|---|---|
| 6-max via omit `--heads-up` (`baseline` roster → 5 opp) | works |
| Control: `--a-disable X --b-disable X` (same both arms) | **100% NO_DIVERGENCE, +0.00 bb/100** |
| NO_DIVERGENCE residual (paired-CRN soundness, multiway) | **+0.000 bb/100** on every run |
| First-divergence attribution with 5 opponents acting between hero decisions | **clean** — every diverging node matches the leak's gate (e.g. give_up_turn → `turn\|…\|{thin classes}` only) |
| HU control after all activity (`--a-hero Baseline --b-hero Baseline --heads-up`) | **100% NO_DIVERGENCE, +0.00** (unchanged; no edits made) |

## The validated runbook

All runs: `docker compose exec -T backend python -m experiments.ab_node_attribution …`.

**6-max self-play pricing (the primary anchor):**
```
docker compose exec -T backend python -m experiments.ab_node_attribution \
    baseline 3000 42,3042,6042,9042,12042,15042,18042,21042 \
    --hero TAG --hero-spot-tendency <name>:0.8 \
    --a base --b base \
    --a-disable spot_tendencies:<name> \
    --top 12
```
Arm A has the tendency OFF (`--a-disable`), arm B has it ON; the paired delta is its
marginal multiway cost. **`--a base --b base` is mandatory** — the gate defaults to
`--a tight --b wide` (DIFFERENT preflop charts), which makes the control diverge on
preflop chart differences instead of on the leak. With `--a base --b base` the only
difference is the disabled rule.

**6-max control (must pass before trusting a price):**
```
… --hero TAG --hero-spot-tendency <name>:0.8 --a base --b base \
    --a-disable spot_tendencies:<name> --b-disable spot_tendencies:<name>
→ 100% NO_DIVERGENCE / +0.00 bb/100 / residual +0.000
```

**Field vector (aggressive reg):** swap `baseline` → `punisher`. **Reduce hands** (see
time ceiling): `punisher 800 <8 seeds>` = 6.4k.

### Seed / hands convention that fits the 10-min ceiling

| Roster | What it is | 6-max runtime | Convention |
|---|---|---|---|
| `baseline` | 5× BaselineSolverBot (bare chart, no psychology) | **24k in ~90s** | full 24k, 8 seeds × 3000 |
| `punisher` / `jeff` | 5× human-clone (full psychology, DB profiles) | **24k blows the ceiling (>13 min)** | **6.4k**, 8 seeds × **800** (~5–9 min) |

- The `baseline` roster is the workhorse — 24k 6-max paired hands in ~90s, so use the
  full session-standard 24k (`3000 × 8 seeds`).
- The **clone rosters (`jeff`/`punisher`) are ~10× slower per hand** (psychology +
  equity machinery on 5 seats). 24k × 6-max exceeds the 10-min ceiling. **Drop to 800
  hands/seed (6.4k total)** for a directional field read; widen seeds, not hands, if you
  need a tighter CI. (Both clone rosters fill 5 seats, so both support 6-max — no roster
  gap; they're just slow.)
- **Operational caution (learned the hard way):** the harness backgrounds long docker
  runs. Launch field-vector runs **one at a time** and confirm the prior one finished —
  double-launching a clone-roster job spins up 16 worker processes and starves the shared
  container (and the parallel session). If you must kill an orphaned in-container run,
  match its **unique** signature (e.g. `fit_or_fold:0.8`) via `/proc/*/cmdline`, never an
  unscoped `pkill ab_node_attribution` (that would hit the parallel session).

## 6-max re-priced numbers (the headline)

Hero = TAG carrier (per-action cap 0.30), strength 0.8. Self-play (`baseline`) is the
intrinsic anchor; `punisher` is the aggressive-reg field vector. HU references from
`PERSONALITY_PRICING_AND_VARIETY.md`.

| Leak | HU self-play (ref) | **6-max self-play (24k)** | **6-max vs reg (punisher, 6.4k)** | verdict |
|---|---|---|---|---|
| `give_up_turn` | −1.47 [−4.13, +1.18] | **−1.00 [−3.41, +1.41]** | **−0.31 [−1.55, +0.93]** | **free** — CI∋0 everywhere; even closer to 0 than HU |
| `fit_or_fold` | +1.71 [−0.30, +3.72] | **+3.90 [+1.17, +6.63]** | **−1.60 [−5.81, +2.61]** | **free / +EV** — did NOT flip −EV; self-play CI-clear +EV |
| `auto_cbet` | +0.34 [−3.96, +4.65] | **+3.18 [−0.66, +7.03]** | _(not run — slow roster)_ | **free** — CI∋0, mildly +EV point estimate |

Per-node localization on every run is the cheap-variety signature: the cost spreads thin
across many nodes, all matching the leak's exact gate (give_up_turn → `turn` thin-class
nodes; fit_or_fold/auto_cbet → `flop` nodes), no single-node concentration.

### Why `fit_or_fold` didn't flip to −EV (the key finding)

The hypothesis was that `fit_or_fold` (over-fold air/weak to a flop c-bet) is a
multiway/full-ring leak that washes out HU because HU ranges are wide. **The data
refutes it** — fit_or_fold is *more* +EV in 6-max self-play (+3.90) than HU (+1.71), and
CI∋0 vs the reg.

The reason is the **leak gate's narrowness, not the regime.** The handler fires only on
`{weak_made, air_no_draw}` facing a flop c-bet. Those are genuinely fold-profitable hands
(low equity, no playability, no implied odds with a field behind) — and folding *more* of
them when you're one of several players is, if anything, *more* correct than HU. The
textbook fit-or-fold leak bites only when you fold hands **with equity/playability**
(2nd pair, draws, hands that flop a piece) — which this gate explicitly excludes (draws
are out, `air_strong_draw` is out). So there is nothing for "barrel relentlessly" to
punish: the folds the leak adds are close to correct in both regimes.

**Implication:** to build a genuinely-exploitable fit_or_fold leak (the loop-closing
version), the gate must widen to include equity/playable classes (e.g. also
`air_strong_draw` / a `medium_made` slice on dry boards). That's a `spot_tendencies.py`
change (parallel session's file — out of scope here), and it should be re-priced 6-max
after. The 6-max **gate** would price it fine; the current **leak** just isn't multiway-
specific.

## Multiway-suppression interaction (task 4)

**Layer order in `poker/tiered_bot_controller.py` postflop path (`_get_postflop_decision`):**

```
step 4  apply_multiway_adjustment   (active_count > 2)   ← multiway suppression FIRST
step 5  modify_strategy             (personality global scalars)
step 6  apply_river_bluff_guardrail (river only)
step 6.b apply_spot_tendencies      (the leak layer)     ← reshapes the ALREADY-suppressed dist
step 6a apply_exploitation
step 7+ math / defense floors
```

So **multiway suppression runs BEFORE the spot-tendency reshape.** `apply_multiway_adjustment`
(`poker/strategy/multiway.py`) scales aggressive mass down and check mass up for 3+ players —
**except `VALUE_CLASSES = {nuts, strong_made}`, which are exempt** (the §13 value exemption).
This produces a different interaction per leak, confirmed with a direct numerical probe
(chart base `bet 0.40 / check 0.60`, TAG cap 0.30, 6 players IP):

| Leak | class | multiway touches the input? | net interaction |
|---|---|---|---|
| `slowplay` | nuts/strong_made (**VALUE_CLASSES**) | **No — exempt.** Sees full bet mass. | reshape acts on the un-suppressed dist; **no interaction** — HU and 6-max see the same input |
| `give_up_turn` | thin/air | **Yes.** Multiway pre-suppresses bet `0.40 → 0.093`. | **effect partially eaten:** little bet mass left to give up (`0.093 → 0.019`, only −0.074 abs vs −0.30 HU). Suppression already did most of the "giving up" → 6-max price even closer to free (−1.00 vs −1.47 HU) |
| `auto_cbet` | thin/air | **Yes.** Multiway pre-suppresses bet `0.40 → 0.093`. | **antagonistic:** auto_cbet pumps back `0.093 → 0.393` (cap-bounded +0.30 from the suppressed floor) → it ~**undoes** suppression, landing near the original chart bet freq. Fires on far more nodes (351 vs 57) because it re-activates the whole suppressed range |
| `fit_or_fold` | weak_made/air (facing_bet) | **Mostly no.** Multiway only scales `raise`/`check`; `fold`/`call` largely untouched. | **independent:** fit_or_fold pumps `fold 0.347 → 0.647` regardless → price barely changes between regimes (matches the data) |

**Reading for price interpretation:** a spot tendency that *dampens* aggression
(`give_up_turn`, and the bet side of any future leak) is **partially pre-empted by
multiway suppression** at 3+ players — its measured multiway price understates its HU
effect because suppression already moved the mass. A tendency that *pumps* aggression
(`auto_cbet`) **fights** suppression (re-aggressing the suppressed range), so its multiway
firing rate is higher but its net EV effect is still ~free. A tendency that moves
**fold** mass (`fit_or_fold`) is essentially **orthogonal** to suppression. Slow-play
(value classes) is **unaffected** because value is exempt from suppression entirely.

This is why the multiway numbers for the dampen/pump leaks are *not* simply "the HU number
plus a multiway penalty" — the suppression layer reshapes the substrate the leak then
acts on. The gate prices the *composed* (suppression ∘ leak) behavior correctly (residual
+0.000), which is exactly the live behavior, so the price is faithful.

## CI / runtime characterization (task 2)

- **CI width:** 6-max self-play CIs are comparable to HU at the same N — `give_up_turn`
  6-max 24k = ±2.41 bb/100 (HU 24k was ±2.66). Multiway is **not** dramatically noisier
  for these low-fire-rate leaks; 24k is adequate on the fast `baseline` roster.
- **Fire rate:** ~0.2–1.5% of hands diverge (give_up_turn 57/24k ≈ 0.24%; fit_or_fold
  55/24k; auto_cbet **351/24k ≈ 1.5%**, the highest — the un-suppress-the-range effect).
  Low fire rates mean the CI is driven by a handful of high-variance diverging hands, so
  add **seeds** (more independent blocks) rather than hands if a CI straddles a threshold.
- **Runtime per 24k:** `baseline` ≈ **90s**; clone rosters (`jeff`/`punisher`) **exceed
  the 10-min ceiling at 24k** → use 6.4k (800×8). All comfortably parallel across the 8
  seeds via `ProcessPoolExecutor`.

## Recommendation

**The existing `ab_node_attribution.py` is sufficient for multiway pricing as-is.** No
change is needed. Concretely:

1. **Gate correctness multiway: confirmed.** Paired CRN residual is exactly 0, control is
   100% NO_DIVERGENCE, first-divergence attributes cleanly through 5 intervening
   opponents. The hero-decisions-only trace is the right design for multiway.
2. **The hypothesis (fit_or_fold flips −EV multiway) is false** — and the gate is what
   proved it. The non-flip is a property of the **leak gate** (too narrow — only
   fold-profitable classes), not the regime or the instrument. If we want a multiway-
   exploitable fit_or_fold, **widen the leak gate** in `spot_tendencies.py` to include
   equity/playable classes, then re-price 6-max (no gate change required).
3. **Runbook constraint, not a gap:** the clone rosters are too slow for 24k 6-max. Use
   `baseline` at 24k for the intrinsic anchor and clone rosters at 6.4k for the field
   vector. This is a sizing convention, not a code fix.
4. **Interpretation rule (new):** when reading a *dampen-aggression* leak's multiway
   price, remember multiway suppression has already moved bet→check before the leak runs,
   so the multiway price **understates** the HU effect; a *pump-aggression* leak instead
   re-activates the suppressed range. Bake this into how a leak's multiway number is
   compared to its HU number.

### No code changes made

I made **no edits** to `ab_node_attribution.py` or any source file — the gate priced
multiway entirely through existing flags (`--hero-spot-tendency`, `--a-disable`, and
omitting `--heads-up`). The only artifact is this doc.
