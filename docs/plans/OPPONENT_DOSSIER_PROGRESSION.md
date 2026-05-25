---
purpose: Turn the opponent dossier into a persistent, gated scouting meta-game — lifetime intel you accumulate, unlock, and browse
type: vision
created: 2026-05-25
last_updated: 2026-05-25
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

Dossier endpoint: `GET /api/character/<id>/dossier`
(`flask_app/routes/character_routes.py`), rendered by
`react/.../character/CharacterDetailCard.tsx`.

**Already persists cross-game + survives restart:**
- `cash_pair_stats` — `cumulative_pnl`, `hands_played_cash` per
  `(sandbox_id, observer_id, opponent_id)` (PK is sandbox-scoped since
  v109), but the **dossier reads it cross-sandbox** via
  `load_cash_pair_stats(..., sandbox_id=None)`, which SUMs every sandbox.
  NB: `hands_played_cash` counts *confrontation* hands (chips actually
  flowed between the pair), not every hand sat together.
- `relationship_states` — heat / respect / likability (decay-projected),
  `last_seen`, player-authored `note`, nickname. PK is
  `(observer_id, opponent_id)` — **fully global**, no sandbox scoping.

**Live-only today** (built from in-memory `game_data`, lost on game
end / backend restart; `character_routes.py:463-470`):
- `observation` — VPIP, PFR, aggression factor, hands observed
  (`OpponentModel.tendencies`).
- `pressure_summary` — signature move, biggest pot won/lost, successful
  bluffs, bluffs caught, bad beats, heads-up record (`PressureStats`).
- `memorable_hands` — top hands by impact (`OpponentModel.memorable_hands`).
- `emotion` — live psychology display state. **Stays live-only by design**
  (it's a present-moment state, not a stat to accumulate).

So the durable PnL/relationship spine already exists; the **behavioral
reads are the gap**, and they're the most valuable scouting signal.

## Design principle: each sandbox is independent (except the personality)

**Decision (Jeff):** a sandbox is a self-contained world/save. The only
thing shared across sandboxes is the **personality** itself (its anchors,
archetype, base config). Everything *relational* — what you've won/lost
off someone, your history and standing with them, the intel you've
gathered — belongs to the sandbox you built it in. You shouldn't see PnL
or a rivalry bleed from one save into another.

New observation/intel stats therefore key on
`(sandbox_id, observer_id, opponent_id)`.

**Two existing things violate this and need a decision:**

1. **`cash_pair_stats` dossier read pools cross-sandbox.** Storage is
   already per-sandbox; only the dossier's `sandbox_id=None` read sums
   across saves. *Cheap fix:* pass the active `sandbox_id` to
   `load_cash_pair_stats`. (The admin Chip Economy panel deliberately
   scopes per-sandbox via its dropdown — that path is already correct.)
2. **`relationship_states` is fully global** (PK `observer_id,
   opponent_id` — no `sandbox_id`). Heat/respect/likability, notes, and
   nicknames carry across every save. To honor the principle this needs a
   **schema migration** (add `sandbox_id` to the PK) + backfill, and the
   dossier/event-write paths threaded with the sandbox. Bigger lift.

Open call: fix both now (consistent, but #2 is a migration), or fix the
cheap one (#1) + build new intel per-sandbox and migrate relationships
later. Either way, the **new** stats are per-sandbox from day one.

## Phase 1 — Persist behavioral observation across games (the foundation)

Everything else depends on this. Goal: lifetime tendencies per
`(observer, opponent)` across **all** games, surviving restart.

- **New table(s)** keyed `(sandbox_id, observer_id, opponent_id)` —
  per-sandbox lifetime (per the principle above), aggregating across every
  game *within that sandbox*. Store **cumulative counts, not rates**, so
  games merge losslessly and rates are derived on read:
  - `hands_dealt`, `hands_observed`
  - `vpip_count`, `pfr_count`
  - aggression: `bet_raise_count`, `call_count` (to recompute AF)
  - `showdowns_seen`, `showdowns_won`
  - `first_seen`, `last_updated`
- **Pressure / memorable** get a sibling event log (e.g. `pressure_events`:
  observer, opponent, type, pot_size, impact, ts, game_id) so summaries,
  HU record, and biggest pots aggregate over a lifetime and survive
  restart. Memorable hands can persist their narrative + impact.
- **Write cadence** (open question): per-hand durable vs. flush at
  session/game boundary. Lean: flush the in-memory `OpponentModel` deltas
  into the persisted row at game end + a periodic checkpoint, so a crash
  loses at most one session.
- **Read**: dossier prefers persisted **lifetime** stats; if a game is
  active, overlay the current-session deltas ("lifetime VPIP 22% ·
  this session 31%").

Outcome on its own: the dossier becomes durable and cross-game — the thing
you actually asked for — independent of any meta-game on top.

## Phase 2 — Earning intel by playing (the grind)

The default path: you learn about someone by playing them. Intel reveals
itself as your sample with that opponent grows, **in their sandbox**.

- A dossier isn't available until you've played an opponent some
  **minimum number of hands** (tunable, e.g. 25). Below that: a locked
  "you haven't seen enough of them yet" state.
- Past the floor, intel unlocks **bit by bit** as hands accumulate — you
  earn the read by putting in the time at the table.
- Tunable thresholds per bit/section (see Granularity below).

## Phase 3 — The informant (pay to unlock)

The shortcut path, and a chip sink: **pay an informant** to reveal intel
you haven't grinded out yet.

- Spend chips → **randomly unlock a still-locked section/bit** for this
  opponent. Random keeps it a gamble (you don't get to cherry-pick the
  exact read), but draw only from **still-locked** items so a payment
  always makes progress (never "you paid for something you already had").
- Pricing is a lever: flat per unlock, or scaling (later sections cost
  more), or scaling with the opponent's stakes. A real sink for the chip
  economy / bank pool.
- Open: can you pay to beat the **minimum-hands floor** entirely (buy a
  dossier on someone you've barely played), or does the informant only
  work once a dossier exists? Leaning: informant works pre-floor too —
  that's the fantasy ("I don't know this guy, so I pay to find out").

Grind and informant are the **only two mechanisms for now** — a deliberate
simple foundation. Granularity (below) is the next thing to pin down.

### Granularity — what is a "bit" of intel?

The unit that grind reveals over time and the informant unlocks at random.
Options, coarse → fine:

- **Section-level** — the dossier's existing groupings (Basic read /
  Pattern / Pressure / Exploit). Simplest; informant unlocks a whole
  section. Fewer, chunkier unlocks.
- **Item-level ("bits")** — each individual stat/fact is its own unlock
  (VPIP, then PFR, then aggression factor, then signature move, then a
  specific exploit line…). Matches the "unlock bits over time" feel;
  more granular collection, more unlock events to tune.
- **Hybrid (lean)** — grind reveals **items** as hand-count thresholds are
  crossed (slow drip of bits); the informant unlocks a **section** at once
  (a chunkier payoff worth paying for). Gives the two mechanisms distinct
  textures.

Decide this before building Phase 2/3 — it sets the data model (a
per-`(sandbox, observer, opponent)` set of unlocked bit/section ids) and
the UI (locked placeholders at whatever granularity).

## Future (parked — not building yet)

Explicitly out of scope for the first cut, noted so we don't design them
in prematurely:
- **Perks / upgrades** (e.g. a "Data Collector" that passively accelerates
  intel or scouts opponents you're not seated with).
- **A "reading opponents" skill tree** (level up faster/deeper reads).

These layer cleanly on top of grind + informant later if they earn their
keep.

## Phase 4 — The File Cabinet

- A browsable index of **everyone you've met** (shared a table / >0 hands).
- Each entry → their dossier, subject to the gating above.
- Sort/filter: most-played, rivals (heat), biggest winners/losers vs. you,
  recently seen, locked vs. unlocked.
- Header stats: "People met: N · Dossiers unlocked: M." A retention surface
  — collect-'em-all, and a reason to keep playing the same opponents.

## Archetype badge (cross-cutting)

Surface personality archetype (fish / whale / regular) on the dossier —
but make it **itself a locked bit** ("you've figured out this one's a
fish"), revealed by the grind or bought from the informant like any other
intel. Wires the whale/fish work (`CASH_MODE_WHALE_AT_CARDROOM.md`,
`CASH_MODE_FISH_AS_PERSONAS.md`) into the scouting meta-game. A fish's loose
VPIP is by-design, so flagging it changes how the player reads the numbers.

## Decided

- **Scope: per-sandbox** — new intel keys on `(sandbox_id, observer,
  opponent)`; each save is independent except the shared personality.
- **Earn mechanisms: grind + informant only.** Perks / skill tree parked.
- **Live emotion stays live-only** — it's a now-state, not a stat.

## Open questions / decisions to make

1. **Existing cross-sandbox leaks**: fix the `cash_pair_stats` dossier read
   (cheap) now? Migrate `relationship_states` to per-sandbox (schema
   migration) now or later? (See "Design principle" above.)
2. **Granularity**: section-level vs. item-level vs. hybrid (see Phase 3).
   Sets the data model + UI — decide before building 2/3.
3. **Write cadence**: per-hand durable vs. session-flush + checkpoint.
4. **Identity**: per-character (`personality_id`, e.g. `vacation_greg`)
   across all appearances. Confirm a whale and its later fish appearances
   share one dossier (lean yes).
5. **Minimum-hands floor**: value, and whether the informant can bypass it.
6. **Grind metric**: hands-played, hands-observed, or confrontation-hands?
7. **Informant pricing**: flat / scaling-by-section / scaling-by-stakes.
8. **Cabinet scope**: cash + tournament opponents, or cash only?
   (Lean: everyone you've met in the sandbox.)

## Phasing / dependency

Phase 1 (per-sandbox persistence) is the prerequisite for everything.
Phase 2 (grind) and Phase 3 (informant) build on it and on the granularity
decision; the file cabinet (Phase 4) and the archetype bit can ride
alongside. The existing cross-sandbox leaks (open question 1) are
independent cleanups that can happen any time.

## Related

- Dossier code: `flask_app/routes/character_routes.py`,
  `react/.../character/CharacterDetailCard.tsx`,
  `poker/repositories/relationship_repository.py`,
  `poker/memory/opponent_model.py` (`OpponentModel.tendencies`),
  `PressureStats`.
- `docs/plans/CASH_MODE_WHALE_AT_CARDROOM.md`,
  `docs/plans/CASH_MODE_FISH_AS_PERSONAS.md` — the archetype source.
- `docs/vision/GAME_VISION.md`, `docs/vision/FEATURE_IDEAS.md` — parent
  vision.
