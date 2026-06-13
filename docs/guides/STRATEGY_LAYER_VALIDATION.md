---
purpose: A repeatable gate-ladder for validating ONE strategy/exploitation layer — cheap aborts first, EV last — distilled from the 2026-06-12 re-validation session's mistakes
type: guide
created: 2026-06-12
last_updated: 2026-06-12
---

# Validating a strategy layer — the playbook

The re-validation set out to check the whole strategy stack (~25 layers + 18
charts; see `docs/plans/STRATEGY_REVALIDATION_MATRIX.md`) and instead spent a full
day on ONE layer (exploitation), partly because the same wires got tripped
repeatedly. This is the loop that makes each layer cheap enough to actually get
through the matrix. **Run it per layer. Each phase has a cheap ABORT before the
expensive next one.**

## The gate ladder (cheap → expensive)

### Phase 0 — Provenance (minutes, no sim)
- Pin ONE claim: what is the layer's claimed effect, **when** last measured,
  **against what opponent**, on **what code**?
- If the verdict predates a relevant code/chart change (clone fix, chart regen,
  a refactor) → it is **UNVERIFIED**, not "validated." Re-measure.
- Decide the success criterion NOW (a number + sign + which field), before you
  see any data. Pre-registration stops post-hoc rationalizing.

### Phase 1 — Does it FIRE? (one quick instrumented pass)
- Confirm the layer actually triggers in the target regime. A short run
  (hundreds–1k hands), instrumented for "did this fire / flip any decision?"
- **If 0% → STOP. It is not wired/reachable.** Do not run anything longer. (This
  is the gate that would have saved two ~20-min runs this session — once an
  unwired detector, once a harness that didn't feed the stat the detector needs.)
- Also confirm the **test bed is faithful**: do the opponent's authored leaks
  actually MANIFEST in observed play? (`detection_fidelity_probe.py` style — diff
  observed stats vs the clone's authored profile.) An unfaithful bed makes every
  downstream result a lie.

### Phase 2 — Does it change the right BEHAVIOR? (paired behavior probe)
- CRN-paired ON vs OFF on the SAME spots. The targeted behavior must actually
  move vs the matching opponent (e.g. bluff% drops vs a station), and must NOT
  move where it shouldn't (psychology/tilt gate; a non-matching opponent).
- A logit offset that doesn't flip the sampled action is a no-op — measure the
  *behavior*, not the intent. (`stop_bluff_probe.py` / `exploit_behavior_probe.py`.)
- **If behavior doesn't move at full intensity → the magnitude is wrong (needs a
  hard override), not the detection.** Fix here before EV.

### Phase 3 — Does it win CHIPS? (bb/100, last, most expensive)
- Paired ON/OFF (`exploit_bb100` / `champion_challenger`), CI over seeds.
- Controls that actually control:
  - **Before/after**: use the SAME backdrop as the prior number, or the delta is
    confounded by everything else that changed.
  - **Believable field**: a true reg / folding clone (Jeff/Punisher), NOT
    semi-exploitable rule bots dressed up as "competent" (GTO-Lite/ABCBot give a
    positive edge that proves nothing about overfit).
  - **Non-matching field**: confirm it doesn't misfire / leak where it shouldn't.

### Phase 4 — Record & gate
- Record number + CI + field + code rev in the matrix doc. Ship only on CI-clear
  (or accept-with-reason, e.g. believability > EV). Then NEXT layer — don't let
  one layer's rabbit hole eat the matrix.

## Guardrails (the "everything lies" rules)

These are the specific traps this session hit, each more than once:

1. **Verify generated findings before acting.** The 9-agent audit's own "quick
   wins" were mostly wrong on verification. Treat any audit / agent / "shipped
   +EV" claim as a *hypothesis*, not ground truth.
2. **Wire it before you test it.** Never run the experiment meant to measure a
   change before the change is connected to behavior. A control with a foregone
   answer (an unwired gate → 0%) is theater, not a measurement.
3. **Quick-pass-confirm-it-FIRES before any long run.** Cheap. Catches both
   "unwired" and "fires but does nothing." Phase 1 exists for this.
4. **Suspect the FEED before the FORMULA.** When a stat reads 0/neutral where it
   shouldn't, the math is usually right and a divergent code path isn't feeding
   it. This session: WTSD read 0 in the sim AND in the bb/100 harness — two
   different hand-drivers (`simulate_bb100.run_hand`, `champion_challenger.run_cc_hand`)
   each missing the showdown feed. The stat-duplication debt is real
   (`OPPONENT_STAT_SOURCE_OF_TRUTH.md`); assume it until proven otherwise.
5. **The test bed lies.** Re-measure against the CURRENT opponent, and confirm the
   opponent behaves as labeled. Authored clone stats didn't manifest; an authored
   "folder" called like a station; the "+22.5" was measured against a non-folder.
   A null/positive result against a mislabeled bed teaches nothing.

## Tools (per phase)

| Phase | Tool |
|---|---|
| 1 fires / fidelity | `experiments/detection_fidelity_probe.py` |
| 2 behavior | `experiments/exploit_behavior_probe.py`, `experiments/stop_bluff_probe.py`, `experiments/relationship_modifier_probe.py` |
| 3 bb/100 | `experiments/exploit_bb100.py`, `experiments/champion_challenger.py`, `experiments/sng_runner.py` |
| burst | Hetzner runner (`docs/EVAL_RUNNER.md`) — `poker-bot-optimization` ONLY, always tear down |

## Applied results (layers run through the ladder)

| Layer | Verdict | Where it aborted / shipped | Date |
|---|---|---|---|
| `_apply_exploitation` (Tier-2) | **SHIPPED** +13.4 bb/100 [+8.8,+18.0] vs a realistic Jeff+Punisher field; +24.2 vs extreme station | Re-arch: `loose_passive` 3-axis detector + stop-bluff hard override (PR #330). The original soft-nudge was a Phase-2 fail (bluff 59.9→59.8%) | 2026-06-12 |
| `_apply_relationship_modifier_to_offsets` | **INERT — abort at Phase 2; no live EV risk** | See below | 2026-06-12 |

### `relationship_modifier` — Phase-2 abort, worked example

The matrix flagged this as the **highest *unknown-validation* risk**: ON by
default, mutates the EV-critical offsets pre-clamp, **never EV-measured**. The
ladder closes it without an EV run:

- **Phase 0 (provenance):** the only "validation" was unit tests of the axis→
  multiplier mapping — nothing behavioral, nothing field-relative.
- **Phase 1 (does it fire?):** **0% in every existing harness, structurally.**
  `simulate_bb100`'s controller factory hard-sets `apply_relationship_modifier
  = False` (line ~550, "sims don't seed relationship_states"); `exploit_bb100`
  attaches a bare `OpponentModelManager` with no `_relationship_repo`, so the
  reader hits its first early-out. It cannot be measured without a seeded-heat
  bed — which is *why* it was never measured.
- **Phase 2 (does it change behavior?):** `experiments/relationship_modifier_probe.py`
  forces the flag on at the instance level and injects the **strongest** modifier
  the v1 mapping can emit (max-rival: aggressive offsets ×1.3; soft-friend ×0.85)
  on *every* decision, TAG vs CallStation HU (the matchup where the additive
  offsets are populated 75.9% of decisions). Result: the modifier engaged **11,886
  times and scaled 21,494 aggressive offsets** — and changed **0.0pp** of behavior.
  Decision distribution is byte-identical to OFF (same per-class counts to the
  integer; under CRN a single flipped action would desync the board and move the
  counts). **Conclusion: inert-by-channel.** It scales the additive-logit `offsets`
  the re-arch already proved doesn't flip a sampled action, and never touches the
  channels that actually move behavior (the stop-bluff hard override or
  `value_vs_station` intensity). The `fold_to_pressure` (respect) axis is even less
  reachable — 0 fold deltas were ever available to scale.
- **Verdict:** **abort before Phase 3** — there is no EV to measure on the current
  architecture. Downgrade the matrix rating from "high / highest unknown" to
  **inert, no live EV risk** (it ships ON but cannot change a decision). If
  relationship-based exploitation is actually wanted, it must be **rebuilt on the
  hard-override / gear-switch channel**, exactly like the Tier-2 re-arch — a soft
  multiplier on the dead offset channel is a no-op by construction.

This is the ladder's payoff in miniature: Phase 0/1/2 are all cheap, and they
retire a "highest unknown" load-bearing-looking layer **without a single bb/100
run**.

## The remaining worklist (run this loop on each)

Still stale/un-revalidated from the matrix (priority order): `multistreet_context`
H1 barrel + `overbet_context` (priced vs a non-folder); `math_floor` call-off;
`_apply_position_blindness`; `value_override` preflop; `tilt_conditioning`
band-compliance; `push_fold_6max` unopened/caller bb/100. And the open BUILD item:
the **Tier-1 gear-switch** (coarse preflop range-switch on opponent read — the
exploitation re-arch only did Tier-2).

> `relationship_modifier` is **retired** from this list — validated inert (see
> "Applied results" above). The same inert-by-channel finding applies to any
> future soft-multiplier layer that rides the additive `offsets` dict: prove it
> flips a sampled action at Phase 2 before pricing it.
