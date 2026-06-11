---
purpose: Build plan for a multi-way (6-max) short-stack push/fold lookup table for the tiered bot, plus the short-stack sim harness needed to validate it
type: design
created: 2026-05-24
last_updated: 2026-06-11
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
1. **Validation gate (biggest item).** The ranges are `v1_from_published_nash`
   approximations and have **not** been validated in a real short-stack sim. The
   prerequisite sim knob is *not* yet built — `simulate_bb100` is 100 BB-only.
   Porting `--start-bb` short-stack support (it was on the original branch, ~26
   commits diverged from current `simulate_bb100.py`; rebuild rather than
   cherry-pick) is the unblock. Then run a short-stack A/B vs a human-like
   opponent (not just rule bots) — see **Risks / decisions**.
2. **v2 ranges** (see **v1 scope boundaries**): real multi-jammer call ranges,
   the reshove table (jam over a min-raise — researched at `[L]` confidence
   below), and cold-caller modeling. Each is currently a documented fall-through.
3. **Ante variant** — ranges are no-ante; the live SNG's ante status is unconfirmed.

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

## Sources
Published Nash chip-EV (ICM-off) push/fold references: Mathematics of Poker
(Chen & Ankenman), HoldemResources HUNE tables, gamblingcalc Nash push/fold
chart (6-max by depth, no-ante), mypokercoaching / Upswing / 888poker push-fold
charts, pokerstrategy SNG Nash ranges. SB-unopened, BB-vs-SB-call, and the
8/10/15 BB position anchors are cross-validated [H]; 4 BB early cells and
reshove are extrapolated [L].
```
