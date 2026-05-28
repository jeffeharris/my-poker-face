---
purpose: Plan for giving the tiered bot per-archetype bet-sizing personality by adding a size-aware gradient to the existing logit-space personality distortion, with optional per-personality override.
type: design
created: 2026-05-28
last_updated: 2026-05-28
---

# Sizing Personality — the missing half of aggression

> **Why this exists:** Bet-sizing tendency is one of the most recognizable
> things about a real poker player (small-ball grinders, pot-it-or-check
> players, overbet merchants). The tiered bot today expresses *how often* a
> personality bets but says nothing about *how big* — every bet size is lumped
> into one `aggressive` bucket and gets the same logit offset. This plan adds
> "how big" as one more term in the personality model the bot already has,
> rather than a new sizing subsystem.

## Current state — how sizing works today

The tiered (`sharp`) bot is the career-mode default; every non-fish AI plays as
tiered. Its decision pipeline is:

1. **Look up the node** → a `StrategyProfile`: a probability distribution over
   *abstract tokens*, e.g. `{check: 0.76, bet_33: 0.18, bet_67: 0.06}`.
2. **Distort for personality** — `modify_strategy()` warps that distribution in
   logit space (`poker/strategy/personality_modifier.py`), bounded by the
   archetype's `DeviationProfile` (nit barely deviates, maniac can swing hard).
3. **Sample one token** (`sample_action`).
4. **Resolve the token to chips** in `poker/strategy/action_mapper.py`
   (`bet_67` → 67% of pot), with optional symmetric `sizing_jitter`.

The gap is in step 2. In `compute_trait_offsets()`:

- `categorize_action()` (line ~67) buckets every action into `fold` / `passive`
  / `aggressive`. **All of `bet_33`, `bet_67`, `bet_100`, `raise_67`,
  `raise_150`, `jam` are `aggressive`.**
- The aggression offset is applied *uniformly* to every aggressive token
  (lines ~139–142): `offsets[i] += agg_dev * profile.aggression_scale`, the same
  value for `bet_33` and `bet_100`.

Adding a constant to a subset's logits preserves their internal ratio under
softmax, so **the relative mix among sizes is passed through untouched from the
solver.** Aggression controls bet *frequency*; nothing controls bet *size*.

### What the tables actually offer

Measured from the chart JSONs (not assumed):

| Spot | Tokens at the node | Multiple sizes? |
|---|---|---|
| Preflop **open** | only `raise_2.5bb` | no |
| Preflop **3bet+** | `raise_2.2x`, `raise_3x`, `jam` | yes |
| Postflop | `bet_33/67/100`, `raise_67/150`, `jam` | yes in ~⅔ of nodes |

Postflop node sizing-token distribution (2,160 leaf nodes):

- 1 sizing token: 720 nodes (33%)
- 2 sizing tokens: 240 nodes (11%)
- 3 sizing tokens: 1,200 nodes (56%)

So a personality can only express sizing where the chart offers ≥2 sizes
(≈1,440 postflop nodes + 3bet/4bet preflop). On preflop opens and single-size
nodes, all bots size identically.

## Design — one size-gradient term in the distortion layer

Add a size-aware offset to `compute_trait_offsets`. Today aggression shifts mass
*toward betting*; this shifts mass *among the bet sizes*. Same logit math, same
softmax, same `clamp_divergence`. **`action_mapper.py` is not touched** — no
chip scaling, no off-chart numbers, no fallback codepath.

### Why this is the elegant form

- **The "coverage gaps" are not special cases.** On a single-size node or a
  preflop open there is one aggressive token, so there is nothing to reweight
  and the term is a no-op automatically (`modify_strategy` already early-outs at
  `len(supported) <= 1`). You cannot shift a mix of one — and that is the
  EV-correct, realistic behavior (open sizing is standardized; single-size nodes
  are low-entropy spots where forcing a different size would be the most costly).
- **The EV leak is archetype-bounded for free.** A maniac (`max_kl=1.2`) can
  swing sizing hard; a nit (`max_kl=0.4`) barely moves. The same divergence
  clamp that already governs how far personality strays now governs sizing too —
  no separate "EV stance" knob needed.
- **It stays strictly on-chart.** The term only reweights sizes the solver
  authored for that exact board. The leak is "a slightly worse *mix* of
  legitimate sizes," not "an invented number" — small and self-limiting.
- **It is literally the missing half of aggression.** Real aggression is "how
  often *and* how big." This completes the axis the model already has rather
  than bolting on a new concept.

### Worked example

Real node `{check: 0.76, bet_33: 0.18, bet_67: 0.06}` (solver checks most, and
when it bets prefers small). A big-bettor's size offset lifts `bet_67`'s logit
above `bet_33`'s, so the post-softmax mix becomes ≈
`{check: 0.76, bet_33: 0.07, bet_67: 0.17}` — **bets just as often (24%), but
now usually big**, bounded by how far its archetype may stray.

## Changes

### 1. `poker/strategy/action_vocab.py` — token magnitude helper

```python
def sized_token_magnitude(token: str) -> Optional[float]:
    """Numeric size of a sized abstract token, or None.

    bet_67 → 0.67, bet_100 → 1.0, raise_150 → 1.5,
    raise_2.5bb → 2.5, raise_3x → 3.0, raise_2.2x → 2.2.
    Returns None for fold/check/call/jam (non-sized or shove).
    """
```

`jam` returns `None` — it is excluded from the gradient because `risk_identity`
already governs shove propensity, and including it would double-count and clash
units (a shove is not a pot/bb fraction). Reuses the parsing logic currently
inline in `action_mapper.resolve_*_sizing`.

### 2. `poker/strategy/deviation_profiles.py` — per-archetype default ("the bucket")

Add `sizing_bias: float = 0.0` to `DeviationProfile` (signed; positive = bigger).
Starting defaults — **tunable, to be validated by sim**, not load-bearing:

| archetype | `sizing_bias` | feel |
|---|---|---|
| `nit` | −0.6 | dribbles |
| `rock` | −0.3 | small / careful |
| `calling_station` | −0.2 | rarely bets; small when it does |
| `tag` | 0.0 | disciplined, solver sizing |
| `lag` | +0.5 | applies pressure |
| `maniac` | +1.0 | overbet-happy |

### 3. `poker/psychology_model.py` — optional per-personality override

Add `sizing_bias: Optional[float] = None` to `PersonalityAnchors`. This is the
*optional override* and is **explicitly outside** the `[0,1]` identity-anchor
validation list, because it is signed. `from_dict` reads `data.get('sizing_bias')`
→ `None` when absent (so every existing config is unchanged); `to_dict` emits it
only when set. Document it as the one optional signed knob among the anchors.

### 4. `poker/strategy/personality_modifier.py` — the gradient term

In `compute_trait_offsets`, after the existing aggression/looseness/risk/ego
offsets:

```
effective_bias = anchors.sizing_bias if anchors.sizing_bias is not None
                 else profile.sizing_bias

# aggressive, non-jam, sized tokens present at this node
mags  = [sized_token_magnitude(a) for a in supported if it is not None]
if len(mags) >= 2:
    lo, hi = min(mags), max(mags)
    spread = hi - lo               # spread == 0 → one size → no-op
    mean   = sum(mags) / len(mags)
    for each such action i:
        gradient = (mags_i - mean) / spread      # in [-0.5, +0.5]
        offsets[i] += effective_bias * gradient
```

The gradient is zero-mean across the sizes (it redistributes *within* the
aggressive mass, orthogonal to the aggression term that moves the mass). The
existing `clamp_divergence` bounds it per-archetype.

## Deliberately untouched

- `action_mapper.py` — no scaling, no off-chart numbers.
- `baseline_solver` / `skip_personality_distortion` — these bypass
  `modify_strategy`, so the pure solver bot stays pure.
- Preflop opens & single-size nodes — automatic no-op (correct & realistic).

## Tests (`tests/`)

- positive bias shifts the mix toward the larger token; negative toward smaller;
  betting *frequency* (aggressive-vs-passive mass) is preserved
- single-size node and preflop open → exact no-op
- `jam` excluded from the gradient (a `{jam, bet_67}` node only ranks `bet_67`)
- archetype clamp bounds the effect (maniac moves more than nit for equal bias)
- anchor override beats the profile default; absent override falls through to it

## Validation (after implementation)

Run a CRN bb/100 sim A/B with the existing eval harness (`docs/EVAL_RUNNER.md`,
`champion_challenger.py` / `sng_runner.py`) to measure the leak per archetype.
The approach keeps the deviation on-chart and clamp-bounded, so the cost is
expected small — but measuring it is the project's culture, not an assumption.
Tune the per-archetype `sizing_bias` defaults against that measurement.

## Open questions

- Override channel: `anchors.sizing_bias` (chosen here — least plumbing, since
  `modify_strategy` already receives `anchors`) vs `config_json.bot_profile`
  (mirrors `bot_type`'s home but must be threaded through the controller).
- Gradient normalization: `(mag − mean) / spread` (chosen — unit-free, bounded
  to `[-0.5, +0.5]`, comparable across nodes) vs normalize by mean vs absolute
  magnitudes.
- Whether `calling_station` should be slightly positive (stations sometimes
  spew big) rather than −0.2.
