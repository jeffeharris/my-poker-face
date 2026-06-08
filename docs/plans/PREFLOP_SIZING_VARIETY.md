---
purpose: Plan to replace the single-token preflop raise sizing (always raise_3x / raise_2.5bb) with archetype-characterful size VARIETY, so AI opens and 3-bets stop being mechanically identical
type: design
created: 2026-06-08
last_updated: 2026-06-08
---

# Preflop sizing variety — kill the "always exactly 3×" tell

> **Status: PLAN.** Ships alongside the P0 band-aid (live-path `sizing_jitter`,
> already wired in `flask_app/handlers/tiered_factory.py`). This doc is the
> proper fix. Sibling to the [[SIZING_PERSONALITY]] plan — that one adds the
> *postflop* size gradient; this one fixes *preflop*, where there are no size
> tokens to act on yet.

## The problem (player-reported)

Every tiered-bot preflop raise is mechanically the same size: opens are
`raise_2.5bb`, and re-raises (3-bets/4-bets) are `raise_3x` — **one token per
node, the same multiplier every time**. The result reads as robotic and
*predictable*: "they will raise, and they will raise by exactly 3× my bet,"
most noticeably from lag/maniac (who 3-bet most). It's a tell in the bad
sense — uniform, not characterful.

## Why it's preflop-specific

The size-aware personality work in [[SIZING_PERSONALITY]] buckets postflop tokens
(`bet_33`/`bet_67`/`bet_100`) and warps their distribution per archetype. But
**preflop charts emit a single raise token per node** — there's nothing for a
size gradient to choose among:

- `_set_rfi` (`experiments/build_archetype_charts.py`) writes opens as
  `{'raise_2.5bb': 1.0}`.
- `_loosen_facing` / the base chart write all `vs_open` / `vs_3bet` raises as
  `raise_3x` (2,535 entries, 100% `raise_3x` across every chart — verified).

So the postflop gradient never engages preflop, and the resolver
(`action_mapper.resolve_preflop_sizing`) maps `raise_3x` → exactly 3× the bet.

## Shipped already (P0 — band-aid, not the fix)

`LIVE_SIZING_JITTER = 0.12` set on live controllers in `tiered_factory.py`
(`build_tiered_controller` + `build_fish_controller`). The resolver's existing
`sizing_jitter` samples the raise-to uniformly in `[3×·0.88, 3×·1.12]` → live
3-bets land ~2.6–3.4× instead of exactly 3×. Live-only (sims/tests keep the
deterministic `0.0` default; Baseline GTO reference stays exact). This breaks the
*exact-number* tell but not the *one-size-fits-all* character — every raise is
still ~3×.

## The proper fix (two parts)

**Part 1 — emit multiple raise-size tokens preflop (the missing substrate).**
Give the preflop charts a *distribution* over sizes instead of a point mass:

- `vs_open` (3-bet): a polarized mix, e.g. `{raise_2.2x, raise_3x, raise_3.8x}`.
  Polarized 3-bet sizing (small with the flat-ish/linear range, big with the
  value+bluff range) is itself a *readable, characterful* tell — exactly the
  "reliable archetype" goal, not GTO balance.
- `rfi` (open): a small mix (e.g. `raise_2.2bb`/`raise_2.5bb`/`raise_3bb`).
- Authored in the generator (`build_archetype_charts.py`) so it's reproducible,
  the same source-of-truth seam Knob 2 (the LAG chart trim) used. New
  `resolve_preflop_sizing` multipliers may be needed (it already parses
  `raise_<n>x` / `raise_<n>bb` generically — confirm `raise_2.2x` etc. resolve).

**Part 2 — archetype size-personality (engage the gradient preflop).**
Apply the [[SIZING_PERSONALITY]] size-aware distortion to the new preflop tokens
so each archetype *picks among* the sizes in character:

| archetype | preflop sizing character |
|---|---|
| nit / rock | min-3bet (2.2–2.5×), tight and small |
| tag | standard 3× linear |
| lag | mixes — some small flats-as-raises, some 3.5× |
| maniac | skews big / overbet (3.5–4×+), the size *is* the wildness |
| calling_station / weak_fish | rarely raises; when it does, erratic |

This is the same `categorize_action` → size-bucket extension
[[SIZING_PERSONALITY]] proposes for postflop, now with preflop tokens to act on.

## Sequencing

1. **P0 — jitter (DONE).** Live `sizing_jitter=0.12`. Immediate relief.
2. **P1 — preflop size tokens.** Generator emits a size mix at `rfi`/`vs_open`/
   `vs_3bet`; verify the resolver handles the new multipliers; regenerate charts.
   Measurable: realized open/3-bet *size distribution* spreads out (add a size
   histogram to the `/tmp/probe_3bet_af.py`-style harness).
3. **P2 — archetype size-personality.** Wire the size gradient (per
   [[SIZING_PERSONALITY]]) to preflop; per-archetype sizing as above.
4. **P3 — validate.** Add a "3-bet size" read to the Archetype Review tool /
   `archetype_stat_counts` so sizing variety is a first-class, tunable stat like
   frequency is now. Confirm each archetype's size signature is distinct +
   readable (the whole point).

## Interactions / caveats

- **Opponent modeling** ([[SIZING_AWARE_OPPONENT_MODELING]]): once our bots vary
  size by strength, that's a *tell our own opponent-model could read* — and a
  human can read it too (desired: archetypes should be readable). Keep the
  archetype sizing *consistent* so it's a learnable read, not noise.
- **Don't reintroduce a frequency change.** Sizing variety must not alter 3-bet
  *frequency* (the Knob-0/1/2 work) — Part 1 only redistributes the raise mass
  across size tokens, not raise-vs-call.
- **LLM/hybrid path** is separate ([[BET_SIZING_ANALYSIS_REPORT]] covers it:
  "personality differentiation is weak for sizing; aggression affects frequency
  more than size") — same theme, different code path; out of scope here.

## Pointers

- This workstream: [[ARCHETYPE_SHAPING_FINDINGS]] (frequency shaping — the split,
  chart trim, target bands).
- Prior sizing work: [[SIZING_PERSONALITY]] (postflop size gradient — the engine
  this reuses), [[SIZING_AWARE_OPPONENT_MODELING]] (reading sizing tells),
  [[SIZING_COACH_SURFACES]] (surfacing them), [[BET_SIZING_ANALYSIS_REPORT]]
  (LLM-path analysis).
- Code: `experiments/build_archetype_charts.py` (chart authoring),
  `poker/strategy/action_mapper.py` (`resolve_preflop_sizing`),
  `poker/strategy/personality_modifier.py` (the distortion gradient),
  `flask_app/handlers/tiered_factory.py` (`LIVE_SIZING_JITTER`).
