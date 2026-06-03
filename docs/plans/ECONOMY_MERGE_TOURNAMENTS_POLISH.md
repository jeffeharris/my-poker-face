---
purpose: Plan for merging the tournaments-branch economy (the economy-signal "chairman") and the polish-branch economy (field-relative wealth levers) into one coherent system — mechanical merge + the semantic integration that matters more.
type: guide
created: 2026-06-03
last_updated: 2026-06-03
---

# Economy Merge Plan — `tournaments` ↔ `polish`

## TL;DR

The two branches independently built the **two halves of one economy** and
converged on the same philosophy (field-relative, signal-driven):

- **`tournaments` = the supply axis** — a reserve thermostat (the
  "economy-signal chairman") that holds the bank pool at a setpoint by
  raking (refill) and running tournament overlays (distribute).
- **`polish` = the distribution axis** — `LEVER_REFERENCE_MODE=field_liquid`,
  which makes vice / side-hustle / grinder-hunger key off the **field's**
  liquid net worth instead of each AI's own starting bankroll.

**The textual merge is small** (3 files; only `lobby.py` needs hand-work).
**The real work is the semantic integration** — wiring the two halves
together, which is an *opportunity*, not a conflict. Everything stays
default-off, so the merge changes no production behaviour by itself.

Merge-base: `f3a1cb8f` (2026-06-01).

---

## 1. What each branch built

### `tournaments` — supply / reserve thermostat
- **`core/economy/economy_signal.py`** — the "chairman." One pure
  `EconomyState{reserves, holdings, ratio = reserves/holdings, regime}`
  with `FLUSH (≥0.08)` / `NEUTRAL` / `EMPTY (≤0.02)` regimes, sim-tuned
  (`EXP_006_BANK_RESERVE_THERMOSTAT.md`). Two pure lever policies read it:
  - `tournament_funding` → `FundingPlan` (drain-to-setpoint overlay when
    FLUSH; refill rake when EMPTY) — **wired** (P2/P3).
  - `cash_rake_schedule(state)` → `RakeSchedule` (FLUSH/NEUTRAL `{1000}`@2%;
    EMPTY `{1000,200}`@3%) — **built + tested, NOT wired into cash mode.**
  - `should_offer_event` — FLUSH = "time to redistribute" (tournament cadence).
- `flask_app/services/tournament_economy_service.py` (escrow, overlay, rake),
  `tournament/economy.py`, new ledger tournament reasons, `SCHEMA_VERSION=147`,
  a **Renown-v2 field-relative prestige scorer**, and the
  `TOURNAMENT_CIRCUIT_ENABLED` world-tick hook.

### `polish` — distribution / wealth levers
- **`LEVER_REFERENCE_MODE`** (`own_start` default | `field_liquid`) +
  **`cash_mode/field_wealth.py`** (`FieldWealthSnapshot`): vice / side-hustle /
  grinder-hunger key off field liquid net worth (bankroll + seat).
- Made the headless sim **fully LLM-free** + added a net-worth sim metric.
- Corrected the stale rake docstrings; documented the lever layer in
  `docs/technical/CASH_MODE_WEALTH_LEVERS.md`.
- Commits: `54765436`, `a86f706b`, `41f2eeaa`, `642a79c9`, `84af8f8e`,
  `c557ea6f`, `6cb4c04d`.

---

## 2. Textual conflicts (the mechanical merge)

Only **three files** changed on both branches since `f3a1cb8f`:

| File | Severity | Resolution |
|---|---|---|
| `cash_mode/sim_runner.py` | trivial | tournaments +4 (`prestige_snapshots_repo` wiring) vs polish net-worth/vice/narration — different regions; keep both |
| `cash_mode/economy_flags.py` | trivial | tournaments *appends* `TOURNAMENT_CIRCUIT_ENABLED` + Renown-v2 flags; polish edits the rake docstring + adds the `LEVER_REFERENCE_MODE` block — keep both |
| `cash_mode/lobby.py` | real, **bounded** | three small zones (below) |

**`lobby.py` — the only file needing hand-resolution.** Three zones:

1. **Import block (top).** Both add imports → union them.
2. **`refresh_unseated_tables` signature.** Both insert new kwargs — keep
   **all four**: `prestige_snapshots_repo`, `tournament_repo` (tournaments) and
   `vice_use_llm_narration`, `hustle_use_llm_narration` (polish).
3. **`_process_global_greedy_fills`.** tournaments made *logical* edits (the
   prestige "marquee" pull toward high-renown tables + the tournament-
   participant seating exclusion); **polish's hunks here are pure
   `ruff-format` reflow** (e.g. collapsing `_can_afford_target(...)` to one
   line), not logic. → **Take tournaments' logic**, then re-run `ruff format`.

> **Gotcha:** polish's pre-commit `ruff-format` reflowed parts of `lobby.py`
> beyond the logical edits, *manufacturing* extra textual conflicts. Treat
> every `lobby.py` conflict as "polish-reformatted vs tournaments-edited-logic"
> and keep the logic. **Run `ruff format cash_mode/lobby.py` after resolving.**

polish's actual economy work (the `FieldWealthSnapshot` build + the
vice/side-hustle/casino passes, ~lines 2189–2433) is in a region tournaments
does **not** touch — clean.

---

## 3. No conflict (changed on one side only)

- **Schema:** polish added **no** migration (still `136`); tournaments went
  `137→147`. They stack — just take tournaments' `schema_manager.py`. Verify
  `SCHEMA_VERSION == 147` post-merge. (The old "v136 collides" note is stale.)
- **`closed_economy.py`** (polish's field-snapshot refactor), **`ledger.py`**
  (tournaments' new reasons), **`movement.py`** (tournaments'
  `resolve_dominant_signal`) — each touched by one branch only. Clean.

---

## 4. Semantic integration (the real work) — three decisions

These do **not** produce git conflicts (the code lives in different files),
but merging the text without these decisions ships two economies that
silently disagree.

### D1 — Rake authority: dynamic supersedes static  *(do at merge / first follow-up)*
polish ships a **static** rake (`economy_flags.RAKE_STAKE_BIG_BLINDS={1000}`,
`RAKE_RATE=0.02`); tournaments ships the **dynamic**
`economy_signal.cash_rake_schedule(state)`. They don't textually clash, but
only one can be the authority.

- **Decision:** the dynamic schedule is the authority; the static constants
  become its FLUSH/NEUTRAL fallback (they already match: `{1000}`@2%).
- **Work:** in `compute_rake` / `full_sim._apply_rake_to_winner`, source the
  tiers + rate from `cash_rake_schedule(signal(ledger_repo, sandbox_id))`,
  computing **one `EconomyState` per tick under `get_sandbox_lock`** and
  sharing it with the tournament lever (the chairman's anti-oscillation rule:
  *one snapshot, both levers*).
- **Gate (handoff §6 / EXP_006):** cash rake and tournament overlay share the
  pool and **must be sim-modeled together** before either flips on. The cash
  rake is per-pot (≈ the per-tick cadence EXP_006 natively tuned), so its
  constants transfer more directly than the overlay's did.

### D2 — Vice ↔ chairman: make vice reserve-aware  *(defer; design now)*
polish's vice (under `field_liquid`) is **reserve-blind** — it taxes the
field-rich into the pool regardless of regime, so it can refill a pool the
chairman is trying to *drain* via an overlay.

- **Option A (first merge):** leave vice as pure distribution. Bounded, fine.
- **Option B (eventual):** `field_liquid` decides *who* vice taxes; the
  chairman's `regime` scales *how much* (ease vice off when FLUSH, on when
  EMPTY). This is the chairman's own "coordinated actuators" thesis extended
  to a third actuator. Recommended end state.

### D3 — A shared field-snapshot primitive  *(defer; note the convergence)*
Three field-relative reads now exist: `economy_signal.EconomyState`
(reserves/holdings), `field_wealth.FieldWealthSnapshot` (per-AI liquid), and
the Renown-v2 field scorer (prestige). They're cousins computed per tick.
Keep them separate for the merge; flag the opportunity to unify the per-tick
"field read" behind one pass later. **Do not block the merge on this.**

---

## 5. Flag state after a clean merge (all default-off → prod unchanged)

| Flag | Value | Effect |
|---|---|---|
| `LEVER_REFERENCE_MODE` | `own_start` | levers stay per-AI (legacy) |
| `TOURNAMENT_CIRCUIT_ENABLED` | `False` | no autonomous tournament world-tick |
| dynamic cash rake (D1) | unwired/off | static `{1000}`@2% until D1 lands |
| Renown-v2 | off | v1 prestige stays the live path |
| `VICE_MODE` | `real` | unchanged |

So the merge itself flips nothing live; each lever is opt-in behind its flag.

---

## 6. Validation before flipping anything on

- **Together, not separately** (§6): the honest LLM-free economy sim with
  cash rake + tournament overlay both active, checking the bank pool holds
  the `~0.08` band and `drift ≈ 0`.
- Run the **`test_cash_mode`** + **`test_tournament`** suites green.
- **Known caveat:** 27 pre-existing `test_movement` / `test_movement_prestige`
  / `test_take_stake` failures predate **both** branches (the `f849413c`
  rebuy-retention churn) — track them, but they don't block this merge.

---

## 7. Recommended sequence

1. Pick the integration branch (likely `development`). If tournaments is
   further along, land it first; then merge polish on top (or merge both into
   a fresh integration branch).
2. Resolve the 3 `lobby.py` zones (§2), take tournaments' `schema_manager.py`,
   `ruff format` + `ruff check`.
3. Get `test_cash_mode` + `test_tournament` green (modulo the 27 pre-existing).
4. **D1** (wire the dynamic rake) as a sim-validated follow-up PR (§4, §6).
5. Defer **D2** / **D3**; capture them as follow-up issues.

---

## References

- **polish:** `docs/technical/CASH_MODE_WEALTH_LEVERS.md`,
  `docs/technical/CASH_MODE_ECONOMY.md`, `cash_mode/field_wealth.py`,
  `cash_mode/economy_flags.py`
- **tournaments:** `core/economy/economy_signal.py`,
  `docs/plans/TOURNAMENT_ECONOMY_ON_STATE_MODEL.md`,
  `docs/plans/MULTI_TABLE_TOURNAMENT_P2_ECONOMY.md`,
  `docs/experiments/EXP_006_BANK_RESERVE_THERMOSTAT.md`,
  `docs/plans/P3_REMAINING_HANDOFF.md` (§6)
