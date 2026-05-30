---
purpose: Results of the variety/fish validation sweeps (short-stack safety, depth-drain curve, aggression priced vs calling fields) backing the deploy decision
type: reference
created: 2026-05-29
last_updated: 2026-05-29
---

# Variety + fish validation — results

Backs `docs/plans/VARIETY_VALIDATION_AND_DEPLOY_HANDOFF.md`. Driver:
`experiments/variety_eval.py` (sweeps A/B/D; reuses `measure_passivity`'s
per-seed worker so numbers are byte-identical to a hand `measure_passivity`
run). Heavy numbers below were produced on the Hetzner `poker-bot-optimization`
box (ccx63, 48 dedicated cores; bit-identical to local) and torn down after.

**Headline:** the shipped precedence flip (width-tier tables at all depths) is
**safe at short stacks** — no archetype spews shallow. Drain is **depth-capped**
(deeper bottom buy-in is the biggest economy-cycling lever). And the feared "aggression is only +EV vs foldy fields" caveat
is **refuted** — aggression earns *more* vs fields that call (they pay off
value); the punished trait is **passivity**, exposed by a competent *folder*,
not a caller (see B).

## A — Short-stack validation (PASS)

_Field: foldy Baseline×5. Hetzner 3000h × 8 seeds. Columns: VPIP / PFR / jam% / avgOpen(bb) / AF / bb/100._

| archetype | depth | VPIP | PFR | jam% | avgOpen | AF | bb/100 |
|---|---|---|---|---|---|---|---|
| **Nit** | 100bb | 15 | 9 | 0.1 | 4.3 | 0.29 | −25.7 |
| | 50bb | 14 | 10 | 0.1 | 3.9 | 0.35 | −9.7 |
| | 25bb | 14 | 10 | 0.1 | 3.6 | 0.35 | −0.5 |
| **Rock** | 100bb | 19 | 12 | 0.1 | 4.5 | 0.30 | −27.3 |
| | 50bb | 17 | 12 | 0.1 | 4.0 | 0.35 | −6.2 |
| | 25bb | 18 | 13 | 0.3 | 3.7 | 0.33 | +3.4 |
| **TAG** | 100bb | 23 | 19 | 0.1 | 4.6 | 0.62 | −15.5 |
| | 50bb | 18 | 16 | 0.5 | 4.5 | 0.72 | +0.6 |
| | 25bb | 14 | 13 | 1.3 | 2.5 | 0.86 | +0.7 |
| **LAG** | 100bb | 37 | 30 | 0.7 | 6.0 | 0.77 | +14.8 |
| | 50bb | 35 | 29 | 1.0 | 5.6 | 0.83 | +23.8 |
| | 25bb | 36 | 30 | 1.9 | 4.8 | 0.99 | +27.1 |
| **Calling Station** | 100bb | 44 | 15 | 0.1 | 4.9 | 0.25 | −72.8 |
| | 50bb | 40 | 16 | 0.2 | 4.4 | 0.29 | −9.3 |
| | 25bb | 41 | 17 | 1.2 | 3.8 | 0.29 | −12.3 |
| **Maniac** | 100bb | 55 | 47 | 1.6 | 6.2 | 1.26 | +52.5 |
| | 50bb | 53 | 46 | 1.9 | 5.9 | 1.30 | +50.1 |
| | 25bb | 56 | 50 | 4.3 | 4.6 | 1.31 | +28.8 |

Red-flag scan: **none**. Worst 25bb jam% is Maniac 4.3% (the rest ≤1.9%) —
nowhere near blind-shoving. (Near-zero bb/100 values at 25/50bb show per-seed
sign noise, as expected when the number is ~0; the structural metrics
VPIP/PFR/jam are rock-stable across all 8 seeds.)

**Verdict:** PASS — no fix needed. Why the flip is safe even though width
tables are depth-agnostic: the **range width** comes from the 100bb table, but
the **sizing + jam layer remains depth-aware** — avg open size shrinks with
depth across every archetype (e.g. Maniac ~6.2bb→~4.5bb, TAG ~4.5→~2.5bb), and
jam% stays low at 25bb (worst ≈ Maniac ~4–6%, nowhere near blind-shoving). A
loose 100bb range played at 25bb just means more limps/small-opens, not shoves.
Archetype identity (VPIP spread Nit ~15 → Maniac ~57) holds at all depths; the
aggressive archetypes sensibly tighten shallow.

## D — Buy-in depth diff (drain is depth-capped)

_Fish hero vs TAG-grinder×5. Hetzner 3000h × 8 seeds. bb/100, negative = fish loses._

| archetype | 40bb | 60bb | 80bb | 100bb |
|---|---|---|---|---|
| Calling Station | −7.3 | −11.4 | −74.8 | −91.3 |
| WeakFish | −38.2 | −59.8 | −126.1 | −121.5 |

The drain is **depth-capped and accelerates past ~60bb**: a Calling Station
bleeds ~12.5× faster at 100bb than at 40bb (−7 → −91), with the cliff between
60bb (−11) and 80bb (−75). WeakFish bleeds even at 40bb (−38) and saturates
around 80–100bb (~−120). _(Local 1500h×3seed pass agreed in shape: Station
−9.6→−68, ~7×.)_

**Recommendation:** the bottom
buy-in depth is the single biggest cycling lever — a shallow $2 (≈40bb) caps
the fish drain to a slow trickle, a deep one bleeds them ~7× faster. Keep $2
shallow + weak_fish for a sustainable trickle; reach for a deeper bottom buy-in
(or per-tier `MAX_BUY_IN_BB` bump in `cash_mode/stakes_ladder.py`) only if the
economy needs faster recycling. Product/economy call for Jeff — numbers above.

## B — Aggression priced across fields (the honest cost)

_Heroes: Maniac, LAG, StationPBlind (isolates position_blind), Calling Station.
Fields: FOLDY=Baseline×5 (over-folds), JEFF=Jeff_clone×5 (realistic calls-down
human, WtSD 0.59), NEVERFOLD=CallStation×5 (always_call). Hetzner 2000h × 8
seeds. bb/100._

| hero | depth | vs FOLDY | vs JEFF | vs NEVERFOLD |
|---|---|---|---|---|
| **Maniac** | 40bb | +37.2 | +218.1 | +840.0 |
| | 100bb | +72.3 | +275.3 | +1283.6 |
| **LAG** | 40bb | +25.4 | +133.8 | +511.3 |
| | 100bb | +23.1 | +169.6 | +796.2 |
| **StationPBlind** | 40bb | −16.6 | +49.2 | +291.7 |
| | 100bb | −53.1 | +73.2 | +391.2 |
| **Calling Station** | 40bb | −3.1 | +39.3 | +220.7 |
| | 100bb | −61.0 | +70.4 | +336.4 |

### The premise was backwards — and that's the finding

The handoff feared "foldy fields make aggression look +EV (overstated)." The
data says the **opposite**: every hero earns **far more** vs the calling fields
than vs the foldy field. Maniac +37 (foldy) → +218 (Jeff) → **+840** (never-fold)
at 40bb. The foldy field *understates* aggression's edge.

Why: a field that **calls** is a field that **pays off value** and **can't win
without showdown** — it's the *easiest* opponent, not the punishing one. The
"bluff gets called" cost is real but dwarfed by the "value gets paid" benefit.
The original premise conflated **bluff-EV** (yes, higher vs callers) with
**total-EV** (much higher vs callers). A pure caller is a **donor, not a
punisher.**

**The punishing direction is a competent FOLDER, not a caller.** vs the foldy
field (the closest proxy to a disciplined opponent who folds air and doesn't pay
off), the *passive* heroes bleed and bleed harder with depth — Calling Station
−3→−61, StationPBlind −17→−53 (40→100bb) — while the *aggressive* heroes stay
positive (Maniac +37→+72, LAG +25→+23). So the real skill gradient: **passivity
is the punished trait; aggression is robustly +EV** and only its *magnitude*
(not its sign) depends on how much the field pays off.

### position_blind isolation (StationPBlind − Calling Station vs FOLDY)

- 40bb: −16.6 − (−3.1) = **−13.5** → position_blind makes the fish lose MORE when shallow (more drain — good for $2).
- 100bb: −53.1 − (−61.0) = **+7.9** → position_blind makes the fish lose LESS when deep (less drain — bad if applied to deep fish).

This **validates the existing $2-only stake gate** for `position_blind`
(handoff consideration #4): it's a shallow-stack drain lever; on deep fish it
would slightly *help* them. Keep it stake-gated.

### Caveat / remaining nuance

None of these fields is a *competent aggressor that punishes over-bluffing by
folding correctly AND barreling air back* — that's the `punisher` clone
(`measure_passivity --opponents punisher`). The honest cost of **over-bluffing
specifically** (vs total aggression EV, answered here) would use that field; it's
the one open follow-up if we ever want to price the bluff-heavy levers in
isolation. For the question asked — "does aggression secretly bleed vs a field
that calls?" — the answer is a clear **no**.

## E — Recurring eval: ON-DEMAND (no schedule, per Jeff 2026-05-29)

No cron/routine. The sweeps are DB-free and bit-identical local↔box, so fresh
numbers are one command away whenever wanted (below). A standing schedule was
declined to avoid Hetzner teardown risk / idle billing.

## How to reproduce

```bash
# local dev-first pass
docker compose exec -T backend python -m experiments.variety_eval all --hands 1500 --seeds 42,3042,6042

# Hetzner heavy pass (see docs/EVAL_RUNNER.md; poker-bot-optimization only, tear down after)
ssh root@<box> 'cd /root/poker && docker compose run --rm --no-deps backend \
  python -m experiments.variety_eval all --hands 3000 --seeds 42,142,242,342,442,542,642,742'
```
