---
purpose: How to run the preflop chart-opportunity census (spot/money/archetype/fall-through) and the current findings that prioritize the next chart.
type: reference
created: 2026-06-12
last_updated: 2026-06-12
---

# Chart Opportunity Census

Answers "where should the next preflop chart go?" with data instead of intuition,
using the priority model:

```
priority = decision frequency × EV impact per decision × confidence gap × archetype relevance
```

This covers **steps 1–4** (spot census, money census, archetype matrix,
fall-through audit). Step 5 (EV A/Bs, chart-off vs chart-on per archetype) is
deferred until candidates are picked.

## Components

| Piece | Path | Role |
|---|---|---|
| Instrumentation | `poker/tiered_bot_controller.py` (preflop path) + `poker/strategy/strategy_table.py` (`lookup_with_fallback_traced`) | Stamps each preflop decision's snapshot with `chart_source`, `node_key`, `chart_lookup_source`, `push_fold_enabled`, and money context. |
| Census sim | `scripts/chart_census_sim.py` | Runs the production sharp bot (full chart stack) as hero vs a homogeneous archetype field across a depth sweep; persists hero decisions to a throwaway sqlite. Reuses `experiments.simulate_bb100.run_6max_matchup` so decisions go through the **exact** production path. |
| Analysis | `scripts/chart_census.py` | Pure-stdlib reader; emits the four reports as text and/or a JSON payload (`--json`). Runs on a sim DB or a copy of prod. |
| Dashboard API | `flask_app/routes/chart_census_routes.py` | `GET /api/admin/chart-census` serves the JSON artifact (`data/chart_census.json`); `POST /api/admin/chart-census/ask` is the analyst chat (Assistant-tier LLM grounded on the payload, conversation passed in the request body). Both gated behind `can_access_admin_tools`. |
| Dashboard UI | `react/react/src/components/admin/ChartCensusPanel/` | Admin tab "Chart Census" — renders the four reports (KPIs, fall-through table, spot bars, money tables, archetype heat-matrix) plus an "Ask the analyst" chat (`CensusChat.tsx`). Static: reads the pre-generated artifact, no live sim. |

### Analyst chat

The "Ask the analyst" box posts the conversation to `/api/admin/chart-census/ask`,
which embeds the full census JSON in an Assistant-tier system prompt and answers
grounded **only** on those numbers (it's told not to invent figures). The server
is stateless — the client sends the whole message history each turn. Uses the
Assistant tier (`get_assistant_model`/`get_assistant_provider`) because the cheap
in-game tier hallucinates facts; tracked under `CallType.EXPERIMENT_ANALYSIS`.

Assistant replies render through the shared `components/shared/ChatMarkdown.tsx`
(react-markdown + remark-gfm; the repo's first markdown renderer — other admin
chats like `ExperimentChat`/`InterrogationChat` can adopt it). Raw HTML is not
enabled, so model output can't inject markup.

`scripts/` is gitignored — force-add these two scripts (`git add -f`).

### Snapshot fields (in `player_decision_analysis.strategy_pipeline_snapshot_json`)

- `node_key` — `scenario|position|opener|hand` (`PreflopNode.key`).
- `chart_label` — base chart **selected** (`6max@100bb`, `6max@50bb`,
  `6max:loose_mid`, `HU`). Note: short-stack push/fold spots still snap to the
  nearest depth bucket (e.g. `6max@25bb`) even though push/fold produced the action.
- `chart_source` — the layer that **produced** the action:
  `push_fold | facing_all_in_veto | chart_hit | chart_fallback`.
- `chart_lookup_source` — deep-table detail: `hit | squeeze_degrade | masked_out | miss`.
- `push_fold_routed`, `push_fold_enabled`, `effective_stack_bb`, `big_blind`,
  `cost_to_call`, `pot_total`, `player_stack`, `resolved_action`, `resolved_raise_to`.

## Running it

```bash
# 1. Generate a census DB (parallel across field×depth matchups)
docker compose exec backend python3 scripts/chart_census_sim.py \
    --db /tmp/census.db --hands 1000 --jobs 6 \
    --fields station,maniac,tag,nit,folder,balanced \
    --depths 100,40,25,15,10

# 2. Analyze
docker compose exec backend python3 scripts/chart_census.py /tmp/census.db
```

Fields map to `ARCHETYPES`: station→Calling Station, maniac→Maniac, tag→TAG,
nit→Nit, folder→FoldyBot, balanced→Defender. Depths set the per-hand effective
stack (deep 100/40/25 exercise the depth charts; 15/10 exercise Nash push/fold +
reshove).

### Refreshing the admin dashboard

The "Chart Census" admin tab reads `data/chart_census.json`. Regenerate it with:

```bash
docker compose exec backend python3 scripts/chart_census_sim.py --db /tmp/census.db --hands 800 --jobs 6
docker compose exec backend python3 scripts/chart_census.py /tmp/census.db --json data/chart_census.json --quiet
```

Then reload the tab (it re-fetches the artifact). No live sim runs from the UI.

**Prod sanity-check:** point `chart_census.py` at a copy of prod. Spot/money
distributions validate the sim mix. Caveats: prod game_ids aren't census-tagged
so the archetype field reads `(unknown)`, and most prod personas have push/fold
disabled, so the push/fold rows are sparse.

## Findings (pilot run — 2026-06-12)

A representative pilot (24 matchups, ~2.7k decisions, `--hands 90`) — directional,
not production-scale. **Scale `--hands` to ~1000+ before committing chart work.**

**Where decisions land:** rfi 70%, vs_open 26%, vs_squeeze 2.6%, vs_3bet 1.0%,
vs_4bet 0.2%. By source: chart_hit 54%, push_fold 31% (the depth sweep is half
short-stack), facing_all_in_veto 10%, chart_fallback 5%.

**Money (bb at risk = chips the action commits; folds = 0):** rfi 61%, vs_open
33%. The high-leverage tail: `vs_open → facing_all_in_veto` = 23% of all bb at
risk (stack-off calls), and vs_4bet averages 33 bb/decision (max 100) — the
rare-but-huge class.

**Archetype matrix highlights:** vs a maniac field, **vs_squeeze jumps to ~12%**
of decisions (squeezy field); vs a folder/limpy field, the hero is first-in
~100% and **fall-through hits ~33%**. nit/station fields fall through ~0%.

### Prioritized opportunities

1. **Short-stack first-in over a limper** (`pushfold_fallthrough:rfi`) — 5.7% of
   decisions, **382 bb at risk**, concentrated vs limpy fields. A short-stack
   iso-over-limper has no push/fold node (a limper means "not truly first-in" so
   `_try_push_fold_6max` bails), so it drops to the deep chart, miscalibrated at
   10–15 bb. The direct analog of the reshove win. **Highest frequency × money.**
2. **vs_squeeze chart gap** (`conservative_default:vs_squeeze:miss`) — 2.0% of
   decisions auto-fold to the conservative default (no `vs_squeeze` data and the
   `vs_3bet` degrade node also absent). Low bb-at-risk but a believability leak
   **concentrated vs aggressive/maniac fields** (high confidence-gap × archetype
   relevance).
3. **vs_4bet** — tiny frequency (0.2%) but huge per-decision. Largely already
   handled by the facing-all-in pot-odds veto + the vs_4bet gradient (PRs
   #271–276). Confirm the veto covers it rather than adding a chart.

## Prod reality-check (2026-06-12)

Read-only query of the live prod DB (`player_decision_analysis`, 3,422 preflop
sharp-bot snapshots — a low-traffic site, so directional) — what the sim census
gets right vs over-states:

- **Instrumentation isn't in prod yet** — `chart_source` present in 0 rows, so the
  full spot/fall-through census can't run on historical prod data. A true prod
  census needs this PR deployed and new decisions accumulated, then point the
  dashboard at a prod DB copy.
- **The short-stack regime is rare in prod.** `effective_stack_bb` distribution:
  ≤10bb 3.3%, 10–15bb 2.0% (so **≤15bb ≈ 5.3%**), 15–25bb 6.5%, 25–40bb 15.0%,
  **40–70bb 47.6%**, >70bb 25.4%. The census *forced* the depth sweep (10/15bb were
  2 of 5 buckets ≈ 40%), so the short-stack-iso-over-limper gap is real but a ~5%
  tail in the live game, not the headline its forced-depth bb-at-risk implied.
- **Push/fold is dormant in prod** — `push_fold_routed = 0` across all rows
  (including the ≤15bb ones). The per-persona `push_fold_nash` opt-in isn't set on
  the circulating cast, so short-stack decisions already fall to the deep/depth
  charts. So before an iso-over-limper push/fold chart matters, the real fork is
  whether to enable push/fold on prod personas at all.
- **Volume lives at 40–70bb** (~48%), served by the width-tier archetype charts
  (`6max:lag` dominant, then `6max:maniac`/`calling_station`/`weak_fish`/`rock`) +
  the 50bb depth chart. The census under-sampled this band (sweep jumped 40→25→15);
  a leak here would dwarf the short-stack tail by volume.

Net: the tooling is sound, but treat sim-derived *frequencies* as directional until
a deployed prod census confirms them.

## Known limitations

- Money metric = chips committed by the chosen action, so easy folds (incl.
  folds facing all-ins) read as 0 bb — it weights value/jam decisions and
  under-weights high-EV fold spots. Good for prioritization; not an EV measure.
- Archetype fields are homogeneous and the hero is a single archetype (TAG);
  routing is personality-independent so this is fine for *where decisions land*,
  but not for EV (step 5).
