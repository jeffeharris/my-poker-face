---
purpose: Scope for a psychology-in-the-loop paired EV harness — the missing instrument to put a believable bb/100 number on tilt-state decision changes before they go default-on
type: design
created: 2026-06-09
last_updated: 2026-06-10
---

# Tilt EV harness (scope)

The tilt-excursion work (`docs/technical/TILT_EXCURSION_DESIGN.md`) shipped two
decision-affecting, flag-gated pieces — the **signature** (risk_identity
spew/collapse, `TILT_SIGNATURE_ENABLED`) and the **erratic-reads** coupling
(`TILT_ERRATIC_READS_ENABLED`). Both are unit-tested, the signature is
behaviorally + EV-safety validated by a paired *decision* probe
(`experiments/tilt_signature_probe.py`), and both are structurally clamp-bounded
so they can't be catastrophic. What's missing before either could go default-on is
a **believable bb/100 number in live play** — and the existing eval harnesses
can't produce it. This doc scopes the harness that could.

## Current measurement (2026-06-09 → 2026-06-10)

A first pass against this scope produced three results worth recording, plus a
Phase-2 follow-up (point 4) that wired in the range-aware equity point 3 demanded:

1. **Loss-mix recalibration (distribution harness).** `measure_zone_distribution.py`'s
   synthetic event model ran hot. A 5-seed live sweep (`experiments/tilt_live_sweep.py`,
   tilt flags OFF, real pressure detector) puts the hotheads at **Poe (0.40) 9.3% ±
   3.3** and **Fyodor (0.25) 15.8% ± 5.8** per-hand tilt; the synthetic hothead band
   sat at 18.7%. Softening `LOSS_MIX` from ~29% to ~20% composure-crushers lands the
   synthetic hothead band **median at 12.5%**, matching the live hothead-pair **mean
   ~12.6%**. The match is **aggregate only** — per-persona synthetic diverges (Poe
   synth ~23% vs live 9%), so the synthetic is a spread-shape tool, not a per-persona
   predictor. Consequence for this harness: the bb/100 multiplier is the **live
   per-archetype tilted rate** (from the sweep), not the synthetic absolute.

2. **The prior recorded live OFF numbers don't reproduce.** `TILT_EXCURSION_DESIGN.md`
   recorded OFF tilt of 2.4% (Poe) / 3.7% (Fyodor) from an earlier run with no
   committed script; the OFF recover() path is unchanged since, yet every fresh run
   reads ≥ 6% (OFF mean 9–16%). Almost certainly a measurement-denominator difference
   in the prior run. The corrected, multi-seed live baseline is in the design doc's
   validation block. Note the on/off sim is **trajectory-desynced** (ON not
   consistently ≥ OFF across 5 seeds) — it cannot measure persistence's marginal
   effect, which is exactly the gap this paired harness fills.

3. **Phase-1 of approach C is built** — `experiments/tilt_ev_probe.py`: the paired
   signature probe + an eval7-priced EV estimator (fold-equity model, fish vs
   competent backdrop). It validates the EV machinery end-to-end and prices the
   **collapse** direction plausibly (risk-averse: ~−0.03 bb/spot vs a fish, ~0 vs a
   competent opp). It surfaced a **hard requirement for the trustworthy build**: `eq`
   is currently vs a *random* hand, so heads-up aggression is mechanically +EV (even
   72o is ~37% vs random) and the **spew** direction reads spuriously +EV. Light spew
   is −EV only against the villain's *continue* range — so **range-aware
   eq-when-called is required (not a refinement)** before the aggression-direction
   bb/100 can be trusted. The collapse sign/magnitude is usable now.

4. **Phase-2 range-aware equity is now wired in (2026-06-10)** — `tilt_ev_probe.py`
   replaces eq-vs-random with two range-conditioned equities: `eq_call` (vs the
   villain's *betting* range) and `eq_called` (vs the villain's *continue-vs-raise*
   range, the strong top of range). Each backdrop now defines its continue range,
   not just its fold-to-raise frequency (fish = wide/sticky `LATE` ~32%; competent =
   tight `STANDARD_3BET` ~8%). A trash `J4o`-shove spot was added as the
   discriminator the point-3 artifact was about. **Result: the fix worked but did
   not flip the spew sign — and that turned out to be a finding, not a
   shortcoming.** The range-aware model correctly prices the all-in/trash branches
   −EV (COLLAPSE now reads −0.02/−0.06 bb/spot, properly negative; the isolated J4o
   jam prices ~−0.5 bb), but **SPEW (risk-seeking, shaken) still reads +EV** (~+0.22
   vs fish, +0.25 vs competent). A per-spot breakdown shows why: the softmax +
   divergence clamp make the signature express as **`call` → small `raise_2.5bb` on
   PLAYABLE hands** — a legitimately +EV shift given fold-equity — **not** as the
   trash all-in jams it is feared to be. On the J4o spot the signature actually
   *jams less* (all-in 0.113 → 0.088). So the structural clamp bound holds: **the
   signature cannot manufacture a catastrophic trash-shove** (that pathology lives
   in the preflop charts, not here). Two consequences: (a) the **catastrophe gate is
   effectively answered** — the signature is structurally non-catastrophic; (b) the
   spew **sign is dominated by the spot MIX** (how often the bot is in a small-raise
   spot vs a big-jam spot), so a trustworthy bb/100 still needs the **real recorded
   corpus + frequencies** — hand-picked spots cannot settle the sign. Range-aware
   equity was the prerequisite; the corpus is now the single binding step.

5. **Erratic-reads now has a factor-level EV-direction probe (2026-06-10)** —
   `experiments/tilt_erratic_probe.py`. Erratic-reads operates one level below the
   signature: it does not shift the action distribution, it only scales the
   EXPLOITATION layer. `tilt_factor` enters as `effective_bias = adaptation_bias ·
   tilt_factor`, which **linearly** scales the exploitation offset magnitude
   (`exploitation.py:1410`) and hard-gates the layer off below `GATING_FLOOR=0.05`.
   Nothing else reads it. So the flag's EV is linear in the factor: `ΔEV ≈
   exploitation_edge · (E[tilt_factor_on] − tilt_factor_off)`, and pricing the
   FACTOR delta is the whole question up to two corpus scalars (the exploitation
   edge per read in bb, and the tilted-decision rate). **Finding — the flag is
   mildly +EV vs off, not a cost.** The OFF cliff is 0.5 tilted / **0.0 shaken**
   (shaken forgets every read); the ON erratic mean is `1 − 0.5·intensity` (0.75 at
   intensity 0.5), which sits ABOVE the cliff. The probe (gate-aware, integrated
   over the U draw, real per-persona `adaptation_bias`) shows Δm small-positive for
   tilted (→0 at high intensity, where the erratic mean ≈ the 0.5 cliff) and
   **strongly positive for shaken at every adaptation tier** — there the cliff zeros
   exploitation (`fire_off≈0`) and the erratic taper restores it (`fire_on≈1`,
   Δm up to +0.52). So the "pure attenuator / can only reduce a read's edge" safety
   note in `TILT_EXCURSION_DESIGN.md` §4 is true only relative to a *full* read
   (factor=1); relative to the actual OFF baseline, the flag makes a tilted/shaken
   bot exploit MORE on average — the believability win ("unreliable reads") rides on
   top of a mean exploitation INCREASE, concentrated in the shaken band. It is +EV
   while the read is +EV and non-catastrophic regardless (the offsets stay clamped);
   the only −EV exposure is a *stronger* read against an opponent actively inducing,
   which is the believability point, not a catastrophe. bb/100 still needs the same
   recorded corpus (part (a)) to supply the two scalars.

## Why the existing harnesses can't measure this

Two independent blockers, both confirmed against the code:

1. **The bb/100 harnesses don't run psychology.** `experiments/exploit_bb100.py`
   and `experiments/champion_challenger.py` give a clean common-random-numbers
   (CRN) paired bb/100, but their hand loop never invokes the psychology pipeline
   (no `pressure_detector` / `recover` / composure update). So the bot's composure
   never moves, it never tilts, and the tilt flags **never fire** — the measured
   delta would be ~0, meaningless. (`exploit_bb100` even resets stacks per hand,
   which would suppress stack-based pressure events too.)

2. **`_apply_flags` toggles controller *attributes*, not the env-gated flags.**
   `champion_challenger._apply_flags` does `setattr(controller, attr, value)`. The
   tilt flags are read through the feature-flag registry (`is_enabled(...)` → env /
   DB), not controller attributes, so the `CHANGES` mechanism can't toggle them
   per arm as-is.

And the reason a naive psychology-on A/B (e.g. `run_ai_tournament` flag-on vs
flag-off) is **not** enough: a decision-gate change desyncs the RNG, so the two
arms diverge into different game trajectories (`reference_cash_sim_ab_paired`).
We saw this directly — the on/off aggregate sim showed the *composed*-state
aggression differing across arms, proving the spots weren't comparable, and
tilt→short-stack→forced-all-ins swamped the signal.

## The core tension

A trustworthy measurement needs **both**:

- **psychology in the loop** (so the bot actually tilts from real outcomes), and
- **pairing** (so the flag is the only difference, not the trajectory).

These fight each other: two paired twins at one table see the same cards, but once
their decisions differ their *results* diverge, so their *psychology* (composure,
tilt state) diverges too. You can't naively pair when the thing you're measuring
changes the state that gates it.

## Approaches (pick one)

**A — Forced-shared tilt state (cleanest pairing).** Run two twins of the same
archetype on a CRN table, but drive BOTH twins' composure from a *single shared*
outcome/pressure stream (or force an identical scripted composure trajectory), so
on every hand both are in the *same* tilt state and only the flag differs. Measure
the paired per-hand bb/100 (CRN, à la `exploit_bb100`). Pro: clean isolation. Con:
the shared-state injection is artificial (composure no longer reflects each twin's
own results) — fine for an EV-isolation measurement, not for realism.

**B — Conditional EV, non-paired, large N.** Run a single psychology-on sim
(`run_ai_tournament` style) flag-on, and measure bb (or chip EV) *only on hands
where the bot is tilted* (`zone_composure < 0.40`), vs its own composed-hand
baseline and vs a flag-off run. Accept the noise; lean on large N + many seeds.
Pro: fully realistic. Con: noisy, slow, and the tilt↔short-stack confound persists
(needs stack-normalized EV, not raw chips).

**C — Tilted-spot decision replay + EV rollout (recommended).** Extends the
existing paired *decision* probe one step: (1) run a psychology-on sim once to
**record the real tilted decision spots** the bot actually reaches (hand, board,
pot, stacks, opponent model, composure) into a corpus; (2) for each recorded spot,
compute the strategy both arms (flag off/on) on that *identical* spot and estimate
its **EV** — either against a fixed opponent range (closed-form preflop EV) or a
short Monte-Carlo rollout to showdown. ΔEV per spot × tilt-frequency → a bb/100
attributable to the tilt change. Pro: trajectory-free (the spots are fixed), uses
real tilted spots, reuses the paired-probe machinery + the bounded-options EV
estimates. Con: needs an EV estimator wired in; rollout adds cost.

**Recommendation: C.** It is the natural extension of what already works
(`tilt_signature_probe.py` is C minus the EV estimator), it sidesteps the
pairing/psychology tension entirely (record once, replay paired), and it directly
yields "the tilt change costs X bb/100, concentrated in the Y% of hands the bot is
tilted." A is a good cross-check if C's EV model is doubted.

## What to reuse

- **Tilted-spot corpus:** the psychology-on sim path already exists
  (`run_ai_tournament` with `enable_psychology=True`, tiered no-LLM bots — see
  `experiments/configs/tilt_persistence_check.json`); add spot-capture (the
  `decision_analysis_repo` already records zone state per decision — extend it to
  dump the full decision context, or capture in a sidecar).
- **Paired evaluation + EV estimator:** `experiments/tilt_ev_probe.py` is
  `tilt_signature_probe.py` plus an eval7-priced EV estimator. It toggles the flag
  per arm, prices each arm's strategy in bb, and reports paired ΔEV. Phase-2 part
  (b) is **DONE (2026-06-10)** — `eq`-vs-random is replaced with **range-aware
  eq-when-called** (an in-probe eval7 MC over the villain's continue-range combos;
  see point 4 above). The remaining Phase-2 step is part (a): **swap the synthetic
  `SPOTS` for the recorded corpus** — now the single binding step, since part 4
  showed the spew sign is mix-dominated.
- **EV estimator components:** `decision_analyzer.calculate_equity_vs_random` /
  `calculate_equity_vs_ranges` (eval7 Monte-Carlo) are the equity core;
  `bounded_options.calculate_required_equity` for pot odds. (`bounded_options`'s
  `ev_estimate` is only a categorical "+EV/neutral/-EV" label — not a number.)
- **CRN bb/100 (for approach A):** `exploit_bb100`'s CRN loop is the template;
  it would need a psychology-pipeline call per hand + a forced-shared-state hook +
  an env-flag toggle per arm (the `CHANGES`/`_apply_flags` path only does
  attributes today — add an env-flag spec kind).

## Open questions

- EV estimator fidelity: closed-form-vs-range is cheap but assumes a fixed
  opponent; a rollout is more faithful but costs more. Start closed-form, validate
  a sample against rollout.
- Which opponent to price EV against — the table's actual mix, a fixed
  exploitable backdrop, or GTO? (Mirrors the `exploit_bb100` backdrop question:
  price against both a fish backdrop and a competent one; a cost that only appears
  vs fish is overfit.)
- Is the goal a *catastrophe gate* (bb/100 loss under some bound) or a *believable*
  target (tilt should cost the right amount)? The former is objective; the latter
  is a playtest/taste call the harness only informs.

## Cross-references

- `docs/technical/TILT_EXCURSION_DESIGN.md` §4 — the pieces this would validate +
  the structural clamp bound + the KL-from-baseline EV-safety measure already done.
- `experiments/tilt_signature_probe.py` — the paired decision/KL probe (approach C
  minus the EV estimator).
- `experiments/tilt_ev_probe.py` — approach C for the SIGNATURE: the paired probe +
  an eval7-priced EV estimator, now with range-aware eq-when-called (point 4).
- `experiments/tilt_erratic_probe.py` — factor-level EV-direction probe for
  ERRATIC-READS (point 5): prices the tilt_factor delta (cliff vs erratic taper)
  that linearly scales the exploitation edge.
- `experiments/exploit_bb100.py` / `experiments/champion_challenger.py` — the CRN
  bb/100 machinery (approach A template; today psychology-blind).
- `experiments/configs/tilt_persistence_check.json` — the psychology-on sim config
  to source the tilted-spot corpus from.
