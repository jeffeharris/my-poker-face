---
purpose: Catalog of major intent-vs-implementation bugs, logic errors, and duplicated-divergent implementations found in a multi-agent audit, ranked for triage
type: reference
created: 2026-06-06
last_updated: 2026-06-07
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

## Captain's Log Signals Reviewed

Reviewed `docs/captains-log/` for project history before continuing the audit. The recurring bug
classes are now part of the audit checklist:

- This pass re-read the cash cold-load seat orphan, sponsorship seat hold, chip-custody cutover /
  atomic-write, multi-table tournament engine, and schema-drift logs. Those histories are used as
  confirmation context for the new cash-presence and single-table cold-load findings below.
- A later history pass also re-read the persona-identity / cash-world continuity, launch-day cutover,
  presence-shadow, and prestige/renown logs. The added account-linking finding below follows the same
  recurring lesson: a user identity change must migrate every durable owner-scoped surface, not only games.
- **Cold-load parity:** restored games must reattach every memory-only field that fresh builders stamp
  (`cash_table_id`, tournament session metadata, identity/nickname, cash-world psychology, routing flags).
- **One authority per state machine:** cash seats/presence and chip custody repeatedly failed when two stores
  were both treated as authoritative, or when route helpers wrote outside the chokepoint transaction.
- **Retryable money movement:** status rows must not be marked settled/complete before bankroll, ledger, and
  escrow transfers are durable and recoverable.
- **Presence before enrollment:** tournament entry and cash seating must enforce single-presence at the
  effectful boundary, not just as a best-effort cleanup.
- **Schema/version numbers are not proof:** long-lived DBs can miss renumbered migrations; use completeness
  checks against fresh canonical schema, especially before prod or branch cutovers.
- **Client-owned presentation needs backend gating:** run-out/showdown sequencing deliberately moved pacing to
  the client, so REST/socket "available action" contracts must not expose stale betting controls during
  non-betting presentation phases.

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

### S1 (socket game-state privacy)

#### ⬜ 58. Socket `update_game_state` leaks every hole card and the deck order
The socket serializer starts from the raw core `PokerGameState.to_dict()`. That serializer includes
every player's `hand`, plus `deck` and `discard_pile`, and the socket path emits the dict as normal
`update_game_state`. The REST game-state route has a separate player loop that deliberately returns
only the human hand, and the socket layer has a separate `reveal_hole_cards` event for run-it-out /
showdown reveals, so private cards should not be present in routine state pushes.
- **Files:** `poker/poker_game.py:77-93,136-145`, `flask_app/handlers/game_handler.py:521-523,709`,
  `flask_app/handlers/game_handler.py:795-805`, `flask_app/routes/game_routes.py:1298-1302`,
  `react/react/src/hooks/usePokerGame.ts:287-293`.
- **Why confirmed:** REST scrubs non-human hands, but socket state uses the raw serializer before
  emitting to the game room; no later socket scrub removes `players[*].hand`, `deck`, or `discard_pile`.
- **Impact:** any authorized browser client can inspect socket payloads to see opponents' hole cards
  and future deck order during live cash or tournament hands.
- **Fix sketch:** create one shared public game-state serializer for REST and socket; strip `deck`,
  `discard_pile`, and non-human hands from ordinary updates; keep full-card payloads only in the
  explicit reveal event. Add socket contract tests for pre-reveal hands and deck absence.


### S1 (tournament escrow / payout recovery)

#### ⬜ 72. Completed tournament rows can hide unpaid `pending` escrow from every recovery path
The manual `/advance` and `/play-out` routes persist the completed tournament session before payout runs. A process
exit between the persist and `_maybe_payout()`, or any pre-claim exception inside `_maybe_payout()` such as sandbox
resolution failure, leaves the row as `status='complete'` while `payout_status` is still `pending`. The active-tournament
lookup ignores completed rows, and the payout watchdog/admin reconcile scan only lists `payout_status='in_progress'`;
`apply_payout_on_complete()` itself also no-ops unless a caller explicitly invokes it while status is still pending.
- **Files:** `flask_app/routes/tournament_routes.py:526-527,552-553` (complete session persisted before payout),
  `flask_app/routes/tournament_routes.py:108-144` (`_maybe_payout` best-effort wrapper),
  `flask_app/services/tournament_economy_service.py:255-271` (main payout starts only from `pending`),
  `poker/repositories/tournament_session_repository.py:120-128,242-256` (active lookup and stuck scan filters),
  `flask_app/services/tournament_ticker.py:278-324` (watchdog consumes only stuck `in_progress` rows).
- **Why confirmed:** the current route order creates a `complete/pending` state that neither active resume nor stuck
  payout reconcile enumerates. This is not #43's live-table mutation and not #52's opposite `active/complete` pair.
- **Impact:** funded tournament escrow can remain unpaid and unreconciled while the tournament no longer appears active
  or stuck. Entrants are released from tournament presence, but prize distribution and escrow sweep never run.
- **Fix sketch:** do not persist terminal `status='complete'` before payout has either claimed or explicitly skipped;
  or extend the recovery scanner to handle `status='complete' AND payout_status='pending'` by safely claiming payout.
  Add a crash/pre-claim-exception regression for `/advance` and `/play-out`.

#### ⬜ 73. Escrow-in rollback can delete the only recoverable tournament row while leaving partial escrow
Tournament registration writes the `tournaments` row, stamps economy columns, then writes buy-in and overlay escrow
ledger rows. Those ledger helpers return `None` on write failure; if exactly one escrow row lands, the post-write
balance check raises. The mismatch branch does not reverse the successful escrow row, and the spawn/accept callers
catch the exception and delete the tournament row. That can leave a `tournament:<id>` escrow balance with no durable
`tournaments` row for payout, active-tournament lookup, or stuck-payout reconcile.
- **Files:** `flask_app/services/tournament_spawn.py:188-224,330-366` (row written before funding, then deleted on
  funding exception), `flask_app/services/tournament_economy_service.py:75-88,107-119,139-172` (economy stamp,
  best-effort escrow rows, mismatch raise), `core/economy/ledger.py:470-479,482-565,883-907` (ledger helpers swallow
  failed writes), `flask_app/services/tournament_economy_service.py:450-451` (reconcile requires a row at
  `payout_status='in_progress'`).
- **Why confirmed:** the code comments claim escrow rows are load-bearing and verified, but the rollback path removes
  the row without sweeping any escrow row that already landed. This is distinct from #39's public delete route; it is
  the registration rollback path.
- **Impact:** partial tournament escrow can become orphaned and invisible to normal payout/reconcile tooling. Human
  bankroll rollback can also disagree with the ledger-derived escrow balance, creating custody drift around tournament
  registration failures.
- **Fix sketch:** treat escrow-in as a saga with compensating ledger writes: if verification fails, reverse/sweep any
  landed escrow before deleting or mark the tournament row recoverable with a failed-funding status. Add regressions for
  buy-in-only and overlay-only ledger landing.

### S2 (memory / cash-pair stats)

#### ⬜ 27. Human REST/socket all-ins still record `amount=0` — scope miss from #1
The shared `normalize_action_amount()` helper explicitly says every live/sim/experiment recording path
must normalize both `call` and `all_in`, and `handle_ai_action` does. The two human action paths in
`flask_app/routes/game_routes.py` still carry the older inline call-only normalization:
`record_amount = amount; if action == "call": ...`. Human UI all-ins pass `amount=0`, so the real chip
settlement is correct but the memory hand recorder still stores a zero-chip shove. That recreates the
worst part of #1 for human shove hands: `RecordedHand.get_player_contributions()` can drop the shover as
a loser, suppressing `ChipFlow`, BIG_WIN/BIG_LOSS/KNOCKOUT events, and `cash_pair_stats` PnL for a
high-impact class of cash and tournament hands.
- **Files:** `flask_app/routes/game_routes.py:2083-2089,2608-2613`;
  correct sibling path `flask_app/handlers/game_handler.py:4621-4632`;
  helper contract `poker/memory/memory_manager.py:50-86`.
- **Why confirmed:** current source contradicts the fixed-finding text above: AI web handling uses the
  shared helper, but both human REST and socket handlers duplicate only the call branch. Existing
  regression tests prove the helper and direct `record_action_in_memory` behavior, not these two route
  call sites.
- **Fix sketch:** replace both inline call-only blocks with `normalize_action_amount(action, amount,
  highest_bet=pre_action_state.highest_bet, player_bet=current_player.bet,
  player_stack=current_player.stack)`. Add REST and socket route-level regressions for a human all-in
  with request/socket `amount=0` asserting the recorded action amount equals the pre-action stack.

### S2 (live action concurrency)

#### ⬜ 29. Human action handlers mutate game state outside the per-game lock — double-submit race
`progress_game()` serializes AI turns and phase advancement with `game_state_service.get_game_lock(game_id)`,
and the cold-load path explicitly documents why load/recovery must re-check under that lock. The two human
action handlers do not acquire it. They read `current_game_data`, validate `awaiting_action`, run
`play_turn()`, record memory/coach/decision side effects, write `state_machine.game_state`, persist, and only
then call `progress_game(game_id)` (which may short-circuit if another progression already holds the lock).
Two REST requests, two socket events, or one of each can therefore both validate the same human turn before
either writes `awaiting_action=False`. Final chip state is last-write-wins on the shared state machine, but
side effects before the write can duplicate or diverge: decision analysis, coach progression, memory actions,
table messages, opponent models, and DB saves. Different concurrent actions are worse because the later write
can clobber the earlier game-state mutation while preserving earlier side effects.
- **Files:** `flask_app/routes/game_routes.py:2018-2130` (REST action),
  `flask_app/routes/game_routes.py:2525-2650` (socket action),
  `flask_app/handlers/game_handler.py:3962-3969` (`progress_game` lock),
  `flask_app/services/game_state_service.py:105-120,160-174` (plain get/set; explicit lock API).
- **Why confirmed:** current source has no lock or turn nonce around either human action critical section,
  while adjacent cold-load code at `game_routes.py:645-652` and cash leave/top-up tests document the same
  read-mutate-write hazard and use the per-game lock as the intended guard.
- **Fix sketch:** acquire `get_game_lock(game_id)` before re-reading `current_game_data`, validation,
  `play_turn`, side effects, `set_game`, and save; release before calling `progress_game()` if keeping its
  non-blocking lock semantics. Alternatively add an action nonce/version in the emitted game state and reject
  stale submits, but the state mutation still needs serialization.

#### ⬜ 96. Retry endpoint can unlock an in-flight `progress_game()` and start a second progression loop
`progress_game()` uses a non-blocking per-game `threading.Lock` so only one AI/phase-advance loop mutates a game at a
time. The manual retry route breaks that contract: if it sees the game's lock is held, it calls `lock.release()` from
the request thread, marks `game_started=False`, and calls `progress_game()` again. Python's plain `threading.Lock` is
not owner-tracked, so this can release a lock held by the real progress thread while that thread is still inside an AI
decision, run-it-out transition, hand evaluation, save, or emit path. The original thread will later continue and hit
its own `finally: lock.release()`, while the retry-triggered loop may already be mutating the same `state_machine`.
- **Files:** `flask_app/routes/game_routes.py:2267-2328` (retry route releases a held lock and restarts progression),
  `flask_app/handlers/game_handler.py:3956-3969,3980-4138` (progress loop owns the lock for AI turns, emits, saves,
  run-it-out, and hand-evaluation work), `flask_app/services/game_state_service.py:160-174` (plain per-game
  `threading.Lock`).
- **Why confirmed:** the cleanup code explicitly avoids evicting games with held locks because minting/releasing a second
  lock allows concurrent progression. The retry route does the equivalent by force-releasing the held lock instead of
  waiting, marking a retry request as a concurrency primitive rather than a recovery signal. This is distinct from #29,
  where human action handlers fail to take the lock; here a route tears down the lock while it is actively protecting
  `progress_game()`.
- **Impact:** a user retry during a slow AI turn can produce two live progression loops for one game. That risks double AI
  actions, duplicate table messages/LLM calls, skipped or repeated phase transitions, duplicate hand-end settlement,
  inconsistent persistence, and a final `RuntimeError` when the original loop releases an already-unlocked lock.
- **Fix sketch:** never release a held game lock from retry. Return `409 already_running` with diagnostic data, or set a
  retry/recovery flag that the active `progress_game()` loop checks between bounded AI calls. If forced recovery is
  needed, use a separate watchdog with ownership/lease timestamps and restart only after proving the holder is gone. Add
  a regression where retry is called while `progress_game()` holds the lock and assert no second loop starts.

#### ⬜ 104. REST action route lets admins play another user's human turn
`_authorize_game_access()` is an owner-or-admin helper, and the REST `/api/game/<id>/action` route uses it for the
same mutation surface that actually plays a human poker action. Once that helper grants admin access, the action
validator only checks that the current table actor is a human seat and that the requested action is available; it
never requires the requester to be the game owner or an explicit impersonation actor. The socket `player_action`
path is stricter and rejects admins unless their user id equals the cached owner id, while the active frontend posts
human actions through REST.
- **Files:** `flask_app/routes/game_routes.py:96-138` (admin bypass in shared game access helper),
  `flask_app/routes/game_routes.py:2010-2042` (REST action calls that helper, then validates/mutates),
  `flask_app/validation.py:12-24` (validates human turn and action availability, not requester-to-seat identity),
  `flask_app/routes/game_routes.py:2523-2550` (socket action remains owner-only),
  `react/react/src/hooks/usePokerGame.ts:818-826` (frontend action dispatch uses REST).
- **Why confirmed:** source has two different authorization contracts for the same human action. REST grants admin
  read/admin access and then proceeds into the mutating action path; socket action denies the same admin as `NOT_OWNER`.
- **Impact:** an admin inspecting or debugging another user's live game can accidentally or intentionally take the
  user's poker action, consuming their turn and producing normal game, memory, coach, prompt, and bankroll side effects.
  It also makes REST/socket action behavior inconsistent for privileged users.
- **Fix sketch:** split read/admin game access from player-action authorization. Require `current_user.id == owner_id`
  for `/api/game/<id>/action` unless an explicit audited impersonation/admin-action mode is added, and add REST tests
  for owner, non-owner, and admin action attempts alongside the existing socket auth coverage.

### S2 (admin / session security)

#### ⬜ 109. `/admin/*` mutating endpoints bypass the production CSRF gate
The CSRF middleware is enabled in production, but it protects only mutating paths whose URL starts with `/api/`.
The React fetch monkeypatch uses the same `/api/` test before adding `X-CSRF-Token`. The admin dashboard blueprint
is mounted at `/admin`, so its state-changing routes land at `/admin/api/...` or `/admin/pricing...`: model toggles,
pricing writes/deletes, settings updates/resets, playground replay/cleanup, and reference-image uploads can all be
cookie-authenticated mutating requests outside the CSRF check.
- **Files:** `flask_app/config.py:67-78` (production CSRF default on), `flask_app/csrf.py:49-58,68-74`
  (mutating request must start with `/api/` or it is skipped), `react/react/src/utils/csrf.ts:28-36,54-65`
  (frontend only injects CSRF headers for `/api/` paths), `flask_app/routes/admin_dashboard_routes.py:28-29`
  (blueprint prefix `/admin`), `flask_app/routes/admin_dashboard_routes.py:132-162,1046-1119,1345-1475`
  (representative admin mutations under `/admin/api/*` and `/admin/pricing*`).
- **Why confirmed:** a production `POST /admin/api/settings` or `DELETE /admin/pricing/<id>` is not protected by
  `_is_protected_request()` because the full request path starts with `/admin/`, not `/api/`. The frontend wrapper
  also would not attach a token to those same paths, confirming the intended CSRF contract does not cover them.
- **Impact:** any cross-site request that reaches an authenticated admin browser can change model visibility, pricing,
  prompt/settings retention, alert webhooks, or image/playground assets without the double-submit token that the app
  assumes protects cookie-authenticated state changes.
- **Fix sketch:** protect all cookie-authenticated mutating routes, not only `/api/*`, or move admin JSON mutations
  under `/api/admin/*`. Update the frontend CSRF wrapper to match the backend path policy and add regressions for
  `/admin/api/settings`, `/admin/pricing`, and an ordinary `/api/*` POST.

### S2 (cash chip custody / sponsorship)

#### ⬜ 33. Personality-backed `sponsor-and-sit` creates a stake principal without debiting the AI lender
The sponsor route says personality loans are pure transfers from the AI lender's bankroll to the player's
table stack, while house loans are central-bank issuance. The route creates an active `Stake` with
`staker_id=offer_lender_id` and `principal=offer_amount`, then records a cash session whose human stack is
fully sponsor-funded. The house branch records a `house_stake_issue`; the personality branch records no
lender debit and no `stake_fund` transfer. Its comment says the personality transfer happens through
`_build_cash_game`, but the table-aware build path explicitly skips AI bankroll debits because preselected
AI chips are already on the table, and the legacy build path only debits selected AIs for their own seats,
not a human borrower's sponsored principal. Leave settlement later credits the personality staker from the
borrower seat, so the lender can be paid principal/profit for chips that were never removed at origination.
- **Files:** `flask_app/routes/cash_routes.py:1915-1929` (personality stake intent),
  `flask_app/routes/cash_routes.py:2241-2275` (stake row),
  `flask_app/routes/cash_routes.py:2311-2320` (house-only issuance branch),
  `flask_app/routes/cash_routes.py:688-694` (table-aware AI debit skipped),
  `flask_app/routes/cash_routes.py:4793-4807` (leave-time staker credit).
- **Why confirmed:** current source has no personality-lender debit/transfer on the route path that creates
  the funded human stack and stake row; settlement does credit the staker later.
- **Fix sketch:** for personality sponsorship, debit the lender's AI bankroll and record a `stake_fund`
  transfer from `ai:<lender>` to the borrower seat before seating/stack creation, with failure aborting the
  stake/session. Add route-level tests asserting lender bankroll decreases and the ledger contains the
  funding transfer, while house sponsorship remains central-bank issuance.

#### ⬜ 34. Table-aware cash leave credits AI stacks to bankroll and also preserves those stacks on the table
Lobby v1.5 table-aware game creation treats preselected AI chips as already on the persistent cash table and
skips bankroll debit. On human leave, `_leave_table_locked` credits every seated AI's current live stack back
to its persistent AI bankroll, then also writes each non-busted AI's stack back into the `cash_tables.seats`
row. The custody audit counts stored AI bankrolls and cash-table AI seat chips as separate chip-bearing
surfaces, so a non-busted AI stack can exist in both places after the human leaves.
- **Files:** `flask_app/routes/cash_routes.py:688-694` (table-aware AI bankrolls not debited),
  `flask_app/routes/cash_routes.py:4931-4955` (AI cash-out credit on leave),
  `flask_app/routes/cash_routes.py:4985-5010` (same AI stacks persisted back to table seats),
  `flask_app/services/chip_ledger_audit.py:123-126` (bankroll and table-seat buckets counted separately).
- **Why confirmed:** the leave path performs both mutations in sequence, and the audit treats the two
  destinations as independent custody surfaces. This does not require a race.
- **Fix sketch:** decide single custody owner for table-aware seated AI chips. If chips remain on
  `cash_tables.seats`, do not credit bankroll on human leave; if cashing AIs out to bankroll, clear/free or
  zero their table seats. Add a route-level leave regression with at least one AI seat and assert total custody
  does not increase.

#### ⬜ 44. Stake settlement marks rows non-retryable before the chips move
Two human cash stake repayment paths claim/settle the stake row before the borrower/staker bankroll and ledger
side effects are durable. On table leave, `_leave_table_locked()` calls `settle_stake_on_leave()` first; that
helper flips the stake to `settled`/`carry` and payout columns before the route executes the borrower seat ->
staker/house/borrower transfers. A crash or exception in the later transfer loop leaves the stake no longer
visible to `load_active_for_session()`, so a retry takes the no-active-stake branch and can credit the borrower
their full stack. The voluntary carry-payoff route has the same shape: it CASes `carry -> settled` before
debiting the human, crediting the staker, writing the payoff ledger row, and zeroing `carry_amount`.
- **Files:** `flask_app/routes/cash_routes.py:4757-4766,4770-4848,4868-4881` (leave status claim before chip
  movements), `cash_mode/stake_settlement.py:191-203,209-215` (status/payout persistence),
  `poker/repositories/stake_repository.py:163-191` (retry only finds `status='active'`),
  `flask_app/routes/cash_routes.py:2880-2941` (carry payoff status before debit/credit/ledger).
- **Why confirmed/history:** current source has independent repo commits before the money movement. The
  captain's log for chip-custody atomic writes explicitly says the human cash routes were deferred from the
  unit-of-work conversion because wrapping their interleaved repo writes would deadlock
  (`docs/captains-log/development/chip-custody-atomic-writes.md:56-68`), so the crash window is a known
  unclosed tail.
- **Impact:** an interrupted leave/payoff can strand a staker unpaid while the stake appears settled or gone
  from the active-settlement path. Later retries may over-credit the borrower, under-credit the staker, and
  leave the ledger/audit with no normal route to reconcile the missing payoff.
- **Fix sketch:** introduce a durable `settling`/`paying` state with reconciliation, or move stake status,
  carry/payout columns, borrower debit, staker/house credit, and ledger rows into a single safe unit. Only mark
  `settled` after the chip movement is durable; retry in-progress rows by paying the remaining ledger delta.

#### ⬜ 45. Human-to-AI match-share staking ignores a refused AI match debit and seats unfunded chips
The human staking route prechecks that the target AI can cover a match-share contribution, then later performs
the actual AI match debit inside the sandbox-locked mutation path. That later debit can still refuse because
bankroll projection changed, the row is missing, or a concurrent drain landed. `debit_bankroll_for_seat()` says
callers must unwind on `None`, but the route ignores the return value, writes an AI seat with
`principal + match_amount`, and creates the active stake row anyway.
- **Files:** `flask_app/routes/cash_routes.py:3786-3810` (precheck only),
  `flask_app/routes/cash_routes.py:3933,4012-4024,4030-4057` (seat funded and stake saved even if debit
  returns `None`), `cash_mode/bankroll.py:495-500,522-534,549-561` (debit refusal contract).
- **Why confirmed:** the route does not inspect the helper return, and the helper has explicit refusal paths
  before any bankroll/ledger write.
- **Impact:** a stale projection or concurrent chip drain can mint the AI's match contribution onto a table
  without debiting the AI bankroll or recording the matching ledger transfer. The resulting stake principal is
  overstated and later settlement can pay the human staker from unfunded chips.
- **Fix sketch:** inside the sandbox lock, require `debit_bankroll_for_seat(...)` to return a state/result. On
  `None`, abort before saving the seat/stake, or roll back the already-applied human debit and ledger rows.


#### ⬜ 76. Side-hustle expiry can erase the only recovery marker for a failed up-front payout
The side-hustle start path deliberately inserts `ai_side_hustle_state` before crediting the up-front earning. If
`_credit_hustle_payout()` cannot load/save the AI bankroll, it returns `0`, but the off-grid row remains with the
intended `amount`. When the row later expires, `tick_side_hustle_expirations()` deletes it first, never retries or
verifies the bankroll/ledger credit, and returns a `HustleEndResult` whose `paid_amount` simply echoes the stored
amount. The lobby can report a paid return while the only durable row that could have identified the missed credit
has been removed.
- **Files:** `cash_mode/ai_side_hustle.py:432-463` (row inserted before credit, failed credit only lowers local
  pool accounting), `cash_mode/ai_side_hustle.py:467-545` (expiry deletes first and echoes amount as paid),
  `cash_mode/ai_side_hustle.py:618-712` (`_credit_hustle_payout` returns `0` on load/save failure),
  `poker/repositories/side_hustle_state_repository.py:189-199` (delete is the only state transition).
- **Why confirmed:** the closed-loop tests assert the happy path and empty-pool no-start case, but there is no
  regression for a committed hustle row followed by a failed up-front credit. In that state, expiry removes the
  only recovery marker without checking whether any `side_hustle_earning` ledger row exists.
- **Impact:** an AI can stay broke after an announced earning, while the system has no normal retry/reconcile path
  and the UI/ticker treats the payout as completed.
- **Fix sketch:** store a payout status/credited amount on the side-hustle row, or make row insert + bankroll credit
  + ledger draw one transaction. Expiry should only delete rows whose credit is durable; failed rows should retry or
  remain in a recoverable `payout_failed`/`credit_pending` state.

#### ⬜ 80. Human-to-AI stake offers bypass the stakeable visibility pool
The curated `GET /api/cash/stakable-ai` endpoint starts from `list_stakeable_ai()`, which in turn starts from
`personality_repo.list_eligible_for_cash_mode(user_id=owner_id)`. That pool applies public/circulating/owner/fish and
cash-ineligible filters before relationship/economic gates. `POST /api/cash/stakes/offer` does not reuse that pool: it
accepts arbitrary `target_pid`, directly loads `personality_repo.load_personality_by_id(target_pid)`, and then proceeds
through the later stake gates. If a hidden/private/disabled/non-circulating id satisfies those later gates, the route can
seat and stake a persona the curated panel would never show.
- **Files:** `flask_app/routes/cash_routes.py:3358-3446` (curated stakable list),
  `cash_mode/player_staking.py:420-430` (candidate pool from `list_eligible_for_cash_mode`),
  `poker/repositories/personality_repository.py:511-590` (cash-mode eligibility policy),
  `flask_app/routes/cash_routes.py:3517-3637,3956-4057` (offer path direct lookup, debit/seat/stake).
- **Why confirmed:** the list and POST routes use different target sources. The POST route validates existence via
  direct id lookup but never asks whether that id was eligible for the caller's cash-mode pool.
- **Impact:** knowing a personality id can let a player bypass roster visibility/curation and put hidden or private
  personas into live cash staking flows, with bankroll debit and active stake creation.
- **Fix sketch:** resolve `target_pid` through the same eligible/stakeable candidate set under the sandbox lock before
  accepting the offer. Add route tests for disabled, non-circulating public, other-user-private, owner-private, and
  eligible public personas.

#### ⬜ 82. Staker forgiveness treats string `false` as a grant
`POST /api/cash/stakes/<stake_id>/staker-forgive` documents `{"grant": bool}` but parses the field with
`grant = bool(body.get("grant"))`. Any non-empty string is truthy in Python, so JSON such as `{"grant":"false"}` or
`{"grant":"0"}` enters the grant branch. That branch zeroes `carry_amount`, marks the stake `settled`, clears the
pending ask, and records `STAKE_FORGIVEN`; the refusal branch only clears the ask and records refusal.
- **Files:** `flask_app/routes/cash_routes.py:3247-3271` (route contract + bool coercion),
  `flask_app/routes/cash_routes.py:3296-3308` (grant/refuse side effects).
- **Why confirmed:** current parsing distinguishes only truthy/falsy Python values, not JSON booleans. Non-empty string
  values are accepted instead of rejected.
- **Impact:** a malformed client or tampered request can forgive an AI borrower's carry when the staker intended refusal,
  erasing a human receivable and settling the stake.
- **Fix sketch:** require `grant` to be an actual JSON boolean and return 400 for missing/non-bool values. Add route
  regressions for `false`, `true`, `"false"`, `"0"`, missing, and `null`.

#### ⬜ 68. Sponsor/stake seat claims can evict the presence-authoritative occupant
Self-funded `/api/cash/sit` checks `entity_presence` before claiming a seat, but `/api/cash/sponsor-and-sit`
and `/api/cash/stakes/offer` trust the projected `cash_tables` slot kind before saving a new table payload.
`save_table()` then drives presence departures for any occupant missing from that payload. A stale table row or
race can therefore let a sponsor/stake claim overwrite a seat that the presence machine still considers occupied.
- **Files:** `flask_app/routes/cash_routes.py:1392` (self-funded presence guard),
  `flask_app/routes/cash_routes.py:2162` and `flask_app/routes/cash_routes.py:3944` (sponsor/stake slot-only
  checks), `cash_mode/presence_transitions.py:309` (missing occupants become departures).
- **Why confirmed/history:** explorer pass found the guard asymmetry in the current routes. The captain's
  cold-load seat-orphan and sponsorship-seat logs document this exact historical failure class: cash table rows
  and presence/live-game truth drift, then a later fill/claim makes the wrong store authoritative.
- **Impact:** a human or AI that is still present can be forced out by a stale projected seat write, corrupting
  session/presence state and any stake/custody tied to the displaced seat.
- **Fix sketch:** make sponsor and stake seat claims re-check presence under the same sandbox/seat lock used for
  the effectful claim, and reject or reconcile when the projected table row disagrees with presence. Add races/stale
  row regressions for sponsor sit and stake offer.

#### ⬜ 69. Concurrent human-to-AI stake offers can create multiple active stakes for one AI borrower
The "AI already has active stake" check runs before the sandbox lock, and the locked section only re-checks that
the chosen seat is still open before writing the seat and stake. The schema has indexes over stake state, but no
unique active-borrower constraint, so two concurrent offers can both pass the pre-lock check and create separate
active stakes for the same AI.
- **Files:** `flask_app/routes/cash_routes.py:3658` (active-stake check before lock),
  `flask_app/routes/cash_routes.py:3944` and `flask_app/routes/cash_routes.py:4037` (locked seat/stake writes),
  `poker/repositories/schema_manager.py:5826,5855` (non-unique stake indexes).
- **Why confirmed/history:** current code has no in-lock borrower cardinality check. This is separate from #45's
  unfunded match-share branch; it affects pure human-to-AI stakes even when every debit succeeds.
- **Impact:** one AI can carry multiple active stake principals, producing ambiguous payoff/carry settlement and
  overstating custody obligations for that borrower.
- **Fix sketch:** move the active-borrower claim into the locked critical section and add a durable uniqueness guard
  for non-terminal stakes if SQLite/DB version permits it. Add a two-offer concurrency regression.

#### ⬜ 70. Ghost cash leave can close a staked session without settling the active stake
If warm-load fails, `_leave_table_locked()` takes the ghost/memory-miss path, finalizes the cash session at zero,
and returns before the normal `load_active_for_session()` / `settle_stake_on_leave()` settlement branch runs. The
boot cleanup path does settle active stakes at zero before removing orphaned sessions, which shows the intended
missing step.
- **Files:** `flask_app/routes/cash_routes.py:4638,4738` (ghost leave finalizes/returns),
  `poker/repositories/cash_session_repository.py:283` (session close),
  `poker/repositories/stake_repository.py:480` (active stake still counted),
  `cash_mode/lobby.py:4385` (boot sweep settles active stakes at zero).
- **Why confirmed/history:** current leave path skips stake settlement entirely in the ghost branch. The cash
  cold-load seat-orphan log records this same "memory miss becomes a different cleanup path" class.
- **Impact:** the cash session is closed but the active stake remains outstanding, leaving custody/accounting and
  future stake eligibility inconsistent with the player's actual table departure.
- **Fix sketch:** make every leave finalization path settle or terminally discharge any active stake before closing
  the session. Add a regression for a staked session whose live game is missing but cash session row remains active.

### S2 (tournament / cash presence and escrow)

#### ⬜ 38. Main Event invite accept proceeds even when the required cash leave fails
The invite-accept route documents a hard invariant: before entering the Main Event, the human must be stood
up from any active cash table so they are in exactly one place and their table chips are cashed back into
bankroll. The helper that enforces this is fail-open. `_leave_cash_if_seated()` sets `leave_requested`, calls
`_leave_table_locked()`, catches every exception, logs it, then returns `True`; `accept_invite()` ignores that
return value entirely and proceeds into `invites.accept()` and tournament buy-in/funding. A leave failure (or a
leave path that returns an error response instead of raising) therefore does not block tournament entry.
- **Files:** `flask_app/routes/tournament_routes.py:386-406` (required cash leave is best-effort),
  `flask_app/routes/tournament_routes.py:432-448` (accept ignores the leave result and enters the
  sandbox-locked accept flow), `flask_app/routes/cash_routes.py:4526-4668` (leave can return cleanup/error
  responses without the caller inspecting status).
- **Why confirmed:** current source states the single-presence intent, but the effectful enforcement swallows
  failure and the route does not re-check cash presence before creating the tournament.
- **Impact:** a player can keep a cash table seat/stack and also enter the Main Event. Their cash stack may not
  be credited back before buy-in affordability is checked, cash-table occupancy can stay human while tournament
  participant exclusion also applies, and any later leave/retry has to reconcile a session that should not have
  survived tournament entry.
- **Fix sketch:** make `_leave_cash_if_seated()` fail closed: return an explicit success/error result or raise
  on non-2xx `_leave_table_locked()` responses, and have `accept_invite()` abort with a retryable 409/503 until
  the cash session is actually closed. Add a route regression where `_leave_table_locked()` raises/returns an
  error and assert `invites.accept()` is not called.

#### ⬜ 39. `DELETE /api/tournament/<id>` can strand funded tournament escrow and hide it from payout recovery
The tournament real-chip layer treats `tournament:<id>` escrow as load-bearing: `apply_buy_in()` debits the
human, stamps economy columns, writes escrow ledger rows, and payout later loads the tournament row plus
`payout_status` before draining escrow. The public delete route does none of that. It checks ownership, calls
`registry.delete(tournament_id)`, and returns `ok`; `registry.delete()` deletes the durable `tournaments` row.
If the tournament was already funded and still active/pending, the human buy-in and/or bank overlay remain in
ledger escrow under `tournament:<id>`, but the row that `apply_payout_on_complete()` and the stuck-payout
watchdog need is gone.
- **Files:** `flask_app/services/tournament_economy_service.py:78-85,93-119,139-165` (buy-in/overlay escrow
  contract), `flask_app/services/tournament_economy_service.py:245-257` (payout no-ops when row is missing or
  not pending), `flask_app/routes/tournament_routes.py:599-608` (delete route),
  `flask_app/services/tournament_registry.py:278-284` (durable row delete).
- **Why confirmed:** the delete route has no status/payout/escrow branch; existing route coverage only asserts
  the standings row disappears, not that funded escrow is refunded, swept, or recoverable.
- **Impact:** abandoning an active funded tournament can burn the player's buy-in, leave a hidden
  `tournament:<id>` ledger balance, release entrants from tournament exclusion by deleting the row, and make
  payout reconciliation impossible because recovery lists `tournaments` rows.
- **Fix sketch:** do not hard-delete funded active tournaments. Either reject delete once `payout_status` is
  `pending`/`in_progress`, or implement an explicit abandon flow that atomically refunds/sweeps escrow, stamps a
  terminal status, and keeps enough durable row/audit history for reconciliation. Add tests for funded active
  delete preserving zero escrow and correct bankroll/ledger deltas.

#### ⬜ 42. MTT `/sit` can create duplicate live tables for the same tournament
`/api/tournament/<id>/sit` only reuses `rec['game_id']` when that game is already warm in
`game_state_service`. If the durable tournament row has a `game_id` after restart/eviction, the route falls
through and builds a brand-new `tourney-*` game instead of returning the existing persisted game id for normal
`/api/game-state/<game_id>` cold-load. It also checks `rec['game_id']` before the tournament lock and never
re-checks inside, so concurrent sit/resume requests can both build live games. The last writer wins
`rec['game_id']`, leaving the earlier game orphaned but still persisted and capable of advancing the same
session if opened.
- **Files:** `flask_app/routes/tournament_routes.py:214-250` (`existing` check outside lock; in-memory-only
  reuse), `flask_app/services/tournament_registry.py:73-89,95-111,149-190` (durable rows rehydrate with
  `game_id`), `flask_app/handlers/tournament_game_builder.py:355-374` (new live game registration/persist),
  `flask_app/handlers/tournament_game_builder.py:470-498` (live boundary mutates shared tournament session).
- **Why confirmed/history:** the captain's log calls MTT cold-load the keystone and documents that restarting
  then cold-loading the existing MTT table should reattach and keep field rounds advancing
  (`docs/captains-log/tournaments/multi-table-tournament-engine.md:307-340`). Current `/sit` bypasses that
  persisted-game path unless the game is already in memory.
- **Impact:** a restart, eviction, double-click, or concurrent resume can create multiple live tables for one
  field. They can have divergent cards/stacks/controllers while sharing the same tournament session; a stale
  hand boundary can fold old state into an already-advanced field, corrupt standings, and strand orphan game
  rows.
- **Fix sketch:** under `registry.get_lock(tournament_id)`, re-read `rec['game_id']`; if present, return it
  regardless of warm-cache status (or explicitly cold-load that game) instead of rebuilding. Make build/id
  assignment single-flight and add restart plus double-sit regressions.

#### ⬜ 43. Legacy `/advance` and `/play-out` routes can mutate a human MTT behind its live table
The route docstring still describes the pre-bridge world where `advance` resolved the human's table with the
same resolver as AI tables. The live-table bridge now exists: `/sit` stores a `game_id`, and the game handler
advances the field from the real hand boundary. The old manual routes do not check that a live table exists.
`/advance` runs `session.play_round(...)` while the human is still in; `/play-out` runs `session.play_out()`
unconditionally. Both persist and may payout while the real `tourney-*` game still has older stacks/cards.
- **Files:** `flask_app/routes/tournament_routes.py:499-553` (manual advance/play-out mutate session),
  `flask_app/routes/tournament_routes.py:214-250` (live table recorded on sit),
  `flask_app/handlers/tournament_game_builder.py:426-498` (real live boundary owner),
  `docs/captains-log/tournaments/multi-table-tournament-engine.md:266-280` (bridge reuses one live game and
  the session owns elimination/completion).
- **Why confirmed:** current source rejects autonomous tournaments but not human tournaments with
  `rec['game_id']`. The routes remain callable after the bridge displaced their original purpose.
- **Impact:** a stray API call, stale frontend action, or admin/test script can fast-forward the field behind
  the player's live table. The next real boundary then reconciles stale table state into a mutated or completed
  tournament, risking duplicate busts, wrong ranks, and payout/finalization on inconsistent standings.
- **Fix sketch:** reject `/advance` when a non-terminal human tournament has `game_id`, and reject `/play-out`
  until `session.human_out` or no live game remains. Prefer deleting or admin-gating the obsolete routes if the
  live bridge fully owns human tournaments.

#### ⬜ 48. MTT cold-load forgets `tournament_multi_table` and routes the next hand through the single-table boundary
Fresh MTT live games stamp `tournament_multi_table=True`, but the `/api/game-state` cold-load reattach path only
restores the session/id/table/human/resolver fields. After a restart or TTL eviction, the next hand boundary sees a
`tournament_session` with no `tournament_multi_table` flag and enters the single-table completion branch instead of
the MTT boundary. The real MTT boundary, which advances/paces the rest of the field and reconciles the human table,
is gated on that same missing flag.
- **Files:** `flask_app/handlers/tournament_game_builder.py:337-353` (fresh MTT metadata),
  `flask_app/routes/game_routes.py:1202-1231` (cold-load restores MTT session metadata but not
  `tournament_multi_table`), `flask_app/handlers/game_handler.py:3478-3490` (single-table branch runs when the flag
  is absent), `flask_app/handlers/game_handler.py:3681-3693` (MTT boundary is skipped without the flag),
  `flask_app/handlers/single_table_tournament.py:81-98` and `tournament/session.py:118-137` (single-table fold only
  applies live-table stacks and increments rounds), `flask_app/handlers/tournament_handler.py:267-345` (MTT boundary
  folds the live result, advances AI tables, and reconciles/reseats the next table).
- **Why confirmed:** current cold-load code has an MTT-specific reattach block but omits the explicit routing flag
  that the game loop requires. Existing tests assert fresh `/sit` metadata and single-table routing, but do not cover
  an evicted `tourney-*` game resuming through `/api/game-state` and then crossing a hand boundary.
- **Impact:** a restarted Main Event can silently stop behaving like a multi-table tournament. Other tables do not
  advance, table balancing/relocation is skipped, display-name seats may fail to update the id-keyed field, and the
  human's event can drift into frozen stacks, wrong elimination timing, or a one-table-style completion path.
- **Fix sketch:** in the MTT cold-load reattach block, restore `tournament_multi_table=True` plus the other builder
  metadata that is derivable from the row/session (`tournament_is_persona_field`, `tournament_sandbox_id`, and the
  display-name to field-id bridge). Add a regression that evicts a `tourney-*` game, cold-loads it via
  `/api/game-state`, finishes one hand, and asserts `tournament_hand_boundary` rather than `single_table_hand_boundary`
  runs.

#### ⬜ 49. Tournament payout credits bankrolls even when the authoritative payout ledger row failed
Tournament payout comments treat `tournament_payout` ledger rows as the authority for whether a finisher was paid:
reconcile reads paid amounts from those rows and only pays the delta. But `record_tournament_payout()` is just a
best-effort transfer helper; `record_transfer()` swallows repository failures and returns `None`. The live payout and
reconcile paths ignore that return value and still save the human/AI bankroll cache in the same block.
- **Files:** `core/economy/ledger.py:482-573` (transfer rows swallow failures), `core/economy/ledger.py:849-880`
  (tournament payout uses that helper), `flask_app/services/tournament_economy_service.py:284-316` and
  `flask_app/services/tournament_economy_service.py:318-348` (live payout credits cache after unchecked row),
  `flask_app/services/tournament_economy_service.py:460-478,485-505` (reconcile trusts ledger totals, then repeats
  the unchecked row/cache write).
- **Why confirmed:** the helper contract says failures return `None`; payout code neither checks nor raises before
  writing the bankroll int. That breaks the row-authoritative invariant the surrounding comments depend on.
- **Impact:** a dropped `tournament_payout` row can still credit a bankroll. The next reconcile sees no authoritative
  paid row and can pay the same finisher again, or an operator/audit sees escrow and bankroll caches disagree with no
  recoverable source of truth.
- **Fix sketch:** make tournament payout transfers strict inside `chip_unit_of_work`: require a non-`None` ledger row
  before saving the bankroll cache, or add a strict ledger helper that raises on transfer failure. Add tests where the
  ledger repo rejects a payout row and assert no bankroll credit lands.

#### ⬜ 50. Live tournament payout marks `complete` even when escrow still has a residual balance
After paying finishers, live tournament payout skims rake, sweeps the remaining escrow, then checks the final escrow
balance. If the balance is non-zero it only logs an error and still advances `payout_status` to `complete`. The stuck
payout recovery path only scans `payout_status='in_progress'`, so this terminal status hides the residual from the
watchdog and the admin reconcile route.
- **Files:** `flask_app/services/tournament_economy_service.py:351-380` (rake/sweep/final-balance check logs only),
  `flask_app/services/tournament_economy_service.py:398` (still sets `complete`),
  `poker/repositories/tournament_session_repository.py:242-256` (stuck payout scan only lists `in_progress`).
- **Why confirmed:** no branch changes status or raises when `final_balance != 0`; the function always reaches the
  terminal payout-status write after the error log.
- **Impact:** failed rake/return ledger writes can strand tournament escrow while marking the payout non-retryable.
  The payout card looks complete, but ledger balances still carry tournament chips.
- **Fix sketch:** treat non-zero final escrow as a retryable payout failure: leave `payout_status='in_progress'` (or a
  dedicated error state scanned by reconcile), and only mark `complete` after the balance is exactly zero.

#### ⬜ 51. Stuck-payout reconcile can use the owner's current sandbox instead of the tournament's original escrow sandbox
Tournament escrow rows are written with the sandbox id that existed at registration/payout time, but the `tournaments`
table does not persist that sandbox id. Both ticker and manual payout reconcile derive a sandbox from the owner at
reconcile time. If the owner's default sandbox changes, is archived, or is recreated between registration and
reconcile, recovery looks at and writes a different set of scoped ledger accounts.
- **Files:** `flask_app/services/tournament_economy_service.py:139-153` (funding writes escrow rows with
  `sandbox_id`), `poker/repositories/schema_manager.py:1383-1397` (tournaments schema has economy fields but no
  sandbox id), `flask_app/services/tournament_ticker.py:309-324` (ticker reconcile calls `resolve_sandbox(owner)`),
  `flask_app/routes/tournament_routes.py:579-586` (admin reconcile resolves the current default sandbox).
- **Why confirmed:** the durable tournament row has no original-sandbox field even though every escrow ledger helper
  is sandbox-scoped, so recovery necessarily recomputes a potentially different value.
- **Impact:** a stuck payout can credit prizes or sweep escrow in the wrong sandbox while the original tournament
  escrow remains stranded. This is especially risky around save-file/sandbox archival or future multi-sandbox users.
- **Fix sketch:** persist `sandbox_id` on tournament rows at creation/funding and use it for all payout/reconcile
  calls. Backfill existing rows from linked invites or the first tournament escrow ledger row.

#### ⬜ 52. Live MTT completion pays before durable terminal session/status persistence
The live MTT boundary applies tournament payout immediately when the in-memory outcome is complete, then finalizes
stats, folds observations, and only afterward persists the tournament session/status. That persist is best-effort and
swallows failures; the row's `status='complete'` is derived only during registry persistence. A crash or DB write
failure after payout but before the boundary persist leaves the payout terminal while the tournament row can remain
`active` with stale session JSON.
- **Files:** `flask_app/handlers/tournament_game_builder.py:479-497` (payout/finalize happen before boundary persist),
  `flask_app/handlers/tournament_game_builder.py:565-581` (boundary persist is best-effort),
  `flask_app/services/tournament_registry.py:158-176` (status derives from session during persist),
  `flask_app/services/tournament_economy_service.py:398` (payout status can already be complete).
- **Why confirmed:** source ordering pays from the completed in-memory session before the durable row records that
  completed field/status, and the later persist exception path intentionally does not fail the live game.
- **Impact:** presence/resume/reconcile can see an `active` tournament with `payout_status='complete'`: the player may
  still look tournament-bound, the event can be resumable from stale standings, and payout retry is suppressed.
- **Fix sketch:** durably persist the terminal session/status before claiming payout, or make completion+payout a
  coordinated state machine with recovery for `payout_status='complete' AND status='active'`.


#### ⬜ 74. Stuck `in_progress` payouts can still be marked complete at the registry layer
Autonomous settlement deliberately leaves `status='active'` when payout is wedged at `payout_status='in_progress'`, so
presence exclusion and reconcile visibility remain intact until the watchdog finishes. The ticker wrapper then calls
`registry.persist(tid)`, whose `persist_session()` derives `status='complete'` solely from `session.is_complete()` and
can overwrite that active hold. The live MTT boundary has the same shape: payout is swallowed if it throws after
claiming `in_progress`, then `_persist_boundary()` persists the complete session regardless.
- **Files:** `flask_app/services/tournament_spawn.py:468-494` (intentional active hold for wedged payouts),
  `flask_app/services/tournament_ticker.py:202-220` (post-advance registry persist),
  `flask_app/services/tournament_registry.py:156-170` (status derived only from session completion),
  `flask_app/handlers/tournament_game_builder.py:479-497,524-562,565-581` (live payout swallowed before boundary
  persist), `flask_app/services/tournament_economy_service.py:400-404,567-572` (in-progress is reconcilable; reconcile
  later releases status).
- **Why confirmed:** current persistence ignores `payout_status` even though settlement comments require status to stay
  active until a wedged payout is reconciled. This is not #52; #52 is `payout_status='complete'` with stale active
  tournament status, while this is `payout_status='in_progress'` with premature complete status.
- **Impact:** payout remains reconcilable, but active-tournament and active-participant presence guards can release the
  field early. Entrants can be seated elsewhere while tournament escrow is still mid-distribution, undermining the
  single-presence and payout recovery invariants.
- **Fix sketch:** make every terminal `persist_session()` preserve active status while `payout_status='in_progress'`,
  or teach registry persistence to derive status from both session completion and payout state. Add autonomous ticker
  and live-boundary regressions where payout throws after `claim_payout()`.

#### ⬜ 53. Cash entry paths do not block players already active in a tournament
Main Event invite acceptance handles the cash-to-tournament direction by forcing or requiring a cash leave first. The
reverse direction is not guarded: cash `/start`, cash `/sit`, and sponsor-and-sit only check for an existing active
cash game. They do not check whether the same owner already has an active tournament row/live table.
- **Files:** `flask_app/routes/tournament_routes.py:411-432` (tournament accept handles only cash departure),
  `flask_app/routes/cash_routes.py:1106-1114` (cash start duplicate guard is cash-only),
  `flask_app/routes/cash_routes.py:1268-1276` (cash sit duplicate guard is cash-only),
  `flask_app/routes/cash_routes.py:2024-2031` (sponsor-and-sit duplicate guard is cash-only),
  `poker/repositories/tournament_session_repository.py:118-135` (active tournament rows are queryable by owner).
- **Why confirmed:** the cash routes proceed to affordability checks, seat claims, game creation, stake creation, and
  bankroll debits after only `_find_active_cash_game_id(owner_id)`; no active-tournament check appears in those entry
  guards.
- **Impact:** a player can be seated in a Main Event and a cash table at the same time, splitting bankroll/presence
  and undermining the single-presence assumptions behind cash departure, tournament-bound seat exclusion, and lobby
  state.
- **Fix sketch:** add a backend active-tournament guard to every cash entry path under the same sandbox lock used for
  seat mutation, before reservations, game creation, staking, or bankroll debit.

#### ⬜ 89. Tournament entrants age out of cash-seat exclusion while the tournament remains resumable
The cash lobby excludes tournament participants by calling `active_participant_pids(owner_id)`, but that repository
query deliberately filters to rows updated in the last six hours. The tournament resume path has no matching age
cutoff: `find_active_for_owner()` and the registry can still rehydrate an active tournament row after that window.
After a long-running or paused MTT sits idle past the exclusion cutoff, its AI entrants can become eligible for cash
seating even though the tournament remains active and resumable.
- **Files:** `poker/repositories/tournament_session_repository.py:30-35,112-175` (six-hour participant cutoff vs
  active owner lookup), `cash_mode/lobby.py:1223-1237` (cash off-grid exclusion consumes the expiring participant
  set), `flask_app/services/tournament_registry.py:114-135` and `flask_app/routes/tournament_routes.py:171-181`
  (active tournament resume has no age cutoff).
- **Why confirmed/history:** the current repository has two different active-tournament notions: resumable rows can
  live indefinitely, while cash-seat exclusion sees only recently touched participants. This is distinct from #53,
  which lets the human enter cash while a tournament is active; this one releases tournament field entrants after
  a clock window.
- **Impact:** tournament-bound personas can be seated into cash games at the same time they remain in a resumable MTT,
  breaking the single-presence model and any tournament/cash custody assumptions tied to those AI identities.
- **Fix sketch:** make cash exclusion consult durable active tournament membership without a short age cutoff, or
  explicitly mark abandoned tournaments terminal before releasing their entrants. Add a regression for an active
  tournament whose `updated_at` is older than `EXCLUSION_MAX_AGE_HOURS` but still resumes through the registry.


### S2 (cash presence / session authority)

#### ⬜ 71. One-cash-session entry guard is non-atomic across cash entry routes
`/api/cash/start`, `/api/cash/sit`, and `/api/cash/sponsor-and-sit` each check `_find_active_cash_game_id()` before
later seat/game/session writes. The durable `cash_sessions` indexes are non-unique, and `CashSessionRepository.create()`
explicitly relies on callers to avoid double-inserting per owner, so two concurrent entry requests can both pass the
pre-write guard and create multiple active cash sessions for one owner.
- **Files:** `flask_app/routes/cash_routes.py:1106,1268,2024` (pre-write active-session checks),
  `poker/repositories/schema_manager.py:6528,6919` (non-unique active/blocking indexes),
  `poker/repositories/cash_session_repository.py:101` (caller-enforced single-session comment).
- **Why confirmed/history:** current code has no durable uniqueness guard and no single owner-level cash-entry
  transaction. This is distinct from #53's cash-vs-tournament presence gap and #40's reseat/live-fill race.
- **Impact:** one player can become present in multiple cash sessions/tables, splitting bankroll custody and making
  leave/resume/seat-repair logic choose between competing active sessions.
- **Fix sketch:** introduce an owner/sandbox entry claim under the cash presence lock and back it with a durable
  active-session uniqueness constraint or transactional compare-and-insert. Add concurrent `/start` vs `/sit` and
  `/sit` vs sponsor-sit regressions.

#### ⬜ 88. Generic game delete can bypass cash leave and strand a blocking cash session
The saved-game list hides `cash-*` games because cash games have a dedicated leave/resume lifecycle, but the generic
`DELETE /api/game/<game_id>` and `/api/end_game/<game_id>` routes do not reject cash ids. They delete warm game state
and the `games` row directly. `GameRepository.delete_game()` clears live game tables but does not settle stakes,
finalize `cash_sessions`, cash out stacks, or release table seats. If a stale client or direct request deletes a cash
game through the generic endpoint, `_find_active_cash_game_id()` can still return the blocking `cash_sessions` row and
`/api/cash/state` can redirect to a missing game id while boot cleanup skips blocking sessions by design.
- **Files:** `flask_app/routes/game_routes.py:531-567` (cash games hidden from generic list),
  `flask_app/routes/game_routes.py:2343-2355,2362-2374` (generic delete/end-game routes call direct deletion),
  `poker/repositories/game_repository.py:253-263` (game delete does not touch cash session/custody tables),
  `flask_app/routes/cash_routes.py:150-184,6688-6710` (blocking cash-session lookup and redirect),
  `cash_mode/lobby.py:4368-4375` and `poker/repositories/cash_session_repository.py:183-190` (blocking sessions are
  deliberately preserved).
- **Why confirmed/history:** current cash code treats cash departure as effectful settlement, but the generic game
  deletion surface remains callable on cash ids. This is not #70's ghost-leave branch; here the bug is bypassing the
  cash leave path entirely.
- **Impact:** an active cash session can remain blocking with no matching game row, leaving the player unable to
  cleanly resume or leave and leaving bankroll/stake/table state outside the cash settlement path.
- **Fix sketch:** reject `cash-*` ids from generic delete/end-game routes and route them through `/api/cash/leave` or a
  cash-specific abandon flow that performs settlement first. Add route regressions asserting generic deletion returns
  400/409 for active cash games and leaves session/custody state unchanged.

### S2 (cash live-fill concurrency)

#### ⬜ 40. Cash `reseat` live-fill mutates game/table/bankroll state outside the locks used by sibling flows
`rebuy()` and `top_up()` both take the per-game lock before re-reading and mutating game state because
`progress_game()` mutates the same state under that lock. `reseat()` is another between-hands mutation of the
same live cash game, but it reads `game_data` without the game lock, prunes `state_machine.game_state`, debits
AI bankrolls, saves table seats, calls `_seat_freshly_filled_ais()` to append new players/controllers, writes
`game_state_service.set_game()`, then calls `progress_game()`. It also saves the cash table row without the
sandbox lock that the normal sit/live-fill paths use around read-check-save.
- **Files:** `flask_app/routes/cash_routes.py:2417-2422` and
  `flask_app/routes/cash_routes.py:5192-5197` (sibling lock invariant),
  `flask_app/routes/cash_routes.py:2558-2670` (reseat read/mutate/write path),
  `flask_app/handlers/game_handler.py:2424-2428,2464-2498,2506-2518` (`_seat_freshly_filled_ais()` mutates
  players/controllers/game_data and persists the game).
- **Why confirmed:** there is no `get_game_lock(game_id)` or `get_sandbox_lock(sandbox_id)` around the reseat
  critical section, while adjacent routes explain that these exact mutations need serialization.
- **Impact:** a double-click, concurrent leave/top-up/rebuy, or a progress/ticker interleave can duplicate AI
  bankroll debits, persist table seats without matching live players, append players that a later stale write
  drops, or resume a game the owner is simultaneously leaving. This is the same class of last-write-wins state
  drift as #29, but on cash live-fill and chip custody surfaces.
- **Fix sketch:** acquire the per-game lock, re-read `game_data`, re-check `cash_solo_paused` and phase, and do
  all state-machine/game-data mutations before releasing it. Wrap table load/save and AI bankroll/ledger seat
  funding in the sandbox/table critical section or a repository transaction. Release before calling
  `progress_game()`. Add double-reseat and reseat-vs-leave regressions asserting no duplicate AI debit and
  table/game rosters match.

### S2 (experiments / replay execution)

#### ⬜ 41. Replay experiment launch path is dead: new rows start as `running`, and the route calls the runner with the wrong signature
Replay experiments are supposed to be created, then launched through `POST /api/replay-experiments/<id>/launch`.
The current path fails before it can run. `ReplayExperimentRepository.create_replay_experiment()` inserts into
the shared `experiments` table without an explicit `status`, and that table defaults `status` to `running`.
The launch route rejects any experiment whose status is already `running`, so a freshly created replay experiment
is immediately considered "already running" even though no runner thread was started. If the status is manually
changed away from `running`, the route still calls `run_replay_experiment_async()` with `persistence=...` and no
`db_path`, but the function signature is `(experiment_id, replay_experiment_repo, db_path, ...)`, so launch raises
before starting the background thread.
- **Files:** `poker/repositories/replay_experiment_repository.py:57-73` (create insert omits status),
  `poker/repositories/schema_manager.py:1442-1453` (shared experiments table default `status='running'`),
  `flask_app/routes/replay_experiment_routes.py:181-201` (rejects running, then calls async runner with
  `persistence=` and no `db_path`), `experiments/run_replay_experiment.py:489-514` (actual async signature).
- **Why confirmed:** source and existing tests document the generic experiment default as `running`; the replay
  launch route has no replay-specific pending state, and the keyword argument does not match the imported
  function signature.
- **Impact:** the replay-experiment UI can create experiments but cannot execute them through the API. Replay
  comparisons, prompt-variant validation, and captured-decision regression workflows silently stall at zero
  results or return a launch error.
- **Fix sketch:** create replay experiments with an explicit `pending`/`created` status, have launch accept only
  launchable states and atomically claim them, and call `run_replay_experiment_async(experiment_id,
  extensions.replay_experiment_repo, extensions.persistence_db_path, parallel=..., max_workers=...)`. Add a
  route-level test that creates a replay experiment, launches it with the runner mocked, and asserts the thread
  call receives the repo and DB path.

### S2 (tournament completion durability)

#### ⬜ 77. Prompt-capture retention leaves replay experiments counting orphaned captures
The retention sweep deletes old `prompt_captures`, while replay experiment link/result tables rely on
`REFERENCES prompt_captures(id) ON DELETE CASCADE`. Repository connections never enable `PRAGMA foreign_keys`, so the
cascade does not fire. After retention deletes a linked capture, experiment metadata still counts
`replay_experiment_captures` and `replay_results` rows, but the runner's capture load joins back to
`prompt_captures` and returns no usable captures for the same experiment.
- **Files:** `poker/repositories/prompt_capture_repository.py:602-630` (retention deletes prompt captures),
  `flask_app/services/retention_service.py:35-48` (scheduled sweep invokes it),
  `poker/repositories/schema_manager.py:1578-1628` (replay links/results depend on cascade),
  `poker/repositories/base_repository.py:180-185` (connections configure WAL/busy/synchronous but not foreign keys),
  `poker/repositories/replay_experiment_repository.py:287-310,452-475,512-543` (counts links but loads via join),
  `experiments/run_replay_experiment.py:112-117` (runner fails when joined captures are empty).
- **Why confirmed:** SQLite does not enforce foreign-key actions unless enabled per connection; the repo connection
  setup omits that pragma. Existing replay tests cover normal linked captures, not retention deletion.
- **Impact:** finite prompt-retention windows can silently corrupt existing replay experiments: admin listings still
  show capture/result counts, but reruns fail with `No captures linked to this experiment`, and result pages can count
  rows that no longer have source prompts.
- **Fix sketch:** enable foreign keys on repository connections and add explicit cleanup/pruning for replay links and
  results before enabling retention on production data. Alternatively exempt captures linked to replay experiments from
  retention unless the experiment is archived/deleted. Add a regression that deleting a captured prompt updates replay
  counts and runner behavior consistently.

#### ⬜ 32. Tournament completion finalization is guarded only in memory; cold-load can double-count career stats
`finalize_tournament()` claims idempotency via `game_data['tournament_finalized']`, but that flag is an
in-memory dict field, not part of the persisted tournament session/result. Cold-load reconstructs MTT
`game_data` from the session row and does not restore the flag. A re-entered terminal boundary after restart,
TTL eviction, or a stale `progress_game` can call `finalize_tournament()` again. The result row itself is
`INSERT OR REPLACE`, but `update_career_stats()` increments `games_played`, wins, and eliminations every time,
so the human career totals can be inflated even though the tournament result appears idempotent. The terminal
MTT boundary also applies payout/finalization before `_persist_boundary()` saves the advanced terminal session,
leaving a crash window where side effects are durable but `session_json` is stale.
- **Files:** `flask_app/handlers/tournament_completion.py:170-231` (in-memory guard + stats update),
  `flask_app/routes/game_routes.py:1219-1231` (MTT cold-load reattach omits the guard),
  `flask_app/handlers/tournament_game_builder.py:479-497` (payout/finalize before boundary persist),
  `poker/repositories/tournament_repository.py:30` (result replace),
  `poker/repositories/tournament_repository.py:176` (career stats increment).
- **Why confirmed:** current source has no durable completion marker check before career-stat increment;
  existing idempotency test calls `finalize_tournament()` twice on the same dict, which does not cover
  cold-load/restart re-entry.
- **Fix sketch:** persist a durable completion/finalization marker keyed by `game_id`/tournament id and make
  `update_career_stats()` conditional on first successful finalization. Persist terminal session state before
  side effects or make the recovery path reconcile from the durable result/payout status.


#### ⬜ 67. Single-table tournament cold-load can reset standings when session persistence misses
Ordinary games now build a one-table `TournamentSession`, but all durable writes of that session are best-effort:
game creation and every hand boundary swallow `persist_single_session()` failures. On cold-load, if the `tournaments`
row is missing or still a lightweight envelope without `field`, the route builds a fresh session from the current
live table snapshot. That fallback guesses `starting_stack` from live stacks even though the builder explicitly
requires the original buy-in because live stacks differ after blinds; the fresh `TournamentField` then marks every
entry active at `starting_stack`, losing prior eliminations/rounds/hand counter.
- **Files:** `flask_app/routes/game_routes.py:969-1047` (missing row/envelope fallback builds fresh session),
  `flask_app/routes/game_routes.py:1914-1995` (new-game session persist is best-effort),
  `flask_app/handlers/single_table_tournament.py:40-60,100-109` (buy-in requirement and per-hand best-effort persist),
  `flask_app/services/tournament_registry.py:244-263` (persist helper swallows failures),
  `tournament/field.py:79-81` and `tournament/session.py:128-137` (fresh field/all-active elimination fold).
- **Why confirmed/history:** the multi-table tournament captain's log calls cold-load the keystone and records that
  using live `player.stack` as the buy-in was already caught as a conservation bug. Current cold-load reintroduces
  that guess specifically when durable single-table session state is absent.
- **Impact:** after a DB write failure, restart, or stale envelope row, an in-progress single-table tournament can
  forget standings and re-emit or mis-rank eliminations. Completion, career stats, and payout/result display can
  disagree with the live chip state.
- **Fix sketch:** make single-table session persistence required at game creation and hand boundaries, or store enough
  session state in the saved game row to recover without synthesis. On cold-load, only synthesize fresh sessions for
  explicit legacy games that cannot have prior session-backed eliminations. Add a missing-row-after-elimination
  regression.

### S3 (API / chip-state integrity)

#### ⬜ 28. REST `raise` accepts fractional chip amounts and writes floats into game state
The socket action path casts `amount` to `int`, and the schema/docs consistently model stacks, bets,
pots, ledgers, and decision-capture amounts as integer chips. The REST action path uses the raw JSON
value and validation explicitly accepts `float`; `BettingContext.validate_and_sanitize()` does not cast;
`place_bet()` then subtracts/adds that value directly. A request such as `amount=100.5` produces float
`Player.stack`, `Player.bet`, and `pot['total']` values in the persisted game JSON. That can leak into
integer-affinity DB columns and chip-custody/cash accounting surfaces even though normal UI controls
probably send integers.
- **Files:** `flask_app/routes/game_routes.py:2018` (raw JSON amount),
  `flask_app/routes/game_routes.py:2529` (socket path casts to int),
  `flask_app/validation.py:30` (float accepted),
  `poker/betting_context.py:60-82` (no integer coercion),
  `poker/poker_game.py:475-484` (float arithmetic applied to stack/bet/pot).
- **Why confirmed:** a focused local-code check of `player_raise(..., 100.5)` returned `float 899.5`,
  `float 100.5`, `float 100.5` for stack, bet, and pot total.
- **Fix sketch:** make REST parse/cast the amount the same way the socket path does, or tighten
  `validate_player_action()` to require non-bool `int` chip amounts for raises and reject floats.
  Add REST validation coverage for fractional, string, negative, and bool amounts.

### S3 (CI / deploy reliability)

#### ⬜ 30. GHCR build workflow still uses Docker actions flagged for Node.js 20 deprecation
GitHub Actions is warning on the **Build & Push Images (GHCR)** job that Node.js 20 JavaScript actions
are deprecated. Per the annotation supplied from CI, actions will be forced to Node.js 24 by default on
**June 16, 2026**, and Node.js 20 will be removed from runners on **September 16, 2026**. The deploy
workflow still uses the older majors in the GHCR build/push path: `docker/setup-buildx-action@v3`,
`docker/login-action@v3`, and `docker/build-push-action@v6`, and the deploy job has a second
`docker/login-action@v3` before pulling the images. There is already a sibling workflow using
newer Docker action majors (`playwright-base.yml` has `docker/login-action@v4` and
`docker/build-push-action@v7`), so this is likely a straightforward workflow-version drift rather than a
code issue. Left alone, the deploy pipeline may fail or behave differently once GitHub flips the default.
- **Files:** `.github/workflows/deploy.yml:202,205,212,225,295`; comparison
  `.github/workflows/playwright-base.yml:22,29`.
- **Why confirmed:** local workflow versions match the CI annotation's deprecated actions list.
- **Fix sketch:** update the deploy workflow to Node 24-compatible Docker action majors where available
  (at minimum align login/build-push with the newer sibling workflow), then run/observe a
  `workflow_dispatch` GHCR smoke test. If action updates are not immediately possible, set
  `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true` in the workflow to opt in early and catch failures before
  June 16, 2026.

#### ⬜ 100. Production deploy does not enforce the schema-completeness gate after migrations
The captain-log/prod plan fixed a real drift class: a DB stamped at a high `schema_version` can still miss
a migration that was renumbered below its current max. The guard exists (`scripts/schema_completeness_check.py`)
and was used manually on the launch-day prod copy, but the recurring GitHub deploy path does not run it.
The workflow starts the new containers, waits, and then runs only the avatar migration command before `/health`.
Normal schema migrations happen earlier when Flask startup calls `create_repos() -> SchemaManager.ensure_schema()`,
but `ensure_schema()` only walks versions `current_version + 1..SCHEMA_VERSION` and returns when the DB is already
at head; it does not compare the live DB to the canonical fresh schema. A renumbered/skipped artifact can therefore
survive app startup and still pass the deploy health check until a runtime path touches the missing column/table/index.
- **Files:** `.github/workflows/deploy.yml:301-319` (containers start, then only
  `migrate_avatars_to_db` runs before health), `flask_app/extensions.py:397-398` and
  `poker/repositories/__init__.py:45-53` (startup persistence calls `ensure_schema()`),
  `poker/repositories/schema_manager.py:376-386,1749-1754,2440-2448` (init + version-gated
  migration walk), `scripts/schema_completeness_check.py:2-19,70-133` (the missing-object gate),
  `docs/plans/PROD_MERGE_PLAN.md:65-68,96-102` (gate marked required), and
  `docs/captains-log/development/launch-day-cutover-and-four-wrong-turns.md:20-24` (launch-day
  dry-run plus completeness gate passed manually).
- **Why confirmed/history:** the schema-drift captain's log records the exact failure mode: the dev DB was stamped
  v148 while missing the v139 `prestige_snapshots.entity_kind` column and v139/v140 indexes because those migrations
  were renumbered below a DB that had already advanced past them. Current deploy automation has backup and startup
  migration steps, but no `schema_completeness_check.py` or `migration_dryrun.py` enforcement in the workflow.
- **Impact:** a future branch merge or production-only lineage can deploy green with a head-version DB that is still
  missing schema objects. `/health` may stay clean because it does not exercise every cash/tournament/renown/dossier
  path, so the first visible symptom can be a runtime crash or silent fan-out failure under real traffic.
- **Fix sketch:** add a workflow gate after schema migration and before health/cleanup:
  `docker compose -f docker-compose.prod.yml exec -T backend python scripts/schema_completeness_check.py --db /app/data/poker_games.db`.
  For large schema jumps, run `scripts/migration_dryrun.py` against the WAL-safe backup copy before touching live prod,
  then run the completeness check on the migrated live DB and fail/rollback on any missing item. Keep the CI contiguity
  test as the registry-shape guard, but treat the completeness check as the live-DB guard.

#### ⬜ 107. Deploy avatar migration can silently no-op because deploy excludes its default source icons
Both manual and GitHub deploy paths exclude `generated_images/`, then run `python -m poker.migrations.migrate_avatars_to_db`.
That migration defaults its avatar source to `generated_images/grid/icons`; when the directory is missing, `import_avatars()`
prints an error and returns an error dict, but `main()` ignores the return value, runs verification, prints
`Migration Complete`, and exits zero. The current workspace also has no `generated_images` directory, so the default source
is not guaranteed by the repo.
- **Files:** `deploy.sh:21-26,61-62` (manual deploy excludes generated images, then runs avatar migration),
  `.github/workflows/deploy.yml:261-273,316-319` (GitHub deploy does the same),
  `poker/migrations/migrate_avatars_to_db.py:33-35,68-82,220-226` (default source dir, missing-dir error dict,
  ignored return/zero completion), `generated_images/` (absent from current checkout).
- **Why confirmed:** deploy automation calls a migration whose default input is explicitly excluded from the deploy
  payload. The migration treats missing inputs as report data rather than process failure, so CI/deploy health can stay
  green while no avatar rows were imported.
- **Impact:** a fresh or restored production DB can deploy successfully with missing `avatar_images` rows. Runtime then
  falls back to missing-avatar behavior or regeneration paths even though the deploy log claims database migrations ran.
- **Fix sketch:** ship or mount an explicit avatar asset bundle and pass `--images-dir`, or make missing source icons a
  nonzero deploy failure when avatar import is part of deploy. Add a post-migration assertion for the expected
  `avatar_images` count before health succeeds.

#### ⬜ 101. Renown/prestige feature flags accept contradictory states that silently disable dependent mechanics
The launch-day captain's log records an ops miss where `PRESTIGE_SEEKING_ENABLED` was armed but inert because the
renown-v2 data flags were not also enabled. `docker-compose.prod.yml` now defaults all three launch flags on together,
but the Python flags still do not enforce the dependency graph they document: `RENOWN_V2_PERSIST_AI` says it implies
`RENOWN_V2_ENABLED`, and `PRESTIGE_SEEKING_ENABLED` says it implies `RENOWN_V2_PERSIST_AI`, yet all three are independent
`_env_flag(...)` reads. If an explicit env override sets prestige seeking on while either renown dependency is off, the
ticker writes no AI percentile rows and the lobby loads an empty percentile map; the marquee/status-seeking term then
degrades to zero while the operator-visible feature flag still says the mechanic is enabled.
- **Files:** `docker-compose.prod.yml:91-96` (comments/defaults say all three must be on together),
  `cash_mode/economy_flags.py:401,413,422` (independent booleans despite implication comments),
  `flask_app/services/ticker_service.py:695-709,734-761,816-848` (v2 overlay requires `RENOWN_V2_ENABLED`; AI fan-out
  requires `RENOWN_V2_PERSIST_AI`), `cash_mode/lobby.py:2091-2105` (prestige seeking checks only its own flag and then
  tolerates no percentile data), `poker/repositories/prestige_snapshots_repository.py:297-327` (empty dict when no v2
  percentile rows exist), and `cash_mode/attractiveness.py:99-105` (marquee term intentionally degrades to 0 with no
  renown data).
- **Why confirmed/history:** the current code makes the exact launch-day wrong turn possible again under an explicit
  override or dev env: the dependent flag can be true while its data-producing prerequisites are false. Existing tests
  cover the no-data/inert paths as valid behavior, but there is no startup validation or normalization test for invalid
  flag combinations.
- **Impact:** prod or dev can appear to have prestige-seeking movement enabled while the table-selection signal is zero.
  That makes flag audits and release verification misleading, and can hide a disabled reputation mechanic until someone
  measures movement outcomes rather than checking env values.
- **Fix sketch:** add an economy flag dependency validator at startup (or normalize effective flags in
  `economy_flags.py`). Prefer failing loudly in production when `PRESTIGE_SEEKING_ENABLED` is true but
  `RENOWN_V2_PERSIST_AI`/`RENOWN_V2_ENABLED` are false, and when `RENOWN_V2_PERSIST_AI` is true but
  `RENOWN_V2_ENABLED` is false. Emit the effective flag state at boot and add tests for the invalid combinations.


#### ⬜ 86. Hetzner eval runner can rsync local `.env` secrets to a disposable external box
The eval-runner captain's log records that an off-box Hetzner sweep was hard-blocked because the runbook
would have shipped the working tree's real `.env` API keys, and explicitly filed that the runbook should
exclude `.env`. The current `docs/EVAL_RUNNER.md` rsync command excludes `.git`, `node_modules`, DB files,
Python caches, outputs, and `/data/`, but still does not exclude `.env` or other local secret env files.
Following the documented command from a normal dev checkout can copy provider keys to a fresh external
host that is meant to be torn down after the run.
- **Files:** `docs/EVAL_RUNNER.md:53-59` (rsync exclude list omits env/secrets),
  `docs/captains-log/renown/renown-v2-ai-wiring.md:185-191` (history note: `.env` leak was already caught
  once and filed).
- **Impact:** a performance/eval workflow can exfiltrate OpenAI/DeepSeek/Runware/etc. credentials to a
  temporary third-party VM, and `--delete` keeps the remote mirror authoritative. Teardown reduces runtime
  exposure but does not undo the secret copy if the VM is compromised, snapshotted, logged, or not deleted.
- **Fix sketch:** add explicit `.env`, `.env.*`, and other local-secret excludes to the eval runner rsync
  command while preserving `.env.example` if needed, then create a dummy env file on the box with non-secret
  placeholder keys for import-only provider construction. Add a short preflight note to verify the rsync
  dry-run does not include secret files.

### S3 (tournament / psychology persistence)

#### ⬜ 31. Cash-world MTT completion writes persona psychology to the wrong key, or skips it after cold-load
The cash-world tournament completion path is intended to flush real personas' evolved tournament mood back
to `ai_bankroll_state.emotional_state_json`. Fresh MTT games store `tournament_is_persona_field` and
`tournament_sandbox_id`, but cold-load reattach restores only session/id/table/resolver fields, so a
restarted persona-field MTT can complete without flushing any mood. On warm completion, the flush loop is
also keyed incorrectly: `game_data['ai_controllers']` is built as `display_name -> controller`, but
`finalize_tournament()` iterates that map as `for pid, ctrl` and passes the display name into
`flush_persona_psychology()`, whose repository write key is `(personality_id, sandbox_id)`. If display name
and personality id differ, the write creates/updates a pseudo-persona row with `chips=0` instead of the real
persona's emotional-state blob. The existing completion test is false-green because it uses `napoleon` as
both display key and pid.
- **Files:** `flask_app/handlers/tournament_game_builder.py:220` (controllers keyed by display),
  `flask_app/handlers/tournament_game_builder.py:346-353` (fresh persona-field metadata),
  `flask_app/routes/game_routes.py:1219-1231` (cold-load reattach omits that metadata),
  `flask_app/handlers/tournament_completion.py:219-228` (flush gate + display key passed as pid),
  `cash_mode/psychology_persistence.py:144-158` and
  `poker/repositories/bankroll_repository.py:231-263` (writes by personality_id, inserts if missing).
- **Why confirmed:** current source establishes both key shape and cold-load metadata loss; repo insert
  behavior makes the wrong-key case silent rather than loud.
- **Fix sketch:** persist/rehydrate `tournament_is_persona_field` and `tournament_sandbox_id` for MTT
  game data, and flush by stable seat/personality id (for example from `player.personality_id`, `seat_id`,
  or the session table entry), not the display-name controller key. Add regressions where display name != pid
  and where a persona-field MTT is cold-loaded before completion.



### S2 (auth / account linking)

#### ⬜ 98. Guest-to-Google linking strands cash-mode career state on the guest identity
Google OAuth preserves `oauth_guest_id` during login, then calls `transfer_game_ownership()` before replacing
the Flask session with the Google user id. The transfer helper says it moves all owner references, but the
transaction only updates `games`, `api_usage`, `prompt_captures`, `player_career_stats`,
`tournament_standings`, and `tournament_results`. It does not move the newer durable cash/career surfaces
keyed by `owner_id`, `user_id`, or `player:<owner_id>`: the default sandbox, player bankroll row, ledger
account, cash sessions, active/carry stake rows, presence rows, holdings/prestige snapshots, dossier unlocks,
user preferences/avatar, experiment chats/presets/reference images, coach progress/profile/tips, tournament
session rows, and outstanding tournament invites.
- **Files:** `poker/auth.py:287-290,357-391,409-420` (guest id captured, transfer called, session switched to
  Google id), `poker/repositories/user_repository.py:141-228` (claimed all-owner transfer updates only six
  table families), `flask_app/services/sandbox_resolver.py:69-86` and
  `poker/repositories/sandbox_repository.py:133-164` (cash default sandbox lookup is by current owner id and
  auto-creates when none exists), `flask_app/routes/cash_routes.py:99-125,486-516` and
  `poker/repositories/bankroll_repository.py:715-733` (cash bankroll lookup/seeding is by current owner id),
  `core/economy/ledger.py:245-248,354-367` (ledger balance is keyed as `player:<owner_id>`),
  `poker/repositories/schema_manager.py:843-847,1248-1263,1489-1504,1539-1552,1701-1735,5827-5853,
  5961-5974,6504-6535,6767-6772,6857-6867,7009-7035,7121-7135,7171-7179,7322-7338,7409-7422,
  7493-7514,7753-7776` (unmigrated owner/user/player identity surfaces).
- **Why confirmed/history:** the current transfer transaction omits the cash-world tables outright. The
  captain's logs repeatedly document identity/cold-load/presence bugs from split authorities and raw
  `human:guest_jeff` surfaces (`docs/captains-log/tournaments/persona-identity-and-psychology-continuity.md:13-23`,
  `docs/captains-log/development/cash-coldload-seat-orphan.md:31-48`,
  `docs/captains-log/development/presence-shadow-cutover-step2.md:151-172`), and this path repeats that
  failure class at the account boundary.
- **Impact:** a guest who signs in with Google can appear to lose their casino/career progress because the new
  Google identity gets a fresh sandbox and fresh 200-chip bankroll while the old guest sandbox, ledger history,
  active cash session, table presence, stakes, dossier progress, experiment assets, coach-training progress,
  profile settings, and invites remain under the guest id. If the guest had an active seat or stake, the stale rows
  can also keep blocking or distorting cash custody and presence after the user can no longer reach them normally.
- **Fix sketch:** make account linking an identity-migration saga in one transaction or a resumable migration
  job: move/merge every owner-scoped table, rewrite canonical ledger/presence account ids with compensating
  entries if needed, and invalidate sandbox caches for both ids. If a full migration is too risky, fail closed
  when the guest has cash/tournament/session/stake/bankroll state and show a migration-required flow. Add
  regressions for a guest with bankroll, default sandbox, active cash session/presence, a stake/carry, profile
  settings, experiment/chat/training state, and a tournament invite linking to both a new and an existing Google
  account.

#### ⬜ 102. Socket game handlers reject the new owner after guest-to-Google transfer until REST repairs cache
The REST game-state route knows cached owner metadata can be stale after guest-to-user ownership transfer: if an
in-memory game still says `owner_id=guest_*`, `_authorize_game_access()` re-checks the persisted `games` row,
calls `_sync_cached_owner_from_db()`, and then allows the new Google owner. The socket handlers do not use that
helper. `join_game`, `player_action`, `send_message`, and `progress_game` read `game_state_service.get_game()`
and compare the socket user directly with the cached `owner_id`; if the database transfer happened while the
game stayed hot in memory, the rightful Google owner gets `NOT_OWNER` until a REST path happens to sync the cache.
- **Files:** `poker/auth.py:287-290,357-391,409-420` (OAuth captures guest id, transfers DB ownership, clears
  the session, then installs the Google user), `poker/repositories/user_repository.py:135-160` (transfer updates
  persisted `games.owner_id`/`owner_name` only; no in-memory game-state cache update),
  `flask_app/routes/game_routes.py:78-93,96-138` (REST stale-owner repair and cache sync),
  `flask_app/routes/game_routes.py:2494-2512,2523-2550,2652-2682,2709-2725` (socket handlers deny on cached
  owner without a persisted-owner re-check), `tests/test_game_route_auth.py:173-211` (REST regression for this
  exact stale-cache transfer), `tests/test_websocket_auth.py:73-180,192-432` (socket auth tests cover owner,
  non-owner, and admin paths, but not transferred-owner stale cache).
- **Why confirmed:** the codebase already treats this as a real failure mode for REST. The same in-memory game
  data object is the socket authority, but the socket branches duplicate owner checks instead of calling the
  DB-backed authorization path or syncing stale ownership before denial.
- **Impact:** a guest who signs in with Google while a game remains loaded can land back in the SPA with a valid
  transferred DB row but no usable realtime game controls. The socket may refuse room join, player actions, chat,
  or manual progression as `NOT_OWNER`, making the transferred game look broken or abandoned until a REST
  game-state fetch runs first and repairs the cache.
- **Fix sketch:** add a socket-safe owner authorization helper that mirrors `_authorize_game_access()`'s persisted
  re-check and `_sync_cached_owner_from_db()` call, then use it in `join_game`, `player_action`, `send_message`,
  and `progress_game`. Add socket regressions mirroring
  `test_game_state_allows_new_owner_after_db_transfer_with_stale_cache` for room join and at least one mutating
  handler.

#### ⬜ 103. Copied coach/stats owner guards still deny transferred games on stale cached owners
The coach and stats blueprints each define their own `_require_game_owner()` helper and describe it as mirroring
`game_routes._authorize_game_access()`, but both copies only consult the persisted `games` owner when the cached
`owner_id` is `None`. They never perform the mismatch re-check that `game_routes` added for guest-to-user ownership
transfer. If a hot in-memory game still carries the guest owner after OAuth moves the `games` row to the Google
owner, these auxiliary endpoints deny the rightful user with 403 even though `/api/game-state/<game_id>` would repair
and allow the same access.
- **Files:** `poker/auth.py:287-290,357-391,409-420` and
  `poker/repositories/user_repository.py:135-160` (OAuth transfer updates persisted game ownership but not hot cache),
  `flask_app/routes/game_routes.py:78-93,96-138` (central REST helper re-checks DB on cached-owner mismatch and syncs),
  `flask_app/routes/coach_routes.py:58-81` and `flask_app/routes/stats_routes.py:40-64` (copied helpers re-check DB
  only for missing cached owners), `flask_app/routes/coach_routes.py:84-95,147-158,688-728,731-742,822-887`
  (coach endpoints using the stale helper), `flask_app/routes/stats_routes.py:178-188,302-312,528-543` (chat
  suggestion endpoints using the stale helper), `tests/test_game_route_auth.py:173-211` (core REST stale-transfer
  regression), `tests/test_coach_route_auth.py:180-208` (coach tests cover `NULL` owner denial but not transferred
  owner mismatch).
- **Why confirmed:** the helper bodies diverge exactly at the transfer fix: central REST re-checks persistence before
  denial whenever cached owner differs from the caller, while the coach/stats copies deny immediately unless the
  cached owner is missing.
- **Impact:** after guest-to-Google linking, the user can recover the game through core game-state but still lose
  coach stats, coach ask/config/review/progression/onboarding, and chat-suggestion flows until some other request
  repairs the cache first. That makes the account-link transition order-dependent and can look like permissions or
  coach entitlement failure.
- **Fix sketch:** remove the copied helpers or delegate them to a shared owner-or-admin authorization helper with the
  same persisted-owner mismatch repair as `_authorize_game_access()`. Add coach and stats route regressions using a
  stale cached guest owner plus a transferred persisted Google owner.

#### ⬜ 111. Existing Google users with a previous guest link skip transfer for later guest sessions
Google login stores the current guest id in `oauth_guest_id`, but the callback only transfers a guest into an
existing Google account when that user has no `linked_guest_id`. A user who once linked `guest_old`, later plays as
`guest_new`, and then signs into the same Google account skips `transfer_game_ownership()` entirely, even for the
six table families that the transfer helper currently knows how to move.
- **Files:** `poker/auth.py:287-290` (current guest id captured before OAuth), `poker/auth.py:357-367`
  (existing-user transfer gated by `not existing_user.get('linked_guest_id')`),
  `poker/auth.py:373-375,409-420` (login proceeds as the existing Google user),
  `poker/repositories/user_repository.py:141-228` (the skipped transfer is the only path moving games/API usage,
  prompt captures, career stats, and tournament result/standing rows).
- **Why confirmed:** `linked_guest_id` is a single historical field, not evidence that the current `oauth_guest_id`
  has already been migrated. Once it is populated, every later guest session for that Google account bypasses the
  transfer branch.
- **Impact:** returning users can strand even the legacy-supported ownership surfaces under a fresh guest id after
  signing in. That compounds #98's incomplete transfer coverage by sometimes skipping the transfer completely.
- **Fix sketch:** always transfer the current OAuth guest id when it differs from the Google user id, using an
  idempotent link-history table or per-guest transfer marker instead of a single `linked_guest_id` gate. Add regressions
  for first link and second/new guest link into the same Google account.

#### ⬜ 112. Guest cookies survive Google linking and can resurrect the linked guest after logout
Guest login sets long-lived signed `guest_id`, `guest_tracking_id`, and `guest_name` cookies. Google OAuth then clears
only the Flask session before installing the Google user; it does not expire those guest cookies. Logout only clears
those cookies when the current session user is a guest. After a linked Google user logs out, a later request with no
session can restore the stale guest identity from the still-valid cookie.
- **Files:** `poker/auth.py:180-213` (guest cookies are set for 30 days), `poker/auth.py:229-244` (logout clears them
  only if the logged-out session user is a guest), `poker/auth.py:357-420` (Google callback switches sessions without
  expiring guest cookies), `poker/auth.py:603-640` (no-session requests restore a guest from `guest_id`).
- **Why confirmed:** the cookie lifetime outlives the Google session transition, and the restore path has no check that
  the guest id was linked or migrated.
- **Impact:** after signing out of Google, the same browser can silently fall back into the old guest owner, creating
  new games, bankroll rows, coach state, quota tracking, and profile changes under the identity the user thought they
  had linked away from.
- **Fix sketch:** expire guest cookies on successful Google link and on all logout responses, or have `get_current_user()`
  reject guest cookies whose id is linked to a Google user. Add an OAuth-link regression that logs out and verifies no
  guest session is resurrected.

### S2 (auth / visibility / generated-persona writes)

#### ⬜ 54. Public reference-image lookup can auto-generate and persist arbitrary personalities
`GET /api/personality/<name>/reference-image` has no authentication or owner/visibility check. It calls
`personality_generator.get_personality(name)` just to test existence, but that helper auto-generates a missing
personality and saves an ownerless public, non-circulating row. The neighboring `PUT` route has owner/admin guards,
so the read route is using a more powerful mutating lookup than the write route allows.
- **Files:** `flask_app/routes/personality_routes.py:440-461` (unguarded GET calls `get_personality`),
  `flask_app/routes/personality_routes.py:467-505` (PUT requires auth/owner/admin),
  `poker/personality_generator.py:514-554` (missing names are generated and persisted as ownerless public rows).
- **Why confirmed:** the GET route performs no auth checks before calling the side-effecting generator. The generator
  explicitly persists non-reserved missing names when no `owner_id` is supplied.
- **Impact:** anonymous callers can trigger paid/personality-generation work and durable DB writes for arbitrary names.
  They can also bypass visibility expectations for existing private/disabled personalities by reading reference-image
  metadata through a route that does not mirror the surrounding ownership checks.
- **Fix sketch:** make GET non-mutating and visibility-scoped: require auth for private/system rows, return 404 for
  inaccessible or missing personalities, and use repo lookup methods that do not auto-generate.

#### ⬜ 55. Character dossier reads bypass personality visibility and can leak another user's live-game data
`GET /api/character/<identifier>/dossier` intentionally allows anonymous reads. It resolves any personality id/name
through direct repository lookups that do not filter visibility/ownership, builds the personality payload, then scans
all in-memory games for a matching player name with no owner filter. Anonymous responses still include personality
fields plus any live emotion/observation/pressure/memorable-hand data found in that global game scan.
- **Files:** `flask_app/routes/character_routes.py:52-64` (observer auth optional),
  `flask_app/routes/character_routes.py:67-84` and `poker/repositories/personality_repository.py:302-341` (direct
  id/name resolution ignores visibility), `flask_app/routes/character_routes.py:164-190` (payload uses direct by-id
  load), `flask_app/routes/character_routes.py:198-224` (global in-memory game scan by player name),
  `flask_app/routes/character_routes.py:516-654` (anonymous response still returns live-game-derived fields).
- **Why confirmed:** no owner/admin/public/circulating check happens before the route returns, and `_find_game_data_with_player`
  iterates `game_state_service.games` without comparing the game owner to the caller.
- **Impact:** a caller who knows or guesses a private personality id/name can read dossier metadata. If that display
  name appears in another user's active game, the caller can also receive transient table-derived observations or
  memorable-hand data from that game.
- **Fix sketch:** require auth for dossier reads beyond public persona metadata; enforce public/circulating-or-owner-or-admin
  visibility; and restrict live game lookup to caller-owned games, ideally by requiring a `game_id` and reusing the
  game access guard.

#### ⬜ 75. Coach opponent-tells can fail open around the paid scouting gate
`GET /api/coach/opponent-tells?opponent=...` is meant to mirror the dossier's paid `sizing_polarization` gate.
However, `_sizing_tell_locked()` resolves the query string to a stable personality id and returns `False`
("unlocked") whenever that resolution fails. The route then still loads revealed showdown decisions by the raw
`pda.player_name` string and computes the sizing tell. So an opponent name that exists in the caller's hand history
but cannot be resolved to a visible/stable personality id skips the informant/hand-count gate entirely.
- **Files:** `flask_app/routes/coach_routes.py:373-405` (gate returns unlocked when `personality_id` is missing),
  `flask_app/routes/coach_routes.py:408-488` (locked check happens before raw-name tell computation),
  `flask_app/services/coach_sizing_tells.py:212-283` (DB adapter reads by `g.owner_id` + raw `pda.player_name`),
  `flask_app/routes/character_routes.py:803-824` (dossier route gates the same read after resolving the stable id).
- **Why confirmed:** the route comment says the coach tell is gated behind the same read the dossier sells, but the
  missing-personality branch is fail-open while the later data lookup is not tied to the resolved id.
- **Impact:** players can receive a computed sizing-polarization tell without grinding the observation floor or paying
  the informant when display-name history exists but personality-id resolution fails or is intentionally ambiguous.
- **Fix sketch:** make Circuit gating fail closed when raw-name decision history exists but the opponent cannot be
  resolved to a stable id; persist/query coach decision history by opponent id where available; add route tests for
  unresolved-name, below-floor, and informant-unlocked cases.

#### ⬜ 81. Informant unlock POST can buy hidden-personality intel by id
`POST /api/character/<identifier>/informant` resolves the path identifier with `_resolve_personality_id()`, which uses
direct by-id/name repository lookups with no owner/visibility/public/circulating check. Once resolved, the route records
an informant unlock and debits the caller's bankroll for that personality id. This mutation path is separate from the
already-listed dossier GET visibility leak: it creates durable unlock state and spends chips for hidden targets.
- **Files:** `flask_app/routes/character_routes.py:67-84` (direct id/name resolver),
  `flask_app/routes/character_routes.py:975-1125` (authenticated informant purchase/debit path).
- **Why confirmed:** the route requires a logged-in observer but does not enforce that the target personality is visible
  to that observer before existence, offer, unlock, or debit logic.
- **Impact:** authenticated users who know or guess a private/disabled/other-user personality identifier can create
  unlock records, spend bankroll, and use the response behavior as an existence oracle.
- **Fix sketch:** share the dossier visibility policy before purchase; allow only visible public, owner-owned, or admin
  targets. Return the same 404/403 shape for hidden and missing ids, and add route tests for hidden/private/system rows.

#### ⬜ 92. Informant purchases can persist an unlock before the bankroll debit or ledger row succeeds
The informant purchase route writes `dossier_informant_unlocks` first, then debits the bankroll cache and writes the
ledger transfer. The comment documents the tradeoff: writing first avoids double-charging retries, but if the later
debit or ledger write fails, the durable unlock remains and the next retry is treated as already owned. The credit
history special-case uses the same unlock-before-debit shape.
- **Files:** `flask_app/routes/character_routes.py:1025-1062` (credit-history unlock then debit/ledger),
  `flask_app/routes/character_routes.py:1095-1115` (scouting unlock then debit),
  `poker/repositories/game_repository.py:1145-1166` (`INSERT OR IGNORE` durable unlock).
- **Why confirmed/history:** the dossier progression captain log calls out the intended player-favoring failure mode,
  and current source still has no transaction tying the entitlement write to the bankroll/ledger writes. This is
  separate from #81's hidden-target purchase issue; it applies even to a visible, valid informant offer.
- **Impact:** a DB/repository failure, process crash, or partial write after `record_informant_unlock()` can grant paid
  intel without consuming chips or writing the corresponding economic audit row. Later retries cannot repair the charge
  because the idempotent unlock is already present.
- **Fix sketch:** make purchase atomic under a chip unit-of-work that records entitlement, bankroll debit, and ledger row
  together, or introduce a pending purchase/strict ledger row and only expose the unlock after the debit is durable. Add
  route regressions that inject failures in `save_player_bankroll()` and ledger recording and assert no usable unlock
  remains.

#### ⬜ 94. Dossier scouting gate over-redacts non-Circuit character reads for authenticated users
The dossier route comment says scouting should be Circuit-only and ungated outside the Circuit, but the backend resolves
or creates a default sandbox for every authenticated observer and then applies the gate whenever `sandbox_id` is truthy.
A normal non-Circuit character-card fetch uses only `/api/character/<id>/dossier`; it does not pass game/Circuit context.
If no lifetime row exists, `life_counts or {}` becomes zero observed hands, so sections can render locked even though the
user is not in the Circuit context where the grind/informant economy applies.
- **Files:** `react/react/src/components/character/api.ts:309-312` (dossier fetch has no Circuit parameter),
  `flask_app/routes/character_routes.py:520,562-564,803-821` (observer default sandbox resolves, then gates on
  `sandbox_id`), `flask_app/services/sandbox_resolver.py:70-84` (creates default sandbox when absent),
  `react/react/src/components/character/CharacterDetailCard.tsx:216-222` (scouting strip renders whenever the response
  includes scouting; Circuit context only controls buying).
- **Why confirmed/history:** the dossier progression captain log's invariant is `sandbox + observer + lifetime row` for
  gated Circuit reads. Current code treats an authenticated default sandbox as enough context, even when no lifetime row
  exists. This is the opposite of #75's coach tell fail-open and separate from #81/#92 purchase bugs.
- **Impact:** signed-in users can see locked/redacted dossier sections on ordinary character surfaces, making unlocked
  profile content look unavailable and pushing users toward a Circuit-only purchase/grind flow outside the intended mode.
- **Fix sketch:** apply scouting only when the route receives explicit Circuit/sandbox context or finds a real lifetime
  row for the observer/opponent. Do not create a default sandbox merely to render a general dossier. Add route/UI tests
  for anonymous, authenticated non-Circuit, and Circuit-context reads.

#### ⬜ 79. Custom game creation trusts opponent names and can generate ownerless public personas
`POST /api/new-game` accepts `personalities` entries as raw strings or `{name: ...}` objects, appends those names to the
AI roster, and builds controllers for them. The controller initialization path creates an `AIPokerPlayer`, whose
personality loader calls `PersonalityGenerator.get_personality(self.name)` without passing the authenticated owner id.
A missing name therefore triggers AI personality generation and persists an ownerless, public, non-circulating row;
when avatar generation is enabled, the route also starts background avatar generation for the same unvetted names.
- **Files:** `flask_app/routes/game_routes.py:1639-1668` (auth only),
  `flask_app/routes/game_routes.py:1757-1797,1854-1882,1998-1999` (raw roster names -> controllers -> avatars),
  `flask_app/handlers/tiered_factory.py:95-134,278-313,371-388` (default/allowed controllers inherit the AI player
  init path), `poker/controllers.py:682-710` and `poker/poker_player.py:105-113,295-306` (owner id not passed into
  `get_personality`), `poker/personality_generator.py:490-554` (missing names are generated and saved public when
  `owner_id` is absent).
- **Why confirmed:** the route validates duplicate display names and LLM/bot config, but not personality existence,
  ownership, visibility, or generation eligibility before constructing controllers.
- **Impact:** any authenticated user can turn arbitrary opponent names into durable generated-personality rows and
  provider work through game creation. Hidden/private/disabled names can also be selected through direct lookup paths
  instead of the player-facing personality list policy.
- **Fix sketch:** require requested opponents to resolve through the same visible/allowed personality catalog used by
  Custom Game selection, or pass `owner_id` and create private rows only through an explicit create-personality flow.
  Add route tests for unknown names, disabled rows, other-user private rows, owner private rows, and public catalog rows.

#### ⬜ 56. Targeted chat suggestions trust `targetPlayer` and can generate off-roster personalities
`POST /api/game/<game_id>/targeted-chat-suggestions` verifies the caller owns the game, but then trusts the JSON
`targetPlayer` string. It does not verify that the target is a seated non-human in that game before calling
`personality_generator.get_personality(targetPlayer)`, so a game owner can trigger the same side-effecting generation
path for arbitrary names, or pull inaccessible/private config into prompt context by naming it as a target.
- **Files:** `flask_app/routes/stats_routes.py:302-312` (game ownership guard),
  `flask_app/routes/stats_routes.py:329-331` (target comes from request JSON),
  `flask_app/routes/stats_routes.py:395-405` (unchecked target loads personality metadata),
  `poker/personality_generator.py:514-554` (missing names are generated and persisted).
- **Why confirmed:** the route already has `game_state` in hand, but no roster membership check appears before the
  target personality lookup.
- **Impact:** any owner of any game can create durable generated-personality rows for arbitrary names, and prompt/log
  context can include personalities that are not seated in the authorized game.
- **Fix sketch:** derive valid targets from `game_data['state_machine'].game_state.players`; reject missing, human, or
  unseated targets. Load metadata with a non-mutating, visibility-scoped lookup.

#### ⬜ 91. Post-round chat suggestions trust `playerName` and can expose another seat's hole cards to LLM/logs
`POST /api/game/<game_id>/post-round-chat-suggestions` verifies the caller owns the game, but then trusts the JSON
`playerName` to choose whose hand context is rendered. `build_hand_context_from_recorded_hand()` reads
`recorded_hand.hole_cards[player_name]`, and the rich formatter prints those as `YOUR HOLE CARDS`; the route logs both
the full hand context and prompt before sending it to the LLM. Because recorded hands store hole cards by player name,
a caller can submit an AI seat name after an uncontested hand and make mucked bot cards appear as the player's cards in
prompt/log context.
- **Files:** `flask_app/routes/stats_routes.py:528-559,611-657` (owner guard, trusted `playerName`, prompt/logging),
  `flask_app/utils/hand_context.py:48-95,255-321` (player-name keyed hole-card rendering),
  `poker/memory/hand_history.py:107-116,334-340` (recorded hands carry hole cards for names).
- **Why confirmed/history:** the route derives game authorization from `game_id` but derives the sensitive card
  perspective from unvalidated request data. This is not just off-roster text generation like #56; it can surface
  another seated player's recorded private cards to a third-party LLM provider and application logs.
- **Impact:** game owners can reveal hidden/mucked AI hole cards and include them in provider prompts, telemetry, and
  logs, polluting post-round suggestions with information the player should not have seen.
- **Fix sketch:** derive the human/player perspective from the authenticated owner's seated player in `game_data`, or
  validate `playerName` against the authorized human seat and reject all other names. For non-showdown hands, do not
  expose non-human hole cards in post-round prompt context. Add route tests that submit an AI name after a fold win and
  assert the AI cards are absent from prompt/log payloads.

#### ⬜ 59. Public prompt-preset list leaks private/admin presets
`GET /api/prompt-presets` treats authentication as optional. With no current user it passes
`owner_id=None` into `list_prompt_presets()`, whose unauthenticated branch selects every row from
`prompt_presets` instead of only system/shared rows or returning 401. The item-level GET/PUT/DELETE
paths enforce ownership, so the list endpoint contradicts the private-preset model.
- **Files:** `flask_app/routes/prompt_preset_routes.py:43-65`,
  `poker/repositories/prompt_preset_repository.py:124-165`, `tests/test_prompt_preset_routes.py:78-210`.
- **Why confirmed:** an unauthenticated route call leaves `owner_id` falsy; the repository's else
  branch has no `WHERE owner_id ... OR is_system` visibility filter.
- **Impact:** anonymous callers can enumerate saved prompt configs and guidance text, including
  private/admin experiment presets that may contain strategy notes or prompt-injection guidance.
- **Fix sketch:** require authentication for list, return only `owner_id = current_user.id OR is_system = TRUE`
  for normal users, and add an admin-only all-presets path if needed. Add list regressions for
  anonymous, other-user, owner, and admin cases.

#### ⬜ 83. Direct prompt-preset GET leaks ownerless non-system presets
The preset list route's repository query deliberately includes a user's own presets plus `is_system = TRUE`, with a
comment noting that `owner_id IS NULL` non-system presets should not be broadly exposed. The direct GET route fetches by
id and then `_can_access_preset()` allows any authenticated user whenever `owner_id is None`, without checking
`is_system`. The response includes `prompt_config` and `guidance_injection`.
- **Files:** `flask_app/routes/prompt_preset_routes.py:23-36,127-157` (direct route access helper),
  `poker/repositories/prompt_preset_repository.py:92-151` (direct get vs list policy/comment).
- **Why confirmed:** list and direct-read policies diverge for ownerless, non-system rows.
- **Impact:** authenticated users who know a preset id can fetch admin/manual/shared-in-progress preset contents that
  are intentionally absent from their preset list.
- **Fix sketch:** make `_can_access_preset()` require `is_system` for ownerless shared reads, with admin override, and
  add direct GET tests for ownerless system, ownerless non-system, owned-by-self, and owned-by-other presets.

#### ⬜ 60. Public avatar GET fallback can spend image-generation budget
The image blueprint intentionally leaves avatar GETs public, but the fallback path is not read-only.
When `/api/avatar/<personality>/<emotion>` or `/full` finds a priority fallback image, it serves that
image and kicks off `start_single_emotion_generation()` for the missing requested emotion. That handler
explicitly allows triggers from the unauthenticated avatar-serving endpoint, starts a background thread,
and calls `generate_character_images()` without an `owner_id`, so the PRH-2 per-owner budget gate cannot
bind the paid image call.
- **Files:** `flask_app/routes/image_routes.py:224-280,283-341`,
  `flask_app/handlers/avatar_handler.py:166-193,219-254`, `poker/character_images.py:323-330,392-396,467-481`,
  `core/llm/client.py:395-414`, `tests/test_image_route_auth.py:1-7,170-180`.
- **Why confirmed:** both public GET routes are limiter-exempt and call the generation starter on fallback;
  the background path passes no `owner_id`; tests only lock that GETs remain unauthenticated, not that they
  are side-effect-free.
- **Impact:** anonymous traffic can turn image-serving misses into provider calls and DB writes, consuming
  global spend budget and backend worker resources outside any user cap.
- **Fix sketch:** keep GET serving public but make it read-only; trigger missing-emotion generation only from
  authenticated game/message paths with game-owner attribution, or require an explicit generation endpoint.
  Add a regression that a public GET fallback does not call `start_single_emotion_generation()`.

#### ⬜ 61. Admin force-regenerate of a public persona can privatize and de-circulate it
The force-regenerate route now blocks ordinary users from overwriting system/other-user personas, but the
admin-allowed path still passes `owner_id=current_user['id']` into `PersonalityGenerator.get_personality()`.
The generator saves regenerated rows with `visibility='private'` when `owner_id` is supplied and always passes
`circulating=False`; the repository only preserves existing owner/visibility/circulation when those args are
`None`.
- **Files:** `flask_app/routes/personality_routes.py:812-850`, `poker/personality_generator.py:532-554`,
  `poker/repositories/personality_repository.py:130-155,163-177,245-252`,
  `tests/test_personality_route_idor.py:130-185`.
- **Why confirmed:** the non-admin IDOR regression covers system/other-user denial, but no admin regression
  asserts catalog metadata is preserved; the admin path supplies explicit owner/private/non-circulating values
  that win over repository preservation.
- **Impact:** an admin regenerating a public/system persona can remove it from the shared catalog and the
  auto-seat pool by making it private to that admin and non-circulating.
- **Fix sketch:** for existing rows, generate a new config and update config-only while preserving `owner_id`,
  `visibility`, and `circulating`; pass `owner_id` only for brand-new user-created generations. Add an admin
  force-regenerate regression for a public, circulating persona.

#### ⬜ 108. Bare `PersonalityGenerator()` paths write to a different local DB than the Flask app
The app's repository layer uses the shared DB helper, which resolves to `data/poker_games.db` outside Docker and
`/app/data/poker_games.db` inside Docker. App startup correctly injects `extensions.personality_repo` into the global
`extensions.personality_generator`. But `/api/generate_personality` validates collisions and ownership against
`extensions.personality_repo`, then instantiates a bare `PersonalityGenerator()`. A bare generator creates its own
repository and, outside Docker, falls back to root `poker_games.db` instead of `data/poker_games.db`. `AIPokerPlayer`
also caches a bare generator for personality loading.
- **Files:** `poker/db_utils.py:10-18` and `flask_app/config.py:226-229` (shared app DB path helper),
  `flask_app/extensions.py:250-312` (repos and global generator use app DB repo),
  `flask_app/routes/personality_routes.py:821-849` (route checks app repo, then creates bare generator),
  `poker/personality_generator.py:434-456` (bare generator ensures schema on root `poker_games.db` locally),
  `poker/poker_player.py:295-300` (AI player loads through a cached bare generator).
- **Why confirmed:** outside Docker `/app/data` is absent, so Flask reads/writes `data/poker_games.db` while bare
  generators read/write root `poker_games.db`. The bare generator even runs schema setup on the alternate file, masking
  the split instead of failing loudly.
- **Impact:** local generated personalities can return success but not appear in normal list/read routes, because they
  landed in the wrong database. Local AI players can also ignore app-visible user/admin edits, use stale/root-DB configs,
  or create duplicate generated personas in a parallel DB.
- **Fix sketch:** use `extensions.personality_generator` or construct `PersonalityGenerator(personality_repo=extensions.personality_repo)`
  in route code, and make `PersonalityGenerator._get_default_db_path()` delegate to `poker.db_utils.get_default_db_path()`.
  Add a local-path regression that generated personalities land in `extensions.persistence_db_path`.

#### ⬜ 65. Direct personality GET bypasses list visibility and disabled filters
`GET /api/personalities` filters the player-facing list with `include_disabled=is_admin` and
`circulating_only=not is_admin`, but `GET /api/personality/<name>` only rejects rows owned by another user. Ownerless
DB rows with `visibility='disabled'` and public-but-non-circulating rows are returned to any authenticated caller by
name; `load_personality()` also increments `times_used`, so the read is not a neutral metadata probe.
- **Files:** `flask_app/routes/personality_routes.py:67-90,133-152`,
  `poker/repositories/personality_repository.py:257-290,419-509,830-840`,
  `tests/test_personality_visibility_admin.py:150-177`, `tests/test_personality_route_idor.py:130-185`.
- **Why confirmed:** the direct route never loads or checks row visibility/circulation metadata before returning the
  config, while the list route and repository filters explicitly encode those gates. Existing tests cover owner/admin
  visibility writes and force-regenerate IDOR, but not direct GET of disabled/non-circulating rows.
- **Impact:** authenticated users who know a hidden persona name can fetch disabled/system/test/generated persona
  config and perturb its usage counter, even when the same persona is intentionally absent from the user-facing list.
- **Fix sketch:** make direct GET use the same visibility policy as the list route: owner, admin, or visible public
  row allowed; disabled rows admin-only; and player-facing non-circulating rows either denied or explicitly scoped to
  management views. Add route regressions for disabled, non-circulating, owner-private, and other-user-private rows.

### S2 (coach / progression attribution)

#### ⬜ 113. Admin coach reads and writes progression under the admin user, not the game owner
Coach game routes allow admins past the owner guard, but after that they call `_get_current_user_id()` for progression,
coach-state reads, and onboarding writes. When an admin inspects another user's game, the hand context comes from the
other user's game while the progression/profile data is loaded or mutated for the admin account.
- **Files:** `flask_app/routes/coach_routes.py:70-81` (owner mismatch allowed for admins),
  `flask_app/routes/coach_routes.py:97-108` and `flask_app/routes/coach_routes.py:183-190` (stats/ask pass requester id
  into `compute_coaching_data_with_progression()`), `flask_app/routes/coach_routes.py:822-837` (progression reads the
  requester), `flask_app/routes/coach_routes.py:889-908` (onboarding updates/initializes the requester).
- **Why confirmed:** the helper distinguishes authorization requester from game owner only at the access check. The
  downstream progression calls then use the requester unconditionally, so admin access changes which account's coach
  state is read/written.
- **Impact:** admin inspection can mix one user's live hand with another user's coaching profile, leak/history state,
  and skill progression. Mutating onboarding calls can also alter the admin's coach level/profile while acting on a
  different user's game.
- **Fix sketch:** resolve the authorized game owner and pass that id into game-scoped coach progression/history reads;
  keep the requester id only for auth/audit. Block or explicitly audit admin writes against another owner's coach
  profile. Add owner/admin route regressions for stats, progression, and onboarding.

#### ⬜ 114. Coach leak/chart loaders confuse account owner name with the human seat name
Game creation lets the player choose `playerName` independently from the authenticated account name (`owner_name`), and
training does the same. Several coach loaders later identify the human's decision rows by filtering
`player_decision_analysis.player_name == games.owner_name` instead of using the actual human seat name or a stable seat
identity.
- **Files:** `flask_app/routes/game_routes.py:1649-1651,1971-1975` (seat display name and owner name are separate),
  `flask_app/routes/training_routes.py:322-325` (training accepts a separate player name),
  `flask_app/services/coach_leaks.py:285-309`, `flask_app/services/coach_chart_data.py:160-171`, and
  `flask_app/services/coach_sizing_tells.py:330-348` (owner-wide coach reads keep rows whose `player_name` equals
  `owner_name`).
- **Why confirmed:** nothing requires the human seat's display name to equal the account name, yet the analysis filters
  treat that equality as the human-seat discriminator.
- **Impact:** players using a nickname or renamed table seat can get missing or wrong self leaks, chart review data,
  drill seeds, and sizing-read samples. In edge cases, an AI/human row whose display name matches the account owner can
  be misclassified as the player's own decision history.
- **Fix sketch:** persist/use the actual human seat identity for each game, preferably a stable seat id or `is_human`
  marker in decision-analysis rows, and filter coach history by that instead of `owner_name`. Add regressions where
  `playerName != owner_name` for live games and training games.

#### ⬜ 115. Training proactive tips contaminate tip-effectiveness metrics after the training game is purged
Training games are force-saved in proactive coach mode, proactive tips are recorded into `coach_tips`, and the
tip-effectiveness query joins `coach_tips` to `player_decision_analysis` by game/hand/player without joining `games` or
excluding training mode. Training cleanup deletes the `games` row but preserves historical analysis rows by design, so
training nudges can permanently remain in owner/admin effectiveness metrics even after the training session disappears.
- **Files:** `flask_app/routes/training_routes.py:120-142` (prior training games are deleted),
  `flask_app/routes/training_routes.py:280-292` (training game is saved with proactive coach mode),
  `flask_app/routes/coach_routes.py:175-180,207-210` (proactive tips served/recorded through coach ask handling),
  `poker/repositories/coach_repository.py:349-382` (effectiveness reads `coach_tips` + `player_decision_analysis`
  without `games` or training exclusion), `poker/repositories/game_repository.py:253-260` (delete intentionally
  preserves historical analytics tables).
- **Why confirmed:** training is the one mode that is both explicitly ephemeral and explicitly coach-driven. Its
  persisted tips/decision rows outlive the `games` row that could identify them as training, while the metrics query
  has no independent mode filter.
- **Impact:** practice-session nudges can skew `/api/coach/tip-effectiveness` and admin coach metrics, producing stale
  or false compliance rates that no longer correspond to live/real-money play.
- **Fix sketch:** stamp `coach_tips` and decision-analysis rows with game mode, or preserve enough game metadata for
  analytics joins, then exclude `train-*`/training mode from real-session effectiveness. Add regressions for active and
  purged training games.


### S2 (training / guest quota and coach cost)

#### ⬜ 90. Guest training sessions bypass guest hand limits while enabling proactive coach LLM work
`/api/training/start` only requires a current user/guest identity and does not reject guests. The training game it
creates sets `training_mode=True`, saves `guest_tracking_id=None`, and immediately stores coach mode `proactive`. The
generic guest action quota only runs when `guest_tracking_id` exists, and `_track_guest_hand()` treats missing tracking
ids as a pre-migration/no-op path. Each human turn in the training game can still schedule `prefetch_proactive_tip()`,
which loads the proactive coach mode and builds the assistant payload before calling the coach LLM path.
- **Files:** `flask_app/routes/training_routes.py:240-260,290-320` (training game omits tracking id and enables
  proactive coach mode), `flask_app/routes/game_routes.py:2044-2050` and `flask_app/handlers/game_handler.py:200-217`
  (guest limit/counting require tracking id), `flask_app/handlers/game_handler.py:3892-3900`,
  `flask_app/services/coach_prefetch.py:99-128`, and `flask_app/services/coach_assistant.py:243-250` (proactive coach
  prefetch reaches LLM work).
- **Why confirmed/history:** the route combines two states that the guest quota path treats differently: it creates a
  guest-owned game but deliberately omits the tracking token used to count guest hands. The proactive coach path remains
  live because coach mode is persisted before play.
- **Impact:** unauthenticated/guest users can play training hands outside the normal guest hand limit while still
  triggering background coach calls, creating an unmetered provider-cost path and weakening the intended guest gating.
- **Fix sketch:** either block guest identities from training start, or assign the guest tracking id to training games
  and count hands through the same quota path. Disable proactive coach mode for guest training unless it is explicitly
  budgeted. Add route/handler regressions for guest training start, hand-count increment, and coach prefetch suppression
  after the limit.

#### ⬜ 93. Training decisions are saved into the real-session preflop leak review
Training mode is documented as non-counting by wiring absence, and the UI presents the chart-graded coach as reviewing
real sessions. But training reuses the generic `/api/game/<id>/action` path, which always calls
`analyze_player_decision()` and saves `player_decision_analysis`. The preflop review loaders then read all owner
preflop decision rows by joining `player_decision_analysis` to `games` on `owner_id`, with no `train-%` exclusion.
- **Files:** `flask_app/routes/training_routes.py:1-15,240-292` (training is non-counting but persists a `train-*`
  game), `flask_app/routes/game_routes.py:2058-2070,330-350` (shared action path saves decision analysis),
  `flask_app/routes/coach_routes.py:291-305` (review loads all owner chart decisions),
  `flask_app/services/coach_chart_data.py:150-171` and `flask_app/services/coach_leaks.py:290-309` (owner-wide
  preflop queries do not exclude training games).
- **Why confirmed/history:** the training captain log calls the feature practice/non-counting, but the current
  decision-analysis persistence is not one of the suppressed wiring surfaces. This is separate from fixed #18's
  `casebot` mapping drift.
- **Impact:** drill/practice decisions can contaminate the player's real-session leak report, trend analysis, and chart
  coaching, making skill diagnostics reflect sandboxed training spots rather than actual play.
- **Fix sketch:** either mark decision-analysis rows with a game mode and filter training out of real-session reviews,
  or intentionally add a separate training review surface. Add regressions that a `train-*` preflop action is absent
  from `load_owner_chart_decisions()` and `load_owner_preflop_decisions()`.

#### ⬜ 95. Hidden training games still consume the saved-game quota
Training games are hidden from the generic saved-game list because they have a dedicated training resume surface, and
the training plan says `train-*` games should be excluded from both `list_games()` and the saved-game limit. The training
start route still persists a normal `games` row, and `/api/new-game` enforces the guest/user game limit by calling
`count_user_games(owner_id)`, which counts every owned row without filtering `train-%`. A user can therefore hit the
generic saved-game cap with rows they cannot see in the generic continue-games list.
- **Files:** `docs/plans/TRAINING_MODE.md:102` (exclude `train-*` from list and saved-game limit),
  `flask_app/routes/training_routes.py:278-289` (training persists a game row),
  `flask_app/routes/game_routes.py:551-566` (generic list hides `train-*`),
  `flask_app/routes/game_routes.py:1653-1658` (new-game quota uses broad count),
  `poker/repositories/user_repository.py:19-28` (count query has no prefix/game-mode exclusion).
- **Why confirmed/history:** current code implements only the list half of the training plan. The quota half still
  treats hidden training rows as ordinary saved games.
- **Impact:** training sessions can silently consume saved-game capacity, causing new game creation to fail or hit guest
  caps even though the user-facing saved-games list appears below the limit.
- **Fix sketch:** change `count_user_games()` or the quota call to exclude dedicated-resume prefixes (`train-*`, and
  likely the same cash/tourney children hidden from the list), or store an explicit quota-counting game type. Add quota
  tests with only training rows and mixed ordinary/training rows.

### S3 (profile / avatar upload reliability)

#### ⬜ 57. User avatar upload/photo paths trust `Content-Length` and can process oversized bodies
The profile routes advertise an 8 MB avatar cap, but the file-upload and img2img-photo paths only check
`request.content_length` before reading the entire multipart part into memory. If the header is missing or
understates the real body, `request.files['file'].read()` can still load unbounded bytes. The shared service
defines `MAX_AVATAR_BYTES` and enforces it for server-side URL fetches via `_read_capped()`, but
`store_from_bytes()` and `_store()` do not check the actual byte length. Generated-image downloads also read
`MAX_AVATAR_BYTES + 1` bytes but never reject the over-cap sentinel before processing.
- **Files:** `flask_app/routes/profile_routes.py:51-53,151-158,201-207`,
  `poker/user_avatar_service.py:39-42,83-92,111-128,224-235`,
  `poker/image_processing.py:57-72` (PIL decode/resize after only a magic-byte gate).
- **Why confirmed:** `rg` finds no global Flask `MAX_CONTENT_LENGTH`; the only actual-byte cap is the URL-fetch
  loop. Upload, photo, and generated-image bytes all converge on `_store()` without a length guard.
- **Impact:** authenticated users and guests can force the backend to buffer and decode large images despite the
  documented cap, causing memory/CPU spikes and potentially bloating stored avatar data after provider-generated
  over-size responses.
- **Fix sketch:** enforce `len(image_bytes) <= MAX_AVATAR_BYTES` inside `_store()` for every acquisition path;
  read multipart uploads with a hard cap or set Flask `MAX_CONTENT_LENGTH`; reject generated downloads when
  `len(raw) > MAX_AVATAR_BYTES`; add service/route regressions for missing/false `Content-Length` and generated
  over-cap downloads.

### S2 (LLM provider timeouts)

#### ⬜ 62. Google LLM provider accepts PRH-18 timeouts but never applies them
`LLMClient` has a per-call/default timeout so in-game and ticker calls fail fast instead of holding locks.
It resolves the timeout and forwards it to `provider.complete()`, and the provider interface documents that
contract. `GoogleProvider.complete()` accepts the `timeout` parameter but never passes it to
`generate_content()` or wraps the call with a deadline, unlike the OpenAI-compatible providers that add the
per-call timeout to their SDK kwargs.
- **Files:** `core/llm/client.py:48-55,175-204`, `core/llm/providers/base.py:52-56`,
  `core/llm/providers/google.py:125-174`, `core/llm/providers/openai.py:115-120`,
  `core/llm/providers/groq.py:122-127`, `flask_app/handlers/tiered_factory.py:148-158`,
  `poker/poker_player.py:119-141`, `cash_mode/vice_narration.py:128-140`.
- **Why confirmed:** the timeout argument reaches `GoogleProvider.complete()` but is unused in the only network call.
- **Impact:** selecting Google for in-game decisions or flavor can still stall a hand/ticker under the exact lock
  window PRH-18 intended to bound.
- **Fix sketch:** plumb the timeout through the Google SDK if supported, configure the generated client call with
  a deadline, or wrap provider dispatch in an explicit timeout; add a provider unit test asserting the timeout
  reaches the SDK/wrapper.


### S2 (LLM model policy)

#### ⬜ 66. `/api/new-game` accepts disabled and system-only LLM models hidden from users
`/api/user-models` explicitly filters the catalog to models where both `enabled=1` and `user_enabled=1`, while
`/api/system-models` is admin-only for system-enabled/internal models. The game-creation route ignores
`_get_enabled_models_map()` and validates default and per-player `llm_config` only against static
`AVAILABLE_PROVIDERS` / `PROVIDER_MODELS`. A caller can therefore POST a disabled model, or a system-only
`user_enabled=0` model that the UI deliberately hides, and still create a game using it.
- **Files:** `flask_app/routes/game_routes.py:1396-1454` (user-visible model filter),
  `flask_app/routes/game_routes.py:1465-1568` (system-only/admin model split),
  `flask_app/routes/game_routes.py:1673-1712,1773-1785` (new-game static validation),
  `poker/repositories/llm_repository.py:135-183,293-338` (dual enabled/user_enabled model policy).
- **Why confirmed:** the user-facing endpoint and repository define the policy, but `api_new_game` never consults the
  enabled map it already has in the module.
- **Impact:** admin model toggles do not actually constrain direct game creation. Users can route in-game LLM calls to
  expensive, disabled, incompatible, or internal-only models as long as those models still exist in the static config
  and credentials are present.
- **Fix sketch:** validate every default and per-player game LLM config against `get_enabled_models_map()` when the
  table exists, falling back to the static list only for migration/table-absent cases. Add route tests for
  `enabled=0`, `user_enabled=0`, and an omitted `llm_config` whose configured default is disabled.

#### ⬜ 110. Enabled-model startup sync turns every newly added provider model on by default
The model config says only `DEFAULT_ENABLED_MODELS` should start enabled and that all other provider models should be
seeded disabled until an admin opts them in. The initial schema migration follows that whitelist, but the startup
`sync_enabled_models()` path inserts any missing model with `enabled=1, user_enabled=1`. Adding a model to the static
provider list therefore bypasses admin review and makes it available through `/api/user-models` on the next boot.
- **Files:** `core/llm/config.py:247-263` (`DEFAULT_ENABLED_MODELS` intent),
  `poker/repositories/schema_manager.py:3375-3412` (migration seeds from the whitelist),
  `flask_app/extensions.py:400-404` (startup sync runs every boot), `poker/pricing_loader.py:344-365`
  (missing models inserted as both system- and user-enabled), `flask_app/routes/game_routes.py:1396-1454`
  (`/api/user-models` exposes rows where both flags are enabled).
- **Why confirmed:** the migration and config describe a disabled-by-default policy for non-whitelisted models, but the
  boot-time sync uses unconditional `1, 1` for newly discovered models and is the path that runs after config changes.
- **Impact:** expensive, experimental, provider-incompatible, image-only, or system-only models can become user-visible
  simply because they were added to `PROVIDER_MODELS`, undermining the admin model toggle and budget controls.
- **Fix sketch:** make `sync_enabled_models()` mirror the migration whitelist: default non-whitelisted models to
  disabled, preserve existing rows, and add a regression that a newly added non-whitelisted model does not appear in
  `/api/user-models` until explicitly enabled.


### S2 (LLM spend accounting)

#### ⬜ 97. Pricing rows can make the LLM spend gate undercount or subtract provider usage
The PRH-2 spend gate decides whether to dispatch a new LLM call by summing recent `api_usage.estimated_cost`.
That makes pricing rows part of the enforcement boundary, but the pricing layer is only soft-validated. Missing
current pricing makes `_calculate_cost()` return `None`, so the call records `estimated_cost=NULL` and the spend reader
treats it as $0. Negative pricing is worse: the admin pricing API only checks that `cost` parses as a float, the schema
has no non-negative constraint, and the repository inserts that value directly. A negative input/output/image SKU then
creates negative estimated-cost rows that lower the rolling spend total after cache recompute.
- **Files:** `core/llm/tracking.py:430-493` (missing text/image SKU returns no cost), `core/llm/tracking.py:301-322`
  (budget SUM includes negative rows and treats NULL as 0), `core/llm/tracking.py:383-400` (warm spend cache ignores
  non-positive costs, so it diverges until recompute), `flask_app/routes/admin_dashboard_routes.py:1046-1080,1085-1106`
  (pricing APIs accept any numeric float), `poker/repositories/llm_repository.py:419-512` (pricing inserts have no
  non-negative validation), `poker/repositories/schema_manager.py:1102-1113` (`model_pricing.cost` has no CHECK),
  `flask_app/config.py:161-190` (NULL-cost startup scan warns only after rows already exist).
- **Why confirmed/history:** PRH-2 and the ops docs treat the spend gate as the launch backstop, but the current code
  acknowledges NULL-cost rows "slip the cap" and handles them as observability, not enforcement. The negative-cost path is
  not covered by the scanner at all.
- **Impact:** an admin typo, bad pricing bulk upload, stale custom model, or deleted/expired SKU can make paid LLM/image
  traffic invisible to the global/per-owner caps, or even subtract from the rolling total. Budget exhaustion alerts and
  cutoffs can therefore stay quiet during the exact provider-spend spike they are meant to stop.
- **Fix sketch:** reject `cost <= 0` in the route, repository, and schema; add a migration/check for existing non-positive
  rows; prevent enabling/routing models without all required active pricing SKUs when a budget is armed; and treat unknown
  cost as a blocking or conservative high-cost condition rather than `$0`. Add regressions for negative pricing, NULL-cost
  usage, and cache/recompute behavior.

### S3 (memory / prompt stats drift)

#### ⬜ 78. Re-saving a hand reallocates `hand_history.id` and splits equity snapshots
`save_hand_history()` uses `INSERT OR REPLACE` on `UNIQUE(game_id, hand_number)`. A second save for the same hand
therefore deletes and recreates the row with a new autoincrement `id`. Equity snapshots are unique by
`(hand_history_id, street, player_name)`, and repository connections do not enable foreign-key enforcement, so old
`hand_equity` rows remain attached to the stale id while later saves can add another set under the new id.
`get_equity_history_by_game_hand()` then selects by `game_id`/`hand_number`, takes the first `hand_history_id`, and
returns all matching rows, mixing stale and current snapshots under one reconstructed history.
- **Files:** `poker/repositories/hand_history_repository.py:16-58` (`INSERT OR REPLACE` and returned id),
  `poker/repositories/schema_manager.py:618-665` (`hand_history` natural-key unique and `hand_equity` surrogate-id
  uniqueness), `poker/repositories/base_repository.py:180-185` (foreign keys not enabled),
  `poker/repositories/hand_equity_repository.py:16-51,111-165` (snapshot save/load behavior).
- **Why confirmed:** the audit's INSERT/REPLACE table checklist treats `hand_history` as safe because all columns are
  written, but this is a separate delete/reinsert id-stability bug affecting child rows.
- **Impact:** analytics that use equity histories, suckout/cooler detection, pressure stats, or downstream coaching can
  double count or blend old/new equity snapshots for a hand that was re-persisted after analysis.
- **Fix sketch:** upsert `hand_history` with `ON CONFLICT(game_id, hand_number) DO UPDATE` so `id` is stable, or make
  `hand_equity` unique by `(game_id, hand_number, street, player_name)` and clean old snapshots before re-save. Enable
  foreign keys per connection and add a regression that re-saving a hand then equity history leaves exactly one
  snapshot per street/player.

#### ⬜ 63. DB-backed session prompt stats overcount raise amounts
DB-backed session memory claims to query accurate persistent stats for AI prompts, but `get_session_stats()`
sums raw `RecordedAction.amount` for each player. The canonical `RecordedHand.get_player_contributions()`
helper explicitly documents that `raise` and `bet` amounts are raise-to snapshots, not increments, and tracks
per-phase deltas to avoid overcounting. The persistent prompt path bypasses that normalization.
- **Files:** `poker/memory/session_memory.py:283-299`,
  `poker/repositories/hand_history_repository.py:271-344,368-424`, `poker/memory/hand_history.py:178-187`.
- **Why confirmed:** raw action sums are used for `total_winnings` and recent `Lost $...` summaries, while the
  hand-history model already identifies that naive summing overstates raises.
- **Impact:** after persistence/cold-load, AI prompts can show bogus `Up $...` / `Down $...` and recent loss
  amounts, distorting memory-fed decisions and commentary.
- **Fix sketch:** compute per-player contributions with `RecordedHand.get_player_contributions()` or reuse its
  per-phase delta normalization inside `get_session_stats()`. Add a prompt-context regression with blind, call,
  raise-to, and later-street bet actions.

### S3 (realtime activity feed)

#### ⬜ 99. Timestamp-only world-event cursor can permanently drop same-tick bursts beyond 20 events
The realtime lobby ticker emits only activity rows with `created_at > _last_marker[owner_id]`, scans only the newest
`WORLD_EVENT_LIMIT = 20` rows from the in-memory activity ring, then advances the marker to `fresh[0].created_at`.
That assumes the scan contains every event with that timestamp. It does not: one `refresh_unseated_tables()` call
uses a single `now` for the whole sandbox refresh, and the join/leave, hand, carry, vice, and hustle emitters stamp
every row in their batch with `now.isoformat()`. A busy tick can append more than 20 rows with the same timestamp;
the newest 20 emit, the older same-timestamp rows are outside the scan, and the next tick filters them out because
the strict `>` marker is already equal to their timestamp.
- **Files:** `flask_app/services/ticker_service.py:47,627-635` (20-row scan, strict timestamp cursor, marker set to
  the newest scanned row), `cash_mode/activity.py:255-296` (ring buffer has no sequence id; `recent_events` returns
  newest append order and applies the limit), `cash_mode/lobby.py:1063-1066,2038-2058` (one timestamp is shared
  across the full sandbox refresh and per-table emitters), `cash_mode/lobby.py:3205-3248,3375-3405,3640-3685,
  3858-3964,4019-4057,4109-4147` (batch emitters reuse `ts = now.isoformat()` for many rows).
- **Why confirmed/history:** the async ticker narration log explicitly called out the timestamp-marker trap
  (`docs/captains-log/scaling-stage1/async-ticker-narration.md:32-38`), and `_narrate_and_emit()` now avoids it with
  a fresh timestamp. The general emit path still has a same-timestamp overflow variant of the same cursor bug.
  `tournament_ticker.events_from_beats()` shows the intended mitigation by microsecond-staggering same-tick beats
  (`flask_app/services/tournament_ticker.py:91-103`).
- **Impact:** the socket `world_event` feed can silently miss part of a busy world tick: mass live-fill/leave churn,
  multi-table hand summaries, carry resolutions, or off-grid return/start beats may never reach mounted lobby clients.
  The activity ring may still contain some of them for later REST polling, but the realtime cursor has made the skipped
  rows ineligible for future socket emission.
- **Fix sketch:** replace the timestamp-only cursor with a stable `(created_at, sequence)` or monotonically increasing
  in-memory event id, and emit all rows after that cursor in chronological order up to the per-tick budget without
  marking unscanned equal-timestamp rows as delivered. Alternatively, microsecond-stagger every batch emitter and set
  the marker to the max emitted timestamp, with a regression that records 21 same-timestamp events and proves all 21
  eventually emit.

### S3 (frontend / backend protocol drift)

#### ⬜ 64. REST cold-load game-state drops socket metadata the frontend treats as authoritative
A resumed/cold-loaded game can sit on the REST `/api/game-state/<id>` shape until the next progression event.
That response omits fields the socket serializer sends and the frontend store treats as authoritative:
`hand_number`, `newly_dealt_count`, `awaiting_action`, `run_it_out`, `fast_forward`, `ai_instant`, and
`always_fast_forward`. `join_game` only emits `player_joined` for already-started games, so it does not guarantee
an immediate socket state frame to repair the missing REST metadata.
- **Files:** `flask_app/routes/game_routes.py:1346-1377,2494-2522`,
  `flask_app/handlers/game_handler.py:659-707`, `react/react/src/hooks/usePokerGame.ts:588-615`,
  `react/react/src/types/game.ts:69-103`, `react/react/src/stores/gameStore.ts:227-235,327-335`,
  `react/react/src/hooks/handSequencer.ts:116-128`, `react/react/src/constants/presentationTiming.ts:30-37`.
- **Why confirmed:** REST applies directly to the store, where missing `hand_number` becomes `undefined` and
  missing speed flags default false; socket state has the fields but may not arrive immediately after join/resume.
- **Impact:** hand-scoped UI can lose transitions or tag cash-world events with `NaN`/wrong hand, and fast-forward
  controls can appear in always-fast-forward or all-instant games until the next socket update.
- **Fix sketch:** make REST use the same public serializer as socket `update_game_state`, including hand/runout/speed
  metadata, excluding only intentionally event-only payloads. Add a cold-load contract test comparing REST and
  socket-visible keys.

#### ⬜ 105. Navigating between `/game/:gameId` routes leaves stale socket frames able to overwrite the new game
The `/game/:gameId` route renders the same `GamePage` component without a key, and `GamePage` passes the route param
through to `ResponsiveGameLayout`. `usePokerGame` opens a socket and joins the provided game id, but its initialization
effect does not return cleanup tied to `providedGameId`; socket disconnect happens only when the hook unmounts. If the
same mounted route changes from one game id to another, the old socket can remain joined to the old room. The
`update_game_state` listener accepts any payload with `game_state`, and the backend payload omits `game_id`, so late
frames from the old room are indistinguishable from the current game's frames.
- **Files:** `react/react/src/App.tsx:630-641` (same unkeyed route element),
  `react/react/src/components/game/GamePage.tsx:11-12,65-71` (route param passed to layout),
  `react/react/src/hooks/usePokerGame.ts:287-305` (socket state updates accepted without game-id check),
  `react/react/src/hooks/usePokerGame.ts:688-747,775-780` (new socket on `providedGameId`, cleanup only on unmount),
  `flask_app/handlers/game_handler.py:700-709` (socket `update_game_state` payload has no `game_id`).
- **Why confirmed:** the listener cannot filter stale room frames because the payload does not identify its game, and
  the hook lifecycle does not disconnect/leave the previous room as part of a game-id change.
- **Impact:** navigating between game URLs can let an old table overwrite the shared frontend game store for the new
  table, mixing players, messages, action options, tournament/cash metadata, and chip state. The user can see stale
  controls or act from a wrong-table state until a newer frame or refresh repairs it.
- **Fix sketch:** disconnect or leave the previous socket room whenever `providedGameId` changes, reset game-specific
  store/sequencer state during the transition, and include `game_id` in all game-room socket payloads so listeners
  discard mismatched frames. Add a frontend/socket regression that simulates a route-param change and a late old-room
  update.

#### ⬜ 84. Admin personality CRUD breaks for names with URL delimiters
The admin create modal accepts any trimmed, non-duplicate name. AI generation persists names through JSON body, so names
containing `/`, `?`, or `#` can be created. Later admin save, delete, and avatar-description calls interpolate
`selectedName` directly into `/api/personality/<name>` URLs without encoding, while the Flask routes use single-segment
`<name>` path parameters. Existing avatar image/reference-image calls use `encodeURIComponent`, proving this is not a
uniform frontend convention.
- **Files:** `react/react/src/components/admin/PersonalityManager/CreateModal.tsx:23-35` (name validation),
  `react/react/src/components/admin/PersonalityManager/PersonalityManager.tsx:204-209,231-234,344-349` (raw URL
  interpolation), `react/react/src/components/admin/PersonalityManager/AvatarImageManager.tsx:31-116` (neighboring
  encoded calls), `flask_app/routes/personality_routes.py:244-370,775-858` (single-segment path routes plus JSON-body
  generation).
- **Why confirmed:** a name like `AC/DC` becomes an extra path segment; `Foo?bar` becomes a query string; and `Foo#bar`
  is truncated as a browser fragment before reaching Flask.
- **Impact:** generated or manually staged personalities with delimiter names can become stranded: the admin UI can
  create/regenerate them, but cannot reliably save, delete, or edit avatar descriptions for them.
- **Fix sketch:** encode every path-segment use of personality names, or preferably route mutations by stable
  `personality_id`. Add frontend/unit or route-contract tests for `/`, `?`, and `#` names.

#### ⬜ 85. Experiment detail assistant omits credentials on admin-only chat endpoints
The experiment blueprint registers an admin guard for all routes, including `/api/experiments/<id>/chat`,
`/chat/history`, and `/chat/clear`. The experiment-detail assistant in `AdminRoutes.tsx` calls those endpoints with
bare `fetch` and no `credentials: 'include'`. In development and LAN mode `config.API_URL` is cross-origin, so browser
cookies are not sent; other admin helpers use `adminFetch` or explicit credentials. Clear also drops local messages
without checking server success.
- **Files:** `react/react/src/components/admin/AdminRoutes.tsx:100-151` (bare fetch calls),
  `react/react/src/config.ts:5-9` (cross-origin dev API URL), `react/react/src/utils/api.ts:11-25` (credentialed admin
  helper), `flask_app/routes/experiment_routes.py:19-27,2016-2135` and `flask_app/route_utils.py:8-19` (admin guard and
  chat endpoints).
- **Why confirmed:** the chat endpoints require the same session/admin cookies as the rest of the admin API, but the
  frontend does not send them off-origin.
- **Impact:** on the configured dev/LAN setup, opening or using the detail assistant can fail auth even while the admin
  page itself is authenticated. The clear action can also wipe the local panel while server-side chat history remains.
- **Fix sketch:** use `adminFetch` for the three assistant calls, include credentials, and only clear local state after a
  successful server response. Add a frontend regression or mocked fetch assertion for credentialed requests.

#### ⬜ 35. REST `/api/game-state` can show action buttons during run-it-out or non-betting phases
The Socket.IO state payload starts from `game_state.to_dict()` and then clears `player_options` through
`should_clear_player_options()` for run-it-out, SHOWDOWN, HAND_OVER, EVALUATING_HAND, and GAME_OVER. The
REST `/api/game-state` payload is hand-built and sends raw `game_state.current_player_options`, while omitting
`awaiting_action` and `run_it_out`. A refresh/poll during all-in run-out or between-hand/showdown phases can
therefore resurrect legal-looking buttons even though the backend will reject the action as not awaiting or
run-it-out.
- **Files:** `flask_app/routes/game_routes.py:1346-1364` (REST raw options),
  `flask_app/handlers/game_handler.py:659-668` and `poker/game_helpers.py:5-28` (socket clearing).
- **Why confirmed:** current source has two divergent serializers; `current_player_options` itself has no
  phase/run-out guard.
- **Fix sketch:** make REST reuse the same serializer/clear helper or include `awaiting_action`/`run_it_out`
  and clear options identically. Add route-level regression for `run_it_out=True` and `GAME_OVER`.

#### ⬜ 106. Short-stack all-in-only controls open an invalid raise sheet instead of submitting all-in
When a player faces a bet larger than their stack, the engine can expose `['fold', 'all_in']`: `call` requires
`stack > cost_to_call`, while `all_in` remains available for any positive stack. Both desktop and mobile treat this
all-in-only case as a button that opens the bet/raise sheet. That sheet initializes around the minimum raise-to amount,
which can exceed the short stack's maximum all-in amount, and the confirm button is disabled unless the amount is a
valid raise or exactly the max all-in amount. The user clicks `All-In $stack` but lands in a raise UI instead of
submitting the only legal continue action.
- **Files:** `poker/poker_game.py:268-291` (all-in-only action option shape),
  `react/react/src/components/game/ActionButtons/ActionButtons.tsx:260-266,320-326` (desktop confirm disable and
  all-in button opens raise UI), `react/react/src/components/mobile/MobileActionButtons.tsx:96-101,304-312` (mobile
  confirm disable and all-in button opens raise UI), `react/react/src/hooks/useBettingCalculations.ts:123-125`
  (min/max raise-to calculation can make short all-in below the min raise).
- **Why confirmed:** the frontend comments identify the all-in-only branch and intentionally route it to the raise
  interface, while the calculation and disable predicate make the opened sheet invalid until the amount is manually
  changed to the max all-in.
- **Impact:** short-stack call-off spots are broken or misleading. A player facing a shove or covering bet sees an
  all-in control, taps it, and gets a disabled/invalid raise flow instead of shoving, increasing missed-turn and
  wrong-action risk.
- **Fix sketch:** when `playerOptions` contains `all_in` but not `raise` or `bet`, submit `onAction('all_in',
  safeMaxRaiseTo)` directly, or initialize the sheet to `safeMaxRaiseTo` and label it as all-in rather than raise/bet.
  Add desktop and mobile regressions that the all-in-only button submits the all-in amount.

#### ⬜ 36. Raise table messages double-add `highest_bet` because formatter expects raise-by but callers pass raise-to
The frontend and engine standardize action amounts as **raise TO**. REST and socket action handlers pass the
client amount directly to `play_turn()` and also to `format_action_message()`. The formatter still documents
and implements raise **BY** semantics by adding `highest_bet + amount`, so a player facing $50 who raises to
$100 is announced as raising to $150. That pollutes visible table chat and any prompt/history path that reads
those persisted table messages.
- **Files:** `flask_app/handlers/message_handler.py:23-33` (raise-by formatter),
  `flask_app/routes/game_routes.py:2055-2056,2092-2095,2577-2578,2601-2604` (raise-to amount passed through),
  `poker/poker_game.py:542-576` (engine raise-to semantics).
- **Why confirmed:** source semantics diverge; it manifests whenever `highest_bet > 0`.
- **Fix sketch:** change the formatter to treat raise amount as raise-to for `raise`, or pass
  `amount - highest_bet` only to the formatter. Add direct formatter and route-level message tests.

#### ⬜ 37. Frontend requires `small_blind`, but backend game-state payloads only send `big_blind`
`GameState.small_blind` is a required frontend field and the header formats `blinds.small`. Backend REST sends
`big_blind` only, and the Socket.IO serializer also adds `big_blind` only. Fixtures include `small_blind`, so
tests can pass while real backend state can render `Blinds $undefined/...` after hydration.
- **Files:** `react/react/src/types/game.ts:69-83`,
  `react/react/src/components/game/GameHeader/GameHeader.tsx:45`,
  `flask_app/routes/game_routes.py:1346-1364`,
  `flask_app/handlers/game_handler.py:667-668`.
- **Why confirmed:** required frontend contract field is omitted by both backend payload builders.
- **Fix sketch:** add `small_blind: game_state.current_ante // 2` (or a source-of-truth blind config value)
  to REST and socket payloads, or make the frontend derive it from `big_blind` when absent. Add a contract
  test using real backend payload shape.

#### ⬜ 46. Mounted `/tournament` Main Event page still posts the removed `/api/tournament/register` route
The backend route module states the old synthetic-field `/register` route was removed and current human MTT
entry is the invite/accept/sit lifecycle. The legacy React tournament page is still mounted at `/tournament`,
the tournament menu still navigates there via its "Main Event" button, and that page still calls
`tournamentApi.register()` which posts `/api/tournament/register`. The newer cash-lobby Main Event card uses the
current invite endpoints, but the visible menu path is stale.
- **Files:** `flask_app/routes/tournament_routes.py:1-18,171-250,328-496` (no `/register`; current lifecycle),
  `react/react/src/components/tournament/api.ts:33-40` (stale register client),
  `react/react/src/components/tournament/TournamentPage.tsx:76-82` (button path calls register then sit),
  `react/react/src/components/menus/TournamentMenu.tsx:349-367` and `react/react/src/App.tsx:568-692`
  (page reachable/mounted).
- **Why confirmed:** local route decorators contain no `/api/tournament/register`, while the frontend still
  posts it from a reachable protected page.
- **Impact:** users who enter Main Event through the tournament menu get a 404/failed registration instead of
  the real circuit invite flow, while users entering from the cash lobby see a different working path. This is a
  visible product fork and can drive users into the obsolete manual `/advance`/`play-out` page state.
- **Fix sketch:** remove or redirect the `/tournament` page/menu entry to the cash lobby Main Event card, or
  rewire it to `GET /api/tournament/lobby`, `POST /api/tournament/invite/accept`, and
  `POST /api/tournament/<id>/sit`. Delete the stale `register` client/types/tests once no caller remains.

#### ⬜ 47. Tournament-complete socket payload drops payout fields the result UI renders
`build_completion_result()` computes `prize_pool`, per-place `payouts`, `renown_enabled`, and merges payout
amounts/renown into each standings row. The React result type and overlay render those values. The socket emitters
then rebuild a reduced `tournament_complete` payload and omit the top-level purse/payout metadata. The single-table
path also calls `build_completion_result()` without the finalized prize pool after `finalize_tournament()` already
computed the authoritative result.
- **Files:** `flask_app/handlers/tournament_completion.py:104-130` (computed display payout fields),
  `flask_app/handlers/tournament_completion.py:236-246` (reduced socket payload),
  `flask_app/handlers/single_table_tournament.py:141-163` (rebuilds/emits reduced payload),
  `react/react/src/types/tournament.ts:23-31` and
  `react/react/src/components/game/TournamentComplete/TournamentComplete.tsx:92-113` (frontend expects and renders
  purse/prize/renown fields).
- **Why confirmed:** local source shows the result object has the payout metadata before emission, but neither
  `tournament_complete` emitter includes `prize_pool`, `payouts`, or `renown_enabled`.
- **Impact:** tournament end screens can omit the total purse and payout/renown results even after payouts were
  computed and persisted. This makes the draw-economy finish screen disagree with the backend settlement path.
- **Fix sketch:** emit the full result contract (`prize_pool`, `payouts`, `renown_enabled`, and payout-enriched
  `standings`) from both completion paths. In the single-table path, reuse the prize-pool-aware finalized result or
  pass the stored/known prize pool into the rebuilt result instead of defaulting to zero.

### S3 (strategy / rules)

#### ⬜ 116. Coach outs calculator pads incomplete boards with arbitrary deck cards
`_compute_outs()` claims to count cards that improve the player's hand rank, but on flop/turn boards it first pads the
current hand with `deck[:N]` and then pads each candidate test board with the next arbitrary deck cards. That makes the
baseline and candidate scores depend on eval7 deck order rather than only the visible cards plus the candidate out.
`compute_coaching_data()` then surfaces those counts as `outs` and `outs_cards` in coach guidance.
- **Files:** `flask_app/services/coach_engine.py:86-128` (partial boards completed with arbitrary deck slices before
  comparing scores), `flask_app/services/coach_engine.py:824-829` (the resulting count/cards are returned to the coach
  payload).
- **Why confirmed:** for an incomplete board, there is no unique complete-board hand rank to compare. The helper creates
  one by choosing unrelated future cards from the deck order, so a card can be labeled an out because of how the filler
  cards interact, or missed because the filler already made a stronger baseline.
- **Impact:** live coach advice can show materially wrong draw/outs counts, which feeds hand-strength explanation,
  decision coaching, and any prompt text that treats `outs` as factual.
- **Fix sketch:** count outs by evaluating whether each candidate improves the made hand using only currently visible
  cards and poker hand categories, or compute equity/draw classes by simulation rather than a single arbitrary completion.
  Add regressions for common flush, straight, pair, and full-house draw spots on flop and turn.

#### ⬜ 117. Short big blind lowers the preflop call floor below the real big blind
Blinds are posted through `place_bet()`, which caps the posted amount at the blind player's stack. The round's
`highest_bet` is then derived only from actual contributed chips, and voluntary action cost/options price calls from
that value. If the big blind is short and posts less than the configured big blind, later players are allowed to call
only the short all-in amount instead of the full blind.
- **Files:** `poker/poker_game.py:817-818` (setup posts small and big blinds through `place_bet()`),
  `poker/poker_game.py:470-478` (bet amount is capped to the bettor's stack), `poker/poker_game.py:199-204`
  (`highest_bet` is actual max contribution), `poker/poker_game.py:267-291` (cost/options price from `highest_bet`).
- **Why confirmed:** with BB=50, SB=25, and a big blind stack of 20, the big blind contributes 20 all-in, so the next
  voluntary actor sees `highest_bet=25` from the small blind rather than a 50-chip call floor.
- **Impact:** short-stack tournament/cash hands can let non-blind players enter for less than the required big blind and
  derive too-low raise targets, corrupting preflop pot geometry and downstream memory/strategy labels.
- **Fix sketch:** track a preflop call floor separate from actual chip contributions. Non-blind callers should owe the
  configured big blind even when the blind posted short, while side-pot math continues to use actual contributions.
  Add short-blind regressions for unopened, called, and raised pots.

#### ⬜ 118. A player can raise when every opponent is already all-in
The state machine intentionally keeps a lone non-all-in player waiting when they still owe chips against all-in
opponents, so they can call or fold. But `current_player_options` permits `raise` whenever that player has chips beyond
the call and the raise cap is open; it does not check whether any non-folded opponent can contest additional chips.
- **Files:** `poker/poker_state_machine.py:271-283` (run-it-out waits while the only actor owes a call),
  `poker/poker_game.py:272-291` (raise option depends on stack/cap, not on a contesting non-all-in opponent).
- **Why confirmed:** in a shape with one opponent all-in for 100 and the hero holding 1000 while owing 100, the option
  logic emits `fold`, `call`, `raise`, and `all_in` even though no opponent can call more chips.
- **Impact:** the UI/backend can allow illegal betting into an uncontested side pot. Even if later settlement returns
  extra chips, action history, pressure/memory stats, coach analysis, and table messages record an impossible action.
- **Fix sketch:** when no other non-folded, non-all-in opponent can match more chips, suppress `raise` and suppress
  over-call `all_in`; keep all-in only for a short call. Add regressions for single all-in opponent, multiway with one
  live caller, and all opponents all-in.

#### ⬜ 119. Core accepts non-positive blind/bet amounts despite claiming it rejects them
`place_bet()` documents a `ValueError` for amounts less than or equal to zero, but the implementation does not perform
that check before subtracting chips and adding to the pot. `initialize_game_state()` also stores `big_blind` directly as
`current_ante` and `last_raise_amount`. A zero blind can create zero-chip raises/action loops, and a negative blind or
bet can increase stacks and write negative pot contributions.
- **Files:** `poker/poker_game.py:456-457` (documented invalid amount behavior), `poker/poker_game.py:470-487`
  (implementation subtracts/adds `amount` without validating it), `poker/poker_game.py:791-795` (incoming `big_blind`
  becomes `current_ante`/`last_raise_amount` directly), `poker/poker_game.py:817-818` (blind posting uses that value).
- **Why confirmed:** the core path has no positive-integer guard at either game creation or bet placement. Negative
  amounts invert the stack/pot update math; zero amounts can still be treated as raises in paths that increment action
  state separately.
- **Impact:** invalid game configs or direct core callers can produce stack inflation, negative pots, corrupted blind
  state, or non-progressing betting rounds.
- **Fix sketch:** validate `big_blind` and bet/raise/blind amounts as positive integers at core boundaries, and reject
  zero except for explicit check/no-op semantics that do not call `place_bet()`. Add core tests for negative/zero blinds,
  negative bets, and zero raises.



### S2 (cash presence / off-grid authority)

#### ⬜ 120. Off-grid vice/hustle writers still fail open under live presence authority
The Presence cutover docs now say `entity_presence` is the live seat/idle/whereabouts authority, but the
side-hustle and vice start/end paths still route through the old Phase-1 `presence_shadow.shadow_transition()`
helper. That helper is enabled when either the shadow or authority flag is on, yet it always catches and
swallows every transition failure. The off-grid modules explicitly rely on that: a successful `ai_vice_state` or
`ai_side_hustle_state` insert can leave `entity_presence` unchanged when `START_VICE` / `START_HUSTLE` is illegal
from the current presence state. The lobby has targeted guards for known re-seat paths, which shows the split-brain
is real, but those guards are compensating for a live authority path that still fails open.
- **Files:** `cash_mode/presence_shadow.py:55-72,75-121` (authority flag enables the helper, but all failures are
  swallowed), `cash_mode/ai_side_hustle.py:499-521,586-598` and `cash_mode/ai_vice_spending.py:698-706,1258-1271`
  (off-grid row writes mirror through that helper), `cash_mode/lobby.py:2107-2120` and
  `tests/test_cash_mode/test_offgrid_reseat_guard.py:1-12` (current workaround says presence authority cannot catch
  `START_VICE` from `SEATED`), `tests/test_cash_mode/test_shadow_offgrid.py:263-290` (tests preserve successful
  off-grid rows with unchanged presence as expected divergence).
- **Why confirmed/history:** the captains-log says presence is now the live write/read authority for seats and idle,
  while current off-grid writers still carry the earlier shadow-phase semantics. Existing tests document the exact
  illegal-and-swallowed transition instead of failing it under authority.
- **Impact:** a missed guard, stale row, boot/expansion path, or future off-grid call site can create an AI that is
  active in `ai_vice_state` / `ai_side_hustle_state` while presence still says `SEATED`, `OFFLINE`, or `IDLE`. The
  lobby then has to rely on scattered repo filters rather than the authoritative state machine, reopening
  `seated_and_offgrid` and whereabouts/candidate-list contradictions.
- **Fix sketch:** split shadow from authority for off-grid transitions. Under `PRESENCE_AUTHORITY_ENABLED`, call a
  fail-closed transition path that sequences legal precursors (`SEATED` leaves first, missing idle seeds/returns as
  intended) or aborts the off-grid row write in the same sandbox lock. Keep best-effort swallowing only for dormant
  shadow mode, and update the off-grid tests to expect authority-mode failures or legal precursor transitions.

### S2 (LLM spend / retention)

#### ⬜ 121. DeepSeek and Mistral silently multiply retries beyond the app retry budget
`LLMClient.complete()` owns a central retry loop of up to three provider attempts. OpenAI, Groq, and xAI explicitly
construct their OpenAI-compatible SDK clients with `max_retries=0` so SDK retries do not stack under that loop. The
DeepSeek and Mistral adapters also use the OpenAI-compatible SDK, but omit `max_retries=0`, so the SDK default retry
policy can run inside every app-level attempt.
- **Files:** `core/llm/client.py:187-214` (central retry loop), `core/llm/providers/openai.py:44-53` and
  `core/llm/providers/groq.py:62-70` (sibling adapters disable SDK retries), `core/llm/providers/deepseek.py:89-93`
  and `core/llm/providers/mistral.py:51-55` (no `max_retries=0`).
- **Why confirmed:** provider implementations use the same SDK family, but only two adapters lack the retry-disable
  option that the others document as necessary to keep timeouts bounded.
- **Impact:** during provider timeouts, 5xxs, or rate-limit failures, DeepSeek/Mistral calls can exceed the app's
  intended retry/timeout budget, making debug/admin/casino LLM paths stall longer and potentially issuing extra paid
  outbound calls.
- **Fix sketch:** set `max_retries=0` on DeepSeek and Mistral OpenAI-compatible clients. Add a provider regression or
  static contract test asserting every OpenAI-compatible adapter disables SDK retries and relies on `LLMClient`.

#### ⬜ 122. Admin text debug/replay/experiment calls bypass per-owner LLM budgets
The per-owner LLM budget gate only runs when an `owner_id` is supplied. The prompt debug replay route, prompt
interrogation `Assistant`, prompt interrogation `chat_full`, admin playground text replay, and experiment
assistant/analysis calls all make paid text calls without passing the current admin's id. Neighboring image replay
paths and experiment chat persistence already resolve the admin/owner id, showing the intended attribution is
available.
- **Files:** `core/llm/budget.py:157-164` (owner budget gated on non-empty `owner_id`),
  `flask_app/routes/prompt_debug_routes.py:232-236,337-345,386-392` (debug text calls omit owner),
  `flask_app/routes/admin_dashboard_routes.py:309-313` (admin playground text replay omits owner),
  `flask_app/routes/admin_dashboard_routes.py:575-580` (image replay comparison path resolves admin owner id),
  `flask_app/routes/experiment_routes.py:280-288,1642-1653,2072-2078` (experiment analysis/design chat/assistant
  text calls omit owner), `flask_app/routes/experiment_routes.py:1712-1714` (same flow resolves owner for chat
  persistence after the paid call).
- **Why confirmed:** these routes are admin/debug/experiment surfaces, but their text calls are recorded with no owner
  budget key while sibling paid image calls and persistence paths intentionally attribute work to the admin.
- **Impact:** authenticated admins can run expensive text replay/interrogation/experiment workflows while
  `LLM_PER_OWNER_DAILY_BUDGET_USD` and per-admin cost attribution never apply. The global cap still applies, but
  owner-level throttling, reporting, and alerts are bypassed.
- **Fix sketch:** resolve `auth_manager.get_current_user()` / `_get_chat_owner_id()` in these text tooling routes and
  pass `owner_id` into `LLMClient.complete()`, `Assistant(...)`, and `chat_full()`. Add tests where the owner budget
  is exceeded but the global budget is not, and assert these debug/experiment calls are rejected.


#### ⬜ 123. Prompt-capture retention skips legacy `NULL` call-type captures forever
The capture config describes finite retention as deleting captures older than N days. The cleanup query deletes only
rows where `call_type IS NOT NULL`; migrated historical captures can still have `NULL` because migration v39 added the
column nullable and copied old rows without backfilling it. The stats path still treats those legacy null-call-type
rows as real captures by coalescing them to `player_decision`, so they remain visible data but are never eligible for
retention cleanup.
- **Files:** `core/llm/capture_config.py:19-21` (retention contract),
  `poker/repositories/schema_manager.py:3414-3420,3450-3469,3473-3485` (nullable `call_type`, old rows copied without
  the column), `poker/repositories/prompt_capture_repository.py:572-579` (legacy rows counted as captures),
  `poker/repositories/prompt_capture_repository.py:602-623` (cleanup excludes `call_type IS NULL`).
- **Why confirmed:** this is distinct from #77's replay FK/cascade issue: even captures with no replay links are spared
  by the retention predicate solely because they are legacy null-call-type rows.
- **Impact:** finite retention settings can leave old prompt/response data in the DB indefinitely, undermining the
  intended privacy/storage control and producing storage drift that the admin stats still count.
- **Fix sketch:** remove `call_type IS NOT NULL` from the retention delete, or explicitly include legacy null rows.
  Add a cleanup regression with old `NULL` and non-null `call_type` captures and assert both are purged while recent
  rows survive.

### S2 (tournament internals)

#### ⬜ 124. MTT live human hands are not counted in tournament `total_hands`
`TournamentSession` treats `_hand_counter` as the count of unique hands played and completion reports that value as
`total_hands`. Synthetic hands increment `_hand_counter` inside `_play_hands()`, but the live-human table path folds
an externally played hand through `_apply_result()` and never increments the counter. Every live MTT hand therefore
advances cards/stacks/eliminations without contributing to persisted completion totals.
- **Files:** `tournament/session.py:344-357` (live hand boundary entry point), `tournament/session.py:373-381`
  (human result path calls `_apply_result` instead of `_play_hands`), `tournament/session.py:413-465` (`_apply_result`
  reconciles stacks and advances the button without incrementing), `tournament/session.py:467-478` (synthetic hands
  do increment), `flask_app/handlers/tournament_completion.py:126-133` (`total_hands` emitted from `_hand_counter`).
- **Why confirmed:** the code path for live table hands bypasses the only counter increment, while the completion
  payload exposes that counter as the tournament total.
- **Impact:** live MTT results undercount by one for every human-table hand. Completion stats, UI summaries, and any
  downstream analytics keyed to hand volume are wrong for human-driven tournaments.
- **Fix sketch:** increment `_hand_counter` exactly once when applying a live human-table result, preferably in the
  same boundary path that accepts `human_result`. Add a regression that one live hand plus AI-table pacing reports the
  human hand in `total_hands`.

#### ⬜ 125. AI-only tournament rounds still use human pacing jitter
The session doc says 0/1/2 pacing exists to keep AI tables loosely synced when the human plays one hand. The AI-only
`advance_round()` path says it advances the field exactly one round, and the headless `TournamentDirector` defines a
round as one hand at every eligible table. But `_round()` uses `PACING_CHOICES` for every non-human table even when no
human hand/result is supplied, while still incrementing the round/blind clock.
- **Files:** `tournament/session.py:1-12` (0/1/2 pacing tied to a human hand), `tournament/session.py:328-342`
  (`advance_round` AI-only contract), `tournament/session.py:372-381,409-410` (jittered 0/1/2 table hand count with
  one round tick), `tournament/director.py:1-6,200-204` (headless director's one hand per table per round model).
- **Why confirmed/history:** the captains-log records the autonomous ticker as incremental AI-only tournament play;
  current code reuses the live-human jitter path instead of the headless one-hand-per-table cadence.
- **Impact:** autonomous, post-bust, or fast-forward tournaments can tick blinds/max-rounds while some tables play zero
  hands, and can let other tables play two hands. That distorts eliminations, blind pressure, and completion timing
  versus the documented AI-only engine.
- **Fix sketch:** split `_round()` by mode: preserve 0/1/2 jitter only when advancing alongside a human hand, and use
  exactly one synthetic hand per eligible table for AI-only `advance_round()` / `play_out()`. Add an AI-only session
  regression that counts one hand per active table per round.

#### ⬜ 126. Tournament AI renown grants drop the latest v2 component breakdown
Tournament AI renown grants are intended to clone the latest reputation snapshot fields and bump only `renown_v2`.
The grant code loads the latest AI snapshot and carries regard/quadrant/cuts, but always passes `components=None` into
`record_ai_many()`. That repository serializes `components` to `renown_v2_components`; lobby reputation payloads expose
that breakdown for v2 explainability.
- **Files:** `flask_app/services/tournament_renown.py:96-116` (`latest` loaded, but `components` set to `None`),
  `poker/repositories/prestige_snapshots_repository.py:130-169` (`components` serialized to `renown_v2_components`),
  `flask_app/routes/cash_routes.py:5296-5305` (payload exposes v2 driver breakdown).
- **Why confirmed:** a tournament grant row becomes the newest snapshot for that AI, but it writes a null components
  payload even when the previous latest snapshot had one.
- **Impact:** after an AI cashes in a tournament, the lobby/admin reputation UI can show the increased uncapped renown
  with no v2 driver breakdown until another full recompute overwrites the row.
- **Fix sketch:** carry `latest['renown_v2_components']` through `_grant_ai`, parsing JSON to a dict when needed before
  passing `components`. Add a regression that a tournament grant preserves component JSON while increasing `renown_v2`.

### S3 (admin UI / mutation feedback)

#### ⬜ 127. Admin batch-save panels treat non-2xx mutation responses as success
`adminFetch()` includes credentials but returns the raw `fetch()` response; it does not throw on `!response.ok` or
`success:false` JSON. The pricing manager builds batches of raw `Promise<Response>` objects, awaits `Promise.all()`,
then clears pending changes and shows success. The capture settings panel repeats the pattern and locally marks
rejected settings as saved. The Flask endpoints explicitly return failed mutations as JSON plus non-2xx statuses for
invalid pricing, missing/deleted pricing ids, invalid setting keys, and invalid retention/capture values.
- **Files:** `react/react/src/utils/api.ts:20-36` (raw `adminFetch`),
  `react/react/src/components/admin/PricingManager/PricingManager.tsx:287-337,347-374` (pricing batches treat fulfilled
  non-2xx responses as success),
  `react/react/src/components/admin/UnifiedSettings/sections/CaptureSection.tsx:65-99` (settings save does the same),
  `flask_app/routes/admin_dashboard_routes.py:1046-1082,1109-1119,1345-1386` (backend returns `success:false` with
  400/404/500 for rejected mutations).
- **Why confirmed:** `fetch()` resolves promises for HTTP 400/404/500; these components never inspect status/body before
  clearing local dirty state.
- **Impact:** admins can get a success toast, lose pending pricing edits, or see capture settings locally updated even
  though the server rejected the write. Pricing/settings remain stale on the backend while the UI says they landed.
- **Fix sketch:** add a small admin JSON mutation helper that checks `response.ok`, parses JSON, and rejects on
  `success === false`; use it in pricing and settings batch paths. Only clear pending changes, close panels, refresh,
  and update local state after every mutation confirms success.


#### ⬜ 128. Admin image playground accepts unbounded URL/upload blobs as global reference assets
The admin image playground can ingest image bytes from three paths without a hard byte cap or URL policy.
Reference-image upload reads either `file.read()` or an arbitrary JSON `url` via
`requests.get(...).content`, validates only magic bytes, opens it with PIL, and stores the blob. Image
replay then downloads `response.url` into memory and base64s it into the JSON response, while assign-avatar
accepts arbitrary base64 `replayed_image_data` and writes it directly to `avatar_images`. The
`reference_images` schema has `owner_id`, but the save path never populates it and lookups read by id only,
so uploaded references become global debug assets.
- **Files:** `flask_app/routes/admin_dashboard_routes.py:392-465` (unbounded upload/remote URL fetch ->
  reference_images), `flask_app/routes/admin_dashboard_routes.py:599-610` (unbounded generated-image
  download -> base64 response), `flask_app/routes/admin_dashboard_routes.py:671-688` (unbounded base64
  decode -> avatar write), `poker/repositories/personality_repository.py:1180-1218` (reference images
  saved/read with no owner or size guard), `poker/repositories/schema_manager.py:1248-1258`
  (`owner_id`/`expires_at` columns exist but are unused by this save/read path),
  `flask_app/routes/image_routes.py:408-440,478-501` (avatar regeneration consumes any reference id by raw
  DB lookup).
- **Why confirmed:** the route buffers the full upload/remote body before validation, uses no Content-Length
  or streamed max-byte enforcement, applies no scheme/host allowlist for URL uploads, and writes the bytes to
  SQLite without filling `owner_id` or checking `expires_at`.
- **Impact:** an admin request can make the backend fetch internal/LAN URLs, buffer/decode oversized images,
  bloat the database with persistent BLOBs, or return huge base64 payloads. The existing admin-CSRF gap (#98)
  makes this reachable through an authenticated admin browser, but the ingestion/size/ownership bug exists
  independently.
- **Fix sketch:** reject URL uploads unless explicitly needed; otherwise enforce `https` plus
  private-network/metadata-host blocking, stream with a small max byte cap, validate actual decoded
  dimensions/pixel count, and store `owner_id`/`expires_at`. Apply the same byte cap to replay downloads and
  assign-avatar base64 payloads, and make reference-image reads owner/admin-scoped.


#### ⬜ 129. Cash lobby marks owner-scoped seats from global active-stake state
`GET /api/cash/lobby` resolves the caller's sandbox, loads only that sandbox's cash tables, and annotates
seat metadata from sandbox-scoped bankroll/emotion/fish data. The active-stake glyph is the exception: it calls
`stake_repo.get_active_personality_participants()` with no owner, sandbox, or table context, then marks any matching
AI seat in the caller's lobby as `in_active_stake`. Because `stakes` is global and the query returns every active
personality borrower/staker, one user's active stake can light up the same public personality in another user's
sandbox lobby.
- **Files:** `flask_app/routes/cash_routes.py:5406-5584` (owner sandbox resolved; tables loaded with
  `sandbox_id`), `flask_app/routes/cash_routes.py:5650-5654` (global active-stake set built),
  `flask_app/routes/cash_routes.py:5792-5797` (seat response gets `in_active_stake` from that global set),
  `poker/repositories/stake_repository.py:596-616` (active participants query has no owner/sandbox/table filter).
- **Why confirmed/history:** the captains-log history repeatedly treats sandbox/presence state as per-owner world
  state, and this lobby route follows that pattern everywhere else. The active-stake query is explicitly global and
  the `stakes` table has no sandbox discriminator, so the response cannot distinguish this sandbox's stake dynamics
  from another user's.
- **Impact:** the lobby can reveal that another user's public AI is currently involved in a live stake and can falsely
  imply stake dynamics in the current player's own sandbox. It is low severity but player-visible and contradicts the
  per-owner cash-world boundary.
- **Fix sketch:** scope active stake annotations to entities seated in this sandbox's live stake sessions, or add a
  durable sandbox/table/session discriminator to stake rows and filter by it. Until stakes are scoped, omit the glyph
  from owner-scoped lobby responses or derive it only from the caller's active session/table.


#### ⬜ 130. Single-table tournament rows can exclude display-name matches from cash seating
The cash lobby's tournament exclusion expects stable cash `personality_id`s from active multi-table tournament fields.
`find_active_for_owner()` already excludes `resolver_kind='single'` rows because they are ordinary single-table games,
but `active_participant_pids()` scans all recent active rows for the owner and returns `field.entries` keys after only
filtering synthetic `P01` and `human:*` ids. Persisted single-table sessions are saved as `resolver_kind='single'`, and
their field entries are built from live player display names. If a display name matches a real cash `personality_id`,
the cash seat-filler treats that persona as "away at the Main Event" and removes it from the caller's cash world.
- **Files:** `poker/repositories/tournament_session_repository.py:112-128` (`find_active_for_owner` excludes
  `single` rows), `poker/repositories/tournament_session_repository.py:130-184` (`active_participant_pids` does not),
  `flask_app/services/tournament_registry.py:244-260` (single-table sessions persisted as `resolver_kind='single'`),
  `flask_app/handlers/single_table_tournament.py:40-59` (single-table field keys are player display names),
  `cash_mode/lobby.py:1223-1238` (cash filler treats returned ids as tournament-bound persona ids).
- **Why confirmed/history:** this is adjacent to the captains-log's schema/read-model drift theme: a multi-table
  participant-id read model is fed single-table display-name rows. The existing 6-hour ghost-seat entry (#89) covers
  stale active rows aging out; this is live `resolver_kind='single'` contamination.
- **Impact:** ordinary in-progress single-table games can suppress unrelated cash personas whose stable id happens to
  equal a player's display name, making lobby seating/whereabouts look wrong for that owner until the game completes or
  the exclusion ages out.
- **Fix sketch:** make `active_participant_pids()` match `find_active_for_owner()` by filtering
  `resolver_kind != 'single'`, or persist stable persona ids for single-table rows and keep them out of the MTT cash
  exclusion. Add a regression with a single-table display-name collision against a cash persona id.

#### ⬜ 131. Admin avatar generation endpoints auto-create missing personalities before paid image calls
Two admin avatar-generation endpoints treat path names as existing personalities, but the validation path is
side-effecting. `regenerate_avatar()` checks existence by calling `personality_generator.get_personality(name)`, which
generates and persists missing names before returning a config, so the 404 branch is unreachable for ordinary unknown
names. `/api/generate-character-images/<name>` skips existence validation entirely; both paths then build avatar prompts
through the same generator and dispatch paid image calls.
- **Files:** `flask_app/routes/image_routes.py:398-441` (`regenerate_avatar` existence check calls the generator,
  then regenerates emotions), `flask_app/routes/image_routes.py:519-549` (`generate-character-images` calls generation
  directly), `poker/personality_generator.py:514-554` (missing names are generated and saved),
  `poker/character_images.py:748-793` (route wrappers pass through to generation/regeneration),
  `poker/character_images.py:451-481` (avatar prompt path calls `get_personality()` and then `generate_image()`).
- **Why confirmed:** `get_personality()` is not a read-only existence check. It creates a public or private
  non-circulating row for unknown non-reserved names, and the image route continues into provider calls with that
  generated config.
- **Impact:** an admin typo or crafted path can create durable personalities and consume image-generation budget for
  names that were never selected from the catalog. This is related to public/reference and custom-game autogeneration
  issues (#54/#79), but the affected admin image endpoints and paid avatar call path are separate.
- **Fix sketch:** validate path names with a non-generating repository lookup using the same owner/admin/catalog
  visibility policy as personality management before any image call. Only call `get_personality()` after a row is known
  to exist, or add an explicit create-and-generate endpoint. Add route regressions for unknown names and disabled/private
  rows.


#### ⬜ 132. User avatar generation bypasses per-owner image spend budgets
Authenticated profile avatar generation has the current user's id, but the service sends it only as `player_name` and
never as `owner_id` when calling `LLMClient.generate_image()`. The image spend gate and usage tracker key owner budgets
and owner spend rows exclusively from `owner_id`, so text-to-avatar and photo-to-avatar calls are charged only against
the global budget even though they are user-initiated and authenticated.
- **Files:** `flask_app/routes/profile_routes.py:172-186,194-222` (profile avatar generation resolves
  `g.profile_user['id']` and calls the service), `poker/user_avatar_service.py:130-160` (passes user id as
  `player_name` into `_generate`), `poker/user_avatar_service.py:196-217` (`generate_image` call omits
  `owner_id`), `core/llm/client.py:395-414,464-485` (budget gate, usage tracking, and prompt capture all use
  `owner_id`), `core/llm/budget.py:157-164` (per-owner limit is skipped when `owner_id` is falsey),
  `tests/test_user_avatar.py:162-199,230-247` (generation tests stub the call but do not assert owner attribution).
- **Why confirmed:** `LLMClient.generate_image()` has an `owner_id` parameter and admin image routes already pass it,
  but the user-avatar service never forwards the authenticated user id. Passing `player_name=user_id` only populates
  the player/name tracking field; it does not drive the spend gate or owner spend query.
- **Impact:** any authenticated guest or Google user can generate profile avatars outside
  `LLM_PER_OWNER_DAILY_BUDGET_USD` and outside per-user image cost reporting/alerts. The global cap still applies, but
  one user can consume shared image budget without tripping their own cap.
- **Fix sketch:** thread `owner_id=user_id` through `generate_from_prompt()`, `generate_from_photo()`, and `_generate()`
  while keeping `player_name` as display/context metadata if needed. Add service or route tests asserting both prompt
  and photo generation pass `owner_id` to `generate_image()` and are rejected when the owner budget is exceeded.


#### ⬜ 133. Admin avatar image tools omit credentials and mask auth failures
The Personality Manager's Avatar Images panel is mounted inside an admin workflow, but its avatar/reference-image
mutations use bare `fetch()` calls to protected endpoints. In development and LAN mode `config.API_URL` points at a
cross-origin backend, so browser cookies are not sent unless `credentials: 'include'` or the shared admin helper is used.
The reference-image setter also updates local `referenceImageId` before the request and never checks `response.ok`, so a
401/403 response can look saved locally until the next reload.
- **Files:** `react/react/src/components/admin/PersonalityManager/AvatarImageManager.tsx:111-125,145-153,183-193,219-228`
  (bare reference/regenerate fetches), `react/react/src/components/admin/PersonalityManager/PersonalityManager.tsx:610-622`
  (admin panel mount), `react/react/src/config.ts:5-9` (cross-origin dev API URL),
  `flask_app/routes/image_routes.py:354-357` (avatar regeneration is admin-only),
  `flask_app/routes/personality_routes.py:467-495` (reference-image update requires auth/owner/admin).
- **Why confirmed:** the backend guards return authentication/authorization failures without the session cookie, while the
  frontend never sends credentials on these requests. This is adjacent to #85's experiment-detail chat credential drift
  and #127's non-2xx admin mutation handling, but the affected avatar/reference-image component and endpoints are distinct.
- **Impact:** admin avatar regeneration and reference-image assignment can silently fail in the default cross-origin dev/LAN
  setup. The reference picker can display a value that was rejected by the server, sending the next image generation or
  maintenance pass down the wrong path.
- **Fix sketch:** route these calls through `adminFetch()` or add explicit `credentials: 'include'`, check `response.ok`
  and `success === false`, and only update local reference-image state after a confirmed save. Add mocked-fetch coverage
  that asserts credentials and failed-response handling for reference and regeneration calls.


#### ⬜ 134. Prompt Playground avatar assignment modal loads protected personalities without credentials
The Prompt Playground avatar assignment modal fetches `/api/personalities` with a bare `fetch()` even though that endpoint
requires an authenticated user. With the configured cross-origin dev/LAN API URL, the session cookie is omitted, the
backend returns `AUTH_REQUIRED`, and the modal quietly leaves its personality list empty because the 401 body has no
`personalities` field.
- **Files:** `react/react/src/components/debug/PromptPlayground/AvatarAssignmentModal.tsx:37-49` (bare personality-list
  fetch), `react/react/src/config.ts:5-9` (cross-origin dev API URL), `flask_app/routes/personality_routes.py:67-75`
  (`/api/personalities` auth requirement), `react/react/src/components/debug/PromptPlayground/PromptPlayground.tsx:563,898`
  (assignment modal entry points).
- **Why confirmed:** the same component successfully reads public avatar emotions with bare fetch, but its personality list
  targets a protected endpoint and never includes credentials or checks `response.ok`.
- **Impact:** an authenticated admin/debug user can open the playground but get an empty assignment dropdown in the normal
  cross-origin setup, making avatar assignment look like missing data rather than an auth failure.
- **Fix sketch:** use the credentialed API/admin helper for the personality fetch, surface a failed load state when the
  response is not OK, and add a frontend regression for credentialed personality-list loading.


#### ⬜ 135. Cash-session `broken` lifecycle events lose sandbox id in recovery paths
The persisted lifecycle event table stores `sandbox_id`, and the admin lifecycle card scopes event counts with
`WHERE sandbox_id = ?`. Normal stale-row sweeps pass the session sandbox when recording a `swept` event, but both recovery
paths that mark a cash session `broken` omit `sandbox_id`. Those rows are persisted with `NULL` scope and disappear from
per-sandbox lifecycle counts even though the warning log says the session needs operator attention.
- **Files:** `poker/repositories/schema_manager.py:6929-6955` (`cash_session_events.sandbox_id`),
  `poker/repositories/cash_session_repository.py:441-467` (`event_counts(..., sandbox_id=...)` filters by sandbox),
  `flask_app/routes/chip_ledger_routes.py:154-164` (admin lifecycle counts pass the sandbox arg),
  `flask_app/routes/cash_routes.py:4288-4310` (leave-failure `broken` event omits sandbox),
  `cash_mode/lobby.py:4438-4498` (sweep success passes sandbox; sweep-failure `broken` omits it).
- **Why confirmed/history:** the captains-log cash history treats seat/lifecycle state as sandbox-scoped operational truth,
  and the schema comment says this table backs orphan/lifecycle ops queries. The `broken` paths violate that same scoped
  telemetry contract.
- **Impact:** global ops logs still show broken sessions, but the per-sandbox admin counter under-reports the exact sessions
  an operator is likely trying to diagnose. A broken session in a busy deployment can be invisible from the scoped lifecycle
  view.
- **Fix sketch:** carry `sandbox_id` from the loaded cash session or cash row into every `_emit_cash_session_event(...,
  "broken", ...)` call. Add repository/route coverage proving `broken` counts appear under the affected sandbox and not only
  in unscoped totals.


#### ⬜ 136. Boot orphan-seat cleanup treats global owner cash rows as backing sandbox-local seats
`kill_all_cash_sessions()` can reconcile orphan human seats for one sandbox, and it correctly loads tables with
`list_all_tables(sandbox_id=sandbox_id)`. Its proof that a human seat is still backed by a resumable game is not
sandbox-scoped: it builds `owners_with_cash_row` from all `cash-*` games across all owners/sandboxes, then skips cleanup
when the seat's owner id appears anywhere in that global set.
- **Files:** `cash_mode/lobby.py:4510-4528` (`kill_all_cash_sessions` boot reconcile with optional sandbox),
  `cash_mode/lobby.py:4567-4592` (stale sweep precedes orphan-seat reconcile for that sandbox),
  `cash_mode/lobby.py:4596-4605` (global `list_games` owner set plus sandbox-scoped table list),
  `cash_mode/lobby.py:4610-4625` (owner-only skip leaves the seat intact).
- **Why confirmed/history:** `docs/captains-log/development/cash-coldload-seat-orphan.md` records this exact bug class:
  table ids are per-sandbox and cold-load divergence can leave a human seat stranded. The current cleanup reintroduces a
  cross-sandbox proof step into an otherwise sandbox-scoped repair.
- **Impact:** if owner A has a valid cash game in sandbox X and an orphan human seat in sandbox Y, the sandbox-Y cleanup sees
  `owner A has a cash row` and preserves the orphan. The Y lobby can keep showing the seat/chips as occupied even though no
  resumable game exists for that sandbox.
- **Fix sketch:** key surviving cash rows by `(owner_id, sandbox_id)` or game id/table id, not owner id alone. Add a boot
  reconcile regression with one owner active in sandbox X and orphaned in sandbox Y, and assert only the Y seat is refunded
  and cleared.


#### ⬜ 137. Stalled-variant resume endpoint returns success while its background call crashes
`POST /api/experiments/<experiment_id>/stalled/<game_id>/resume` starts a background thread and immediately returns
`success: true`, but the thread calls `resume_variant_impl(experiment_repo=...)`. The helper signature requires `db_path`
as its first positional/keyword argument and has no `experiment_repo` parameter, so the thread raises `TypeError` before
any tournament resumes. The route's user-visible response is therefore disconnected from the actual resume outcome.
- **Files:** `flask_app/routes/experiment_routes.py:3264-3278` (thread starts and route returns success),
  `flask_app/routes/experiment_routes.py:3285-3310` (background call passes the wrong keyword),
  `experiments/resume_helpers.py:147-154` (actual helper signature),
  `flask_app/routes/experiment_routes.py:3331-3336` (error is logged, heartbeat set idle, lock released after the route
  already reported success).
- **Why confirmed/history:** the tournament captains-log emphasizes one resume/completion authority and load-bearing
  completion paths. This route never reaches that authority because the call boundary drifted from the helper.
- **Impact:** the UI/API can tell an admin a stalled variant is resuming when no hand is run and no result is produced.
  A truly stalled experiment can remain stuck while its heartbeat is reset to idle, obscuring the failure mode.
- **Fix sketch:** call `resume_variant_impl(db_path=extensions.persistence_db_path, ...)` or make the helper accept an
  injected repository intentionally. Move the success response to a durable queued/resuming state, and add a route test
  that exercises the background target signature or a synchronous helper wrapper.


#### ⬜ 138. Whole-experiment resume ignores typed `HandResult` reset/pause states
The whole-experiment resume background path manually reimplements a tournament loop, but it still treats
`runner.run_hand()` as if it returned old string/boolean statuses. `run_hand()` now returns a truthy `HandResult` dataclass
with `.needs_reset`, `.is_paused`, and `.is_end`. The manual route loop compares the object to the string
`"reset_needed"` and checks `elif not hand_result`, so reset, pause, and end statuses are not handled like the canonical
runner loops.
- **Files:** `flask_app/routes/experiment_routes.py:3096-3157` (manual resume loop checks string/falsy results),
  `flask_app/routes/experiment_routes.py:3167-3174` (marks the experiment complete when pause was missed),
  `experiments/run_ai_tournament.py:95-124,1138-1172` (`HandResult` type and return contract),
  `experiments/run_ai_tournament.py:1690-1774,1949-2026` (canonical loops use `.needs_reset`, `.is_paused`, `.is_end`).
- **Why confirmed/history:** the captains-log tournament work explicitly moved toward one wrapper/one completion path, but
  this route is a stale duplicate loop that missed the typed result migration.
- **Impact:** a resumed experiment can ignore `reset_on_elimination`, miss a user pause, continue after terminal states, and
  then call `_complete_experiment_with_summary()` on an invalid or partially paused run.
- **Fix sketch:** delete the bespoke route loop in favor of `AITournamentRunner._continue_tournament()`/shared helper, or at
  minimum handle the `HandResult` properties exactly like the canonical loops. Add resume tests for reset-needed, paused,
  and end statuses.


#### ⬜ 139. Resumed experiment tournaments never write the DB completion authority
Experiment recovery defines an incomplete tournament as an `experiment_games` row with no matching `tournament_results`
row. Fresh tournament runs write that row before returning, but `_continue_tournament()` only builds a `TournamentResult`,
marks the heartbeat idle, releases the resume lock, and returns. The stalled-variant route then calls `_save_result()`,
which writes a JSON file, not the `tournament_results` database row that recovery queries.
- **Files:** `poker/repositories/experiment_repository.py:696-716` (`get_incomplete_tournaments` uses
  `LEFT JOIN tournament_results ... tr.id IS NULL`), `experiments/run_ai_tournament.py:1868-1878` (fresh run writes
  `save_tournament_result`), `experiments/run_ai_tournament.py:1892-1905,2075-2097` (`_continue_tournament` returns without
  saving `tournament_results`), `experiments/resume_helpers.py:232-258` (resume helper returns that result),
  `flask_app/routes/experiment_routes.py:3311-3319` and `experiments/run_ai_tournament.py:2910-2921` (route saves only JSON).
- **Why confirmed/history:** the multi-table tournament captains-log calls completion the most load-bearing path and records
  the move to one completion/result authority. Resume currently writes a sibling artifact while leaving the DB authority
  absent.
- **Impact:** a resumed variant can appear completed in logs/JSON while repository recovery still classifies it as
  incomplete. Subsequent resume/completion checks can retry or miscount finished tournaments, producing stuck or duplicated
  recovery behavior.
- **Fix sketch:** have `_continue_tournament()` persist `tournament_results` through the same repository path as fresh runs,
  or centralize result finalization so fresh and resumed tournaments share one DB write. Add a regression that resumes a
  tournament to completion and then verifies `get_incomplete_tournaments()` excludes it.


#### ⬜ 140. Stalled-variant resume can bind a row from another experiment to the requested config
The stalled-variant resume route receives both an `experiment_id` path parameter and a `game_id` row id, but it locks and
loads `experiment_games` by row id only. It fetches the caller-supplied experiment config separately, then passes that
config plus the unscoped row into the background resume. Today #137 prevents the resume from reaching the helper, but once
that signature is fixed a direct admin/API call can combine experiment A's config/status flow with experiment B's variant
row.
- **Files:** `flask_app/routes/experiment_routes.py:3225-3237` (lock by row id, fetch requested experiment separately),
  `flask_app/routes/experiment_routes.py:3242-3262` (`SELECT ... FROM experiment_games WHERE id = ?` without
  `experiment_id = ?`), `flask_app/routes/experiment_routes.py:3264-3269` (background call gets requested experiment id
  and unscoped row data).
- **Why confirmed:** there is no SQL predicate or post-fetch check tying the selected `experiment_games` row to the path's
  `experiment_id`. This is distinct from #137's dead call boundary; it is the route's row-ownership/config binding bug.
- **Impact:** after the signature issue is repaired, a crafted admin/debug request can resume or lock the wrong variant row
  under the wrong experiment configuration and then run completion checks against the wrong experiment.
- **Fix sketch:** acquire/release the resume lock through a method that verifies `(row_id, experiment_id)`, or fetch the row
  with `WHERE id = ? AND experiment_id = ?` before locking. Add route coverage that rejects a variant row belonging to a
  different experiment.


#### ⬜ 141. Last-admin protection is raceable and can leave RBAC with zero admins
The admin user-management route tries to prevent removing the last admin, but the invariant is enforced as a route-level
check-then-delete sequence. `DELETE /api/admin/users/<user_id>/groups/admin` rejects self-removal, separately counts current
admin memberships, then separately deletes the target membership. The repository delete has no predicate proving another
admin still exists, and each repository method opens its own connection/transaction.
- **Files:** `flask_app/routes/user_routes.py:87-120` (self-removal check, `count_users_in_group('admin')`, then
  `remove_user_from_group`), `poker/repositories/user_repository.py:319-343` (unconditional membership delete plus separate
  count query), `tests/test_repositories/test_user_repository.py:175-201` (repository tests cover simple remove/count only,
  not the last-admin invariant or concurrent removals).
- **Why confirmed:** with two admins, two concurrent requests can each observe `admin_count == 2` and then delete the other
  admin's membership. There is no conditional SQL, transaction, lock, or schema constraint that re-checks the count at the
  mutation authority.
- **Impact:** RBAC administration can strand the deployment with zero users holding `can_access_admin_tools`, locking out
  admin-only recovery pages and any UI route needed to reassign the admin group.
- **Fix sketch:** move the invariant into one repository transaction/statement, for example an atomic delete that only
  removes an admin membership when a second admin still exists and the caller is not self-removing. Add route/repository
  coverage for last-admin rejection and a two-admin concurrent-removal scenario.


#### ⬜ 142. Admin model visibility toggles leave optimistic state after rejected writes
The admin Unified Settings model section maps visibility to two backend booleans (`enabled`, `user_enabled`) and updates
React state optimistically before any server write. It checks only the first toggle response body, then fires a second
`user_enabled` toggle for `system`/`users` visibility without reading `response.ok` or `success`. On any rejected first
write, rejected second write, stale model id, or interrupted request, the panel can keep showing the optimistic visibility.
- **Files:** `react/react/src/components/admin/UnifiedSettings/sections/ModelsSection.tsx:59-101` (optimistic update,
  partial response checking, no rollback), `flask_app/routes/admin_dashboard_routes.py:132-162` (toggle endpoint returns
  `success:false` with 4xx/5xx on rejected writes), `poker/repositories/llm_repository.py:147-156` (stale ids raise
  `Model not found`).
- **Why confirmed:** `adminFetch()` resolves non-2xx responses as normal `Response` objects, and this component restores no
  snapshot of the prior model list. The second backend mutation is awaited only for transport completion, not inspected for
  failed status or `success:false`.
- **Impact:** an admin can believe a model is off, system-only, or user-enabled while the server still has the old flags.
  That can mislead provider rollout, model availability, and budget-control decisions until a full refresh reveals the real
  state.
- **Fix sketch:** apply visibility changes only after all required backend writes succeed, or keep a prior snapshot and roll
  back on any non-OK/`success:false` response. Prefer a single backend endpoint that atomically sets both booleans. Add a
  mocked-fetch test covering stale-id first failure and second-call failure.


#### ⬜ 143. In-game coach mode persists stale local state after failed config mutations
`useCoach.setMode()` updates React state and `localStorage('coach_mode')` immediately, then posts the desired mode to
`/api/coach/<game_id>/config` without checking the response. The backend rejects common states as non-2xx: the game must be
warm in memory, owned by the caller/admin, and the mode must be valid. A failed mutation therefore leaves the table and
localStorage fallback advertising a mode the backend never saved.
- **Files:** `react/react/src/hooks/useCoach.ts:44-54,84-129` (local fallback, server load, optimistic mode set, unchecked
  POST), `flask_app/routes/coach_routes.py:708-728` (POST returns 404 for cold/evicted games, 403 via owner guard, 400 for
  invalid modes, and only then saves), `react/react/src/components/game/PokerTable/PokerTable.tsx:197-205` (local `mode`
  gates recommendations and post-hand review requests).
- **Why confirmed:** the catch block handles only network rejection. HTTP 404/403/400 resolves successfully in `fetch()`, so
  no rollback or refresh occurs even when the backend refused to persist the mode.
- **Impact:** a player can see coach off/reactive/proactive locally while backend config remains unchanged, causing tips,
  recommendations, post-hand review requests, and first-paint localStorage behavior to disagree with the saved game state.
  This is separate from existing coach analytics/training entries; it is a client/server config mutation mismatch.
- **Fix sketch:** make `setMode()` await the POST, inspect `response.ok`, and roll back or reload server config on failure.
  For cold games, either cold-load before saving config or return a UI-visible reload-required state. Add frontend coverage
  for failed POST preserving the previous mode.


#### ⬜ 144. Main Event decline/expire can consume an invite before autonomous spawn succeeds
`_resolve_autonomously()` claims an offered invite into `declined`/`expired` before calling
`spawn_autonomous_tournament()`. The human accept path wraps its build in `try/except` and re-opens the invite on build or
funding failure, but the decline/expire path has no equivalent recovery. If autonomous spawn raises after the claim
because the exclusion scan, row write, funding plan, or buy-in/overlay escrow write fails, the invite remains terminal and
unlinked even though no tournament is active to advance or reconcile.
- **Files:** `flask_app/services/tournament_invites.py:359-396` (terminal CAS claim before spawn, no exception recovery),
  `flask_app/services/tournament_invites.py:332-343` (accept path explicitly reverts on build/funding failure),
  `flask_app/services/tournament_spawn.py:189-226` (autonomous row is written, funding can raise, row cleanup re-raises).
- **Why confirmed/history:** the tournament logs emphasize decline/expire as "starts WITHOUT you" and one active tournament
  authority. The source intentionally consumes unfieldable declines, but exceptions are different: no autonomous result is
  recorded, no retryable invite remains, and no row links the terminal invite to a recoverable tournament.
- **Impact:** a lapsed or declined Main Event can disappear instead of running autonomously. The player has no open invite to
  retry, the ticker has no active tournament to advance, and partial funding failures can combine with the existing escrow
  recovery gaps while the invite lifecycle says the event was consumed.
- **Fix sketch:** wrap autonomous spawn after the claim. On exception, either revert the invite to `offered` like accept or
  mark a retryable `failed_autonomous` state with enough context for a watchdog/admin retry. Add decline and expire
  regressions that force funding failure after claim and assert the invite is not silently terminal-unlinked.


#### ⬜ 145. Initial-admin bootstrap misses the first Google login
`INITIAL_ADMIN_EMAIL` is resolved only once during app startup. In production the configured admin is expected to be a
Google/OAuth email, but if that user has not signed in yet, `initialize_admin_from_env()` logs "not found" and returns.
The later OAuth user-creation path creates the Google user and signs them in, but it does not retry the bootstrap or assign
the admin group.
- **Files:** `flask_app/extensions.py:415-419` (startup-only bootstrap), `poker/repositories/user_repository.py:450-493`
  (email lookup returns `None` until the user row exists), `poker/auth.py:354-386` (first Google login creates the user but
  does not call the bootstrap/assignment path).
- **Why confirmed:** repository tests cover "email not found" and "email found" as separate startup cases, but there is no
  callback-time promotion when the configured email first appears.
- **Impact:** the first real production admin can authenticate successfully yet have only ordinary user permissions until an
  app restart or direct DB/group repair. That blocks admin-only recovery and configuration pages at exactly the first-login
  setup moment.
- **Fix sketch:** after Google user create/update, if the email matches `INITIAL_ADMIN_EMAIL`, assign `admin` in the same
  repository path used at startup. Add an OAuth/user-repository regression where the email is absent at boot, then created,
  and immediately receives admin permissions.


#### ⬜ 146. Concurrent profile preference writes can clobber sibling scalar settings
`preferences_json` is meant to hold multiple scalar profile settings without clobbering siblings, but the merge is not
atomic. `_set_preference_scalar()` loads the whole JSON blob on one connection, mutates one key in memory, then upserts the
whole blob on a second connection. Concurrent saves from two tabs/devices, such as game speed and default coach mode, can
both read the same old blob and then last-write-wins the other key away.
- **Files:** `poker/repositories/user_preferences_repository.py:127-164` (read-merge-write split across DB operations),
  `poker/repositories/user_preferences_repository.py:180-208` (game speed and coach default share the same blob),
  `flask_app/routes/profile_routes.py:109-137` (independent profile endpoints can be called concurrently).
- **Why confirmed:** the comment states shared blob settings should not clobber each other, but the write authority replaces
  `preferences_json` with a stale full-object snapshot rather than updating one key under a transaction/compare-and-swap.
- **Impact:** account/profile settings can silently revert under concurrent settings saves or multi-device use. The response
  for each individual request says success, but the final stored profile can drop one of the settings.
- **Fix sketch:** serialize the read/merge/upsert in one repository transaction, use an optimistic compare-and-swap retry on
  the previous JSON value, or move scalar settings to explicit columns. Add a two-writer regression proving
  `game_speed` and `coach_default_mode` both survive.


#### ⬜ 147. `LLMClient` default reasoning effort overrides the configured minimal baseline
`core.llm.config` documents `DEFAULT_REASONING_EFFORT = "minimal"`, and providers can treat minimal as the cheap/no-reasoning
baseline. But `LLMClient.__init__()` defaults its `reasoning_effort` argument to `"low"` and passes that explicit value to
every provider, so callers that omit reasoning effort never reach the configured minimal default.
- **Files:** `core/llm/client.py:33-53` (`reasoning_effort="low"` passed into provider creation),
  `core/llm/config.py:71-73` (configured default is `minimal`), `core/llm/providers/xai.py:70-76` (`grok-4-fast` switches to
  the reasoning model for any non-minimal effort), `core/llm/providers/openai.py:34` (OpenAI only falls back to config when
  the caller passes a falsey effort).
- **Why confirmed:** the xAI fast-tier tests cover explicit `minimal` vs `low`, but the client default chooses `low`, not the
  shared configured baseline.
- **Impact:** omitted-effort callers pay higher latency/cost than intended. For xAI `grok-4-fast`, the default client points
  at the reasoning variant instead of the fast non-reasoning variant; for GPT-5-family calls it avoids the intended minimal
  default.
- **Fix sketch:** change `LLMClient.__init__` to default `reasoning_effort=None` or `DEFAULT_REASONING_EFFORT`, and add
  provider/client tests proving an omitted effort uses `minimal` while explicit `low` still opts into reasoning.


#### ⬜ 148. Anthropic medium/high thinking budgets exceed the shared max-token default
Anthropic extended thinking budgets are mapped independently from the request's `max_tokens`. `medium` and `high` map to
8,000 and 16,000 thinking tokens, but provider calls default `max_tokens` to the shared `DEFAULT_MAX_TOKENS` of 5,000 and
pass that unchanged. Those reasoning modes can therefore ask Anthropic for a thinking budget larger than the entire token
limit for the response.
- **Files:** `core/llm/providers/anthropic.py:59-69` (thinking budget map), `core/llm/providers/anthropic.py:95-103,139-153`
  (`max_tokens` default passed alongside `thinking.budget_tokens`), `core/llm/config.py:345-347`
  (`DEFAULT_MAX_TOKENS = 5000` includes reasoning plus output).
- **Why confirmed:** existing Anthropic audit/test coverage addresses accounting after a response; this is request
  construction before the provider call.
- **Impact:** configured Anthropic `medium`/`high` reasoning can fail provider-side or leave no output budget, turning an
  admin-selected model tier into a systematic runtime error.
- **Fix sketch:** validate `thinking_budget < max_tokens` before sending, raise/inform admins on incompatible settings, or
  increase `max_tokens` per reasoning tier with an explicit reserved output floor. Add a provider unit test for medium/high
  request construction.


#### ⬜ 149. Admin system-LLM settings accept arbitrary provider/model values
The admin settings API whitelists setting keys, but it validates values only for prompt capture, retention days, and the
webhook URL. Provider/model keys such as `DEFAULT_PROVIDER`, `FAST_PROVIDER`, `NANO_PROVIDER`, `IMAGE_PROVIDER`, and
`ASSISTANT_PROVIDER` can persist any string. Runtime getters then return the DB value directly, and `LLMClient`/image
providers fail later when a tier tries to use an unknown provider or unsupported provider/model pair.
- **Files:** `flask_app/routes/admin_dashboard_routes.py:1161-1175` (provider/model keys are accepted),
  `flask_app/routes/admin_dashboard_routes.py:1364-1397` (no provider/model value validation before save),
  `core/llm/settings.py:54-65,68-101` (DB app setting returned directly), `core/llm/config.py:232-275` (available providers
  and provider model lists already exist for validation).
- **Why confirmed:** this is not the frontend optimistic-state problem: the backend itself accepts invalid values as a
  successful settings write.
- **Impact:** a typo or crafted admin/API request can break an entire LLM tier at runtime, including default, fast, nano,
  image, or assistant calls, until the DB setting is repaired.
- **Fix sketch:** validate provider settings against `AVAILABLE_PROVIDERS`, validate model settings against the selected
  provider's model list or enabled-model repository, and validate provider/model pairs atomically where possible. Add route
  coverage for unknown providers and mismatched model/provider pairs.


#### ⬜ 150. Flask-Session filesystem config is never activated
`AuthManager.init_app()` sets `SESSION_TYPE='filesystem'` and the project installs `Flask-Session`, but no code imports
`flask_session` or calls `Session(app)`. Flask therefore keeps its default signed client-side cookie session interface; the
filesystem session setting is dead config.
- **Files:** `poker/auth.py:85-95` (sets Flask-Session config only), `requirements.txt:7-9` (Flask and Flask-Session both
  installed), whole-repo search for `flask_session`/`Session(app)` finds no initializer.
- **Why confirmed:** Flask does not switch to server-side filesystem sessions from config alone; the extension must be
  initialized.
- **Impact:** session storage, revocation, and OAuth-session semantics are not what the configuration implies. `session.clear()`
  and "regenerate session" comments replace signed-cookie contents, not a server-side session id; operators expecting
  filesystem-backed session invalidation or cleanup get client-cookie behavior instead.
- **Fix sketch:** either initialize Flask-Session explicitly with a known session directory and tests asserting the session
  interface, or remove the dead config/dependency and update docs/comments to state that sessions are signed client-side
  cookies.


#### ⬜ 151. Experiment design chat treats failed LLM calls as successful empty replies
The experiment design chat route calls `LLMClient.complete()` and then immediately parses/persists `response.content`
without checking `response.status`, `error_code`, or `error_message`. `LLMClient` returns structured error responses for
provider failures, budget blocks, and internal tool-loop failures instead of raising. This route therefore appends an empty
assistant message, saves it to the chat session, and returns HTTP 200 with `success: true` and an empty `response`.
- **Files:** `flask_app/routes/experiment_routes.py:1642-1653` (LLM call),
  `flask_app/routes/experiment_routes.py:1655-1677` (content parsed and assistant turn appended without status check),
  `flask_app/routes/experiment_routes.py:1729-1749` (session persisted and success response returned),
  `core/llm/client.py:155-165,308-328` (budget/provider failures return `status="error"` responses).
- **Why confirmed:** route tests mock only happy-path response content and never cover `LLMResponse(status="error")`. This is
  distinct from the provider/settings findings: those can cause errors, while this route masks any such error as a successful
  chat turn.
- **Impact:** admins can lose a design turn to a blank assistant response, and the persisted chat history now contains an empty
  assistant message that affects subsequent prompts. Provider outages, budget blocks, invalid model settings, and max-tool-loop
  errors become silent UX/data corruption instead of visible retryable failures.
- **Fix sketch:** after `complete()`, fail closed on `response.status != "ok"`: return a non-2xx JSON error with the safe
  message/code and do not append/persist an assistant turn. Add route tests for `budget_exceeded` and a generic provider error.


#### ⬜ 152. Experiment SQL tool row cap is bypassable by explicit or commented `LIMIT`
The replay experiment assistant's SQL tool advertises "SELECT max 100 rows," and the tests frame row limiting as a security
property. The implementation only appends `LIMIT 100` if the uppercased original SQL text does not contain the token
`LIMIT`. A model/tool request can therefore supply `LIMIT 100000` and get far more than 100 rows, or include `LIMIT` inside a
trailing/block comment so the check skips appending while SQLite executes the query without any actual limit.
- **Files:** `flask_app/routes/experiment_routes.py:382-395` (tool schema promises max 100 rows),
  `flask_app/routes/experiment_routes.py:488-504` (cap is skipped whenever `LIMIT` appears in raw normalized SQL),
  `tests/test_sql_query_security.py:5-9,250-315` (security intent says limit enforcement, but only tests no-limit and small
  explicit-limit cases).
- **Why confirmed:** comment stripping occurs only inside the branch that has already decided there is no `LIMIT`; comments
  containing `LIMIT` prevent the branch from running. Explicit large limits are not clamped.
- **Impact:** the admin experiment assistant can pull unbounded rows from allowed high-volume tables such as
  `prompt_captures`, `api_usage`, and replay result tables. That can inflate LLM prompt/tool payloads, expose much broader
  captured prompt data than intended to the assistant context, and create slow DB/tool responses from a supposedly capped
  read-only helper.
- **Fix sketch:** parse or wrap SELECT queries so the outer result set is always capped, for example
  `SELECT * FROM (<validated query without trailing semicolon>) LIMIT 100`, and reject explicit limits above 100. Strip
  comments before checking for limit tokens. Add regressions for `LIMIT 100000`, `-- LIMIT`, and `/* LIMIT */` bypasses.


#### ⬜ 153. Experiment SQL tool allowlist misses quoted, qualified, and comma-join table names
The experiment assistant SQL helper intends to allow only a small set of read-only experiment tables. Its table extraction
regex only captures bare `FROM table` / `JOIN table` identifiers. SQLite also accepts quoted identifiers, schema-qualified
names, and comma joins, so queries like `SELECT * FROM "users"`, `SELECT * FROM [users]`, `SELECT * FROM main.users`,
or `SELECT * FROM personalities, users` produce an empty or incomplete parsed table set, pass the allowlist check, and then
execute against the real database.
- **Files:** `flask_app/routes/experiment_routes.py:403-413` (intended table allowlist),
  `flask_app/routes/experiment_routes.py:469-486` (bare-word table regex and allowlist check),
  `flask_app/routes/experiment_routes.py:490-504` (executes the original SQL),
  `tests/test_sql_query_security.py:172-207,375-379` (covers unquoted disallowed tables/subqueries and case-insensitive
  allowed tables, but not quoted, schema-qualified, or comma-join disallowed identifiers).
- **Why confirmed:** the parser returns no disallowed table for quoted identifiers and only the first table in comma joins,
  so `disallowed = tables - allowed_lower` is empty even though SQLite will resolve the omitted table names. This is distinct
  from #152's row-cap bypass; this one breaks the table allowlist itself.
- **Impact:** the admin experiment assistant can read non-whitelisted tables such as users, groups, settings, auth/session
  related tables, or any other SQLite table if the model/tool request uses quoted identifiers or comma joins. Those rows can be serialized
  into tool output and then sent to the LLM/provider context.
- **Fix sketch:** stop parsing SQL with regex. Use SQLite authorizer callbacks, a SQL parser, or execute against a restricted
  read-only view database containing only approved tables. At minimum, strip comments, reject quoted/schema-qualified
  identifiers until safely parsed, and add regressions for `"users"`, `[users]`, `main.users`, comma joins, and quoted joins.


#### ⬜ 154. Persona-delete cleanup can return chips before the original holder is cleared
The personality delete route runs deletion-integrity hooks before deleting the personality, but the hooks are best-effort
and each chip return is split from clearing the holder that still owns those chips. `sweep_presence_on_persona_delete()`
first records a `casino_seat_return` for the AI's residual table chips, then calls `cash_table_repo.save_table()` to open
the slot and clear presence. `settle_ai_bankroll_to_pool_on_delete()` likewise records a `casino_seat_return` for each
stored bankroll row before calling `save_ai_bankroll(... chips=0)`. If the second write in either sequence raises after
the ledger row lands, the route logs at the outer best-effort boundary and still deletes the personality. The bank pool is
credited while the table seat or bankroll row can still hold the same chips.
- **Files:** `flask_app/routes/personality_routes.py:389-430` (settle/sweep is best-effort and deletion still proceeds),
  `cash_mode/presence_sweep.py:120-158,162-187` (seat-chip ledger return before `_open_seat()` / `save_table()`),
  `cash_mode/bankroll.py:360-430` (bankroll ledger return before zeroing `ai_bankroll_state`),
  `core/economy/ledger.py:1430-1464` (`casino_seat_return` is an AI → central-bank destruction row),
  `poker/repositories/schema_manager.py:810-819` (bankroll rows are not FK-cascaded by personality deletion),
  `tests/test_cash_mode/test_presence_sweep.py:72-82` and
  `tests/test_cash_mode/test_chip_custody_parity.py:362-387` (happy paths only; no failure-after-ledger regressions).
- **Why confirmed/history:** the chip-custody captain's log explicitly calls out separate repo transactions as the source of
  ledger/int divergence, and the presence logs say delete-time sweeps replaced later zombie-seat reconcilers. This path
  reintroduces the same ordering hazard inside the replacement deletion hooks.
- **Impact:** a transient DB lock or presence/bankroll write failure during persona deletion can create double-counted
  custody (central bank credited while the seat or bankroll still holds chips), leave a zombie seat/personality id in the
  lobby, or leave a stale bankroll row for a deleted persona. Because the personality row is gone, the normal delete route
  is no longer a natural retry trigger.
- **Fix sketch:** make each chip return and holder-clear one atomic unit of work, or reverse the order so a failed return
  leaves the persona undeleted/retryable. At minimum, abort deletion when any sweep/settle hook reports failed cleanup, and
  add regressions where the ledger write succeeds followed by a forced `save_table()` or `save_ai_bankroll()` failure.


#### ⬜ 155. Casino teardown deletes occupied tables without clearing authoritative presence
The Presence cutover makes `cash_table_repo.save_table()` the transactional chokepoint that turns seat changes into
`entity_presence` transitions. Casino teardown bypasses that chokepoint: when a closing casino's countdown reaches zero,
`resolve_casino_provisioning()` returns fish seat chips to the pool and then calls `cash_table_repo.delete_table()`, whose
implementation is a raw `DELETE FROM cash_tables`. Retired-tier and dam wind-down paths can enter closing while fish are
still seated, so the elapsed countdown can delete a table that still has SEATED presence rows. The manual
`force_respawn_casinos.py` script repeats the same pattern with raw SQL.
- **Files:** `cash_mode/casino_provisioning.py:1188-1230` (closing-countdown teardown returns chips then deletes the table),
  `cash_mode/casino_provisioning.py:1268-1326` (retired-tier/dam wind-down can close occupied casinos),
  `poker/repositories/cash_table_repository.py:380-398` (`save_table()` drives presence) and
  `poker/repositories/cash_table_repository.py:504-535` (`delete_table()` raw-deletes only the table row),
  `scripts/force_respawn_casinos.py:59-64,205-210` (manual casino reset raw-reads/deletes casino rows),
  `tests/test_cash_mode/test_casino_provisioning.py:868-922` (teardown test empties seats before delete; no occupied-presence case).
- **Why confirmed/history:** the presence captain's log says the post-flip invariant is one seat writer: `save_table()` runs
  presence reconciliation inside the same transaction, while legacy reconcilers are dormant. Deleting the row skips that
  writer entirely, so no `RETURN_TO_POOL`/`GO_OFFLINE` transition is emitted for occupants.
- **Impact:** fish can remain `SEATED` in `entity_presence` at a deleted table. That can block reseating via the partial seat
  index or make whereabouts/lobby/admin surfaces report deleted-table occupants. If the same casino id respawns later, stale
  presence can also collide with or misclassify the new occupants.
- **Fix sketch:** before `delete_table()`, persist an all-open version of the existing table through `save_table()` while the
  old row still has occupant metadata, verify no SEATED presence rows remain for that table, then delete. Update
  `force_respawn_casinos.py` to use the same repository-backed vacate path. Add an occupied closing-casino regression that
  asserts fish presence returns to pool before the table row disappears.


#### ⬜ 156. Tourist cleanup scripts still target dropped `cash_idle_pool` and bypass presence authority
Several one-shot/operator cleanup scripts still assume the pre-v152 idle cache exists and manipulate cash state with raw SQL.
Schema v152 drops `cash_idle_pool` because idle now lives in `entity_presence` plus `cash_idle_metadata`, so these scripts can
fail immediately on current databases. Worse, `cleanup_tourist_zombie_personalities.py` raw-updates `cash_tables.seats_json`
to open zombie seats, then deletes bankroll/idle/personality rows, bypassing the `save_table()` presence transition that the
post-cutover code relies on.
- **Files:** `scripts/cleanup_tourist_zombie_personalities.py:152-160,197-225,269-283` (reads/deletes `cash_idle_pool`, raw
  opens seats, deletes personalities), `scripts/cleanup_ephemeral_tourist_leak.py:89-101,263-269` (reads/deletes
  `cash_idle_pool`), `scripts/_eph_purge.py:127-134` (deletes from `cash_idle_pool`),
  `poker/repositories/schema_manager.py:6337-6354` (v152 drops the table and documents `entity_presence` as idle authority),
  `poker/repositories/cash_table_repository.py:380-398` (`save_table()` is the presence-aware seat writer).
- **Why confirmed/history:** this is the maintenance-script version of the same presence-cutover lesson: after v152, old
  cache tables and raw seat JSON edits are no longer safe recovery tools. The scripts were not updated when the idle cache was
  removed.
- **Impact:** an operator following these cleanup scripts on a current DB can get `no such table: cash_idle_pool`, aborting
  the repair. If run against a DB where the table still exists, the raw seat/personality deletes can leave `entity_presence`
  rows for deleted tourist ids or seats, producing the zombie/double-presence states the cutover was meant to eliminate.
- **Fix sketch:** retire or rewrite the scripts against `entity_presence` / `cash_idle_metadata`, and route any seat opening
  through `cash_table_repo.save_table()` or `sweep_presence_on_persona_delete()`. Add a smoke test that runs each cleanup script
  in dry-run mode against a fresh v152+ schema.


#### ⬜ 157. Autonomous Main Event normal payout can use the current ticker sandbox instead of the funded sandbox
Autonomous Main Events are funded with the invite's sandbox id, but the durable tournament row does not persist that id.
The world ticker later advances an owner's active autonomous tournament by owner only and passes the sandbox currently being
ticked into `advance_autonomous_tournament()`. If the owner's default/active sandbox changes between invite funding and the
completion tick, the normal first-pass payout can credit bankrolls, write payout ledger rows, sweep escrow, and grant renown
under the wrong sandbox before the stuck-payout reconcile path ever gets involved.
- **Files:** `flask_app/services/tournament_spawn.py:189-219` (autonomous row saved without sandbox, then funded with the
  invite sandbox), `poker/repositories/schema_manager.py:1382-1397` (tournaments schema has no `sandbox_id`),
  `flask_app/services/ticker_service.py:888-969` (ticker calls advance with the currently ticked sandbox),
  `flask_app/services/tournament_ticker.py:185-207` (active autonomous lookup is owner-only, payout receives caller sandbox),
  `flask_app/services/tournament_spawn.py:475-485` (normal autonomous settlement passes that sandbox to payout).
- **Why confirmed/history:** #51 covers stuck-payout reconciliation deriving the wrong sandbox later; this is the successful
  normal-completion path. The same missing durable field affects first-pass payout, not just recovery.
- **Impact:** prizes and renown can land in sandbox B while the tournament escrow was funded in sandbox A. Conservation checks
  and bankroll/ledger rows can appear balanced in the wrong sandbox while the original escrow remains stranded or invisible to
  the normal completion flow.
- **Fix sketch:** persist `sandbox_id` on `tournaments` at creation/funding, filter active autonomous lookup by owner+sandbox
  or use the row's sandbox for advancement, and pass that persisted sandbox into payout, conservation checks, renown grants,
  and event context. Add a regression that funds in sandbox A, ticks sandbox B, and asserts no payout runs under B.


#### ⬜ 158. Human-out tournament finalization leaves stale result history after later play-out
`finalize_tournament()` intentionally saves a result row when the human busts before the field is complete, so career stats
are recorded immediately. That row can have `winner_name=None`. Later, the player can keep spectating and call `/advance` or
`/play-out`; those routes finish the field, persist the session, and run payout, but they never rebuild or replace the
`tournament_results` row. Because `finalize_tournament()` also guards itself with `game_data['tournament_finalized']`, a
later field-complete call is explicitly treated as already finalized.
- **Files:** `flask_app/handlers/tournament_completion.py:158-166,189-204,231` (human-out result persisted once and then
  guarded), `tests/test_tournament/test_completion.py:173-194` (human-out result can have no winner),
  `flask_app/routes/tournament_routes.py:519-527,545-553` (`/advance` and `/play-out` complete/payout without saving final
  results), `poker/repositories/tournament_repository.py:14-55,273-303` (history reads `tournament_results`).
- **Why confirmed/history:** the tournament captain's log says completion/result persistence is one of the load-bearing paths;
  this split creates two terminal concepts: an early human career snapshot and the actual field winner, but only the early row
  is durable.
- **Impact:** tournament history, winner feeds, and post-session analytics can show a completed/payout-settled event with no
  winner or stale standings from the human bust moment. The user can watch the tournament finish, but durable history still
  represents the earlier partial result.
- **Fix sketch:** split human-bust career-stat recording from final tournament result persistence, or allow the field-complete
  path to replace/update `tournament_results` while making career-stat updates idempotent. Add a regression that finalizes on
  human-out, then `play_out()` completes the session, and the saved result row has the real winner.


#### ⬜ 159. Short all-ins inflate raise counters even when they are not legal full raises
`player_all_in()` correctly preserves `last_raise_amount` when an all-in raise increment is smaller than the current minimum
raise, and its comment says the short all-in does not reopen betting or change the min raise. The same branch still increments
`raises_this_round`, and preflop short all-ins also increment `preflop_raise_count`. Those counters are then consumed as if a
real escalation happened.
- **Files:** `poker/poker_game.py:591-623` (short all-in leaves min raise unchanged but increments raise counters),
  `poker/strategy/preflop_classifier.py:70-79` (counter maps one extra short all-in into `vs_3bet` / `vs_4bet`),
  `poker/strategy/postflop_classifier.py:97-103` (preflop counter maps 2+ into `3BP`), `poker/poker_game.py:272-276`
  (raise cap keys off `raises_this_round`), `poker/bounded_options.py:840-853` and
  `poker/lean_bounded_controller.py:389-397` (strategy/rationale treats the inflated count as a real re-raise).
- **Why confirmed/history:** #14 covers the action-flag/reopening rule. This is a separate semantic drift: even if action
  reopening were fixed, a sub-min all-in after an open can still make the next actor look like they face a 3-bet and can make
  later streets look like a 3-bet pot.
- **Impact:** strategy lookup, postflop node selection, EV adjustments, prompt/rationale text, and non-heads-up raise-cap logic
  can all be one raise tier too high after a short all-in. Bots may fold or demote raises as though there was a legal 3-bet/4-bet,
  and postflop play can use `3BP` context for a single-raised pot with a short all-in side pot.
- **Fix sketch:** update the counters only when `raise_by >= game_state.last_raise_amount` (or when the action is otherwise a
  legal full raise), and add regression coverage for open -> sub-min all-in -> next actor plus postflop pot-type classification.


#### ⬜ 160. Non-action game mutators let admins mutate games they do not own
`_authorize_game_access()` is an owner-or-admin helper. Several mutating REST endpoints reuse it even though their side effects
are player-facing game mutations, not read-only admin inspection. The socket `player_action` path is owner-only, but sibling
socket events for authored chat and progression explicitly allow admins.
- **Files:** `flask_app/routes/game_routes.py:96-138` (shared owner-or-admin helper),
  `flask_app/routes/game_routes.py:2143-2185` (fast-forward toggles and may kick progression),
  `flask_app/routes/game_routes.py:2188-2264` (chat is forced to the human seat before dispatch),
  `flask_app/routes/game_routes.py:2267-2328` (retry can release locks and restart progression),
  `flask_app/routes/game_routes.py:2343-2378` (delete/end remove games),
  `flask_app/routes/game_routes.py:2523-2550` (socket player action is owner-only by contrast),
  `flask_app/routes/game_routes.py:2652-2708` and `flask_app/routes/game_routes.py:2709-2725` (socket chat/progress allow
  owner or admin).
- **Why confirmed/history:** #98 covers REST `/api/game/<id>/action` using the same helper for direct player actions. This is
  the adjacent mutation surface: an admin can mutate a game without sending a formal player action.
- **Impact:** an admin viewing/debugging a live game can inject chat as the human seat, flip fast-forward, restart AI progress,
  release a progression lock through retry, or delete/end the game. These effects are not separated into an audited operator
  workflow, so accidental admin-page actions can corrupt another owner's session.
- **Fix sketch:** split game authorization into read/admin access and owner-only mutation access. Require `current_user.id ==
  owner_id` for player-facing game mutations unless a deliberate audited impersonation/operator endpoint is added. Add REST and
  socket regressions for owner, non-owner, and admin on message, fast-forward, retry/progress, delete, and end-game.


#### ⬜ 161. Internal cash-row purge deletes sessions without settling seat chips or stakes
`_purge_other_cash_rows()` is still a live cleanup helper, called before each new cash game and during leave cleanup. Its own
comment says the path skips the leave/cash-out and stake-settlement pipeline, then the implementation directly deletes the
`games` row and the `cash_sessions` row. That means a stale but still value-bearing cash row can be erased without returning
AI table stacks, settling human seat balances, closing house stakes, or writing the matching custody ledger transfers.
- **Files:** `flask_app/routes/cash_routes.py:261-333` (helper documents the skipped settlement then calls direct deletes),
  `flask_app/routes/cash_routes.py:624-665` and `flask_app/routes/cash_routes.py:1130-1139` (new self-funded starts invoke
  the helper through `_build_cash_game()`), `flask_app/routes/cash_routes.py:1442-1452` and
  `flask_app/routes/cash_routes.py:2195-2204` (table/sponsored starts use the same builder),
  `flask_app/routes/cash_routes.py:4590-4601,4638-4679,5108-5128` (leave cleanup and memory-miss cleanup call it again),
  `cash_mode/lobby.py:4395-4447` (boot sweep has the settle-before-delete logic this helper lacks),
  `tests/test_cash_mode/test_chip_custody_parity.py:280-324` (boot-sweep regression proves orphan seat chips must be
  settled, not zeroed).
- **Why confirmed/history:** #38 covers external generic game delete bypassing cash leave. #136 covers boot orphan-seat scoping.
  This is the internal cash cleanup surface: it is intentionally used to enforce one active cash row per owner, but still uses
  raw deletion while the boot sweeper has already been upgraded to settle before delete.
- **Impact:** starting or leaving a session after a stale row exists can silently destroy AI bankroll chips, strand or forgive
  stake principal without the intended house/personality settlement rows, delete the only `cash_sessions` audit record for that
  session, and make ledger-derived custody disagree with persisted bankrolls. The user sees a clean new sit or leave while the
  closed economy lost whatever was still at the stale table.
- **Fix sketch:** route `_purge_other_cash_rows()` through the same settlement primitive used by `_boot_sweep_stale_cash_rows`,
  including cash-session finalisation and human-seat presence cleanup, before any direct delete. Add regressions for start-time
  purge and leave-time purge with a non-empty seat ledger balance and an active house stake.


#### ⬜ 162. Cash sit paths can 500 and leave a claimed seat when display names collide
Cash game construction still keys the live player roster by display name and calls `initialize_game_state()` with the human
display name plus AI display names. That core helper raises `ValueError` on duplicate names. The generic new-game route has an
explicit duplicate-name guard, but cash `/start`, `/sit`, and sponsor-and-sit do not preflight collisions against the table or
eligible persona roster. The table-aware cash paths save the human seat before `_build_cash_game()`; their rollback only runs
when `_build_cash_game()` returns an error tuple, so a duplicate-name exception can strand the seat claim/presence write.
- **Files:** `poker/poker_game.py:759-781` (duplicate display names raise), `flask_app/routes/game_routes.py:1801-1808`
  (non-cash route handles the human/opponent duplicate case), `flask_app/routes/cash_routes.py:769-778` and
  `flask_app/routes/cash_routes.py:787-797` (cash builder joins selected AI by display name and calls the raising helper),
  `flask_app/routes/cash_routes.py:1428-1466` and `flask_app/routes/cash_routes.py:2178-2224` (table/sponsored sit saves
  the human seat before build and only rolls back returned errors), `poker/repositories/personality_repository.py:511-608`
  (eligible cash pool returns ids and display names with no uniqueness guarantee), `tests/test_cash_sit_route.py:100-106`
  (existing test comment documents a seeded Napoleon collision causing a 500 at game creation).
- **Why confirmed/history:** this is not #53, which is about active tournament presence. It is a display-name identity bug on
  cash construction. The current test suite already had to mark a fixture non-circulating to avoid the duplicate-name 500.
- **Impact:** a user whose display name matches a seated/circulating persona, or a table with duplicate persona display names,
  can hit a 500 instead of a recoverable 400/seat retry. On `/sit` and sponsor-and-sit, the persisted table can keep showing the
  human seat as reserved/occupied even though no live cash game or cash session was created, blocking that seat and confusing
  presence/cold-load recovery.
- **Fix sketch:** preflight cash rosters by case-folded display name before saving a human seat, or generate unique live display
  labels while keeping `seat_id`/`personality_id` as the stable key. Wrap `_build_cash_game()` exceptions in the same seat
  rollback used for returned errors. Add regressions for human name matching an AI and two AI seats with the same display name.


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
| ⬜ 87 | `flatter` is declared sarcasm-capable but the flattery early-return treats `sarcastic` as sincere | `poker/memory/chat_intent.py:78-83`, `flask_app/handlers/chat_relationship.py:241-304,361-363`, `react/react/src/components/chat/QuickChatSuggestions.tsx:80-87` | Low/currently suppressed in normal UI: backend mapping/docs say sarcastic flattery is `sharpen`, but `_dispatch_flattery` ignores the register and applies the normal vanity flattery path. The UI excludes `flatter` from `SARCASM_ABLE_TONES` because this surgery is still missing, but direct API/future UI enablement would invert the wrong way. Either remove `flatter` from the sarcasm map/docs until supported, or teach `_dispatch_flattery` to bypass vanity flattery and use `sarcasm_mirror_shift('sharpen', ...)` + jab stimulus when perceived. History: `docs/captains-log/temperament/social-temperament-and-sarcasm.md:83-95`. |

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
