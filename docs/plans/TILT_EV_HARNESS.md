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

6. **Recorded corpus + corpus bb/100 are BUILT (2026-06-10) — part (a) done.**
   `experiments/tilt_corpus_extract.py` pulls real tilted-decision spots from a
   psychology-on sim and `experiments/tilt_corpus_ev.py` prices the signature on
   them. **No new capture was needed** — the tiered bot already persists, per
   decision, a `strategy_pipeline_snapshot_json` with `base_strategy_probs` (the
   pre-emotion baseline), `emotional_state`, `anchors`, `legal_actions`,
   `deviation_profile_name`, and the geometry; joined with the row's hole
   cards/board/`num_opponents` that is a complete, self-contained probe spot. A spot
   is "tilted" iff its recorded `emotional_state.state` is one the features act on.
   - **Corpus (exp 4, `tilt_persistence_check.json`, 6×200 = 1200 hands):** 4605
     decisions, **333 tilted spots → 7.23% tilted-decision rate** (the bb/100
     multiplier, now measured). States: 234 overconfident / 76 tilted / 23
     dissociated / **0 shaken**. Hothead Fyodor is 298 of the 333 (33% of his
     decisions); 77% of tilted spots are preflop.
   - **Signature corpus bb/100 ≈ 0.00** (−0.000 vs both fish and competent
     backdrops; `tilt_corpus_ev.py`). This is REAL, not a wiring bug — a per-state
     diagnostic confirms the signature fires correctly where it should. The reason
     it nets ~0: the signature only changes behavior when a persona's **risk
     character disagrees with the state's legacy direction**, and in this corpus
     that intersection is **7 of 333 spots** (risk-averse Poe, 0.3, tilted →
     collapse; Σ|Δagg|=0.002). The rest are no-ops: overconfident is excluded by
     design (234); Fyodor is a risk-seeker and `tilted` already defaults aggressive
     so his character-driven direction is unchanged (64); Buddha is risk-averse and
     `dissociated` already defaults passive (23). **So in real play the signature's
     EV footprint is ~0 — not because it is clamped, but because the population of
     spots where it diverges from the legacy state-map is tiny.** A strong
     non-catastrophe confirmation.
   - **CORPUS-BREADTH LIMITATION (the real remaining gap):** this corpus has only 4
     personas and **never reached a `shaken` state** — exactly the state where a
     risk-seeker's spew *diverges* from the legacy map (shaken defaults passive, so a
     risk-seeker flips to aggressive). It also under-samples risk-averse personas
     reaching tilt (only Poe). So the ~0 is trustworthy for THIS distribution but
     under-prices the collapse/spew tails. To price those, re-run the corpus with a
     wider, more tilt-prone, risk-diverse persona cast (and confirm `shaken` is
     reached). The instrument is done; only the input distribution is narrow.
   - **Erratic-reads:** the corpus supplies the tilted-decision rate (7.23%) and
     state mix; combined with point 5's Δm this is mildly +EV, but the
     overconfident-heavy / no-shaken mix means the big shaken-band amplification (Δm
     up to +0.52) didn't occur here either — same corpus-breadth caveat. The last
     missing scalar is the exploitation edge per read (bb).

7. **Wider-cast corpus (2026-06-10) — refines the signature number and finds a hard
   reachability wall.** `experiments/configs/tilt_corpus_wide.json`: a 6-handed,
   deliberately tilt-prone, risk-diverse cast (3 risk-seekers — Fyodor/Freddie
   Fratboy/Calamity Jane — + 3 risk-averse — Winston Churchill/Poe/Scrooge), 8×250 =
   **2000 hands** (exp 5). 10675 decisions, **1087 tilted spots, 10.18% rate**.
   - **Signature corpus bb/100 = −0.165 (fish) / −0.134 (competent)** — a small,
     believable, non-catastrophic COST, similar magnitude vs both backdrops (not
     overfit to the fish). It is **entirely the collapse tail**: all of it comes
     from **Poe's 44 risk-averse-tilted spots** (Δagg −0.016 — passivity forgoes a
     little value); every risk-seeker is an *exact* no-op (Fyodor 589 spots, Freddie
     349, Calamity 104 → 0.000), because tilt already defaults aggressive and their
     character agrees. This refines point 6's ~0 (which under-sampled collapse): the
     true signature cost is **≈ −0.15 bb/100, all collapse, negligible** (a competent
     reg's win rate is single-digit-positive bb/100; 0.15 is noise-level) — and it is
     a believability *feature* (tilted risk-averse players genuinely tighten up).
   - **Reachability wall (the real conclusion):** across BOTH runs (~3200 hands, 8
     personas) the sim reached **zero `shaken` states** — exactly the state where a
     risk-seeker's spew DIVERGES from the legacy map (shaken defaults passive). The
     cause is structural, confirmed in code: `get_emotional_shift`
     (`bounded_options.py`) only emits `shaken` when the `shaken`/`timid` penalty
     ZONE fires, and `shaken` is a corner zone needing **low confidence AND low
     composure simultaneously** — which the **composure floor (~0.40, the tilt line;
     see `EMOTIONAL_SYSTEM_ANALYSIS`)** prevents the bot from reaching. So **the spew
     tail is not measurable via this sim path at all** until that upstream floor is
     addressed — no persona cast can fix it. Separately, **risk-averse personas
     barely tilt** (Scrooge 0%, Churchill 0.05%, Poe 2.7% of decisions): they play
     tight, take fewer big losses, so they rarely leave composed/overconfident — the
     collapse tail is thinly populated by *nature*, not by cast choice. Net: the
     tilt-state population is dominated by risk-seekers in aggressive-default states
     (the signature's no-op case), so **the signature's real-play EV is structurally
     pinned near zero (−0.15 bb/100), and the only un-pricing left (spew) is blocked
     on tilt-state reachability, not on this harness.**

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
  see point 4 above). Part (a) — **swap the synthetic `SPOTS` for the recorded
  corpus** — is also **DONE (2026-06-10)** via `tilt_corpus_extract.py` +
  `tilt_corpus_ev.py` (point 6). Both parts complete; the only remaining gap is
  corpus BREADTH (wider/risk-diverse, tilt-prone persona cast that reaches `shaken`)
  to price the collapse/spew tails the current 4-persona corpus under-samples.
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
- `experiments/tilt_corpus_extract.py` — part (a): extracts the real tilted-spot
  corpus + meta (tilted-decision rate, state mix) from a psychology-on sim's
  persisted `strategy_pipeline_snapshot_json` (point 6).
- `experiments/tilt_corpus_ev.py` — the corpus bb/100 for the signature: prices
  range-aware ΔEV on the recorded spots, amortized over hands played (points 6–7).
- `experiments/configs/tilt_corpus_wide.json` — the wider risk-diverse, tilt-prone
  6-handed cast for the breadth run (point 7); surfaced the `shaken` reachability
  wall and the collapse-only −0.15 bb/100.
- `experiments/exploit_bb100.py` / `experiments/champion_challenger.py` — the CRN
  bb/100 machinery (approach A template; today psychology-blind).
- `experiments/configs/tilt_persistence_check.json` — the psychology-on sim config
  to source the tilted-spot corpus from.
