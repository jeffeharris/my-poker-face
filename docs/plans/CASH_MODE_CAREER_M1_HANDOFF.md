---
purpose: Continuation/handoff for the Career "Circuit" Scene-0 work on branch circuit-progression — what's built, how it works, what's verified, and what to do next
type: guide
created: 2026-05-31
last_updated: 2026-06-01
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
finale**, **graduates** → walks back to the lobby (Sal **escorts** him there) →
**hands the comp back** (enters the lobby at 0) and is meant to be **staked by Sal
into a home-court cardroom**.

Session-2 status (newest first):
- **Comp-return** ✅ committed `91ca28b6` — on graduation the house reclaims the
  comp (player → 0, chips to the bank pool via the ledger, conservation-tested);
  the $2 home court is sponsor-eligible at 0 so there's no soft-lock.
- **Scene→lobby return + Sal's lobby handoff** ✅ committed `96583149`.
- **Finale (Sal stacks Larry)** ✅ committed `29e8cd8c` — mechanics unit-tested;
  the end-to-end "Larry busts to 0" still wants a **live playtest** (headless
  drivers can't replicate the handler betting loop).
- **Rig + scene system** ✅ (`1144fc2c`): seat-rotation bug fixed (name-keyed
  deck), cold-load survival, generalized into a reusable `table_scenes` registry,
  real famous-hand teaching spots, chat-bubble beat formatting.

**NEXT (not built): Sal *stakes* the home court** — see Next steps #1 + the
**open conservation question** there. The comp-return drops you to 0, but the
"Sal backs your first seat" half isn't wired yet (today the generic staking
system would offer you a backer from the home court's own seated AIs, not Sal).

## What's built (with file pointers)

### The keyring (M1 core)
- **schema v124 `career_progress`** (`poker/repositories/schema_manager.py`,
  `_migrate_v124_create_career_progress`) — per-(sandbox, owner) JSON blob. Repo:
  `poker/repositories/career_progress_repository.py`. Wired in
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

### Frontend
- `LuckyStackIntake.tsx` (cold open), `Lobby.tsx` keyring wiring.
- `SalFloater.tsx` — Sal's floating portrait; now plays a **queue** (every line
  surfaces, incl. the 3-line graduation reveal), on the HUD layer with the action
  bar raised above it. `chatText.tsx` formats inline `*action*` in both bubbles.

### Tests
`tests/test_scripted_deck_seam.py`, `tests/test_cash_mode/test_career_scene.py`,
`test_scene0_intable.py` (incl. the **rotation regression** + **cold-load
restore/persist** tests), `test_career_progression.py`,
`tests/test_cash_career_lobby_route.py`. Cash bucket green; TS + eslint clean.

## Status
- **Branch:** `circuit-progression`. Commits (newest→oldest): `91ca28b6`
  comp-return, `96583149` scene→lobby + Sal handoff, `29e8cd8c` finale + bubble
  formatting, `32fd6ed4` docs, `1144fc2c` the Circuit feature. Schema v124. Live
  dev DB (`guest_jeff`) migrated + seeded.

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
   backend. `scripts/reset_career.py` does the DB part.

## Next steps (suggested order)
1. **Sal *stakes* the home court** (the comp-return's other half — design locked
   with Jeff: a **stake** with friendly terms that introduces the staking system;
   **forgiveness** via a strong Sal↔player relationship through the *existing*
   forgiveness system, **NOT** a bespoke settlement hook). Build:
   - **Carve-out** so non-circulating **Sal Monroe** (`sal_moretti`) can be the
     lender for the pre-arranged mentor stake. He's filtered out of
     `list_eligible_for_cash_mode` (`visibility=public AND circulating=1`), so
     generic `sponsor_and_sit(lender_id=sal)` returns None. Add a mentor branch in
     `sponsor_and_sit` gated on `career_progress` (career_active + tutorial_complete
     + `home_court_table_id == table_id` + not `mentor_stake_used`) that builds
     Sal's offer directly with friendly terms (floor 1.0, rate 0), sets
     `offer_lender_id=SAL_ID` (→ personality/pure stake path), and flips a new
     one-shot `mentor_stake_used` flag on `CareerProgress`.
   - **Lobby** exposes `mentor_stake {table_id, lender_id, lender_name='Sal
     Monroe'}` when graduated + broke + home court set + not used.
   - **Frontend** routes the home-court sit to `sponsor_and_sit(lender_id=sal_moretti)`
     instead of the generic StakeOfferModal.
   - **Forgiveness** (deferred, no settlement code): seed a strong Sal↔player
     relationship ("mentors start warm") so the existing relationship-driven
     forgiveness handles a busted carry.

   ⚠️ **OPEN CONSERVATION QUESTION — RESOLVE FIRST (could mint chips):** how is a
   *personality* stake's principal funded/debited from the lender? `_build_cash_game`
   debits only **seated** AI bankrolls and is **not** passed a lender; the
   post-stake block mints only for *house* loans (`offer_lender_id is None`). The
   code comment claims personality loans are "pure transfers (AI lender's bankroll
   → player table stack via the AI debit step in `_build_cash_game`)" — which
   implies funding is **coupled to the lender being one of the seated AIs**
   (Lobby v1.5). If so, a **non-seated Sal would not be debited → minted chips**.
   First write a paired before/after audit test for an existing personality stake;
   if funding needs seating, debit Sal explicitly in the mentor branch via a paired
   ledger entry (AI bankroll → player table stack). (Jeff's intent: stakers gate on
   **capacity ~2× high buy-in, NOT seating** — but the general offers-route seated
   narrowing is being **left as-is** per his call; only the mentor stake needs the
   non-seated funding path.)
2. **Verify the finale live** — confirm Sal actually busts Larry to 0 in a real
   playthrough (headless drivers couldn't replicate the betting loop).
3. **M2 — real `vouch_ready`** (respect-gated, likability-driven, played-with,
   one-per-AI) over the relationship graph; evaluate on the world ticker. FIRST
   step is the regard-edge **instrumentation/logging** M1 was meant to leave so
   the thresholds (~0.70 like / respect floor) are tuned from real data — verify
   that logging exists before tuning. See `CASH_MODE_CAREER_PROGRESSION.md` § M2.
4. **Generalize the floater** to any scene mentor (risk #2) when a second scene
   needs it.
5. **M3** — training lounge / scripted drills (re-port the `from_saved_state`
   reconstruction engine from `training-room` when hand-replay is built; the
   `table_scenes` system is the natural host).
6. **Wire avatar generation** (seam: `intake_avatar_prompt`).

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
