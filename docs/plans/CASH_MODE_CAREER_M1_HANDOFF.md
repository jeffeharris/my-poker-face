---
purpose: Continuation/handoff for the Career "Circuit" Scene-0 work on branch circuit-progression — what's built, how it works, what's verified, and what to do next
type: guide
created: 2026-05-31
last_updated: 2026-06-03
---

# Career "The Circuit" — handoff (branch `circuit-progression`)

Pick-up doc for a fresh context. Design canon lives in
`docs/plans/CASH_MODE_CAREER_PROGRESSION.md` (the "The Circuit" section); the
real-hands content library in `docs/plans/CASH_MODE_FAMOUS_HANDS_LIBRARY.md`;
narrative log in `docs/captains-log/circuit-progression/`.

## TL;DR — where we are

**Act-1 "The Circuit" is built and committed on `circuit-progression`.** A new
player wanders into the **Lucky Stack** diner, gets a comped stake + an
alliterative **fish-name**, sits at a pinned **Scene-0** table with mentor **Sal
Monroe** (display name; persona id stays `sal_moretti`) + the fish **Loose
Larry**, plays a **rigged ~10-hand tutorial**, watches Sal **stack Larry in a
finale**, **graduates** → lands back in the lobby on the **single populated home
court** → **hands the comp back** (enters at 0) → **Sal stakes him into that
cardroom**. The full Act-1 spine — wander in, get christened, play the room,
graduate, get backed — is now **playable front to back**.

**Session-3 status (2026-06-03, newest first) — branch merged up to current
`development` (schema now v141), 17 commits pushed to `origin/circuit-progression`:**
- **Sal *stakes* the home court** ✅ — the mentor stake (the comp-return's other
  half) is fully wired: carve-out in `sponsor_and_sit` (`_maybe_mentor_offer`),
  lobby `mentor_stake` payload, frontend routes the home-court sit to
  `sponsor-and-sit(lender_id=sal_moretti)`. Plus the **general conservation fix**
  (`_debit_personality_lender_principal` — EVERY personality stake now debits the
  lender's bankroll; the napoleon `xfail` flipped to passing) and the
  **forgiveness seed** (mentor sit seeds Sal↔player warm 0.85/0.85/0 so the
  EXISTING `request_forgiveness` grants a busted carry — no settlement code).
- **Graduation handoff self-heal** ✅ — a lingering Scene-0 session was wedging the
  whole post-graduation flow (comp-return + mentor stake both gate on
  `not has_active_session`); `get_lobby` now closes a graduated player's leftover
  Scene-0 session.
- **"Dad jokes" refill bug** ✅ — `_refill_cash_seats` was replacing the busted
  scripted fish with a random persona; now skips scene games. Pinned
  `loose_larry circulating=0` (DB had drifted to 1); `reset_career.py` rebuilds the
  cast.
- **Larry's flop tell** ✅ — the discipline-hand fish telegraphs the straight WHEN
  he flops it (new per-street `fish_streets` hook), not at hand open.
- **Intake is a scene now** ✅ — the waitress is a centered portrait that talks in
  the shared **print style** (`components/shared/DramaticText`, extracted from
  FloatingChat; beats are event-driven so the next waits for the previous to
  finish). Replies are plain callback flavor (innocent of poker), decoupled from
  quick-chat → persisted as `intake_reply`/`intake_reply_id`. Name pre-fills from
  the account; button is "Tell her"; after intake you **land in the lobby** on the
  single populated table (venue tabs hidden mid-tutorial), not auto-sat.
- **Infra**: merged 155 development commits (career_progress migration renumbered
  **v124 → v141**); split `.pre-commit-config.yaml` so commits/merges don't fight
  the formatters (checks at `pre-commit`, fixers at `pre-push`; CI is the gate).

## What's built (with file pointers)

### The keyring (M1 core)
- **schema v141 `career_progress`** (`poker/repositories/schema_manager.py`,
  `_migrate_v141_create_career_progress`) — per-(sandbox, owner) JSON blob.
  *(Renumbered from v124 → v141 on the development merge — development had taken
  v124 for `opponent_observation_lifetime` and reached v140.)* Repo:
  `poker/repositories/career_progress_repository.py` (also holds `intake_reply` /
  `intake_reply_id`, `mentor_stake_used`, `comp_returned`, `mentor_intro_table_id`).
  Wired in
  `poker/repositories/__init__.py` + `flask_app/extensions.py`
  (`career_progress_repo`). `career_active` defaults **False = full lobby** (can
  never blank a legacy player). Now also holds **`scene_progress`** (generic
  `{scene_id: {idx, passed, complete}}`) for cold-load durability.
- **Lobby filter** — `flask_app/routes/cash_routes.py` `get_lobby`: new-vs-legacy
  detection, Scene-0 seed for a brand-new sandbox, table filter to revealed.

### `cash_mode/career_progression.py` (logic + intake)
- `ensure_scene0_seeded`, `classify_new_player`, `visible_tables`,
  `make_fish_name` (deterministic alliterative — keeps the player's name),
  `generate_intake_persona` (LLM bio + canned fallback), `fire_first_vouch`.
- Constants: `SAL_ID="sal_moretti"`, `SAL_NAME="Sal Monroe"`,
  `SCENE0_FISH_ID="loose_larry"`, `SCENE0_TABLE_ID="cash-scene0-001"`.

### `cash_mode/career_scene.py` (the Scene-0 script)
- `SCENE0_SCRIPT` — hand 0 normal, then **3 teaching hands sourced from real
  famous hands** (no invented spots): **value** = slow-played set of sevens;
  **bluff-catch** = Moneymaker vs Farha (`Q♠9♥` top pair vs the fish barrelling
  `K♠7♥` air — you make the call Farha folded); **discipline** = Chan vs Seidel
  (`Q♣7♣` top pair vs the fish's flopped nut straight `J♥9♠` — you lay down the
  hand Seidel couldn't) — among quiet fillers. Larry has fishy `*blub*` chatter
  (`fish_setup`/`fish_react`); Sal narrates **principle only, never the hero's
  hole cards**, and pass/fail lines respect showdown visibility. Graduation =
  `SAL_GRADUATION_SEQUENCE` (the reveal + fish-name shed + vouch).
- `resolve_scripted_action` turns scripted intents into legal moves, stack-capped
  so the cast never busts.

### `cash_mode/table_scenes.py` (the reusable scene system) — NEW
- `TableScene` descriptor (`scene_id`, `table_id`, `cast` role→persona_id,
  `script`, `mentor_name`, `on_complete`, `graduation_lines`) + a registry keyed
  by table_id. **Scene 0 is the first registered consumer.** A new scripted table
  scene = register a `TableScene`; the rig, cast, narration, cold-load, and
  completion are all generic.

### The deck-rigging seam (engine) — `poker/poker_state_machine.py`
- **`provide_hand_holes(holes_by_name, board)`** — one-shot scripted holes keyed
  by **player name**, resolved into a concrete deck at deal time against the
  **post-button-rotation** seating (`_deck_from_scripted_holes`). This fixed the
  rig bug: `reset_game_state_for_new_hand` rotates the players tuple each hand, so
  the old seat-indexed deck dealt the hero's monster to whoever sat in seat 0.
- The older `provide_hand_deck(deck)` (seat-indexed) is retained for completeness;
  scenes use the name-keyed seam.

### The game-handler driver (`flask_app/handlers/game_handler.py`)
- Scene-generic: `_scene_for_game` / `_init_scene` / `_advance_scene` /
  `_scene_scripted_action` / `_complete_scene` operate on a resolved `TableScene`.
  `_init_scene` **restores** persisted position on cold-load (else fresh start);
  `_advance_scene` judges → narrates → rigs the next hand by name → persists →
  completes (Scene 0 → `_fire_career_first_vouch`).
- `generate_ai_commentary` **suppresses the cast's regular post-hand commentary**
  during a scene (scripted lines are the single voice).

### The mentor stake + forgiveness (session 3) — `flask_app/routes/cash_routes.py`
- **`_maybe_mentor_offer`** builds non-circulating Sal's offer directly (career-
  gated: `career_active + tutorial_complete + home_court_table_id == table_id +
  not mentor_stake_used`; friendly terms floor 1.0 / rate 0). The mentor branch in
  `sponsor_and_sit` uses it, flips `mentor_stake_used`, and seeds Sal↔player **warm**
  (0.85/0.85/0, both directions) for forgiveness.
- **`_debit_personality_lender_principal`** — funds ANY personality stake's
  principal out of the lender's bankroll (pure non-bank transfer, keeps the
  chip-ledger audit flat). Fixed a universal mint bug; the napoleon audit test
  (`test_cash_sponsor_routes.py`) now passes.
- **Lobby** exposes `mentor_stake {table_id, lender_id, lender_name, stake_label}`
  (graduated + broke + home court + not used). **Self-heal** in `get_lobby` closes
  a graduated player's lingering Scene-0 session so the handoff isn't wedged.

### Frontend
- **`components/shared/DramaticText.tsx`** (NEW) — the "print style" beat renderer
  (actions fade in, speech types out; beats are **event-driven** — the next waits
  for the previous to finish; `DramaticReserve` reserves height to kill jitter).
  Extracted from `FloatingChat`; reused by `SeatSpeechBubble`, `SalFloater`, intake.
  `splitSentences` (in `utils/chatBeats`) gives a pause after each sentence.
- `LuckyStackIntake.tsx` — the waitress is a centered portrait that talks via
  `DramaticReserve`; replies are `{id, reply}` (no quick-chat mapping); name
  pre-fills from `useAuth`; "Tell her" → lands in the lobby (`handleIntakeDone`).
- `Lobby.tsx` — keyring wiring, `mentor_stake` routing in `handleSeatTap`, venue
  tabs hidden mid-tutorial (`inTutorial`), auto-scroll/expand to the home court on
  the mentor handoff.
- `SalFloater.tsx` — Sal's floating portrait; plays a **queue**, now renders via
  `DramaticReserve` (typed, sentence-split).

### Tests
`tests/test_scripted_deck_seam.py`, `tests/test_cash_mode/test_career_scene.py`,
`test_scene0_intable.py` (incl. the **rotation regression**, **cold-load
restore/persist**, and the **scene-fish-not-refilled** regression),
`test_career_progression.py`, `tests/test_cash_career_lobby_route.py` (keyring +
`TestCareerMentorStake`: carve-out, conservation, one-shot, forgiveness seed,
self-heal), `tests/test_cash_sponsor_routes.py` (the conservation audit).
Post-merge: **1223 cash_mode tests pass**, TS build + ruff/prettier clean.

## Status
- **Branch:** `circuit-progression` — **merged up to current `development`**
  (`d317bd5b`), pushed. **Schema v141.** Session-3 commits (newest→oldest):
  `5607522b` forgiveness seed, `62dd134c` pre-commit split, `d317bd5b` merge,
  `cba77e29` land-in-lobby, `768d5397` refill-fix, `cee32165` event-driven beats,
  `a675ba41`/`267c6ec3`/`7bf7ba31`/`23a27401` intake polish, `b1bd01b0` captain's
  log, `17234478` handoff self-heal, `613ed7ec` flop tell, `344c58ff` mentor stake.
- Live dev DB (`guest_jeff`) reset + on v141. **No PR opened** (branch is current
  with development).
- Captain's logs in `docs/captains-log/circuit-progression/` (3 sessions).

## Known risks / gotchas (READ before continuing)
1. **Narrow deal-window race (residual).** Cold-load resumes by persisted scene
   index, and the in-progress hand keeps its already-dealt rigged cards. The only
   gap: a cold-load landing in the sub-second between rigging a hand and dealing
   it would deal *that one hand* random (Sal's line could mismatch) — it
   self-heals next hand. Not worth the "was this dealt yet?" detection for now.
2. **The floater is still Sal-specific.** `SalFloater` keys on the sender name
   `"Sal Monroe"`, so a *future* scene with a different mentor narrates in plain
   chat until the floater is generalized (small frontend follow-up; the backend
   scene system is fully reusable).
3. **Renaming a live persona spawns a zombie.** Clear active games BEFORE renaming
   `sal_moretti` (the [[zombie-persona auto-create]] class).
4. **Orphan `cash_sessions` resurrects deleted games.** Always clear
   `cash_sessions` + `cash_session_events` + `games` together, then restart
   backend. `scripts/reset_career.py` does the DB part (and now **rebuilds the
   Scene-0 cast** — re-opens the human seat, re-seats Sal + Loose Larry).
5. **Pre-existing flaky `test_graduation_returns_the_comp_to_the_pool`.** The
   world-economy RNG churns the bank pool during `get_lobby`, so its exact
   pool-delta assertions flake (~40% across repeated runs). NOT from the Circuit
   work; it'll intermittently red the cash bucket until the assertion is made
   churn-tolerant. (Easy follow-up: assert intent — comp not re-returned — rather
   than an exact pool number.)
6. **Scripted-table cast is fragile to generic seat-fill paths** (the "dad jokes"
   class). FIXED for `_refill_cash_seats` (skips scene games) + the world refresh
   (`refresh_unseated_tables` already skips scripted) + `loose_larry circulating=0`.
   Treat any NEW seat-fill / movement path as needing a `_scene_for_game` skip.
7. **Merges + pre-commit.** The hook is split (checks at `pre-commit`, file-
   modifying fixers at `pre-push`) so commits/merges don't fight it. CI
   (`deploy.yml`) runs ruff + prettier independently — the real gate. A BIG merge
   *push* can still trip pre-push on incoming code → `git push --no-verify`.
   `docker-compose.override.yml` is now gitignored (dev untracked it; `.example`
   template) — the local net pin lives there.

## Next steps (suggested order)
1. **Verify the finale live** ⭐ — the one part automated tests can't cover: play
   to graduation and confirm Sal actually busts Larry to 0, the comp-return fires,
   and the new mentor-stake handoff lands end-to-end. Reset with
   `scripts/reset_career.py guest_jeff` + `docker compose restart backend`, then
   `/cash`.
2. **M2 — real `vouch_ready`** (respect-gated, likability-driven, played-with,
   one-per-AI) over the relationship graph; evaluate on the world ticker. FIRST
   step is the regard-edge **instrumentation/logging** M1 was meant to leave so
   the thresholds (~0.70 like / respect floor) are tuned from real data — verify
   that logging exists before tuning. See `CASH_MODE_CAREER_PROGRESSION.md` § M2.
3. **Generalize the floater** to any scene mentor — `SalFloater` now renders via
   `DramaticReserve` but still keys on the sender name `"Sal Monroe"`, so a 2nd
   scene's mentor narrates in plain chat. Small frontend follow-up when needed.
4. **M3** — training lounge / scripted drills (re-port the `from_saved_state`
   reconstruction engine from `training-room` when hand-replay is built; the
   `table_scenes` system is the natural host).
5. **Wire avatar generation** (seam: `intake_avatar_prompt`, returned by
   `/api/cash/intake`).
6. **Open a PR** to `development` (branch is merged-current; nothing landed yet).
   Before then: drop the env-specific `chore(dev)` subnet commit if it was local-
   only, and decide on the flaky-test hardening (risk #5).

### Small polish offers (raised, not taken)
- Waitress portrait size/placement (200px centered) — eyeball + nudge.
- The `"hard to read"` intake reply — swap if too on-the-nose.
- The comped home-court sit takes the **min $2 buy-in (80)**; could drop the full
  comped 200 on the table instead.

## Career vs custom (generic) sandbox separation
Jeff wants to later split career mode from a generic/custom sandbox and **keep the
rigged stuff out of custom sandboxes**. Good news — it's already gated per-sandbox,
so this is a toggle + audit later, not a refactor:
- **`career_progress.career_active`** (per `(sandbox, owner)`) defaults **False =
  plain full lobby**. The keyring filter, intake, and all Circuit behavior only
  engage when True. A custom sandbox simply never flips it on.
- **Scripted scenes** only fire at tables in the `cash_mode/table_scenes` registry,
  and those tables are only **seeded** into a brand-new *career* sandbox
  (`classify_new_player` → "seed" path in `get_lobby`). A custom sandbox has no
  `cash-scene0-*` table, so `_scene_for_game` → None and the driver is a no-op.
- **Comp-return** is gated on `career_active and tutorial_complete`; the **mentor
  stake** (when built) gates on `career_progress`. Neither touches a non-career
  sandbox.
- **Discipline to keep:** every new career/rigged feature stays gated on
  `career_active` / the scene registry — never inline in a generic cash path. Then
  "break out career mode" later is: a sandbox-mode setting that controls whether
  `career_active` is ever set, plus a one-pass audit that nothing rigged leaks when
  it's off.

## Resetting a player's career (dev)
```bash
docker compose exec -T backend python scripts/reset_career.py guest_jeff
docker compose restart backend   # REQUIRED — evicts the in-memory game
```
Then navigate to **`/cash`** (the lobby), not any old `/game/...` URL.

## Key decisions (from the design riff)
- **Sal Monroe** (display name; id stays `sal_moretti` — "Salmon Roe", a quiet
  fish pun). Don't rename the id.
- Fish are literally fish; main cast aren't; **never confirm if Sal is**.
- Teaching is **invisible**; the comped 200 chips is "the house stakes the fish".
- Teaching hands are **real famous hands**, filtered for **skill not luck**, and
  cast so the **hero does what the legend got wrong** (Farha's fold, Seidel's
  call). Library + the skill/lore split: `CASH_MODE_FAMOUS_HANDS_LIBRARY.md`.
