---
purpose: Handoff for a fresh context picking up the strategy re-validation + exploitation re-architecture
type: guide
created: 2026-06-12
last_updated: 2026-06-12
---

# Handoff — strategy re-validation & exploitation re-architecture

## TL;DR
We distrusted the shipped strategy stack's "+EV" claims (they predate the
2026-06-11 clone fold-to-3bet fix + the preflop chart regen) and started
re-validating. The big finding: **exploitation is effectively dead vs believable
opponents** — it's a soft logit nudge that doesn't change behavior. We designed a
replacement architecture (trunk + tendency layer; exploit = gear-switch + hard
override; psychology gates adaptation). Design is written; implementation is NOT
started.

## Where things are (git)
- **main:** 6max push/fold + reshove + fold-equity gate + the human_clone
  fold-to-3bet fix are MERGED (PRs #286/#293/#302).
- **branch `strategy-revalidation`** (this work, not yet PR'd — it's docs + lints +
  audit, decide whether to PR): postflop lints (real, tested), the re-validation
  matrix/audit, the exploitation findings, and the 3 architecture docs below.

## Read in this order
1. `STRATEGY_LAYERS.md` — the authoritative layer model (maps 1:1 to the pipeline).
2. `TENDENCY_CONTRACT.md` — the spine: a `Tendency` = {detect, construct, counter,
   scope}, defined once, used to build archetypes / detect / exploit (the first pass).
3. `EXPLOIT_CATALOG.md` — leak → counter-tendency → scope, per archetype + granular.
4. `docs/plans/STRATEGY_REVALIDATION_MATRIX.md` — what's stale, the audit, the
   verification addendum, and the re-validation results (incl. the 0-fires probe).
5. `docs/captains-log/lookup-tables/revalidation-and-the-22bb-that-wasnt.md` — the
   narrative (why we distrust, what broke).

## The mental model (one paragraph)
Strategy = (1) base chart [a semi-generalized TRUNK + a per-archetype tendency layer]
+ (2) a small persona flavor nudge (`modify_strategy` — NOT the exploit mechanism)
+ (3) situational overrides; PSYCHOLOGY is independent and gates adaptation
(composed exploits; tilt collapses to base). A **tendency** is one object reused in
both directions: *construct* an archetype that has the leak ⇆ *counter* it when
detected. One catalog = archetype-build = opponent-model vocab = learnable.

## The load-bearing finding (proven, don't re-litigate)
Exploitation's value is concentrated on caricatures and **does not change behavior
vs believable opponents.** Behavioral proof (`experiments/exploit_behavior_probe.py`):
vs a pure CallStation, detection maxes at intensity 1.0 yet bluff rate moves
59.9%→59.8%. The "+22.5 bb/100" re-measured to +10.3 vs an extreme station,
marginal vs competent, ~0 vs a realistic field. ⇒ **nudge = flavor; exploit must be
chart-switch (real range change) or hard override.**

## Next step (build order)
1. **Detection-reachability + stat-fidelity probe FIRST**, then re-key only what's
   proven to fire live. The obvious re-keys (`hyper_passive`→postflop AF; ungate
   multiway `high_fold_to_cbet`) are premised on detections that don't currently
   reach real hands (vpip-0.35 reg < 0.70 cutoff; observed fold-to-cbet ~0.06). See
   matrix §follow-up. Blind re-keying changes nothing.
2. **One gear-switch vertical slice:** opponent-read → `_select_preflop_table`
   chooses trunk+counter-tendencies; gate on composed emotional state
   (`_zone_to_tilt_factor`). Validate with `exploit_behavior_probe.py` (play must
   move vs the matching opponent; must NOT move when tilted) → then bb/100.
   Start with vs-nit steal (preflop, cleanest).
3. **Layer-3 hard overrides** for postflop exploits charts can't express
   (stop-bluff-vs-station, barrel-vs-folder). New villain stats unlock more rows.

## Tools
- `experiments/exploit_behavior_probe.py` — does an exploit CHANGE BEHAVIOR
  (bluff%/value% by hand class, ON vs OFF). THE gate for any exploit change.
- `experiments/reshove_bb100_probe.py` — bb/100 A/B with the folding clone
  (`FIELD=punisher|competent`).
- `exploit_bb100` (clone backdrops auto-register from `clone_profiles/`),
  `simulate_bb100` (`--start-bb`), `sng_runner`.
- Hetzner burst runner: `docs/EVAL_RUNNER.md`. **Only `poker-bot-optimization`
  context, ALWAYS tear down.** Gotcha: `mkdir /root/poker/data` before
  `docker compose run` (seed entrypoint needs the writable mount even DB-free).

## Hard-won lessons (the meta-thread)
- **Verify agent/audit output.** The 9-agent audit got 3/5 "quick-wins" wrong; the
  0-fires synthesis had an internal contradiction (proposed a fix premised on a
  detection that doesn't fire live). Treat all generated findings as hypotheses.
- Old "+EV, shipped" verdicts are **unverified on current code** (pre clone-fix /
  pre-regen). Re-run against a *folding* opponent before trusting.
- **Clone fidelity is a live risk:** the punisher clone's authored `fold_to_cbet=0.70`
  measures ~0.06 in play — authored stats don't always manifest. A clone is only a
  valid test bed once its leak actually shows up in the opponent model.

## Pending re-validation batches (Hetzner; build+test probes locally first)
relationship_modifier (ON by default, NEVER EV-measured — top unknown);
multistreet H1 barrel + overbet vs the folding field; math_floor call-off;
push_fold_6max unopened/caller bb/100.
