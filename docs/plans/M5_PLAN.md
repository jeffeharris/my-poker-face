# Milestone 5: RBAC and Polish

## Background

This is the final milestone for the **Coach Progression System**. Milestones 1-4 built the full backend (11 skills, 4 gates, evaluation pipeline, session memory) and frontend (progression strip, skill grid, mode-aware bubbles, onboarding). M5 adds production gating and metrics.

## What Ships

1. **RBAC gating**: `can_access_coach` permission added via DB migration. All 7 coach routes protected with `@require_permission('can_access_coach')` decorator. Guests get 401; authenticated users (in `user` or `admin` group) have access.

2. **Metrics endpoints**: Three admin-only API endpoints for monitoring coach usage:
   - `GET /api/coach/metrics/overview` — Player counts, levels, gate funnel
   - `GET /api/coach/metrics/skills` — Per-skill state distribution and accuracy
   - `GET /api/coach/metrics/advancement` — Advancement timing and stuck-player detection

3. **Analysis scripts**: `scripts/coach_analysis.py` for threshold tuning from production data.

## Files Modified

| File | Change |
|------|--------|
| `poker/repositories/schema_manager.py` | Migration v64: `can_access_coach` permission |
| `flask_app/routes/coach_routes.py` | RBAC decorators on all routes + 3 metrics endpoints |
| `poker/repositories/coach_repository.py` | Metrics query methods |
| `scripts/coach_analysis.py` | **NEW** — threshold analysis script |
| `docs/plans/M5_PLAN.md` | **NEW** — this document |

## Key Decisions

- **Gate all 7 routes** (not just progression/onboarding) — matches requirements doc §1.4
- **Query-only metrics** — no new tables, compute from existing `player_skill_progress`, `player_gate_progress`, `player_coach_profile`
- **No admin UI for threshold editing** — thresholds stay in `skill_definitions.py`, analysis scripts inform manual tuning
- **No frontend changes needed** — existing `coachEnabled = !isGuest` check already hides coach for guests; `if (res.ok)` guards handle 401/403 gracefully
