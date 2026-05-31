---
purpose: Continuation/handoff for the Career Progression M1 work on branch circuit-progression — what's built, how it works, what's verified, and what to do next
type: guide
created: 2026-05-31
last_updated: 2026-05-31
---

# Career Progression M1 — handoff (branch `circuit-progression`)

Pick-up doc for a fresh context. Everything here is **uncommitted** on
`circuit-progression`. Design canon lives in
`docs/plans/CASH_MODE_CAREER_PROGRESSION.md` (the "The Circuit" section);
narrative log in `docs/captains-log/circuit-progression/`.

## TL;DR — where we are

We built **Act-1 "The Circuit"**: a new player wanders into the **Lucky Stack**
diner, gets a comped stake + an alliterative tourist **fish-name**, sits at a
pinned **Scene-0** table with mentor **Sal Monroe** + the fish **Loose Larry**,
plays a **rigged 10-hand tutorial** (deck pre-stacked so the lessons always
appear), and on completion **Sal vouches them into a home-court cardroom** (the
keyring opens one door). Sal narrates via a **floating portrait**.

All unit/integration tests green. **The one thing NOT yet confirmed end-to-end
is the rigged deck actually dealing the scripted cards during real live play**
(see Known Risks #1) — every manual test so far hit a *stale pre-rig game* and
got the un-rigged random cards. That's the first thing to verify.

## What's built (with file pointers)

### The keyring (M1 core)
- **schema v124 `career_progress`** (`poker/repositories/schema_manager.py`,
  migration `_migrate_v124_create_career_progress`) — per-(sandbox, owner) JSON
  blob. Repo: `poker/repositories/career_progress_repository.py`
  (`CareerProgress` dataclass + `CareerProgressRepository`). Wired in
  `poker/repositories/__init__.py` + `flask_app/extensions.py` (`career_progress_repo`).
  Fields: `career_active` (master switch, **default False = full lobby**, so it
  can never blank a legacy player), `revealed_table_ids`, `scene0_*`,
  `tutorial_complete`, `home_court_table_id`, `vouched_by`, `intake_complete`,
  `player_name`, `fish_name`, `chat_intensity`, `chat_style`.
- **Lobby filter** — `flask_app/routes/cash_routes.py` `get_lobby`: detects
  new-vs-legacy (`career_progression.classify_new_player`), seeds Scene 0 for a
  brand-new sandbox, and filters tables to `career_progression.visible_tables`
  (scripted + revealed only when `career_active`).

### `cash_mode/career_progression.py` (logic + intake)
- `ensure_scene0_seeded` — pins the `table_type='scripted'` Scene-0 table (Sal +
  Larry, conservation-safe bankroll-debit seating).
- `classify_new_player` / `visible_tables` — new-vs-legacy + the keyring filter.
- `make_fish_name` — **deterministic alliterative** name: a per-letter adjective
  bank (`FISH_NAME_ADJECTIVES`, ~70 words a–z) + the player's own first name
  ("Jeff" → "Juke-Joint Jeff"). The LLM is NOT used for the name (it kept
  dropping it → "Lost Little Larry").
- `generate_intake_persona` — fish-name (rule-based) + a funny one-line **bio**
  (LLM, fast tier, with a canned fallback). `intake_avatar_prompt` — avatar
  generation seam (not fired).
- `fire_first_vouch` — reveals a random `$2` home court, records `EVENT_VOUCH`.
- Constants: `SAL_ID="sal_moretti"`, `SAL_NAME="Sal Monroe"`,
  `SCENE0_FISH_ID="loose_larry"`, `SCENE0_TABLE_ID="cash-scene0-001"`.

### `cash_mode/career_scene.py` (Scene 0 played at the table)
- `SCENE0_SCRIPT` — 10 hands: hand 0 normal, then VALUE / BLUFF-CATCH /
  DISCIPLINE teaching hands among quiet rigged fillers. Each `Scene0Hand` has
  pinned `holes` (per role) + `board` + `fish_plan`/`mentor_plan` (per-phase
  scripted intents) + `sal_setup`/`sal_pass`/`sal_fail` + `pass_when`.
- `build_hand_deck` — orders a pre-stacked 52-card deck from the live seating.
- `resolve_scripted_action` — turns a scripted intent (`bluff`/`bet`/`passive`/
  `limp`/`fold`) into a legal action with bet sizing (`SIZE_FRAC`) capped at
  `MAX_SCRIPTED_BET_STACK_FRAC=0.5` so the cast never busts.
- Sal's lines coach **Larry's public behavior + principles, never the hero's
  hole cards** (he can't see your hand). `SAL_GRADUATION` send-off.

### The deck-rigging seam (engine)
- `poker/poker_state_machine.py` — one-shot `provide_hand_deck(deck)` (mirrors
  the existing seed override). A provided deck replaces the shuffle for exactly
  one hand, then clears. Honored in `initialize_hand_transition` (hand 0) and
  `hand_over_transition` (hands 2+). **Hand 1 is a normal random deal** (sidesteps
  the only forced `create_deck`); teaching hands are 2+.

### The game-handler hooks (`flask_app/handlers/game_handler.py`)
- Scripted tables are PINNED: skipped in `refresh_unseated_tables`
  (`cash_mode/lobby.py`) AND the human-table hook `_refresh_lobby_table_for_session`.
- `_init_scene0` / `_advance_scene0` / `_scene0_scripted_action` /
  `_graduate_scene0` / `_sal_say` — at the Scene-0 table: init roles + opening
  line on first AI turn; per-hand-boundary judge (did the hero fold? keyed to
  `pass_when`) + Sal narration + pre-stack the next hand's deck; graduate (vouch)
  when the script ends. Scripted actions for Larry/Sal injected in
  `handle_ai_action` before the bot decision.

### Intake + characters (frontend)
- `POST /api/cash/intake {name,intensity,style}` (`cash_routes.py`) → persona;
  lobby returns `intake_needed` + `fish_name`. `_resolve_player_name` returns the
  **fish-name during Scene 0**, shed to the chosen name after graduation.
- `react/.../cash/LuckyStackIntake.tsx` (+ `.css`) — the cold open: waitress
  portrait + name + **3 plain vibes** (Friendly/Cocky/Ruthless → quick-chat
  tones + intensity) → reveal (fish-name + bio + Sal nodding) → **"Take the seat"
  drops straight into the Scene-0 game** (not the lobby). Sets
  `localStorage.quickchat_intensity`.
- `react/.../cash/Lobby.tsx` — sticky `showIntake` gate (so the reveal doesn't
  flash-unmount when the poll flips `intake_needed`); `handleIntakeTakeSeat` sits
  at the scripted table.
- `react/.../mobile/SalFloater.tsx` (+ `.css`) — Sal's lines pop as a floating
  transparent portrait + speech bubble; `FloatingChat` gated to skip Sal.
- Assets: `react/react/public/waitress.png`, `sal.png` (transparent cutouts via
  Runware imageInference→imageBackgroundRemoval; regen with `scripts/gen_waitress.py`).

### Tests (all green)
`tests/test_cash_mode/test_career_progression.py`, `test_career_scene.py`,
`test_scene0_intable.py`, `tests/test_scripted_deck_seam.py`,
`tests/test_cash_career_lobby_route.py`. TS + eslint clean. Full `test_cash_mode`
bucket exit 0 as of the keyring landing.

## Status
- **Branch:** `circuit-progression`. **Nothing committed** — all the above is
  working-tree changes (see `git status`). Schema is at **v124**.
- The live dev DB (`guest_jeff`) has been migrated + seeded; reset to a fresh
  career via `scripts/reset_career.py` (see below).

## Known risks / gotchas (READ before continuing)
1. **Live rig unverified.** The deck-provision works in tests + the data-level
   hook chain, but a fully fresh *live* run (intake → sit → hands 2+ deal the
   scripted cards across real hand boundaries) has NOT been observed yet — every
   manual attempt hit a stale game. **Verify first:** reset, play through, check
   the VALUE hand deals AK vs K5 etc. If cards are random, trace whether the
   `provide_hand_deck` flag survives the between-hands auto-save (it's
   state-machine-internal, lost on cold-load; should survive in-memory continuous
   play).
2. **Renaming a persona while a game is live spawns a zombie.** Renaming
   `sal_moretti` mid-session made a cold-load auto-generate `sal_moretti_v2`
   ("Sal Moretti", `ai_generated`). Lesson: clear active games BEFORE renaming a
   persona. (The [[zombie-persona auto-create]] class.)
3. **Orphan `cash_sessions` resurrects deleted games.** Deleting only the `games`
   row leaves an `active` `cash_sessions` row → `_find_active_cash_game_id`
   returns it → 409 "session already active" → frontend routes to the 404'd game
   ("game no longer existed" loop). Always clear `cash_sessions` +
   `cash_session_events` + `games` together, then **restart backend** (evict the
   in-memory game). `scripts/reset_career.py` does the DB part.
4. **Scripted narration is coupled to the rig.** Sal's pass/fail lines assume the
   rigged cards dealt. If the rig misfires, the lines lie ("worse king" when you
   were beaten). Future hardening: narrate off the *actual* cards/outcome.
5. **Sal narrates pre-hand (anticipatory) + post-hand (judged) only** — no
   mid-hand/board-synced narration. Lines are written to never reference the
   hero's hole cards.

## Next steps (suggested order)
1. **Verify the live rig end-to-end** (Risk #1) — the gating unknown.
2. **"Sal stacks Larry" Scene-0 finale** (designed, not built): a final scripted
   hand where Sal busts Larry ("pay that man his money"), then graduates — closes
   the session with a bang instead of quiet fillers.
3. **Post-hand-1 "intro the lobby" beat** (Jeff wanted): after the first hand,
   surface the world/lobby with Sal + Larry.
4. **Commit** the M1 work (it's all uncommitted).
5. **M2** — real relationship-driven `vouch_ready` (respect-gated,
   likability-driven, one-per-AI) over the graph, layered on the `vouched_by`
   ledger.
6. **M3** — training lounge / scripted drills. NOTE the `training/` reconstruction
   engine was ported then deleted (superseded by `career_scene`); re-port from the
   `training-room` branch when hand-replay is built.
7. **Wire avatar generation** (seam: `intake_avatar_prompt` + `avatar_prompt` in
   the intake response).

## Resetting a player's career (dev)
```bash
docker compose exec -T backend python scripts/reset_career.py guest_jeff
docker compose restart backend   # REQUIRED — evicts the in-memory game
```
Then navigate to **`/cash`** (the lobby), not any old `/game/...` URL.

## Key decisions (from the design riff)
- **Sal Monroe** (display name; id stays `sal_moretti`) — "Salmon Roe," a fish pun
  that quietly feeds the "is Sal a fish?" ambiguity. Don't rename the id.
- Fish are literally fish; main cast aren't; **never confirm if Sal is** (no Sal
  fish-flash). Larry can flash as a fish once (`/sal.png`/asset pipeline exists).
- Intake is **snappy**: name + 3 plain vibes (no jargon, no chill/spicy toggle).
- After intake, **drop into the game**, then intro the lobby after hand 1.
- Fish-name is **deterministic + alliterative + keeps the player's name**; LLM
  only writes the bio.
- Teaching is **invisible**; the comped 200 chips is the "house stakes the fish"
  meta; the economy is a closed, unexplained cycle.
