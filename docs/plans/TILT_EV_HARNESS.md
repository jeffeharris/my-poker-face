---
purpose: Scope for a psychology-in-the-loop paired EV harness — the missing instrument to put a believable bb/100 number on tilt-state decision changes before they go default-on
type: design
created: 2026-06-09
last_updated: 2026-06-09
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
- **Paired evaluation:** `experiments/tilt_signature_probe.py` already toggles the
  flag per arm via `os.environ` and runs `modify_strategy` on a fixed spot. Swap
  its synthetic baselines for the recorded corpus and add the EV estimator.
- **EV estimator:** the bounded-options layer already computes per-action
  `ev_estimate`; or use `eval7` equity + pot odds for a closed-form / rollout EV.
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
- `experiments/exploit_bb100.py` / `experiments/champion_challenger.py` — the CRN
  bb/100 machinery (approach A template; today psychology-blind).
- `experiments/configs/tilt_persistence_check.json` — the psychology-on sim config
  to source the tilted-spot corpus from.
