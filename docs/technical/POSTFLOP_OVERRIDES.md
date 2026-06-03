---
purpose: Map of the tiered bot's postflop override layers — what each replaces, the gates, and how they compose into the decision pipeline
type: architecture
created: 2026-06-03
last_updated: 2026-06-03
---

# Postflop strategy override layers

The tiered bot's postflop move is **not** a single chart lookup. The chart
(`postflop_strategies.json`) produces a baseline action distribution; a chain of
override/nudge layers then reshapes it. This doc maps the four heaviest
**override** layers (full distribution replacement, not offset addition) and how
they compose with the base lookup.

For the chart provenance and the table-selection logic, see
[`LOOKUP_TABLE_PROVENANCE.md`](LOOKUP_TABLE_PROVENANCE.md). For the wider
postflop quality system (hand classifier, archetype classifier, defense floor,
offset budgets), see [`TIEREDBOT_DECISION_QUALITY.md`](TIEREDBOT_DECISION_QUALITY.md).
For the 3-layer architecture this sits inside, see
[`TIERED_BOT_ARCHITECTURE.md`](TIERED_BOT_ARCHITECTURE.md).

## Pipeline order

Every layer carries a canonical `layer_order` ordinal so attribution analysis can
sort/group consistently. The single source of truth is `_LAYER_ORDER`
(`poker/strategy/intervention_trace.py:136`); `MAX_LAYER_ORDER` is derived from it.
The orchestrator is `_get_postflop_decision` (`poker/tiered_bot_controller.py:963`).

| Order | Layer (`_LAYER_ORDER` key) | Kind | Module |
|---:|---|---|---|
| 0 | `personality` | distortion | `modify_strategy` |
| 0 | `spot_tendencies` | reshape | `_layer_spot_tendencies` |
| 1 | `exploitation` (+ nested `value_vs_station`, `bluff_reduction`) | offsets | `_apply_exploitation` |
| 2 | `induce_override` | **replace** | `induce_override.py` |
| 2 | `strong_hand_override` | **replace** | `value_override.py` |
| 3 | `bluff_catch_override` | **replace** | `value_override.py` |
| 3 | `sizing_defense` | scale-to-fold | `value_override.py` |
| 4 | `multistreet_context` | freq pump | `multistreet_context.py` |
| 4 | `overbet_context` | size shift | `overbet_context.py` |
| 4 | `defense_floor` | call floor | `defense_floor.py` |
| 5 | `stab_defense` | bluff-catch widen | `stab_defense.py` |
| 5 | `short_stack`, `postflop_commit` | commit | controller |
| 6 | `math_floor` | pot-odds floor | controller |

`induce_override` and `strong_hand_override` share `layer_order=2` but never
co-fire (induce preempts; see Composition). The Phase 8 `value_vs_station` and
`bluff_reduction` rules nest inside the exploitation step at `layer_order=1` —
they compute an intensity that feeds `compute_exploitation_offsets`
(`intervention_trace.py:128-132`).

## Why replace instead of nudge

The exploitation layer (order 1) adds bounded probability *offsets*. The order-2/3
layers fully *replace* the distribution. Per the `value_override.py` module
docstring (lines 40-44): "A pro vs ManiacBot with AA doesn't think in 'shift call
probability by +0.5 logit.' They think: 'Get the money in. Period.'" When offsets
cap near a ~30% shift but the correct play is a 100% commit, the offset framework
cannot express it — so the strong-hand, induce, and bluff-catch layers bypass it
and write the target distribution directly. *(Rationale attributed to the module
docstring; the offset cap itself lives in the exploitation clamp tiers.)*

---

## 1. Strong-hand value override — `strong_hand_override` (order 2)

`poker/strategy/value_override.py` (`should_apply_value_override:131`,
`compute_value_override_strategy:167`).

**Detects:** hero holds a value class (`nuts`, `strong_made`, or archetype-relative
`strong`) vs a detected `hyper_aggressive` opponent.

**Replaces with** a "get money in" policy:

| Spot | Distribution |
|---|---|
| Facing all-in | 100% call (or 100% jam if no call action) |
| Facing a bet | 50% call / 50% raise (split evenly across raise actions) |
| Open (unopened) | raise-prob scaled by class, remainder to passive |

Open-spot raise probabilities (`raise_prob_map`, lines 279-283): `nuts` 0.95,
`strong` 0.90, `strong_made` 0.80; fallback 0.80.

**Gate** (all required, `should_apply_value_override`):
`hand_strength ∈ {nuts, strong_made, strong}`; `adaptation_bias × tilt_factor >
GATING_FLOOR` (`GATING_FLOOR = 0.05`, `exploitation.py:41`); and
`classify_opponent_archetype(stats) == 'hyper_aggressive'` (line 162).

**Dormant vs the real field.** Per the module docstring (lines 11-20, dated
2026-05-28), the `hyper_aggressive` gate fires on 0/26 real LLM opponents, and an
ablation measured 0 action flips against ManiacBot/Fish-Spew. It is kept for the
true all-in-spammer edge (>30% all-in, AF>3.5), not cut. *(Frequencies attributed
to the docstring; the live gate is the `classify_opponent_archetype` call.)*

---

## 2. Induce / trap override — `induce_override` (order 2)

`poker/strategy/induce_override.py`. Wired at `tiered_bot_controller.py:1171`
(`_apply_induce_override:2154`), immediately **before** the value override.

**Detects:** hero holds the nuts (or `strong_made` under stricter nut-status gates)
vs an opponent with a confirmed multi-street barreling tendency.

**Replaces with** a smooth-call/trap line instead of raising-the-nuts (which would
end the betting). Call probability is confidence-scaled into
`[CALL_PROB_MIN, CALL_PROB_MAX] = [0.70, 0.90]` (lines 112-113); remaining mass
splits evenly across raises to preserve unexploitability.

Four dispatch branches (the position × spot matrix):

| Situation | Branch | Signal threshold |
|---|---|---|
| IP, facing bet | smooth-call | `barrel_frequency ≥ 0.60` (`MIN_BARREL_FREQUENCY:96`), `barrel_opportunities ≥ 5` (`MIN_BARREL_OPPORTUNITIES:97`) |
| IP, open | check-back | `flop_check_then_barrel_rate ≥ 0.55` (`MIN_FLOP_CHECK_THEN_BARREL_FREQUENCY:159`) |
| OOP, open | trap-check | `cbet_attempt_rate ≥ 0.70` (`MIN_CBET_ATTEMPT_RATE:187`) |
| OOP, facing bet | check-raise | `cbet_attempt_rate ≥ 0.70` + `barrel_frequency ≥ 0.50` (`MIN_OOP_CHECK_RAISE_BARREL_FREQUENCY:189`) |

Additional gates (facing-bet IP): HU only (`active_opponent_count == 1`); street ∈
{flop, turn} (river excluded — no more barrels); `effective_stack_bb ≥ 40.0`
(`MIN_EFFECTIVE_STACK_BB:117`); hand-class nut-status per `HAND_CLASS_GATES:132`;
`adaptation_bias × tilt_factor > GATING_FLOOR`; not a station.

**Why shipped despite indeterminate EV.** Per
[`docs/plans/INDUCE_OVERRIDE_PHASE_A.md`](../plans/INDUCE_OVERRIDE_PHASE_A.md), a
72,000-hand ablation produced only 9 fires; the correctness signal (78%
followup-barrel rate vs a 70% target) passed but the bb/100 signal was below
stderr at that scale. Shipped on premise-correctness, not EV magnitude.
[`docs/plans/INDUCE_OVERRIDE_PHASE_B.md`](../plans/INDUCE_OVERRIDE_PHASE_B.md)
widened the opportunities gate (10→5, matching `OPPS_RAMP_MIN = 5.0:110`) and added
the OOP branches + confidence-scaled mixing. *(EV/fire counts attributed to the
plan docs; the thresholds above are code-verified.)*

---

## 3. Bluff-catch override — `bluff_catch_override` (order 3)

`poker/strategy/value_override.py` (`should_apply_bluff_catch_override:667`,
`compute_bluff_catch_strategy:723`). Wired at `tiered_bot_controller.py:1212`.

**Detects:** hero holds a marginal made hand (`medium_made`, `weak_made`) vs a
confirmed **EXTREME**-tier aggressor, facing a live bet.

**Replaces with** a `{call, fold}` distribution. Call probability =
`base_call_prob(hand, bet/pot) × board_danger_dampener(street, texture, paired)`.
Base probabilities and dampener multipliers are loaded from
`poker/strategy/data/phase_7_5_config.yaml` via `phase_7_5_config.py` — they are
*not* in-module constants. The shift is clamped back toward the chart baseline by
the EXTREME tier's L1 cap (`_clamp_to_envelope`).

**Gate** (all required): `hand_strength ∈ {medium_made, weak_made}`
(`BLUFF_CATCH_TRIGGER_CLASSES` — disjoint from the value override's trigger set, so
the two can never co-fire); `clamp_tier == ClampTier.EXTREME`;
`adaptation_bias × tilt_factor > GATING_FLOOR`; `bet_size_pot_ratio > 0`
(facing a bet); and a multiway-suppression check
(`_continuing_opponents_block_bluff_catch` — no continuing non-aggressor is
all-in / a station / low-sample).

### Sizing defense — `sizing_defense` (order 3, sibling)

`compute_sizing_defense_strategy` (`value_override.py:848`), wired at
`tiered_bot_controller.py:1236`. The **dual** of bluff-catch: instead of calling
*wider* vs a confirmed over-bluffer, fold *more* vs a face-up value bettor.

It multiplies the call probability by a proportional retention factor ramping from
1.0 at `sizing_defense_min_polar = 0.15` down to a floor at
`sizing_defense_full_polar = 0.40`; the floor is `sizing_defense_call_multiplier =
0.55` (controller defaults, `tiered_bot_controller.py:414/423/2732`). Freed mass
goes to fold, clamped at the DEFAULT tier (not EXTREME) because the
sizing-polarization read is orthogonal to aggression frequency.

**Per-persona opt-in.** `sizing_defense_enabled` defaults `False`
(`tiered_bot_controller.py:413`); a persona enables it via `"sizing_defense": true`
in `personalities.json` (read at line 470). Per
[`docs/captains-log/lookup-tables/skill-spectrum-and-sizing-defense.md`](../captains-log/lookup-tables/skill-spectrum-and-sizing-defense.md),
it measured ~+4.27 bb/100 with a CI spanning zero globally; the misfire cost vs a
caller exceeds the gain vs a stabilizer at current signal precision, so it is
bounded by the proportional dampener and shipped opt-in rather than always-on.
*(EV attributed to the log; the default flag and ramp constants are code-verified.)*

---

## 4. Multi-street context — `multistreet_context` (order 4)

`poker/strategy/multistreet_context.py` (`apply_multistreet_context`). Wired at
`tiered_bot_controller.py:1260` (`_layer_multistreet_context:1493`).

**Detects** two cross-street signals the memoryless chart can't see (`MultiStreetSignals`):
`was_prev_street_aggressor` (hero had initiative) and `facing_double_barrel` (opp
bet flop AND the prior street). Production reads these from the hand recorder; sims
read controller shadow fields.

**Pumps frequency** via two independently-toggleable hypotheses:

- **H1 (barrel):** with initiative and `unopened`, raise bet mass to
  `H1_BARREL_TARGET` (lines 66-70): `nuts` 0.80, `strong_made` 0.70,
  `medium_made` 0.55, `air_strong_draw` 0.50. HU-gated.
- **H2 (fold-barrel):** facing a double-barrel, raise fold mass to
  `H2_FOLD_TARGET` (lines 82-83): `weak_made` 0.80, `medium_made` 0.60. Shipped OFF
  by default (measured inert/negative).

**River barrel dropped.** The production default restricts H1 to flop+turn:
`self.multistreet_h1_streets = frozenset({'FLOP', 'TURN'})`
(`tiered_bot_controller.py:344`). Per
[`docs/captains-log/lookup-tables/eval-harness-and-exploitation.md`](../captains-log/lookup-tables/eval-harness-and-exploitation.md),
per-node attribution showed a consistent +flop/+turn/−river structure
opponent-independently ("a strong draw has resolved by the river"); the flop+turn
restriction then measured CI-clear positive. *(EV attributed to the log; the
`multistreet_h1_streets` default is code-verified at line 344.)*

The whole layer is gated by `multistreet_h1_barrel`/`multistreet_h2_foldbarrel`
flags (defaults `True`/`True` at the call site, lines 1533-1534).

## 5. Overbet context — `overbet_context` (order 4)

`poker/strategy/overbet_context.py` (`apply_overbet_context`). Wired at
`tiered_bot_controller.py:1280` (`_layer_overbet_context:1554`), **after**
multistreet (which sets bet *frequency*; overbet sets bet *size*).

**Detects** hero as the aggressor (`action_context == 'unopened'`) on TURN/RIVER
with a value-class hand. Defaults: `DEFAULT_CLASSES = {nuts, strong_made}` (line
66), `DEFAULT_STREETS = {TURN, RIVER}` (line 74), `DEFAULT_SIZE = 150` (% pot, line
75), `DEFAULT_FRACTION = 1.0` (line 76).

**Shifts size only** (never touches check/call/raise/jam/fold) via three mechanisms:

- **T1 `_shift_bet_mass:110`** — moves `overbet_fraction` of existing `bet_*` mass
  to `bet_{size}` (default `bet_150` = 1.5× pot).
- **T2 `_promote_check_to_bet:80`** — river-only: promotes `river_bluff_fraction` of
  `check` mass into an overbet, creating river bluff supply; requires a detected
  over-folder (`river_bluff_fold_to_big_bet ≥ river_bluff_min_ftbb`, default 0.6).
- **Adaptive gate `_effective_overbet_fraction`** — when `adaptive_overbet=True`,
  scales the fraction by the value-vs-station intensity, so the overbet fires only
  when a paying station is detected.

**Why a runtime layer, not a chart edit.** Per the eval-harness log, a load-time
chart transform can't gate on `active_count` (not a chart key) or vary per-bot; a
runtime layer was built and validated to match a load-time probe within seed noise.
The exploit is "face-up" (no current opponent reads bet-size tells) but correct vs
the field. *(Validation magnitudes attributed to the log; the constants are
code-verified.)*

---

## Composition rules

The orchestrator threads a `prior_layer_fired` boolean down the chain so layers
**defer** (emit a no-op trace) when an upstream override already replaced the
distribution. This prevents compounding.

```
induce fires →
  value_override        : DEFERS  (prior_layer_fired = induce.fired; reason 'deferred_to_induce_override')
  bluff_catch           : independent (disjoint hand classes — can still fire)
  sizing_defense        : defers if bluff_catch fired
  multistreet_context   : defers if induce OR value_override OR bluff_catch fired
  overbet_context       : defers if induce OR value_override OR bluff_catch OR multistreet fired
  defense_floor         : defers if induce OR value_override OR bluff_catch OR multistreet OR overbet fired
  stab_defense          : defers if induce OR value_override OR bluff_catch OR overbet fired
```

Code anchors for the deferral wiring:

| Layer | `prior_layer_fired` source | Site |
|---|---|---|
| value_override | `induce_override_trace.fired` | `tiered_bot_controller.py:1196`; short-circuit at `:2281`, reason `deferred_to_induce_override` (`:2286`) |
| sizing_defense | `bluff_catch_trace.fired` | `:1243` |
| multistreet | `induce ∨ value_override ∨ bluff_catch` | `ms_prior_fired:1524`, applied `:1545` |
| overbet | `+ multistreet` | `overbet_prior_fired:1591`, applied `:1619` |
| defense_floor | `+ overbet` | `:1649-1655` |
| stab_defense | `induce ∨ value_override ∨ bluff_catch ∨ overbet` | `stab_prior_fired:1336`, applied `:1348` |

**Mutual exclusivity by hand class.** `strong_hand_override` triggers on
`{nuts, strong_made, strong}`; `bluff_catch_override` triggers on
`{medium_made, weak_made}`. These sets are disjoint by construction, so the two
order-2/3 replacement layers can never both fire on the same decision (the
`value_override.py:680` docstring states this explicitly).

## Notes for maintainers

- All four override modules live in `poker/strategy/`; three of the named layers
  (`strong_hand_override`, `bluff_catch_override`, `sizing_defense`) share the
  single file `value_override.py`.
- The bluff-catch sizing/dampener numbers are the only override parameters held in
  a data file (`data/phase_7_5_config.yaml`); every other threshold above is an
  in-module Python constant. The data file's provenance row lives in
  [`LOOKUP_TABLE_PROVENANCE.md`](LOOKUP_TABLE_PROVENANCE.md).
- If you add/move a layer, update `_LAYER_ORDER` (`intervention_trace.py:136`) —
  it is the single source of truth and `layer_order_for` raises on unknown layers.
