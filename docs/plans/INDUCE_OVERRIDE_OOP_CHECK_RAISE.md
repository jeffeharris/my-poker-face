---
purpose: Phase B Item 5 design — OOP induce override (trap-check + check-raise)
type: design
created: 2026-05-23
last_updated: 2026-05-23
status: shipped 2026-05-23 — see "Empirical reality" section below
---

## Shipped status (2026-05-23)

Item 5 shipped with both branches (trap-check + check-raise) plus a 2×2
position dispatcher in `apply_induce_override`. 25 new tests, 169 total in
the related suite passing.

**Full ablation matrix (experiment 77, 88 tournaments × 1000 hands):**

| Villain | OFF | ON | Lift | σ | Hypothesis |
|---|---|---|---|---|---|
| ManiacBot | +2.19 | −0.19 | **−2.39** | −0.37 | H1 MISS — negative direction |
| CaseBot | −4.54 | −2.80 | +1.75 | +0.34 | H2 within (lift, not leak) |
| GTO-Lite | +4.28 | −0.77 | −5.05 | −0.91 | H2 MISS (within noise) |
| ABCBot | +0.65 | −0.90 | −1.54 | −0.31 | H2 within |
| TrapBait | −3.22 | −4.16 | −0.94 | −0.20 | H2 within |

**Fire counts per arm (ON arms, 8000 hands each):**

| Villain | smooth_call | check_back | trap_check | check_raise |
|---|---|---|---|---|
| Maniac | 10 | 0 | **26** | **24** |
| CaseBot | 0 | 0 | 2 | 0 |
| GTO-Lite | 0 | 0 | 0 | 0 |
| ABCBot | 0 | 0 | 0 | 0 |
| TrapBait | 5 | 0 | **19** | **21** |

**Headline finding — H3 PASS, H1 MISS with negative direction**:

Item 5 fires 50× per 8000 hands vs Maniac (6-10× the rate of Items 2/4)
and is correctly selective — fires only vs cbetters. The fire-rate sample-size
problem that hampered Items 2-4's bb/100 measurement is solved.

But the EV direction is negative (−2.39 bb/100 vs Maniac, well below
noise floor). Possible mechanism: ManiacBot doesn't fold to the
check-raise (their strategy is "raise unless equity < 0.25"), so hero
commits in spots where smooth-calling would have extracted more on
later streets. The default strategy may already handle these spots
reasonably and the override is overriding good play.

**Sigma is −0.37 — well within sampling noise**, so the directional
finding shouldn't be treated as a definitive verdict. But it is the
first Phase B item where the primary lift direction is negative, so it
warrants flagging.

**Verdict**: shipped as correctness widening with documented caveat.
Selectivity is correct (only fires vs cbetters), trace data is rich
(60 fires per ON arm provide actual EV-impact signal in
`player_decision_analysis`). Future tuning candidates:

1. Lower `OOP_CHECK_RAISE_PROBABILITY` from 0.80 → 0.60 (more call mix,
   less raise) — maniacs don't fold to raises so smooth-call may
   extract more value.
2. Add an "opponent doesn't fold to raises" gate (e.g., look at
   `fold_to_raise_rate` if such a stat existed) to skip the
   check-raise mass when villain's response is jam.
3. Re-run matrix with `n=16` tournaments to bring sigma into
   significant range and confirm direction.

These are deferred — the design hypothesis was wrong-ish but not
definitively refuted at this sample size. Don't optimize on noise.

**TrapBait reaction (40 fires)**: unexpected but explainable. TrapBaitBot
delegates to maniac when not in OOP-first-flop-check mode, including
preflop where it raises 80%. When TrapBait IS the PFR (50% of HU hands)
and hero is in BB (OOP), TrapBait's cbet_attempt_rate accumulates and
hero's Item 5 gate fires.

**Configs**:
- `experiments/configs/induce_override_phase_b_item5_smoke.json` (1 tournament × 500 hands)
- `experiments/configs/induce_override_phase_b_item5_full.json` (8 × 1000 × 10 arms)
- Analysis: `scripts/analyze_item5_matrix.py 77`

# Induce Override — Item 5 (OOP)

## Context

Phase B Items 2 and 4 cover the IP cases (smooth-call vs barrel, check-back to
induce barrel). The OOP cases are explicitly left on the table:

| Spot | IP branch | OOP branch (Item 5) |
|---|---|---|
| Facing-bet | Item 2 — smooth-call (`smooth_call` effect) | **NEW: check-raise** |
| Open-spot | Item 4 — check back (`check_back` effect) | **NEW: trap-check** |

Both Items 2 and 4 explicitly return `oop_not_supported_*` no-op reasons when
hero is OOP. Item 5 replaces those returns with actual logic.

The poker premise: when hero is OOP with a strong hand vs a frequent-cbetter,
checking the flop and check-raising the cbet extracts more value than donk
leading. The pattern is symmetric to Item 2's "let villain barrel before
raising" — we're letting villain commit to a bet before raising.

## Re-framing: why this is simpler than the handoff suggested

The original Phase B plan (line 370-386) flagged Item 5 as needing
two-decision state because the check-raise is a "two-decision sequence."
**This was an overcomplication.** The two decisions are:

- Decision A (flop OOP, free to act): hero checks
- Decision B (flop OOP, facing cbet): hero raises

These are INDEPENDENT gate evaluations. Decision B doesn't need to know
Decision A's intent because **Decision B inherently requires hero to have
checked the flop** — in HU OOP acts first; if hero is now facing a bet
postflop on the same street, hero must have checked (donk-leading is
non-standard and out of scope). The gate inputs that matter (hand class,
position, opponent stats, board, street, stack) are stable between
Decision A and Decision B within a hand, so re-derivation works.

The two branches dispatch on the same `has_fold` / `has_check` distinction
the existing code already uses; Item 5 just fills the OOP slot inside each.

## Six-question design checklist

### 1. Two-decision state

**Answer: no cross-decision state needed.** Each branch is an independent
gate evaluation. Decision B's preconditions (OOP, facing a bet on the flop)
imply hero checked the flop already. The two gates share inputs but operate
on different action-sets (Decision A has `has_check`, Decision B has
`has_fold`).

### 2. Stat reuse vs new

**Answer: reuse existing stats — no new stat plumbing.**

The exploit signal is "villain cbets often AND continues to barrel after
being called." Both already exist:

- `cbet_attempt_rate` (Phase 8.1a) — PFR-side cbet rate
- `barrel_frequency` (Phase B Item 1) — turn-barrel rate after cbet+call

For Item 5 the relevant signal is "villain cbets often when we check OOP."
`cbet_attempt_rate` is already filtered to clean cbet opportunities (no donk
into PFR), so a "hero checks, PFR cbets" sequence is exactly what it
measures. Use `cbet_attempt_rate >= 0.70` as the primary gate.

For the check-raise branch additionally gate on `barrel_frequency >= 0.50`
(softer threshold than Item 2's 0.60 because the check-raise extracts on the
flop directly — turn barrel is bonus, not required).

### 3. Bluff/value protection

**Answer: accept read-vulnerability with mixing floor, defer full balance.**

Pure check-raise from nuts is exploitable (villain learns to check back the
flop). The mitigation mirrors Item 2: even when the trap fires, leave some
probability mass on the natural action so villain can't pure-fold the flop.

Concrete:
- Trap-check branch: `check = 0.80`, `bet/raise = 0.20`
- Check-raise branch: `raise = 0.80`, `call = 0.20`, `fold = 0.0`

This keeps the line unpolarized enough that an adapting villain can't pure-
exploit. **Full balance (check-raise as bluff with air/draws) is out of
scope for Item 5** — that's polarized OOP strategy, not induce. Filed as a
Phase C item.

### 4. Redistribution mechanics

**Answer: flat split, matching Item 4's choice.**

- Trap-check: flat `check=0.80`, raises split evenly across raise actions
- Check-raise: flat `raise=0.80` split evenly across raise actions,
  `call=0.20`, `fold=0.0`

Confidence-scaling (à la Item 2) is deferred — Item 2's empirical work
showed scaling adds complexity without measurable EV benefit at the current
matrix scale. If Item 5 ships and the fire rate is high enough to measure
(unlikely per Items 3-4 precedent), revisit.

### 5. Gate scope

**Hand class gates**: reuse `HAND_CLASS_GATES` (nuts + strong_made, same
texture rules as Items 3-4). The `_check_hand_class_gate` helper extracted
during Item 4 makes this a one-line reuse.

**Streets**: flop only.
- Turn check-raise vs delayed cbet is a different exploit (and rarer).
- River check-raise vs value-bet is yet another exploit (out of scope).

**Stack**: same `MIN_EFFECTIVE_STACK_BB = 40` — we need room for villain to
fold or escalate post-check-raise.

**Multiway**: HU only (`active_opponent_count == 1`), Phase B convention.

**Psychology**: same `adaptation_bias * tilt_factor > GATING_FLOOR` gate.

### 6. Testable hypothesis

**Target opponent**: ManiacBot is close enough for a first pass. ManiacBot
raises 80% preflop (so it's often the PFR) and cbets ~100% (always raise
unless equity < 20%). When hero checks OOP, ManiacBot will cbet ~80%+ of the
time. That's the check-raise target.

A "CbetSpammerBot" (always cbets 100% as PFR, gives up turn often) would be
a cleaner target but isn't a prerequisite — reuse ManiacBot.

**Hypotheses**:
- **H1**: ≥ +5 bb/100 lift vs ManiacBot when rule is ON (combined with
  existing Items 2/3/4 induce fires).
- **H2**: ≤ −2 bb/100 leak on each of CaseBot, GTO-Lite, ABCBot.
- **H3**: OOP check-raise branch fires ≥ 5× per 8000-hand arm vs Maniac.

**Caveat per handoff Finding 3**: Items 3 and 4 shipped with 0 measurable
bb/100 lift because the spots are too rare. Item 5 likely follows the same
pattern. Don't gate acceptance on bb/100 — gate on (a) fire-rate selectivity
(should fire on Maniac, not on non-cbetters) and (b) the qualitative check
that check-raise actually completes the trap line (villain folds turn or
gets stacked).

## Implementation sketch

### File changes

`poker/strategy/induce_override.py`:

1. Two new gate helpers:
   - `should_apply_oop_trap_check(...)`
   - `should_apply_oop_check_raise(...)`
2. Two new redistribution helpers:
   - `compute_oop_trap_check_strategy(strategy)` — `check=0.80`, raises 0.20
   - `compute_oop_check_raise_strategy(strategy)` — `raise=0.80`, `call=0.20`
3. Two new apply helpers:
   - `_apply_oop_trap_check(...)`
   - `_apply_oop_check_raise(...)`
4. Modify the existing two top-level dispatchers (`_apply_facing_bet_induce`,
   `_apply_open_spot_induce`) to route by position:
   - facing-bet + IP → existing Item 2 smooth-call
   - facing-bet + OOP → new `_apply_oop_check_raise`
   - open-spot + IP → existing Item 4 check-back
   - open-spot + OOP → new `_apply_oop_trap_check`

### New constants

```python
MIN_CBET_ATTEMPT_RATE = 0.70
MIN_POSTFLOP_SEEN_AS_PFR = 5
OOP_TRAP_CHECK_PROBABILITY = 0.80
OOP_CHECK_RAISE_PROBABILITY = 0.80
```

The `cbet_attempt_rate` threshold is 0.70 (higher than Item 4's 0.55 for
flop_check_then_barrel_rate because cbet_attempt_rate has more samples — it
ticks every time the player is PFR on the flop, vs the narrow check-through
condition for flop_check_then_barrel_rate).

`MIN_POSTFLOP_SEEN_AS_PFR = 5` is the sample-floor on cbet_attempt_rate
opportunities (parallels Item 2's `MIN_BARREL_OPPORTUNITIES`).

### Trace fields

- `effect`: `'trap_check'` (Decision A) or `'check_raise'` (Decision B)
- `reason_code`: `induced_flop_oop_trap_check` or `induced_flop_oop_check_raise`
- Inputs: `cbet_attempt_rate`, `postflop_seen_as_pfr_count`, plus the
  standard hand-class / stack / position fields

### Test surface

Mirror Item 4's test pattern:
- Unit gate tests (positive baseline + each-gate-blocks variants)
- Redistribution math tests
- Dispatch tests (verify position routing)
- Ablation test
- Trace shape test

Estimate: ~20 new tests in `tests/test_strategy/test_induce_override.py`.

### Smoke + validation configs

- `experiments/configs/induce_override_phase_b_item5_smoke.json` —
  1 tournament × 500 hands HU TieredHero vs ManiacBot. Validates branch is
  evaluated.
- `experiments/configs/induce_override_phase_b_item5_full.json` —
  8 tournaments × 1000 hands × 5 villains × 2 arms (mirrors Item 4's full
  matrix shape).

## Effort estimate

| Stage | Effort |
|---|---|
| 1. Stage 1 — trap_check gate + redistribution + tests | ~2 hours |
| 2. Stage 2 — check_raise gate + redistribution + tests | ~2 hours |
| 3. Stage 3 — dispatcher updates + integration tests | ~1 hour |
| 4. Stage 4 — smoke run + validation | ~30 min |
| 5. Stage 5 — full matrix + write-up | ~90 min runtime + 30 min analysis |
| Total | ~6-8 hours wall-clock + ~90 min matrix runtime |

This is faster than Item 4 because:
- No new stat plumbing (reuse `cbet_attempt_rate` + `barrel_frequency`)
- No new bot strategy (reuse ManiacBot)
- The dispatch refactor done in Item 4 already supports both position
  branches — just need to fill in the OOP slots

## Open questions

1. **Donk-bet handling.** If hero donk-leads the flop OOP (uncommon but
   legal), Decision B doesn't apply (hero is now the bettor). The
   trap-check branch should prefer check over donk in mixed strategies —
   confirm that the redistribution math correctly handles this when the
   input strategy has both `check` and `bet_*` actions.

2. **Stack-depth gate floor.** 40 BB matches Items 2/3/4, but the
   check-raise extracts more aggressively on the flop than the smooth-call
   does. Maybe 30 BB is enough? Defer until smoke data exists.

3. **Should turn check-raise be in scope?** When villain barrels turn after
   hero called the flop check-raise... no wait that's not Item 5. Item 5 is
   flop-only. Turn check-raise vs a delayed cbet is a separate exploit
   filed as Phase C.

4. **Multiway extension.** Phase B convention is HU-only. If 3-handed and
   above, the OOP check-raise gets murkier (multiple players to barrel into
   us). Defer to a Phase C if it ever becomes worth modeling.

## Related plans

- [INDUCE_OVERRIDE_PHASE_A.md](INDUCE_OVERRIDE_PHASE_A.md) — original
  prerequisite, shipped
- [INDUCE_OVERRIDE_PHASE_B.md](INDUCE_OVERRIDE_PHASE_B.md) — Items 1-4
  shipped, Item 5 (this doc), Item 6 deferred
- [INDUCE_OVERRIDE_HANDOFF.md](INDUCE_OVERRIDE_HANDOFF.md) — pickup state +
  shipped status
