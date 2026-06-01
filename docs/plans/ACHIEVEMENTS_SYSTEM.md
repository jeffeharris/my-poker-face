---
purpose: Implementation plan for a player-facing achievement system (21 unlockable achievements across cash + tournament play) with a declarative, easily-extended registry.
type: spec
created: 2026-05-27
last_updated: 2026-05-29
---

# Achievements System — Implementation Plan

## 1. Goal

Add a player-facing **achievement system**: players unlock badges for milestones
and skillful play. v1 ships **21 achievements** spanning both **tournament** and
**cash/career** play. On unlock the player sees a **celebratory trophy card** and
a **lobby activity-ticker line**; they browse the full locked/unlocked catalog from
a **lobby drawer** and a grid on the **Career Stats page**.

Two product constraints drive the architecture:

- **The catalog will grow.** Adding an achievement must be a *one-entry* change to a
  declarative registry — no schema change, no engine change. (Approach B, below.)
- **Forward-only.** Counters start at 0 at launch; nothing is backfilled from history.
  No surprise mass-unlocks, no migration backfill pass. Milestone checks (bankroll,
  stake level) still fire on the next qualifying event.

## 2. Chosen architecture (Approach B — clean package)

A self-contained `poker/achievements/` package + a repository + a thin Flask service
that glues "evaluate → persist → notify" and is called from each trigger surface.

```
poker/achievements/
  __init__.py            # re-exports: AchievementTrigger, ACHIEVEMENTS, AchievementEngine, facts
  definitions.py         # the declarative catalog: ACHIEVEMENTS: list[Achievement] + AchievementTrigger enum
  facts.py               # HandFacts / StakeOfferFacts / StakeSettleFacts dataclasses
  engine.py              # AchievementEngine.evaluate(trigger, facts) -> list[UnlockedAchievement]

poker/repositories/
  achievement_repository.py   # AchievementRepository over the v119 player_achievements table

flask_app/services/
  achievement_service.py      # evaluate via engine -> persist -> emit socket + ticker line

flask_app/routes/
  achievement_routes.py       # GET /api/achievements  (catalog + this owner's state)
```

**Why a package, not a single module:** the user expects to keep adding achievements
and likely new *trigger surfaces* (e.g. "vice", "relationship milestone") over time.
The `definitions` / `facts` / `engine` split keeps each concern small and lets new
triggers slot in without touching unlock bookkeeping.

## 3. The achievement catalog (v21)

Thresholds are **tunable defaults** — they live as `target` on each definition.

| id | Name | Description (player-facing) | Trigger | `target` | Notes / data source |
|---|---|---|---|---|---|
| `first_blood` | First Blood | Win your first hand | HAND | 1 | counter: +1 on a won hand |
| `grinder` | Grinder | Play 100 hands | HAND | 100 | counter: +1 every hand |
| `table_captain` | Table Captain | Win 100 hands | HAND | 100 | counter: +1 on a won hand |
| `heater` | Heater | Win 3 hands in a row | HAND | 3 | streak: +1 on win, reset to 0 on a played-and-lost hand |
| `low_stakes_regular` | Low-Stakes Regular | Play 200 hands at the $2 tables | HAND | 200 | counter, gated on `stake_label == '$2'` |
| `royal_flush` | Royal Flush | Win a hand holding a royal flush | HAND | 1 | boolean: `won and winning_hand_rank == 1` |
| `monster_pot` | Monster Pot | Win a single pot of 5,000+ chips | HAND | 1 | boolean: `won and pot_won >= 5000` |
| `hero` | Hero | Win a hand with a hero call | HAND | 1 | boolean: `hero_call` (existing `HERO_CALL` detector, human = actor) |
| `stone_cold_bluff` | Stone Cold Bluff | Win a hand by betting everyone out | HAND | 1 | boolean: `bluff_win` (non-showdown win, human last aggressor)¹ |
| `bounty` | Bounty | Knock an opponent out of the hand | HAND | 1 | boolean: `opponents_busted >= 1` |
| `double_knockout` | Double Knockout | Bust two opponents in a single hand | HAND | 1 | boolean: `opponents_busted >= 2` |
| `high_roller` | High Roller | Grow your bankroll to 100,000 chips | HAND | 1 | boolean: `bankroll >= 100000` (cash only) |
| `stepping_up` | Stepping Up | Take a seat at a $50+ stake | HAND | 1 | boolean: `stake_rank('$50') reached` (cash only) |
| `champion` | Champion | Win a tournament | HAND | 1 | boolean: `tournament_won` (final hand) |
| `backer` | Backer | Get a stake offer to an AI accepted | STAKE_OFFER | 1 | boolean: `accepted` |
| `loan_shark` | Loan Shark | Earn $10,000 lifetime profit from staking | STAKE_SETTLE | 10000 | cumulative: `+= max(0, net_for_player)` when `role == 'staker'` |
| `creditor` | Creditor | Have an AI end up owing you money | STAKE_SETTLE | 1 | boolean: `role == 'staker' and carry_created` (borrower busted under principal) |
| `richest_in_room` | Richest in the Room | Hold the highest net worth in your world | CASH_STANDING | 1 | boolean: `player_net_worth >= max_ai_net_worth` (sandbox-wide) |
| `apex_predator` | Apex Predator | Go net-positive against **every** AI in your world | CASH_STANDING | (dynamic) | collection vs the **full roster**: `positive_pairs_count >= eligible_count` (requires meeting *and* beating all); `dynamic_target = eligible_count`, progress = # AIs you're net-up on |
| `socialite` | Socialite | Meet **every** AI in your world | CASH_STANDING | (dynamic) | collection vs the **full roster**: `met_count >= eligible_count`; `dynamic_target = eligible_count`, progress = `met_count` |
| `fan_favorite` | Fan Favorite | Win an AI over completely (max likability toward you) | CASH_STANDING | 1 | boolean: `max_likability_toward_player >= 1.0` (the axis ceiling) |

¹ **Successful-bluff proxy.** v1 fires on a non-showdown win where the human was the
last aggressor (bet/raise that folded everyone out). A *true* weak-hand bluff (evaluate
the human's hole cards and require a weak made hand) is a clean follow-up — the `facts`
already carry hole cards, so it's a predicate tightening, not a structural change. Noted
as a known approximation.

**Categories** (for grouping in the UI): `milestone` (first_blood, grinder, table_captain,
low_stakes_regular, high_roller, stepping_up, richest_in_room), `skill` (royal_flush,
monster_pot, hero, stone_cold_bluff, bounty, double_knockout), `tournament` (champion),
`staking` (backer, loan_shark, creditor), `social` (apex_predator, socialite, fan_favorite).

**Collection achievements** (`apex_predator`, `socialite`) are gated on the **entire
eligible AI roster**, not the set you've happened to meet — otherwise they'd fire trivially
on your first session (beat the 2 AIs at your first table = "beaten everyone you've met").
The full-roster bar makes Apex Predator strictly harder than Socialite (you must *meet*
everyone **and** *beat* everyone) — a fitting career-endgame capstone. They use a **dynamic
target** (§4) = the live roster size, so the progress bar reads "12 / 18" against the real
denominator. Once unlocked, a later roster addition never re-locks them (the engine is
idempotent). *Open sub-option (§11): a per-pair "≥N hands of history" floor so a single
fluke hand against an AI doesn't count toward Apex Predator.*

## 4. Declarative definition shape

```python
# poker/achievements/definitions.py
from enum import Enum
from dataclasses import dataclass
from typing import Callable
from .facts import HandFacts, StakeOfferFacts, StakeSettleFacts, Facts

class AchievementTrigger(str, Enum):
    HAND = "hand"                  # evaluated at hand completion (cash + tournament)
    STAKE_OFFER = "stake_offer"    # evaluated when a player->AI stake offer resolves
    STAKE_SETTLE = "stake_settle"  # evaluated when a stake the player backs settles
    CASH_STANDING = "cash_standing"  # evaluated on the periodic net-worth/standing snapshot

@dataclass(frozen=True)
class Achievement:
    id: str                       # stable slug; the DB key. NEVER rename once shipped.
    name: str                     # display title
    description: str              # player-facing unlock condition
    category: str                 # "milestone" | "skill" | "tournament" | "staking"
    icon: str                     # lucide-react icon name (frontend renders by name)
    trigger: AchievementTrigger
    target: int                   # static unlock threshold (1 == boolean). Ignored when
                                  # dynamic_target is set.
    # Pure fn: given the trigger's facts + the player's CURRENT stored progress,
    # return the NEW progress value (absolute). Engine unlocks when result >= effective
    # target. Booleans return `target` when met else `current`. Counters return current+1.
    # Streaks return current+1 or 0. Cumulatives return current + delta. Collections return
    # the live achieved count.
    progress: Callable[[Facts, int], int]
    # Optional: compute the unlock threshold from the facts at eval time, for "beat the
    # whole field" achievements whose denominator grows (apex_predator, socialite). Returns
    # 0 when the field is empty so an empty world never trivially unlocks. When None, the
    # static `target` is used.
    dynamic_target: Callable[[Facts], int] | None = None
    secret: bool = False          # optional: hidden in UI until unlocked

ACHIEVEMENTS: list[Achievement] = [
    Achievement(
        id="first_blood", name="First Blood",
        description="Win your first hand",
        category="milestone", icon="Swords",
        trigger=AchievementTrigger.HAND, target=1,
        progress=lambda f, cur: cur + 1 if f.won else cur,
    ),
    Achievement(
        id="heater", name="Heater",
        description="Win 3 hands in a row",
        category="milestone", icon="Flame",
        trigger=AchievementTrigger.HAND, target=3,
        progress=lambda f, cur: (cur + 1) if f.won else 0,   # reset on a played loss
    ),
    Achievement(
        id="royal_flush", name="Royal Flush",
        description="Win a hand holding a royal flush",
        category="skill", icon="Crown",
        trigger=AchievementTrigger.HAND, target=1,
        progress=lambda f, cur: 1 if (f.won and f.winning_hand_rank == 1) else cur,
    ),
    Achievement(
        id="loan_shark", name="Loan Shark",
        description="Earn $10,000 lifetime profit from staking",
        category="staking", icon="HandCoins",
        trigger=AchievementTrigger.STAKE_SETTLE, target=10000,
        progress=lambda f, cur: cur + max(0, f.net_for_player) if f.role == "staker" else cur,
    ),
    Achievement(
        id="socialite", name="Socialite",
        description="Meet every AI in your world",
        category="social", icon="Users",
        trigger=AchievementTrigger.CASH_STANDING, target=1,   # target ignored (dynamic)
        progress=lambda f, cur: f.met_count,                  # store the live count
        dynamic_target=lambda f: f.eligible_count,            # unlock at met == roster
    ),
    Achievement(
        id="apex_predator", name="Apex Predator",
        description="Go net-positive against every AI in your world",
        category="social", icon="Crosshair",
        trigger=AchievementTrigger.CASH_STANDING, target=1,
        # progress = how many roster AIs you're currently net-up on; unlock when that
        # equals the FULL eligible roster (so you've met AND beaten everyone).
        progress=lambda f, cur: f.positive_pairs_count,
        dynamic_target=lambda f: f.eligible_count,
    ),
    # ... remaining entries, same shape
]

# Indexed for the engine; built once at import.
BY_TRIGGER: dict[AchievementTrigger, list[Achievement]] = _group_by_trigger(ACHIEVEMENTS)
BY_ID: dict[str, Achievement] = {a.id: a for a in ACHIEVEMENTS}
```

### Adding a new achievement later (the extensibility payoff)

1. **Existing trigger + existing facts:** append one `Achievement(...)` to `ACHIEVEMENTS`.
   That's the whole backend change. No schema migration (rows are created lazily by
   `owner_id, achievement_id`). The `/api/achievements` catalog and both UIs are
   server-driven, so they pick it up automatically.
2. **Needs a new fact:** add a field to the relevant `*Facts` dataclass and populate it at
   the trigger site (one line in `achievement_service` builder).
3. **Needs a brand-new trigger surface:** add an `AchievementTrigger` value, a builder +
   `evaluate_*` entry point in `achievement_service`, and one call site. Engine/repo
   untouched.
4. **Frontend:** if the icon name is new to lucide, it just works; otherwise nothing.

## 5. Facts (inputs the engine sees)

```python
# poker/achievements/facts.py
@dataclass(frozen=True)
class HandFacts:
    owner_id: str
    player_name: str               # human's display name this game
    is_cash: bool
    stake_label: str | None        # '$2'..'$1000' in cash; None in tournament
    stake_rank: int                # index into STAKES_ORDER (-1 if n/a)
    won: bool                      # human is among the hand's winners
    is_showdown: bool
    pot_won: int                   # chips the human netted this hand (0 if lost)
    winning_hand_rank: int | None  # HandEvaluator rank if human won at showdown (1 = royal)
    bankroll: int | None           # current persistent bankroll (cash only)
    opponents_busted: int          # # of opponents who hit 0 chips in a pot the human won
    hero_call: bool                # human was actor of a HERO_CALL DetectedEvent this hand
    bluff_win: bool                # non-showdown win where human was last aggressor
    tournament_won: bool           # human won the tournament (final hand)

@dataclass(frozen=True)
class StakeOfferFacts:
    owner_id: str
    accepted: bool
    target_pid: str

@dataclass(frozen=True)
class StakeSettleFacts:
    owner_id: str
    role: str                      # 'staker' | 'borrower'
    net_for_player: int            # signed chips from this stake
    carry_created: bool            # borrower busted under principal -> player is now owed

@dataclass(frozen=True)
class CashStandingFacts:
    """Sandbox-wide standing — built right after each cash hand (§7d) from a few aggregate
    reads (AI bankrolls, cash_pair_stats, relationship axes). The denominator for the
    collection achievements is the FULL eligible roster, not the met-set."""
    owner_id: str
    sandbox_id: str
    player_net_worth: int          # bankroll + receivables - payables
    max_ai_net_worth: int          # deepest AI net worth in the sandbox (0 if none)
    eligible_count: int            # full eligible cash roster size — the collection denominator
    met_count: int                 # distinct roster AIs the player has met (chips flowed)
    positive_pairs_count: int      # roster AIs the player is net-up on (cumulative_pnl > 0)
    max_likability_toward_player: float  # highest AI->player likability axis in the sandbox

Facts = HandFacts | StakeOfferFacts | StakeSettleFacts | CashStandingFacts
```

All `HandFacts` fields are cheaply derivable at the existing hook (see §7); the service
layer builds them, keeping the engine pure and trivially unit-testable.

## 6. Engine + repository

### Engine

```python
# poker/achievements/engine.py
@dataclass(frozen=True)
class UnlockedAchievement:
    achievement: Achievement
    unlocked_at: datetime

class AchievementEngine:
    def __init__(self, repo: AchievementRepository):
        self._repo = repo

    def evaluate(self, trigger: AchievementTrigger, facts: Facts) -> list[UnlockedAchievement]:
        owner_id = facts.owner_id
        defs = BY_TRIGGER.get(trigger, [])
        if not defs:
            return []
        state = self._repo.load_owner_state(owner_id)   # {achievement_id: (progress, unlocked_at)}
        unlocked: list[UnlockedAchievement] = []
        for a in defs:
            cur_progress, already = state.get(a.id, (0, None))
            if already is not None:
                continue                                  # idempotent: never re-unlock
            new_progress = a.progress(facts, cur_progress)
            target = a.dynamic_target(facts) if a.dynamic_target else a.target
            now = datetime.utcnow()
            if target > 0 and new_progress >= target:
                self._repo.upsert(owner_id, a.id, new_progress, unlocked_at=now)
                unlocked.append(UnlockedAchievement(a, now))
            elif new_progress != cur_progress:
                self._repo.upsert(owner_id, a.id, new_progress, unlocked_at=None)
        return unlocked
```

Notes:
- **Forward-only** falls out naturally: missing rows default to `(0, None)`; nothing seeds
  from history.
- **Idempotent / dedup:** an `unlocked_at`-set row is skipped forever. Re-running the same
  hand can't double-fire.
- **Failure isolation:** the service wraps `evaluate` in try/except so an achievement bug
  can never break hand resolution or stake settlement (mirrors how `on_hand_complete` and
  `dispatch_events` are guarded today).

### Repository + schema (v119)

```sql
CREATE TABLE IF NOT EXISTS player_achievements (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id      TEXT NOT NULL,
    achievement_id TEXT NOT NULL,
    progress      INTEGER NOT NULL DEFAULT 0,
    unlocked_at   TIMESTAMP,                       -- NULL = locked / in progress
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_id, achievement_id)
);
CREATE INDEX IF NOT EXISTS idx_player_achievements_owner ON player_achievements(owner_id);
```

A single `progress INTEGER` covers every v21 shape (boolean 0/1, counter, streak,
cumulative chips, live collection count) — no `progress_json` needed. `owner_id` is the durable identity
(works for guests + Google, survives `transfer_guest_to_user`).

`AchievementRepository` methods: `load_owner_state(owner_id)`, `upsert(owner_id,
achievement_id, progress, unlocked_at)`, `list_for_owner(owner_id)` (for the API).

**Migration recipe** (`poker/repositories/schema_manager.py`):
1. `SCHEMA_VERSION = 119` (currently 118).
2. Add `# v119: player_achievements ...` to the changelog block.
3. Add `119: (self._migrate_v119_add_achievements, "Add player_achievements table")` to
   the `migrations` dict (after the 118 entry, ~line 1880).
4. Add `_migrate_v119_add_achievements(self, conn)` — the `CREATE TABLE` + index above
   (no `conn.commit()`; the runner commits).
5. Mirror the `CREATE TABLE IF NOT EXISTS` into `_init_db()` so fresh DBs get it; bump the
   table-count note in the `_init_db` docstring.

## 7. Trigger surfaces (exact wiring)

### 7a. HAND — `flask_app/handlers/game_handler.py::handle_evaluating_hand_phase`

Insertion point: right after `memory_manager.on_hand_complete(...)` (~line 2727), where
`winner_info`, `winning_player_names`, `is_showdown`, `net_profit`, `game_state`,
`game_data`, and `tournament_outcome` are all in scope.

Build `HandFacts`:
- `owner_id = game_data.get('owner_id')` — skip if falsy (no identity to credit).
- `human = next(p for p in game_state.players if p.is_human)`; `player_name = human.name`.
- `won = player_name in winning_player_names`.
- `pot_won = net_profit if won else 0`.
- `winning_hand_rank`: `winner_info.get('hand_rank')` when `won and is_showdown`.
- `is_cash = bool(game_data.get('cash_mode'))`; `stake_label` from the cash table /
  `game_data`; `stake_rank` via `cash_mode.stakes_ladder.STAKES_ORDER`.
- `bankroll`: cash only — `bankroll_repo.load_player_bankroll(owner_id)` (the cash payload
  already loads this nearby; reuse).
- `opponents_busted`: count `p` where `not p.is_human and p.stack == 0` after award **and**
  `won` (the human took the pot). Cross-check against the existing `players_with_chips`
  logic already computed for tournament-final detection.
- `hero_call`: inspect the `DetectedEvent`s from the relationship detector for a
  `HERO_CALL` whose actor resolves to the human. The detector runs inside
  `on_hand_complete`; expose the last batch (e.g. `memory_manager.last_detected_events`)
  or re-run `detect_events` read-only. **Open item — see §11.**
- `bluff_win`: `won and not is_showdown` and the human's last aggressive action
  (bet/raise/all_in) was the final aggressive action of the hand. Derive from
  `memory_manager.hand_recorder` actions (the `RecordedHand` is available post-complete).
- `tournament_won`: `tournament_outcome and tournament_outcome['human_won']`.

Then: `achievement_service.evaluate_hand(facts)`.

### 7b. STAKE_OFFER — `flask_app/routes/cash_routes.py::offer_stake_to_ai` (~line 3101)

After the AI accepts (the `"accepted": True` branch, ~line 3663), build
`StakeOfferFacts(owner_id, accepted=True, target_pid=...)` and call
`achievement_service.evaluate_stake_offer(facts)`.

### 7c. STAKE_SETTLE — stake settlement paths in `cash_routes.py`

Where a stake the **human staked** settles with a captured payout
(`staker_payout` / `net_for_player`), call
`achievement_service.evaluate_stake_settle(StakeSettleFacts(owner_id, role='staker',
net_for_player=..., carry_created=...))`. `carry_created` is true when the borrower busted
under principal and the stake flipped to `'carry'` (the player is now owed money → feeds
`creditor`). Settlement happens in multiple places (leave-time `settle_stake_on_leave`,
`payoff_stake`, `staker_forgive`); the cleanest single chokepoint is the function that
writes the final `staker_payout` — wire there to avoid double-counting. **Open item — see §11.**

### 7d. CASH_STANDING — right after each cash hand

The aggregate "beat-the-field" achievements (`richest_in_room`, `apex_predator`,
`socialite`, `fan_favorite`) need sandbox-wide state — every AI's net worth, the player's
per-opponent PnL, met count vs the full roster, and the max AI→player likability. These are
evaluated **immediately after each cash hand** so they unlock the moment the player earns
them (no background-timer lag).

Insertion point: the same cash-mode branch of `handle_evaluating_hand_phase` that builds
`HandFacts` (§7a). When `is_cash`, also build one `CashStandingFacts` for the owner and call
`achievement_service.evaluate_cash_standing(facts)`. The facts come from a few cheap
aggregate reads scoped to the sandbox:
- `max_ai_net_worth` / `player_net_worth`: AI bankrolls (`ai_bankroll_state`) + the player's
  bankroll and open stake positions (the net-worth path already assembles this).
- `eligible_count`: `personality_repo.list_eligible_for_cash_mode(user_id=owner_id)` size.
- `met_count` / `positive_pairs_count`: a scan of `cash_pair_stats` for the owner in the
  sandbox (met = a row with chips flowed; positive = `cumulative_pnl > 0`).
- `max_likability_toward_player`: the max AI→player likability axis in the relationship graph.

The per-hand cost is a handful of indexed reads — negligible against the seconds an LLM hand
already takes. (If profiling ever flags it, these can move to the periodic
`holdings_snapshots` recorder pass as a batched backstop; the engine is idempotent so it's a
safe relocation.)

## 8. Notification delivery

The user chose **trophy card + lobby ticker line**.

### Socket event (reaches game + lobby)

`achievement_service` emits, for each newly-unlocked achievement:

```python
socketio.emit("achievement_unlocked", {
    "id": a.id, "name": a.name, "description": a.description,
    "icon": a.icon, "category": a.category,
    "unlocked_at": now.isoformat(),
}, to=presence.lobby_room_name(owner_id))   # "lobby:{owner_id}"
```

Every socket (lobby **and** in-game) is already joined to `lobby:{owner_id}` on connect, so
this reaches the player wherever they are — no new room wiring. Works in tournament too.

### Ticker line

Add a `LobbyEvent` type to `cash_mode/activity.py`:
```python
EVENT_ACHIEVEMENT = "achievement"   # "You unlocked Royal Flush"
```
Record it via `activity.record_event(...)` with the player's `sandbox_id` so it appears in
the lobby `ActivityTicker` and the interhand world ticker.
**Caveat:** the ticker is cash-sandbox-scoped. Tournament unlocks have no cash sandbox, so
the **ticker line is cash-only**; the trophy card + socket fire in both modes. Documented,
acceptable for v1.

### Frontend trophy card

`react/react/src/components/.../AchievementUnlockToast.tsx` — modeled on `ArrivalWelcome`:
- `createPortal` to `document.body` (escapes the `PageLayout` stacking trap).
- Trophy icon + name + description; fades in/out (~3s); tap to dismiss early.
- **Queue** multiple unlocks (a hand can unlock several at once) so they show in sequence.

## 9. Frontend plan

| File | Change |
|---|---|
| `react/react/src/types/achievements.ts` | `AchievementDef`, `AchievementState` (progress/unlocked_at), `AchievementsResponse`, `AchievementUnlockedEvent` |
| `react/react/src/components/cash/api.ts` *(or a new `src/api/achievements.ts`)* | `getAchievements(): Promise<AchievementsResponse>` → `GET /api/achievements` |
| `react/react/src/hooks/usePokerGame.ts` | `socket.on('achievement_unlocked', e => pushAchievementUnlock(e))` alongside the existing `world_event` handler (~line 575) |
| `react/react/src/stores/gameStore.ts` | `achievementUnlocks: AchievementUnlockedEvent[]` queue + `pushAchievementUnlock` / `shiftAchievementUnlock` actions |
| `react/react/src/components/achievements/AchievementUnlockToast.tsx` | portal trophy card (mounted once in the game layout + lobby) |
| `react/react/src/components/achievements/AchievementsDrawer.tsx` | `BottomSheet`-based grid (locked/unlocked + progress bars); opened from a Lobby button with an unlocked-count badge near `CareerHero` |
| `react/react/src/components/cash/Lobby.tsx` | mount the drawer trigger + listen for `achievement_unlocked` to refresh the badge |
| `react/react/src/components/stats/CareerStats/CareerStats.tsx` | reuse the same grid component (mode-agnostic home; the page already has an `achievements-row` style) |
| `react/react/src/components/cash/tickerEvents.tsx` + `cash/types.ts` | add `'achievement'` to the `LobbyEvent` type union + a Trophy icon mapping |

The catalog is **server-driven** (`/api/achievements` returns every definition + this
owner's state), so the frontend never hardcodes the list — adding achievements needs no FE
change beyond a possible new icon.

### Backend route

`flask_app/routes/achievement_routes.py` — `GET /api/achievements` (top-level, not under
`/api/cash`, since it spans both modes). Returns the full catalog merged with the
authenticated owner's progress/unlock rows:
```json
{ "achievements": [
  { "id": "first_blood", "name": "First Blood", "description": "...", "category": "milestone",
    "icon": "Swords", "target": 1, "progress": 1, "unlocked": true, "unlocked_at": "..." },
  ... ],
  "unlocked_count": 3, "total": 21 }
```

## 10. Build sequence (milestones)

- **M1 — Backend core.** Package (`definitions`/`facts`/`engine`), `AchievementRepository`,
  schema v119. Unit tests: each achievement unlocks on its condition; dedup; streak reset;
  cumulative; forward-only. *(No wiring yet — fully testable in isolation.)*
- **M2 — Service + wiring.** `achievement_service` (evaluate→persist→emit socket+ticker);
  the HAND call site; `achievement_unlocked` socket; `EVENT_ACHIEVEMENT` ticker type.
- **M3 — Staking + standing surfaces.** STAKE_OFFER + STAKE_SETTLE call sites (resolve the
  settlement chokepoint, §11) for `backer` / `loan_shark` / `creditor`; the CASH_STANDING
  call site in the holdings-snapshot recorder (§7d) for `richest_in_room` / `apex_predator`
  / `socialite` / `fan_favorite`.
- **M4 — Read API + FE plumbing.** `/api/achievements` route; types; `getAchievements`;
  store slice; socket listener; `AchievementUnlockToast`.
- **M5 — Browse UIs.** `AchievementsDrawer` in the Lobby (+ badge); CareerStats grid;
  ticker icon.
- **M6 — Polish + tests.** FE `tsc`; backend full suite; manual unlock walkthrough.

## 11. Open items / decisions to confirm before/while building

1. **`hero_call` / `bluff_win` plumbing.** Cleanest is to have `on_hand_complete` stash the
   `DetectedEvent` batch (e.g. `memory_manager.last_detected_events`) and the
   `RecordedHand`, which the service reads. Alternative: the service re-runs read-only
   detection. Decide in M2.
2. **Stake-settlement chokepoint.** Settlement fires from `settle_stake_on_leave`,
   `payoff_stake`, and `staker_forgive`. Need one place (or a guarded helper) so
   `loan_shark` profit isn't double-counted. Identify the single `staker_payout` writer in M3.
3. **Tunable thresholds.** 100 hands / 100 wins / 3-streak / 200 $2-hands / $10k staking /
   5,000 pot / 100k bankroll / $50 stake. Confirm or adjust (they're just `target` values).
   `fan_favorite` ceiling is **1.0** (decided — the likability axis maximum).
4. **DECIDED — collection scope = full roster.** `socialite` (met all) and `apex_predator`
   (net-positive vs all) are gated on the **entire eligible cash roster**
   (`list_eligible_for_cash_mode` size), not the met-set, so they can't fire on a first
   session. Remaining sub-decision: whether `apex_predator` should require a per-pair
   "≥N hands of history" floor so a single fluke win against an AI doesn't count. Default:
   **no floor** in v1 (full-roster bar is already steep); revisit if it feels cheap.
5. **DECIDED — standing achievements evaluate right after each cash hand (§7d)**, so they
   unlock promptly. No background-timer lag. (Optional future relocation to the
   `holdings_snapshots` recorder if per-hand cost ever matters — idempotent, safe.)
6. **Future additions already gestured at:** these slot into the same registry —
   true weak-hand bluff (tighten `bluff_win`), "play N hands at each stake", relationship
   milestones (befriend an AI), vice/side-hustle beats, prestige thresholds.
7. **Secret achievements / tiers / points** — `secret` flag is in the dataclass but unused
   in v1; bronze/silver/gold tiers and an achievement-points score are deferred.
8. **Sound / haptics** on unlock — deferred.

## 12. Testing

- **Engine unit tests** (`tests/test_achievements_engine.py`): one test per achievement
  asserting unlock on its trigger facts; dedup (second identical eval = no unlock); streak
  reset; cumulative accrual across calls; forward-only (no unlock without qualifying facts).
- **Repository tests**: module-scoped schema fixture (per the project's Docker test
  pattern); upsert/load round-trip; unique constraint.
- **Wiring smoke**: a hand that should unlock `first_blood` produces a persisted row + a
  socket emit (mock socketio).
- Frontend: `tsc --noEmit`; a render test for the drawer grid (locked vs unlocked).

All API calls that could fail (LLM, none here) are N/A; the only external surface is the DB
and socket emit, both guarded so a failure never breaks the hand/stake path.

## 13. Relationship to player prestige / renown

The player-prestige system (`CASH_MODE_PLAYER_PRESTIGE.md`, "Renown v2") is
**uncapping `renown` into a continuous, lifetime points ledger fed by the same
fact surfaces this engine defines** — `HAND` (busts, pots, rare hands),
`STAKE_SETTLE` (backing), `CASH_STANDING` (net-worth rank, met/beaten counts).
Achievements are the **discrete milestone** view; renown is the **continuous
score**. Several v21 achievements *are* renown sources — `richest_in_room`,
`socialite`, `apex_predator`, `backer`, `loan_shark`, `creditor`, `bounty`,
`double_knockout`, `royal_flush`, `monster_pot`, `hero`.

**Share the pipeline, don't duplicate.** Recommended bridge (hybrid): renown
accrues continuously over these facts (every hand moves the needle) **and**
achievement unlocks mint one-off renown nuggets for *legendary* moments — smooth
needle plus punctuated spikes.

Two cross-cutting notes that live in the prestige doc but constrain this engine:

- **AI-symmetry.** This engine is `owner_id`-keyed (human-only). Renown must also
  compute for **AIs** (the occupant-prestige layer), so AI renown is computed
  directly from the symmetric fact sources, *not* via this engine — the
  achievement→renown bridge is a human-side bonus only.
- **Shared dependency — the scalp/bust counter.** `bounty`/`double_knockout` use
  a per-hand `opponents_busted`; renown-weighted scalps want a **durable,
  attributed "who busted whom"** counter (the world runs the full sim, so real
  busts already occur — they're just not persisted with eliminator attribution
  in the cash path, and not at all for AI-vs-AI). Wiring it once serves both
  systems. See `CASH_MODE_PLAYER_PRESTIGE.md` § "Known telemetry gaps".
