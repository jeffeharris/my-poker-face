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

## Open — ranked

### S3 (mid tier — real but narrower)

#### ⬜ 6. Human-leave stake settlement uses `credit_ai_cash_out(from_seat=True)` with no staker_kind branch
The one stake-settle site not hardened like its 3 siblings — drains the staker's OWN seat instead of
the human borrower's seat. Seat-account ledger drift; human-staker branch is dormant.
- **Files:** `flask_app/routes/cash_routes.py:4759-4773` vs `cash_mode/lobby.py:2780-2868`, `cash_routes.py:2831-2917`.
- **Scope:** Add `STAKER_KIND_HUMAN` branch + `from_seat=False` + `record_stake_payoff` from borrower seat.

#### ⬜ 7. Per-hand rake: player tables rake GROSS pot, sim rakes NET transfer
`_apply_player_table_rake` uses `game_state.pot['total']` (gross) while `full_sim` uses NET stack-delta
sum; docstring claims they mirror. Human cash tables rake up to ~2× the sim-tuned model on contested
pots → bank thermostat assumptions off. Bounded to human tables.
- **Files:** `flask_app/handlers/game_handler.py:2976-2986,3097` vs `cash_mode/full_sim.py:560-569,953`.
- **Scope:** Align rake base (and clamp) to the sim helper.

#### ⬜ 8. Legacy `/api/cash/start` debits bankroll but emits no `player_buy_in` ledger row *(dormant)*
Legacy route debits int bankroll without `record_player_buy_in`, but leave unconditionally emits
`record_player_cash_out` → unpaired ledger → phantom chips in derived balance. Only caller is dead
frontend code; reachable via direct HTTP/stale bundle; partly backstopped by `ledger_reconcile` cron.
- **Files:** `cash_routes.py:1140-1148` vs `sit_at_table:1459`, `_leave_table_locked:4844`.
- **Scope:** Add paired `record_player_buy_in`, or delete the orphaned route.

#### ⬜ 9. Live-filled FISH rebuilds as `sharp` solver on cold-load (`bot_types` never re-persisted)
`_seat_freshly_filled_ais` mutates only in-memory controllers; no save passes `llm_configs=`, so
persisted `bot_types` stays frozen and the fish falls to the `sharp` else-branch on restore →
becomes a solver + makes per-decision LLM narration calls. Narrow: only live-filled fish surviving a
cold-load.
- **Files:** `game_handler.py:2454,2467,431,3905`, `cash_routes.py:1022`, `game_repository.py:109`.
- **Scope:** Re-stamp `bot_types`/`llm_configs` on live-fill, or have restore read the persona's `rule_strategy`.

#### ⬜ 10. ALL-IN option labeled `+EV` purely on bet-size ratio, ignoring equity
`all_in_ev = "+EV" if equity>=0.65 or cost_to_call > stack*0.5` — second disjunct ignores equity.
Mislabels overbet shoves (equity ~0.05–0.45 facing >½-stack bet) as +EV. LLM still has fold/call.
- **Files:** `poker/bounded_options.py:857-864`.
- **Scope:** Gate the second disjunct on equity (pot-odds/committed, not remaining-stack ratio).

#### ⬜ 11. Postflop IP/OOP classifier inverts SB vs BB in blind-vs-blind pots
`_POSITION_ORDER` ranks SB more in-position than BB; SB acts first postflop so it's OOP. Sharp/Tiered
bot picks wrong solver chart + mis-routes the induce 2×2 in BvB. HU special-casing makes a naive fix
break heads-up.
- **Files:** `poker/strategy/postflop_classifier.py:17,51-72`, `poker/strategy/induce_override.py:704-774`.
- **Scope:** Fix SB/BB order without breaking the HU button-collapse path.

#### ⬜ 12. SPR bucket uses hero's own stack, not effective (min) stack
`_determine_spr_bucket` sets `effective_stack = player.stack` (mislabeled), overstating SPR when hero
covers a shorter opp → suppresses `postflop_commit`. One-directional, sharp bot only; SPR fallback
degrades to 'high' anyway.
- **Files:** `poker/strategy/postflop_classifier.py:93-107` (correct helper unused in `stack_utils.py`).
- **Scope:** Use `effective_stack_chips` (min hero/opp).

#### ⬜ 13. `shaken` corner zone unreachable in emotional shift → scared+tilted players get AGGRESSIVE shift
`tilted` and `shaken` share the 0.35 composure threshold and `shaken_intensity` is always a strict
fraction of `tilted_intensity`, so tilted always wins. In band confidence ∈ [0.10,0.35) with
composure<0.35, risk-averse personas get an all-in nudge instead of fold-and-protect. Partly offset
by `compute_modifiers`. (The `shaken` STATE is still reachable via the `timid` zone.) **Tests mock
impossible penalty dicts → false green.**
- **Files:** `poker/zone_detection.py:303-315`, `poker/bounded_options.py:1442-1506,1013-1019`, `poker/psychology_model.py:595-611`.
- **Scope:** Separate the thresholds or re-weight shaken intensity; fix the misleading tests.

#### ⬜ 14. Short all-in reopens betting for already-acted players (illegal re-raise)
`place_bet` unconditionally calls `reset_player_action_flags(exclude_current_player=True)` whenever
the high bet rises, even for a sub-min-raise all-in; `player_all_in` only implements the
min-raise-sizing half of "don't reopen." NLHE rules violation; narrow conjunction; no crash,
conservation intact.
- **Files:** `poker/poker_game.py:489-492,591-623,497-508`, `poker/poker_state_machine.py:289-294`.
- **Scope:** Gate the flag reset on whether the increment is a full legal raise.

### S3 (low tier — flavor / dormant / doc-drift)

| # | Issue | Files | Note |
|---|-------|-------|------|
| ⬜ 15 | `was_bad_beat` mislabels ~every showdown loss as `bad_beat` (keys off winner's rank, not whether loser was ahead) | `psychology_pipeline.py:402-407`, `pressure_detector.py:311-318`, `player_psychology.py:1343-1346` | Cosmetic — doesn't move psychology axes; injects bad-beat whining into prompts |
| ⬜ 16 | `bluff_frequency` never updated from gameplay (caller never passes `bluffed=True`) | `memory_manager.py:749`, `opponent_model.py:1350,941` | Dormant feature; pinned at 0.3 prior; small build, not just a fix |
| ⬜ 17 | Sim rule-bot context hardcodes `committed_fraction_of_stack=0`/`is_losing_at_table=False` | `rule_based_controller.py:408-411` vs `rule_bot_controller.py:261-269` | Eval-fidelity only; live fish route through tiered `calling_station` |
| ⬜ 18 | Training `casebot` resolves to v1 strategy live but v2 on cold-load (two hand-rolled maps disagree) | `training_routes.py:102` vs `tiered_factory.py:113`, `game_handler.py:412` | Practice opponent silently swaps strength on eviction/restore |
| ⬜ 19 | Seated→leave vice intercept bypasses the reserve gate that throttles the idle path | `ai_vice_spending.py:905-1012,769-787`, `lobby.py:2491-2494` | When `VICE_RESERVE_GATED` armed (prod default ON), over-refills reserves |
| ⬜ 20 | Two divergent `paid_places_for` (15% display vs 30% payout) | `tournament/session.py:46-51` vs `tournament/economy.py:21-33` | Bubble beat fires at wrong moment; no chip mishandling |
| ⬜ 21 | Anthropic provider double-counts extended-thinking tokens in cost estimation | `core/llm/providers/anthropic.py:207-212`, `tracking.py:476-491` | Over-estimates spend; non-default tier |
| ⬜ 22 | THEME_GENERATION routed to Assistant tier (deepseek), not documented Default (gpt-5-mini) | `personality_routes.py:689,703` + CLAUDE.md tier map | Cost-attribution drift; possibly the doc is stale, not the code |
| ⬜ 23 | Presence subsystem docstrings say "DORMANT — nothing reads/writes" while it's the default-ON prod seat authority | `cash_mode/presence.py:8-12`, `entity_presence_repository.py:9-12`, `schema_manager.py:7157-7162` | Stale-doc footgun; runtime is correct + self-healing |
| ⬜ 24 | `OpenAIProvider.image_model` returns Runware id for the default model | `core/llm/providers/openai.py:71-74`, `config.py:75` | Dead path; guaranteed 400 if ever hit |
| ⬜ 25 | AI chat-bubble avatars always render `confident` (dead branch reads removed attr) | `message_handler.py:159`, `game_handler.py:596` | Cosmetic; only the departed-AI farewell edge case |
| ⬜ 26 | `math_floor` docstring (0.15 / "≤15%") contradicts active constant (0.05) | `poker/strategy/math_floor.py:44,71-73,124` | Doc only; zero behavioral effect |

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
