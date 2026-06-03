---
purpose: Cold-start handoff for the Renown-v2 work — what's built/validated/deferred, the run recipes, the gotchas, and the gated next steps.
type: guide
created: 2026-06-01
last_updated: 2026-06-02
---

# Renown-v2 — Handoff

Branch: **`renown`**. Design spec: `docs/plans/CASH_MODE_PLAYER_PRESTIGE.md`
(read the "Renown v2" section + the validation subsection). Scalp prereq:
`CASH_MODE_SCALP_TRACKER.md`. Honest narrative of how we got here (wrong turns
kept): `docs/captains-log/renown/`.

## 2026-06-02 UPDATE — human-only v2 is COMMITTED (`b90451e4`), flag still OFF

The **human-only "ship it" path is implemented + tested + committed** as
`b90451e4` (79 green: oracle parity, repo, field-loader semantics, + live-wiring
integration). The tree is clean — this covers the doc's stages **B-human → C → D**
for the human — and **`RENOWN_V2_ENABLED` stays default-OFF** (kill switch). What
changed from the plan below:

- **The denominator decision.** The handoff called the math "locked." The
  mandated pre-flight surfaced that the design `wallclock` denominator is
  **degenerate on the real field**: the only wall-clock proxy (distinct
  `holdings_snapshots` ticks) is near-uniform (CV 0.16, median==max), so it
  flattens the volume drivers, `3×median` exceeds the field max, and **0
  entities classify as figures** (even the rank-#1 human reads "Disliked
  Nobody"). The sim can't arbitrate (constant hand-count → degenerate). Under
  **`hands`** the field behaves (human = Infamous Villain like v1; 2 figures)
  and the hands-treadmill is **inert** (hands ⊥ performance ρ≈0.05; AI
  hand-volumes negligible). Decision: production scores under
  `PROD_VOLUME_DENOMINATOR = "hands"`; the scorer/parity **default stays
  `wallclock`** so the Rung-1 lever tests stay green (per-call `WeightsV2`).
- **Architecture that made it cheap.** All 4 hooks + the lobby read the
  persisted `quadrant` STRING, so the flag flip lives in ONE site (the ticker
  recompute writes the field-relative quadrant). The field score is ~480ms
  once per the existing 300s throttle — **not** an O(N²)-per-tick cost.
- **What got built:** schema v133 (additive `prestige_snapshots` cols), prestige
  repo v2 (record kwargs + `load_renown_v2_peak` own-scale ratchet),
  `RenownFieldRepository` (batched oracle port, degrade-to-zero),
  `ticker_service._maybe_v2_overlay`, lobby payload `formula_version` + v2 block,
  ReputationPanel v2 branch (uncapped gauge). Parity gate:
  `scripts/renown_field_parity.py` (prod loader == oracle, 80/80 on real DB).
- **Turning it on:** `RENOWN_V2_ENABLED` is now **env-flippable** via `_env_flag`
  (committed default still False). Set `RENOWN_V2_ENABLED=1` in a dev `.env`.
- **All committed (`b90451e4`).** The tree is clean — nothing in this section is
  uncommitted any longer. The schema-lineage reconciliation + the deferred
  AI-field stage (A/B below) are the only renown work left, each its own project.
- **Visually verified** against the REAL field via a standalone gauge preview
  (`react/react/preview-renown.html` + `src/preview-renown.tsx`, throwaway):
  guest_jeff renders "Infamous Villain", renown **55** vs a 36.7 figure-cut,
  "ahead of 100% of the field", ledger led by Breadth 30 / Stakes 16.
- **Blocker for running the full app on the REAL DB — schema-lineage divergence
  (NOT just "empty worktree DB").** The real DB is on the `development` lineage
  at **v136**, whose v132/v133 are *different* migrations (limp_count /
  sizing-aware columns) than the `renown` branch's (cash_scalps / prestige-v2).
  So `ensure_schema` on the real DB sees v136 ≥ 133 and applies **nothing** —
  the v2 columns + cash_scalps never get created. Running on real data first
  needs the standard **renumber-renown's-migrations-above-v136 + merge
  development** reconciliation (the schema-collision pattern this project hits
  on every cross-branch merge). Until then the gauge is verified via the
  preview, not the live lobby.
- **Still left:** the lineage reconciliation above (to run live on real data);
  the **deferred AI-persist + field-wide-ticker stage (A/B below) is
  unchanged** — still its own project.

---

## Original handoff (2026-06-01) — context below this line

## TL;DR state

Renown is cash mode's **fame** scoreboard (separate from bankroll; two axes —
renown = magnitude, regard = beloved↔reviled). v1 shipped a **capped [0,1]**
score; v2 makes it **uncapped + field-relative + performance-weighted**.

- The **v2 math is validated** (offline, on fixtures + the real field) and now
  **ported into production behind a default-OFF flag** — computed-but-unconsumed,
  zero live behavior change.
- The **scalp tracker** (the "who busted whom" counter that powers the villain
  route) is **complete and live** — recording from both the world sim and human
  tables.
- What's left is the **risky integration** (persist AI renown / field-wide
  ticker compute / flip the flag / frontend gauge) — each deferred behind its
  own validation gate. **This is the next project.**

## What's DONE (committed on `renown`)

| Piece | Where | Commit |
|---|---|---|
| Offline scorer (Rung-1 instrument, **the validated spec**) | `scripts/renown_v2_scorer.py` | `9403db3f` |
| Field-relative backing/breadth fix + backing-economy finding | scorer + docs | `d5e13250` |
| Scalp attribution helper (pure) | `cash_mode/scalps.py` | `795b8840` |
| Rung-3 sweep harness (capture + sweep) | `scripts/renown_v3_{capture,sweep,rebalance}.py` | `20a2fde3`, `e2b895c9` |
| Wall-clock fix validated (presence proxy) | scorer/rung2 | `36fbd732` |
| Scalp counter: schema v132 `cash_scalps` + repo | `poker/repositories/cash_scalps_repository.py`, `schema_manager.py` | `7aadc92e` |
| Scalp wiring — world sim (3a) | `cash_mode/lobby.py::refresh_unseated_tables` | `1f062ec2` |
| Scalp wiring — human table (3b) | `flask_app/handlers/game_handler.py::_record_cash_scalps` | `b6a3e574` |
| **v2 compute layer (step 4)** | `cash_mode/prestige.py` + `economy_flags.py` flag | `d9dce6a1` |

### The validated v2 formula (locked by tests)
- **Uncapped** lifetime points; every driver **concave** (sqrt/log1p) — unbounded but can't explode.
- **Scalp quality** = `base + scale·victim_field_percentile` with `log1p(count)` per victim (busting a *relatively* big name ≫ a nobody; bounded — the un-normalized version exploded, see gotchas).
- **Backing & breadth are field-relative**: `w·log1p(raw / field_median)` (a near-universal driver must be relative or it flattens/runs away).
- **Volume denominated by wall-clock**, not hand-count (the anti-treadmill lever).
- **"High renown" = `max(top-X% boundary, k·field_median)`** — relative count cap + self-scaling quality floor; **no absolute constant** (v1's `0.40` was the bug).
- 4 ★ routes: renown-weighted scalps / time-at-#1 net worth / backing / legendary hands.

`cash_mode/prestige.py` v2 functions: `score_renown_field` (two-pass),
`quadrant_label_relative`, `high_renown_cut`, `build_renown_inputs_from_repos`,
`RenownInputsV2`/`FieldContextV2`. **`tests/test_cash_mode/test_prestige_v2.py`
asserts byte-parity with the offline scorer** — if you change the math, change
the scorer and the prod port together or parity tests fail.

## What's DEFERRED (the next project — each gated)

Flag: `cash_mode/economy_flags.py::RENOWN_V2_ENABLED` (default **False**). v2 is
computed-but-unconsumed until this flips. Stages, in order:

- **(A) Persist AI renown.** `prestige_snapshots` is keyed `(sandbox_id, owner_id)`
  with a `load_renown_peak` MAX ratchet — no entity_id. Needs either an ALTER
  (add `entity_id`/`entity_type`, preserve the ratchet for both classes) or a
  parallel `ai_prestige_snapshots` table. **Risky migration on a v122 table —
  sim-validate.** (For the human v1→v2 transition you can skip this: add the v2
  columns to the human row additively and compute-on-read.)
- **(B) Ticker field-wide compute.** v2's relative quadrant + scalp weighting
  need renown for the *whole field*. The ticker (`flask_app/services/ticker_service.py::_maybe_recompute_prestige`)
  computes only the human today. Field-wide = call `score_renown_field` once per
  cycle. **Gate: `CYCLE_BUDGET_MS=250ms`, O(N²) inbound-relationship risk, DB
  locks under burst — docker-exec stress-test with 50+ AIs** before flipping.
- **(C) Flip `RENOWN_V2_ENABLED`** so the 4 hooks read `quadrant_label_relative`
  instead of the absolute `quadrant_label`. Zero-residual kill switch.
- **(D) Frontend** `ReputationPanel` branch on `formula_version` for the uncapped
  gauge (no 0–100 bar).

### Separate real issue (NOT renown — flagged, not fixed)
The **backing economy runs hot**: top backers stake ~the whole field, even at
default-neutral affinity, because `cash_mode/sponsor_offers.py` `TIER_FLOORS`
let a stranger (0.5/0.5) clear premium (0.0) and standard (0.4/0.5). Raising
those floors slows the economy AND makes backing a *selective* signal. It's a
real chip-flow change → **sim-validate**; don't fold it into renown.

## Run recipes

Tests run in Docker (`tests/CLAUDE.md`). The backend mounts `poker/ cash_mode/
flask_app/ core/` live but **not `scripts/` or `tests/`** — mount them per-run:

```bash
# v2 + regression tests (image already built for this worktree)
docker compose run --rm --no-deps -v "$PWD/tests:/app/tests" -v "$PWD/cash_mode:/app/cash_mode" backend \
  python3 -m pytest tests/test_cash_mode/test_prestige_v2.py tests/test_cash_mode/test_prestige.py \
  tests/test_cash_mode/test_scalps.py tests/test_repositories/test_cash_scalps_repository.py -p no:warnings -q

# Offline balance tools (pure stdlib, host — no Docker, no deps):
python3 scripts/renown_v2_scorer.py                       # Rung 1 (fixtures)
python3 scripts/renown_v3_capture.py --from-db -o /tmp/log.json   # snapshot real field (read-only)
python3 scripts/renown_v3_sweep.py /tmp/log.json          # weight sweep + treadmill verdict
python3 scripts/renown_v3_rebalance.py /tmp/log.json      # volume-down-weight crossover
```

The real DB lives in the **main worktree** (`/home/jeffh/projects/my-poker-face/
data/poker_games.db`, ~5GB, live/WAL). The scripts read it **read-only with
`immutable=1`** (no locks, no corruption). Active sandbox used:
`4db9b9f2-0724-439a-a4f9-1329c3678611` (guest_jeff, 80 entities).
`scripts/` is gitignored — force-add (`git add -f`) to keep changes.

## Gotchas (would-bite-you list)

- **Uncapping breaks anything that multiplies by another entity's renown** — use
  the victim's *field percentile*, not raw renown (scalp weight exploded 212-vs-30 otherwise).
- **`holdings_snapshots` ids are prefixed** (`ai:deadpool`/`player:guest_jeff`);
  `cash_pair_stats`/`stakes` use raw ids — strip the prefix to join (silently nulled two drivers once).
- **The treadmill verdict needs a field with volume variance + real performance** —
  the DB-free synthetic sim is too homogeneous (constant hand-count, no skill
  gradient: only 2 of 80 personas are skill-tiered). The sweep has a CV<0.05
  degeneracy guard that refuses a trivial PASS. The *real DB field* is the right
  instrument; the wall-clock fix is validated via the holdings-tick **presence proxy**.
- **`bankroll_repo=None` in `full_sim` ⇒ all default controllers** (archetype/
  strategy come from `personalities.config_json` via the repo, not the seat dict).
- **Synthetic `bot_NN` sim ids trigger a paid LLM persona-generation** — seed sims
  from real `personalities.json` names.
- **Verify before declaring "needs live data"** — the read-side projection had
  proxies for both the treadmill verdict and wall-clock that I initially missed.
  Live-play needed to settle the balance: **zero**.

## If you're picking this up

Most likely next task = the deferred integration (A→B→C→D above), which is its
own sim-gated project. Before flipping anything: re-run the sweep on a fresh
`--from-db` log to confirm the balance still holds, then stress-test (B) under
the ticker budget. The math is done and locked by parity tests — don't
re-litigate it; the work left is safe *integration* behind the kill switch.
