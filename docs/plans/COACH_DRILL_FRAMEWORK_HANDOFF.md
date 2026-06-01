---
purpose: Cold-start handoff for building the typed drill framework — orientation, exact current code, env/verify, resolved decisions, and a P1 checklist
type: guide
created: 2026-06-01
last_updated: 2026-06-01
---

# Drill framework — build handoff

Read **`docs/plans/COACH_DRILL_FRAMEWORK.md`** first (the design). This doc is the
operational glue: where things are, how to run/verify, what's decided, and the
P1 first steps. Branch: **`training-room`** (pushed to origin; ~22 commits).

## What already exists (the `chart` drill, to become a type)

- **`flask_app/services/coach_drill.py`** — `sample_drill_spots(scenario, position,
  n=10, *, rng=None)`, `grade_drill_answer(scenario, position, hand, action) ->
  {verdict: good|thin|leak, your_freq, chart_freq, primary_action}`,
  `pick_drill_leak(leak_set)`. Consts `DRILL_DEPTH_BB=100`, `DRILL_PLAYERS=6`,
  `GOOD_MIN=0.30`, `THIN_MIN=0.10`. → these become `build_chart_spots` /
  `grade_chart` under the registry (behavior unchanged).
- **Reference (chart ground truth)** — `poker/strategy/preflop_reference.py`
  `reference_strategy(hand, position, scenario, opener, eff_bb, num_players) ->
  {fold,call,raise} | None`. Handles rfi/vs_open/vs_3bet/vs_4bet, depth buckets, HU.
- **Routes** — `flask_app/routes/coach_routes.py`:
  `GET /api/coach/drill` (`?scenario=&position=` or auto-picks the top confirmed
  leak via `pick_drill_leak(get_owner_chart_leak_set(...))`) → `{enough_data, leak,
  spots}`; `POST /api/coach/drill/answer` `{scenario, position, hand, action}` →
  verdict. Both gated by `_coach_required` (= `require_permission('can_access_coach')`).
- **Frontend** — `react/react/src/components/training/PreflopDrill.tsx` (+ `.css`).
  Already has: `handToCards()` + `<Card>` rendering, the `SpotPicker` (scenario +
  position, BB excluded for rfi), Fold/Call/Raise, graded feedback, the solid/total
  score, and the "Practice a specific spot" toggle. This is what becomes the
  generic `DrillRunner` + `ChartSpot`.
- **Card component** — `react/react/src/components/cards` (`import { Card } from
  '../cards'`). Props `card={{ rank, suit }}` with **title-case** suit
  (`'Spades'|'Hearts'|...`) and **`'10'` not `'T'`**; `size="small"` (55×80).

## Ground-truth helpers for the new drills

- **pot-odds / outs / hand-rank** → eval7 (backend dep, used live). Equity utilities
  live in `poker/` (see `poker/decision_analyzer.py`, which already computes
  `equity` / `required_equity` / `ev_call`) and `poker/hand_ranges.py`
  (`hand_to_canonical`, combo expansion). Grade **server-side**.
- **push/fold** → `poker/strategy/push_fold.py`
  `lookup_push_fold_action(hand, position, effective_stack_bb, num_opponents,
  facing_jam) -> 'jam'|'fold'|'call'|None`; `PUSH_FOLD_THRESHOLD_BB=15`. **HU-only**.
- Shared "did they play the solver line" rule (for reference):
  `coach_chart_leaks.followed_solver_line(kind, action)`.

## Decisions (resolved — don't re-litigate)

1. **Difficulty knob:** ship **fixed v1** (no difficulty selector). Add later.
2. **Pot-odds spot realism:** generate a mix — some clear calls, some clear folds,
   and several **near the pot-odds boundary** (so it teaches, not trivial).
3. **Answer-trust:** the client submits the spot it was shown; **recompute the
   grade from it** server-side. No spot-ids. (It's practice, not ranked.)
4. **Postflop:** **no solver-graded postflop**. Postflop only via the math drills
   (pot-odds/outs on a board). "GTO postflop action" is the separate project.
5. **Scoring:** every grader returns a normalized `outcome` ∈ {good, ok, miss}; the
   runner tallies that uniformly.

## Environment / run / verify

- **Ports (this worktree):** backend `localhost:5005`, frontend `localhost:5179`
  (set in `.env`). Everything runs in Docker.
- **Tests:** `docker compose exec -T backend python -m pytest tests/<file> -q`;
  full fast gate `python3 scripts/test.py --quick`; TS `python3 scripts/test.py
  --ts` (or `docker compose exec -T frontend npx tsc --noEmit`); lint
  `docker compose exec -T frontend npx eslint src/<file>`.
- **Visual verify (Playwright MCP):** the dev guest login (name **"Jeff"**)
  resolves to `owner_id = guest_jeff`, which **owns the seeded data** — so the
  leak/drill panels are populated in a fresh Playwright browser. Drill any spot
  via `/menu/training/drill?scenario=rfi&position=SB`. (Drill routes need the
  coach permission; the guest has it.)
- **Seeded data is throwaway** — `seed_gr_*` (Gordon Ramsay's AI play relabeled to
  Jeff) + `seed_jeff_*`. Backups in `data/poker_games.backup_*`. The live
  effectiveness/nudge data is **empty until a coached game is actually played**.

## Gotchas hit this session (avoid re-discovering)

- **Migration auto-reload trap:** when adding a schema migration, define the
  `_migrate_vNNN` *method* BEFORE the dict entry that references it, or the dev
  backend crashes mid-edit. After all edits, `docker compose up -d backend`.
- **Loader query plan:** never put `pda.player_name = g.owner_name` in a WHERE that
  also filters `g.owner_id` — it forces an O(games×rows) nested loop. Scope by
  `owner_id`, join on `game_id`, filter the human seat in Python (see
  `coach_chart_data.load_owner_chart_decisions`).
- **flex-wrap rows:** if a flex row must keep a right column inline while a child
  wraps below, the text column needs `flex: 1 1 0; min-width: 0` (auto basis claims
  max-content and pushes siblings down) — see `.pfl-leak-detail`.
- **Reused `Sparkline`** (`components/cash/Sparkline.tsx`) has an optional `label`
  prop; its hover tooltip `$`-formats the value (cosmetic for non-money series).

## P1 checklist (framework + chart-as-type + pot-odds)

1. Registry in `coach_drill.py`: `DrillType(build, grade)`; rename the current
   functions to `build_chart_spots` / `grade_chart`; add `outcome` (good/ok/miss)
   to the chart verdict. Update `tests/test_coach_drill.py` imports.
2. Generalize routes: `GET /api/coach/drill?type=<t>` (default `chart`, keep the
   `?scenario&position`/top-leak path); `POST .../answer {type, spot, answer}`.
3. **pot-odds**: `build_potodds_spots` (hand+board+pot+bet, boundary-mixed via
   eval7 equity) + `grade_potodds` (equity ≥ required_equity = bet/(pot+bet)).
4. Frontend: refactor `PreflopDrill` → `DrillRunner` (shared loop/score/picker) +
   `ChartSpot` (today's UI) + `PotOddsSpot` (board + pot/bet + Call/Fold) + a
   drill-type selector. Keep the chart path byte-compatible.
5. Unit tests for the registry + pot-odds generator/grader; `--quick` + `--ts` +
   eslint; Playwright-verify both drill types as `guest_jeff`.

Then **P2** push/fold, **P3** outs + hand-rank (see design doc phasing).

## Related docs
- `docs/plans/COACH_DRILL_FRAMEWORK.md` — the design (read first).
- `docs/plans/COACH_CHART_LEAKS.md` — the leak system the chart drill grades against.
- `docs/plans/COACH_PROGRESS_SLICES.md` — slices/trend/cache (shipped).
- `docs/captains-log/training-room/chart-graded-coach.md` — narrative + wrong turns.
