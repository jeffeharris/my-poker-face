---
purpose: Effort/risk scoping for the 55 pre-main TRIAGE items (T1-28..T1-39, T2-36..T2-64, T3-61..T3-74). Each item tagged with scope (S/M/L), approach, and owner.
type: reference
created: 2026-05-15
last_updated: 2026-05-15
---

# Pre-main TRIAGE scoping

This is the action plan for the 55 findings added 2026-05-15 in the pre-main batch review. Each item is scoped by:

- **Scope:** S (≤30 min, single-file mechanical change), M (≤2 hours, multi-site or design decision), L (half-day+ design + migration)
- **Approach:** one-line fix sketch
- **Owner:** `ready` (can be implemented directly from the TRIAGE entry) or `agent:<name>` (deep-dive plan being written separately)

## T1 — Must fix before main

| ID | Title | Scope | Approach | Owner |
|---|---|---|---|---|
| T1-28 | Coach routes missing game-ownership check | S | Add `_authorize_game_access` (or shared helper) call at top of each of 7 coach handlers after `get_game()`. Plan in `COACH_ROUTE_OWNERSHIP_GAP.md`. | ready |
| T1-29 | Psychology state never persisted post-refactor | L | Schema migration + save/restore rewrite. New `psychology_json` column. | **agent:psychology-persistence** → `PSYCHOLOGY_STATE_NOT_PERSISTED_PLAN.md` |
| T1-30 | C-bet detector drops all-in flop c-bets | S | Add `'all_in'` to step-3 condition in `cbet_detector.py:145`. Plan in `CBET_DETECTOR_ALLIN_GAP.md`. | ready |
| T1-31 | OpponentModel `_last_hand_dealt`/`_last_hand_counted` not serialized | S | Add both fields to `OpponentModel.to_dict()` and `from_dict()`. | ready |
| T1-32 | `_get_connection_with_retry` structurally broken | L | Refactor — caller audit + design decision (decorator vs CM vs manual). | **agent:db-retry-rewrite** → `BASE_REPO_RETRY_REWRITE_PLAN.md` |
| T1-33 | `recover_stuck_runout` races without game lock | M | Acquire `get_game_lock(game_id)` around load+recover+set_game; design locking strategy. | **agent:recover-race** → `RECOVER_STUCK_RUNOUT_RACE_PLAN.md` |
| T1-34 | HU positional equity offsets not applied | M | Apply `HEADS_UP_POSITION_OFFSETS` to equity in `generate_bounded_options`. Verify no double-application. | **agent:hu-equity** → `HU_EQUITY_OFFSET_PLAN.md` |
| T1-35 | "+EV guarantee" broken for moderate-equity hands | S | Change `ev_estimate="+EV" if block_fold else best.ev_estimate` to always promote to +EV when reaching that branch. | ready |
| T1-36 | `detect_fold_events` references deleted `elastic_personality` | S | Either replace with `controller.psychology.effective_aggression` (via controllers dict) or delete the fallback branch. Whole `detect_fold_events` is currently uncalled in production paths (per T3-66), so deleting may be best. | ready |
| T1-37 | Frontend bare `JSON.parse` in render crashes panel | S | Wrap each of 4 sites (`DecisionAnalyzer.tsx:1070, 1252, 1535, 1721`) with `useMemo` + try/catch, or use existing `parseJsonArray` helper. | ready |
| T1-38 | `PipelineTracePanel` crashes on undefined fields | S | `trace.effect_size?.toFixed(3) ?? '0.000'`, `Object.keys(trace.inputs ?? {})`, etc. Update types too. | ready |
| T1-39 | Experiment chat tests fail "Authentication required" | S | Add `@patch('poker.authorization.authorization_service')` to `tests/test_chat_persistence.py` and `tests/test_experiment_routes.py` to grant `can_access_admin_tools`. | ready |

## T2 — Should fix before main

| ID | Title | Scope | Approach | Owner |
|---|---|---|---|---|
| T2-36 | Chat-suggestion endpoints missing ownership check | S | Add `_authorize_game_access` to 3 handlers in `stats_routes.py`. | ready |
| T2-37 | `generate-character-images` unauthenticated | S | Add `auth_manager.get_current_user()` check or admin gate. | ready |
| T2-38 | Strategy mapper min-raise uses BB instead of last raise size | M | Find all callsites, replace `highest_bet + big_blind` with `highest_bet + min_raise_amount`. | **agent:strategy-min-raise** → `STRATEGY_MAPPER_MIN_RAISE_PLAN.md` |
| T2-39 | `_EXPLOITATION_RULE_ORDER` missing `bluff_reduction` | S | Add `('bluff_reduction', 'default')` to the list in `tiered_bot_controller.py:117-125`. | ready |
| T2-40 | Defense floor `'air'` check is dead code | S | Change `hand_class == 'air'` to `hand_class in {'air_no_draw', 'air_strong_draw', 'air'}` in `defense_floor.py:117` (and `_matrix_row_label`). | ready |
| T2-41 | `_apply_math_floor` uses `big_blind=0` fallback | S | Replace `getattr(game_state, 'current_ante', 0) or 0` with `big_blind_of(game_state)`. | ready |
| T2-42 | Zone gravity documented but never executes | M | Decide: implement or delete. | **agent:zone-gravity** → `ZONE_GRAVITY_DECISION.md` |
| T2-43 | Zone effects use global `random` | S | Pass `rng = random.Random()` from `__post_init__` through helper chain in `player_psychology.py:1031, 1038, 1092`. | ready |
| T2-44 | `record_hand_dealt(hand_number=None)` bypasses idempotency | S | Add separate flag/guard for None path, or document caller responsibility. | ready |
| T2-45 | `calculate_equity_vs_random` uses global `random.shuffle` | S | Use local `rng = random.Random()` matching `calculate_equity_vs_ranges` pattern. | ready |
| T2-46 | `get_play_style_label` uses wrong denominator | S | Gate on `hands_dealt` (matching VPIP denominator) instead of `hands_observed`. | ready |
| T2-47 | `pot_contributions` mixes raise-TO and call-cost | M | Normalize to actual chips committed per action type; use `hand.get_player_contributions()` if available or pre-compute. | ready (small design call) |
| T2-48 | `board_analyzer._extract_rank` corrupts `10`-rank | S | Change to `return card[:-1]` (rank is everything before the suit char). | ready |
| T2-49 | Historical block renders for `total_hands=0` | S | Gate `opp_data['historical']` population in `coach_engine._get_opponent_stats` on `total_hands >= min_hands`. | ready |
| T2-50 | `addressing` parsed when `should_speak=False` | S | `addressing = [...] if should_speak else []` in `commentary_generator.py:515-520`. | ready |
| T2-51 | `composure_state` big-loss hardcoded $1000 | S | Replace `amount < -1000` with `amount < -(10 * big_blind)` in `psychology_model.py:201`. | ready |
| T2-52 | Severely-tilted narrative suppression | S | Investigate intent → document or remove the guard. (1-line decision after verifying.) | ready (light verification) |
| T2-53 | Coach `effective_stack`/SPR regress websocket test | S | Update test mock to return proper `(player, player_idx)` tuple + stack int, or skip coach progression for the test. | ready |
| T2-54 | Personality determinism regression (`call` vs `fold`) | M | Root cause across psychology / bounded options / strategy. | **agent:personality-regression** → `PERSONALITY_DETERMINISM_INVESTIGATION.md` |
| T2-55 | Prompt preset `owner_id IS NULL` cross-tenant leak | S | Change `WHERE owner_id = ? OR owner_id IS NULL OR is_system = TRUE` → `WHERE owner_id = ? OR is_system = TRUE`. | ready |
| T2-56 | Raw `sqlite3.connect()` sites bypass `busy_timeout` | S | Add `conn.execute("PRAGMA busy_timeout=5000")` after each of 6 raw connects in `ai_debug_service.py` and `experiment_routes.py`. | ready |
| T2-57 | `run_until_player_action()` unbounded after recovery | S | Add step counter (max 100) around the post-loop settle. Covered in `RECOVER_STUCK_RUNOUT_RACE_PLAN.md`. | ready (via recover-race agent) |
| T2-58 | `delete_game` leaves orphans | S | Add 4 DELETEs (tournament_tracker, pressure_events, emotional_state, controller_state) inside `delete_game`. | ready |
| T2-59 | SchemaManager migrations lack `busy_timeout` | S | Either have SchemaManager extend BaseRepository, or add `conn.execute("PRAGMA busy_timeout=5000")` to `_init_db()`. | ready |
| T2-60 | Frontend: non-keyboard-accessible click handlers | S | Add `role="button"`, `tabIndex={0}`, `onKeyDown` to capture-item div and TraceRow `<tr>`. | ready |
| T2-61 | Frontend: duplicate type defs | S | Make `PromptDebugger/types.ts` re-export from `DecisionAnalyzer/types.ts` (or move both to `shared/types.ts`). | ready |
| T2-62 | `composed_nudges` flag has no effect on standard bot | S | Either wire `apply_composed_nudges` into `HybridAIController._get_ai_decision` or document the intentional omission. Sketch in T2-62 entry. | ready (decision needed) |
| T2-63 | Absolute import violates relative-import rule | S | Move `RAISE_LEVEL_ACTIONS` + `_classify_raise_action` to `poker/raise_utils.py`. Update both imports. | ready |
| T2-64 | `_reapply_math_blocking` inconsistent fold-block threshold | S | Use `_should_block_fold(context, profile)` instead of hardcoded `>= 1.7`. | ready |

## T3 — Post-release tech debt

| ID | Title | Scope | Approach | Owner |
|---|---|---|---|---|
| T3-61 | `on_join` socket handler blocks admins | S | Add `or _is_admin(user_id)` to the owner check at `game_routes.py:1514`. | ready |
| T3-62 | `_EXPLOITATION_RULE_ORDER` duplicates `rule_order` | S | Export `RULE_ORDER` from `exploitation.py`, import in controller. | ready |
| T3-63 | Misleading comment in `apply_emotional_window_shift` | S | Update comment to match (correct) code: only `extreme` does the remove. | ready |
| T3-64 | `_option_spectrum_position` overflow | S | Cap raise pos at 99_000; bump all_in sentinel to 1_000_000. | ready |
| T3-65 | Duplicate `rd` assignment | S | Remove line 496 in `hybrid_ai_controller.py`. | ready |
| T3-66 | `detect_fold_events` / `detect_chat_events` dead code | S | Either delete (if confirmed unused — overlap with T1-36) or wire them up. | ready (after T1-36 decision) |
| T3-67 | `GRAVITY_STRENGTH` config dead key | S | Delete from `zone_config.py` if zone-gravity decision is "delete". | ready (after T2-42 decision) |
| T3-68 | `generate_quick_reaction` uses global random | S | `random.Random().choice(reactions)`. | ready |
| T3-69 | `needs_llm_normalization` false-positive on lone `*` | S | Add length guard: `len(text) >= 3`. | ready |
| T3-70 | DecisionAnalyzer god-component drift | L | Split mobile/desktop into single component (T3-48 extension). | post-release |
| T3-71 | `outer_decision_context` lacks primary_aggressor_spot | S | Pass primary_aggressor_spot explicitly when known, or document the no-op behavior. | post-release |
| T3-72 | N+1 in `get_experiment_game_snapshots` | M | Batch the controller_state and emotional_state loads with `WHERE game_id IN (...)`. | post-release |
| T3-73 | `RAISE_LEVEL_ACTIONS` missing 2-bet entry | S | Add `2: '4bet'` to the dict (4bet+ remains for ≥3). | ready |
| T3-74 | `test_call_type_count` pre-existing flake | S | Track separately; not from this branch. | track separately |

## Scope summary

| Bucket | Count | Total est. time |
|---|---|---|
| **T1 ready (S/M)** | 9 | ~5 hours |
| **T1 needs agent (L/M)** | 4 (T1-29, T1-32, T1-33, T1-34) | depends on agent output |
| **T2 ready (S/M)** | 27 | ~15 hours |
| **T2 needs agent (M)** | 3 (T2-38, T2-42, T2-54) | depends on agent output |
| **T3 ready** | 12 | ~6 hours |
| **T3 post-release** | 4 | deferred |

**Total ready-to-implement work: ~26 hours of direct edits** (most are S-scope mechanical changes).

## Agent assignments (7 running in parallel)

| Agent | Item | Output doc |
|---|---|---|
| psychology-persistence | T1-29 | `docs/triage/PSYCHOLOGY_STATE_NOT_PERSISTED_PLAN.md` |
| db-retry-rewrite | T1-32 | `docs/triage/BASE_REPO_RETRY_REWRITE_PLAN.md` |
| recover-race | T1-33 (+ T2-57) | `docs/triage/RECOVER_STUCK_RUNOUT_RACE_PLAN.md` |
| hu-equity | T1-34 | `docs/triage/HU_EQUITY_OFFSET_PLAN.md` |
| strategy-min-raise | T2-38 | `docs/triage/STRATEGY_MAPPER_MIN_RAISE_PLAN.md` |
| zone-gravity | T2-42 (+ T3-67) | `docs/triage/ZONE_GRAVITY_DECISION.md` |
| personality-regression | T2-54 | `docs/triage/PERSONALITY_DETERMINISM_INVESTIGATION.md` |

## Recommended execution order

1. **Quick T1 batch** (~3 hours): T1-28, T1-30, T1-31, T1-35, T1-36, T1-37, T1-38, T1-39 — all S-scope, no dependencies.
2. **Agent-blocked T1 batch** (after agent reports): T1-29, T1-32, T1-33, T1-34 — implement per plan docs.
3. **Quick T2 batch** (~12 hours): everything not behind an agent. Prioritize security (T2-36, T2-37, T2-55) and orphans (T2-58).
4. **Agent-blocked T2** (after reports): T2-38, T2-42, T2-54.
5. **T3 batch** (~6 hours): mechanical cleanup, can run alongside T2 or after.

Items T3-70 through T3-74 are post-release.
