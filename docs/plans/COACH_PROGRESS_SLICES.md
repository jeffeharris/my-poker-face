---
purpose: Design unifying sliced leak analysis, progress-over-time, and a per-owner cache into one architecture for the chart-graded coach
type: design
created: 2026-06-01
last_updated: 2026-06-01
---

# Coach progress & slices

Builds on `docs/plans/COACH_CHART_LEAKS.md`. Design only — not yet built.

## The problems this solves

1. **The all-time aggregate never forgets.** A leak you fixed in February stays
   `confirmed` forever because it's averaged over all history. The read isn't
   about your *current* form.
2. **No progress signal.** Nothing shows a leak shrinking — the motivating
   "watch it improve" hook is missing.
3. **Recompute-every-open.** Cheap now (~140ms after the query fix) but still
   redundant across opens, and it'll matter once we add per-window trends.

## The unifying insight

**Progress is a sequence of recency slices**, and **a slice is just a filter
applied to the decision set before grading.** Every decision already carries
`created_at`, `hand_number`, and `effective_stack_bb`, so:

- a *slice* = `[d for d in decisions if predicate(d)]` → grade (45ms, pure)
- a *trend* = grade consecutive time/volume windows of the same history
- the *diff* = recent-window report vs all-time report

So slicing, progress, and (the thing we cache) are **one pipeline**, not three
features. No new grading code — it all rides the existing pure
`compute_chart_leaks`.

## Architecture

```
load_owner_chart_decisions  (fast, ~70ms; add created_at to the row)
        │  decisions[] (carry created_at, hand_number, eff_bb, num_players)
        ▼
   slicer(spec)            partition: all-time | window(30d) | last_n(500) | depth-bucket | time-blocks
        │
        ▼
 compute_chart_leaks       grade each slice (45ms each, pure)
        │
        ├── recent report ─┐
        ├── all-time report┤→ diff(recent, all-time) → shrinking/persistent/new/fixed
        └── K time-blocks ──→ per-leak trend series ("watch it shrink")
        ▼
   per-owner cache         keyed by decision-count; invalidates when new hands land
```

## Slice axes (ranked)

1. **Recency** (primary) — `window(days=N)` for the headline "recent" read;
   `block(hands=N)` for trend points. Fixes "never forgets"; *is* the progress
   foundation.
2. **Stack depth** (≤15 / 25 / 50 / 100bb) — leaks are depth-specific and we
   already grade depth-aware. "Disciplined deep, spew short."
3. **Session / N-hand block** — the natural unit for a trend line and for "how
   did I play tonight?".

(scenario × position is the *within*-report grouping, not a slice.)

## The diff (the motivating signal)

Compare the recent slice to all-time per `(scenario, position[, hand])`:

| recent vs all-time | meaning | UI |
|---|---|---|
| recent gap < all-time gap | **shrinking** — fixing it | green ↓ |
| recent ≈ all-time | persistent | neutral |
| recent only | newly emerging | amber ↑ |
| all-time only (gone recent) | fixed — drop it | faded / cleared |

## Honest handling of the window × confidence tension

A narrow window has less volume, so fewer leaks reach `confirmed` (n≥6 harder).
Rules:
- Each slice computes its own `watching`/`confirmed` by its own `n`.
- Only label a leak **shrinking/persistent** when *both* slices clear a minimum
  volume; otherwise say "not enough recent hands to tell." Never imply progress
  we can't measure.
- **Trend points = volume-based blocks** (per ~N hands), not calendar weeks, so
  each point has comparable sample size. The "recent" *headline* can still be
  calendar (last 30 days) to match player intuition — label which is which.

## Cache

- **Key:** `(owner_id, decision_count)`. `decision_count` from a cheap
  `SELECT COUNT(*)` (or a maintained counter). New hand → count changes → miss →
  recompute. No explicit invalidation needed.
- **Value:** the loaded decisions and/or the computed reports (all-time + recent
  + trend). Caching reports makes repeat opens instant.
- **Scope:** process-local LRU (per worker warms independently). A shared
  (Redis) cache is overkill for v1. The in-game recall already caches its leak
  set per game session — same idea, different lifetime.

## When a snapshot table IS warranted (deferred)

On-demand derivation is enough while raw decision rows are retained. A
`coach_leak_snapshots` table (mirroring `prestige_snapshots`, ticker-written)
only earns its place when:
- raw `player_decision_analysis` rows get pruned/rotated (retention) — the trend
  would otherwise lose history, **or**
- we want to freeze "what the coach claimed then" for audit.

Until then, the trend is a *view*, not stored state.

## Surfaces

- `GET /api/coach/preflop-leaks?window=30d|all|last500` → sliced report + the
  recent-vs-all-time diff per leak.
- `GET /api/coach/preflop-leaks/trend` → per-leak series across the last K
  volume-blocks (review-panel sparkline).
- Review panel: shrinking/persistent/new badges + a small per-leak trend
  sparkline; depth toggle (deep / short).

## Phasing

- **P1 — recency slice + diff. ✅ shipped.** Loader returns `created_at`;
  `?window_hands=` param; recent-vs-all-time diff per leak + recall reads recent.
- **P2 — trend. ✅ shipped.** `compute_leak_trend` (K equal volume-blocks) →
  per-leak `trend.series`; reused `Sparkline` on the panel.
- **P3 — cache. ✅ shipped.** `preflop_leak_cache`, keyed by
  `(owner, depth, window)` and gated by the owner's decision count
  (self-invalidating); process-local, mirrors `sandbox_resolver`.
- **P4 — depth slice. ✅ shipped.** `depth_slice` (deep ≥35bb / short) +
  `?depth=` param + a deep/short toggle on the panel.
- **P5 — snapshot table.** Only if retention/audit demands it. (Not built.)

## Open decisions

- Default recent window: 30 days vs last-500-hands. (Lean last-N-hands —
  volume-stable; calendar dilutes for infrequent players.)
- Trend block size (e.g. 150–250 hands) — tune against real volume.
- ~~Does "fixed" drop a leak from the live in-game recall too?~~ **Resolved &
  shipped:** `get_owner_chart_leak_set` now scopes to the recent window
  (default last 500 hands) — a leak you've fixed stops nudging; a leak you've
  started making shows up. All-time remains available via `recent_hands=None`.
