# Strategy Specs

Design specs from the June 2026 chart review. Recommended build order:

| # | Spec | Fixes | Effort | EV impact |
|---|---|---|---|---|
| 1 | [PREFLOP_DEFENSE_REGEN_SPEC](PREFLOP_DEFENSE_REGEN_SPEC.md) | `vs_3bet` position-invariance (fold-to-3bet 65–74% on CO/BTN/SB vs a 65.2% auto-profit line; UTG/HJ fine), BB overfold vs late opens (~7–9 pts under floor), depth-chart over-tightening + stale RFI | Low — generator changes on existing machinery | Highest urgency: 3-bet-any-two profits vs BTN/SB opens |
| 2 | [VALIDATION_SUITE_SPEC](VALIDATION_SUITE_SPEC.md) | Aggregate-stats-only validation; would have caught #1 automatically | Low–medium | Indirect — protects everything else |
| 3 | [POSTFLOP_COVERAGE_SPEC](POSTFLOP_COVERAGE_SPEC.md) | One authored node + degrade ladder | Medium — generator + calibration | Largest absolute EV gain |
| 4 | [SIXMAX_PUSH_FOLD_SPEC](SIXMAX_PUSH_FOLD_SPEC.md) | No 6-max short-stack table | Low — extends existing HU solver | Narrow but clean win |

Build #2 alongside #1 — the regen specs define lints/probes as their
acceptance criteria, so the suite pays for itself immediately.

Dependency note: within #1 the order is strict (`vs_open` → `vs_3bet` →
`vs_4bet` → depth charts → archetype transforms), because each stage reads the
previous stage's output as its villain model.
