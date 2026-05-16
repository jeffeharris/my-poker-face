---
purpose: Phase A — add an induce/slowplay override to TieredBot's postflop pipeline, gated on existing stats, to exploit multi-street barrelers on the facing-bet path
type: design
created: 2026-05-15
last_updated: 2026-05-16
---

# Induce Override — Phase A

## Context

TieredBot's only rule keyed to opponent aggression today is the
**strong-hand value override** in `poker/strategy/value_override.py:114`
(invoked from `tiered_bot_controller.py:_apply_value_override`, line
~810). When it fires facing a bet, it does **50% call / 50% raise** —
get the money in. That is the right exploit vs a pure station, but
the *wrong* exploit vs a multi-street **barreler**: by raising the
flop bet, hero ends the bluff sequence and captures one street of
value where smooth-calling would have captured the same flop bet plus
a turn barrel and (often) a river barrel.

The exploitation layer in `poker/strategy/exploitation.py:1078` has the
same blind spot for the inverse case. Its `hyper_aggressive` rule
*widens calls* and *tightens opens* but never *demotes raises in favor
of smooth-calls* with value hands.

Today's `AggregatedOpponentStats` (`exploitation.py:420`) already
carries `aggression_factor_postflop` and `cbet_attempt_rate` — enough
to construct a **barreler proxy** without adding new tracking.

ManiacBot (`poker/rule_strategies.py:251` — `_strategy_maniac`) bets
75% pot on every street unless equity < 20%, qualifying as a real
triple-barreler. Importantly, ManiacBot OOP first-to-act almost always
*bets* the flop rather than checking it — so the induce exploit
against ManiacBot lives on the **facing-bet** branch, not the
open-spot branch. (An earlier draft of this plan scoped Phase A to
open-spot IP induce; that branch would fire ~0 times vs ManiacBot
because villain never checks first.)

Phase A: ship an `induce_override` layer using existing signals,
scoped to the facing-bet IP path. Phase B extends to OOP
(check-raise), adds a proper `barrel_frequency` stat, and introduces
confidence-scaled mixing.

### Empirical baseline (2026-05-16 dry-run)

Before drafting was finalized, a 60-hand HU dry-run of TieredBot vs
ManiacBot confirmed the gate components are populated as expected once
two prerequisite plumbing fixes shipped (see [Prerequisites](#prerequisites)
below):

```
Final ManiacBot stats observed by TieredHero (22 hands):
  aggression_factor_postflop:    4.000  (saturated at medium_af_postflop cap)
  cbet_attempt_rate:             0.800  (4 attempts / 5 opportunities)
  postflop_seen_as_pfr_count:    5
  pfr:                           0.767
```

Two takeaways:

1. **AF_pf saturates at the cap (4.0)** rather than being deflated by
   defensive calling — ManiacBot never calls postflop in HU at 200 BB
   stacks because raise is always legal in its strategy. The Phase A
   gate threshold of `AF_pf ≥ 3.0` clears comfortably.

2. **`postflop_seen_as_pfr_count` rate is ~1 per 4-5 hands in HU.**
   The 22-hand sample produced 5 PFR-seen-on-flop events. Scaled to
   the Phase A experiment matrix (1000 hands × 8 seeds = 8000 hands
   per arm), expect ~1800 events per arm — easily clearing the
   sample-floor gate. But for shorter validation runs (e.g., a quick
   smoke test under 100 hands), the sample threshold may bottleneck.

## Testable Hypothesis

> Inserting an `induce_override` layer that, on flop/turn IP
> facing-bet spots with `hand_class == 'nuts'` (and `nut_status ==
> 'nuts'`) vs an opponent with `aggression_factor_postflop >= 3.0`
> AND `cbet_attempt_rate >= 0.70` AND ≥ 10 hands observed AND ≥ 10
> `postflop_seen_as_pfr_count` observations, redistributes facing-bet
> probability mass to **1.00 call / 0.00 raise** during Phase A
> validation (instead of `value_override`'s 0.50/0.50) at ≥ 40 BB
> effective stack and dry boards (≤ 1 danger flag), will produce:
>
> - **(H1) ≥ +5 bb/100 improvement** for TieredBot vs ManiacBot,
>   measured across **1000 hands × 8 seeds (n = 8000 hands per arm)**.
> - **(H2) ≤ −2 bb/100 leak** vs each of: CaseBot, GTO-Lite, baseline
>   ABCBot (TAG-equivalent). (Same sample size per matchup.)
> - **(H3)** Rule fires on **< 1% of decisions** vs ABCBot/nit
>   opponents (gate selectivity check — non-barrelers should not trip
>   the AF_postflop × cbet_attempt gate).

All three must hold. **Falsifier**: any single (H1) miss, (H2) breach,
or (H3) > 2% fire rate vs non-barrelers kills Phase A and triggers a
gate review before any Phase B work.

The numbers above are committed pre-run:
- **+5 bb/100 is deliberately conservative** for the facing-bet
  scope. Per-firing EV correction (the original estimate of 2–4 BB
  understated the multi-street geometry): at 75% pot bets across
  flop, turn, and river, smooth-calling captures the flop bet +
  turn barrel (~5.6 BB extra) + river barrel (~14 BB extra) where
  raising flop captures only the called flop bet. Realistic
  per-firing delta vs ManiacBot is **6–15 BB**, not 2–4 BB. Firing
  rate is correspondingly lower: the dry-run measured ~1 cbet
  opportunity per 4-5 hands. Of those, the nuts + dry-board + IP
  filter probably keeps **5–15 fired-induce decisions per 1000-hand
  match**. Net EV upside: 30–225 BB per match → 3–22 bb/100. +5 is
  the conservative end, large enough to detect.
- **−2 bb/100 leak** is below the n=8000 noise floor (~1 bb/100
  stderr based on Phase 8 readouts scaled by √n). At the prior
  n=2000 design point, stderr was ~5–10 bb/100 and the test would
  have been inconclusive — the bumped sample size is required.
- **<1% fire rate vs TAG/ABCBot** follows from the gate construction —
  `aggression_factor_postflop ≥ 3.0` excludes TAGs (typical AF_pf ~
  1.5–2.0) and `cbet_attempt_rate ≥ 0.70` excludes ABCBot's
  conservative postflop play.

## Prerequisites

Two plumbing fixes were required for the gate to fire correctly on
ManiacBot in HU. **Both shipped on 2026-05-16** as part of the
empirical-baseline dry-run; this section documents the fixes so the
plan's dependency graph is explicit.

1. **`cbet_attempt_rate` / `postflop_seen_as_pfr_count` surfaced to
   `AggregatedOpponentStats`** (`poker/strategy/exploitation.py:454`).
   Previously these fields existed on `OpponentTendencies` (per Phase
   8.1a) but were never propagated through the four aggregator
   construction sites. The induce_override gate reads from
   `AggregatedOpponentStats`, so without the surface fix it would
   have read the dataclass defaults (`0.5` / `0`) regardless of
   observed behavior.

   Touched sites:
   - `_build_aggregate_from_single` (`opponent_model.py:957`)
   - `_build_aggregate_from_multi` (`opponent_model.py:987`)
   - `aggregate_from_spots` (`exploitation.py:1564`)
   - `_copy_stats` (`exploitation.py:1665`)
   - `TieredBotController._build_opponent_spots` (`tiered_bot_controller.py:1845`)

   Test coverage: `tests/test_strategy/test_aggregate_cbet_attempt_surface.py`.

2. **(False alarm — no fix needed)** An earlier review flagged that
   the tiered-controller construction at `run_ai_tournament.py:933`
   doesn't pass `opponent_model_manager` to the constructor. This is
   benign because `controller.opponent_model_manager = ...` is set
   for every controller post-construction at
   `run_ai_tournament.py:983`.

## Scope

**In:**

- New module: `poker/strategy/induce_override.py`
- New controller method: `TieredBotController._apply_induce_override`
- Insertion point: **before** `_apply_value_override` (mirrors
  `defense_floor`'s pattern). When induce fires, value_override
  defers via its own `prior_layer_fired` check. Earlier draft put
  induce after value_override with the latter being overridden —
  that creates two OVERRIDE-class trace entries on the same
  decision and complicates attribution. Note: `_apply_bluff_catch_override`
  operates on disjoint hand classes (nuts vs marginal/weak), so
  there's no overlap to worry about there.
- Trace plumbing: `InterventionTrace` emit, `_fill_prior_action_source`
  pass-through, snapshot fields under `induce_*` keys
- Ablation key: `('induce_override', 'default')` in `disable_rules`
- Experiment config: `experiments/configs/induce_override_phase_a.json`
  running the four-matchup ablation matrix (ManiacBot, CaseBot,
  GTO-Lite, TAG) with rule-on vs rule-off arms
- **Mixing strategy during Phase A validation**: fixed 1.00 call /
  0.00 raise. Maximizes signal during ablation testing where
  villains are static. The unexploitability mix (0.85/0.15) is
  Phase B work once adaptive opponents enter the matrix. See
  [Mixing Strategy](#mixing-strategy) for the rationale.

**Out (Phase B and later):**

- New stat: `barrel_frequency` / `barrel_opportunities` on
  `OpponentTendencies` — proper signal vs today's AF_pf × cbet_attempt
  proxy
- **Confidence-scaled mixing** — call probability ramps with signal
  strength (sample size + AF magnitude over threshold). Phase B
  blocker.
- Open-spot IP induce (the OOP-check-then-barrel exploit). Useful but
  rare vs current test villains; needs `TrapBaitBot` to validate.
- OOP induce (check-raise tech) — IP only for Phase A
- River induce — gate explicitly excludes river
- Personality-aware intensity (e.g. a LAG hero traps less than a TAG
  hero) — Phase A applies the same redistribution regardless of
  archetype
- A "they noticed we're trapping" counter-adaptation loop
- `strong_made` (non-nuts) inclusion — Phase A is nuts-only. Phase B
  extends downward once Phase A's leak surface is understood.

Keeping the surface narrow is deliberate. If the narrow rule doesn't
clear (H1), the wider versions won't either. If it does, Phase B
widens incrementally with the same testable-hypothesis framing.

## Design Sketch

### Rule gate (all must hold)

| Check | Source | Value |
|---|---|---|
| Facing a bet | `'fold' in valid_actions` | True |
| Hero in position | `node.position == 'IP'` (or HU defender) | True |
| Street is flop or turn | `node.street in {'flop', 'turn'}` | True |
| Hand is the nuts | `hand_class == 'nuts'` AND `node.nut_status == 'nuts'` | True |
| Board is dry | `len(node.danger_flags) <= 1` | True |
| Effective stack ≥ 40 BB | `_compute_effective_stack_bb(...) >= 40` | True |
| Opponent is a barreler proxy | `stats.aggression_factor_postflop >= 3.0` AND `stats.cbet_attempt_rate >= 0.70` | True |
| Sample sufficient | `stats.hands_observed >= 10` AND `stats.postflop_seen_as_pfr_count >= 10` | True |
| Opponent is NOT a station | `not _is_passive_with_jams(stats)` AND `not _is_hyper_passive(stats)` | True |
| Not facing an all-in | `not decision_context.facing_all_in` (no future streets to extract on) | True |
| Standard psychology gate | `adaptation_bias * tilt_factor > GATING_FLOOR` (same as value_override) | True |
| Ablation hook | `('induce_override', 'default') not in disable_rules` | True |
| Multiway gate | `active_opponent_count == 1` (HU only for Phase A) | True |

`prior_layer_fired` IS a blocker (by design — induce inserts before
value_override, so the only "prior layer" would be one that fired
earlier in the pipeline, e.g. exploitation offsets). When induce
fires, value_override checks the trace and defers — same pattern
defense_floor uses.

### Effect

Redistribute the existing `modified_strategy.action_probabilities`,
preserving the action keys (same invariant as value_override):

- **`has_call`**: `call = 1.00`, all other actions to 0. Phase A
  validation runs with a pure smooth-call line — none of the
  matchup villains adapt, so any non-zero raise frequency would
  just dilute the per-firing EV signal we're trying to measure.
- **`has_raise` only (no call — pathological, shouldn't happen
  facing a bet)**: leave alone, `fired=False`,
  `reason_code='facing_bet_no_call_action'`.

Phase B switches the redistribution to **0.85/0.15** (or
confidence-scaled — see Phase B Item 2) once adaptive opponents are
in the matrix. The 0.15 raise frequency is the unexploitability
tax: a perfectly observant opponent can't pure-fold turn after our
call because we still raise 15% of the time. Testing this property
in Phase A would require an adapting villain, which we don't have.

### Pipeline integration

Insertion in `_get_postflop_decision` (after 6a.5b bluff_catch):

```python
# 6a.5c  ← NEW: induce override (post bluff_catch, before defense_floor)
from .strategy.induce_override import apply_induce_override
prior_layer_fired = (
    value_override_trace.fired or bluff_catch_trace.fired
)
modified_strategy, induce_trace = apply_induce_override(
    modified_strategy,
    decision_context=outer_decision_context,
    stats=stats_for_induce,  # facing-aggressor or aggregate; see selector note
    node=node,
    hand_strength=hand_strength,
    effective_stack_bb=effective_stack_bb,
    active_opponent_count=active_count,
    adaptation_bias=adaptation_bias,
    tilt_factor=tilt_factor,
    prior_layer_fired=prior_layer_fired,  # informational, not blocking
    disable_rules=getattr(self, "disable_rules", frozenset()),
)
induce_trace = _fill_prior_action_source(
    induce_trace, self._last_intervention_trace,
)
self._last_intervention_trace.append(induce_trace)
```

**Stats selector**: facing a bet, the right opponent set is *the
aggressor*, not the aggregate. The controller already builds
`OpponentSpot` instances and has `select_primary_aggressor()` — use
the facing-aggressor stats so we don't dilute the barreler signal by
averaging with passive overcallers. Pattern is identical to bluff_catch
override's selector.

### Mixing Strategy

> *Once recognized, do we always trap or trap X% of the time scaled
> by confidence?*

**Phase A: fixed mix (0.85 call / 0.15 raise) on a binary gate.** Not
confidence-scaled.

Rationale:
- Matches the existing rule-precedent. `value_override` and
  `bluff_catch_override` both fire-or-don't with a fixed
  redistribution. Phase A consistency makes ablation interpretation
  cleaner.
- A confidence ramp adds a second knob (gate threshold + ramp curve)
  that we'd be tuning simultaneously with the rule's basic premise.
  If H1 misses, we can't disentangle "wrong gate" from "wrong ramp."
- The 0.15 raise frequency *is* the unexploitability mix — but Phase
  A's test villains don't adapt, so this property isn't actually
  tested here. It's baked in now so Phase B doesn't have to retrofit
  it when we add adaptive opponents.

**Phase B: confidence-scaled mixing.** The proper signal would be
`barrel_frequency` (street-resolved), and the call probability should
ramp with signal confidence on two axes:
- **Sample confidence**: at minimum gate (10 cbet_attempt
  observations), use a softer mix (e.g. 0.70/0.30). At high sample
  (50+), tighten toward 0.90/0.10.
- **Signal magnitude**: linear ramp on `barrel_frequency` between
  0.60 (no trap) and 0.85 (full trap).

The existing `compute_pattern_intensity` function in
`exploitation.py:792` is the model — it already does
`_ramp(value, start, end)` × `sample_confidence(count)` for the
hyper-aggressive offset magnitudes. Phase B reuses that pattern.

Why not just ship the confidence ramp now? Two reasons:
1. The proxy (`AF_pf × cbet_attempt`) is too coarse to support a
   meaningful ramp — both signals plateau quickly. The real
   precision comes with `barrel_frequency`, which is Phase B.
2. Adding the ramp doubles the surface area of bugs we have to
   diagnose if Phase A misses. Ship simple, prove the premise,
   then add nuance.

### Snapshot fields

For Mode 1 (shadow-eval) replay, write to `_last_pipeline_snapshot`:

- `induce_eligible` (bool — did all non-`prior_layer_fired` gates pass?)
- `induce_fired` (bool — did the rule actually replace strategy?)
- `induce_superseded_value_override` (bool — was `value_override` fired
  on this same decision before induce overrode it?)
- `induce_barreler_proxy` (dict: `{af_pf, cbet_attempt, hands_observed, cbet_attempt_count}`)
- `induce_node_inputs` (dict: `{street, position, danger_flag_count, hand_class, nut_status, effective_stack_bb, facing_bet}`)

### Trace shape

`InterventionTrace` fields per existing convention:

- `layer = 'induce_override'`
- `rule_id = 'default'`
- `layer_order = layer_order_for('induce_override')` — add to
  `intervention_trace.py` ordering between `bluff_catch` and
  `defense_floor`
- `operation = InterventionOperation.OVERRIDE.value`
- `effect = 'distribution_replaced'`
- `effect_size = l1_distance(before, after)`
- `reason_code ∈ {'induced_flop_facing_bet', 'induced_turn_facing_bet',
  'gated_off_<gate_name>', 'facing_bet_call_only', 'facing_bet_no_call_action'}`

## Leaks & Mitigations

The facing-bet smooth-call exploit opens specific leak surfaces.
Walking each one and its mitigation:

| Leak | Severity | Phase A mitigation | Mitigated by existing system? |
|---|---|---|---|
| **Turn draw completes** — smooth-call gives villain a free river card on what looked like a dry board | High if missed | `danger_flags <= 1` gate AND `hand_class == 'nuts'` (nuts means the made hand is immune to most one-card improvements). Phase A excludes `strong_made` for this reason. | Partial — `danger_flags` exists, but the binary `<= 1` gate is conservative; doesn't capture board-runout combinatorics |
| **Villain isn't actually a barreler** — gate proxy misfires, hero smooth-calls flop, villain checks turn | Medium | Two-axis gate (`AF_pf ≥ 3.0` AND `cbet_attempt_rate ≥ 0.70`) plus sample floors (≥10 hands, ≥10 cbet_attempts). The 0.15 raise mix is the floor — even on a misfire, we capture *some* value. Diagnostic counter `induce_followup_barrel_rate` from showdown attribution catches this empirically. | Yes for the station/passive case (`_is_passive_with_jams` and `_is_hyper_passive` exclusions reuse the existing detectors). No direct mitigation for "high AF_pf but doesn't actually barrel turns" — that's exactly the gap Phase B's `barrel_frequency` stat closes. |
| **Villain barrels turn but slows on river** | Low | We still capture flop bet + turn barrel = 2 streets, better than the 1 street we'd get from raising flop. Net positive even in this case. | Neutral — no system component changes the math |
| **Multiway intrusion** — third opponent overcalls our flop call, changes turn dynamics | High | Phase A is HU-only. `active_opponent_count == 1` gate. | Yes — gate is explicit |
| **Stations get out of the pot** — passive opponents fold to flop raise but call the smooth-call line all day | Medium | `_is_passive_with_jams(stats)` and `_is_hyper_passive(stats)` exclusions inherited from `exploitation.py`. The full station detection apparatus runs upstream and feeds our gate. | **Yes** — this is the cleanest existing-mitigation case. The station detection that gates Phase 8 `value_vs_station` and prevents `hyper_passive` over-firing is the same machinery. |
| **Short-stack pot-commits us into a bad turn raise** — at < 20 BB effective, smooth-calling flop leaves an awkward 1.5x-pot turn that we can't fold | Medium | `effective_stack_bb >= 20` gate. Below 20 BB the existing short-stack heuristics layer suppresses non-jam raises anyway, so the post-induce decision tree is constrained. | Yes — `apply_short_stack_heuristics` already exists and runs after induce in the pipeline |
| **The clamp doesn't catch us** — Phase 7.5's three-tier clamp bounds *offsets*, not *overrides*. Induce is an OVERRIDE so it bypasses the clamp. | Medium | Same precedent as `value_override` and `bluff_catch_override` — both are OVERRIDE-class interventions and both ship without clamp protection. The trace + ablation matrix is the runtime mitigation: if induce regresses, disable it per-decision via `disable_rules`. | Partially — ablation hook exists, but the *behavioral* safety net (clamp) doesn't apply here |
| **Information leak to adaptive opponents** — once a future TieredBot or LLM-driven opponent observes the smooth-call line, they check turn instead of barreling | Low for Phase A (no adaptive opponents in matrix); High for prod | The 0.15 raise frequency is the unexploitability tax. Even a perfectly adapting opponent can't pure-fold turn after our flop call because we still raise 15% of the time. | No — this is a Phase B+ concern |
| **River exclusion gap** — if villain barrels flop + turn + river, we still need to call river, not raise. Phase A excludes river from the induce gate, but does the decision tree downstream behave on river? | Low | River decisions on this line fall through to the standard pipeline (`value_override` will fire 50/50 call/raise with nuts on river facing-bet, which is correct — no streets to extract). | Yes — standard pipeline already handles river correctly |

**Net leak assessment:** The two leak surfaces *not* mitigated by
existing systems are (a) the AF_pf-without-actual-barreling case
(Phase B's `barrel_frequency` closes it) and (b) the
clamp-bypass-via-override case (precedent-justified, ablation-mitigated
at runtime). Both are acceptable Phase A risks given the diagnostic
counters can detect them.

The single most important diagnostic is **`induce_followup_barrel_rate`**:
showdown-attributed count of "villain actually barreled the turn
after our smooth-call." If this is below ~0.4 vs ManiacBot in the
Phase A run, the proxy is wrong and (H1) almost certainly misses.
We'd know *why* it missed, which lets us jump straight to Phase B
instead of retuning blindly.

## Validation Plan

### Experiment matrix

`experiments/configs/induce_override_phase_a.json`:

| Arm | Hero | Villain | Rule | Hands | Seeds |
|---|---|---|---|---|---|
| A1 | TieredBot | ManiacBot | OFF (ablated) | 1000 | 8 |
| A2 | TieredBot | ManiacBot | ON | 1000 | 8 |
| B1 | TieredBot | CaseBot | OFF | 1000 | 8 |
| B2 | TieredBot | CaseBot | ON | 1000 | 8 |
| C1 | TieredBot | GTO-Lite | OFF | 1000 | 8 |
| C2 | TieredBot | GTO-Lite | ON | 1000 | 8 |
| D1 | TieredBot | ABCBot (TAG baseline) | OFF | 1000 | 8 |
| D2 | TieredBot | ABCBot (TAG baseline) | ON | 1000 | 8 |

Total: **n = 8000 hands per arm**, 4x the earlier draft (n=2000 was
inside the HU pot-variance noise floor — see Risk #6 in the prior
draft, now upgraded to a baseline requirement).

Use `disable_rules = frozenset({('induce_override', 'default')})` for
the OFF arms — same ablation mechanism the Phase 7.6 matrix uses, no
new infrastructure needed.

Heads-up only. Multiway adds noise that the Phase A gate isn't tuned
for and Phase A is HU-only by gate construction.

### KPI computation

Per arm:
- `bb_per_100 = (final_chips - starting_stack) / big_blind / hands * 100`
- Aggregate across seeds; report mean ± stderr.

Phase A passes iff all three hold simultaneously:

- `bb100(A2) - bb100(A1) >= +5` **(H1)**
- `bb100(B2) - bb100(B1) >= -2` AND `bb100(C2) - bb100(C1) >= -2` AND `bb100(D2) - bb100(D1) >= -2` **(H2)**
- For matchups C and D, `induce_fire_rate < 0.01` from snapshot
  counters **(H3)**

### Diagnostic counters

Beyond the headline KPI, log per arm:

- `induce_eligible_count` — gate-pass count regardless of `prior_layer_fired`
- `induce_fired_count` — actual distribution replacements
- `induce_superseded_value_override_count` — induce displaced
  value_override on the same decision
- `induce_action_flip_count` — primary action changed by induce
- `induce_followup_barrel_count` — induce fired, hand reached
  next street, villain bet next street
- `induce_followup_check_back_count` — induce fired, villain checked
  next street (the leak signal)
- `induce_followup_barrel_rate = induce_followup_barrel_count / induce_fired_count`

The followup-barrel rate is the **direct empirical check** on the
poker premise (see Leaks table above).

## Risks & Failure Modes

1. **The barreler proxy doesn't actually predict barreling.**
   `aggression_factor_postflop` aggregates bet/raise/all-in vs call,
   summed across streets — a player who jams a lot or check-raises a
   lot can inflate this without firing actual turn barrels. Mitigation:
   diagnose via `induce_followup_barrel_rate`. If it fails vs
   ManiacBot, Phase A is dead and we move directly to Phase B's
   `barrel_frequency` stat.

2. **`value_override` keeps eating the spot first.** Value override's
   gate is `hyper_aggressive` which fires at lower AF/all-in than
   induce's gate. We expect `induce_superseded_value_override_count`
   to be *high* against ManiacBot — that's the rule working as
   designed. If it's near zero, value_override isn't firing on the
   spots we expect, which means our mental model of the pipeline is
   off and the gates need reviewing before the experiment is
   informative.

3. **Mixed-strategy raise still leaks information.** 0.15 raise from
   nuts on dry board IP is *more* concentrated on value than the
   solver baseline would be at that frequency. A skilled opponent
   could exploit by folding turn after our flop raise. Acceptable
   for Phase A (none of our test villains adapt that way); a known
   leak for Phase B to address with frequency tuning if a learning
   villain joins the matrix.

4. **TAG/nit fire rate non-zero.** The `cbet_attempt_rate >= 0.70`
   gate should keep this near zero — typical TAG cbets ~55–65% — but
   small sample effects in the first few hands could spike it. The
   ≥ 10 cbet_attempt_count sample floor is the guardrail; verify it's
   doing its job by checking C/D arm fire rates.

5. **`nut_status` definitions don't line up with what the rule needs.**
   The induce rule assumes `nut_status == 'nuts'` means "can't get
   outdrawn meaningfully." If the postflop classifier's nut_status
   includes hands that *can* be outdrawn (e.g. a set on a one-flush
   board), the danger-flags gate is the only thing protecting us. A
   pre-implementation read of `node.nut_status` semantics is required
   to confirm this. (Open question.)

6. **Per-hand BB variance dwarfs the +5 bb/100 signal.** At 500 hands
   per arm with 4 seeds (n=2000), a single ~200 BB cooler hand
   contributes 10 bb/100 to its arm. If induce fires 20× per arm and
   contributes +60 BB total, one bad cooler can flip the (H1) sign.
   Mitigation: report stderr alongside means; if (H1) is +5 bb/100 ±
   8 bb/100, declare inconclusive and expand sample to 1000 hands ×
   8 seeds before re-running.

## Decision Points

After the experiment runs:

| Outcome | Decision |
|---|---|
| All of (H1), (H2), (H3) hold | Ship Phase A as-is. Open Phase B follow-ups: confidence-scaled mixing + `barrel_frequency` stat + `strong_made` inclusion + OOP check-raise tech. |
| (H1) holds, (H2) breaches on one matchup by < 4 bb/100 | Investigate which gate failed; tighten and re-run. Don't ship until clean. |
| (H1) holds, (H2) breaches > 4 bb/100 anywhere | Proxy is firing on non-barrelers. Kill Phase A, jump to Phase B's proper barrel stat. |
| (H1) misses but `induce_followup_barrel_rate` > 0.6 vs ManiacBot | Trap works but the redistribution is wrong. Try (0.95/0.05) or (0.70/0.30) and re-run. One retune attempt; if that misses too, kill. |
| (H1) misses and `induce_followup_barrel_rate` < 0.4 vs ManiacBot | The premise is wrong: villain doesn't barrel after our smooth-call. Don't retune — investigate villain behavior before any further work. |
| (H3) breach (TAG/nit fire rate > 2%) | Sample floor is too low or AF_pf threshold too low. Tighten gates and re-run before scoring (H1)/(H2). |
| Sample variance too high to call (any KPI ± stderr crosses zero) | Expand to 1000 hands × 8 seeds, re-run. |

## Parallel Work (Not a Phase A Blocker)

Useful to build alongside Phase A but not required for it:

- **TrapBaitBot** — a new `rule_bot` strategy (`_strategy_trap_bait`
  in `poker/rule_strategies.py`) that checks flop OOP ~70% of the
  time, then barrels turn and river hard. Pattern after
  `_strategy_maniac`. This is the right opponent for the *open-spot*
  IP induce rule that Phase B might revisit. Build only if there's a
  reason to test the open-spot scope; ManiacBot already covers Phase
  A's facing-bet scope.

## Open Questions

- Does `node.nut_status == 'nuts'` actually guarantee outdraw-immunity,
  or can it include vulnerable hands? Required pre-implementation
  read of the postflop classifier. Worst case: tighten the gate to
  exact `made_tier` checks instead of relying on `nut_status`.

- Does the `facing-aggressor` stats selector (instead of aggregate)
  meaningfully change the gate hit rate in HU? In HU these should be
  identical (only one opponent). Worth a unit test asserting
  equivalence in the HU case.

- How does this interact with `value_override`'s `facing_all_in`
  branch? The Phase A gate excludes `facing_all_in` explicitly. A
  unit test asserting mutual exclusion is cheap and worth having.

- Should induce fire on the **turn** with a different threshold than
  the flop? A turn smooth-call gives villain one more street to
  barrel, but the river-equity risk on draw-completing cards is
  real. Phase A treats flop and turn symmetrically; the diagnostic
  counters will surface whether one is contributing more than the
  other (split `induce_fired_count` by street).

## Related Plans

- [Phase 6 — Opponent Exploitation](PHASE_6_OPPONENT_EXPLOITATION.md)
- [Phase 7.5 — Adjustment Layer Widening](PHASE_7_5_ADJUSTMENT_LAYER_WIDENING.md)
- [Phase 7.6 — Intervention Trace](PHASE_7_6_INTERVENTION_TRACE.md)
- [Phase 8.1 — Tracking & Hyper-Passive](PHASE_8_1_TRACKING_AND_HYPER_PASSIVE.md) — adds `cbet_attempt_rate` (Phase A depends on this being shipped)
- [TieredBot Decision Quality](TIEREDBOT_DECISION_QUALITY.md)
