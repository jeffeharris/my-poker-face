---
purpose: Evidence-based plan to address the tiered bot's postflop structural passivity via a multi-street context layer, including the baseline measurement and A/B test design to prove (or disprove) it
type: design
created: 2026-05-24
last_updated: 2026-05-25
---

# Structural Passivity Plan — Multi-Street Context Layer

> **Handoff (2026-05-24):** Self-contained plan for a fresh context. The
> analysis target is the **no-personality `BaselineSolverBot`** (tiered
> Layer-1 only, `anchors=None` → skips personality distortion *and*
> `_apply_exploitation`; confirmed `poker/tiered_bot_controller.py:1046`).
> Background: `docs/plans/PUSH_FOLD_6MAX_SCOPE.md`,
> `docs/plans/SOLVER_CHART_SCOPE.md`, and the `tieredbot-bb100-lookup-tables`
> memory note. Read those first.

## 1. Evidence (what we measured this session)

**The bot is pathologically passive postflop.** Per-context action breakdown,
Baseline hero vs the 5-rule mix, 250 hands (instrumented by pairing
`StrategyTable.lookup_postflop_with_fallback` node keys with
`StrategyProfile.sample_action` outputs):

```
unopened   (n=53): check 96%, bet 4%   ← incl. strong_made 5/5 check, nuts 3/3 check
facing_bet (n=29): fold 55%, call 45%, RAISE 0%
facing_raise(n=13): fold 62%, call 38%, RAISE 0%
```
Postflop **AggFactor ≈ 0.03**. It bets ~4% with initiative and **raises 0%**
facing bets — a pure check/call/fold machine.

**But the chart itself is NOT passive** (intrinsic mean P(action) by
context×class, aggregated from `postflop_strategies.json`):

```
unopened    strong_made bet 0.64, nuts bet 0.73, medium bet 0.39
facing_bet  medium call 0.70/raise 0.13, strong call 0.63/raise 0.37, nuts raise 0.66
```
So the chart *prescribes* betting/raising — something between the chart and
the action strips it.

**What strips it: the multiway layer + the spots the bot is in.**
`poker/strategy/multiway.py` scales aggressive actions down hard in 3+ way
pots (`_bluff_mult`: IP `0.5→0.1`, OOP `0.3→0.1` as players increase;
`_check_mult` IP 1.3 / OOP 1.5) and renormalizes. Most of the bot's postflop
volume is multiway (it enters pots as a flat-caller), where this (correctly)
suppresses betting one pair into a field.

**Crucially: forcing more aggression did NOT help.** A/B with multiway
softened/off: MIX `-145 → -160/-165` (worse), CaseBot `-74 → -93/-112`
(worse). So blanket "bet more" is wrong — the passivity is *locally correct*
given the marginal-multiway spots the bot is in.

**Per-street chart frequency edits are INERT for bb/100.** Tightening
facing-bet calls / adding value-raises produced **byte-identical** MIX bb/100
(verified the transform changes sampling, but the affected spots are
rare/overridden by `defense_floor`/`math_floor` and dominated by the passive
multiway context). So you cannot fix this by editing per-street frequencies.

**The one lever that worked was preflop ENTRY.** Cutting OOP `vs_open`
flat-calls (`fold_more`) = **+17 bb/100** (shipped, commit `8bb880df`) — by
avoiding the marginal multiway pots entirely rather than playing them
passively.

**The structural gap: no multi-street planning.** The postflop table is a
**memoryless per-street policy**, keyed on
`street|position|pot_type|board_texture|hand_class|draw|action_context|reserved`
(`postflop_strategies_README.md`). Nothing carries the *cross-street line*:
`pot_type` is collapsed to `SRP`, and `action_context` is only the *current
street's* facing state. So when deciding the turn it has **no record of what
it did on the flop** — it can't barrel a credible story, can't represent a
line, and re-derives a fresh distribution each street. `NEXT_PHASE_VISION.md`
deferred "creative play injection" for exactly this: *"requires a multi-street
story-tracking layer the codebase doesn't have."*

**That layer already exists — in the coach.** `flask_app/services/
context_builder.py:126-147` derives, from a per-hand action log:
`player_bet_flop` (was hero the flop aggressor), `opponent_bet_turn`,
`opponent_double_barrel` (opp bet flop AND turn). The coach *evaluates* lines
with these (`skill_evaluator.py`: `continue_story` gated on `player_bet_flop`;
`dont_pay_double_barrels` gated on `opponent_double_barrel`). The bot also
already keeps cross-street state for its (off-for-Baseline) exploitation layer:
`_sim_last_preflop_aggressor`, `_sim_recent_aggressor`, `CbetDetector`. The
piece the *base* decision never sees is **hero's own line** (`player_bet_flop`).

**META-CAVEAT (critical for the test):** every *postflop/precision* change
this session was a **wash or inert** vs the rule-bot roster — chart edits
(inert), sizing fix (neutral on MIX, only helped the disciplined bots),
push/fold table (wash, per-seed deltas disagreed in sign). The recurring
lesson: **the rule bots are static/exploitable and don't punish imprecision,
so bb/100 vs them is insensitive to postflop quality.** Any test of a postflop
change MUST use opponents that reward precision (GTO-Lite, a `Jeff_clone`
human model) and/or more-sensitive leading metrics — or it will look like a
null result regardless of whether the change is good.

## 2. Hypothesis (falsifiable)

**H (primary):** Giving the tiered postflop decision **hero's-own-line and
sustained-aggression context** — `was_prev_street_aggressor` and
`facing_double_barrel` — lets it (a) **continue initiative** (barrel turn/river
when it was the aggressor) in HU/short-handed spots where that's +EV, and (b)
**stop paying off value** (fold marginal hands to sustained multi-street
aggression) — reducing the diagnosed leaks without the blanket-aggression
damage that multiway-off caused.

Decompose so we can attribute:
- **H1 (initiative / barrel continuation):** `was_prev_street_aggressor=True`
  → raise the bet/barrel frequency for the appropriate hand classes, *gated to
  HU / ≤2-opponent spots* (so we don't reintroduce the multiway over-aggression
  that already failed). Predicted effect: more pots won without showdown;
  fewer "c-bet then check-fold turn" lines (the `continue_story` failure).
- **H2 (don't pay off barrels):** `facing_double_barrel=True` → shift marginal
  hand classes (weak/medium made) from call→fold facing the second/third
  barrel. Predicted effect: lower "pays-off rate"; better bb/100 vs
  value-heavy bettors (GTO-Lite, CaseBot).

**Null/alternative we must be able to detect:** the layer is **inert** (like
the chart edits) because the spots are rare / overrides dominate / the
eval roster doesn't reward it. If so, that's itself a finding: the bottleneck
is the **eval** (move to GTO-Lite/clone/full-SNG) or the **architecture**
(memoryless table has a hard ceiling → the solver program in
`SOLVER_CHART_SCOPE.md`).

## 3. Baseline measurement (control to change against — DO THIS FIRST)

bb/100 has proven **too insensitive** to detect postflop changes vs rule bots.
So pre-register a baseline across **three metric tiers**, before any change:

### Tier A — direct passivity metrics (most sensitive; the real target)
Reuse the per-context instrumentation (pair `lookup_postflop_with_fallback`
node-key with `sample_action`; see this session's transcript / the AF-0.03
measurement). Capture for the Baseline hero:
- **Postflop AggFactor** overall and by `action_context`.
- **`unopened` bet% by hand_class** (esp. strong_made/nuts — currently ~0%).
- **`facing_bet`/`facing_raise` fold/call/raise split by hand_class.**
- **Barrel-continuation rate:** P(bet turn | hero bet flop) — currently ~0
  (the `continue_story` failure).
- **Pay-off rate:** frequency of call-flop → call-turn → reach river and lose
  (paying off value). Needs a per-hand line tracker (mirror the coach's
  action-by-phase derivation).
These are deterministic-ish at fixed seed and move on far fewer hands than
bb/100 — they are the **primary control**.

### Tier B — bb/100 vs a precision-rewarding roster
- **Primary opponents:** `GTO-Lite` (disciplined, the closest to "good") and a
  **`Jeff_clone`** (`simulate_bb100 --clone-opponent Jeff`, derived from real
  hand history — requires ≥20 observed hands). These reward postflop precision
  in a way the static rule bots do not.
- Keep the 5-rule MIX + per-bot as a secondary/regression reference.
- Harness: `experiments/simulate_bb100.py --six-max[-vs-rules]`, equity-MC
  disabled for Baseline (`sim._record_sim_equity_at_actions = lambda *a,**k:None`),
  `ProcessPoolExecutor` across cells, **paired seeds** (≥3: 42/142/242),
  ≥3000 hands/cell. Report mean + per-seed (watch for sign-disagreement =
  noise, as seen in the push/fold A/B).

### Tier C — (stretch) full-SNG win-rate
The real objective is WTA-SNG win rate (chip-EV = $EV). Fixed-depth bb/100 is a
proxy. If/when a full-SNG runner exists (escalating blinds, depleting stacks,
play to one winner — see `PUSH_FOLD_6MAX_SCOPE.md` harness note), measure
win-rate; it's the least-noisy holistic gate but the biggest harness lift.

**Pre-register the baseline numbers** (Tier A distribution + Tier B bb/100 per
opponent, all seeds) in this doc's results section before implementing, so the
A/B has a frozen control.

## 4. The test (A/B, pre-committed)

- **Two arms, same seeds/hands/opponents:** layer **OFF** (current) vs layer
  **ON**. Toggle via a flag (e.g. `enable_multistreet_context` on the
  controller / a sim arg), so it's a true paired in-process A/B (mirror the
  push/fold control-arm pattern: monkeypatch the new derivation to a no-op for
  the OFF arm).
- **Primary gate (Tier A):** layer ON must move the *direct* metrics in the
  predicted direction — barrel-continuation rate up (H1), pay-off rate down /
  fold-to-double-barrel up (H2) — **without** reintroducing blanket multiway
  over-aggression (watch unopened multiway bet% — should NOT balloon).
- **Secondary gate (Tier B):** bb/100 vs GTO-Lite and `Jeff_clone` improves or
  holds (direction-only; report per-seed). No regression on the MIX guardrail.
- **Attribution:** run H1-only, H2-only, and H1+H2 arms to isolate which
  signal carries the effect.
- **Pass:** Tier A moves correctly + Tier B non-negative on the
  precision-rewarding opponents. **Honest-null is an acceptable outcome** and
  routes to the eval/architecture conclusions in §2.

## 5. Implementation sketch (where it plugs in)

- **Derive the signals** in the tiered postflop decision path. Two options:
  (a) **port** the coach's derivation (`context_builder.py:126-147`:
  `player_bet_flop`, `opponent_double_barrel`) over the bot's per-hand action
  record (`poker/memory/hand_history.py` `RecordedAction(phase=…)`); or
  (b) **reuse** the bot's existing cross-street state
  (`_sim_last_preflop_aggressor`, `_sim_recent_aggressor`, `CbetDetector`) —
  lower-cost since it's already maintained. Add **hero's-own-line**
  (`was_prev_street_aggressor`) which the base decision currently lacks.
- **Apply as a thin override layer**, not chart-key surgery (chart edits were
  inert). Position it in the postflop pipeline alongside the existing rule
  overrides (`tiered_bot_controller.py` postflop: exploitation → induce →
  value_override → bluff_catch → defense_floor → short_stack → math_floor). It
  must (a) be **gated narrowly** (HU/≤2 opp for H1 barrel; specific hand classes
  for H2 fold), and (b) interoperate with the floors (mirror `defense_floor`'s
  "skip when an upstream override already replaced the strategy" pattern).
- Keep it behind the `enable_multistreet_context` flag so the A/B OFF arm is
  the exact current behavior.
- **Validate against the inert-trap:** confirm via the Tier A instrumentation
  that the layer actually *changes sampled actions* in the targeted spots
  (the chart edits failed silently — don't repeat that).

## 6. Risks

- **Inert, like the chart edits** — spots rare / overrides dominate. Mitigate:
  apply as an override (not chart freq), gate to spots that actually occur,
  and *verify action changes* via Tier A before trusting bb/100.
- **Eval can't see it** — rule bots don't reward precision (the session's
  recurring null result). Mitigate: GTO-Lite + `Jeff_clone` as primary; Tier A
  leading metrics; full-SNG as stretch.
- **Reintroducing multiway over-aggression** (which already failed) — gate H1
  strictly to HU/short-handed; never blanket.
- **Multi-street state correctness in the sim** — the bot's aggressor tracking
  is driven in `run_hand` (`_sim_*` fields); ensure `was_prev_street_aggressor`
  is derived correctly there and in production (`MemoryManager` path).

## 7. References (verify file:line — point-in-time 2026-05-24)

- Passivity / AF measurement, multiway A/B, inert chart edits, fold_more +17:
  this session's transcript + memory `tieredbot-bb100-lookup-tables`.
- `poker/strategy/multiway.py` — `_bluff_mult`/`_check_mult` suppression.
- `poker/strategy/data/postflop_strategies_README.md` — node taxonomy (no
  cross-street history; pot_type=SRP only).
- `poker/tiered_bot_controller.py` — postflop pipeline (~`:691` onward);
  Baseline exploitation no-op `:1046`; `_sim_last_preflop_aggressor` /
  `_sim_recent_aggressor`; multiway applied ~`:751`.
- `flask_app/services/context_builder.py:126-147` — `player_bet_flop`,
  `opponent_double_barrel` derivation (the layer to port).
- `flask_app/services/skill_evaluator.py` — `continue_story`,
  `dont_pay_double_barrels` (the coach's multi-street skills).
- `poker/memory/hand_history.py` — `RecordedAction(phase=…)` per-street log.
- `experiments/simulate_bb100.py` — harness; `--clone-opponent`,
  `--six-max-vs-rules`, `--start-bb` (short-stack knob added on
  `push-fold-6max`).
- `docs/plans/NEXT_PHASE_VISION.md` — the deferred "creative play" note.
- `docs/plans/SOLVER_CHART_SCOPE.md` — the architectural fallback if this and
  other table work hits the memoryless-table ceiling.

## 8. Recommended order for the new context

1. **Build the Tier-A instrumentation** (per-context action distribution +
   barrel/pay-off rates) and **capture the frozen baseline** vs GTO-Lite +
   `Jeff_clone` + MIX. Record numbers here.
2. **Stand up `Jeff_clone`** in the harness (it's the key to a sensitive eval);
   confirm it has ≥20 observed hands.
3. **Implement the layer behind a flag** (signals + narrow-gated override).
4. **Run the A/B** (OFF vs ON, + H1/H2 attribution arms); fill results.
5. **Decide:** ship if Tier A moves + Tier B non-negative; if honest-null,
   conclude on eval-bottleneck vs architecture-ceiling and route to the
   solver program or a real-SNG eval.

## 9. Results (2026-05-25 — executed)

### Harness & instrumentation built
- **`experiments/measure_passivity.py`** — Tier-A instrumentation. Mirrors
  `simulate_bb100.run_6max_matchup` exactly (seat names, dealer rotation,
  per-hand global+rng seeding) so bb/100 is comparable, but uses a trimmed
  instrumented hand loop. For the Baseline hero the loop omits the
  `opponent_manager`/equity-MC/c-bet machinery (none of it affects Baseline
  decisions or final stacks — exploitation is a no-op at `anchors=None`,
  equity recording only writes to models), satisfying the plan's
  "equity-MC disabled for Baseline" requirement. Seeds run concurrently via
  `ProcessPoolExecutor`. Reuses the snapshot's `node_key` (one-line add to
  `tiered_bot_controller.py`) to pair each resolved action with its full
  postflop context, plus a per-hand line tracker for barrel/pay-off rates and
  a layer-fire counter (the inert-trap check).
- **`Jeff_clone` is unavailable** in this environment — `data/poker_games.db`
  has 0 rows in `opponent_models`/`hand_history`/`games`, and
  `--clone-opponent` requires ≥20 observed hands. Per the plan's fallback,
  **GTO-Lite is the precision-rewarding primary**, MIX the regression ref.

### Tier-A frozen baseline (Baseline hero, mode=OFF, 9000 hands = 3000 × seeds 42/142/242)

| Roster | AggFactor | unopened bet/raise% | facing_bet fold/call/RAISE | barrel-cont. P(bet turn\|bet flop) | c-bet→give-up | pay-off rate | facing-double-barrel fold/call | bb/100 (per-seed) |
|---|---|---|---|---|---|---|---|---|
| **GTO-Lite** | 0.045 | ~0% / 5% | 41% / 58% / 1% | **3% (2/79)** | 84% (66/79) | **98% (63/64)** | 46% / 54% (n=218) | **−78.9** (−78.0/−67.2/−91.5) |
| **MIX** | 0.019 | ~0% / 3% | 53% / 47% / 0% | **0% (0/9)** | 56% (5/9) | **91% (74/81)** | 58% / 42% (n=240) | **−106.2** (−92.2/−96.5/−129.8) |

Reproduces the diagnosed pathology: postflop AggFactor ~0.02–0.05, ~0% raises
facing bets, near-zero barrel continuation, and a 91–98% pay-off rate. All
bb/100 per-seed deltas agree in sign (consistent loss, not noise).

`unopened` bet% by class (GTO baseline) confirms the chart *does* prescribe
some value betting that gets stripped: nuts 21%, strong_made 12%, medium 5% —
but it's dominated by the 95% check that the multiway context (correctly)
imposes.

### Signal-frequency diagnostic (the crux: do the layer's spots even occur?)
500 hands, Baseline vs GTO-Lite, mode=ON, instrumented for `derive_signals`
frequencies independent of whether the layer fired:

- **`unopened` decisions: 356; with prior-round initiative
  (`was_prev_street_aggressor`): 63 (18%).** Of those, 29 were also a value
  class (H1-eligible). **By active players: `2p=2, 3p=2, 4p=1, 6p=24`.**
  → **83% of the bot's "had-initiative" spots are full-ring MULTIWAY.** Only
  **2 of 29** are HU (the spots where barreling is unambiguously +EV and
  where multiway suppression isn't a concern).
- **facing-bet decisions: 83; facing a double-barrel: 8 (10%); of those
  marginal (H2-eligible): 4.**
- **Layer activity: H1 `barrel` fired 0×; H2 `fold_barrel` fired 1× (changed
  the sampled primary action 0×).** ON bb/100 = −40.1 vs OFF −40.8 (same
  seed/hands) — **the layer is inert.**

**Mechanism (why it's inert — structural, not a gating bug):** the bot's
"initiative" postflop spots occur overwhelmingly *multiway*, because it enters
pots as a preflop **flat-caller** (→ 3+ way pots) and even when it raises
preflop it gets multiple callers. Barrel-continuation (H1) is only safe HU
(widening it to multiway is exactly the over-aggression the prior multiway-off
A/B already proved harmful: MIX −145→−160/−165), and HU-with-initiative spots
arise ~0.4% of the time. Fold-to-double-barrel (H2) has ~0.8% eligible spots
and the table is already fold-leaning enough there that pumping fold rarely
flips the sampled action. **A postflop multi-street override cannot manufacture
the initiative the preflop entry strategy never created.**

### A/B: multi-street layer OFF vs ON (paired 3000 × seeds 42/142/242)

| Roster | facing-double-barrel fold% (OFF→ON) | bb/100 OFF | bb/100 ON | paired per-seed Δ | layer fires (barrel / fold_barrel) |
|---|---|---|---|---|---|
| **GTO-Lite** | 46% → **56%** | −78.9 | **−74.6** | +3.5 / +4.1 / +5.4 | 5 (chg 3) / 40 (chg 0) |
| **MIX** | 58% → **64%** | −106.2 | **−99.0** | +7.0 / +8.0 / +6.6 | 0 / 31 |

**The two hypotheses diverge — this is NOT a flat null:**

- **H1 (barrel continuation) is structurally inert, as the diagnostic predicted.**
  At 9000 hands the H1-eligible spots are **444/536 full-ring multiway** vs only
  **38 HU** (and **0 HU** vs MIX). With the HU-only gate it fires ~5× / 9000
  hands (3 action-changes) — far too few to move bb/100. *This confirms the
  upstream root cause:* the bot flat-calls preflop → multiway → almost never the
  lone aggressor with a HU barrel. **H1 cannot work until preflop ENTRY creates
  HU-with-initiative spots → routes to the preflop "isolate" track (§10).**

- **H2 (don't pay off double-barrels) WORKS.** It fired 31–40× / 9000 hands,
  shifted the facing-double-barrel fold rate **+6–10pp**, and improved bb/100
  **+4.3 (GTO) / +7.2 (MIX)** — *consistently positive across all 6 paired
  seeds, no sign disagreement.* It directly cuts the diagnosed **90–98%
  pay-off-rate** leak (the bot was calling marginal made hands down into
  sustained multi-street aggression and losing). The fire-count breakdown
  attributes the gain cleanly to H2 (H1's 3 action-changes can't move bb/100 by
  +4–7). Notably it helps even vs the MIX (which includes ManiacBot) — a *double*
  barrel is a strong enough signal that folding marginal hands to it is +EV on
  net even against a roster with a bluffer.

### Decision
- **SHIP H2** (`enable_multistreet_context=True`; H1 left on but currently
  inert — harmless, and ready to activate once entry is sharpened). It is a
  real, low-risk, consistently-positive mitigation of the pay-off leak. *(Flag
  default stays OFF in `__init__`; enable via experiment/production config.)*
- **H1 is blocked on preflop entry, not on the postflop layer.** The
  memoryless-table + passive-entry ceiling stands for *initiative*; the fix is
  upstream (§10), not more postflop logic.
- Eval caveat still holds: bb/100 vs rule bots is insensitive, but the H2 signal
  survived it (consistent across both rosters and all seeds), which is itself
  evidence the effect is real rather than roster-specific noise.

## 10. Track 1 — Preflop ENTRY sharpening (isolate)  *(in progress)*

Root-cause follow-up to H1's inertness. The bot's passivity is locked in at
entry: it flat-calls `vs_open` into multiway. `poker/strategy/preflop_isolate.py`
shifts OOP-defender (`SB`/`HJ`/`CO`, the same scope as the +17bb `fold_more`)
`vs_open` `call` mass → `raise_3x` (3-bet to isolate), redirecting to *raise*
where `fold_more` redirected to *fold*. Flag-gated in-memory transform
(`measure_passivity --entry isolate`), non-destructive, BTN/BB untouched, rows
still sum to 1.0; 8 unit tests in `test_preflop_isolate.py`.

Leading indicator added to the harness: **field-size distribution at the hero's
postflop decisions** (HU% should rise if isolating works) — that is the
prerequisite for H1 to ever fire.

### Track 1 A/B result (control-entry vs isolate-entry, 3000 × seeds 42/142/242)

| Roster | HU% @ postflop (isolate) | AggFactor (base→iso) | bb/100 base | bb/100 isolate | per-seed Δ |
|---|---|---|---|---|---|
| **GTO-Lite** | **3%** | 0.045 → 0.045 | −78.9 | −78.7 | +0.1 / 0.0 / +0.7 (≈0) |
| **MIX** | **~2%** | 0.019 → 0.019 | −106.2 | −109.1 | −2.7 / −14.6 / +8.6 (noisy, ≈0/worse) |

**The isolate first-cut is inert — and the field-size distribution explains
why, unifying the whole investigation.** vs GTO-Lite the isolate run is
byte-identical to baseline (AggFactor unchanged, bb/100 within 0.2). The
field-size readout is the smoking gun: **~78% of the hero's postflop decisions
are 6-way (`6p=6395` of ~8247), and HU is only 2–3%.**

**Root cause (the eval, not the bot):** the rule-bot roster — CallStation never
folds, GTO-Lite/others call wide — **almost never folds preflop, so nearly
every flop is full-ring.** In that regime *isolation is impossible*: a 3-bet
doesn't fold anyone out (they call), so the pot stays multiway, and the only
effect is building a bigger pot OOP with a marginal hand (slightly −EV vs MIX).
This is the same wall H1 hit. The bot is passive postflop **because it is
almost always multiway, and it is almost always multiway because the opponents
don't fold** — not because of anything in the postflop or entry strategy.

### Unified conclusion
1. **H2 (don't pay off double-barrels) ships** — it is the one mitigation that
   works *regardless of the eval*, because it's a defensive fold (doesn't need
   opponents to fold). +4–7 bb/100, all seeds.
2. **H1 (barrel) and Track-1 (isolate) are both blocked by the EVAL**, not by
   the bot or the postflop architecture. Against non-folding rule bots you are
   structurally always-multiway, so initiative and isolation cannot exist.
   *Per-street chart edits (inert), the multiway A/B (worse), the multi-street
   barrel layer (inert), and now preflop isolation (inert) all fail for the
   same reason.*
3. **The binding constraint is Track 2 — a precision-rewarding eval where
   opponents fold appropriately.** Concretely: a GTO-Lite-or-tighter *folding*
   roster (so HU pots actually form), a `Jeff_clone` (the dev branch's
   `portable clone profiles` / freeze-to-JSON makes this viable without a
   populated DB), and ultimately the full-SNG win-rate runner (Tier C). Only on
   such an eval can initiative/isolation work be validated; on the current
   roster every such change is doomed to read inert.
4. The isolate transform + flag are kept (tested, non-destructive) — they
   become live the moment the eval has folding opponents, and are directly
   useful in genuinely short-handed/HU SNG stages.

## 11. Track 2 — Jeff_clone eval (2026-05-25, the unlock)

Stood up `Jeff_clone` as a precision-rewarding *folding* eval, using the
merged portable-clone infra (`experiments/clone_profiles/jeff.json`, loaded
via `measure_passivity --opponents jeff`, no DB needed). Jeff = a real human
model from 4669 observed hands: VPIP 0.39 / PFR 0.16 / **fold_to_cbet 0.45** /
AF 1.22 / WtSD 0.59 — i.e. it *folds* preflop and to c-bets, unlike the rule
bots. 3000 × seeds 42/142/242.

### The eval was THE binding constraint — confirmed

| | vs GTO-Lite | vs MIX | **vs Jeff_clone** |
|---|---|---|---|
| HU% @ postflop | 2% | ~2% | **41%** |
| field size | ~78% 6-way | mostly multiway | **HU/3-way (6-way≈0)** |
| Postflop AggFactor | 0.045 | 0.019 | **0.208** |
| unopened bet/raise% | ~5% | ~3% | **18%** (nuts 41 / strong 37 / med 24) |
| raise% facing bet | ~1% | 0% | **5–8%** |
| barrel-continuation | 3% | 0% | **22%** |
| pay-off rate | 98% | 91% | **45%** |
| **bb/100 (OFF)** | **−78.9** | **−106.2** | **−9.6** (−12.3/−12.9/−3.5) |

Against a realistic human the bot is **near break-even (−9.6 bb/100)** and
plays *actively* — it bets for value with initiative, raises facing bets, and
continues barrels. **Its catastrophic −80 to −106 vs the rule bots was an
artifact of being always-multiway against non-folders, not an intrinsic leak.**
The chart was never the problem; the eval was.

### Layer / isolation re-tests on the good eval

| Arm (vs Jeff_clone) | barrel-cont | facing-dbl-barrel fold | layer fires | bb/100 |
|---|---|---|---|---|
| OFF (baseline) | 22% (58/259) | 54% | — | −9.6 |
| **layer ON (H1+H2)** | **35% (100/283)** | **68%** | barrel **307** (chg 154) / fold_barrel 11 | −9.1 |
| isolate entry (OFF) | 22% | — | — | −10.3 (HU% still 41%) |

- **H1 (barrel) is no longer inert** — with real HU spots it fires **307×**
  (vs 5 vs rule bots) and lifts barrel-continuation **22%→35%**. The eval
  unblocked it exactly as predicted. But **bb/100 is flat (−9.6→−9.1, within
  noise across all seeds)**: H1 is *active and directionally correct* but **not
  yet clearly +EV** — barreling more isn't winning more. Converting the +13pp
  continuation into EV needs barrel selection/sizing/board-awareness iteration
  (the next sub-project), or H1-only attribution to confirm sign.
- **H2 (fold double-barrels) still helps** — folds 54%→68%; consistent with the
  rule-bot result. Shippable.
- **Preflop isolation is NOT a lever** — inert vs *both* eval types. vs rule
  bots: can't isolate non-folders (always multiway). vs Jeff: his own folding
  already produces 41% HU, so the hero forcing 3-bets doesn't move field-size
  (41%→41%) and is bb/100-neutral (slightly worse). The entry isn't the
  bottleneck once opponents fold.

### Track 2 decision
1. **Adopt Jeff_clone as the primary eval for all future tiered-bot work.** It
   is sensitive (reveals −9.6 vs a human, not −106 vs stations), realistic, and
   DB-free/portable. The rule-bot roster is a degenerate always-multiway regime
   that masks true bot quality — keep only as a guardrail.
2. **Ship H2** (unchanged from §9).
3. **H1 is now a live, measurable opportunity** (no longer dead): iterate barrel
   logic on the Jeff eval. First step: H1-only attribution arm to confirm sign,
   then tune which classes/boards barrel.
4. **Retire preflop isolation as a lever** (keep code; it's not the fix).

## 12. H1/H2 attribution + per-signature leak finder (2026-05-25)

**Attribution (vs Jeff_clone, 3000×3, paired):** H1-only **−8.7** (Δ +0.9, all
seeds +); H2-only **−10.0** (Δ −0.4, all seeds −); H1+H2 −9.1. So H1 (barrel)
is +EV vs the folding human; **H2 is opponent-dependent** — +4–7 vs value-heavy
rule bots but −0.4 vs Jeff (he bluffs, so folding marginal hands to his
double-barrels folds out winners). H2's correct home is opponent-gated, not
blanket-on. **H1 tuning:** value-only (drop `air_strong_draw`) is *worse*
(−9.5 vs −8.7) — semi-bluff barrels with strong draws are +EV even vs a station
(fold equity + draw equity). Keep H1 = all four classes.

**Per-signature leak finder** (`measure_passivity --leak-report`): buckets the
bot's postflop decisions by line-signature (street, action_context, hand_class,
prev-aggressor, double-barrel) and ranks by the gap between realized aggression
and the chart's own intent (`base_strategy_probs`). Run vs Jeff (9000 hands):

```
street ctx       class         agg?   n   chk  AGG | chart  gap
TURN   unopened  nuts          -      73   42   58 |  45    +12
TURN   unopened  strong_made   -     291   62   38 |  36    +3
RIVER  unopened  strong_made   -     355   61   39 |  36    +3
FLOP   unopened  strong_made   Y     131   70   30 |  36    -6
```

**Findings:**
1. **The pipeline is not the leak on this eval** — every gap is ±3–7%
   (realized ≈ chart). The multiway/override "stripping" was a rule-bot
   artifact; on the (mostly-HU) Jeff eval the bot faithfully executes the chart.
2. **The leak is the CHART itself: it under-bets value.** In absolute terms it
   checks the **nuts 42% on the turn**, **strong made 60–62% turn/river**, and
   **70% as the flop c-bettor** — leaving value uncollected vs a call-happy
   human (Jeff WtSD 0.59).
3. **Multi-street line-bits barely move behavior** (prev-aggressor Y vs − differ
   ~2% for the same hand_class). The dominant axis is `hand_class × street ×
   context`. **So the biggest leak is low-dimensional — not a 2^K signature
   table or a solver; the chart's unopened value-betting frequencies.**

**Architecture takeaway (re: "one idea to replace the disparate layers"):** the
disparate layers split into *situation policy* ⊕ *opponent-exploit deviation*,
and they cannot merge (H2 sign-flips by opponent). But the leak finder shows the
situation-policy leak is low-dimensional and chart-local — fixable with a
targeted **value-bet floor** (mirror of `defense_floor`, for betting), not a
big unify. That floor would also catch the population **H1 misses** (strong
hands get checked 60%+ even when hero is *not* the prior-street aggressor).

**Next lever:** value-bet floor — hand-class-gated bet floor for unopened
{nuts, strong_made} (and thin medium on the river vs stations), tested on Jeff.


