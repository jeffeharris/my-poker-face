---
purpose: Scope/architecture for sizing-aware opponent modeling — track each opponent's bet-size↔strength tells, defend against face-up bettors, attack over-folders, and (first) build the adaptive opponent that measures our own face-up overbet's exploitability
type: design
created: 2026-05-28
last_updated: 2026-05-28
---

# Sizing-aware opponent modeling — scope

> **Status: SCOPE ONLY (not built).** Designed via the feature-dev workflow
> (2 code-explorer + 2 code-architect agents) on branch `lookup-tables`. The
> motivating context is the shipped, intentionally **face-up** value-overbet
> layer (`poker/strategy/overbet_context.py`): the bot always overbets value on
> dry turns, which measured +EV vs every opponent **only because nothing in the
> system reads bet-sizing tells** — neither the clones (pot-odds-reactive, no
> memory of *our* sizing) nor the bot's own exploitation layer (keys on
> vpip/pfr/ftc/AF — sizing-blind).

## TL;DR

The bot is **sizing-blind on both sides**: it can't tell a face-up polar bettor
from a balanced one (so it pays off big bets it should fold), and it can't tell
when its own face-up overbet is being exploited. This feature adds a per-opponent
bet-size↔strength read and consumes it three ways. Per the **measure-first**
discipline that's defined this session, the **first deliverable is the
measurement instrument** — an adaptive opponent that reads *our* sizing and
counter-folds — so we learn whether the face-up overbet is even exploitable
before building production defense.

## The four capabilities + sequence

| | Capability | Depends on |
|---|---|---|
| **D** | **Measure** — adaptive opponent + sim sizing instrumentation; quantify our face-up overbet's exploitability on the attribution/CRN gate | — (first) |
| **A** | **Track** — per-opponent `sizing_polarization_score` + `fold_to_big_bet` in the opponent model | — |
| **B** | **Defend** — fold more marginals to a face-up polar bettor's big bets; call down a balanced one | A |
| **C** | **Attack** — overbet/bet bigger vs measured over-folders (opponent-aware `overbet_context`) | A |

**Sequence (chosen): D → A → B, then C.** D is the gate: if a maximally-adaptive
opponent barely dents the overbet, B is low-priority (nobody punishes face-up
sizing enough to matter) — and we won't have built production behavior we can't
validate. C is in scope but sequenced after A (it needs the `fold_to_big_bet`
signal).

---

## D — the measurement instrument (first deliverable)

**Goal:** run the existing overbet A/B (`ab_node_attribution … --b base --overbet-b`)
against an opponent that *learns the face-up tell and counter-folds*, and compare
to the +42/+13 we measured vs sizing-blind opponents. A large drop (or flip
negative) = the overbet is exploitable; a small drop = it's robust even vs
adapters.

**D1 — oracle punisher FIRST (codex rec; the cheap ceiling).** Before any
learning machinery, add an adaptive clone that **deterministically** max-folds
its marginal range when facing a hero bet of **exactly the B-only overbet size
(`bet_150+`)** — no showdown learning, no `SizingMemory`, no effect on `bet_100`
/ normal raises / jams. Run the existing A (overbet OFF) vs B (overbet ON) A/B
vs this opponent.
- **Why first:** an oracle that *knows* `bet_150` = value and folds maximally to
  it is the **exploitability ceiling** (worst case for a face-up overbet). If
  even the oracle barely dents the +42/+13 → the overbet is robust vs *any*
  adapter, and we stop. Only if it guts it do we build the realistic learner (D2).
- **Why it's attribution-clean:** the opponent's behavior changes **only on a
  size the A-arm never produces** (`bet_150` comes only from the overbet layer).
  So on every shared node — including the base arm's natural `bet_100` / raises /
  jams — the opponent is byte-identical across arms, and first-divergence
  attribution stays exact. (This is the fix for the validity hole below.)
- Mirrors the existing `cripple_challenger` oracle-calibration pattern.

**D2 — learned `SizingMemory` (only if D1 shows a material leak).** A stateful
object carried across hands by the harness (the established pattern:
`OpponentModelManager` is created once per matchup and attached; clones are
stateless closures). Accumulates `(bet_fraction, hero_hand_class)` from hero
showdowns after a big bet; `exploit_fold_multiplier()` returns 1.0 until ~10-15
obs then >1.0. **Per-arm** (not shared) so B's tell doesn't contaminate A.
Measures how much of the oracle's ceiling a *realistic learner* actually
recovers (sample-limited, slower).

**⚠ Measurability correction (codex):** the attribution gate
(`ab_node_attribution`) drives hands via **`run_passivity_hand`** (NOT
`simulate_bb100.run_hand`) and sets the hero's `opponent_model_manager = None`.
So D must instrument **`run_passivity_hand` directly** — a *parallel* `sizing_log`
(do **not** mutate the 4-tuple `action_log` that `_record_sim_equity_at_actions`
unpacks) + an `--adaptive-opp` flag wiring the oracle/learner into the opponent
seat. For D2, the hero hand-strength must be captured **at the big-bet action**
(street + board snapshot), like Phase A does — NOT the hero's *final*-decision
`_last_pipeline_snapshot`, which may be a different street.

**Files:** `poker/human_clone.py` (oracle mode + optional `sizing_memory` param),
`experiments/measure_passivity.py` + `experiments/ab_node_attribution.py`
(`run_passivity_hand` instrumentation + `--adaptive-opp` flag + per-arm state).
Optional thin wrapper `experiments/measure_overbet_exploit.py`. (`simulate_bb100`
only needed if a non-attribution runner is also wired.)

**D1 RESULT (2026-05-28): the oracle guts it — overbet −24.48 bb/100
[−29.85, −19.12] vs oracle jeff, vs +42.47 vs sticky jeff (HU 24k).** A ~67 bb/100
swing; every dry-turn value node flips −EV (the oracle folds, so the overbet
extracts less than the base `bet_67` that gets called). So the face-up overbet is
**materially exploitable in principle** — keep it for the current (non-sizing-
reading) field, but it is not robust; the realistic exploited level is a
population question (→ A/D2). Built: `oracle_punish_overbets` clone mode +
`--adaptive-opp` on the attribution gate.

**The decision branch D produces:**
- **Overbet dented hard** (+42 → low/negative) → face-up overbet IS exploitable
  → balancing (overbet-bluffs) becomes worth it AND B is validated as a real
  skill (build A+B).
- **Overbet barely dented** → robust even vs adapters → A/B lower priority; the
  static overbet stands; C (attack) still independently worth it.

---

## A — track the read (production signal)

Reuse the **Phase A showdown-correlation machine**
(`memory_manager._record_showdown_equity_at_actions`), which already walks each
revealed player's postflop bet/raise/call actions, computes their equity-vs-random
at that street's board, and credits it via `update_equity_at_action`. It throws
away the bet size — A adds that back:

- **`sizing_polarization_score`** = `equity_when_betting_big − equity_when_betting_small`
  (positive ⇒ bets bigger with stronger hands ⇒ face-up/polar). **Two size bins
  only** (big = ≥~0.75 pot, small = below) — four bins (architect Approach 3) is
  sample-starved (12 cells × ~8 obs ≈ 96 showdowns/opponent; defer to a later
  iteration if the 2-bin signal proves out).
- **`fold_to_big_bet`** — live-updated (all hands, not just showdowns; like
  `fold_to_cbet`), incremented when an opponent folds/calls facing a `large`/`jam`
  bet bucket. This is the **offensive trigger** for C and has far better sample
  coverage than the showdown-gated polarization score.
- New `OpponentTendencies` fields + `update_equity_at_bet_size` /
  `update_fold_to_big_bet` updaters + `_recalculate_stats` derivation + backward-
  compatible `to_dict`/`from_dict` (`data.get(field, neutral_default)`). Mirror
  through `AggregatedOpponentStats`. Optional `'sizing_polar'` archetype label in
  `classify_opponent_archetype`.

**Why this dodges the dead zone (the design's key advantage):** the old
exploitation layer is near-inert vs realistic 0.30–0.70 vpip opponents because its
detectors gate on **vpip/AF**. The sizing signal gates on the **equity spread
between big and small bets** and on **fold-to-big-bet** — axes orthogonal to vpip.
A 0.45-vpip TAG who bets small with air and huge with sets registers correctly.

---

## B — defend (consume the read, facing a bet)

`bluff_catch_override` (`value_override.py`) is the only facing-bet layer that
already reads `bet_size_pot_ratio` → a call-prob table. Extend it with optional
`sizing_archetype` / `bet_bucket` params: vs a `sizing_polar` opponent's
`large`/`jam` bet, multiply call-prob down (~0.55) before the L1 clamp; vs a
balanced/reverse-tell bettor, nudge up. **Critically, fire this at DEFAULT clamp
tier** (not just EXTREME) — the EXTREME gate is vpip/AF-driven and would re-trap
the effect in the dead zone. The sizing read is independent of aggression
frequency, so its consumption must be too.

## C — attack (consume the read, betting)

`apply_overbet_context` gains `fold_to_big_bet_rate: Optional[float]`. When a
measured over-folder (`fold_to_big_bet ≥ ~0.60`, `_big_bet_faced_count ≥ 8`),
broaden `overbet_classes` (e.g. add `medium_made`) or push `overbet_fraction`
toward 1.0; vs a sticky caller, keep it value-only. The existing overbet matrix
already shows the directional payoff (overbet crushes folders, neutral vs the
reg) — C makes it opponent-conditioned instead of static. Follows the established
"pass intensity as a scalar, let the layer decide" pattern.

---

## Risks / open questions

- **Sample hunger (primary).** `sizing_polarization_score` is showdown-gated; two
  bins need ~8 obs each ≈ 60–80 hands/opponent — most sessions will leave it at
  the neutral prior. `fold_to_big_bet` (live, all hands) converges faster — lean
  on it for C; treat the polarization score as a slower-maturing refinement.
- **Bet-fraction computation (codex-sharpened).** `RecordedAction.amount`
  semantics differ by type (raise = raise-*to* level; call/all-in = increment),
  and only `pot_after` is stored. `amount/(pot_after−amount)` is **wrong for
  raises**. `get_player_contributions()` has the right accounting *idea* but
  exposes neither per-action increment nor `pot_before` — D/A need a small
  **ordered-replay helper**: `increment = amount − prior_committed_this_phase`
  (bet/raise) or `amount` (call/all-in); `pot_before = pot_after − increment`;
  `bet_fraction = increment / pot_before`. And note two *distinct* signals:
  "**bettor sized big**" (bettor's increment over pot-before-their-action — drives
  the polarization tell) vs "**facing a big bet**" (caller's
  `cost_to_call / pot_before_call` — drives `fold_to_big_bet` + the defense).
- **D attribution validity.** The adaptive opponent must behave identically across
  arms on non-big-bet nodes (gate the fold-tighten on big bets only), or
  first-divergence attribution breaks. Per-arm `SizingMemory`, deterministic
  (no entropy in the adapt logic) for paired replay.
- **Don't double-count Phase A.** The size-binned equity means are size-conditioned
  refinements of the existing `equity_when_betting_postflop`; they feed *separate*
  rules (bluff-catch dampener) than Phase A (which gates `hyper_passive`). No
  interaction.
- **Realistic-population calibration / the sizing dead zone.** "Orthogonal to
  vpip" ≠ "will fire" (codex): a realistic opponent can sit in a *sizing* dead
  zone — mostly one size, rarely ≥0.75-pot, big bets split value+bluff, or simply
  not reaching showdown after big bets, leaving the 2-bin score unpopulated. The
  defensive (B) read only matters if real opponents are face-up *enough*. Worth
  characterizing from the game DB (Range-Explorer + hand_history) before tuning
  thresholds — same lesson as the exploitation threshold-calibration thread.
  (`fold_to_big_bet` for C is more robust here: it doesn't need showdowns or a
  size *contrast*, just a fold/call response to large bets.)

## Build sequence (milestones)

1. **D1 (oracle)**: `run_passivity_hand` instrumentation + `--adaptive-opp` oracle
   that max-folds only to hero `bet_150+`. Measure the overbet exploitability
   **ceiling** vs the oracle (HU). **Hard gate:** oracle barely dents +42/+13 →
   overbet robust, skip D2/B. Oracle guts it → continue.
   **D2 (learner, conditional)**: per-arm `SizingMemory` learned from showdowns;
   how much of the ceiling a realistic adapter recovers.
2. **A**: `OpponentTendencies` size-bin equity + `fold_to_big_bet` + updaters +
   serialization + `AggregatedOpponentStats` + optional `sizing_polar` label. Unit
   tests + a live-DB sanity (does the label fire for intended opponents only?).
3. **B**: `bluff_catch_override` sizing dampener (DEFAULT-tier). A/B on the
   attribution gate vs a polar opponent (the D adapter) — does folding-more-to-
   face-up-bettors pay?
4. **C**: opponent-aware `overbet_context`. A/B vs over-folder vs sticky vs reg —
   does conditioning the overbet on `fold_to_big_bet` beat the static layer?
5. **(Deferred)** four-bin sizing matrix; overbet-bluff balancing (only if D says
   the face-up overbet is materially exploitable).

## Coordination

Touches the exploitation layer (`exploitation.py`, `OpponentModelManager`) the
parallel session owns — A's new `OpponentTendencies` fields + any `classify_*`
change should be coordinated. B/C touch this session's `value_override` /
`overbet_context`. The sim instrumentation (`simulate_bb100`/`ab_node_attribution`)
is shared eval infra — additive (`--adaptive-opp` flag, parallel `sizing_log`),
byte-identical when the flag is off.
