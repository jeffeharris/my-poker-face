---
purpose: Close the chip loop — redirect table rake into the bank pool and replace passive idle regen with an active, off-grid "side hustle" that draws from the pool
type: design
created: 2026-05-24
last_updated: 2026-05-24
---

# Cash Mode — The Side Hustle (closing the chip loop)

## Thesis

Two gaps from `CASH_MODE_CLOSED_ECONOMY.md` get closed together:

1. **Rake currently evaporates.** `table_rake` destroys chips (`winner →
   central_bank`) but is *not* in `BANK_POOL_DEPOSIT_REASONS`, so the raked
   chips leave the universe instead of feeding the recyclable pool.
2. **Regen is an unbounded faucet from nowhere.** `ai_regen` creates chips
   `central_bank → ai` based purely on elapsed wall-clock time, even while
   the AI is sitting at a poker table. Nothing bounds it.

The fix turns these into one closed loop:

```
   rake (winner → pool)            ┐
   vice  (rich AI → pool)          ├──►  bank pool  ──►  side hustle (pool → broke AI)
   casino_seat_return (→ pool)     ┘                     tourist_injection / casino_seat_seed
```

Chips destroyed by rake and vice become the *only* fuel for the chips
re-created by the side hustle. When the pool is fat (lots of recent rake /
vice), broke AIs recover fast; when it is dry, recovery stalls — real
scarcity, no magic faucet.

The side hustle is the **mirror image of vice** (`CASH_MODE_AI_VICE_SPENDING.md`):

| | Vice (shipped) | Side hustle (this doc) |
|---|---|---|
| Who | rich AIs (concentration ≥ 2.5× cast median) | broke AIs (can't afford to play) |
| Chip flow | `ai → central_bank` (pool **deposit**) | `central_bank → ai` (pool **draw**) |
| Off-grid | yes, for an LLM-chosen duration | yes, for an LLM-chosen duration |
| Amount | fraction of *excess* above baseline, jittered | fraction of *deficit* below baseline, jittered |
| Narration | character-appropriate indulgence | character-appropriate hustle |
| Ticker | `vice_start` / `vice_end` | `hustle_start` / `hustle_end` |
| Psych effect | pull toward baseline (relief) | none in v1 (hook reserved) |

Because the shapes match, the build is mostly "clone the vice plumbing and
flip the sign."

---

## Part A — Redirect rake to the bank pool

The bank pool is virtual: `compute_bank_pool_reserves()` returns
`Σ(BANK_POOL_DEPOSIT_REASONS destructions) − Σ(BANK_POOL_DRAW_REASONS
creations)` (`cash_mode/closed_economy.py:182`). `record_table_rake`
already writes a destruction (`winner → central_bank`,
`core/economy/ledger.py:362`).

**Change:** add `'table_rake'` to `BANK_POOL_DEPOSIT_REASONS`
(`core/economy/ledger.py:89`). Nothing about the ledger entry's direction
changes — raked chips simply start counting toward recyclable pool depth.

**What this does and doesn't change:**
- Chips are *still* destroyed at rake time; the universe still shrinks
  per hand. The conservation invariant (`drift == 0`) is untouched —
  every entry is still a real ledger row.
- The only change is the *pool-depth view*: raked chips are now marked
  "recyclable" and become drawable by the side hustle / tourist injection.
- **Test/audit follow-up:** grep `table_rake` in `tests/` and the audit
  endpoint. Any test that computes `compute_bank_pool_reserves` with rake
  present, or that classifies `table_rake` as pure (non-pool) destruction,
  needs its expectation updated. This is the main risk surface of Part A.

---

## Part B — The Side Hustle

### Concept

When an AI is too broke to sit at any table, instead of recovering
passively while idle, they **leave the field and go work a personal side
hustle** — a character-appropriate way of earning money that an LLM
invents per personality (Napoleon flips a small business, Bezos spins up
a logistics side gig, Hemingway ghost-writes). They are off-grid for an
LLM-chosen duration, then return with a lump of chips drawn from the bank
pool. Casino tables remain the *preferred* place: any AI who can afford to
play, plays — the hustle is strictly the fallback for those who can't.

### Off-table states (the zone taxonomy)

Turning off passive regen forces a clarification that didn't matter
before: idle and earning become **distinct states**. Previously the idle
pool *was* where regen happened, so "idle" and "slowly earning" were the
same thing. Now they split. An AI is in exactly one of four states:

| State | Who | Off-grid | Chips | Backed by |
|---|---|---|---|---|
| **Seated** | anyone who can afford to play (preferred) | no | wins/loses at the table | `cash_tables` seat |
| **Idle / on break** | *solvent* AIs between tables (resting, cycling, queued to move up) | yes | none — truly idle, no faucet | existing `cash_idle_pool` (`take_break`, `bored_move`, `stake_up_queued`) |
| **Side hustle** | *broke* AIs who can't afford to play | yes | earns from the pool | new `ai_side_hustle_state` |
| **Vice** | *rich* AIs (concentration ≥ 2.5× median) | yes | spends into the pool | existing `ai_vice_state` |

So **yes — there is still a "completely idle" zone, and it's the existing
idle pool.** The change is conceptual: idle used to mean "resting *and*
slowly accruing chips"; now it means just resting. The `forced_leave`
idle reason (busted AIs) is precisely the population that should route to
the side hustle — `forced_leave` becomes the precursor signal the hustle
selector reads, rather than a state where chips quietly regenerate.

### State model

Mirror `ai_vice_state` exactly:

- New table `ai_side_hustle_state` (schema migration): `(personality_id,
  sandbox_id, started_at, ends_at, amount, duration_bucket, narration)`,
  PK `(personality_id, sandbox_id)`.
- New `SideHustleStateRepository` mirroring `ViceStateRepository`
  (`insert`, `list_expired(sandbox_id, now)`, `delete`,
  `list_active_pids(sandbox_id)`).
- New module `cash_mode/ai_side_hustle.py` mirroring
  `cash_mode/ai_vice_spending.py`.

An AI on a side hustle is excluded from seating and from the vice
candidate set (a broke AI can't simultaneously be vicing — they partition
naturally by wealth, but the exclusion keeps it explicit).

### Who goes, and when (trigger)

Casino-preferred means the gate is **"cannot afford to play anywhere,"**
not merely "below comfort." A grinder at 79% of starting that can still
buy in keeps grinding at the tables (existing seating logic). The hustle
catches only AIs that would otherwise be stuck forever once passive regen
is off.

- **Eligible:** in the idle pool (not seated), not already on a hustle,
  not on a vice, and `projected_bankroll < min_buy_in` at the cheapest
  stake (i.e. literally can't sit). The existing `forced_leave` idle
  reason and `is_hungry_grinder` are close analogs to reuse.
- **Per-refresh cap:** `HUSTLE_STARTS_PER_REFRESH = 2` (mirrors
  `VICE_STARTS_PER_REFRESH`) to bound LLM narration latency on the refresh
  path. Excess candidates re-roll next refresh.
- Unlike vice, the trigger is **not probabilistic** — every stuck AI
  should eventually hustle (otherwise dead personas pile up). The
  probabilistic roll lives in the *amount*, per the design decision.

### Earning amount (probabilistic roll, bounded range, weighted by starting money, pool-gated)

The inverse of `compute_vice_amount`. Let `projected` be the AI's current
bankroll and `S` its `starting_bankroll`:

```
deficit_ratio  = max(0, (S − projected) / S)              # how far below baseline, 0..1
earn_fraction  = HUSTLE_BASE_FRACTION                      # e.g. 0.05
               + deficit_ratio * HUSTLE_DEFICIT_WEIGHT     # e.g. 0.15 → deeper deficit earns more
raw            = int(S * earn_fraction * jitter)           # jitter ∈ uniform(0.5, 1.5)
amount         = min(raw, S − projected)                   # never overshoot baseline
amount         = min(amount, pool_reserves)                # NEVER draw more than the pool holds
# skip if amount < HUSTLE_MIN_AMOUNT
```

Properties this gives us, mapping to the rate decision:
- **Weighted by starting money** — `raw` scales with `S`, so a high-roller
  earns a bigger absolute lump and rebuilds to high-roller status
  (preserves persona tiers).
- **Probabilistic within a bounded range** — the `jitter ∈ [0.5, 1.5]`
  roll spreads each session across distinct sizes; the fraction band
  bounds it.
- **Deficit-responsive** — the deeper the hole, the bigger the earn, so
  desperate AIs recover faster.
- **Capped at baseline** — one hustle never returns more than `S`
  (mirror of vice's floor-protection, inverted into a ceiling).
- **Pool-gated** — the `min(amount, pool_reserves)` clamp is what makes
  the loop closed. If the pool can't cover the full roll, the AI earns
  what's available; if the pool is empty, the AI earns nothing this
  session and stays broke. This is the closed economy working as intended.

### Duration

Reuse vice's `DURATION_RANGES` buckets (short / medium / long) and
`duration_for_bucket`. The narration LLM picks the bucket to match the
hustle ("a quick consulting gig" = short; "rebuilding a logistics empire"
= long).

### Ledger accounting

- New reason `'side_hustle_earning'` added to `LEDGER_REASONS`
  (creations) and to `BANK_POOL_DRAW_REASONS` (`core/economy/ledger.py:98`).
- New helper `record_side_hustle_earning(repo, *, personality_id, amount,
  context, sandbox_id)` → `central_bank → ai`, reason
  `'side_hustle_earning'`. Mirror of `record_tourist_injection`.
- Commit order mirrors `_commit_vice_start`: load stored → (no regen
  commit needed once passive regen is off) → credit the earned amount →
  `save_ai_bankroll` → `record_side_hustle_earning` → insert the state
  row. The credit is applied at **end** of the hustle (return time), not
  start — see expiry pass below.

### Lobby integration

Mirror the two vice passes in `refresh_unseated_tables`:
- `tick_side_hustle_expirations` (start of refresh): for each row with
  `ends_at <= now`, draw `amount` from the pool, credit the bankroll
  (`record_side_hustle_earning`), delete the row, return a
  `HustleEndResult` so the lobby emits a `hustle_end` ticker row. **The
  pool-gate is re-checked here** against live pool depth (the pool may
  have drained since the hustle started).
- `resolve_ai_side_hustle` (post-loop): build the eligible broke-AI set,
  roll amounts, take top `HUSTLE_STARTS_PER_REFRESH`, call `narrate_fn`
  (sync — duration comes back with the narration), insert the state row,
  emit `hustle_start`. No chips move at start (the AI is "out earning"; the
  payout lands on return).

### Narration + ticker

- New `cash_mode/side_hustle_narration.py` with `narrate_side_hustle(pid,
  amount, snapshot, personality_repo) -> (narration, duration_bucket)`,
  mirroring `vice_narration.py`. LLM prompt: "In character, describe the
  scrappy/grandiose way <persona> goes off to earn ~$<amount> back, and
  how long it takes (short/medium/long)." Templated fallback on failure.
- New activity events `EVENT_HUSTLE_START = 'hustle_start'` /
  `EVENT_HUSTLE_END = 'hustle_end'` in `cash_mode/activity.py`, with
  `format_hustle_start_message` / `format_hustle_end_message`, emitted from
  a `_emit_side_hustle_events` pass mirroring the vice emitter in
  `cash_mode/lobby.py`. Frontend `ActivityTicker.tsx` gets icons for the
  new types.

### Chip economy page (seeing the flow)

The admin chip-economy page (`GET /api/admin/chip-ledger/audit` →
`react/.../components/admin/ChipLedgerPanel.tsx`) is **fully reason-driven
and dynamic** — it never hardcodes reason codes. So most of this surfaces
*for free*:

- The audit response already includes `bank_pool.deposit_reasons` /
  `draw_reasons` (sorted copies of the frozensets) and a `by_reason` /
  `by_reason_window_24h` breakdown computed from live ledger sums.
- The moment `table_rake` joins `BANK_POOL_DEPOSIT_REASONS` and
  `side_hustle_earning` joins `BANK_POOL_DRAW_REASONS`, both appear in the
  reason lists, get folded into pool reserves, and show up in the
  by-reason tables on the next fetch. **No serializer or frontend change
  is required for correctness.**

But "for free" only gets numbers in tables — it does **not** let you *see
the flow*. The page today has no grouped deposit-vs-draw view and no
diagram; reasons are a comma-separated caveat string + flat signed totals.
To actually visualize the closed loop, add (intentional, in-scope):

- A **"Bank Pool Flow"** section in `ChipLedgerPanel.tsx` that groups the
  by-reason entries into **deposits in** (`table_rake`, `vice_spending`,
  `casino_seat_return`, `bank_pool_deposit`) and **draws out**
  (`side_hustle_earning`, `tourist_injection`, `casino_seat_seed`), each
  with its amount + 24h flow, and the resulting reserves as the balance
  between them. A small left-to-right flow/Sankey-style layout
  (deposits → pool → draws) makes the rake→pool→hustle loop legible at a
  glance.
- A **reason → friendly-label map** in the frontend (e.g.
  `side_hustle_earning` → "Side hustle", `table_rake` → "Table rake")
  so the flow view reads in plain language; fall back to the raw code for
  any unmapped reason so new reasons never disappear.

The backend grouping data is already present (`deposit_reasons` /
`draw_reasons` + `by_reason`), so this is a frontend-only enhancement on
top of the automatic behaviour.

### Retiring passive regen

Per the decision, passive idle regen is replaced entirely:
- Default `REGEN_ENABLED = False` in `cash_mode/economy_flags.py`.
  `project_bankroll` already returns stored chips verbatim when the flag is
  off — no code deletion needed, fully reversible.
- With the flag off, every `record_ai_regen` call site no-ops (delta = 0,
  `projected == stored`). The `ai_regen` reason stays in the vocabulary
  for historical rows.
- The side hustle becomes the sole faucet. **Audit follow-up:** the audit
  subtracts `uncommitted_ai_regen` (projected − stored) to avoid false
  drift; with regen off that term is always 0, which is fine, but confirm
  the audit and its tests don't assume a non-zero projection.
- Note: `is_hungry_grinder` / `list_hungry_grinders` call `project_bankroll`
  — with regen off these read stored chips, which is correct (they should
  reflect real wealth, not a projected faucet).

### Psychology: energy while hustling

How the psychology system works (the part that matters here): each AI has
three **dynamic axes** — `confidence`, `composure`, `energy` — each a float
in `[0, 1]`, stored in `emotional_state_json` under `axes`. They drift
around per-personality **baselines** (derived from immutable **anchors**).
During play a per-hand `recover()` nudges them back toward baseline and
game events knock them around. `energy` is roughly stamina/freshness — low
energy reads as tired.

**The load-bearing fact for off-grid zones:** while an AI is off the
tables (idle, vice, *or* hustle), **no hands are played, so `recover()`
never fires.** The axes are effectively *frozen* unless something
explicitly moves them. Vice is currently the only thing that explicitly
moves them — on return it applies a one-shot pull toward baseline
(`compute_recovered_axes`, indulgence-heals).

So energy in the side hustle has three options, and they're a tunable
decision rather than a fixed part of the design (a `HUSTLE_ENERGY_MODE`
flag, default chosen below):

- **Frozen / unaffected (default):** do nothing. Energy stays at whatever
  it was when they left the table. Simplest, and "static" reduces to this
  in practice since nothing else touches it off-grid.
- **Drain:** the grind is tiring → energy drops (a one-shot hit on return,
  or scaled by duration bucket). This has a *downstream economic effect*
  worth flagging: `pressure = 1 − min(confidence, composure, energy)`, and
  pressure amplifies vice probability. A hustle that drains energy makes
  the AI more likely to vice once it's rich again — a thematically nice
  "grind hard, then blow it" cycle, but it couples the two systems, so
  tune deliberately.
- **Recover:** treat the hustle like rest. Probably the *wrong* fit —
  resting belongs to the idle/break zone, not the work zone.

A coherent fuller model (deferred, but this is where it points): the three
off-grid zones get distinct psychological character — **idle/break = rest
→ energy recovers**, **side hustle = work → energy drains or flat**,
**vice = indulge → all axes pull to baseline**. v1 ships `HUSTLE_ENERGY_MODE
= frozen` and leaves the idle-recover idea unbuilt; the flag makes drain a
one-line experiment once the psychology behaviour is better understood.

---

## Closed-loop invariants & risks

- **Conservation (`drift == 0`)** is preserved: every chip move writes a
  ledger row. Rake redirect and the new draw reason only change the
  *pool-depth view*, not the audit's outstanding math.
- **Pool can't go negative in practice** because every draw is clamped to
  `pool_reserves`. (The ledger helper still writes unconditionally — the
  *caller* enforces the clamp, same contract as `record_tourist_injection`.)
- **Deadlock risk:** if the pool empties while many AIs are stuck broke,
  they hustle for 0 and stay broke; with no one playing, no rake refills
  the pool. Mitigations: (a) the bank pool is seeded at sandbox/sim start
  (`seed_bank_pool`); (b) fish-vs-grinder casino hands generate rake that
  refills the pool even when "real" personas are broke; (c) a small
  `HUSTLE_FLOOR_WAGE` guaranteed minimum that may dip the central bank
  directly — a deliberate, tunable escape valve (mildly breaks closed-ness;
  off by default, documented).

---

## Tuning constants (starting points — expect to retune)

In `cash_mode/ai_side_hustle.py`:

| Constant | Start | Meaning |
|---|---|---|
| `HUSTLE_BASE_FRACTION` | 0.05 | base earn as fraction of starting bankroll |
| `HUSTLE_DEFICIT_WEIGHT` | 0.15 | extra fraction per unit deficit_ratio |
| `AMOUNT_JITTER_LOW/HIGH` | 0.5 / 1.5 | the probabilistic roll band |
| `HUSTLE_MIN_AMOUNT` | 50 | skip sub-threshold earns |
| `HUSTLE_STARTS_PER_REFRESH` | 2 | LLM-latency / ticker-noise bound |
| `HUSTLE_FLOOR_WAGE` | 0 (off) | deadlock escape valve (dips central bank) |

In `cash_mode/economy_flags.py`:

| Flag | Start | Meaning |
|---|---|---|
| `REGEN_ENABLED` | **False** | retire passive faucet |
| `SIDE_HUSTLE_ENABLED` | True | master toggle for the new system |

---

## Status (2026-05-24)

**Implemented — all 9 phases shipped on `career-mode-v0_1` (uncommitted).** Key
files: `cash_mode/ai_side_hustle.py` (mechanic), `cash_mode/side_hustle_narration.py`
(LLM narrator), `poker/repositories/side_hustle_state_repository.py` +
schema v114 (`ai_side_hustle_state`), `core/economy/ledger.py`
(`table_rake`→pool deposit, `side_hustle_earning` draw +
`record_side_hustle_earning`), lobby wiring in `cash_mode/lobby.py`
(expiry + start passes), `cash_mode/economy_flags.py`
(`REGEN_ENABLED=False`, `SIDE_HUSTLE_ENABLED=True`), sim wiring in
`cash_mode/sim_runner.py`, and the chip-economy "Bank Pool Flow" view in
`ChipLedgerPanel.tsx`. Closed-loop validated: `tests/test_cash_mode/test_side_hustle_closed_loop.py`
confirms broke AIs hustle → draw from pool → recover, pool never negative,
`drift == 0`. `HUSTLE_ENERGY_MODE='frozen'` (drain implemented behind the
flag, off by default).

## Implementation plan (phased)

1. **Rake → pool (Part A).** Add `table_rake` to
   `BANK_POOL_DEPOSIT_REASONS`; update affected pool/audit tests. Tiny,
   low-risk, independently shippable.
2. **Ledger surface.** Add `side_hustle_earning` to `LEDGER_REASONS` +
   `BANK_POOL_DRAW_REASONS`; add `record_side_hustle_earning`. Unit tests.
3. **State + repo.** Schema migration for `ai_side_hustle_state`;
   `SideHustleStateRepository`. Repo tests.
4. **Core mechanic.** `cash_mode/ai_side_hustle.py`: pure formulas
   (`compute_deficit_ratio`, `compute_hustle_amount`), `resolve_ai_side_hustle`,
   `tick_side_hustle_expirations`. Unit tests with a deterministic RNG +
   fake pool depth.
5. **Retire passive regen.** Flip `REGEN_ENABLED` default to False; audit
   confirmation.
6. **Lobby wiring + events.** Wire both passes into `refresh_unseated_tables`;
   add ticker events + formatters + frontend icons; templated narrator.
7. **LLM narration.** `side_hustle_narration.py` + plug into the dispatcher.
8. **Chip economy page.** Verify the new reasons surface automatically
   (no change needed for correctness), then add the "Bank Pool Flow"
   grouped deposits→pool→draws view + reason→label map to
   `ChipLedgerPanel.tsx` so the loop is visible.
9. **Sim validation.** Drive the side hustle from `full_sim` / `sim_runner`;
   run a long closed-loop sim and confirm `drift == 0`, pool stays
   non-negative, and broke AIs recover only as fast as rake+vice feed the
   pool.

## Open questions

- **Floor wage default** — ship with `HUSTLE_FLOOR_WAGE = 0` (pure closed
  loop, accept deadlock risk and rely on pool seeding + fish rake), or a
  small non-zero value for safety? Recommend 0 first, watch sims.
- **Voluntary hustle below comfort** — v1 only hustles AIs who *can't* sit.
  Should an AI stuck far below its comfort stake (but able to grind low
  stakes) ever *choose* to hustle back up faster? Deferred to v2 tuning.
