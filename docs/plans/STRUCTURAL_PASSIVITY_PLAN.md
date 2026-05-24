---
purpose: Evidence-based plan to address the tiered bot's postflop structural passivity via a multi-street context layer, including the baseline measurement and A/B test design to prove (or disprove) it
type: design
created: 2026-05-24
last_updated: 2026-05-24
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
