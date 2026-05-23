---
purpose: Pickup-state handoff for the induce_override Phase B work — what's shipped, what's next, key findings that aren't in commit messages or per-item plans, and how to validate further changes.
type: guide
created: 2026-05-22
last_updated: 2026-05-23
---

# Induce Override — Handoff

A fresh context should be able to read this doc and continue Phase B
Items 4–6 without reconstructing session memory. Companion docs:

- [Phase A plan](INDUCE_OVERRIDE_PHASE_A.md) — shipped, validated
- [Phase B plan](INDUCE_OVERRIDE_PHASE_B.md) — Items 1–3 shipped, 4–6 pending

## Current state (branch: `hybrid-ai`)

| Item | Status | Commit | Validation |
|---|---|---|---|
| Phase A induce_override | shipped | `500ac3ce` | 78% followup-barrel (7/9 fires) |
| `cbet_attempt_rate` aggregator surface fix | shipped | `c71f49c8` | tests + dry-run confirmed |
| Persistence decoupling | shipped | `f63978e3` | postflop trace coverage 0% → 100% |
| Experiment infra (player_types / disable_rules / skip_equity) | shipped | `e292cf3c` | 9× sim speedup; ablation matrix works |
| Phase B Item 1 (`barrel_frequency` stat) | shipped | `a4f19bb4` | converges to 0.94 vs Maniac |
| Phase B Item 2 (barrel gate + scaled mixing) | shipped | `cce12ca8` | scaled mix produces [0.78, 0.90] call probs; followup-barrel 80% on high-conf fires |
| Phase B Item 3 (`strong_made` inclusion) | shipped | `056e3160` | gate correct per tests; empirical fire rate ~0 at 1000-hand scale |
| Phase B Item 4 (open-spot IP induce) | shipped | this session (exp 75) | TrapBaitBot + new stat + branch landed together. 144 tests passing. Full matrix (88 tournaments × 1000 hands): vs TrapBaitBot lift +1.43 bb/100 (+0.32σ, H1 missed); leak floor breached vs gtolite (−4.64) and abcbot (−5.65) but within noise (σ < 2); CaseBot saw unexpected +5.53 bb/100 lift (worth a second matrix run if you can spare the API budget). Open-spot branch fired 0× across all 88 tournaments — correctness widening only. |
| `phase-1` merge (relationship + cash + polarization) | shipped | `d29ddf37` | see "Known issues" |

## Active question (no decision yet)

When this handoff was written, the user was choosing between three
follow-up directions. They picked **Phase B continuation (Item 4/5)**
as the low-risk path. The other two are still on the table:

1. **6max strategy table calibration** — biggest absolute EV gain
   but biggest authoring work. See [Big findings](#big-findings).
2. **Personality distortion EV leak** — biggest EV-per-hour but
   changes bot feel. See [Big findings](#big-findings).
3. **Phase B Items 4–6** — currently active path.

If a fresh agent picks up the alternative directions, see the
relevant sections below for context.

## Phase B Item 4 — Open-spot IP induce (SHIPPED)

**Status: shipped this session.** Three pieces landed together:

1. `_strategy_trap_bait` in `poker/rule_strategies.py` + registry
   entries in `BUILT_IN_STRATEGIES` (`'trap_bait'`) and `CHAOS_BOTS`
   (`TrapBaitBot`).
2. `flop_check_then_barrel_rate` stat plumbing across `cbet_detector.py`,
   `opponent_model.py`, `exploitation.py`, and `memory_manager.py` —
   mirrors the `barrel_frequency` pattern (commit `a4f19bb4`).
3. Open-spot branch in `apply_induce_override` (and helper
   `should_apply_open_spot_induce`) with dispatch at the top based on
   `has_check and not has_fold`. Flat 0.70/0.30 check/raise
   redistribution; reads the new stat; trace uses `effect='check_back'`
   and `reason_code='induced_{street}_open_spot'`.

**Validation outcomes:**

*Smoke* (`induce_override_phase_b_item4_smoke.json`, 1 tournament × 500 hands):
- 144 tests passing (16 new induce_override + 14 new flop-check-barrel
  + 11 trap-bait + existing suite still green)
- TrapBaitBot first-to-act flop: 62% check, 38% raise (target ~70/30,
  within noise for n=29)
- TrapBaitBot turn after check-through: 67% bet/raise, 33% check
- New stat detector fires 15× in 500 hands HU

*Full ablation matrix* (`induce_override_phase_b_item4_full.json`,
experiment 75 — 88 tournaments × 1000 hands × 5 villains × 2 arms):

| Villain | OFF | ON | Lift | σ | Hypothesis |
|---|---|---|---|---|---|
| TrapBaitBot | +3.41 | +4.84 | +1.43 | +0.32 | H1 MISS (target +5) |
| ManiacBot | +0.41 | −1.47 | −1.88 | −0.35 | H2 within |
| CaseBot | −12.15 | −6.61 | **+5.53** | +1.44 | H2 — unexpected lift |
| GTO-Lite | +4.23 | −0.41 | −4.64 | −0.81 | H2 MISS (within noise) |
| ABCBot | +7.45 | +1.81 | −5.65 | −1.25 | H2 MISS (within noise) |

- Open-spot branch fired 0× across all 88 tournaments / 88K hands
- Facing-bet branch fired 9× vs TrapBait, 14× vs Maniac in 8K hands
  per arm
- Verdict: correctness widening only. Per handoff Finding 3 — Phase B
  items don't move bb/100 above the noise floor at this matrix scale.

Analysis script: `scripts/analyze_item4_matrix.py <experiment_id>`.

**Tunables shipped:** `MIN_FLOP_CHECK_BARREL_FREQUENCY=0.55`,
`MIN_FLOP_CHECK_BARREL_OPPORTUNITIES=5`,
`OPEN_SPOT_CHECK_PROBABILITY=0.70`. The 0.55 frequency threshold is
lower than the facing-bet branch's 0.60 because TrapBaitBot-class
opponents barrel 65-80% after check-through and the trap-bait pattern
is more readily detectable than the facing-bet barrel pattern.

## Phase B Item 5 — OOP induce (check-raise tech) (pickup state)

**Spec quality: intentionally light.** The plan flags this as
"defer until Items 1–4 are validated. The poker premise is correct
... but the engineering surface is large enough that it deserves its
own design pass."

**Design-doc checklist** (what a fresh agent needs to resolve before
writing code):

1. **Two-decision state.** All current induce variants are
   single-decision; check-raise is two (flop check → turn check-raise,
   or flop check-call depending on villain's response). Pick a state
   model:
   - Per-hand controller state (new field on `TieredBotController`)
   - Trace-driven lookup (recover prior intent from
     `_last_intervention_trace`)
   - Pure-functional re-derivation (same gate inputs → same intent
     each decision)
2. **Stat reuse vs new.** The exploit lever is "villain cbets often
   after we check OOP." `cbet_attempt_rate` (Phase 8.1a) already
   measures this directionally. Decide whether to reuse it or build a
   more specific `cbet_after_oop_check_rate` (parallel to Item 4's
   `flop_check_then_barrel_rate`).
3. **Bluff/value protection.** Pure check-raise from nuts is too
   obvious. Need mixing in check-fold (weak) and check-call (marginal).
   Decide: is balance protection inside the induce layer or a separate
   OOP strategy module that induce calls into?
4. **Redistribution mechanics.** What does "check-raise mass" look
   like as a probability distribution? Two candidates:
   - `check=0.90, raise_50=0.10` on flop + turn override to favor
     raises
   - `check=1.00` flop + override turn distribution after the fact
5. **Gate scope.** Same per-class hand gates as Items 2/3/4 (nuts +
   strong_made, dry boards), or broader for OOP because OOP play needs
   more disguise? Also: river check-raise vs value bet is a different
   exploit — include or exclude?
6. **Testable hypothesis.** Item 5 needs a target opponent (a
   CbetSpammerBot or similar — fires cbet whenever it's PFR regardless
   of board) and concrete success criteria (≥ +X bb/100 lift,
   ≤ −Y leak elsewhere).

**Effort estimate.** 1-2 hours to think through the two-decision state
mechanism, another 1-2 hours on the bluff/value protection scope.
Implementation effort after the design doc is roughly comparable to
Item 4 (~8-12 hours).

**Recommendation.** Don't pull Item 5 in this handoff cycle. Write a
fresh design doc using the checklist above when ready.

## Phase B Item 6 — Personality-aware intensity (DEFERRED 2026-05-23)

**Status: deferred.** Item 4's full ablation matrix (88 tournaments ×
1000 hands, experiment 75) confirmed the fire rate is insufficient for
archetype-specific tuning:

- 9 facing-bet fires per 8000 hands vs TrapBaitBot
- 14 facing-bet fires per 8000 hands vs Maniac
- 0 facing-bet fires vs CaseBot, GTO-Lite, ABCBot
- 0 open-spot fires across all 88 tournaments

With ~5-23 fires per arm and 6 archetypes to compare, the per-archetype
sample is far below what's needed to detect a differential larger than
the ~5-10 bb/100 stderr noise floor.

**Revisit conditions:**

1. A contrived-state harness exists that forces the IP free-to-act
   strong-hand spot synthetically (would require non-natural deals or
   a "spot generator" running outside the normal game loop).
2. A 10× sample bump on the matrix scale is feasible (~800 tournaments
   × 1000 hands × 5 villains × 2 arms × 6 archetypes — currently
   infeasible at ~$30/run for the smaller matrix).
3. The induce gate is loosened to fire more often (would defeat the
   selectivity Phase A and B fought for, but might be worth a "Item 6
   prep" arm that intentionally widens the gate to generate samples).

**Original spec preserved in `INDUCE_OVERRIDE_PHASE_B.md` §"Item 6"** —
`ARCHETYPE_INDUCE_SCALE` dict skeleton with placeholder multipliers
(nit/tag/rock/station=1.0, lag=0.6, maniac=0.4). Pick up from there
when the sample-size problem is solved.

## Big findings (not in plan docs yet)

These came out of the session's investigation work. They're not yet
captured in any individual plan doc but they shape what Phase B+
should focus on.

### Finding 1: Personality distortion costs 8–10 bb/100 vs CaseBot

Empirical (experiments 66 vs 68, 67 vs 69):

| Format | TAG distorted | Baseline (no distortion) | Distortion cost |
|---|---|---|---|
| HU vs CaseBot | −9.13 bb/100 | −0.95 bb/100 | **8.18 bb/100** |
| 6max vs 5 CaseBots | −19.46 bb/100 | −9.70 bb/100 | **9.76 bb/100** |

Setting `skip_personality_distortion=True` on TieredBot (via the
runner flag added in this session — see
`experiments/run_ai_tournament.py:951`) recovers most of this. The
personality distortion exists for character/feel reasons, but the EV
cost is large and consistent across formats.

**Open design question:** is it possible to keep character (dramatic
sequences, table talk, expression layer) while reducing the *action*
distortion? No decision yet. Listed as Option (b) of the three open
items.

### Finding 2: 6max strategy table is the second-biggest leak

Empirical (experiment 72, baseline vs mixed rule_bots):

| Opponent | Baseline result |
|---|---|
| CaseBot | loses (−118 vs CaseBot's gain) |
| GTO-Lite | loses (−30) |
| Baseline | beats heuristic bots overall (+7.68 net) |
| PositionBot | near-even (−4.7) |
| ABCBot | beats (+35) |
| ManiacBot | beats (+38) |

**Pattern: baseline loses specifically to the two rule_bots that
emulate solver play (CaseBot, GTO-Lite). Beats or ties the heuristic
ones.** The README at
`poker/strategy/data/postflop_strategies_README.md` literally names
the #1 leak: "Limped-pot SRP tree — biggest current leak vs CaseBot-
style opponents who limp constantly."

**Calibration roadmap (per the README):**
1. Limped-pot SRP tree (~6-10 hours manual authoring or generator)
2. 3-bet pot tree (same shape)
3. Multiway adjustment refinement (`multiway.py` is 71 lines; current
   heuristic is a simple frequency rescale)
4. 6max preflop solver replacement (weeks/months without paid solver
   tools)

Listed as Option (a) of the three open items.

### Finding 3: Phase B item-by-item fire rate is low

At natural-occurrence sampling rates, induce_override fires roughly
5–15 times per 8000-hand arm vs ManiacBot. Phase A's testable
hypothesis (≥ +5 bb/100 lift) was indeterminate at this scale —
qualitative followup-barrel rate (78%) validated the premise but
quantitative bb/100 needed either much more sample (~10× current) or
a contrived-state harness.

Items 2 and 3 didn't move the fire count meaningfully (Item 2 brought
it from 9 → 10; Item 3 added 0 in a 1000-hand smoke). **Phase B
Items in isolation are unlikely to move bb/100 above the noise floor
at the current matrix scale.**

This is fine — they're correctness improvements (better signal,
better hand-class coverage) — but it means **don't gate Phase B item
acceptance on bb/100 measurability.** The qualitative empirical
checks (followup-barrel rate, gate selectivity) are the real test.

## Validation infrastructure

### Running an ablation matrix

The Phase A config is the canonical example:
```bash
docker compose exec backend python -m experiments.run_from_config \
    experiments/configs/induce_override_phase_a.json
```

Expected runtime: ~60–70 min for 72 tournaments × 1000 hands with
`expression: false` and `skip_equity_in_analysis: true` (both set
automatically by the runner for tiered controllers).

### Per-arm disabling

The runner reads `disable_rules` from `player_types[name]`:
```json
"player_types": {
  "TieredHero": {
    "type": "tiered",
    "expression": false,
    "disable_rules": [["induce_override", "default"]]
  }
}
```

Ablated arm = same config minus the `disable_rules`. The `_w()`
helper in the runner picks both control + variant up correctly.

### Pulling fire counts after a run

Standard pattern (substitute `experiment_id`):
```python
cur.execute('SELECT game_id FROM experiment_games WHERE experiment_id = ?', (eid,))
gids = [r['game_id'] for r in cur.fetchall()]
cur.execute(f'''SELECT intervention_trace_json FROM player_decision_analysis
              WHERE player_name = ? AND intervention_trace_json IS NOT NULL
                AND game_id IN ({','.join('?'*len(gids))})''',
            ['TieredHero'] + gids)
for r in cur.fetchall():
    for t in json.loads(r[0]):
        if t.get('layer') == 'induce_override' and t.get('fired'):
            ...  # inspect t['inputs'], t['reason_code']
```

### Followup-barrel diagnostic

The key empirical check from Phase A's plan (decision-points table):
≥ 70% followup-barrel rate → ship; < 40% → kill. Pattern is:

1. Find each fire's `(game_id, hand_number, phase)`
2. Pull the hand's `actions_json` from `hand_history`
3. Find villain's first action on the NEXT street
4. If `bet`/`raise`/`all_in` → barrel; if `check` → no barrel

See the session's run for code (referenced in commit messages but
not extracted into a script yet — would be a nice cleanup).

## Known issues / gotchas

### Pre-existing test failure (not introduced by us)

`tests/test_strategy/test_tiered_bot_exploitation.py::
TestApplyExploitationNoOp::test_no_offsets_returns_strategy_unchanged`
fails on pristine `origin/phase-1` (verified). phase-1's vvs/steal/
bluff_reduction intensity computations fire on cold-start
(`hands_observed=5`) with tiny shifts (~0.0007), breaking the
`result is base` identity assertion. The intent of the test (no
significant exploit on cold-start) still holds; the assertion is
too strict for the new behavior.

Documented in the `d29ddf37` merge commit. Worth a follow-up to
either loosen the assertion or gate the intensities on
`hands_observed`.

### Sample-size noise floor

At n=8 tournaments × 1000 hands HU, bb/100 stderr is ~5–10 bb/100.
Anything below ~5 bb/100 in a single matrix isn't statistically
meaningful. Phase B items individually move bb/100 by single digits;
combining 2–3 items + a 4× sample bump (n=32 tournaments) is the
threshold for clean quantitative comparison.

### `expression: false` is required for sim correctness

With `expression: true`, the decision-analysis persistence path used
to be gated on the LLM expression layer firing, which only happened
~75% of decisions. We decoupled this in `f63978e3` so trace +
snapshot now persist unconditionally. But: sim runs should still
keep `expression: false` because LLM calls add ~$15-30 cost + 10-20×
runtime for no decision-quality benefit (tiered bots don't use the
LLM for decisions).

### `reset_on_elimination: true` is the default for the matrix

Without it, tournaments end at ~100-400 hands when a player busts
instead of running the full configured 1000. The Phase A config sets
it true. New configs should match.

### `skip_equity_in_analysis: true` is set by the runner automatically

For tiered controllers, the runner sets this attribute after
construction so the experiment skips Monte Carlo equity calc.
~9× speedup. If you ever need equity in the analysis row (for
post-hoc EV computation), set this False explicitly in the config
under `player_types[name]` — but expect runs to be much slower.

## Quick-start checklist for a fresh agent

1. **Read the Phase A and Phase B plan docs.** Phase A has the
   validated baseline; Phase B is the roadmap.
2. **Read this handoff doc.** Specifically the "Big findings" section
   — they're not in any per-item plan.
3. **Confirm tests pass on `hybrid-ai` HEAD:**
   ```bash
   python3 scripts/test.py -k "test_induce or test_barrel or test_persist or test_aggregate"
   ```
   Expect ~104 passing.
4. **Run a smoke** (~5 min) to confirm the infrastructure is healthy:
   ```bash
   # use a small disposable config (1 tournament × 100 hands)
   ```
5. **Pick an item** (4 or 6 recommended; skip 5 until items 4 and 6
   are settled).
6. **Update the plan doc** with what you discover, especially if
   actual fire rates / behavior differ from the plan's predictions.

## Open questions for the next context

These are unresolved decision points the user wanted to think about,
not blocking:

- Is the goal of TieredBot competitive play, or character expression?
  Determines whether Option (b) personality-distortion reduction is
  on the table.
- Should the 6max calibration work (Option a) precede further Phase B
  items? Findings suggest the limped-pot tree is the biggest single
  EV lever, and Phase B items have low absolute impact.
- Is Item 5 (OOP check-raise) worth designing now or deferring
  indefinitely? The poker premise is correct but the engineering
  surface is real.
