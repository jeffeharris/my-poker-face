---
purpose: Design and phased build plan for a non-counting Training/Coaching game mode with difficulty tiers, scripted spots, an interactive intercept coach, and a read-the-player drill
type: spec
created: 2026-05-29
last_updated: 2026-05-29
---

# Training / Coaching Mode

A practice mode where the player plays poker against selectable-difficulty
opponents to learn, with the coach auto-engaged. Games here **do not count**
toward bankroll, cash P&L, prestige/renown, AI relationship memory, or
leaderboards — but **do** feed the per-user coach skill-progression record.
The focus is guidance and instruction, not competition.

## Goals

- A safe sandbox to try ideas and practice specific situations.
- Choose **easier or harder** opposition (difficulty tiers now; per-seat custom
  picker later).
- **Pre-defined scenarios**: table presets (heads-up, short-stack, 6-max,
  deep-stack) and **scripted spots** (fixed hole cards + board + stacks,
  replayable drills).
- **Auto-engaged coach**: proactive tips locked on, inline post-action skill
  feedback, one-tap hand review, an optional recommended-action highlight, and
  a new **interactive intercept** loop (the coach probes *why* before you
  commit).
- A **"Read the Player"** drill: anonymized opponents secretly assigned a
  playstyle/archetype, so the player learns to watch and adapt.

## Non-goals (v1)

- No per-seat custom opponent picker (deferred follow-on; the design leaves a
  seam for it).
- No measured "did the player adapt" metric in Read-the-Player (coach narrates
  adaptation; only the *identification* is scored).
- No new poker-engine behavior. Everything is init-time configuration + reuse.

---

## Architecture decision

**Training is a thin sibling game mode**, modeled exactly on cash mode: a
`train-` `game_id` prefix plus a `training_mode=True` flag on `game_data`. The
poker engine, game page, socket flow, and the entire coach system are reused
unchanged. The genuinely new concepts — the scenario model, the state builder,
the opponent roster, and the intercept protocol — live in a dedicated
top-level `training/` package so they don't crowd the already-large route
files.

This is the **recommended hybrid**: a clean `training/` package for the domain
model, pragmatic reuse of the existing action endpoint for normal play, and a
blocking wrapper endpoint *only* for the intercept (the one interaction that
must pause action submission).

### Why not a unifying `GameMode` abstraction

The tournament and cash builders are already ~80% parallel copies that diverge
by stamping flags on the `game_data` dict. That flag-on-dict pattern is
idiomatic here, and the only place that must re-derive mode is cold-load, where
a `game_id`-prefix check suffices. A `GameProfile` dataclass would touch
`game_handler.py` in many places and add serialize/deserialize cost with no
consumer benefit. We follow the established pattern instead.

---

## Non-counting guarantee (suppression by wiring-absence)

The codebase already suppresses writes structurally: *don't wire the repo, and
its writes don't happen.* A training game inherits this for free.

`_build_training_game()` **deliberately does NOT wire**:

| Write surface | Suppression mechanism |
|---|---|
| Bankroll / chip ledger | Only written from cash routes; training never uses them |
| Tournament placement/elimination | Gated on `tournament_tracker` presence; training omits it |
| `cash_pair_stats` | Gated on `set_relationship_repo(..., cash_mode=True)` (`hand_outcome_detector.py:1199` returns before cash P&L when `cash_pair_repo is None`) |
| `relationship_states` | **NOT gated by `cash_mode`** — `OpponentModelManager.record_event()` (`opponent_model.py:2072`) writes these whenever *any* relationship repo is wired (`memory_manager.py:158/207`). Training must **never call `set_relationship_repo` at all** — `cash_mode=False` is insufficient. |
| Prestige / holdings snapshots | Background ticker keyed on lobby sandbox sessions; training has no `sandbox_id` |

> **Correction (Codex review, 2026-05-29):** an earlier draft claimed
> `cash_mode=False` suppresses relationship writes. It does not — only
> `cash_pair_stats` is `cash_mode`-gated; `relationship_states` fires for any
> wired relationship repo. The hard rule is: **training does not wire a
> relationship repo, period.**

`_build_training_game()` **does forward** (write-allowed):

| Write surface | Why kept |
|---|---|
| `owner_id` + `coach_repo` | Drives `_evaluate_coach_progression()` → `player_skill_progress` (the one persistent write training *wants*) |
| `hand_history` / `hand_equity` | Powers the coach's hand-review and equity context; harmless, useful |
| `api_usage` | Cost accounting; bots are rule/tiered (no decision LLM) and `ai_chat=False`, so cost is dominated by coach calls |

**Verification is structural, not runtime**: there are no scattered
`if training_mode:` guards to suppress economy. The guarantee is an audit of
`_build_training_game()` against the "does NOT wire" checklist, backed by a test
that runs a full hand in a training game and asserts zero rows in
`cash_pair_stats` / `relationship_states` and ≥1 row in `player_skill_progress`.

Also: exclude `train-` games from the user's save-game limit and from
`list_games()` (training sessions are ephemeral, like cash), and give them a
short TTL.

---

## Domain model (`training/` package)

```
training/
  __init__.py
  scenario.py          # TrainingScenario + TablePreset | ScriptedSpot variants
  scenario_library.py  # JSON loader + registry + get/list
  state_builder.py     # build_state_from_scenario() → StateMachineAdapter
  opponent_roster.py   # difficulty tiers, anonymization, resolve_opponents()
```

### Scenario model — `training/scenario.py`

Typed variants of one concept. Storage is **JSON files in
`config/training_scenarios/`**, loaded once at startup into a module-level
registry. Scenarios are authored content (version in git, diff-readable, no
migration), not user data, and need no relational lookups.

```python
@dataclass(frozen=True)
class TablePreset:
    kind: str = "table_preset"          # discriminator
    big_blind: int = 50
    starting_stack_bb: int = 100        # in big blinds
    num_seats: int = 6                  # 2 = HU, 3-4 short, 6 standard, 9 deep
    blind_growth: float = 1.0           # 1.0 = fixed blinds (training default)
    hands_per_level: int = 999

@dataclass(frozen=True)
class ScriptedSpot:
    kind: str = "scripted_spot"
    phase: str = "PRE_FLOP"             # PokerPhase name
    hole_cards: list[str] = field(default_factory=list)        # ["Ah","Kd"]
    community_cards: list[str] = field(default_factory=list)
    hero_stack: int = 1000
    villain_stacks: list[int] = field(default_factory=list)
    pot: int = 0
    hero_bet: int = 0
    villain_bets: list[int] = field(default_factory=list)
    big_blind: int = 50
    hero_position: str = "BTN"

ScenarioConfig = TablePreset | ScriptedSpot   # add variants here as it grows

@dataclass(frozen=True)
class TrainingScenario:
    scenario_id: str
    name: str
    description: str
    tags: list[str]                     # ["preflop", "value-betting", ...]
    difficulty_hint: str                # "easy" | "medium" | "hard" | "any"
    config: ScenarioConfig
    coach_focus_skills: list[str] = field(default_factory=list)  # skill IDs
```

Example (`config/training_scenarios/defend_cbet.json`):

```json
{
  "scenario_id": "defend_cbet",
  "name": "Defending a C-Bet",
  "description": "You raised pre, hit top pair, villain c-bets 2/3 pot.",
  "tags": ["postflop", "value", "facing-aggression"],
  "difficulty_hint": "medium",
  "config": {
    "kind": "scripted_spot",
    "phase": "FLOP",
    "hole_cards": ["Ah", "Ks"],
    "community_cards": ["Kc", "7d", "2h"],
    "hero_stack": 4000, "villain_stacks": [3800],
    "pot": 450, "hero_bet": 0, "villain_bets": [267],
    "big_blind": 100, "hero_position": "BTN"
  },
  "coach_focus_skills": ["respect_big_bets", "have_a_plan"]
}
```

### State builder — `training/state_builder.py`

```python
def build_state_from_scenario(scenario, player_name, opponent_names,
                              random_seed=None) -> StateMachineAdapter: ...
```

- **TablePreset** → identical to the tournament builder:
  `initialize_game_state(...)` + `PokerStateMachine(state, BlindConfig(...))` +
  `run_until_player_action()`.
- **ScriptedSpot** → build a `PokerGameState` (frozen dataclass `.update()`s)
  with explicit per-player stacks, pre-dealt hole cards, `community_cards`,
  pot/bets, and phase, then `PokerStateMachine.from_saved_state(state, phase)`.
  This reuses the exact cold-load entry point — **no engine change**. Do **not**
  call `run_until_player_action()` afterward; the state is already positioned at
  the hero's turn.

**Deck construction for scripted spots**: take
`create_deck(shuffled=True, random_seed=seed)`, remove the pre-placed cards
(hero hole + **villain holes** + community), and leave the rest as the live deck
for the runout. Live-deck count = `52 − len(hero_hole) −
len(villain_holes_total) − len(community_cards)` (the earlier `52 − 2 −
len(community)` was wrong once villains are dealt — Codex review).

**`from_saved_state` is NOT a validator** (Codex review):
`PokerStateMachine.from_saved_state` (`poker_state_machine.py:429`) just wraps
the supplied state + phase — it will happily accept an incoherent spot. The
builder must enforce every invariant itself, and **`hero_position` is not
cosmetic**: position (`small_blind_idx`/`big_blind_idx`/`table_positions`/
next-street first actor) is derived from `players` order + `current_dealer_idx`
(`poker_game.py:156/262/606`), not a stored field. The factory must set
coherently:

- `players` order + `current_dealer_idx` so the intended `hero_position` is the
  real position (else "BTN hero facing a flop bet" becomes "wrong seat acts next
  street" after `advance_to_next_active_player()`).
- `current_player_idx` in range and pointing at the human; `awaiting_action=True`
  (else `progress_game()` may deal/advance/evaluate before the player acts).
- `phase` consistent with `len(community_cards)` (FLOP=3, TURN=4, RIVER=5).
- `pot` dict includes `'total'` (saved paths read `pot['total']` directly —
  `game_repository.py:115`).
- per-player betting-round fields coherent: `bet`, `highest_bet`, `has_acted`,
  `last_raise_amount`, `raises_this_round`, `current_ante` — legal actions are
  derived from these (`poker_game.py:225`).

**Ghost-seat guard (critical — recurring bug class here)**: the factory MUST
`assert state.players[state.current_player_idx].is_human` and assert
`awaiting_action` before returning. Validate scenarios at **library-load time**,
not game-start, so a malformed JSON fails fast. Unit test asserts: human at
`current_player_idx`; live deck count matches the formula above; pre-placed cards
absent from the deck; `awaiting_action` True; phase↔community-count consistent.

### Opponent roster — `training/opponent_roster.py`

Maps difficulty → a list of opponent entries, slotting into `assign_bot`'s
existing `mode=` hook. Easy = loose-passive rule bots / fish; Medium = mixed /
baseline solver; Hard = `sharp` tiered solver.

```python
@dataclass(frozen=True)
class RosterEntry:
    bot_type: str                # "sharp" | "casebot" | "gto_lite" | "rulebot"
    strategy: str | None         # rule strategy: "abc","fish","pot_odds_robot",...
    display_name: str
    anonymized: bool = False     # Read-the-Player: blank avatar + neutral name
    secret_archetype: str | None = None  # the assigned playstyle to reveal later

DIFFICULTY_ROSTERS: dict[str, list[RosterEntry]] = {
    "easy":   [RosterEntry("rulebot","abc","ABC"),
               RosterEntry("rulebot","fish","Fish"),
               RosterEntry("rulebot","foldy","Foldy")],
    "medium": [RosterEntry("casebot",None,"Case Bot"),
               RosterEntry("gto_lite",None,"GTO Lite"),
               RosterEntry("rulebot","pot_odds_robot","Pot Odds")],
    "hard":   [RosterEntry("sharp",None,"Sharp")],
}

def resolve_opponents(difficulty, num_seats, *, anonymized=False) -> list[RosterEntry]: ...
```

The future per-seat picker becomes an override layer inside
`resolve_opponents()` — no structural change.

---

## Backend surface (`flask_app/routes/training_routes.py`)

A `training_bp` blueprint registered alongside the others.

```
POST   /api/training/start              { scenario_id?, preset_id?, difficulty } -> { game_id }
POST   /api/training/restart            { scenario_id, difficulty } (re-run, fresh seed/villains)
GET    /api/training/scenarios          list catalog (filter by difficulty/tag)
GET    /api/training/scenarios/<id>     one scenario definition

# Intercept (Phase 4) — the only blocking action path
POST   /api/training/<game_id>/action          { action, amount, reasoning? }
POST   /api/training/<game_id>/intercept-reply  { reply, confirm_action, confirm_amount }
GET/POST /api/training/<game_id>/intercept-config { enabled, mode }

# Read-the-Player (Phase 5)
POST   /api/training/<game_id>/read-guess       { seat, guess } -> { correct, reveal, score }
```

`_build_training_game()` stamps on `game_data`:

```python
game_data['training_mode'] = True
game_data['training_scenario_id'] = scenario_id   # for restart
game_data['training_difficulty'] = difficulty
game_data['coach_config'] = {'mode': 'proactive'} # coach auto-on, locked
game_data['intercept_state'] = None               # Phase 4
# ABSENT on purpose: tournament_tracker, sandbox_id, cash_mode, relationship_repo(cash_mode=True)
```

**Cold-load (must-fix, was under-scoped — Codex review)**: the cold-load path
(`game_routes.py:638/785/843`) currently **only special-cases `cash-`**, so any
evicted `train-` game would be rebuilt as a **tournament**: it wires a
relationship repo (→ leaks `relationship_states`), creates a `TournamentTracker`,
and **loses `training_mode=True` entirely** because `game_data` flags are not
persisted by `save_game` (`game_repository.py:55`). Required:

- Add an explicit `is_training_game = game_id.startswith("train-")` branch that
  re-derives mode from the **prefix** (the only durable signal), mirroring the
  cash branch.
- In that branch: do **not** wire a relationship repo, do **not** create a
  `TournamentTracker`, and re-set `training_mode=True` +
  `coach_config={'mode':'proactive'}` (coach mode persistence below).
- **Clear** `intercept_state` (never persisted — a refresh mid-intercept aborts
  the probe and re-shows the action buttons, avoiding a wedged game).
- Decide persist-vs-ephemeral explicitly (below). If training games are *not*
  persisted, the action routes must return a clean "session expired"
  (`RELOAD_REQUIRED`) instead of silently rebuilding as a tournament.

**Coach mode is not auto-on by default** (Codex review): new games stamp the
*user's* default coach mode via `stamp_coach_default_mode`
(`game_handler.py:4042`, `game_routes.py:1812`), which may be `off`. Training
must explicitly `save_coach_mode(game_id, 'proactive')` at creation **and** the
cold-load branch must reconstruct it (don't rely on the user default).

**Listing / limits / guests** (Codex review):
- `list_games()` excludes only `cash-` (`game_routes.py:504`) — add `train-`.
- There is no save-limit/TTL handling on the persisted-games path
  (`game_repository.py:55` persists all IDs uniformly). Pick one: don't persist
  training games, or give them an explicit short TTL/purge.
- Guest hand-limits fire on actions and increment at hand-end
  (`game_routes.py:1860/2336`, `game_handler.py:3533`). Decide whether training
  is guest-available and whether it counts against guest hands — branch
  explicitly; it is **not** exempt by default.

**Normal actions reuse the existing `/api/action` path** and add a
`skill_evaluation` field to the response when `training_mode=True` (the silent
`SkillEvaluation` from `_evaluate_coach_progression`, surfaced for inline
feedback). Only the intercept needs the training-specific blocking endpoint.

---

## Coach behavior in training mode

Reuses the production coach system (`coach_engine.compute_coaching_data`,
`coach_assistant.CoachAssistant`, `coach_progression`, `useCoach.ts`,
`CoachPanel`/`CoachBubble`).

| Policy | Trigger | LLM? | Blocks action? |
|---|---|---|---|
| Proactive tip | turn start (`isPlayerTurn`) — **locked on** in training | yes | no |
| Inline skill feedback | post-action hook; surfaced (not silent) | no | no |
| Intercept probe | player submits action + `should_intercept()` | yes | **yes** |
| Hand review | one-tap after hand | yes | no |
| Recommended-action highlight | **optional toggle** (existing `COACH_HIGHLIGHT_SOURCE` path) | no | no |

These compose as independent trigger surfaces with no shared state — not tangled
flags.

### Interactive intercept protocol (Phase 4)

A blocking two-turn protocol held in `game_data['intercept_state']`
(`None → PENDING → cleared`):

```
Player submits action
  -> should_intercept(game_data, action, coaching_data)?
     no  -> delegate to existing progress_game(); return normal response
     yes -> compute_coaching_data() (deterministic, no LLM)
            CoachAssistant.ask("What's your thinking here? <situation>")
            stash intercept_state = {action, amount, question, coaching_data}
            return { intercepted: true, question, stats }

Player replies (+ optional revised action)
  -> CoachAssistant.ask("Player says <reply>; action was <action>. Guide them.")
     clear intercept_state
     progress_game(confirmed_action)          # original OR revision
     return { coach_feedback, game_state }
```

`should_intercept()` sampling (mode default = **selective / mistakes-only** to
bound LLM cost):

```python
def should_intercept(game_data, action, coaching_data) -> bool:
    if not game_data.get('training_mode'): return False
    cfg = game_data.get('intercept_config', {})
    if not cfg.get('enabled', True) or game_data.get('intercept_state'): return False
    ev_lost = coaching_data.get('ev_lost') or 0
    if cfg.get('mode') == 'always': return True
    return ev_lost > 20 or action == 'raise'   # tune thresholds to avoid noise
```

**Robustness (the "frozen game" class)**: any error in resolve MUST clear
`intercept_state` and fall through to committing the original action. Add a
stale-intercept timeout so an abandoned probe never wedges the game.

**Concurrency (must-fix — Codex review)**: a new training endpoint is **not
sufficient** to gate actions. The existing HTTP *and* socket action paths
(`game_routes.py:1824/2307`) can still submit a turn while an intercept is
pending, and they `play_turn()` before the `progress_game()` lock is involved
(`game_routes.py:1856/1872`, `game_handler.py:3616`). Mitigations:

- When `intercept_state` is `PENDING`, the standard action paths must reject the
  action for training games (return a "resolve your intercept first" error)
  rather than committing it — guard at the same point that validates the turn.
- Serialize intercept mutation + commit under the existing per-game lock, or use
  an action nonce/version so a double-submit (or a stale client) can't
  double-commit.
- On eviction mid-intercept the in-memory state is gone and the action route
  returns `RELOAD_REQUIRED`; the client must treat that as "re-fetch state and
  re-show buttons," not an error.

---

## Read the Player drill (Phase 5)

Teaches opponent reading and adaptation. Reuses
`coach_engine._get_opponent_stats()` (per-opponent VPIP/PFR/AF already
computed).

- Game is created with `anonymized=True`: each seat gets a blank avatar + neutral
  name ("Player 2") but a `secret_archetype` — either a real personality's
  playstyle (via the tiered bot's archetype deviation) or a generic rule
  archetype (calling station / maniac / nit / TAG).
- The coach scaffolds the read ("Player 2 has VPIP 68% but raises 4% — what does
  that tell you?").
- After N hands the player taps **"I think Player 2 is a ___"**; reveal scores
  the **identification** (which archetype / name the leak). Two question framings:
  *identify the archetype* (which type) and *identify the leak* (e.g. "calls too
  wide, never folds to barrels → value-bet thin").
- **v1 scores identification only.** "Did the player adapt" is coach narration,
  not a measured metric (the measurement is the fuzzy part; deferred).

Architecturally this is just the `anonymized` + `secret_archetype` roster fields
plus a small guess/score endpoint and a reveal UI.

---

## Frontend

Reuses `GamePage`, `usePokerGame`, and the coach UI. New pieces:

```
react/.../components/training/
  TrainingLobby.tsx     # difficulty + mode/scenario selection
  ScenarioCard.tsx      # one scenario in the picker
  InterceptModal.tsx    # coach question + reply input + confirm/revise (body portal)
  InlineSkillFeedback.tsx  # brief post-action verdict, auto-fades
  ReadThePlayerPanel.tsx   # guess + reveal (Phase 5)
react/.../hooks/
  useIntercept.ts       # intercept pending state + reply (Phase 4)
react/.../types/training.ts
```

Modify: `App.tsx` (lazy `/training` route), `HomeMenu.tsx` (a "Practice"
mode-card beside The Circuit and Tournaments), `utils/gameId.ts`
(`isTrainingGameId`), `GamePage.tsx` (training badge, coach panel auto-open,
back-button routes to `/training`, mount `InterceptModal` as a **body portal**
per the modal-stacking convention).

`useCoach.ts` needs **no changes** — it works on any `game_id`; training games
just default to `proactive`.

---

## Phased build sequence

### Phase 1 — Sparring (MVP) ✅ DONE (2026-05-29, branch `training-room`, uncommitted)
- [x] `training/` package skeleton; `opponent_roster.py` with Easy/Medium/Hard (`easy`=fish/foldy, `medium`=gto_lite/casebot/baseline_solver, `hard`=sharp).
- [x] `training_routes.py` with `POST /api/training/start` (free-play + difficulty + opponent_count), `training_bp` registered.
- [x] `train-` `game_id` prefix; `training_mode=True` stamp; relationship repo NOT wired; `save_coach_mode(game_id, 'proactive')`.
- [x] **Cold-load `train-` branch**: `is_training_game` re-derived from prefix; skips relationship-repo + tournament-tracker wiring; sets `training_mode=True`. (Coach mode persists on the games row, reloads on its own.)
- [x] Excluded `train-` from `list_games()`. Persistence decision: persisted for resume + `_purge_training_games` keeps ≤1 per owner (bounds the saved-game-limit impact, mirrors cash). Guest hand-limit exemption: NOT done — training reuses the shared action path, so guest hands still count (revisit in Phase 4 with the action wrapper).
- [x] Test suite `tests/test_training_mode.py` (12 tests): roster mapping/cycling/fallback; route auth + invalid difficulty; **non-counting structural assertions** (no tournament_tracker key, `memory_manager._relationship_repo is None`, coach='proactive'); controller types; bot_types round-trip for cold-load; `handle_eliminations` no-ops without tracker; excluded from `/api/games`. (Suppression asserted structurally — repo unwired + no tracker — rather than via a played-hand row count, which is a stronger guarantee.)
- [x] `TrainingMenu.tsx` (difficulty cards + table-size) + CSS, `/menu/training` route, "Practice" mode-card on HomeMenu, `isTrainingGameId` util, back-nav + 404-recovery routing for `train-`. TS + eslint clean.
- [ ] Deferred polish: in-game "Training — doesn't count" badge in the game header (the table welcome message covers it for now); guest hand-limit exemption.

### Phase 2 — Table presets + inline feedback
- [ ] `scenario.py` + `scenario_library.py` + `config/training_scenarios/` presets (HU short/deep, 6-max, full-ring).
- [ ] `state_builder.py` TablePreset path; `GET /api/training/scenarios`.
- [ ] Surface `skill_evaluation` in the action response; `InlineSkillFeedback.tsx`.

### Phase 3 — Scripted spots
- [ ] `state_builder.py` ScriptedSpot path (`from_saved_state` + deck filtering + ghost-seat asserts) and its unit test.
- [ ] Initial drill catalog: one spot per coach skill (11) + extras for the hard ones.
- [ ] `ScenarioCard.tsx` selector; `POST /api/training/restart` (replay with fresh villains); "Play again" on game-over.

### Phase 4 — Interactive intercept coach
- [ ] `flask_app/services/intercept_service.py` (`should_intercept`, `begin_intercept`, `resolve_intercept`).
- [ ] `/api/training/<id>/action`, `/intercept-reply`, `/intercept-config`.
- [ ] `useIntercept.ts`, `InterceptModal.tsx`; route training actions through the wrapper.
- [ ] Stale-intercept timeout + error-clears-state; cold-load clears `intercept_state`.
- [ ] **Guard the existing HTTP + socket action paths**: reject a turn while `intercept_state` is `PENDING` (don't let the normal path commit it); serialize under the per-game lock or an action nonce.
- [ ] Optional recommended-action highlight toggle.

### Phase 5 — Read the Player
- [ ] `anonymized` + `secret_archetype` roster fields; anonymized game creation.
- [ ] `POST /api/training/<id>/read-guess` scoring (identification only).
- [ ] `ReadThePlayerPanel.tsx` (coach scaffolding + guess + reveal).

---

## Risks & notes

- **Ghost-seat / seat-orphan class** (recurring here): the scripted-spot factory
  is the highest-risk new code. Assert human at `current_player_idx`,
  `awaiting_action=True`, and a legal state before `from_saved_state`. Validate
  scenarios at library-load time, not game-start.
- **Frozen-game / wedge class**: never leave `intercept_state` set on error or
  cold-load; add a stale timeout; reject normal-path actions while a probe is
  pending (don't double-commit).
- **`relationship_states` is not `cash_mode`-gated** (Codex review): the only
  safe suppression is to never wire a relationship repo for training games —
  both at creation and on cold-load.
- **LLM cost**: bots are rule/tiered (no decision LLM) with `ai_chat=False`;
  cost is the coach. Default intercept to mistakes-only.
- **Cold-load divergence class** (cash mode has hit this repeatedly, and Codex
  confirmed `train-` currently rebuilds as a tournament): the `train-` cold-load
  branch is a **must-fix, not a polish item** — `game_data` flags aren't
  persisted, so mode is re-derived from the prefix. Match the cash precedent
  exactly for `list_games`/persistence.
- **`from_saved_state` does not validate** (Codex review): the builder owns every
  invariant (position/dealer math, betting-round fields, phase↔board count, pot
  `total`, deck count incl. villain holes). Validate at library-load time.
- The non-counting guarantee is **structural** (repos unwired), verified by
  audit + the suppression test (incl. a cold-load case) — not by runtime flags.
