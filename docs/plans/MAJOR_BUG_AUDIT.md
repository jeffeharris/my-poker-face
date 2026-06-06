---
purpose: Catalog of major intent-vs-implementation bugs, logic errors, and duplicated-divergent implementations found in a multi-agent audit, ranked for triage
type: reference
created: 2026-06-06
last_updated: 2026-06-06
---

# Major Bug Audit — Ranked Worklist

Output of a multi-agent audit (2026-06-06) hunting for **big** bugs: intent-vs-implementation
mismatches, logic errors with real blast radius, and work duplicated in different places with
divergent implementations. Two passes:

1. **Broad hunt** — 14 subsystem finders → adversarial verify → rank. 27 confirmed of 41 candidates.
2. **`INSERT OR REPLACE` sweep** — every upsert site audited for column-clobbering. 2 confirmed of 18 tables.

Severity: **S1** critical · **S2** major · **S3** moderate. Status: ✅ fixed · ⬜ open.

> Findings are candidate-quality: each was adversarially verified against the real code, but
> confirm the current line numbers before acting — the tree moves.

---

## Fixed in this pass

### ✅ `save_relationship_state` INSERT OR REPLACE wiped player notes + nickname overrides — S2
- **Was:** `INSERT OR REPLACE` (DELETE+INSERT on PK) omitted `notes` (v95) and `nickname_override`
  (v101), NULLing them on every affinity write. PK `(observer_id, opponent_id)`; the terminal step
  of `record_event` fires on ~every showdown/social/cash confrontation → silent user-data loss.
- **Fix:** `ON CONFLICT(observer_id, opponent_id) DO UPDATE` of only the affinity columns.
  `poker/repositories/relationship_repository.py` (~line 102).
- **Regression:** `tests/test_repositories/test_relationship_repository.py::TestAffinityWritePreservesPlayerData`
  (note/nickname survive a stream of affinity writes; axes still update).

### ✅ `save_personality` INSERT OR REPLACE reset `times_used`/`created_at` + reallocated `id` — S3
- **Was:** Same DELETE+INSERT clobber across all four schema branches; column list omitted
  `times_used` (drives lobby `ORDER BY times_used DESC`), `created_at`, and the AUTOINCREMENT `id`.
  Fired on avatar regen, persona/UI edits, re-seeds.
- **Fix:** `ON CONFLICT(name) DO UPDATE` of only this-path's columns (mirrors the safe
  `update_personality_config`). `poker/repositories/personality_repository.py` (~line 157).
- **Side effect (improvement):** an explicit `personality_id=` collision across a *different* name
  now raises `IntegrityError` instead of silently DELETEing the colliding persona. Test updated:
  `test_explicit_id_collision_raises`.
- **Regression:** `test_resave_preserves_times_used`, `test_resave_preserves_id_and_created_at`.

### ✅ 1. Call/all-in actions recorded with `amount=0` → relationship + off-screen economy corrupted — S2 *(cluster of 2)*
- **Was:** `BoundedOption.raise_to == 0` for both `call` and `all_in`. Only the live web path
  normalized `call`; nothing normalized `all_in`, and the sim/experiment paths normalized neither.
  `get_player_contributions` then credited 0 chips and `allocate_chip_flow` dropped any loser with
  `contrib<=0`, killing `ChipFlow` → no BIG_WIN/BIG_LOSS/KNOCKOUT and no `cash_pair_stats` PnL. The
  lobby sim was worst (no normalization AND no blinds recorded → pure call-down losses emitted zero
  flow). Real chip ledger unaffected → invisible; accumulated wrong AI memory indefinitely.
- **Fix:** One shared helper `poker.memory.memory_manager.normalize_action_amount(action, raw, *,
  highest_bet, player_bet, player_stack)` — `call` → cost-to-call clamped to stack, `all_in` →
  remaining stack, else passthrough. Called by all three recording paths:
  - `flask_app/handlers/game_handler.py` (web; replaced the inline call-only normalization, now also
    handles `all_in`),
  - `cash_mode/full_sim.py` (lobby sim; + added the missing `record_blinds` after the first deal),
  - `experiments/run_ai_tournament.py` (experiment runner).
- **Regression:** `tests/test_memory/test_call_amount_recording.py` —
  `TestAllInAmountNormalization`, `test_all_in_records_stack_not_zero`,
  `test_all_in_loser_produces_nonzero_chip_flow` (end-to-end: shove → contribution → ChipFlow). Also
  collapsed that file's divergent `_normalize_call_amount` re-implementation onto the shared helper.

### ✅ 4. Bounded-options postflop bet sizing ignored already-committed chips — S2 *(under-betting)*
- **Was:** `_get_raise_options` emitted raise-TO targets as raw `pot * sizing`, omitting the anchor
  (the chips already in front of the actor). This engine keeps `player.bet`/`pot['total']` cumulative
  across streets and `raise_to` is an absolute bet level (`player_raise` adds `raise_to - player.bet`),
  so every postflop bet/raise by the `standard`/`lean` bots was under-sized by the prior commitment —
  a table-wide exploitable leak diverging from the solver (`sharp`) bots, whose
  `action_mapper.resolve_postflop_sizing` anchors correctly and warns against exactly this.
- **Fix:** Anchor the targets in `_get_raise_options`, mirroring `action_mapper`: betting
  (`cost_to_call == 0`) → `already_bet + sizing * pot`; raising (facing a bet) →
  `highest_bet + sizing * (pot + cost_to_call)`. `poker/bounded_options.py`. Backward-compatible: with
  no prior commitment / no bet faced the anchor is 0, so existing behavior (and tests) are unchanged.
- **Regression:** `tests/test_bounded_options.py::TestRaiseSizingAnchor` (betting adds prior
  commitment; raising anchors on highest_bet + pot-after-call; shift equals the anchor exactly).

### ✅ 3. Tournament stuck-payout reconcile mis-derived `(human_owner_id, real_persona_ids)` — S2 *(pays wrong accounts)*
- **Was:** Crash-recovery `reconcile_stuck_payouts` re-derived the human/persona split with a
  `personality_repo`-free heuristic that disagreed with the live payout. Human Main Event: it passed
  `real_persona_ids=frozenset()`, sweeping the real-persona OPPONENTS' prize shares to the bank
  instead of crediting their `ai:<pid>` bankrolls. Legacy `/register` path: the synthetic `P01`-as-human
  field diverted the human winner's prize to a phantom `ai:P01`. Escrow nets to 0 → conservation
  audits stay green (masked). Triggers only on a mid-distribute crash.
- **Fix (two parts):**
  1. **Removed the legacy `/register` route** entirely (`flask_app/routes/tournament_routes.py`) — it
     was the *only* live source of synthetic `P01..` fields + human-as-`P01`, and the frontend never
     called it (it uses `/invite` → `spawn_human_tournament`, a real-persona field). Killed the
     orphaned `build_initial_state`/`_build_resolver`/`MAX_*` scaffolding and migrated
     `test_tournament_routes.py` onto a direct registry insert (`_put_human`). The synthetic `P##` ids
     remain only in the legitimate headless engine (`tournament/run.py`, `director.build_initial_state`).
  2. **Reconcile now reuses the live derivation** — `is_autonomous(session, owner)` +
     `real_persona_ids_for(session, personality_repo)` (injected), exactly matching the
     `apply_payout_on_complete` call sites. `flask_app/services/tournament_ticker.py` + both callers
     (`ticker_service.py` watchdog, `tournament_routes.py` admin route) pass `personality_repo`.
- **Regression:** `tests/test_tournament/test_tournament_ticker.py::test_reconcile_human_tournament_credits_real_persona_opponents`
  + `test_reconcile_autonomous_credits_every_entry`. Full tournament suite (340) green.

---

### ✅ 6. Human-leave stake settlement credited the wrong seat — S3
- **Was:** the leave-time stake settle called `credit_ai_cash_out` with the default `from_seat=True`,
  recording a `seat:ai(staker)→ai(staker)` transfer (draining the staker's own seat) instead of the
  borrower's seat, and never branched on a human staker.
- **Fix:** `from_seat=False` + an explicit `record_stake_payoff(source=seat(game_id), sink=…)` from
  the borrower's seat, plus a `STAKER_KIND_HUMAN` branch crediting the human player bankroll.
  `flask_app/routes/cash_routes.py` (mirrors the voluntary-payoff sibling). 303 staking tests green.

### ✅ 7. Per-hand player-table rake used the GROSS pot — S3
- **Was:** `_apply_player_table_rake` raked `game_state.pot['total']` (gross) while the sim rakes the
  NET transfer (`sum of positive stack deltas`), over-raking contested pots up to ~2× and drifting
  from the bank thermostat the sim tuned.
- **Fix:** rake base is now the net transfer — `pot − Σ(winner contributions)`, captured from
  `player.bet` BEFORE the award. `flask_app/handlers/game_handler.py`. 40 rake tests green.

### ✅ 8. Legacy `/api/cash/start` emitted no `player_buy_in` ledger row — S3
- **Was:** the legacy route debited the bankroll but never recorded the paired buy-in, while leave
  unconditionally records a cash-out → unpaired ledger → phantom chips under chip custody.
- **Fix:** added the paired `record_player_buy_in` (mirrors `/api/cash/sit`). `cash_routes.py`. Kept
  the route (3 tests use it) rather than deleting.

### ✅ 9. Live-filled FISH rebuilt as `sharp` on cold-load — S3
- **Was:** `_seat_freshly_filled_ais` built the fish controller in memory but never re-persisted
  `bot_types`/`llm_configs`; the periodic saves omit `llm_configs` and `game_repository` COALESCEs the
  column, so a cold-load restored the fish as `sharp` (solver + per-decision LLM narration).
- **Fix:** stamp `bot_types`/`player_llm_configs` for each freshly-filled seat and persist them with an
  explicit `save_game(llm_configs=…)`. `flask_app/handlers/game_handler.py`. Cash-mode suite (1284) green.

### ✅ 10. ALL-IN `+EV` label ignored equity — S3
- **Was:** `+EV if equity>=0.65 OR cost_to_call > stack*0.5` — the second disjunct labelled an
  overbet shove `+EV` purely on bet size, even at 5% equity.
- **Fix:** the pot-committed disjunct now also requires `equity >= required_equity` (the pot odds).
  `poker/bounded_options.py`.

### ✅ 11. Postflop IP/OOP inverted SB vs BB in blind-vs-blind — S3
- **Was:** a single `_POSITION_ORDER` ranked SB more in-position than BB; SB acts first postflop (OOP)
  in 6-max, so the sharp/tiered bot picked the wrong solver chart in BvB (and mis-routed the induce 2×2).
- **Fix:** table-aware ordering — 6-max ranks BB ahead of SB; heads-up keeps SB (the button) ahead of
  BB. `_determine_position` detects HU (button == small_blind_player). `poker/strategy/postflop_classifier.py`.
  Regressions: `test_bvb_6max_bb_is_ip_sb_is_oop`, `test_heads_up_button_sb_is_ip`.

### ✅ 12. SPR bucket used hero's own stack, not effective — S3
- **Was:** `_determine_spr_bucket` set `effective_stack = player.stack`, overstating SPR when hero
  covers a shorter opp and suppressing the low-SPR `postflop_commit` chart.
- **Fix:** uses `stack_utils.effective_stack_chips` (min hero / largest active opp).
  `poker/strategy/postflop_classifier.py`. Regression: `test_spr_uses_effective_stack`.

### ✅ 13. `shaken` corner zone was unreachable → scared players got an AGGRESSIVE shift — S3
- **Was:** `get_emotional_shift` picks the highest-intensity penalty, but `shaken`'s intensity is a
  PRODUCT of two depths (always < `tilted`'s single depth), so a scared (low conf + low comp) player
  always resolved to the aggressive `tilted` shift instead of the protective `shaken` one.
- **Fix:** corner zones (both axes extreme: shaken/overheated/detached) take precedence over single-axis
  edge zones. `poker/bounded_options.py`. Regression: `TestGetEmotionalShiftCornerPrecedence`.

---

## Open — ranked

### S3 (strategy / rules)

#### ⬜ 14. Short all-in reopens betting for already-acted players (illegal re-raise) — DEFERRED
`place_bet` unconditionally calls `reset_player_action_flags(exclude_current_player=True)` whenever
the high bet rises, even for a sub-min-raise all-in. NLHE rules violation; narrow conjunction; no crash,
conservation intact.
- **Files:** `poker/poker_game.py:489-492,591-623`, `poker/poker_state_machine.py`.
- **Why deferred:** the audit's "gate the flag reset on a full legal raise" is NOT a complete fix — it
  would stop already-acted players from getting their *legal* call/fold on the short all-in (they may
  call or fold, just not re-raise). A correct fix needs call-but-not-raise legal-action restriction in
  core betting logic + heavy round-termination testing. Too risky for a batch; needs its own change.

### S3 (low tier — flavor / dormant / doc-drift)

| # | Issue | Files | Note |
|---|-------|-------|------|
| ✅ 15 | `was_bad_beat` mislabelled ~every showdown loss | `psychology_pipeline.py` | FIXED — the heuristic keyed off the WINNER's hand_rank (≥2 = any pair, ~every showdown). Dropped it (set False); real bad-beat detection is the strict equity detector. Only sets the `pressure_source` label (cosmetic). |
| ⬜ 16 | `bluff_frequency` never updated from gameplay (caller never passes `bluffed=True`) — DEFERRED | `memory_manager.py:784`, `opponent_model.py:1350` | Feature build, not a bug fix: needs real bluff detection (was-aggressor + showed-down-weak) at the observe_showdown call site. Low value (inert prompt descriptor). |
| ✅ 17 | Sim rule-bot context hardcoded `committed_fraction_of_stack=0`/`is_losing_at_table=False` | `rule_based_controller.py` | FIXED (approximation) — stateless proxies (current-round commit fraction; below-average-stack) so the fish leaks fire in sims. Not a perfect mirror (no per-hand stack history in this controller). |
| ✅ 18 | Training `casebot` resolved to v1 live but v2 on cold-load | `training_routes.py` | FIXED — training now uses the canonical `tiered_factory._RULE_BOT_STRATEGY_MAP` (casebot→case_based_v2), matching restore. |
| ✅ 19 | Seated→leave vice bypassed the reserve gate | `cash_mode/ai_vice_spending.py` | FIXED — `commit_leave_vice` now applies `reserve_vice_multiplier` (early-return when reserves healthy + scale the amount), matching the idle path. |
| ✅ 20 | Two divergent `paid_places_for` (15% display vs 30% payout) | `tournament/session.py` → `tournament/economy.py` | FIXED — `session.paid_places_for` delegates to the economy's 0.30 payout structure so the bubble fires where players actually cash. Standings tests updated. |
| ✅ 21 | Anthropic double-counted extended-thinking tokens | `core/llm/providers/anthropic.py` | FIXED — reports `output_tokens` NET of thinking (mirrors OpenAI), so tracking doesn't bill thinking twice. |
| ✅ 22 | THEME_GENERATION tier doc-drift | CLAUDE.md tier map | FIXED — code intentionally uses Assistant tier (PRH-7); doc updated to Assistant + noted `call_type` is a tracking tag, not a selector |
| ✅ 23 | Presence docstrings said "DORMANT" while it's the default-ON prod seat authority | `cash_mode/presence.py`, `entity_presence_repository.py`, `schema_manager.py` (v128) | FIXED — docstrings updated to LIVE/load-bearing |
| ✅ 24 | `OpenAIProvider.image_model` returned the Runware SKU for the default model | `core/llm/providers/openai.py` | FIXED — falls back to `dall-e-3` (OpenAI's own image model); dropped the orphaned IMAGE_MODEL import |
| ✅ 25 | AI chat-bubble avatars always rendered `confident` (dead read of removed `emotional_state`) | `message_handler.py`, `game_routes.py` | FIXED — read `controller.psychology.get_display_emotion()`; updated the false-green avatar test (it mocked the dead attr) |
| ✅ 26 | `math_floor` docstring (0.15 / "≤15%") contradicted active constant (0.05) | `poker/strategy/math_floor.py` | FIXED — docstring now matches `TINY_POT_ODDS_RATIO=0.05` |

---

## `INSERT OR REPLACE` sweep (2026-06-06)

Audited all 18 tables hit by `INSERT OR REPLACE` / `REPLACE INTO`. **Only the two already fixed
above were at risk.** The rest are safe and need no action:

| Table | Reason safe |
|-------|-------------|
| `player_bankroll_state` | write list = full schema |
| `cash_idle_pool` | false positive — SQL scalar `REPLACE()` in a SELECT, not REPLACE INTO |
| `ai_player_state` | omits only rowid + auto-timestamp |
| `controller_state` | omitted cols are dead/legacy, unwritten |
| `opponent_models` | lifetime col snapshotted + restored by design |
| `hand_equity` | sole writer; omits only id/created_at |
| `hand_history` | write list = full schema |
| `hand_commentary` | sole writer; key fresh per hand |
| `avatar_images` | omits only created_at (cosmetic) |
| `cash_pair_stats` | write list = full 5-col schema |
| `enabled_models` | one-time migration; cols added later |
| `ai_side_hustle_state` | full schema; key normally fresh |
| `tournament_results` | omits only rowid id |
| `tournament_standings` | full schema incl. owner_id |
| `ai_vice_state` | full 7-col schema |
| `user_avatars` | already `ON CONFLICT DO UPDATE` |

A single shared upsert helper is **not** warranted — only 2/18 buggy, and the correct SET clause is
table-specific.

---

## Cross-cutting themes

- **`BoundedOption.raise_to == 0` for call/all-in + per-path normalization** — was the single biggest
  damage source (#1, ✅ FIXED). Three recording paths each normalized differently; resolved by one
  shared `normalize_action_amount` helper they all call.
- **SQLite `INSERT OR REPLACE` clobbers omitted columns** — DELETE+INSERT resets any column not in the
  write list (often added by a later migration, or owned by a different writer). The recurring trap:
  an upsert on a *stable/recurring* PK where a *different* path writes a column this statement omits.
  Both confirmed instances are now fixed; prefer `ON CONFLICT DO UPDATE` for multi-writer tables.
- **A canonical impl exists, a sibling drifted** (the "duplicated-divergent" core) — two `paid_places_for`
  (#20), two casebot strategy maps (#18), two rule-bot context builders (#1/#17), two rake helpers (#7),
  the stake-settle site vs its 3 siblings (#6), reconcile vs live tournament payout (#3),
  bounded-options vs action_mapper sizing (#4), per-provider token-split conventions (#21). Collapse to
  one shared function rather than patching each copy.
- **`call_type` is a tracking tag, not a tier selector** — lets documented tier ↔ actual provider drift
  silently (#22).
- **Stale "DORMANT/additive" docstrings vs default-ON flags** — presence authority (#23) is live; audit
  module headers against `economy_flags.py` + compose defaults.
- **Cold-load reconstruction ≠ live build path** — recurring class: bot_type not re-persisted (#9),
  casebot map drift (#18). Anything mutated in-memory mid-session must be re-stamped to the persisted
  snapshot or self-heal on restore.
- **Tests that mock impossible inputs give false green** — `shaken` emotional tests (#13) feed penalty
  dicts the real detector can't produce; the eval harness disables leaks it claims to measure (#17).
  Pin behavior against the real upstream, not hand-built dicts.
