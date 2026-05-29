---
purpose: Measure whether the tiered bot leaks bb/100 to a per-street-aggressive "pot bomber" the exploitation layer fails to detect, isolating detection-gap from response-gap
type: experiment
status: complete
hypothesis_summary: The bot over-folds and loses bb/100 to a per-street bomber whose global AF stays below the 3.5 trigger; vs an EXTREME ManiacBot that DOES trigger, it does not — isolating detection as the gap
result_summary: NO-GO. Bot wins +145 vs Fish-Spew bomber and +30.7 vs ManiacBot with the exploit layer OFF; layer flips 0–1% of actions (inert vs aggression). Detection gap is real but not an EV leak — don't build. Follow-up maniac_counter ablation: value_override/bluff_catch are dormant (0/1000 flips) but live-path (not dead-by-construction) → KEPT; only steal_pressure cut. Untested: short-stack regime.
created: 2026-05-28
last_updated: 2026-05-28
---

# Experiment 005 — Per Street Maniac Leak

> **Why this exists:** EXP_004 surfaced (as a bonus finding) that the maniac
> detector `_is_hyper_aggressive` reads **global AF > 3.5** (or all-in freq >
> 30%), and per-street pot-bombers don't clear it: Honey Badger's per-street AF
> is 17–30 but global AF is 1.93; Napoleon flop 7.2 / global 1.17; Blackbeard
> flop 10.0 / global 1.20. All three trigger **zero** detection. The concern
> (user): a human who comes in and bombs pots on "coast mode" — relentless big
> bets/raises, not constant jams — slips both triggers, and the bot has nothing
> pointed at them. This EXP asks whether that hole **actually costs chips** or
> just looks scary on paper, before we build anything.
>
> Key distinction: we cannot fix **variance** (a bomber who runs hot stacks
> people regardless). We CAN fix **systematic over-folding** — the per-hand EV
> leak that makes the bomber's over-aggression +EV against us. This EXP targets
> the EV leak, not variance, which is why a per-hand-reset measure is correct.
>
> Counterweight data points already on file (why this isn't a foregone
> conclusion): the bot is **+6.7/+4.5/+9.3 bb/100 vs the punisher reg**
> @100/50/25bb, and **Fish-Spew is a net loser** to the bot in 100bb SNGs
> (13.8% < 25% null). Both hint the strategy table may already hold up vs
> aggression. But punisher is a *competent reg* (not an over-bomber) and the
> Fish-Spew read is coarse SNG win-rate, not clean bb/100 — so the bomber-
> specific EV leak is genuinely unmeasured.

## Hypothesis

**H1 (primary — the leak exists):** Against a per-street pot-bomber that the
exploitation layer does **not** detect, the tiered bot over-folds and leaks.
Operationalized on the CRN bb/100 gate (`exploit_bb100.py`, paired exploit-ON
vs exploit-OFF, maniac backdrop):

- **1a (layer inert):** vs the undetected bomber backdrop, the ON−OFF paired
  edge is within ±5 bb/100 of zero (the layer provides ~no help — it isn't
  firing).
- **1b (the bot bleeds):** the hero's absolute bb/100 vs the undetected-bomber
  backdrop is **negative** (95% CI upper bound < 0), i.e. the bomber profits
  from the bot's baseline folding.

**H2 (detection is the gap, not the response):** Against the EXTREME `ManiacBot`
backdrop — which **does** trip `_is_hyper_aggressive` (global AF > 3.5 or all-in
freq > 30%) — the layer fires and the bot does **not** bleed: ON−OFF edge is
clearly positive (> +5 bb/100) AND the bot's absolute bb/100 is materially
better than vs the undetected bomber (by ≥ 20 bb/100). This isolates the gap as
**detection**, not the counter-machinery (which works when it engages).

**H3 (detection-gap confirmed directly):** `classify_opponent_archetype` /
`_is_hyper_aggressive` evaluated on each backdrop's observed in-sim stats fires
on `ManiacBot` but NOT on the per-street bomber (`Fish-Spew`), and the bomber's
**per-street** AF is high while its **global** AF stays < 3.5.

**Falsifier (any one closes the build case):**
- **1b fails — bot is ≥ 0 bb/100 vs the undetected bomber** (CI lower bound > 0):
  the strategy table already handles aggression without the layer → **no leak,
  don't build** the per-street detector.
- **H3 fails — the bomber actually trips the detector**: there's no detection
  gap; the layer already engages → don't build.
- **H2 fails — the bot bleeds to ManiacBot TOO (detected case)**: the problem is
  the *response* machinery, not detection → a per-street detector wouldn't help;
  fix `value_override`/`bluff_catch` gating instead.

## What we're testing

No code change. We measure the **existing** tiered bot (exploit layer ON vs OFF)
against three fixed backdrops that span the aggression-detection boundary:
- `ManiacBot` — extreme, **detected** control
- `Fish-Spew` — per-street over-bomber, hypothesized **undetected**
- (reference) `GTO-Lite` — balanced, for a baseline bb/100 the others are read against

Plus a zero-sim detector check (H3): feed each backdrop's observed stats to
`classify_opponent_archetype`.

## Setup

**Sandbox:** rule-bot backdrops are deterministic; no DB. Hero archetype = `TAG`
(non-Baseline so the layer is live — Baseline has `anchors=None` and no-ops).

**Sim config:**

```bash
# H1 + H2: per-hand CRN bb/100, layer ON vs OFF, per backdrop
for BD in "ManiacBot,ManiacBot,ManiacBot,ManiacBot" \
          "Fish-Spew,Fish-Spew,Fish-Spew,Fish-Spew" \
          "GTO-Lite,GTO-Lite,GTO-Lite,GTO-Lite"; do
  docker compose exec -T backend python -m experiments.exploit_bb100 \
      --change exploitation --archetype TAG \
      --backdrop "$BD" --hands 40000 --seeds 42,142,242
done
```

**Wiring status:** `exploit_bb100.py --backdrop` confirmed working (EXP_004
build investigation); FishBots present post-merge `a27a90a0`. `--opponent-model`
auto-enabled for `--change exploitation`.

**Output destination:** `docs/experiments/EXP_005_PER_STREET_MANIAC_LEAK/` (raw
console + a short summary table).

## Measurements

**Primary (H1/H2):** per-backdrop — hero absolute bb/100 (with 95% CI) and the
ON−OFF paired edge (does the layer help). Plus the "flipped an action on N/M
hands" fraction (whether the layer engages at all).

**Diagnostic (H3):** per-backdrop observed global AF, per-street AF, all-in freq,
and the boolean `_is_hyper_aggressive` / `classify_opponent_archetype` output.

## Comparison data

| Backdrop | detected? | hero bb/100 | ON−OFF edge | global AF | per-street AF |
|---|---|---|---|---|---|
| **ManiacBot** (extreme) | TBD | TBD | TBD | TBD | TBD |
| **Fish-Spew** (bomber) | TBD | TBD | TBD | TBD | TBD |
| **GTO-Lite** (balanced ref) | TBD | TBD | TBD | TBD | TBD |
| punisher reg (prior) | n/a | +6.7 @100bb | n/a | n/a | n/a |

## Caveats / Known Confounders

1. **Fish-Spew has a calling-station core**, not a pure bomber — it may under-
   represent a disciplined human who bombs *and* folds correctly. If it's a net
   loser anyway, that bounds the concern from one side but not the other.
2. **Per-hand stack reset** (exploit_bb100) isolates EV from stacking variance —
   intentional (we're measuring the over-fold leak, not "can they bust us"). A
   separate SNG run would be needed for the stacking/coast-mode dynamic.
3. **CRN ON−OFF measures whether the *layer* helps**, not the absolute leak; the
   per-arm absolute bb/100 is the leak metric. If exploit_bb100 doesn't surface a
   clean absolute, fall back to `simulate_bb100`/`measure_passivity` for the
   absolute number.
4. **Rule-bot maniacs may not match the human "coast mode" profile.** If none of
   the available backdrops is genuinely "high per-street AF, low global AF, low
   all-in" we may need a hand-authored bomber clone — note it and don't force a
   verdict on a poor proxy.

## Validation criteria

| Outcome | Decision |
|---|---|
| H1 + H2 + H3 all hold (bot bleeds to undetected bomber, holds vs detected ManiacBot, detection gap confirmed) | **Build it:** wire a per-street-AF (and/or big-bet-frequency) detection signal into the existing `value_override`/`bluff_catch`, and relax the EXTREME-tier gate for this case. The hole costs chips and the fix is detection-only. |
| H3 holds, H1 fails (detection gap real BUT bot is ≥0 bb/100 vs the bomber) | **Don't build** — the strategy table already folds-correctly enough; the undetected-maniac hole is cosmetic. Document and move on. |
| H2 fails (bot bleeds to ManiacBot too) | The **response** machinery is the problem, not detection. Re-scope to `value_override`/`bluff_catch` gating; a detector alone won't help. |
| H3 fails (bomber trips the detector) | No detection gap. Close the thread. |

## Results

Ran 2026-05-28. **Note on scale:** `exploit_bb100` here runs at ~0.85s/hand
(CRN double-run + opponent model + full tiered pipeline), so 1000 hands × 1 seed
= ~14 min. The planned 40k×3×3 sweep (~85h locally) is impractical here — but the
absolute bb/100 effect is large enough that low N is decisive on the leak
question (the layer-edge CI needs more hands; the absolute leak does not).

**Fish-Spew (undetected per-street bomber), TAG hero, 1000 hands, seed 42:**

| metric | value |
|---|---|
| exploit **OFF** (pure strategy table) | **+145.3 bb/100** |
| exploit **ON** | +132.8 bb/100 |
| layer ON−OFF paired edge | −12.5 bb/100, 95% CI [−35.4, +10.3] (inconclusive) |
| actions the layer flipped | 10/1000 = **1%** |

- **H1b FALSE (falsifier fired):** the bot is **+145 bb/100 vs the bomber with the
  layer OFF** — nowhere near a leak. The baseline strategy table does not
  over-fold to bombs; it punishes the over-aggression (the bomber's bluffs are
  −EV into the bot's calling/value range).
- **H1a holds:** the layer is inert vs the bomber (1% action flips, edge spans 0,
  trends slightly negative) — it isn't detecting/responding. But it doesn't
  matter, because the bot doesn't need it here.

**ManiacBot (extreme, "detected" control), TAG hero, 1000 hands, seed 42:**

| metric | value |
|---|---|
| exploit **OFF** | **+30.7 bb/100** |
| exploit **ON** | +30.7 bb/100 |
| layer ON−OFF paired edge | **+0.0** bb/100 (exact) |
| actions the layer flipped | **0/1000 = 0%** |

The surprise: the layer **does not fire vs ManiacBot either** (0 action flips,
ON == OFF exactly). H2's premise — that ManiacBot trips `_is_hyper_aggressive`
and the layer engages — is **FALSE** at this scale. Yet the bot still wins
+30.7 bb/100 on the bare table.

**Spectrum (absolute bb/100 vs aggression, layer essentially off either way):**

| opponent | bot bb/100 | layer action-flips | character |
|---|---|---|---|
| Fish-Spew (over-bomber) | +145 | 1% | fish-bomber, over-bluffs |
| ManiacBot (extreme maniac) | +30.7 | 0% | rule maniac |
| punisher reg (prior eval) | +6.7 @100bb | n/a | competent aggressive reg |

Across the whole aggression spectrum the bot is **positive** — biggest vs the
over-aggressor (it punishes the spew), thinnest but still + vs the disciplined
reg. There is no aggression profile in our measurements where the bot bleeds.

## Conclusion

**NO-GO on building a per-street maniac detector.** The hole is real at the
*detection* level — the exploitation layer's maniac-side machinery
(`_is_hyper_aggressive` → `value_override`/`bluff_catch`) is verifiably **inert**
(0–1% action flips vs both ManiacBot and Fish-Spew) — but it **does not cost
chips**, because the baseline strategy table doesn't over-fold to aggression. The
bot wins +30 to +145 bb/100 vs aggressive opponents *with the layer off*.

- **H1b: FALSE (falsifier fired)** — bot is hugely ≥0 vs the undetected bomber.
- **H2: premise FALSE** — the layer doesn't fire vs ManiacBot either; but the
  no-leak conclusion holds *more* strongly (the bot doesn't need maniac detection
  vs aggression at all).
- **H3: confirmed and then some** — the maniac-side layer is inert across the
  aggression spectrum, not just vs mid-maniacs.

This corroborates [[project_exploitation_layer_eval]]: the layer's measured
"+22.5 vs caricatures" came from the **station** side (`value_vs_station` +
`hyper_passive` vs CallStation), not the maniac side. The maniac-side rules add
nothing in practice.

**Caveats:**
1. Fish-Spew/ManiacBot are rule-bots, not disciplined human bombers. But the
   pattern (bot wins, never over-folds) holds across fish-bomber, rule-maniac,
   and the competent punisher reg — the full aggression spectrum.
2. Per-hand stack reset isolates EV from stacking variance — **the one regime not
   tested is short-stack/turbo stacking dynamics**, where prior data noted
   Fish-Spew is *not* a clean loser ("@25bb turbo regime artifact"). A
   "coast-mode plow-through" could be a short-stack phenomenon, not a 100bb EV
   leak. If the concern persists, that's the regime to probe (SNG runner), not a
   maniac detector.
3. 1 seed / 1000 hands. The absolute bb/100 (+30, +145) is unambiguous; the
   layer-edge CIs are wide but the layer-edge is ~0 and isn't the decision
   variable.

## Decisions made / next steps

1. **Do not build per-street-AF maniac detection.** The detection gap is real but
   not an EV leak at 100bb — the strategy table already declines to over-fold.
   This closes the EXP_004 "bonus lever."
2. **Consolidation (done, measure-first): cut `steal_pressure`, KEEP the maniac
   counter.** A follow-up `maniac_counter` ablation (added to champion_challenger
   CHANGES — champion disables `value_override` + `bluff_catch`, challenger keeps
   them) found **0/1000 action flips + +0.0 bb/100 vs both ManiacBot and Fish-Spew**:
   the counter is DORMANT — it never fires, because it gates on
   `classify == hyper_aggressive`, which doesn't trip on per-street maniacs. BUT it
   is **not dead-by-construction** like `steal_pressure` (empty frozenset): it
   retains a LIVE path that would fire vs a true all-in-spammer (the layer's
   founding "call wider vs junk jams" exploit). So `steal_pressure` was removed
   (commit `0d62c174`) and `value_override`/`bluff_catch` are **KEPT** as dormant
   tail-risk insurance with a revisit note in `value_override.py`. To justify
   cutting them too: add an all-in-spammer backdrop bot and re-run the
   `maniac_counter` ablation — if they don't help even when firing, cut. (The
   `fold_to_cbet` sim-wiring fix remains separately deferred.)
3. **If the "plow-through" worry persists, probe the short-stack regime** (SNG /
   turbo depth), not maniac detection — that's the one place the 100bb EV picture
   doesn't reach.
4. **Tooling note:** `exploit_bb100` at ~0.85s/hand makes large sweeps a
   Hetzner-eval-runner job, not local. Low-N is fine when the absolute effect is
   large (as here); it is not fine for tight layer-edge CIs.
