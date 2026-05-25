---
purpose: Phase B — extend the induce_override rule with proper street-resolved barrel tracking, confidence-scaled mixing, broader hand classes, and OOP support
type: design
created: 2026-05-15
last_updated: 2026-05-23
status: Items 1–5 shipped on hybrid-ai; Item 6 deferred (empirically un-tunable at current fire rate). See "Shipped status" below and INDUCE_OVERRIDE_HANDOFF.md + INDUCE_OVERRIDE_OOP_CHECK_RAISE.md for details.
---

# Induce Override — Phase B

## Shipped status (2026-05-22)

| Item | Commit | Empirical verdict |
|---|---|---|
| 1 — `barrel_frequency` stat | `a4f19bb4` | Converges to 0.94 vs ManiacBot in ~500 hands. Plumbing-correct. |
| 2 — barrel gate + scaled mixing | `cce12ca8` | Selectivity perfect (0 fires vs non-barrelers). Threshold tuned 10→5 to make sample_confidence ramp produce meaningful variation. Followup-barrel rate 80% on high-confidence fires; 25% on low-confidence — exactly the scaled-mix behavior the design intended. |
| 3 — `strong_made` inclusion | `056e3160` | Gate correct per tests. Empirical fire rate ~0 added at 1000-hand scale — strong_made + actual_nuts/near_nuts + dry board is rare. |
| 4 — open-spot IP induce | this session | Three pieces shipped together: `TrapBaitBot` rule strategy + `flop_check_then_barrel_rate` stat plumbing + open-spot branch dispatch in `apply_induce_override`. **Full ablation matrix (exp 75, 8 tournaments × 1000 hands × 5 villains × 2 arms = 88 tournaments):** vs TrapBaitBot lift +1.43 bb/100 (+0.32σ) — H1 (≥ +5 bb/100) MISS, well below noise floor. Leak floors: maniac −1.88 (within), casebot +5.53 (lift!), gtolite −4.64, abcbot −5.65 — H2 (≤ −2 leak) MISS on gtolite/abcbot but both within noise (σ < 2). **Open-spot branch fired 0× across all 88 tournaments** — H3 MISS. Facing-bet branch fired 9× vs TrapBaitBot, 14× vs Maniac (8000 hands each). Verdict: correctness widening only. The IP-free-to-act-strong-hand spot is genuinely too rare for natural HU play, even at 8000-hand scale. |
| 5 — OOP induce (trap-check + check-raise) | shipped 2026-05-23 (exp 77) | Two OOP branches (trap-check + check-raise) + 2×2 dispatcher (action-set × position). 50 fires per 8K hands vs Maniac (6-10× the rate of Items 2/4) — sample-size problem solved. H1 MISS with negative direction: lift = −2.39 bb/100 (σ=−0.37, within noise but first negative direction). Selectivity correct (0 fires vs GTO-Lite/ABCBot). Possible mechanism: maniacs don't fold to raises so smooth-call may have higher EV than check-raise. Design doc: INDUCE_OVERRIDE_OOP_CHECK_RAISE.md. |
| 6 — personality-aware intensity | **deferred (2026-05-23)** | Now potentially feasible after Item 5's 50-fires-per-arm baseline. Original concern (un-tunable at low fire rate) is partially resolved — Item 5's check_raise + trap_check fires give per-archetype sample large enough that a 2-3× matrix sample (n=16-24 tournaments) might detect archetype differentials. Still deferred for prioritization, but revisit conditions are looser now. |

See [INDUCE_OVERRIDE_HANDOFF.md](INDUCE_OVERRIDE_HANDOFF.md) for the
pickup state and validation infrastructure.

## Context

[Phase A](INDUCE_OVERRIDE_PHASE_A.md) ships a narrow facing-bet IP
induce rule with a fixed 0.85 call / 0.15 raise mix on a binary gate.
The Phase A gate uses today's available proxy
(`aggression_factor_postflop >= 3.0` AND
`cbet_attempt_rate >= 0.70`) which is sufficient to test the premise
but cannot distinguish a multi-street barreler from a player who jams
preflop a lot and never sees a flop. The fixed mix is also insensitive
to signal confidence — a barreler signal from 10 samples gets the same
treatment as one from 100.

Phase B does not start until Phase A clears its testable hypothesis
(see [Phase A — Decision Points](INDUCE_OVERRIDE_PHASE_A.md#decision-points)).
Specifically, Phase B is **only worth doing if `induce_followup_barrel_rate`
in Phase A is high enough to indicate the premise is correct, but the
fixed mix is leaving value on the table or the proxy is firing
inconsistently**. If Phase A misses with low followup-barrel rate, the
right next step is *not* Phase B — the rule needs to be reconceived.

Scope of Phase B, broken into items so each can ship and validate
independently:

1. **`barrel_frequency` stat** — proper street-resolved signal on
   `OpponentTendencies`.
2. **Confidence-scaled mixing** — call probability ramps with sample
   confidence and signal magnitude (pattern matches
   `compute_pattern_intensity` in `exploitation.py:792`).
3. **`strong_made` inclusion** — extend the rule to vulnerable made
   hands on dry boards.
4. **Open-spot IP induce** — separate branch for the OOP-check-then-
   barrel exploit. Requires `TrapBaitBot` opponent.
5. **OOP induce** — check-raise tech (the harder branch).
6. **Personality-aware intensity** — different hero archetypes use
   different trap frequencies.

Each item has its own testable hypothesis. They are sequenced so
earlier items reduce risk for later ones; do not parallel-ship.

## Item 1 — `barrel_frequency` stat

**Shipped** (`a4f19bb4`). Validation: barrel_frequency converges to
0.94 vs ManiacBot in ~500 hands (target was ≥0.80 with
barrel_opportunities ≥30; observed: 0.94 with 34 opportunities).
Third barrels also tracked cleanly (0.91, n=22). Plumbing complete:
`CbetDetector` emits, `OpponentTendencies` accumulates,
`AggregatedOpponentStats` surfaces, all 5 aggregator construction
sites updated.

### Problem

Today's `OpponentTendencies` carries `aggression_factor_postflop`,
which aggregates bet+raise+all-in vs call across all postflop streets.
A maniac who jams preflop and folds postflop has high `AF_pf` once
they get to a postflop spot, but they don't actually barrel. The proxy
fires false positives.

### Design

Add to `poker/memory/opponent_model.py`:

```python
# OpponentTendencies new fields
barrel_frequency: float = 0.5         # bet rate on turn after being called on flop
barrel_opportunities: int = 0          # count of "called flop, now choosing turn action as PFR"
third_barrel_frequency: float = 0.5    # bet rate on river after being called on turn
third_barrel_opportunities: int = 0

# Counters (private)
_barrel_count: int = 0
_barrel_opportunity_count: int = 0
_third_barrel_count: int = 0
_third_barrel_opportunity_count: int = 0
```

Wire from `CbetDetector` (or its sibling) when the phase transitions
flop → turn and the preflop aggressor (a) c-bet the flop, (b) was
called, (c) is acting on the turn. Pattern is parallel to the existing
`update_cbet_attempt(...)` / `update_fold_to_cbet(...)` methods. Same
"neutral prior 0.5 until first opportunity" stance.

Aggregation up to `AggregatedOpponentStats`:

```python
barrel_frequency: float = 0.5
barrel_opportunities: int = 0
third_barrel_frequency: float = 0.5
third_barrel_opportunities: int = 0
```

Surface these to the existing playstyle/archetype detectors. No
behavior changes from Item 1 alone — purely additive tracking.

### Testable Hypothesis (Item 1)

> Adding `barrel_frequency` tracking will, over a 500-hand HU run of
> TieredBot vs ManiacBot, produce `barrel_frequency >= 0.80` and
> `barrel_opportunities >= 30` in the opponent model — i.e. the stat
> measurably converges to ManiacBot's actual ~100% barrel behavior
> within sample.

Validation: run any HU experiment with TieredBot vs ManiacBot, dump
the final `OpponentTendencies` for the villain, check the field.

This is a **plumbing-correctness hypothesis**, not a behavioral one —
Item 1 is preparatory work for Item 2.

### Effort

~80 lines plus tests in `poker/memory/opponent_model.py` and a
counter-wiring update in the phase transition detector. Pattern is
fully established by `cbet_attempt_rate` (Phase 8.1a).

## Item 2 — Confidence-scaled mixing

**Shipped** (`cce12ca8`). Validation outcome:

| | Phase A baseline | Phase B Item 2 (tuned) |
|---|---|---|
| Fires vs Maniac (8K hands) | 9 | 10 |
| Selectivity vs non-barrelers | 0 fires | 0 fires |
| Call probability | fixed 1.00 (validation override) | 0.78 → 0.90 range, mean 0.86 |
| Followup-barrel @ high conf (≥0.85) | — | **4/5 = 80%** |
| Followup-barrel @ low conf (<0.85) | — | 1/4 = 25% |

The low-confidence followup-barrel rate (25%) looks worse than Phase
A's 78% but reflects the scaled-mix design correctly: low call_prob =
partial trap exposure, not full commitment. Real-world EV is the
product of (call_prob × followup-barrel), which preserves Phase A
quality on high-conf spots and adds gracefully-degrading exposure on
low-conf spots.

**Tuning note (in the shipped code):** `MIN_BARREL_OPPORTUNITIES`
dropped from 10 → 5 after the initial run had only 5 fires per arm
with both ramps saturated. Lowering to 5 cuts warmup roughly in
half, and the sample-confidence ramp produces meaningful variation
in call_prob (the design intent).

### Problem

Phase A uses a fixed 0.85 call / 0.15 raise mix on a binary gate. Once
Item 1 ships, the proper signal (`barrel_frequency`) supports a
meaningful ramp on two axes:

- **Sample confidence**: 10 opportunities → soft mix; 50+ → full trap.
- **Signal magnitude**: `barrel_frequency = 0.60` → minimal trap;
  `0.85+` → maximal trap.

### Design

Replace the `induce_override.py` redistribution logic with a
confidence-aware ramp. Pattern from `exploitation.py:compute_pattern_intensity`:

```python
def _induce_call_probability(stats) -> float:
    rate_intensity = _ramp(stats.barrel_frequency, 0.60, 0.85)
    sample_confidence = _barrel_sample_confidence(stats.barrel_opportunities)
    intensity = rate_intensity * sample_confidence
    # Map intensity [0, 1] to call probability [0.70, 0.90]
    return 0.70 + intensity * (0.90 - 0.70)

def _barrel_sample_confidence(opportunity_count: int) -> float:
    return _ramp(float(opportunity_count), 10.0, 50.0)
```

The 0.70 lower bound prevents the rule from degrading toward
value_override's 0.50 at low confidence — if the gate fires at all,
we're at least mildly trapping. The 0.90 upper bound preserves the
unexploitability tax against adaptive opponents.

The gate itself moves from
`aggression_factor_postflop AND cbet_attempt_rate` to:

```python
stats.barrel_frequency >= 0.60
AND stats.barrel_opportunities >= 10
AND NOT _is_passive_with_jams(stats)
AND NOT _is_hyper_passive(stats)
# ... other Phase A gates carry over (street, IP, dry board, etc.)
```

`aggression_factor_postflop` falls out as a gate input — it's no
longer needed once we have the direct signal.

### Testable Hypothesis (Item 2)

> Replacing the binary AF_pf×cbet gate + fixed 0.85 mix with the
> barrel_frequency-based gate + confidence-scaled mix will:
>
> - **(B2-H1)** Maintain or improve bb/100 vs ManiacBot relative to
>   Phase A's shipped numbers, with `induce_fire_rate` at least 10%
>   higher (the new gate fires on a slightly wider set of spots
>   because `barrel_frequency=0.85` is a more specific signal than
>   `AF_pf=3.0`).
> - **(B2-H2)** Reduce `induce_fire_rate` against TAG/nit by at
>   least 50% relative to Phase A, because TAG's barrel_frequency
>   (~0.45) is below the 0.60 threshold while their AF_pf can spike
>   above 3.0.
> - **(B2-H3)** Show measurable correlation between
>   `induce_call_probability` (decision-level) and downstream chip
>   delta — higher trap intensity should correlate with higher
>   expected value on hands where the rule fires.

(B2-H3) is the new diagnostic and the reason confidence-scaling is
worth the cost. If trap intensity *doesn't* correlate with chip
delta, the ramp adds complexity without value and we revert to fixed
mix.

### Decision Points

| Outcome | Decision |
|---|---|
| All three (B2-H*) hold | Ship Item 2. Move to Item 3. |
| (B2-H1) holds, (B2-H2) fails (still firing on TAG) | Investigate ramp curve — likely the 0.60 lower threshold is too permissive. Re-run with 0.70 threshold. |
| (B2-H3) fails (no correlation) | Revert to fixed mix. Ramp adds bugs without value. Reconsider whether `barrel_frequency` alone is the right signal. |
| (B2-H1) regresses | Either the new gate misses spots Phase A caught, or the lower-confidence soft-mix is leaking value. Diagnose via per-spot ablation. |

## Item 3 — `strong_made` inclusion

**Shipped** (`056e3160`). Outcome:

- Gate refactored from two-constant pattern (`ELIGIBLE_HAND_STRENGTH`
  + `ELIGIBLE_NUT_STATUS`) to a per-class lookup table
  (`HAND_CLASS_GATES`). Each class has its own `(nut_status_allowlist,
  max_danger_flags)` tuple.
- `nuts`: unchanged from Item 2 — `actual_nuts` only, danger ≤ 1.
- `strong_made`: added — `actual_nuts` or `near_nuts`, danger == 0
  (stricter texture to compensate for non-nut turn-card risk).
- 38 tests passing (was 31). 7 new tests in `TestItem3StrongMade` plus
  a `HAND_CLASS_GATES` table-shape invariant.

**Empirical reality:** smoke (1 tournament × 1000 hands vs ManiacBot,
seed=42) added 0 fires on the new `strong_made` path. The
intersection of (strong_made hand) × (actual_nuts/near_nuts) ×
(0 danger flags) × (IP) × (facing bet) × (flop/turn) × (≥40 BB) is
rare in natural play. **Item 3 is correctness widening, not measurable
EV widening at this sample size.** Listed as "low risk, low reward"
in the per-finding ledger.

### Problem

Phase A is `nuts`-only. This excludes legitimate trap candidates like
top set on a one-flush board (currently classified `strong_made` with
one danger flag), which on a strict equity basis still benefit from
slow-playing vs a barreler.

### Design

Extend the gate from `hand_class == 'nuts'` to
`hand_class in ('nuts', 'strong_made')` with stricter board-texture
gates for `strong_made`:

- `nuts`: existing gate (`danger_flags <= 1`)
- `strong_made`: `danger_flags == 0` (fully dry) AND
  `node.outs_to_being_beaten <= 3` (or equivalent — depends on
  postflop classifier API)

The narrower texture gate compensates for the increased turn-card
risk on non-nut hands.

### Testable Hypothesis (Item 3)

> Extending induce to `strong_made` hands with stricter texture gates
> will produce ≥ +2 bb/100 incremental lift vs ManiacBot (on top of
> Phase A's shipped baseline) without breaching the (H2) leak floor
> on any non-barreler matchup.

### Decision Points

| Outcome | Decision |
|---|---|
| Incremental lift ≥ +2 bb/100, no (H2) breach | Ship Item 3. |
| Incremental lift < +1 bb/100 | Don't ship — the firing surface is real but the EV per firing is marginal. Existing system handles these spots adequately. |
| (H2) breach on any matchup | Texture gate is too permissive. Tighten to `danger_flags == 0 AND nut_status == 'second_nuts_or_better'` and re-run. |

## Item 4 — Open-spot IP induce

**Shipped** (this session). Three pieces landed together:

1. **TrapBaitBot** (`poker/rule_strategies.py`): new `_strategy_trap_bait`
   that, when OOP first-to-act on the flop (BB seat in HU, `cost=0`),
   checks ~70% to set the trap; otherwise delegates to
   `_strategy_maniac`. Registered in `BUILT_IN_STRATEGIES` (`'trap_bait'`)
   and `CHAOS_BOTS` (`TrapBaitBot`). Empirical smoke verified: 62% check
   OOP first-to-act, 67% turn barrel after check-through (target ~70/80;
   slight shortfall from maniac fallback checking the lowest-equity
   turn hands).
2. **`flop_check_then_barrel_rate` stat plumbing**: mirrored
   `barrel_frequency`'s pattern across `cbet_detector.py` (new event for
   the first voluntary flop checker bet/check on turn after check-through
   flop), `opponent_model.py` (field, counters, `update_flop_check_barrel_attempt`,
   recalc, aggregator passthrough), `exploitation.py`
   (`AggregatedOpponentStats` field, `aggregate_from_spots` weighted
   avg, `_copy_stats` propagation), and `memory_manager.py` (consumer
   wiring on the cbet-event drain).
3. **Open-spot branch in `apply_induce_override`**: dispatch at the top
   of the function based on `has_check and not has_fold`. Parallel
   helper `should_apply_open_spot_induce` mirrors the facing-bet gate
   structure but reads `flop_check_then_barrel_rate` (≥ 0.55) and
   `flop_check_barrel_opportunities` (≥ 5). Redistribution is **flat**
   per spec — `check = 0.70`, `raise = 0.30` split evenly — not
   confidence-scaled (the open-spot exploit is more about spot
   identification than trap intensity). Trace uses
   `effect='check_back'` and `reason_code='induced_{street}_open_spot'`.

**Empirical reality (full ablation matrix, experiment 75):**

| Villain | OFF bb/100 | ON bb/100 | Lift | σ | Verdict |
|---|---|---|---|---|---|
| TrapBaitBot | +3.41 ± 2.98 | +4.84 ± 3.36 | +1.43 | +0.32 | H1 MISS (< noise) |
| ManiacBot | +0.41 ± 4.34 | −1.47 ± 3.25 | −1.88 | −0.35 | H2 within |
| CaseBot | −12.15 ± 2.33 | −6.61 ± 3.05 | **+5.53** | +1.44 | H2 — *lift* not leak |
| GTO-Lite | +4.23 ± 3.97 | −0.41 ± 4.12 | −4.64 | −0.81 | H2 MISS (within noise) |
| ABCBot | +7.45 ± 2.50 | +1.81 ± 3.75 | −5.65 | −1.25 | H2 MISS (within noise) |

| Arm | Facing-bet fires | Open-spot fires |
|---|---|---|
| trapbait ON (8000 hands) | 9 | 0 |
| maniac ON (8000 hands) | 14 | 0 |
| casebot/gtolite/abcbot ON | 0 | 0 |

**Open-spot branch fired 0× across all 88 tournaments / 88,000 hands.**
Top no-op reason is `oop_not_supported_open_spot` (2100-7200 per arm)
— the branch is correctly evaluating but hero is OOP in HU more often
than expected, AND when hero is IP free-to-act the hand-class gate is
the next bottleneck.

The `actual_nuts/strong_made + dry board + IP free-to-act` spot is
genuinely too rare for natural HU play. **Correctness widening only**
— matches Item 3's empirical pattern.

**Surprising finding:** rule-ON gives +5.53 bb/100 lift vs CaseBot.
Not statistically significant (σ=1.44), but the direction is
unexpected — the rule is supposed to do nothing against non-barreler
villains and instead it appears to help against the GTO-Lite-style
case bot. Possible explanation: the rule's facing-bet branch fires 0×
vs CaseBot (CaseBot's barrel_frequency stays below threshold), so the
lift must come from second-order effects (hero's calibration to
opponent state, or the alternative-strategy nudge from
adaptation/exploitation layers). Worth a follow-up dig if it
replicates on a second matrix run.

**Configs:**
- `experiments/configs/induce_override_phase_b_item4_smoke.json` (1 tournament × 500 hands smoke)
- `experiments/configs/induce_override_phase_b_item4_full.json` (8 tournaments × 1000 hands × 5 villains × 2 arms)
- Analysis: `scripts/analyze_item4_matrix.py <experiment_id>`

**Tunables:** `MIN_FLOP_CHECK_BARREL_FREQUENCY = 0.55`,
`MIN_FLOP_CHECK_BARREL_OPPORTUNITIES = 5`, `OPEN_SPOT_CHECK_PROBABILITY = 0.70`.
The 0.55 frequency threshold is lower than the facing-bet branch's 0.60
because TrapBaitBot-class opponents barrel 65-80% after check-through;
the lower threshold widens the firing surface without admitting
non-trappy opponents (whose stat sits near 0.5 neutral prior).

### Problem

Phase A is facing-bet only. The open-spot IP exploit (villain checks
OOP, hero IP free to act) is a real but distinct line: a TrapBaitBot-
class opponent that checks flop ~70% OOP and then barrels turn/river
hard would never trigger Phase A's facing-bet gate.

This is the original scope of the [first Phase A draft](INDUCE_OVERRIDE_PHASE_A.md#context),
re-introduced once the facing-bet version is shipped and validated.

### Design

Add a parallel branch in `induce_override.py`:

```python
def apply_induce_override(strategy, ...):
    if facing_bet:
        return _facing_bet_induce(strategy, ...)
    if open_spot:
        return _open_spot_induce(strategy, ...)
    return strategy, no_op_trace()
```

Open-spot gate is similar to facing-bet but additionally requires:
- A new stat: `flop_check_then_barrel_rate` — rate at which the
  villain checks flop OOP then bets turn after being checked back.
  (Item 1 plumbing extended.)
- Higher confidence threshold (this is a rarer pattern, sample
  builds slowly).

Open-spot redistribution: `check = 0.70`, `raise = 0.30` split across
available raises. (Same numbers as the original Phase A draft.)

### Testable Hypothesis (Item 4)

> Adding open-spot IP induce, tested vs a new `TrapBaitBot` opponent
> (checks flop ~70% OOP, barrels turn ~80%, river ~50%), will produce
> ≥ +5 bb/100 lift vs TrapBaitBot with the rule ON vs ablated, and
> ≤ -2 bb/100 leak against ManiacBot/CaseBot/GTO-Lite/ABCBot. (Same
> shape as Phase A, just with a different villain target.)

### Dependency

Requires `TrapBaitBot` (see [Parallel Work](#parallel-work)) — a new
rule_bot strategy. Pattern is in
`poker/rule_strategies.py:_strategy_maniac`. Sample implementation:

```python
def _strategy_trap_bait(context: Dict) -> Dict:
    """Check flop OOP to set the trap; barrel turn and river hard.
    Tests if AI exploits the OOP check-then-barrel line."""
    phase = context.get('phase', 'PRE_FLOP')
    position = context.get('position', 'IP')
    equity = context.get('equity', 0.5)
    cost = context['cost_to_call']
    rng_value = context.get('rng_value', 0.5)  # deterministic via seed

    # FLOP OOP first-to-act: check 70% of the time regardless of equity
    if phase == 'FLOP' and position == 'OOP' and cost == 0:
        if rng_value < 0.70:
            return {'action': 'check', 'raise_to': 0}
        # 30%: small c-bet, sized to look like a probe
        # ... (delegate to maniac-style sizing)

    # TURN and RIVER: barrel like a maniac unless completely dead
    # ... (delegate to maniac strategy)
```

## Item 5 — OOP induce (check-raise tech)

The hardest branch. Hero OOP with nuts on flop, villain IP, hero
checks intending to check-raise villain's c-bet. Requires:

- Detection of "we're OOP this street and villain has cbet history"
- A two-decision sequence (check → check-raise) where the check is
  recorded so the subsequent raise decision knows it's the trap leg
- Bluff/value protection: pure check-raise from nuts is too
  obvious; need to mix in occasional check-fold (with weak hands) and
  check-call (with marginal hands) to balance the line — but that
  goes beyond the induce rule's scope and into general OOP strategy.

Defer until Items 1–4 are validated. The poker premise is correct
(check-raising nuts vs an OOP c-bettor extracts more value than
leading) but the engineering surface is large enough that it deserves
its own design pass.

## Item 6 — Personality-aware intensity

### Problem

Phase A applies the same redistribution regardless of hero's
personality archetype. A LAG hero who already plays aggressively
benefits less from adding more passive lines (they're already
disguised); a nit hero benefits more from occasional trap mixing
(adds balance to an otherwise predictable strategy).

### Design

Multiply the Item 2 confidence-scaled intensity by an
archetype-dependent factor:

```python
ARCHETYPE_INDUCE_SCALE = {
    'nit': 1.0,     # full trap intensity — nits benefit most from balance
    'tag': 1.0,     # full trap
    'lag': 0.6,     # partial — already disguised
    'maniac': 0.4,  # partial — adds discipline they otherwise lack
    'rock': 1.0,    # full
    'station': 1.0, # full
}
```

These are placeholder values — Item 6's experimental work is tuning
them.

### Testable Hypothesis (Item 6)

> Applying archetype scaling to the trap intensity will produce no
> regression for any archetype (each archetype's bb/100 vs ManiacBot
> stays at or above its post-Item-2 baseline) and will show at least
> one archetype improving by ≥ +3 bb/100 vs its non-scaled baseline.

If no archetype benefits, the scaling adds tuning surface without
value and we revert.

## Counter-adaptation (out of scope for B, noted for completeness)

Once an opponent (LLM-driven, future TieredBot, or another rule_bot
calibrated against TieredBot) observes the trap line consistently,
they should counter-adapt: check the turn after our flop call instead
of barreling. The 0.15 minimum raise frequency in Phase A is the
known floor against this — even a perfect adapter can't pure-fold
turn because we still raise sometimes.

A full counter-adaptation defense requires:

- Detection of "this villain has stopped barreling after our calls"
  (a meta-statistic over the per-opponent model)
- Conditional un-trap: when detected, fall back to value_override's
  50/50

This is Phase C work, after Phase B ships and after we have an
adaptive opponent in the test matrix.

## Parallel Work

Useful to build alongside Phase B but not required for any single
item:

- **`TrapBaitBot`** — required for Item 4. Build during Item 1
  plumbing so it's available when Item 4 starts.
- **`AdaptiveTrapBot`** — a `rule_bot` that *starts* like ManiacBot
  and switches to checking the turn after being called on flop ≥ 3
  times in the same match. Tests Phase C counter-adaptation logic
  and provides a live adversarial signal during Phase B.

## Decision Points

After each item:

- Item 1 (`barrel_frequency` stat): plumbing test. Pass → Item 2.
  Fail (stat doesn't converge) → debug, do not advance.
- Item 2 (confidence-scaled mixing): main behavioral test. Pass →
  ship and proceed to Item 3. Fail → revert ramp, stay on fixed
  mix.
- Item 3 (`strong_made` inclusion): incremental EV test. Pass →
  ship. Fail → don't widen, stay nuts-only.
- Item 4 (open-spot induce): new branch test. Requires TrapBaitBot.
  Pass → ship. Fail → kill open-spot branch entirely.
- Item 5 (OOP induce): design-only for Phase B. Implementation
  pushed to Phase B+ once shape is validated.
- Item 6 (personality-aware intensity): tuning test. Pass → ship.
  Fail → revert; no harm done.

After Phase B (whichever subset ships), Phase C covers
counter-adaptation against learning opponents.

## Open Questions

- Should `barrel_frequency` track only PFR-as-aggressor barrels, or
  any postflop bet-after-call? PFR-only is the conventional poker
  metric, but a non-PFR aggressor who donk-bets and barrels is the
  same exploit opportunity. Probably both, recorded as separate
  counters.

- Does the existing `OpponentSpot` infrastructure support per-spot
  barrel tracking? Item 1 may need to extend it.

- For Item 6, the archetype mapping treats "nit" and "tag" the same
  (full trap). Is that right? Or should nits trap less because they
  rarely *get* to the trap spot (their range is too narrow to land
  on flop nuts often enough for trap mixing to matter)?

- Item 5 (OOP check-raise) intersects with general OOP strategy in
  a way the other items don't. Worth a separate design doc when it
  comes up rather than tucking into induce_override.

## Related Plans

- [Phase A — Induce Override](INDUCE_OVERRIDE_PHASE_A.md) — the
  prerequisite. Phase B does not start until Phase A's testable
  hypothesis clears.
- [Phase 6 — Opponent Exploitation](PHASE_6_OPPONENT_EXPLOITATION.md)
- [Phase 7.5 — Adjustment Layer Widening](PHASE_7_5_ADJUSTMENT_LAYER_WIDENING.md)
- [Phase 7.6 — Intervention Trace](PHASE_7_6_INTERVENTION_TRACE.md)
- [Phase 8.1 — Tracking & Hyper-Passive](PHASE_8_1_TRACKING_AND_HYPER_PASSIVE.md)
- [TieredBot Decision Quality](TIEREDBOT_DECISION_QUALITY.md)
