---
purpose: Fresh-context handoff — fix the preflop-chart measurement bug, then settle (and maybe ship) wider preflop opening, en route to opponent-adaptive open width
type: guide
created: 2026-05-26
last_updated: 2026-05-27
---

# Preflop-width handoff

> **✅ RESOLVED 2026-05-27.** (1) The "measurement bug" was a **misdiagnosis** —
> the `--preflop-chart` swap is sound; the no-op was a rule-mix roster artifact
> (the hero is never first-in from CO/BTN/SB vs never-folding bots, so an RFI
> change is definitionally inert). Verified via lookup-spy + PFR 14→19% + 10/200
> hands differing vs jeff/punisher. (2) Decision SETTLED + SHIPPED: paired
> steal-aware A/B (`ab_preflop_width.py`, 24k hands) → wider CO/BTN/SB is
> CI-clear +EV (jeff +15.97, punisher +5.33); `preflop_100bb_6max.json` widened,
> tight chart preserved, depth charts left tight (unmeasured at short stacks),
> README/provenance updated, `test_strategy` green. (3) Destination scaffolded:
> `docs/experiments/EXP_003_OPPONENT_ADAPTIVE_RFI_WIDTH.md` (adaptive width must
> be table-selection — offsets can't open fold-1.0 hands). NOT yet committed.
> Below is the original handoff, kept for the record.

> **For a fresh context.** Self-contained. Branch `lookup-tables`. All Python
> runs in Docker: `docker compose exec -T backend python ...`. Read the memory
> notes `project_eval_harness_audit.md` (the "⚠ MEASUREMENT BUG" +
> "GTO-yardstick spinoffs" sections) and `project_tieredbot_bb100_lookup_tables.md`
> before starting.

## Goal
Re-validate the preflop-chart measurement instrument, then settle — and if
confirmed, **ship** — widening late-position RFI toward GTO. Destination:
opponent-adaptive open width (widen vs folders, tighten vs calling stations).

## What's already DONE — do not redo
- **SNG champion-challenger gate hardened (P0–P4)** — `8eb9f3a5`. Antithetic
  role-swap, bootstrap CI over seed-blocks, outcome accounting; calibrated
  (A-A null → exactly 50%; cripple → 0%/100%). See `SNG_RUNNER_HARDENING.md`.
- **Cut the low-SPR / 3BP precision slices** — `0164ce64` (gate measured them
  neutral; SPR/pot_type fallback + `postflop_commit` kept).
- **HU push/fold chart recomputed from exact chip-EV Nash** — `66586d76`
  (`generate_push_fold_nash.py`; the placeholder folded A6o/KQo/KJo at 15bb).
- **`LOOKUP_TABLE_PROVENANCE.md`** added — `f2a1f3b8`.
- All stable: **128 goal-tests green** on current HEAD (`8f1ccc49`, a parallel
  session's "canonical action vocab" commit). Production
  `preflop_100bb_6max.json` + `push_fold_hu.json` committed/unchanged.

## Methodology that must carry forward (earned this session)
- The **self-play SNG gate is blind to steal/exploitation value** — it's
  symmetric, so a change whose value comes from an opponent's leaks (stealing
  blinds, value-extracting a station) reads ~neutral in self-play even when it's
  +EV vs a real opponent. Measure those vs `punisher`/`jeff`, not (only) the gate.
- An **external-truth / GTO divergence is a hypothesis generator, not a verdict.**
  Twice this session a "GTO says we're wrong" flag turned out NOT to be a leak
  (the −18/−22 shallow "leak" was a Jeff-station artifact; tight preflop is
  correct compensation for weaker postflop). Always *measure* the flag.
- **Verify the instrument before the result.** (This is exactly what bit us —
  see the blocker.)

## ⚠ BLOCKER — fix this FIRST (the measurement is broken)
The `measure_passivity --preflop-chart` / `hero_table` swap **silently no-ops**
on the current HEAD, so every wider-preflop number measured after the reboot is
invalid. Decisive repro (deterministic, ~1 min):

```
docker compose exec -T backend python -c "
from experiments.measure_passivity import run_passivity_matchup
from poker.strategy.strategy_table import load_strategy_table
base  = load_strategy_table()
wider = load_strategy_table(json_path='poker/strategy/data/preflop_100bb_6max_wider_rfi.json')
opp = ['GTO-Lite','ABCBot','CaseBot','CallStation','ManiacBot']
dt,_ = run_passivity_matchup('Baseline', opp, 200, base, base_seed=42, mode='off', hero_table=None)
dw,_ = run_passivity_matchup('Baseline', opp, 200, base, base_seed=42, mode='off', hero_table=wider)
print('deltas identical?', dt == dw, ' hands differing:', sum(a!=b for a,b in zip(dt,dw)))
"
# observed: deltas identical? True   hands differing: 0   ← BUG
```

**The contradiction (already verified, don't re-derive):** the wider chart
*loads* (BTN combo-weighted open 50.4% vs base 28.0%); the controller *selects*
it at 100bb (`_select_preflop_table(6, 100.0)` → `uses_wider_table=True`); and
`lookup_with_fallback` *differs* (BTN rfi K9o/Q9o/J9o/98o: wider `raise 1.0` vs
base mostly `fold`). Yet the hero plays identically. So the break is between
"table selected" and "decision emitted."

**Candidate leads** (unconfirmed): the `Baseline`/`BaselineSolverBot` decision
path or `StrategyProfile.sample_action`; or an interaction with `8f1ccc49`
(which touched only `poker/strategy/action_mapper.py`, `action_vocab.py`,
`value_override.py` — the jam/all_in vocab). It worked *pre*-reboot (wider-vs-jeff
+68.9 ≠ tight +49.1), so suspect something in that commit or a stale-state issue.

**Acceptance for "fixed":** a wider hero chart must measurably raise the hero's
PFR / change per-hand deltas. Assert it (the repro above must show
`hands differing > 0`, ideally a higher PFR in `measure_passivity`'s PREFLOP
diagnostic) BEFORE trusting any preflop-width A/B.

## The decision waiting on a sound instrument
Does widening CO/BTN/SB RFI toward GTO beat the current tight chart **vs real
opponents**? Pre-reboot signals (NOW UNTRUSTED, must re-measure): +9.5 bb/100 vs
`punisher` reg, +19.8 vs `jeff` station, self-play-neutral. Once the instrument
is verified:
1. Re-run wider-vs-tight vs **punisher AND jeff AND mix** (`measure_passivity
   --preflop-chart <wider> --opponents X --hands 3000 --seeds 42,142,242`).
   (Note: `mix` runs are pathologically slow — multiway per-action equity MC.)
2. **If confirmed +EV:** ship it — update production `preflop_100bb_6max.json`
   (CO/BTN/SB toward GTO), **regenerate the dependent 50/25bb depth charts**
   (`poker/strategy/data/generate_depth_charts.py` derives from the 100bb chart),
   update `preflop_100bb_6max_README.md` calibration log + provenance, re-run
   `tests/test_strategy/`. The README currently records "tight is correct" from
   the self-play-blind measurement — correct it if wider wins vs real opponents.
3. **If neutral/negative:** keep tight, record it, done.

## Destination (after the static-width question is settled)
Opponent-adaptive open width. The exploitation layer already exists:
`poker/strategy/exploitation.py` (`RULE_ORDER`, `classify_opponent_archetype`
→ station/reg/nit) applies preflop logit offsets via the controller's
`_apply_exploitation` (`tiered_bot_controller.py:~646`); `steal_pressure` and
`tight_nit` rules already nudge "widen vs folders." The missing half is
"tighten vs calling stations." Build/strengthen it only after the static wider
baseline is proven — and remember the gate can't see it; measure vs the
opponent spectrum.

## Working-tree state (uncommitted experiment scratch — keep for debugging)
- `poker/strategy/data/preflop_100bb_6max_wider_rfi.json` — GTO-shaped wider
  chart (CO 27.3 / BTN 47.5 / SB 40.3%; UTG/HJ/vs_* byte-identical to base).
- `experiments/measure_passivity.py` — adds `--preflop-chart` (hero-only chart;
  this is the mechanism to debug).
- `experiments/champion_challenger.py` — `btn_wide` / `open_plus_multistreet`
  A/B presets (+ a `wider_rfi` variant may have been removed).
- `*btnwide*` scratch (`build_btnwide.py`, `analyze_btn.py`, etc.) — from a
  parallel session; cleanable.
None of this is shipped. No production behavior changed.
