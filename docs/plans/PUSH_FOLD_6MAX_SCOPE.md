---
purpose: Build plan for a multi-way (6-max) short-stack push/fold lookup table for the tiered bot, plus the short-stack sim harness needed to validate it
type: design
created: 2026-05-24
last_updated: 2026-06-12
---

# Multi-way Push/Fold Table (`push_fold_6max.json`) — Build Scope

> **Handoff note (2026-05-24):** This is the next new *lookup table* to build
> for the tiered bot. The bb/100 work that preceded it only *tuned* the
> existing 100 BB charts (`fold_more` preflop tightening) + fixed a postflop
> sizing double-count. This doc adds a genuinely **new** table. Written for a
> fresh context to execute in goal mode. See **Next to consider** at the
> bottom for the broader new-table backlog so it isn't lost.

## Status & handoff (2026-06-11)

**v1 is implemented** in PR **#286** (`feat/push-fold-6max-revival`), revived from
the original (superseded) `push-fold-6max` branch and re-integrated against current
`main`. As of this note it is mergeable with CI green/pending; the original
branch can be deleted once #286 lands.

**What shipped (v1):**
- `poker/strategy/data/push_fold_6max.json` (+ `generate_push_fold_6max.py`,
  `push_fold_6max_README.md`) — unopened per-position jams + the `bb_vs_sb` /
  `bb_vs_late` caller tables. `calibration_status: v1_from_published_nash`
  (published-Nash approximations, **not** a fresh solver run — see the README).
- `poker/strategy/push_fold.py::lookup_push_fold_action_6max` — the lookup.
- `poker/tiered_bot_controller.py::_try_push_fold_6max` — routing (split out of
  `_try_push_fold_lookup`; HU path unchanged in `_try_push_fold_hu`).
- Tests: `tests/test_strategy/test_push_fold_6max.py` (chart) +
  `test_push_fold_routing.py` (HU-vs-6max dispatch + the scope gates below).

**Scope is single-villain, fail-closed.** v1 only fires in the exact spots the
chart models and **falls through to the deep-stack / `short_stack.py` path** for
everything else. Four gates (all with regression tests) enforce this — see
**v1 scope boundaries** below: (1) 2+ all-ins, (2) limp / iso pots, (3) a short
all-in under a larger live raise, (4) a non-BB hero facing a jam. These were
review findings layered on after the initial revival; if you touch the routing,
keep the fail-closed posture.

**Open / deferred (a new context picks up here):**
1. **Validation harness — BUILT (2026-06-11).** `--start-bb N` is now in
   `simulate_bb100.py` (overrides `--stack` with `N*big_blind`), and the 6max
   runner re-threads `decision_analysis_repo`/`game_id` so the hero's
   `push_fold_routed` snapshot persists. Full short-stack A/B vs a human-like
   opponent (the bb/100 question) is still TODO; the **routing-coverage** read
   below is done and is the more actionable finding.
2. **Reshove table — BUILT + fold-equity-gated + ON (2026-06-11).**
   Jam-or-fold over a single non-all-in open, `reshove` section of
   `push_fold_6max.json` (depth-keyed 8/10/12/15, `[L]`), behind
   `PUSH_FOLD_6MAX_RESHOVE_ENABLED` (now **dev+prod ON**). Detection is the
   controller-agnostic `push_fold.reshove_action_6max` (fail-closed on 3-bet wars
   / cold-callers / multiway / all-ins). With it on, 10 BB routing coverage jumps
   **~17% → 98%** (the reshove spot was the 66% fall-through).

   **Validation story (the loop):**
   - **Unconditional reshove FAILED** the bb/100 A/B: TAG vs the call-happy rule
     mix, **−21 / −35 / −52 bb/100** at 8/10/12 BB (worsening with depth); vs a
     competent field (GTO-Lite/ABCBot) @10 BB a wash (**−2.6**). ⇒ reshove is
     **field-dependent** — Nash-neutral vs openers who fold, catastrophic vs
     openers who don't (risk ~10–12 BB to win ~3.7 BB with no fold equity).
   - **Fix = fold-equity gate** (`exploitation.reshove_fold_equity_ok`): reshove
     only when the opener has demonstrated fold equity. The load-bearing signal
     is **loose VPIP, not passivity** — `vpip_per_voluntary_opportunity > 0.65`
     suppresses BOTH stations and maniacs (the first cut keyed on `_is_hyper_passive`
     and MISSED ManiacBot: vpip 0.97 but AF 4.0, so it read "aggressive" yet never
     folds). Plus a min-sample gate (no read → no reshove; cost is asymmetric).
   - **Gated reshove re-validated (safety):** bb/100 vs the rule mix is now
     **+0.0 at every depth** (the −35 leak is gone — the gate suppresses reshove
     vs every rule-bot opener, all of which are vpip>0.65 non-folders).
   - **Upside PROVEN vs a folder (the loop closed):** the rule bots can't show
     reshove's upside (none fold to 3-bets), so this needed a folding opener. The
     clone engine had the same hole — `human_clone.build_clone_strategy` re-raised
     its whole opening range facing a 3-bet (never folded), which is why every
     clone "turned into a calling station." Fixed it (a disciplined reg now folds
     the bottom of its opens to a re-raise; a station stays wide). Re-ran vs a
     **Punisher_clone field** (`FIELD=punisher`): reshove fires (gate allows the
     vpip-0.25 reg) and is **−1.2 / +0.7 / +2.7 bb/100** at 8/10/12 BB — neutral
     to mildly positive, with the lean GROWING with depth (it was *worst*, −52,
     there vs non-folders). CIs overlap zero, which is correct: Nash reshove
     ranges are built ~break-even vs a disciplined opener; the big wins only come
     vs over-folders, which punisher isn't. **Net: Pareto-safe — never −EV
     (gated off vs non-folders), mildly +EV where fold equity exists.**
   - **Decision:** ON — triple-gated (flag + per-persona `push_fold_nash` +
     fold-equity read), no-leak, with a demonstrated positive lean vs folders.

   Probe: `experiments/reshove_bb100_probe.py` (`FIELD=competent` / `FIELD=punisher`).
   Remaining v2 refinements: opener-position-agnostic (tighten vs early opens);
   validate upside vs Jeff_clone; other bot types could opt the detector in.
3. **v2 ranges still open**: real multi-jammer call ranges and cold-caller
   modeling (both still documented fall-throughs).
4. **Ante variant** — ranges are no-ante; the live SNG's ante status is unconfirmed.

### Validation results (2026-06-11)

Ran `simulate_bb100 --six-max-vs-rules --start-bb 10` with decision persistence
(3 tiered archetypes × 200 hands @ 10 BB; 604 push/fold-**eligible** preflop
decisions, i.e. ≤15 BB, 3–6 handed). Instrumented `_try_push_fold_6max`'s return
and the fall-through reason.

| Spot | Share of eligible | v1 routes it? |
|---|---|---|
| **Facing a single non-all-in open** (reshove-or-fold) | **66%** (396) | ❌ → deep-stack/short_stack (reshove = v2) |
| Unopened first-in (jam/fold) | 17% (96 fold, **6 jam**) | ✅ |
| BB unopened / facing a raise | 16% (96) | ❌ |
| Facing a clean single all-in (caller table) | ~2% (10) | ✅ (but shadowed — see below) |

**Headline:** the v1 table only **decides ~17%** of short-stack preflop spots.
The dominant spot (66%) is **facing a single open-raise** — and of those, **99%
are a single open** (not a 3-bet war) sized **2–3 BB** into the 10 BB stack. So
short-stack play here is overwhelmingly **reshove-or-fold facing a min-open**,
which v1 explicitly defers. This is a sim of rule bots that *size-raise* rather
than open-jam, but that matches real short-stack play (good players open small at
10 BB, not shove). ⇒ The reshove table is the highest-value next piece, not an
optional v2.

**Caller tables are shadowed.** The `bb_vs_sb`/`bb_vs_late` ranges return
`'call'`/`'fold'` at the push/fold route, but `_facing_all_in_preflop_veto`
(the #271 pot-odds override) runs immediately after on the *same* facing-a-jam
spots and returns first — so the published Nash caller chart almost never
actually decides; the equity-based veto wins. Functionally fine (pot-odds ≈ the
Nash caller range), but the JSON caller tables are effectively dead weight given
the veto. Decide in v2 whether to keep them.

**Vocab hardening shipped alongside (then centralized).** The caller path
returned the abstract `'call'`; on a call-off (engine offers only `all_in`) raw
`'call'` would fall back to **fold** (folding a hand the chart said to call).
The first fix translated at the push/fold route; it was then **centralized** at
the abstract→engine boundary — `resolve_preflop_sizing` / `resolve_postflop_sizing`
now take `valid_actions` and resolve a call-off `'call'` to `('all_in', stack)`
via `abstract_call_token`, the same place `JAM` is mapped. That removed the
scattered call-off handling (the push/fold pre-translate + the veto's inline
call/jam branch); the veto now just emits `'call'`. `value_override` keeps its
own `abstract_call_token` — it's a shared producer also consumed by `replay.py`,
which doesn't go through these resolvers. (`AbstractAction.JAM` → engine
`all_in`; there is no abstract `all_in` — see `action_vocab.py`. Don't collapse
the two vocabularies; the split prevents a known `action_mapper` crash.)

## TL;DR

The tiered bot already has a **HU** short-stack push/fold table
(`push_fold_hu.json`, ≤15 BB). Multi-way short-stack spots currently **fall
through to the `short_stack.py` heuristic** (which just suppresses medium
raises — not a Nash range). The real game is **winner-take-all sit-and-go**,
where most consequential decisions happen at **25 BB and shorter**, so this is
the #1 leak in the *actual* game. Build `push_fold_6max.json` (Nash chip-EV
ranges by position × depth) and route multi-way short-stack preflop decisions
to it. **Prerequisite: a short-stack sim** — `simulate_bb100` is 100 BB-only
and will never exercise this table.

## Why (the leak)

- Live game = WTA SNG. SNGs start deep, blinds escalate; by 3–4 handed,
  stacks are commonly 25–50 BB; HU final ≈ 15–25 BB.
- The bot plays **100 BB-tuned tables at short-stack depths** for most
  consequential decisions. The 100 BB ranges call too wide, raise too small,
  and miss push/fold transitions.
- HU short stacks are handled (`push_fold_hu.json`). **Multi-way short stacks
  are not** — they hit the `short_stack.py` heuristic, which is a band-aid.
- This does **not** show up in `simulate_bb100` (always 100 BB). It only
  affects the real SNG game — which is the focus.

## The model: chip-EV Nash, ICM OFF

Winner-take-all ⇒ one payout ⇒ tournament equity is **linear in chips** ⇒
$EV = chip-EV ⇒ the **ICM correction term vanishes**. So use the **chip-EV
Nash push/fold equilibrium with ICM = OFF** (the classic SHAL / Mathematics
of Poker tables apply *exactly*, no bubble/pay-jump tightening). Do **not**
apply ICM.

## Conventions (read before encoding)

- **Effective stack** = min(hero stack, largest stack still to act), in BB,
  including blinds posted. For the lookup key, use **hero's effective BB**
  (matches how every published chart is indexed; the controller already
  computes this — see integration map).
- **No ante.** Ranges below are the no-ante equilibrium; antes widen
  everything ~3–8%. If the SNG has antes, these are a tight-side approximation.
- **Positions (6-max):** UTG (4 players behind), HJ/MP (3), CO (2), BTN (1),
  SB (1, only BB behind). **BB never open-shoves** unopened (folded-to-BB is a
  walk) — BB appears only as a *caller*.
- **Early-position tightening is the dominant effect:** more players behind ⇒
  much tighter unopened jam than the HU SB chart at the same depth (UTG at
  10 BB jams ~6%, vs SB ~37%).
- **Pure jam-or-fold.** Where real solvers mix in min-raises (≥~12 BB), the
  *pure-jam* frequency is the tight-side component listed here — correct for a
  jam-or-fold bot.
- Confidence tags: **[H]** cross-validated, **[M]** single-source/interpolated,
  **[L]** extrapolated (carry the tag per cell; let the bot log/fallback on [L]).

## The ranges (research spec — Nash chip-EV, ICM off)

### Unopened shove (jam-or-fold, first in), position → depth → range

**UTG** (tightest):
| Depth | Shove range | ~% | conf |
|---|---|---|---|
| 4 BB | `22+, A2s+, A2o+, K8s+, KTo+, QTs+, JTs` | 18% | L |
| 6 BB | `22+, A2s+, A7o+, K9s+, KJo+, QTs+, JTs` | 12% | M |
| 8 BB | `22+, A2s+, A7o+, KTs+, KJo+, QJs` | 9% | H |
| 10 BB | `55+, ATs+, AJo+, KQs` | 6.2% | H |
| 12 BB | `66+, ATs+, AJo+, KQs` | 6% | H |
| 15 BB | `77+, AJs+, AQo+, KQs` | 5% | H |

**HJ / MP** (3 behind):
| Depth | Shove range | ~% | conf |
|---|---|---|---|
| 4 BB | `22+, A2s+, A2o+, K6s+, K9o+, Q9s+, QTo+, J9s+, T9s` | 24% | L |
| 6 BB | `22+, A2s+, A4o+, K9s+, KTo+, Q9s+, QJo, JTs` | 14% | M |
| 8 BB | `22+, A2s+, A7o+, KTs+, KJo+, QJs` | 9% | H |
| 10 BB | `33+, A8s+, ATo+, KTs+, KQo, QJs` | 9–10% | H |
| 12 BB | `22+, A8s+, A8o+, KTs+, KJo+, QTs+, JTs` | 11% | M |
| 15 BB | `44+, ATs+, AJo+, KTs+, KQo, QJs` | 8% | H |

**CO** (2 behind):
| Depth | Shove range | ~% | conf |
|---|---|---|---|
| 4 BB | `22+, Ax, Kx, Qxs, Q7o+, Jxs, J8o+, T7s+, T9o, 97s+` | 38% | L |
| 6 BB | `22+, Ax, K7s+, K9o+, Q9s+, QTo+, J9s+, T9s, 98s` | 22% | M |
| 8 BB | `22+, Ax, Kx, Qxs, Q5o+, Jxs, J8o+, T7s+, T9o, 97s+` | 30% | H |
| 10 BB | `22+, Axs, A5o+, Kxs, K9o+, Q9s+, QTo+, JTs` | 15.8% | H |
| 12 BB | `22+, Axs, A8o+, KTs+, KJo+, QTs+, JTs` | 12% | M |
| 15 BB | `44+, ATs+, AJo+, KTs+, KQo, QJs` (jam-only slice) | 10% | M |

**BTN** (1 behind):
| Depth | Shove range | ~% | conf |
|---|---|---|---|
| 4 BB | any two (~100%) | 100% | H |
| 6 BB | `22+, Ax, Kx, Qx, Jxs, J5o+, Txs, T7o+, 9xs, 97o+, 8xs, 86s+, 75s+, 65s` | ~52% | M |
| 8 BB | `22+, Ax, Kx, Qx, Jxs, J5o+, Txs, T7o+, 9xs, 97o+, 8xs` | 40% | H |
| 10 BB | `22+, Ax, Kxs, K5o+, Qxs, Q7o+, Jxs, J8o+, T7s+, T9o` | 26.8% | H |
| 12 BB | `22+, Axs, A4o+, Kxs, K8o+, Q9s+, QTo+, J9s+, JTo` | 20% | M |
| 15 BB | `22+, Axs, A7o+, KTs+, KJo+, Q9s+, QTo+, JTs` (jam-only) | 16% | H |

**SB** (= the exact HU SB Nash pusher chart — highest confidence; mirror
`push_fold_hu.json`'s `sb_open` if already encoded):
| Depth | Shove range | ~% | conf |
|---|---|---|---|
| 4 BB | any two (~100%) | 100% | H |
| 6 BB | ~60% (any A/K/Q + most suited + offsuit broadway-ish; boundary J6o/T7o/64s) | 60% | H |
| 8 BB | `22+, Ax, Kx, Qx, Jx, T2s+, T6o+, 95s+, 97o+, 85s+, 75s+, 64s+, 54s` | 52% | H |
| 10 BB | `22+, Ax, Kx, Qxs, Q3o+, Jxs, J7o+, T6s+, T8o+, 97s+, 98o, 86s+, 76s, 65s, 54s` | 37.5% | H |
| 12 BB | `22+, Ax, Kxs, K4o+, Qxs, Q7o+, J7s+, J9o+, T7s+, T9o, 97s+, 87s, 76s, 65s` | 30% | H |
| 15 BB | `22+, Axs, A2o+, Kxs, K7o+, Q8s+, Q9o+, J8s+, JTo, T8s+, 98s, 87s` | 22% | H |

### Call-vs-shove (facing an all-in)

**BB vs SB jam** (canonical HU Nash *caller* chart — [H], fully cross-validated;
note the caller is tighter than the pusher at every depth — no fold equity):
| BB depth | Calling range | ~% |
|---|---|---|
| 4 BB | `22+, A2s+, A2o+, K2s+, K2o+, Q2s+, Q4o+, J4s+, J7o+, T6s+, T8o+, 96s+, 98o, 86s+, 75s+, 65s, 54s` | 55% |
| 6 BB | `22+, A2s+, A2o+, K2s+, K5o+, Q5s+, Q9o+, J7s+, JTo, T7s+, T9o, 97s+, 87s, 76s` | 42% |
| 8 BB | `22+, A2s+, A4o+, K5s+, K9o+, Q8s+, QTo+, J8s+, JTo, T8s+, 98s` | 33% |
| 10 BB | `22+, A2s+, A7o+, K9s+, KTo+, Q9s+, QTo+, J9s+, T9s` | 24.5% |
| 12 BB | `22+, A3s+, A9o+, KTs+, KJo+, QTs+, QJo, JTs` | 19% |
| 15 BB | `33+, ATs+, AJo+, KJs+, KQo, QJs` | 13% |

**Blinds vs a late-position (BTN/CO) jam** [M]:
| BB depth | Call-vs-BTN-jam | ~% |
|---|---|---|
| 6 BB | `22+, A2s+, A4o+, K7s+, K9o+, Q9s+, QTo+, J9s+, T9s` | 28% |
| 8 BB | `22+, A2s+, A7o+, K9s+, KTo+, Q9s+, QTo+, J9s+, T9s` | 24% |
| 10 BB | `22+, A2s+, A9o+, KTs+, KJo+, QTs+, QJo, JTs` | 18% |
| 12 BB | `22+, A5s+, ATo+, KJs+, KQo, QJs` | 14% |
| 15 BB | `44+, ATs+, AJo+, KQs` | 9% |
- vs a **CO** jam: nudge ~1–2% wider. vs **UTG/HJ** jam (tight openers): call
  *tighter* (~`66+, ATs+, AJo+, KQs` regardless of depth) — **[L]**.

### Reshove (jam over a min-raise/limp) — **[L], lower confidence, gate behind a flag**
| Hero depth | Reshove range | ~% |
|---|---|---|
| 8 BB | `22+, A4s+, A8o+, K9s+, KJo+, Q9s+, QJo, JTs` | 16% |
| 10 BB | `33+, A7s+, A9o+, KTs+, KJo+, QTs+, QJo` | 13% |
| 12 BB | `44+, A9s+, ATo+, KJs+, KQo, QJs` | 10% |
| 15 BB | `55+, ATs+, AJo+, KQs, AKo` | 7% |
Treat sections **unopened + call-vs-shove (2–3)** as the trustworthy core;
reshove is optional v2.

## Integration map (file:line — verify, point-in-time from 2026-05-24 recon)

- **Decision routing:** `poker/tiered_bot_controller.py` `_get_preflop_decision`
  attempts push/fold **before** the deep-stack table lookup (`:531-546`):
  calls `_try_push_fold_lookup(...)` (`:2664-2741`); if it returns an action,
  `base_strategy = StrategyProfile({action: 1.0})`, else falls to
  `preflop_table.lookup_with_fallback`.
- **The HU-only gate to lift:** `_try_push_fold_lookup` `:2684` —
  `if num_seated != 2: return None`. `num_seated = len(game_state.players)`
  (`:512`). Branch this: HU → existing `lookup_push_fold_action`; multi-way
  (`num_seated > 2` AND short) → new `lookup_push_fold_action_6max(...)`.
- **Library:** `poker/strategy/push_fold.py` — `lookup_push_fold_action(hand,
  position, effective_stack_bb, num_opponents=1, facing_jam=False)`; HU gate
  re-asserted `:113` (`if num_opponents != 1: return None`); threshold
  `PUSH_FOLD_THRESHOLD_BB = 15.0` (`:37`); `_nearest_bucket` (`:68-83`);
  module cache + `reset_chart_cache()`.
- **Effective stack** already computed multi-way in `_try_push_fold_lookup`
  (`min(hero, max active opp)/BB`); canonical helper `poker/stack_utils.py:50`
  `effective_stack_bb`.
- **Position:** `poker/strategy/preflop_classifier.py:31` `get_6max_position`
  returns UTG/HJ/CO/BTN/SB/BB — use this instead of the SB/BB-only branch.
- **Hand canonicalization:** `poker/controllers.py:398` `_get_canonical_hand`
  → 'AKs'/'AKo'/'AA' format (chart keys must match).
- **Generator to mirror:** `poker/strategy/data/generate_push_fold_hu.py` —
  `all_canonical_hands()` (169 hands stable order), `_hand_strength_rank()`
  (equity proxy), per-depth top-N range tables, `build_chart()`. Regenerate via
  `python -m poker.strategy.data.generate_push_fold_hu`.
- **HU chart schema to mirror:** `push_fold_hu.json` — `meta` + per-depth
  buckets, each scenario a map of all 169 hands → `{action: prob}`.

## Proposed `push_fold_6max.json` schema

```json
{
  "meta": {"format": "push_fold_6max_v1", "version": "1.0",
           "model": "chip_ev_nash_icm_off", "ante": false,
           "depth_bb_buckets": [4,6,8,10,12,15],
           "calibration_status": "v1_from_published_nash"},
  "unopened": {
    "UTG": {"8": {"AA": {"jam": 1.0}, ... "72o": {"fold": 1.0}}, "10": {...}, ...},
    "HJ": {...}, "CO": {...}, "BTN": {...}, "SB": {...}
  },
  "call_vs_shove": {
    "bb_vs_sb":   {"4": {...}, ... },
    "bb_vs_late": {"6": {...}, ... }
  }
}
```
Carry a per-cell/per-table confidence tag (H/M/L). Lookup: snap hero effective
BB to nearest bucket; above 15 BB return `None` (defer to deep-stack table);
clamp below the lowest bucket. BB never appears in `unopened`.

## Build sequence (ordered, with gates)

1. **Write the spec README first** — `poker/strategy/data/push_fold_6max_README.md`
   (source = this doc + the published-Nash citations; per project convention,
   README before data). Document conventions, confidence tags, limit cases.
2. **Generator** — `poker/strategy/data/generate_push_fold_6max.py` mirroring
   the HU generator: reuse `all_canonical_hands()` + `_hand_strength_rank()`;
   replace the two HU scenarios with per-position unopened ranges + the two
   call tables, driven by per-position/per-depth range-size (or explicit hand
   lists) from the ranges above. Emit `push_fold_6max.json`.
3. **Chart-loader test** — assert all 169 hands present per (position, depth);
   per-row sums = 1.0; AA/KK jam from all positions; aggregate jam% per
   (position, depth) lands in the target band (±a few %); 72o ~0% jam.
4. **`push_fold.py` multi-way lookup** — add `lookup_push_fold_action_6max(hand,
   position, effective_stack_bb, num_players, facing_jam, opener_position=None)`
   (or generalize `lookup_push_fold_action` to dispatch on chart). Reuse
   `_nearest_bucket`/cache machinery.
5. **Controller wiring** — lift the `:2684` HU gate; route multi-way short
   stacks (num_seated > 2, eff_bb ≤ 15) to the 6max lookup using
   `get_6max_position`. Keep HU path unchanged. Unit-test routing (3-/6-player
   states → 6max chart; >15 BB → None → deep-stack table; HU → HU chart).
6. **Short-stack sim harness (PREREQUISITE for validation)** — `simulate_bb100`
   is 100 BB-only (`starting_stack=10000, BB=100`). Add a **stack-depth knob**
   (e.g. `--start-bb`) or a small SNG-style runner so matchups can be played at
   8/12/15/25 BB effective. Without this, the table is untestable. *(This also
   unblocks validating every future short-stack table — worth doing well.)*
7. **Validate** — at 8/12/15 BB, confirm (a) push/fold actually fires
   (`push_fold_routed` snapshot flag), (b) action distribution matches the
   chart's jam/call% bands, (c) bb/100 vs rule bots improves or holds at those
   depths vs the current heuristic-only path. Direction-only on bb/100 (noisy);
   action-distribution is the primary gate.

## Risks / decisions

- **Validation harness is real work** — don't skip it; "looks right in code"
  ≠ confirmed. (We learned this the hard way on the sizing investigation.)
- **`[L]` cells** (4 BB early-position, reshove) are extrapolated — encode with
  the confidence tag and consider deferring reshove to v2.
- **Ante handling** — ranges are no-ante. Decide whether the SNG uses antes; if
  so these are tight. (Could add an ante-on variant later.)
- **Multi-way effective-stack edge cases** — verify the binding stack when 3+
  players with different stacks; the controller's `min(hero, max active opp)`
  is the v1 simplification and matches published indexing.
- **Don't overfit** — same caution as the bb/100 work: validate against a
  human-like opponent if possible, not just rule bots.

## v1 scope boundaries (single-villain only — deferred to v2)

The caller tables (`bb_vs_sb` / `bb_vs_late`) and the lookup
(`lookup_push_fold_action_6max`, single `opener_position`) model **one** villain.
`_try_push_fold_6max` enforces that and **falls through** (returns `None` → the
deep-stack / `short_stack.py` path) for spots it can't represent, rather than
applying a wrong/too-loose range:

- **Multi-way all-in (2+ opponents already jammed).** Calling vs two+ ranges is a
  tighter, distinct spot the single-jammer caller table over-calls. v1 detects
  `len(jammer_indices) > 1` and falls through (see
  `test_6max_multiple_jammers_returns_none`). **v2:** real multi-jammer call
  ranges (and an unambiguous "which jammer" rule, à la
  `exploitation.select_primary_aggressor`).
- **Reshove (jam over a min-raise / limp).** Already deferred (`[L]` confidence);
  a non-all-in raise in front of hero returns `None`.
- **Cold-caller multi-way.** Like the deep-stack vs_open/vs_3bet/vs_4bet charts,
  the unopened/caller ranges are keyed on hero vs a single relevant raiser; a
  cold-caller between the jammer and hero tightens the true range but isn't
  modeled. This is the standard position-vs-opener simplification, shared with
  the deep-stack charts — a known approximation, not a v1 blocker.
- **Table size 3–6 handed only.** The position labels are 6-max. The lookup
  gates `num_players` to 3–6: 7/8-max early seats have more players behind than
  any 6-max range models, and 9+ can't even be labeled (`poker_game` collapses
  9+ tables to blinds-only, so `get_6max_position` falls back to UTG). 7+ handed
  short stacks fall through to the deep-stack / short_stack.py path. **v2:** a
  7–9-max table-size dimension (the app exposes 9-max tournaments) if those
  spots prove to matter.

## Next to consider (broader new-table backlog — DO NOT LOSE)

The push/fold table is the most-ready new table and fixes the real SNG leak.
After it, in priority order:

| Table | Moves 100bb sim? | Fixes real SNG game? | Readiness | Notes |
|---|---|---|---|---|
| **Multiway push/fold** (this doc) | No | **Yes (#1 SNG leak)** | **High** | research done, integration mapped |
| **Limped-pot postflop tree** | Yes | Yes | Medium | documented #1 postflop leak (vs CaseBot limps); ~720 entries; **verify it isn't inert** first — SRP chart edits were overridden/no-op'd by the passive-bot + override layers |
| **3-bet-pot postflop tree** | Yes | Yes | Medium | documented #2 postflop leak |
| **Stack-depth preflop variants** (50/30 BB) | partial | Yes (SNG mid-stack) | Medium | hand-tighten the 100 BB chart for the 20–50 BB band |
| **Solver-derived program** (the "~32 sets") | Yes (long-term) | Yes | Low | the big bet — see `docs/plans/SOLVER_CHART_SCOPE.md` |

**My read on where to go after push/fold:**
- The deepest structural ceiling is **postflop passivity** — the no-personality
  Baseline bets ~4% / raises ~0% postflop because most spots are multiway where
  the `multiway.py` layer (correctly) suppresses aggression, and the bot enters
  too many marginal multiway pots. Per-street *chart* edits were proven **inert**
  for this (overrides + passivity dominate). The higher-ceiling fix is the
  **multi-street context layer** the coach already has (`flask_app/services/
  context_builder.py`: `player_bet_flop`, `opponent_double_barrel`) — port those
  signals into the tiered postflop decision so the bot can continue initiative
  ("I c-bet, keep barreling") and stop paying off double barrels. That's likely
  worth more than any single new postflop chart.
- The **eval-target** decision is still open: bb/100 is measured only vs the
  rule-bot roster. Add a **`Jeff_clone`** opponent (the harness supports
  `--clone-opponent`, derived from real hand history) to guard against
  rule-bot overfit before trusting any further chart change.
- **The bar:** we went −144.9 → −110.9 bb/100 (still deeply negative). Cheap
  chart/sizing tweaks keep chipping; **positive bb/100 likely needs the
  structural fixes** (multi-street layer and/or the solver program). Make an
  explicit go/no-go on those as the next phase.

## Over-a-limper ISO (v1, flag `PUSH_FOLD_FIRST_IN_OVER_LIMPER_ENABLED`, off)

The chart-opportunity census flagged this as the #1 short-stack gap: a ≤15bb hero
first-in-*to-raise* with a single **limper** in front used to bail (the limped pot
isn't "unopened", so `_try_push_fold_6max` returned None) and the spot fell to the
**deep-stack** chart — wrong at 10-15bb.

v1 routes that spot (exactly one limper; multi-limper still falls through) to a new
`over_limper` lookup path. There is **no dedicated `iso_over_limper` chart section
yet**, so it resolves to the **`unopened` jam range** as a conservative proxy: those
ranges are tight at 10-15bb, so jamming them over a limper is low-spew and a strict
improvement over the deep-stack fallback. `_resolve_6max_over_limper_scenario` reads
an `iso_over_limper[pos][depth]` section first if one is ever added, so a sim-tuned
table drops in with no caller change.

Gated **off** pending a bb/100 sim. Heed the reshove lesson: short-stack jams into a
call-happy (limp-call-wide) field can be badly −EV without fold equity — evaluate a
fold-equity gate (à la `reshove_fold_equity_ok`) and a dead-money-aware widen before
turn-on. The dedicated `iso_over_limper` ranges are the sim-tuned follow-up.

### Sim result (`experiments.iso_over_limper_probe`, 2026-06-12)

A/B (flag OFF vs ON), `TAG` hero vs a single-limper field (one `LIMPS_EVERY_HAND`
fish + four rocks → frequent one-limper spots), 2×2000 hands/arm:

| depth | OFF bb/100 | ON bb/100 | delta | iso fires (jam) |
|---|---|---|---|---|
| 10BB | +22.6 | +14.9 | **−7.7** | 6522 (1291) |
| 12BB | +23.3 | +19.4 | **−3.9** | 7000 (1064) |

- **Coverage is strong** — the path fires thousands of times (unlike the reshove,
  which barely fired in the rule-bot field). The mechanism reaches the spot.
- **Naive turn-on LOSES** ~4-8 bb/100, worse at shallower stacks. Cause: the fish
  **never folds**, so the iso-jam has **zero fold equity** — it gets called by any
  two and the limper realizes its equity. The textbook no-fold-equity leak.
- CIs overlap at 4k hands (short-stack bb/100 is noisy), but the sign is consistent
  across both depths and the quick run. The conclusion is direction + mechanism, not
  the exact magnitude.

**Verdict:** confirms gate-off. Turn-on REQUIRES a fold-equity gate (suppress the
iso-jam vs a limper read as sticky — `limp_call_wide`/loose-VPIP, the same signal
the reshove gate uses). This worst case (a never-folder) bounds the downside; a
foldy limper is where the iso wins, but there is no limp-FOLD fish leak to measure
that arm — it needs a `Jeff_clone`-style foldy limper or a synthetic limp-fold leak.

## Sources
Published Nash chip-EV (ICM-off) push/fold references: Mathematics of Poker
(Chen & Ankenman), HoldemResources HUNE tables, gamblingcalc Nash push/fold
chart (6-max by depth, no-ante), mypokercoaching / Upswing / 888poker push-fold
charts, pokerstrategy SNG Nash ranges. SB-unopened, BB-vs-SB-call, and the
8/10/15 BB position anchors are cross-validated [H]; 4 BB early cells and
reshove are extrapolated [L].
```
