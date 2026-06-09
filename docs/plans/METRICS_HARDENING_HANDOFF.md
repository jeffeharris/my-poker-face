---
purpose: Handoff for the metrics-hardening workstream — fix the PDA data-completeness bug, validate the Archetype Review/player stats, and consolidate to ONE calculation path (archetypes + the human user) without two stat systems
type: guide
created: 2026-06-10
last_updated: 2026-06-10
---

# Metrics Hardening — Handoff

Goal: make the **Archetype Review** stats (and a new **player/user stats** surface)
*trustworthy in production*. Measurement *correctness* was already solid; the gaps
were *trustworthiness* — what data the stats compute on. This handoff captures the
fix, the validation, the consolidation design, and what's left.

## TL;DR — open PRs (suggested merge order)

| PR | what | base | status |
|---|---|---|---|
| **#258** | Fast-Forward → PDA fix (stops decisions being dropped) | main | open, independent |
| **#259** | hand↔decisions index + PDA completeness monitor (the audit) | main | open, independent |
| **#255** | #12 believability (Phases 1–5) + #9 + WTSD-per-player & c-bet-last-raiser fixes + live↔sim agreement test + research docs | main | open |

Merge **#258 → #259 → #255** (#258/#259 small & safe; #255 is the big review).
Then build the consolidation (below) **off updated `main`**.

## Decisions banked (do NOT re-litigate)

1. **ONE calculation path for live stats — not two.** `player_decision_analysis`
   (PDA) is the single source for all *action* stats (vpip/pfr/3bet/4bet/
   fold-to-3bet/c-bet/fold-to-cbet/af/afq/per-street) for **both** archetypes
   (labeled rows) **and** the human user. The human's rows are *already in PDA*,
   just filtered out by `WHERE strategy_pipeline_snapshot_json IS NOT NULL`. So
   user stats = the same `_aggregate` code, relax the filter + add a `user` bucket.
   (Verified: a human's full multi-street action stream is in PDA, matching
   hand_history exactly.)
2. **`hand_history`'s role = the WTSD/W$SD outcome-join + a completeness AUDIT.**
   W$SD needs winners and WTSD needs the showdown flag — neither is derivable from
   the decision log, both live in `hand_history` (the join already exists:
   `_fetch_showdown_map`). It is NOT re-sourced as the primary stat substrate —
   that rewrite was considered and **rejected**: with the FF bug fixed, PDA is
   complete enough, and `actions_json` is only marginally more complete (postflop-
   superset by ~1% but lacks labels). Keep hand_history as outcome-join + audit.
3. **Sim counters (`archetype_stat_counts`) = the archetype-shaping eval LAB**
   (clean AI-only, written by `cash_mode/full_sim.py`). Different population/purpose
   from live stats. Unchanged. The live↔sim *agreement test* (#255) proves the two
   implementations compute identically on the same play.
4. **The hand↔decisions link = the natural key `(game_id, hand_number)` + a new
   composite index (#259).** 1 hand : N decisions, so the link lives on the child
   (decision→hand). **No surrogate FK** (PDA is written per-decision mid-hand,
   hand_history at hand end, and some hands legitimately have one without the
   other). **No embedding** decisions into hand_history (kills per-decision
   analytics). Completeness is an *audited invariant* (the monitor), not a DB FK.
5. **The c-bet "blind spot" was never a data gap** — it was the bot-only PDA filter
   + the FF bug. Including human/unlabeled rows in the reconstruction *context*
   (attributing archetype stats only to *labeled* rows) fixes it.

## Done this session

- **FF→PDA bug — root-caused + fixed (#258).** When the human folds, cash mode
  engages Fast-Forward and resolves the orbit through an FF controller built with
  `decision_analysis_repo=None`. Double-failure dropped every AI decision from PDA:
  the FF controller's self-save no-ops on a null repo
  (`tiered_bot_controller._persist_decision_analysis` guard), AND the handler
  fallback `analyze_player_decision` defers because it inspects the *original*
  `ai_controllers` entry (which has a repo). `hand_history` still wrote → a
  one-directional, postflop-only PDA gap. Fix: wire the **live**
  `extensions.decision_analysis_repo` into `_get_or_build_ff_controller`
  (`flask_app/handlers/game_handler.py`). Forward-only.
- **Metric validation — three ways.** (a) spec review vs PT4/HM3
  (`ARCHETYPE_SHAPING_FINDINGS.md` §A); (b) **live↔sim agreement test** (#255,
  `tests/test_strategy/test_live_sim_metric_agreement.py`) — both paths agree on
  all 15 banded stats across the tricky cases, anti-circular (mirrors `full_sim`'s
  derivation on the sim side, with a self-test that injects a bug and confirms it
  goes red); (c) **real-data spot check** — ran the live `_aggregate` on a
  read-only prod cash sample + hand-traced (sane; low c-bet traced to the
  documented best-effort gap, not a formula error).
- **WTSD-per-player + c-bet-last-raiser fixes (#255, commit f7062234).** WTSD now
  counts only players who *actually reached* showdown (a flop-seer who folds the
  turn is excluded); c-bet keys on the *last* preflop raiser (the 3-bettor in a
  3-bet pot), not the RFI opener.
- **hand↔decisions index + completeness monitor (#259).** Composite index on
  `player_decision_analysis(game_id, hand_number)`;
  `experiments/pda_completeness_monitor.py` — read-only auditor that FAILs (exit 1)
  when the postflop gap exceeds a threshold. **Verified on prod: FAILs at 1.22%
  (33 player-hands)** — it catches the live FF gap. The standing detector for that
  bug *class*.

## Next — the light consolidation (build AFTER #255 merges; it touches `_aggregate`)

> **Sequencing:** (a) and parts of (c) modify
> `flask_app/routes/archetype_review_routes.py::_aggregate`, which #255 *also*
> modifies (the WTSD/c-bet fixes). Build on #255's merged result to avoid the
> stacked-PR/`_aggregate`-conflict trap. (b) is orthogonal and can go anytime.

- **(a) User stats + c-bet fix (highest value).** In `_aggregate`: stop filtering
  the human; include human/unlabeled rows in the reconstruction *context*
  (opener / c-bet aggressor / saw-flop) but only *attribute* archetype stats to
  labeled rows; add a `user` bucket (is_human from `hand_history.players_json`, or
  infer from null snapshot). One change → **user stats AND the c-bet-aggressor
  blind-spot fix**. Frequency-neutral (measurement only). User WTSD/W$SD use the
  same shared `hand_history` join (winners_json includes the human — verified).
- **(b) `unknown` labeling fix.** ~61% of historical prod cash bot decisions carry
  `deviation_profile_name='unknown'` (pre-#240 labeling bug) → the live per-archetype
  grid is computed on a minority. Fix forward + backfill recoverable rows by
  **recomputing archetype from the persona's anchors**
  (`select_deviation_profile_key`) — robust, per-player, kills the `unknown`
  dominance. Orthogonal to the source.
- **(c) Trust-surfacing.** Surface **coverage %** per archetype (labeled/total) +
  **per-stat confidence gating** (WTSD/W$SD/c-bet need far more n than VPIP/PFR —
  research: WTSD/W$SD ~8k hands). Make low-confidence visually distinct in
  `react/react/src/components/admin/ArchetypeReviewPanel.tsx`.
- **Wire the monitor into cron/CI** (#259's `pda_completeness_monitor`) so future
  PDA conditional-skip bugs are caught automatically.
- **User stats surface:** the repo owner wants **both** admin (a `user` row in the
  grid) *and* a player-facing "my stats" view (a HUD-on-yourself). (a) computes it
  once; admin first, player-facing later.

## Validation state (honest)

Validated: formulas match PT4/HM3 (review); the two live/sim implementations agree
(test); real-data sane (spot check). **Remaining gaps:** no external HUD
ground-truth; best-effort live stats not validated at precision on messy real data;
AFq (nit/rock) + per-street AF + c-bet/WTSD bands are provisional/imported (tune
from accrued sim data, like the 3-bet bands were); sim counters forward-only.

## Key files

- Live aggregation: `flask_app/routes/archetype_review_routes.py` (`_aggregate`,
  `_fetch_showdown_map`, `_build_payload`, the `IS NOT NULL` filter to relax for (a)).
- Sim counters: `cash_mode/archetype_stats.py` (`ArchetypeStatRecorder`),
  `poker/repositories/archetype_stat_repository.py`, `cash_mode/full_sim.py`.
- Targets/bands: `poker/archetype_targets.py`.
- FF path: `flask_app/handlers/game_handler.py` (`_get_or_build_ff_controller`),
  `flask_app/routes/game_routes.py` (`analyze_player_decision` gate ~277),
  `poker/tiered_bot_controller.py` (`_persist_decision_analysis` ~4415).
- Audit: `experiments/pda_completeness_monitor.py`.
- Agreement test: `tests/test_strategy/test_live_sim_metric_agreement.py`.
- Tilt layer (flag OFF, maniac-only): `poker/strategy/tilt_conditioning.py`;
  surfacing: `poker/strategy/spoken_reads.py`. Plan: `PERCEPTIBILITY_CONDITIONING.md`.
- Related: [[ARCHETYPE_SHAPING_HANDOFF]], [[../technical/ARCHETYPE_SHAPING_FINDINGS]],
  [[PERCEPTIBILITY_CONDITIONING]].

## Gotchas / landmines

- **Don't stack a PR on a base that's about to merge.** The #251 trap: it merged
  into its (already-merged-to-main) base and delivered *nothing* to main — had to
  re-PR as #252. Base off `main`; merge in dependency order.
- **Forward-only:** the FF fix and sim counters do NOT backfill historical data.
  Old live stats keep the historical FF gap.
- **Prod read-only access** (repo owner has authorized read-only SSH for these
  checks): `ssh root@178.156.202.136`; query via `docker exec -i poker-backend-1
  python` opening `file:/app/data/poker_games.db?mode=ro`. SELECT only — never a
  write. The auto-approval classifier requires *explicit* prod authorization.
- **ruff pre-push hook reformats** — it aborts the push after reformatting; commit
  the reformat (`git commit --amend` if not yet pushed) and re-push, or run
  `ruff format` first.
- `scripts/` is gitignored (force-add specific ones); `experiments/` + `tests/` are
  tracked. The mixed-field probe (`scripts/archetype_mixedfield_probe.py`) may need
  recreating in a fresh checkout.

## How to run

- Tests: `python3 scripts/test.py` (full), `--quick`, `--ts`. Always run the FULL
  suite for hot-path changes (a prior session shipped a latent failure by running
  only a subset).
- Completeness monitor (prod, read-only): `ssh root@178.156.202.136 "docker exec -i
  poker-backend-1 python" < experiments/pda_completeness_monitor.py`.
- Archetype probe: `docker compose exec -T backend python <
  scripts/archetype_mixedfield_probe.py 2>&1 | grep -vE "EMOTIONAL|zone_effects"`.
