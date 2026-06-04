---
purpose: Synthesis of four architect proposals for unifying tournament games with cash games (identity + play-path), with a recommended path
type: design
created: 2026-06-04
last_updated: 2026-06-04
---

# Tournament ↔ Cash Unification — Architecture Options

> **Context.** Owner thesis: *"It's just a table. Put the player at it. Cash tables
> work great. The ONLY real difference is the chips are funny-money. Reseating is the
> director's job."* The headless multi-table engine (`tournament/engine_resolver.py` +
> `director.py`) was a **temporary proof-of-concept to learn seating/balance/conservation
> from** — not the production architecture. **Seat-identity unification is the gating
> release issue.** Gut-and-replace is authorized: no legacy/back-compat, no old-save
> migration.

## Correction on the record

The off-screen tables are **not** a statistical chip simulation. `engine_resolver.py`
builds real `Player`/`PokerGameState`/`PokerStateMachine` objects and plays a real hand
each round via `run_cc_hand`, then reads stacks back. The field (`field.stacks` /
`field.entries`) is the **between-rounds persistent state**; `entries[pid] = archetype`
is just the bot-strategy assignment for that seat (the spec's own open question #3).
The engine largely matches the spec. The real wart is the **bespoke ~600-line live
human-table builder** (`tournament_game_builder.py`) that re-implements "stand up a
table" with its **own seat-identity convention** — the source of the stub-name leaks.

---

## What all four architects agree on (the non-negotiables)

1. **Canonical seat identity (the release gate):** `Player.name` = **display name** in
   every mode. The stable key is `personality_id` (human = `owner_id`, e.g. a
   `"human:<owner_id>"` sentinel). `nickname` is **deleted** as an identity carrier.
2. **One lookup primitive:** controllers / memory / bot_types / field write-back key on
   the stable id, **never** on the display name. (`seat_key(player) -> personality_id`,
   or a typed `SeatId` — see the sub-decision below.) This makes the duplicate-display-name
   collision (two "Fish" seats, risk R1) **structurally impossible**.
3. **Keep the good parts of the PoC:** the pure `tournament/` field / seating / blinds
   logic and the two-ledger funny-money **escrow/payout economy**
   (`tournament_economy_service.py`) are correct — keep them.
4. **The headless field needs exactly ONE view-boundary resolver.** Because off-screen
   tables have no `Player` objects between rounds, id→display resolution at the
   standings/ticker boundary cannot be deleted — but it collapses to a single site
   (`tournament_naming.named_standings` → `resolve_display_names`). Delete
   `resolve_display_name` (singular) and every per-surface `nickname` fallback.
5. **~3–4 days to unblock release** on the identity fix alone (the codebase is already
   ~40% there: `Player.personality_id` exists, is serialized, and the builders set it).
6. **F1 and F2 already landed** (autonomous `human_id=None`; whereabouts `human:*`
   filter). Remaining work is the core `name`→display flip + re-keying.

---

## The three architecture options (how far to unify the play paths)

### Option A — Refactor in place
Flip `Player.name`→display in the two tournament builders + the reconcile site, add
`seat_key()`, re-key the tournament hand-flow on it. **Keep the headless engine, the
director, and the field as-is.** Cash converges onto `seat_key` post-release.

- **Effort:** ~4 person-days to the release gate.
- **Wins:** smallest blast radius; the hard part (field-as-truth reseating) is untouched;
  ships fast and low-risk against the existing 342-test suite.
- **Loses:** leaves **two duplicated table-builders** through release — the "convoluted"
  feeling is only ~70% relieved. Doesn't realize the owner's "it's just a table" thesis;
  it just stops the leaks.

### Option B — Tournament IS a cash table (+ funny money + director)
A tournament table is a `cash_tables` row with `table_type='tournament'` in an ephemeral
sandbox, played by the **exact cash game loop** with the **exact cash identity model**.
Only deltas: no rake, no per-hand bankroll writes, funny-money chips, no world-tick
auto-advance. A thin `TournamentDirector` escrows chips at the two ledger boundaries and
moves **real players between real tables** via one atomic seat-transfer primitive
(reusing the hardened `SeatOccupancyRegistry`). Off-screen tables run the **same engine**
at LLM-off fidelity; human + featured tables run LLM-on. **Delete**
`tournament_game_builder.py`, `tournament/identity.py`, `Player.nickname`,
`session.py`'s resolver indirection, `engine_resolver.py`.

- **Effort:** ~4–6 days release slice (single-table tournament w/ unified identity +
  economy); +5–8 days multi-table.
- **Wins:** the gating issue isn't *fixed*, it's made **structurally impossible** — there
  is only one seat model and one play path. Inherits every hardened cash invariant
  (audited seat registry, verified escrow, immutable loop, dossier/psychology wiring) for
  free. **This is the most literal realization of the owner's thesis.**
- **Loses / riskiest unknown:** the whole thesis rests on the cash `progress_game` hand
  boundary being genuinely `table_type`-agnostic. **If cash semantics (bankroll-per-hand,
  lobby refill, presence machine) are woven into the boundary rather than cleanly
  switchable, "reuse the cash loop" degrades into "fork it with `if tournament`
  conditionals" — the exact spaghetti this set out to avoid.** Needs a spike first.

### Option C — One `Table` primitive + pluggable runners
Extract a `Table` primitive (`poker/table/`: a `PokerStateMachine` + seated `Player`s +
controllers + memory) addressed by a **structured, typed `SeatId`** =
`HumanSeat(owner_id) | PersonaSeat(personality_id)`. Two runners drive the same `Table`:
a **live runner** (Flask/socket/LLM, one human seat) and a **headless runner**
(`run_cc_hand`, tiered bots). The `TournamentDirector` orchestrates a *set* of `Table`s;
**cash is the degenerate single-`Table` case**. Cash retrofits onto the primitive
*post-release*.

- **Effort:** ~3–4 days to unblock release (the identity re-key); ~3 weeks to full
  unification.
- **Wins:** the typed `SeatId` makes the bug a **compile-time impossibility** (no string
  is ever both a key and a label) — the strongest identity guarantee of the three. Finally
  unifies all *three* drifted play paths (cash builder, tournament builder, headless
  resolver) behind one primitive + a clean runner seam.
- **Loses / riskiest unknown:** same `progress_game`-factoring risk as B — if the live
  loop can't be cleanly wrapped, `LiveRunner` becomes a second monolith and "one
  primitive" is only half-true. Widest mechanical blast radius (`Player` touched
  everywhere + frontend TS type); full payoff (deleting cash's builder) only lands after
  the deferred post-release cash retrofit.

---

## Sub-decision: the identity primitive

- **`seat_key()` string** (Options A/B and the identity specialist): pragmatic, aligns
  with the existing `"human:<owner>"` field-id convention, smallest change. The id is a
  string; correctness is a discipline applied at ~35 call sites.
- **Typed `SeatId` sum type** (Option C): `HumanSeat | PersonaSeat`. More robust — the
  compiler enforces that a display label can never be used as a key — but more invasive
  (touches the frozen `Player`, serialization, and the frontend `Player` TS type).

If the goal is "get it right and never fight this again," the typed `SeatId` is the
stronger primitive and can be adopted under **any** of the three options.

---

## The frontend re-key (called out by the identity specialist; others underplayed it)

Today the React felt keys on `player.name` for React keys, find-index, revealed-cards
map, chat `sender` match, chat target selection, and nickname overrides
(`PokerTable.tsx`, `ChatTargetSelector.tsx`, `nicknameOverridesStore.ts`). Under the
canonical model these must re-key on `personality_id`, and the **served player payload +
chat `sender` + `revealed_cards` keys must carry/use the stable id**, not the display
name — otherwise duplicate display names collide on the client too.

---

## Recommended path

1. **Land the canonical identity model now (the gate, ~3–4 days), independent of the big
   architecture.** It's a prerequisite for B and C and is compatible with A. Use the
   identity specialist's coupling inventory (groups A–I, file:line). Strongly consider the
   **typed `SeatId`** since we're gutting anyway. This kills the stub-name leaks and
   unblocks release.
2. **Run the pivotal spike in parallel:** is the cash `progress_game` hand boundary
   genuinely `table_type`-agnostic? Point a read-only probe at one in-flight tournament's
   game-state and confirm the boundary hooks (bankroll, lobby refill, presence) can be
   switched off cleanly. **This single answer decides B vs C vs A.**
3. **Commit to the target architecture based on the spike:**
   - Spike clean → **Option B** (most direct realization of the thesis, lowest risk,
     reuses hardened cash machinery).
   - Spike shows the loop is factorable but cash-coupled → **Option C** (extract the
     primitive so neither mode owns the loop).
   - Spike shows the loop is a tar pit and release is imminent → **Option A** now, B/C
     after release.

The identity fix is the same first move in all three. The architecture choice is
reversible *after* the gate; the identity model is not — so get it right first.

---

## DECISION (owner, 2026-06-04): Option C + typed `SeatId`

Build the shared `Table` primitive with pluggable live/headless runners, and a
**typed `SeatId` = `HumanSeat(owner_id) | PersonaSeat(personality_id)`** so a
display label can never be used as a key. Phase 1 (the typed-`SeatId` identity
model) is the release gate and lands first; Phase 2 extracts the `Table`
primitive; cash retrofits onto it.

## Owner-confirmed tournament domain rules

1. **No bankroll.** Only chips on the table. Buy-in (and any rebuy in a
   multi-buy-in event) converts bankroll→table chips at entry; the bankroll is
   never touched again until payout. No per-hand bankroll/cash-out writes.
2. **No voluntary leave.** Only the director moves a player between seats. The
   cash "stand up / leave seat" path does not exist in a tournament.
3. **Human quit = forfeit.** Chips are forfeited, the director removes the
   player, and play continues without them. (Not a cash-out.)
4. **Presence is reused, not bypassed.** The player is "at the tournament table,"
   reported to presence **in the sandbox**. The tournament is a *member* of the
   sandbox, like any other table.

## Spike result — `progress_game` IS mode-gated (Option C is feasible)

Read `flask_app/handlers/game_handler.py::progress_game` (3815) and
`handle_evaluating_hand_phase` (3062–3637). Findings:

- The hand-boundary **shared core** (determine winner → award pot →
  `on_hand_complete` → psychology pipeline → commentary → clear cards → deal
  next) is genuinely mode-agnostic.
- **All cash economics are behind `if game_data.get('cash_mode'):`** — rake,
  scalp tracking, `_refill_cash_seats`, reputation demeanor,
  `_detect_human_cash_bust`, lobby refresh, solo-pause/rebuy. Tournament games
  don't set `cash_mode`, so none of it runs for them.
- Single-table tournament → `single_table_hand_boundary` (gated on
  `tournament_session and not tournament_multi_table`); multi-table →
  `tournament_hand_boundary` (gated on `tournament_session`).

**Conclusion:** not a cash-coupled tar pit. The `LiveRunner` = `progress_game`
with the inline `if cash_mode` / `if tournament_session` branches **lifted into
named boundary hooks** on the `Table`, plus shared bits (`hand_start_stacks`,
controller/memory maps) re-keyed on `SeatId`. The headless runner already exists
(`run_cc_hand` via `engine_resolver`). Presence needs one small addition: a
tournament boundary step that reports SEATED presence in the sandbox without
enabling cash economics.
