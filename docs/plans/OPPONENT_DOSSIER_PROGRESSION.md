---
purpose: Turn the opponent dossier into a persistent, gated scouting meta-game — lifetime intel you accumulate, unlock, and browse
type: vision
created: 2026-05-25
last_updated: 2026-05-29
---

# Opponent Dossier & Scouting Progression

## The idea

The dossier stops being a free, ephemeral readout and becomes a
**scouting meta-game**: intel you accumulate on an opponent over every
game you've played them, that **persists**, is **gated behind play**, and
is **unlockable** through progression (grind, pay, or perks). Reading
opponents becomes a skill you build. A **file cabinet** lets you browse
everyone you've ever met and pull their dossier.

This builds directly on shipped foundations — it's mostly about (1) making
the behavioral reads durable and cross-game, then (2) layering gating and
unlocks on top.

## What exists today (grounding)

Dossier endpoint: `GET /api/character/<identifier>/dossier`
(`flask_app/routes/character_routes.py:347`), rendered by
`react/react/src/components/character/CharacterDetailCard.tsx`. The endpoint
assembles its response at `character_routes.py:476-488` and adds
observer-scoped sections at `:495-530`.

**Already persists cross-game + survives restart:**
- `cash_pair_stats` — `cumulative_pnl`, `hands_played_cash` per
  `(sandbox_id, observer_id, opponent_id)` (PK is sandbox-scoped since
  v109; schema at `schema_manager.py:638-645`). The **dossier reads it
  cross-sandbox** via `load_cash_pair_stats(..., sandbox_id=None)`
  (`character_routes.py:516`), which SUMs every sandbox.
  NB: `hands_played_cash` increments once per *chip-flow* between the pair
  (`hand_delta=1` per `apply_cash_pair_pnl` call), so it's a literal hand
  count heads-up but can over-count in multiway side-pot hands; pairs where
  no chips ever flowed get no row at all.
- `relationship_states` — heat / respect / likability (decay-projected),
  `last_seen`, player-authored `note`, `nickname_override`. PK is
  `(observer_id, opponent_id)` (`schema_manager.py:616-628`) — **fully
  global**, no sandbox scoping.
- `ai_bankroll` — off-table chips, sandbox-scoped (`bankroll_repo`).
- `stake_summary` — outstanding carries as borrower / staker (`stake_repo`).

**Sourced from an active in-memory game** (read from `game_data` via
`_find_game_data_with_player`; `character_routes.py:479-486`). The
underlying tables (`opponent_models`, `pressure_events`, `memorable_hands`)
*are* persisted **per-game** and cold-load back into memory, so they survive
a backend restart **as long as that game is still live**. They are lost from
the dossier when the game ends / is evicted from `game_state_service.games`,
and — critically — **they never accumulate across games**:
- `observation` — VPIP, PFR, aggression factor, hands observed
  (`OpponentModel.tendencies`).
- `pressure_summary` — signature move, biggest pot won/lost, successful
  bluffs, bluffs caught, bad beats, heads-up record
  (`PlayerPressureStats.get_summary()` inside `PressureStatsTracker`;
  `poker/pressure_stats.py`). NB: there is no class literally named
  `PressureStats`.
- `memorable_hands` — top hands by impact (`OpponentModel.memorable_hands`).
  NB: `hand_summary` is **not** persisted (`game_repository.py:961`), so it
  returns empty after a cold-load.
- `emotion` — live psychology display state. **Stays live-only by design**
  (it's a present-moment state, not a stat to accumulate).

So the durable PnL/relationship spine already exists. **The gap is the
behavioral reads not accumulating across games** — and those are the most
valuable scouting signal. "Persist behavioral observation" (Phase 1) means
*cross-game lifetime aggregation*, not merely "survive a restart" (the
per-game rows already do that).

### Where observations are stored today

The behavioral reads live in the **`opponent_models`** table
(`schema_manager.py:546-565`), written on every player action:

```
opponent_models(game_id, observer_name, opponent_name, observer_id,
  opponent_id, hands_observed, vpip, pfr, aggression_factor, fold_to_cbet,
  bluff_frequency, showdown_win_rate, recent_trend, tendencies_json,
  last_updated, UNIQUE(game_id, observer_name, opponent_name))
```

Key facts:
- **Keyed per-game** via `UNIQUE(game_id, observer_name, opponent_name)` —
  each game gets its *own* row; a new game vs. the same opponent starts a
  *fresh* row from defaults. **No `sandbox_id`.**
- It survives a backend restart (live-durable), but **nothing reads these
  rows as a lifetime aggregate** — the dossier only surfaces the row for the
  currently-active in-memory game. Stale per-game rows linger in the table
  unread once their game is gone.
- **Backfill opportunity:** because the per-game rows already sit there with
  `last_updated`, Phase 1's lifetime roll-up can backfill from existing
  `opponent_models` history — but it must map each `game_id` → its
  `sandbox_id` (not on the row) at backfill time.

### UI sections today (not the doc's old labels)

The earlier draft referred to "Basic read / Pattern / Pressure / Exploit"
groupings — **those never existed in the UI.** The real
`CharacterDetailCard` sections, in render order, are:

| Section | Source | Gateable? |
|---|---|---|
| Header / portrait / emotion | name + live emotion | No (always shown) |
| **PROFILE** | attitude, confidence | No (free identity) |
| **BEHAVIORAL INDEX** | curated anchors (aggression, looseness, poise, expressiveness, risk) | **Yes — earnable** |
| **STANDING** | relationship heat/respect/likability | No (your own history) |
| **TRACK RECORD** | cash_pair_stats, pressure summary, memorable hands | **Yes — earnable** |
| **FIELD NOTES** | player-authored note | No (your own writing) |
| **TABLE POSTURE** | chips, observation (VPIP/PFR/AF), ai_bankroll, stakes | **Yes — earnable** |
| **AFFILIATIONS** | sponsor / relationship (lobby prop) | No |
| **OBSERVED REMARK** | live remark | No |

The gating scheme (below) maps onto the three **earnable** sections;
identity, your-own-history, and your-own-notes are always free.

## Design principle: each sandbox is independent (except the personality)

**Decision (Jeff):** a sandbox is a self-contained world/save. The only
thing shared across sandboxes is the **personality** itself (its anchors,
archetype, base config). Everything *relational* — what you've won/lost
off someone, your history and standing with them, the intel you've
gathered — belongs to the sandbox you built it in. You shouldn't see PnL
or a rivalry bleed from one save into another.

All new observation/intel stats key on
`(sandbox_id, observer_id, opponent_id)`.

### What feeds a sandbox's intel (mode scoping — DECIDED)

The accrual gate is **"the game is bound to a sandbox"**, *not* "the game is
cash mode." Concretely:

- **Circuit cash + Circuit tournaments share the sandbox** and carry intel
  back and forth **seamlessly** — same `(sandbox_id, observer, opponent)`
  lifetime row. Playing someone in a Circuit tournament builds the same
  dossier as playing them in a Circuit cash game.
- **Standalone tournaments (outside the Circuit) do NOT bleed in.** They stay
  sandbox-less (`sandbox_id=None`), so their per-game observations are
  captured exactly as today but never roll into a Circuit dossier or the
  cabinet. Keep them separated.
- **Capture vs. accrual:** observations are *recorded* in every mode today
  (`save_opponent_models` is gated only on a `memory_manager` existing,
  `game_routes.py:1924`, `:2414` — not on cash mode). The new behavior is
  purely about which of those *accrue* to lifetime intel: only sandbox-bound
  games do.

**Implementation consequence:** the intel-write path gates on a present
`sandbox_id` (today wired only for cash via `set_relationship_repo(
cash_mode=..., sandbox_id=...)`, `game_routes.py:786`). **Circuit tournaments
must stamp the active sandbox** the same way so they feed the shared row;
this wiring is the prerequisite for the "carry seamlessly" behavior and may
not exist yet.

### Tournament-specific stats (separate track)

Tournament play has metrics that don't belong in the cash reads (e.g.
finishes / ITM vs. an opponent, bustouts inflicted/suffered, final-table
meetings, heads-up-for-the-win record). These get their **own sibling
track** keyed the same `(sandbox_id, observer, opponent)`, added **as needed**
— not part of the v1 cash-read aggregation, but designed so a Circuit
tournament can write to them without disturbing the shared behavioral row.

**Two existing cross-sandbox leaks — DECIDED: fix both now.**

1. **`cash_pair_stats` dossier read pools cross-sandbox.** Storage is
   already per-sandbox; only the dossier's `sandbox_id=None` read sums
   across saves. *Cheap fix:* resolve the observer's `sandbox_id` (already
   done at `character_routes.py:432` for the bankroll lookup) and pass it
   to `load_cash_pair_stats`. (The admin Chip Economy panel already scopes
   per-sandbox via its dropdown — that path is correct.)
2. **`relationship_states` is fully global** (PK `observer_id,
   opponent_id`). Heat/respect/likability, notes, and nicknames carry
   across every save. The principle wants this **per-sandbox**, and it must
   land before the file cabinet (Phase 4) — the cabinet sorts/filters by
   heat, and a global relationship would bleed rivalries across saves.

   **⚠ DEFERRED — this is NOT a one-liner; it collides with prestige.**
   `relationship_states` is **bidirectional**: besides the human→AI rows the
   dossier reads, it stores AI→human / AI→AI **inbound** edges that
   accumulate **globally by design**. `load_inbound_relationships`
   (`relationship_repository.py:210`) feeds the **prestige "regard"**
   computation (shipped 2026-05-29, commits `105017b5` / `eb9bc354`), and
   the code is explicit that global is correct there. Adding `sandbox_id` to
   the PK forces a scoping decision on those inbound rows, and scoping them
   per-sandbox risks breaking prestige. There's also a backfill wrinkle:
   human-observer rows map to the owner's default sandbox via a SQL join to
   `sandboxes` (observer_id == owner_id), but AI-observer rows have no owner
   sandbox — the natural sandbox is the *opponent's* (the human whose world
   they're in), which is ambiguous for AI→AI edges.

   **Decision needed (open item #4) before building:** do inbound edges
   scope per-sandbox (and how does prestige adapt) or stay global while only
   the human→AI direction scopes? Until that's resolved, the migration is
   parked. It does **not** block Phase 1.

## Phase 1 — Persist behavioral observation across games (the foundation)

> **STATUS (2026-05-29): persistence layer SHIPPED on branch `dossiers`** (not
> yet committed). Done: v123 migration (`opponent_observation_lifetime` +
> `opponent_models.lifetime_applied_json`); `GameRepository.fold_observations_into_lifetime`
> (continuous delta-fold) + `load_observation_lifetime`;
> `MemoryManager.sandbox_id` accessor; fold wired + guarded at both
> hand-boundary saves (`game_routes.py`); dossier prefers the lifetime
> observation (`character_routes.py`, canonical rate derivation). Plus the
> cheap `cash_pair_stats` sandbox-scoping fix. 12 new tests green; 54
> game-repo/dispatch regression tests green.
>
> **Pressure & memorable now durable too (2026-05-29 follow-up):** both were
> live-only (vanished between games). Made durable via *aggregate-on-read
> scoped by owner* (≈ sandbox under 1:1): `PressureEventRepository.get_player_events_for_owner`
> replays the opponent's pressure events across the owner's games through the
> canonical `PlayerPressureStats` (reuses get_summary — no double-count);
> `GameRepository.load_lifetime_memorable_hands` pulls the human's top-impact
> hands vs that opponent across games. Dossier prefers both, falls back to
> the live builders. Still gated by the scouting tiers. **Remaining:**
> backfill (skipped by decision); live end-to-end verification; the file
> cabinet (Phase 4).

Everything else depends on this. Goal: **lifetime tendencies per
`(sandbox, observer, opponent)`, aggregated across every game within that
sandbox**, surviving game-end and restart.

- **New table(s)** keyed `(sandbox_id, observer_id, opponent_id)`. Store
  **cumulative counts, not rates**, so games merge losslessly and rates are
  derived on read:
  - `hands_dealt`, `hands_observed`
  - `vpip_count`, `pfr_count`
  - aggression: `bet_raise_count`, `call_count` (to recompute AF)
  - `showdowns_seen`, `showdowns_won`
  - `first_seen`, `last_updated`
- **Pressure / memorable** get a sibling lifetime aggregation. The per-game
  `pressure_events` rows already exist; roll them up into a
  per-`(sandbox, observer, opponent)` lifetime summary (HU record, biggest
  pots, bluff tallies) so the summary survives across games. Memorable
  hands persist their narrative + impact (and Phase 1 should fix the
  `hand_summary` persistence gap at `game_repository.py:961` if we want the
  narrative to survive cold-load).
- **Write cadence — DECIDED: roll-up at game-end + periodic checkpoint.**
  Per-game `opponent_models` are *already* persisted per-action, so the
  lifetime row is a **roll-up of the per-game model**, not a new hot-path
  write. A periodic checkpoint bounds crash loss to one session. No per-hand
  durable write.
- **Unlock state is DERIVED on read, never stored.** This resolves the
  "milestone mid-game" tension: an unlock flips the instant
  `persisted_lifetime_observed + live_session_observed >= threshold`,
  computed at read time. The live session count already lives in the
  in-memory `OpponentModel` and the dossier already overlays it, so the
  threshold can cross **mid-hand** with no per-hand persistence. Only
  *explicit* unlocks (informant purchases) get a stored row.
- **Read**: dossier prefers persisted **lifetime** stats; if a game is
  active, overlay the current-session deltas ("lifetime VPIP 22% ·
  this session 31%").

Outcome on its own: the dossier becomes durable and cross-game — the thing
you actually asked for — independent of any meta-game on top.

### Phase 1 build spec (concrete)

**Scope framing (DECIDED):** the lifetime store is a **Circuit-only**
feature, not "behavioral tracking for all modes made durable." The existing
per-game `opponent_models` tracking is **legacy in-game modeling** — it feeds
the AI controllers' read of an opponent *within the current game* (originally
for tournament mode), is ephemeral-by-game on purpose, and is **left
unchanged for every mode.** The lifetime store layers on top and fills *only*
from sandbox-bound games (Circuit cash + Circuit tournaments). Other modes
(standalone tournaments, quick, themed, custom) keep their legacy per-game
models for live decisions and **never feed the lifetime store** — nothing
consumes cross-game behavior for them. The `sandbox_id` gate is what enforces
this.

Verified groundwork: `OpponentTendencies` already keeps **raw counts**
(`_vpip_count`, `_pfr_count`, `_bet_raise_count`, `_call_count`,
`hands_observed`, `hands_dealt`, `_showdowns`, `_showdowns_won`, + a rich
postflop set) and serializes them into `opponent_models.tendencies_json`. So
counts exist per-game in countable form — the roll-up is lossless and
existing rows are backfillable.

**New table `opponent_observation_lifetime`** (migration **v123**, additive —
`CREATE TABLE`, no destructive change):
```
sandbox_id TEXT NOT NULL, observer_id TEXT NOT NULL, opponent_id TEXT NOT NULL,
hands_dealt INTEGER NOT NULL DEFAULT 0, hands_observed INTEGER NOT NULL DEFAULT 0,
vpip_count INTEGER NOT NULL DEFAULT 0, pfr_count INTEGER NOT NULL DEFAULT 0,
bet_raise_count INTEGER NOT NULL DEFAULT 0, call_count INTEGER NOT NULL DEFAULT 0,
showdowns_seen INTEGER NOT NULL DEFAULT 0, showdowns_won INTEGER NOT NULL DEFAULT 0,
first_seen TIMESTAMP, last_updated TIMESTAMP,
PRIMARY KEY (sandbox_id, observer_id, opponent_id)
```
v1 keeps the headline set (drives VPIP/PFR/AF + showdown — the BEHAVIORAL
INDEX); the deeper postflop counters can roll up later as gated "deeper
reads."

**Roll-up trigger — DECIDED: continuous delta-fold at the existing
hand-boundary save points** (`game_routes.py:1924`, `:2414`, where
`opponent_models` is already persisted each action). Chosen over a
leave/settle hook specifically to **stay off the cash leave/settle path** (a
known bug-prone area). Because the fold is delta-based and idempotent, doing
it every hand is safe and cheap:

- **Delta-based, resume-safe:** `delta = current_counts − applied`;
  `lifetime += delta`; `applied = current_counts`. Cash sessions are
  long-lived and resumable (cold-load reuses `game_id`); folding deltas
  (not once-per-game) handles resume correctly.
- **Applied high-water mark** stored as a new `lifetime_applied_json` column
  on `opponent_models` (additive `ALTER TABLE`; co-located with the source
  counts, same grain — no separate sidecar).
- **Sandbox-gated:** only folds when `memory_manager._sandbox_id` is present
  (Circuit cash + Circuit tournaments), so other modes never contribute.
- **Lifetime row is always current** (folded every hand) → the dossier reads
  it directly; no live overlay needed; milestones still cross mid-game.
- **Crash-safe** — folded each hand, not only at a clean exit.
- **Backfill:** fold existing `opponent_models` rows into lifetime once,
  mapping each `game_id` → its `sandbox_id` (join via the games table at
  backfill time; rows whose game can't be sandbox-mapped are skipped). Run as
  a separate idempotent script (backend-stopped), not auto-run.
- **Deferred (v1):** the "lifetime X% · *this session* Y%" split-display
  needs a session-start snapshot; v1 shows the lifetime number only.

## Phase 2 — Earning intel by playing (the grind)

> **STATUS (2026-05-29): SHIPPED on branch `dossiers`** (uncommitted). Server
> gates the dossier's earnable reads behind hands observed: pure
> `flask_app/services/dossier_scouting.py` (`SCOUTING_SCHEDULE`,
> `compute_scouting`, `apply_scouting_gate` — strips locked values + returns a
> `scouting` descriptor), wired into `get_dossier` (Circuit-only: only when a
> sandbox+observer+lifetime row exists; ungated elsewhere) behind kill switch
> `economy_flags.DOSSIER_SCOUTING_GATE_ENABLED`. Frontend `ScoutingStrip` in
> `CharacterDetailCard` renders the case-file CLASSIFIED/CLEARANCE treatment +
> progress bar + "still to scout" list; locked reads are absent from the
> payload. Floor 25; item drip 25→180 (tunable). 7 new gate tests; TS clean.
> **Deferred:** archetype badge (no detection source wired); live end-to-end
> declassification check in a real session.

The default path: you learn about someone by playing them. Intel reveals
itself as your sample with that opponent grows, **in their sandbox**.

- **Grind metric — DECIDED: hands observed** (hands you were dealt in with
  them). Counts their folds too, so a tight nit accrues scouting progress at
  the same rate as a maniac — you're not punished for playing tight players.
  This is exactly what `tendencies` are computed from.
- A dossier's earnable sections aren't available until you've observed an
  opponent for a **minimum floor — DECIDED: 25 hands** (tunable). Below
  that: a locked "you haven't seen enough of them yet" state.
- Past the floor, intel unlocks **bit by bit** as observed hands accumulate
  — you earn the read by putting in the time at the table.
- **Granularity — DECIDED: hybrid.** Grind reveals **items** as hand-count
  thresholds are crossed (a slow drip of individual bits); the informant
  unlocks a whole **section** at once (a chunkier payoff worth paying for).
  The two mechanisms get distinct textures.
- Per-item thresholds are **tuning, not design** — flagged in Open items.

## Phase 3 — The informant (pay to unlock)

> **STATUS (2026-05-29): SHIPPED on branch `dossiers`** (uncommitted). v124
> migration `dossier_informant_unlocks` (purchased sections per (sandbox,
> observer, opponent)); `INFORMANT_SECTIONS` in `dossier_scouting.py` (4
> sections, flat prices 500–1000, unioned with grind unlocks, bypasses the
> floor); `game_repo.load/record_informant_unlocks` (idempotent);
> `POST /api/character/<id>/informant` (debits bankroll → recyclable bank
> pool via new `informant_unlock` ledger reason, store-first to avoid
> double-charge, 402/409 guards); frontend buy buttons in `ScoutingStrip` +
> `buyInformantUnlock` API. 12 new tests (pure + repo + route); 85 green
> across dossier/ledger/flags. **Pricing & random-vs-chosen** left as tuning
> (chose player-chosen section for clean UX).
>
> **Mode-context (DECIDED):** the scouting unlock *state* shows wherever you
> view a dossier (your Circuit-earned reads carry over — consistent
> everywhere), but the informant's pay-to-unlock buttons appear **only in a
> Circuit context** (cash lobby/table), since that's where the bankroll
> lives. In a tournament the locked sections show an "unlock in the Circuit"
> hint instead of chip costs. Threaded via a `circuitContext` prop on
> `CharacterDetailCard` (Lobby = true, PokerTable = `gameState.cash_mode`).

The shortcut path, and a chip sink: **pay an informant** to reveal intel
you haven't grinded out yet.

- Spend chips → **unlock a still-locked section** for this opponent (hybrid:
  informant works at section granularity). Draw only from **still-locked**
  sections so a payment always makes progress (never "you paid for something
  you already had"). Whether the *specific* section is player-chosen or
  random is a tuning lever (random keeps it a gamble; chosen is cleaner UX).
- **Pricing — DECIDED: flat per-section unlock for v1**, with a scaling hook
  (by section depth or by opponent stakes) noted as a tunable lever. A real
  sink for the chip economy / bank pool.
- **Floor bypass — DECIDED: the informant works pre-floor too.** You can pay
  to learn about someone you've barely played — that's the fantasy ("I don't
  know this guy, so I pay to find out").
- **Implementation:** mirror the vice-spending chip-destruction pattern
  (`cash_mode/ai_vice_spending.py`): debit `PlayerBankrollState.chips` via
  `bankroll_repo`, then `core.economy.ledger.record(source=player, sink=bank,
  reason='informant_unlock', sandbox_id=...)`. This needs a **new
  `LEDGER_REASONS` entry** (`'informant_unlock'`) — it's the first
  player→bank direct chip-spend (top-up is an internal bankroll→stack move,
  not a sink). `sandbox_id` resolves server-side via
  `resolve_default_sandbox_for(owner_id)`.

Grind and informant are the **only two mechanisms for now** — a deliberate
simple foundation.

### Data model for unlocks

- **Grind unlocks: derived, not stored** (see Phase 1) — computed from
  observed-hand thresholds at read time.
- **Informant unlocks: stored** — a per-`(sandbox_id, observer_id,
  opponent_id)` set of explicitly-purchased section ids. The dossier's
  effective unlock state = derived grind unlocks ∪ stored informant unlocks.

## Phase 4 — The File Cabinet

> **STATUS (2026-05-29): SHIPPED on branch `dossiers`** (uncommitted). Backend:
> `GameRepository.list_observation_lifetime_for_observer` +
> `load_all_informant_unlocks_for_observer`; pure aggregator
> `flask_app/services/file_cabinet.py` (`build_file_cabinet` — roster from the
> lifetime store joined to PnL/relationship/names, derives unlock progress +
> "people met / dossiers unlocked" via `compute_scouting`); `GET
> /api/cash/file-cabinet`. Frontend: `FileCabinetDrawer.tsx` (+ CSS) — portal
> modal with sort controls (most-played / progress / rivals / winners /
> losers / recent), per-opponent unlock bar, rivalry flag, PnL; opens the
> dossier (Circuit context) on tap. Wired into the Lobby ("File cabinet"
> button). 5 new tests (aggregator + route); roster sourced from the lifetime
> store (everyone observed), so it aligns with the grind gate.

- A browsable index of **everyone you've met** in the sandbox (shared a
  table / >0 hands).
- **Scope — DECIDED: everyone you've met *in the sandbox*** — Circuit cash
  and Circuit tournaments both count (they share the sandbox). Standalone
  (non-Circuit) tournaments are excluded by design. So the cabinet is "the
  field of this Circuit save," not "cash only." For v1 this is effectively
  cash + any Circuit-tournament play that's been wired to stamp the sandbox.
- Each entry → their dossier, subject to the gating above.
- Sort/filter: most-played, rivals (heat), biggest winners/losers vs. you,
  recently seen, locked vs. unlocked.
- Header stats: "People met: N · Dossiers unlocked: M." A retention surface
  — collect-'em-all, and a reason to keep playing the same opponents.
- **Build on:** `relationship_repo.load_all_relationships(observer_id)`
  (scoped per-sandbox after the v123 migration) as the index source;
  `WhereaboutsDrawer.tsx` is the closest existing met-filtered browse view
  to scaffold from. Needs a new endpoint (e.g. `GET /api/cash/met-opponents`)
  + a new React list component, each row opening `CharacterDetailCard`.

## Archetype badge (cross-cutting)

Surface personality archetype (fish / whale / regular) on the dossier — but
make it **itself a gateable item** ("you've figured out this one's a fish"),
revealed by the grind or bought from the informant like any other intel.
Wires the whale/fish work (`CASH_MODE_WHALE_AT_CARDROOM.md`,
`CASH_MODE_FISH_AS_PERSONAS.md`) into the scouting meta-game. A fish's loose
VPIP is by-design, so flagging it changes how the player reads the numbers.

## Identity

**DECIDED: one dossier per `personality_id`.** A whale and its later fish
appearances share a single dossier — intel is about the *person*, not the
role they're playing this session. Matches how `relationship_states`,
`cash_pair_stats`, and `ai_bankroll` already key.

## Achievements tie-in (planned hook, but not a build dependency)

The achievements system (`ACHIEVEMENTS_SYSTEM.md`) is **spec-only, not
built** — no `.py` references it yet. So v1 scouting unlocks are **fully
self-contained** (derived on read); scouting does **not** block on
achievements, and achievements aren't required for the meta-game to work.

**Planned tie-in (Jeff):** a **"dossiers fully unlocked" count** achievement —
the file cabinet's "Dossiers unlocked: M" is the natural counter, and tiered
badges (unlock your 1st / 5th / 25th full dossier) reward the collect-'em-all
loop. When the achievements registry ships, this is a one-entry addition that
reads the same unlock state the cabinet already computes. The live mid-game
milestone-crossing (Phase 1's derived unlock flipping at the table) becomes
the celebratory trigger then. Until then the count simply lives in the
cabinet header.

**Scoping note (DECIDED):** while scouting *intel* is per-sandbox,
**achievements track globally across all modes and sandboxes** — they're a
career-spanning layer, not a per-save one. So a "dossiers unlocked" badge
counts full dossiers across every Circuit save (and any future tournament
scouting), summing the per-sandbox unlock counts into one global tally. This
mirrors the achievements system's own cross-mode (cash + tournament) design.

## Future (parked — not building yet)

Explicitly out of scope for the first cut, noted so we don't design them in
prematurely:
- **Perks / upgrades** (e.g. a "Data Collector" that passively accelerates
  intel or scouts opponents you're not seated with).
- **A "reading opponents" skill tree** (level up faster/deeper reads).
- **Tournament-opponent scouting + cabinet** (gated on binding tournaments
  to a sandbox).
- **Live achievement celebration** on milestone-crossing (gated on the
  achievements system shipping).

These layer cleanly on top of grind + informant later if they earn their
keep.

## Decided

- **Scope: per-sandbox** — new intel keys on `(sandbox_id, observer,
  opponent)`; each save is independent except the shared personality.
- **Cross-sandbox leaks:** cheap `cash_pair_stats` read fix lands now;
  `relationship_states` per-sandbox migration **deferred** (collides with the
  global-by-design prestige inbound graph — needs open item #4 resolved
  first; only blocks Phase 4, not Phase 1).
- **Earn mechanisms: grind + informant only.** Perks / skill tree parked.
- **Granularity: hybrid** — grind drips items, informant buys a section.
- **Grind metric: hands observed.** Floor: 25 (tunable). Informant bypasses
  the floor.
- **Write cadence: roll-up at game-end + checkpoint; unlock state derived on
  read** (mid-game milestones with no per-hand writes).
- **Informant pricing: flat per-section for v1**, scaling hook as a lever.
- **Identity: one dossier per `personality_id`** (whale + fish share).
- **Mode scoping: intel accrues for sandbox-bound games only.** Circuit cash
  + Circuit tournaments share the sandbox and carry seamlessly; standalone
  (non-Circuit) tournaments never bleed in. Gate is "has `sandbox_id`," not
  "is cash mode."
- **Tournament-specific stats: a separate sibling track**, same key, added as
  needed — not part of the v1 cash-read aggregation.
- **File cabinet: everyone met in the sandbox** (cash + Circuit tournaments).
- **Live emotion stays live-only** — it's a now-state, not a stat.
- **Archetype is a gateable bit**, not always-shown.
- **Achievements: no build dependency, and tracked GLOBALLY across all
  modes/sandboxes.** Unlocks are self-contained per-sandbox; the planned
  **"dossiers fully unlocked" count** achievement sums them into one global,
  career-spanning tally (+ live milestone celebration) once the registry
  ships.

## Open items (tuning, not blocking design)

1. **Per-item grind thresholds** — which item unlocks at which observed-hand
   count (the drip curve). Tune after the data model lands.
2. **Informant: random vs. player-chosen section**, and the scaling curve
   (flat / by-section-depth / by-stakes).
3. **Informant base price** — first-pass number, then tune as a chip sink.
4. **`load_inbound_relationships` scoping** — during the v123 migration,
   decide whether inbound edges scope per-sandbox or stay global.
5. **Memorable-hand narrative persistence** — fix `game_repository.py:961`
   so `hand_summary` survives cold-load (only if we want the narrative in
   the lifetime view).

## Phasing / dependency

```
v123 relationship migration ──┐
cash_pair_stats read fix ─────┤  (independent cleanups, do early)
                              │
Phase 1 (per-sandbox lifetime persistence) ──┬── prerequisite for everything
                                              │
                    ┌─────────────────────────┼─────────────────────────┐
              Phase 2 (grind)          Phase 3 (informant)        Archetype bit
              + granularity            (chip sink + ledger)       (rides Phase 2)
                    └─────────────────────────┬─────────────────────────┘
                                              │
                              Phase 4 (file cabinet)
                              (needs v123 migration done)
```

Phase 1 is the prerequisite. Phase 2 (grind) and Phase 3 (informant) build
on it and the granularity decision. The file cabinet (Phase 4) additionally
needs the v123 relationship migration done. The cross-sandbox cleanups are
independent and can land first.

## Related

- Dossier code: `flask_app/routes/character_routes.py:347` (endpoint),
  `react/react/src/components/character/CharacterDetailCard.tsx` (UI),
  `react/react/src/components/character/api.ts` (fetch),
  `poker/repositories/relationship_repository.py` (`load_cash_pair_stats`,
  `load_all_relationships`),
  `poker/memory/opponent_model.py` (`OpponentModel.tendencies`),
  `poker/pressure_stats.py` (`PlayerPressureStats`, `PressureStatsTracker`).
- Persistence: `poker/repositories/schema_manager.py` (schemas at
  `:616-628` relationship_states, `:638-645` cash_pair_stats; current
  `SCHEMA_VERSION = 122`), `poker/repositories/game_repository.py`
  (opponent-model save/load).
- Chip sink: `core/economy/ledger.py` (`record`, `LEDGER_REASONS`),
  `cash_mode/ai_vice_spending.py` (destruction pattern to mirror).
- Sandbox: `flask_app/services/sandbox_resolver.py`
  (`resolve_default_sandbox_for`).
- File cabinet scaffold: `react/react/src/components/cash/WhereaboutsDrawer.tsx`.
- `docs/plans/CASH_MODE_WHALE_AT_CARDROOM.md`,
  `docs/plans/CASH_MODE_FISH_AS_PERSONAS.md` — the archetype source.
- `docs/plans/ACHIEVEMENTS_SYSTEM.md` — future tie-in.
- `docs/vision/GAME_VISION.md`, `docs/vision/FEATURE_IDEAS.md` — parent
  vision.
