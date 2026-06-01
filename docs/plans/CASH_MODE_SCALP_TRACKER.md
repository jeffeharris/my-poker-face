---
purpose: Spec for a durable, attributed "who busted whom" (scalp) counter in cash mode — the shared prerequisite for renown-weighted scalps and the bounty/double_knockout achievements, for the human and AIs alike.
type: spec
created: 2026-05-29
last_updated: 2026-06-01
---

> **Status (2026-06-01):** steps **1, 2, and 3a DONE**; only **3b** remains.
> - **Step 2** (pure attribution helper) — `cash_mode/scalps.py`
>   (`eliminations_from_sim` + `eliminations_from_human_hand`), unit-tested,
>   deliberately pure (local `HAND_EVENT_BUST` mirror + drift-guard test).
> - **Step 1** (durable counter) — schema **v132** `cash_scalps` +
>   `CashScalpsRepository` (record / record_many / total_for /
>   list_for_eliminator / victims_of), registered in `create_repos` +
>   `extensions`; 7 repo tests + schema-chain tests green.
> - **Step 3a** (AI-vs-AI world-sim path) — wired in
>   `cash_mode/lobby.py::refresh_unseated_tables`: each sim hand's
>   `eliminations_from_sim` is recorded via a lazily-resolved
>   `cash_scalps_repo` (mirrors the entity-presence getter), best-effort so a
>   write failure never breaks the world tick. 42 lobby tests green.
> - **Step 3b (REMAINING)** — the human's own table. Integration point found:
>   `flask_app/handlers/game_handler.py::_refill_cash_seats` derives
>   `busted_indices` (AIs at stack 0) between hands, but does **not** know the
>   hand's headline winner — attribution needs that winner threaded in from the
>   evaluating-hand/award step (the eliminator is the headline pot winner, per
>   §3). Lower value (one human entity's villain renown vs the constant world
>   tick) and higher risk (live human flow), so deferred as a clean follow-up.
> The counter now accrues from the world sim; the Renown-v2 scalp driver that
> *reads* it lands with the broader v2 build (step 4).

# Cash Scalp Tracker — attributed bust counting

## 1. Goal

A **durable, attributed, sandbox-scoped counter of eliminations** in cash mode:
who busted whom, how many times. "Scalps."

It is the shared prerequisite for two systems:
- **Renown-weighted scalps** (`CASH_MODE_PLAYER_PRESTIGE.md` → Renown v2): a
  renown driver where busting a high-renown opponent is worth far more than
  busting a nobody. Needs the *victim's identity* (to weight by their renown),
  not just a count.
- **`bounty` / `double_knockout` achievements** (`ACHIEVEMENTS_SYSTEM.md`):
  today they read a per-hand `opponents_busted` count with no durable record and
  no victim identity.

Two product constraints carried from the achievements system:
- **AI-symmetric.** Must work when the eliminator is an **AI** (AI-vs-AI in the
  world sim), not just the human — so the occupant-prestige layer can use it.
- **Forward-only.** Counters start at 0; nothing is backfilled from history.

## 2. What exists today (verified 2026-05-29)

- **Tournament** has the attribution shape already:
  `poker/tournament_tracker.py` → `EliminationEvent{eliminated_player,
  eliminator, pot_size, eliminated_at_hand}` and `on_player_eliminated(
  player_name, eliminator, pot_size)`. In-memory, tournament-scoped, **not**
  persisted as a cross-game cumulative.
- **Per-game pressure stats** count it: `poker/pressure_stats.py` increments
  `eliminations` on an `"eliminated_opponent"` event. In-memory, per-game.
- **Achievements** compute a per-hand count: `HandFacts.opponents_busted` (the
  human's busts this hand) drives `bounty`/`double_knockout`. No victim
  identity, no durable counter, human-only.
- **Cash world sim runs the full engine** — `cash_mode/full_sim.play_one_hand`
  (~14 live tables) produces real eliminations:
  - `HAND_EVENT_BUST` is a **single-party** `HandEvent` (`personality_id` = the
    player who hit 0; `opponent_pid` is `None`) — **the bust event alone does
    not name the killer.**
  - `HandSimResult` carries `winner_pid` / `loser_pid` (the headline pair) — this
    is the attribution source (see §3).
- **No durable "who busted whom" table** exists. `SCHEMA_VERSION = 122`; the new
  table is **v123**.

## 3. Attribution — the crux

Poker elimination attribution is non-trivial multiway (side pots, multiple
all-ins). v1 uses the **headline-winner heuristic**, matching what the lobby
ticker already does (`activity.format_hand_summary_message` attributes a bust to
the headline winner):

> **The eliminator of a busted player is the hand's headline pot winner.**

Two capture paths, same rule:

### 3a. AI-vs-AI (world sim) — `cash_mode/full_sim.play_one_hand`
For each `HandEvent` of type `HAND_EVENT_BUST` in `result.hand_events`:
- `victim_id = event.personality_id`
- `eliminator_id = result.winner_pid` (the headline winner of that hand)
- Skip if `eliminator_id is None` or `eliminator_id == victim_id` (a self-bust
  on blinds with no one covering has no eliminator).

Record `(sandbox_id, eliminator_id, victim_id)`. No engine change — purely a
derivation from the existing `HandSimResult`.

### 3b. Human's real hand — `flask_app/handlers/game_handler.py::handle_evaluating_hand_phase`
At the existing post-`on_hand_complete` point where `opponents_busted` is already
derived for achievements (busted = non-human players whose `stack == 0` after the
award, in a pot the human won):
- `eliminator_id = owner_id` (human)
- `victim_id` = each busted AI's `personality_id` (resolve from the seat / game
  state — the names are already in scope for refill).

Record one row per victim. (Symmetric note: an AI busting the *human* in a cash
hand isn't a meaningful "scalp" target for renown today — the human doesn't bust
out, they leave — so v1 only records AI victims at the human's table. Revisit if
human-as-victim is ever wanted.)

### Attribution caveats (documented, accepted for v1)
- **Multiway over-attribution.** In a 3+ way all-in the headline winner gets
  credit for a side-pot bust they didn't cover. Sims are near-heads-up in
  practice (opponents named ~96% of the time per the ticker's own note), so this
  is accurate for the common case and only mildly generous in the rare one.
- **Split pots:** `winner_pid` is the headline winner; a chopped pot rarely busts
  anyone, so this is a non-issue in practice.

## 4. Storage (schema v123)

A sandbox-scoped cumulative, mirroring `cash_pair_stats` conventions (raw ids:
`owner_id` for the human, `personality_id` for AIs; no `player:`/`ai:` prefix).

```sql
CREATE TABLE IF NOT EXISTS cash_scalps (
    sandbox_id     TEXT NOT NULL,
    eliminator_id  TEXT NOT NULL,   -- owner_id (human) or personality_id (AI)
    victim_id      TEXT NOT NULL,
    count          INTEGER NOT NULL DEFAULT 0,
    last_at        TIMESTAMP,
    PRIMARY KEY (sandbox_id, eliminator_id, victim_id)
);
CREATE INDEX IF NOT EXISTS idx_cash_scalps_eliminator
    ON cash_scalps(sandbox_id, eliminator_id);
```

Per-`(eliminator, victim)` granularity is deliberate: **renown-weighting needs
the victim identity** (join `victim_id` → that entity's current renown at compute
time). A flat per-eliminator count would lose that.

**Migration recipe** (`poker/repositories/schema_manager.py`): bump
`SCHEMA_VERSION = 123`; add the changelog comment; add
`_migrate_v123_create_cash_scalps`; register in the `migrations` dict; mirror the
`CREATE TABLE IF NOT EXISTS` into `_init_db` and bump its table-count note. (Same
shape as the v121/v122 migrations — see `prestige_snapshots` /
`coach_session_evaluations` for the exact idiom. Note the prestige branch's
v121→v122 renumber history; build on **123**.)

**Repository** — `poker/repositories/cash_scalps_repository.py`
(`CashScalpsRepository(BaseRepository)`):
- `record(sandbox_id, eliminator_id, victim_id, *, now)` — upsert `count += 1`,
  `last_at = now` (single INSERT … ON CONFLICT … DO UPDATE).
- `total_for(sandbox_id, eliminator_id) -> int` — scalp count for one entity.
- `list_for_eliminator(sandbox_id, eliminator_id) -> list[(victim_id, count)]` —
  the per-victim breakdown renown-weighting consumes.
- (optional) `victims_of(sandbox_id, victim_id)` — "who's hunting me."

Register in `repositories/__init__.py` `create_repos()` + `flask_app/extensions.py`
(same pattern as `prestige_snapshots_repo`). Keep the recorder **best-effort /
guarded** so a scalp-write failure never breaks hand resolution (mirrors how
`dispatch_events` / achievements are wrapped).

## 5. A single attribution helper (shared by both paths)

Put the rule in one pure, testable place — e.g.
`cash_mode/scalps.py::eliminations_from_sim(result) -> list[(eliminator, victim)]`
for 3a, and a thin inline derivation for 3b (the human path already has the
busted list). Both call `cash_scalps_repo.record(...)`. Keeping the rule in one
module means the multiway heuristic is changed in exactly one spot later.

## 6. Integration

### Renown (the payoff)
In `cash_mode/prestige.py::compute_prestige`, add a renown driver:
`scalps = Σ_victim list_for_eliminator(...)[victim] × f(renown(victim))`, where
`f` weights by the victim's renown (a legend's scalp ≫ a nobody's). This needs
each victim's renown at compute time — read from the same prestige read used for
the field, or a cached field-renown map. **Continuous + uncapped** per Renown v2
(each bust adds; weighting makes quality matter). AI-symmetric: the eliminator
can be an AI, so AI renown gets scalps too.

### Achievements (cheap win alongside)
- `bounty` / `double_knockout` can keep using the per-hand count, but the new
  durable record enables better ones: e.g. **"bust a Beloved Legend"**, **"100
  scalps"**, **"bust every AI at least once"** — one-line registry adds
  (`CASH_STANDING`/`HAND` triggers) once the counter exists.

## 7. Symmetry & sim fidelity

- **AI eliminators are first-class** — 3a records them. So AI renown can use
  scalps the moment this lands; no human-only assumption.
- **Full sim only.** The world tick runs `full_sim` (real busts). If a `fake_sim`
  path ever runs (flavor chip-moves, no real elimination), it must **not** emit
  scalps — gate recording on the full-sim path. (Today the world uses full_sim,
  so this is a guard, not a live concern.)

## 8. Gotchas / decisions to confirm

- **Headline-winner attribution** is the v1 rule (§3). Confirm it's acceptable
  vs. a more precise "who covered the all-in" attribution (more engine work).
- **Human-as-victim** is intentionally out of scope (the human leaves, doesn't
  bust). Confirm.
- **Forward-only** (no backfill) — consistent with achievements.
- **Sandbox scoping** — scalps reset per sandbox (a fresh villain run starts at
  0 scalps), consistent with the prestige stat.
- **Self-bust / no-eliminator** hands are skipped (no credit).
- **Renown weighting function `f`** (linear in victim renown? curved? floor for
  unknown-renown victims?) — a tuning decision, not a structural one.

## 9. Build sequence

1. **Schema v123 + `CashScalpsRepository`** (record / total_for /
   list_for_eliminator). Unit tests: upsert increments; per-victim breakdown;
   sandbox isolation.
2. **`cash_mode/scalps.py` attribution helper** + unit tests (sim result →
   (eliminator, victim) pairs; self-bust skipped; None winner skipped; multiway
   headline attribution).
3. **Wire the AI-vs-AI path** (3a) in the lobby's sim-result handling +
   **the human path** (3b) in `handle_evaluating_hand_phase`. Both best-effort.
4. **Renown driver** in `compute_prestige` (renown-weighted scalps) — gated
   behind the Renown v2 work; until then the counter just accrues.
5. **(optional) New achievements** off the durable record.

Steps 1–3 are self-contained and shippable on their own (the counter starts
accruing for human + AI); 4 lands with the broader Renown v2 pass.

## 10. Testing

- Repo: upsert/increment, per-victim breakdown, sandbox isolation, forward-only.
- Attribution helper (pure): sim-result fixtures → expected (eliminator, victim)
  pairs; self-bust, None-winner, multiway headline cases.
- Wiring smoke: a sim hand with a BUST records a row with the right
  (eliminator, victim); a human hand busting an AI records `owner_id → pid`.
- Guard: a recorder exception doesn't break hand resolution (mock a failing repo).

## Related
- `CASH_MODE_PLAYER_PRESTIGE.md` → Renown v2 ("renown-weighted scalps", "Known
  telemetry gaps").
- `ACHIEVEMENTS_SYSTEM.md` → §13 (shared dependency), `bounty`/`double_knockout`.
- `poker/tournament_tracker.py` (`EliminationEvent`) — the attribution shape to
  mirror.
- `cash_mode/full_sim.py` (`HandEvent` / `HAND_EVENT_BUST` / `HandSimResult`) —
  the AI-side capture source.
