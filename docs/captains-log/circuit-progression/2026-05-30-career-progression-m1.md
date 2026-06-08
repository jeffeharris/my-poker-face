---
purpose: Narrative log of building M1 of the cash-mode career-progression spine (the keyring + Scene 0 + scripted first vouch)
type: reference
created: 2026-05-30
last_updated: 2026-05-30
---

# Career Progression M1 — "the lobby is a keyring, not a menu"

## What we set out to do

Implement the thinnest playable slice of `CASH_MODE_CAREER_PROGRESSION.md`
(Act 1) with `CASH_MODE_CAREER_ENDGAME.md` (the three-act arc → mentorship
endgame) in mind. The thesis to prove end-to-end: **a brand-new player starts at
one intimate, pinned table (Sal "The Clock" Moretti + a fish + you) and earns a
door into a real cardroom** — money stops being the only lock; a *vouch* is the
first lock.

## Decisions taken (asked the user up front)

1. **Full M1 end-to-end** (schema + keyring + Scene 0 + scripted vouch + Sal +
   minimal frontend), not a thin first PR.
2. **A brand-new player sees ONLY the Scene-0 table** — all cardrooms and the
   casino floor hidden until revealed. Most faithful to the "intimate opening."
3. **Sal is a normal (non-circulating) persona, scene-pinned** — not a bespoke
   scripted controller. Reliable graduation with minimal new machinery.

## The shape that emerged

The key architectural insight (already in the plan, confirmed in code): **the
world doesn't shrink, the player's *view* does.** `ensure_lobby_seeded` still
seeds all 11 cardrooms and the economy still runs across them; `get_lobby` just
*filters* what it renders against a per-`(sandbox, owner)` `revealed_tables`
set. That made the keyring a cheap read-side layer rather than a surgery on the
economy.

Pieces, bottom-up:
- **schema v124 `career_progress`** — one JSON blob per (sandbox, owner):
  `career_active` master switch, `revealed_table_ids`, Scene-0 flags, home
  court, `vouched_by`. `CareerProgressRepository` (read-merge-write, like
  `user_preferences`).
- **`cash_mode/career_progression.py`** — the logic: `ensure_scene0_seeded`,
  `classify_new_player`, `visible_tables`, `evaluate_first_vouch`,
  `pick_home_court`, `fire_first_vouch`.
- **Sal Moretti + Loose Larry** added to `personalities.json` as
  `"circulating": false`; the seeder learned to honour that field.
- **`get_lobby`** wiring; **`game_handler`** scripted-pin + `_maybe_fire_career_vouch`;
  **`activity.py`** `EVENT_VOUCH`; minimal frontend.

## Wrong turns & corrections (the honest part)

- **The `career_active` default mattered more than I first thought.** My first
  instinct was "new player → keyring on." But existing playtesters have no
  `career_progress` row, and a naive filter would have *blanked their whole
  lobby*. The fix: `career_active` **defaults False = show everything**, and
  only a confirmed brand-new sandbox (zero tables at first sight) flips it on.
  The safe failure mode is "full lobby," never "blank lobby." Detection has to
  read the table list *before* `ensure_lobby_seeded` creates the cardrooms —
  "zero tables" is the only clean brand-new signal.
- **Conservation soft spot, twice.** (1) `ensure_ai_bankrolls_seeded` only
  covers *circulating* personas, so non-circulating Sal/Larry needed explicit
  bankroll seeding before the seat debit. (2) Code review caught a real
  double-debit window: if the table+debits persist but the progress save throws,
  a retry would re-debit. Fixed by checking `cash_table_repo.load_table` as the
  source of truth first (the same pattern `ensure_lobby_seeded` uses) — added a
  test for the partial-failure re-entry.
- **Test-ordering pollution.** My new integration test wired
  `career_progress_repo` into the module-global `extensions`, and the *existing*
  lobby-route test expects it `None`. Leaving it set sent that test through the
  keyring path against a stale repo → two failures. Classic xdist
  import-ordering gotcha (it's in `tests/CLAUDE.md`). Fixed by snapshotting +
  restoring the ext globals in my test's `tearDownClass`.

## Where it landed

309 insertions across 12 files + 2 new modules + 2 new test files. All targeted
tests green (career: 19, keyring route e2e: 2; cash_mode + repositories sweep:
exit 0; schema/migration: green). The live dev DB auto-migrated to v124 and
seeded Sal/Larry on restart.

**Endgame-aware:** the prestige keystone (`cash_mode/prestige.py`) already ships,
and Act-1's vouch model reads the same relationship graph the endgame's
reflected-prestige loop will. M1 writes `vouched_by` — the exact ledger M2's real
`vouch_ready` model layers onto.

## Addendum — scripted-spot Scene 0 (same day)

Playtesting surfaced a real problem: the modern "fish" (Loose Larry) is a tiered
`weak_fish` (VPIP~45/PFR~16 + over_bluff + sticky), not a cartoon donkey — so it
raises, c-bets, and folds air like a competent-ish player. A brand-new player
can't read "that's the mark." I'd also mis-described it earlier (I quoted the
*legacy rule-bot fish* that no longer exists — the fish is now a tiered
calling-station). Jeff caught it twice: first that the behaviour wasn't fishy,
then the sharp one — "the tieredbot has 0 llm calls too," which killed my "0
player_decision calls ⇒ it's the fish" diagnostic (sharp bots are also LLM-free
for decisions). Ground truth came from `hand_history` (Larry raising flop+turn,
shoving river) + the `strategy_pipeline_snapshot_json` showing his anchors
driving solver tables. Lesson logged in memory.

Fix direction (Jeff's idea): stop fighting realism — **preload the deck and
script the spot.** Pulled the plan's Scene -1 drill engine forward:
- Ported the `training/` **reconstruction engine** from the `training-room`
  branch (`scenario.py` `ScriptedSpot` + `state_builder.build_scripted_spot_state_machine`
  via `from_saved_state`). That branch's hard lesson — *"cut hand-authored
  drills; keep the reconstruction engine"* — doesn't bite a **tiny played-once
  onboarding script**, which is exactly where hand-authoring IS worth it.
- Authored ONE Scene-0 spot (`cash_mode/career_spots.py`): a **bluff-catch** —
  Larry's busted draw + river over-bluff pinned, hero has top pair, correct line
  = **call**. Teaches the exact leak Jeff saw live. Sal narrates the read + a
  tailored line per action.
- **Replaced the grind-the-fish gate**: graduation = passing the scripted spot
  (`POST /api/cash/scene0/spot/act` → `fire_first_vouch`), not "+40 on Larry."
  Removed the hand-boundary `_maybe_fire_career_vouch`.
- Frontend `Scene0Lesson` modal (auto-opens for a new player; Call/Fold; Sal's
  feedback; a pass opens the door). Two endpoints + API client + Lobby wiring.
- Tests: 7 engine/spot + 5 route (incl. fresh-owner isolation for the shared
  class DB). 114-test focused sweep green; TS + eslint clean.

## The modal was wrong — "The Circuit" narrative pivot (same day)

Playtesting the scripted-spot **modal** in the browser, Jeff (rightly) tore it
apart: a decontextualised Call/Fold pop-up in the lobby — "am I playing a hand
from the lobby? who's Sal? who's Larry? what do you expect me to get from this?"
And the sharper cut: *"that's not a scripted spot — the scripted spot would be me
playing a hand."* Dead right. I'd optimised for the fastest proof and shipped
something incoherent. The fix isn't polish; it's that **the curated hands must
play out at the table**, which is exactly the in-table injection I'd deferred as
"too big for one proof spot." The deferral *was* the bug.

That turned into a long, generative worldbuilding riff (Jeff driving, me
building). What we landed — now written into `CASH_MODE_CAREER_PROGRESSION.md`
as "The Circuit" canon:
- **Comedy, chill-absurd, teaching invisible.** No tutorial chrome.
- **The patrons are literally fish** (main cast aren't; *blub* tics; optional
  one-time Larry fish-flash via DALL·E; **Sal stays ambiguous, never resolved**).
- **The bank stakes the fish**; the economy is a closed, unexplained cycle (the
  diegetic skin on `closed_economy.py` — "nothing ever really happens").
- **The human is a silent wrong-turn** into **The Lucky Stack** diner (coffee +
  biscuits and gravy), comped the 200-seed, christened **"Juke Joint Jeff"** — a
  fish-name **shed at the first vouch** ("a fish can't get vouched, kid").
- **Sal "feeds the fish"** — made it out, comes back to vibe; bonds because
  *fish can't hear strategy talk and you could* (one mechanic = teach + bond +
  reveal); and he's a **literal preview of the endgame** (you come back to feed/
  mentor a player — the protégé sink). Onboarding and endgame, opposite chairs.
- **Scene 0 = a rigged ~10-hand session at the table** (hand 1 normal, 3 teaching
  spots among ~7 quiet fillers, nobody busts), Sal narrating in chat. **The modal
  is to be deleted.**
- Cardroom venues **scattered**; casino floor maybe one building (open).

The M1 plumbing (keyring, `career_progress`, vouch, the `from_saved_state`
reconstruction engine) all stands — only the *presentation* changes. Lesson for
the log: a "proof" that strips the context can prove the plumbing while hiding
that the whole experience is wrong. Cheaper to have built one real in-table hand.

## In-table Scene 0 built (same day)

Rebuilt Scene 0 to play on the felt, per the pivot. The crux was rigging the
deck at the live table: traced the deal (sequential hole-card pairs by player
index, then flop/turn/river off the top, **no burns**) so a pre-stacked deck
pins every card. Added a one-shot **`provide_hand_deck`** seam to the state
machine mirroring the existing seed override — and crucially, hand 1 stays a
normal deal, which sidesteps the *only* place the engine force-creates a deck
(`initialize_hand_transition` on hand 0); hands 2+ deal from the deck
`hand_over_transition` sets, so overriding it there Just Works.

`cash_mode/career_scene.py` = the script + `build_hand_deck` (orders the stack
from live seating) + `resolve_scripted_action` (cast intents → legal moves).
`game_handler` got three hooks: init (roles + opening line), scripted-action
injection for the fish/mentor in `handle_ai_action` (so Larry reliably
over-bluffs the river and Sal folds away — rigged *cards* alone wouldn't
guarantee the *line*), and a hand-boundary driver that judges the teaching hand
(hero folded = failed), narrates Sal in table chat, pre-stacks the next hand,
and graduates (first vouch) when the script ends. Judging by "did the hero fold"
is a deliberate proxy — collapses the whole thing to two hooks instead of also
intercepting the live human action.

Deleted the whole modal path (Scene0Lesson + CSS + Lobby wiring, the
`/api/cash/scene0/spot*` endpoints, `career_spots.py`, and the `training/`
reconstruction engine I'd ported for it — re-portable from training-room when
hand-replay needs it). Tests: deck seam (3), career_scene rig+resolver (12),
in-table hook chain (4); TS + eslint clean. Net: the curation lives in the felt,
not a pop-up.

## Next

M2 = relationship-driven `vouch_ready` (respect-gated, likability-driven,
one-per-AI over the whole roster); M3 = training lounge ($80 freeroll); M4 =
staking gate on the second-cardroom milestone + the Scene-0 can't-fail-out
backstop + failure states. M1 deliberately leaves the casino floor hidden for
keyring players, so a busted new player re-sits the still-visible Scene-0 table
via the existing rebuy path until the M4 backstop lands.
