---
purpose: Handoff for the training-room branch — what shipped (training mode + coach reliability), current state, open threads, and how to pick up
type: guide
created: 2026-05-30
last_updated: 2026-05-30
---

# Training Room — Handoff

Two intertwined tracks were built on branch **`training-room`** this session:
a new **Training/Coaching game mode**, and a **coach-reliability** effort that
surfaced while testing it. Everything below is **committed but NOT pushed and
not PR'd**; working tree is clean.

- Full spec for the mode: `docs/plans/TRAINING_MODE.md`
- Memory topics: `project_training_mode_plan`, `project_coach_reliability`
- App (this worktree): backend `http://localhost:5005`, frontend
  `http://localhost:5179` (shifted ports — `.env` `BACKEND_PORT=5005`,
  `FRONTEND_PORT=5179`, `REDIS_PORT=6384`).
- Tests: `docker compose exec backend python -m pytest tests/test_training_mode.py tests/test_coach_prefetch.py tests/test_coach_assistant.py` — **71 green**. TS + eslint clean.

## Commits (oldest → newest), base `7c4fd765`

```
ce8c2568 Phase 1 — non-counting sparring + difficulty + auto-coach
2e96985c Phase 2a — table presets (heads-up / short / deep / full ring)
c8ea84c3 Phase 2b — inline coach feedback after each action
e6006a9b Phase 3 factory — scripted-spot state injection (+ Phase 3.5 plan)
5223771e Phase 3 — scripted-spot drills (library, route, menu)
2cce3de3 fix(coach): run coaching on the Assistant tier, not the Default 8B model
3176d703 refactor(training): cut hand-authored drills; keep the reconstruction engine
944e74ce feat(coach): prefetch the proactive tip at turn-start
40c2a849 fix(coach): stop feeding/fabricating made-hands on uncontested wins
05984731 feat(coach): opponent archetype reads + Socratic tips + explain-deviation rule
```

---

## Track A — Training/Coaching mode

A non-counting practice mode: a thin `train-` sibling of cash mode
(`training_mode=True` flag on `game_data`). Reuses the engine, game page,
socket flow, and coach system. **Non-counting is structural** — `_build_training_game`
never wires a relationship repo, tournament tracker, bankroll, or sandbox, so
no economy/prestige/relationship/leaderboard writes. Skill-progression IS kept
(it only needs `owner_id`).

**Shipped (Phases 1–3):**
- **Phase 1** — `POST /api/training/start`, `train-` prefix, coach forced
  `proactive`, cold-load `train-` branch (re-derives mode from prefix, skips
  relationship/tracker), `list_games` exclusion, `_purge_training_games`
  (≤1/owner). FE: `TrainingMenu` + `/menu/training` + HomeMenu "Practice" card +
  `isTrainingGameId`.
- **Phase 2** — table presets (`training/scenario.py TABLE_PRESETS`:
  standard/heads_up/short_stack/deep/full_ring) + inline post-action skill
  verdict (`skill_evaluation` in the action response → verdict toast).
- **Phase 3 factory KEPT, authored drills CUT** — `build_scripted_spot_state_machine`
  (the `from_saved_state` injection, with ghost-seat/legality asserts) + its 6
  unit tests survive as the engine for **hand-replay (3.5)**. The JSON drill
  catalog, loader, `scenario_id` route, and menu Drills section were **removed**
  (`3176d703`): curating spots that stay interesting doesn't scale, and dropping
  a player into a stranger's spot isn't teaching *their* game.

**Key files:** `training/{scenario.py,state_builder.py,opponent_roster.py}`,
`flask_app/routes/training_routes.py`, `tests/test_training_mode.py`,
`react/react/src/components/training/TrainingMenu.{tsx,css}`.

**Product reframe (important):** the mode's thesis shifted from "drills" to
**"the coach reviews YOUR real hands, names your leaks, and (opt-in) nudges you
in live games"** — with no-stakes tables to practice. The coach is opt-in per
game in *any* mode, not training-only. The "training room" is the review/practice
surface; leak-aware intervention would live in real games.

**Open (not built), in `TRAINING_MODE.md`:**
- **Phase 3.5 — hand replay from history** (the scalable content engine): a
  flagged/auto-surfaced `hand_history` row → reconstruct via the kept factory →
  non-counting `train-` drill at your seat + decision street. `RecordedHand`
  already stores everything needed (`PlayerHandInfo.starting_stack`,
  `RecordedAction{action,amount,phase}`, `hole_cards`, `community_cards_by_phase`,
  `deck_seed`). Sources: in-game ⚑ flag + auto-tag by `ev_lost`/`pressure_events`.
- **Phase 4** — interactive intercept coach (commit-first probe).
- **Phase 5** — read-the-player (anonymized seats, archetype guess).
- **Drill ending / decide-first loop** — drills currently just keep dealing;
  no "drill complete" beat (deferred when drills were cut).

---

## Track B — Coach reliability (the bigger story)

The in-game coach gave "way off" advice (e.g. "play your set of fives" on a hand
that won pre-flop with no board). Root-caused and substantially improved:

1. **Model tier (`2cce3de3`) — biggest fix.** Coaching was hard-coded to the
   Default LLM tier = an 8B model (`groq llama-3.1-8b-instant`); it can't reason
   about poker. Moved to the **Assistant tier** (`get_assistant_*`, currently
   `deepseek-chat`). A/B on the exact failing prompt: 8B hallucinated a set;
   deepseek gave correct advice. `flask_app/services/coach_assistant.py:~199`.
2. **Fact-binding (`40c2a849`).** Uncontested wins no longer mislabeled
   "— High Card" (`hand_context._build_winner_summary` gates on `was_showdown`);
   system prompt forbids describing hands the board doesn't support.
3. **Archetype reads (`05984731`).** Coach surfaces the tiered bots'
   detection-layer opponent classification (calling station / maniac / sticky
   jammer) via `_build_aggregate_from_single` + `classify_opponent_archetype`
   (≥15-hand gate) — `coach_engine._classify_opp_archetype`. Far more actionable
   than raw VPIP/PFR/AF; system prompt says how to exploit each.
4. **Socratic proactive tips (`05984731`).** Proactive tips nudge thinking and
   **withhold the action** (`action=null`) — no spoon-feeding.
5. **Explain-deviation rule (`05984731`).** System prompt: default to the
   computed recommendation, justify any deviation, never silently contradict the
   math. (A code "override to GTO" was **rejected** — skill-focused LEARN-mode
   coaching legitimately deviates from GTO; an override would break it.)
6. **Prefetch (`944e74ce`).** Proactive tip computed at turn-start
   (`handle_human_turn` → `coach_prefetch.py`), cached by decision signature with
   an Event; `/ask` serves the in-flight result (one LLM call/decision). Gated to
   proactive mode. Modest win now; foundation for instant on-demand hints.

### Coach: what's still open (prioritized)

1. **Capture in-decision tips (prerequisite to measuring anything).** 100% of
   logged coach calls are post-hand *reviews* — the live proactive/ask tips you
   act on are **not captured** (`prompt_captures` where `call_type='coaching'` =
   reviews only; local 44/44, dev 991/991). We're tuning blind until this exists.
2. **Solver-grounding (the correctness-ceiling arc).** The coach's recommendation
   still comes from `decision_analyzer.determine_optimal_action` (heuristic), and
   the per-action `skill_evaluator` verdicts are coarse rule logic (~60-70%
   accurate; misfires on semi-bluffs/position). Grounding BOTH in the tiered
   **solver lookup + detection layer** is the real fix. Feasible: solver action
   via `strategy_table.lookup_with_fallback` using the *distribution* (sidesteps
   the bots' RNG sampling); archetype/intensity already wired (see #3 above).
3. **Skills system: KEEP, don't trust the verdicts.** It's load-bearing for
   personalization + the leaks vision; don't cut. But its inline verdicts are the
   weak link — improve via #2, don't surface them as authoritative.

### Things explicitly rejected/shelved (so they're not re-tried)

- **Click-time coach prefetch via a separate batch sim** — blocked by the bots'
  stateful, system-seeded RNG (`tiered_bot_controller` `random.Random(None)` +
  `sample_action`): a separate batch plays a *different* line; reusing live
  controllers corrupts the real flow. Faithful version needs a
  compute-once-then-animate refactor of `progress_game`. Shelved.
- **The 7s inter-hand wait is NOT the coach** — it's a blocking commentary LLM
  call (`commentary_complete.wait(timeout=10)`, `ENABLE_AI_COMMENTARY` default
  true) + animation pacing sleeps. The cheap fix (skip commentary + compress
  animation **for training mode**) was proposed and **not done** — easy win still
  on the table if practice-mode snappiness matters.
- **Temperature tuning** — not exposed on `Assistant`/`LLMClient`, and considered
  moot on the reasoning endpoints. Dropped.

---

## Gotchas / decisions to know

- **Coach is on the Assistant tier now** (deepseek-chat) — slower + pricier than
  the instant model. Fine for training (coach is the only LLM there); watch cost
  when the coach is enabled in *real* games. Retarget via `ASSISTANT_*` config.
- **Scripted-spot factory invariants** (for 3.5): assert human at
  `current_player_idx`, `awaiting_action=True`, phase↔board-count; deck =
  `52 − hero_holes − villain_holes − community`; `Card` is unhashable (filter
  with list `not in`); parse with `Card.from_short` (rank `'10'` not `'T'`).
- **Cold-load divergence class** (cash mode hit this repeatedly): the `train-`
  branch re-derives mode from the prefix because `game_data` flags aren't
  persisted. Any new mode flag needs the same treatment.
- **Guest hand-limits still count** in training (shared action path) — revisit
  with the Phase 4 action wrapper if guests should be exempt.

## Pick-up order (recommendation)

1. **Capture in-decision coach tips** — small, unblocks measurement.
2. **Solver-ground the recommendation + skill verdicts** — raises the ceiling.
3. **Phase 3.5 hand-replay** — the scalable content engine; reuses the kept factory.
4. (Optional) commentary/animation speedup for training; drill-ending loop.
