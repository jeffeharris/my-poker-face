---
purpose: How the cash economy regulates wealth distribution — the vice / side-hustle / grinder-hunger / rake levers and the own_start vs field_liquid reference model.
type: reference
created: 2026-06-03
last_updated: 2026-06-04
---

# Cash Mode Wealth Levers

`CASH_MODE_ECONOMY.md` is the **accounting** layer (where chips come from,
where they go, the conservation invariant, the audit). This doc is the
**policy** layer: the four levers that decide *who* gains and loses chips
over time, and the unified reference model (`LEVER_REFERENCE_MODE`) that
controls how each lever measures "rich" and "poor."

All of this is **default-off relative to the new behaviour**:
`LEVER_REFERENCE_MODE` defaults to `own_start`, which reproduces the
historical per-AI behaviour. Flip to `field_liquid` to make every lever
field-relative.

## The four levers

Every lever fires inside `cash_mode/lobby.py:refresh_unseated_tables`,
once per world tick, at hand boundaries. The first three act on
**idle (unseated) AIs only**; rake acts on pot winners.

| Lever | Role | Trigger | Chip flow | Code |
|---|---|---|---|---|
| **Vice** | wealth **tax** | a rich AI (by the reference) rolls a vice | AI bankroll → bank pool (`vice_spending`, real) / (`bank_pool_deposit`, fake) | `cash_mode/ai_vice_spending.py` (real), `closed_economy.resolve_fake_vice_deposits` (sim stub) |
| **Side-hustle** | bottom **support** | a poor AI (by the reference) goes off-grid | bank pool → AI bankroll (`side_hustle_earning`) | `cash_mode/ai_side_hustle.py` |
| **Grinder-hunger** | **action** demand | a poor casino-tier AI wants to play | trigger only; drives casino/fish spawns (`casino_seat_seed`) | `closed_economy.is_hungry_grinder` / `list_hungry_grinders`, `casino_provisioning.py` |
| **Rake** | **recycle** | every pot at a rake-eligible tier | winner's seat → bank pool (`table_rake`) | `economy_flags.compute_rake`, `full_sim._apply_rake_to_winner` |

The **bank pool** is the recyclable reservoir: vice + rake + casino
returns fill it; side-hustle + casino seeding drain it. It is virtual
(computed from ledger reasons, no row). `table_rake` is a
`BANK_POOL_DEPOSIT_REASON` — **rake recycles, it is not destroyed.**

The three forces compose into a self-correcting band: table play
concentrates wealth (bigger stakes → bigger absolute swings), while vice
(progressive tax) and side-hustle (bottom support) pull back toward the
middle.

## The Director thermostat (reserve-band gating)

A second flag-gated layer, **orthogonal to `LEVER_REFERENCE_MODE`** and
likewise **default-off**, gates vice *intensity* and the rake *schedule* on
the **bank's reserve depth** instead of letting them run at a fixed rate. The
"Director" is not an entity — it is a pure read-model over the ledger
(`core/economy/economy_signal.py:signal`) that computes one number per
decision, `r = reserves / holdings`, and drives every lever off it. Built for
the fresh-prod-sandbox starting conditions; **full design + sim findings:
[`docs/plans/PROD_STARTING_CONDITIONS.md`](../plans/PROD_STARTING_CONDITIONS.md)**
(orientation: `…_HANDOFF.md`).

The **canonical reserve ladder** (one source of truth, all levers reference
it — `economy_signal.py:98–106`):

| Constant | `r` | Band |
|---|---|---|
| `RESERVE_CRITICAL` | 0.03 | rake widest + top rate; vice full |
| `RESERVE_HEALTHY` | 0.06 | healthy floor; also the tournament drain floor |
| `RESERVE_TRIGGER` | 0.12 | offer a Main Event, drain back to `HEALTHY` |
| `RESERVE_VICE_CEILING` | 0.18 | vice fully off (hot bank, braked) |

The flags (all `_env_flag(..., False)` in `economy_flags.py`):

| Flag | line | Effect |
|---|---|---|
| `GENESIS_RESERVE_ENABLED` | 172 | Seed the bank pool to 5% of holdings once at fresh-sandbox birth (`closed_economy.ensure_genesis_reserve_seeded`), so a prod sandbox boots lived-in instead of inert (empty pool → no casinos/tournaments). |
| `VICE_RESERVE_GATED` | 156 | Scale the whole vice pass by the reserve deficit (`ai_vice_spending.reserve_vice_multiplier:661`): full at/below `HEALTHY`, tapering to off at `VICE_CEILING` — so vice is ~half-on *at* the trigger (pushes reserves across it) and brakes above it. |
| `RAKE_RESERVE_GATED` | 210 | Graduate BOTH the raked stake tiers and the rate by band (`economy_signal.cash_rake_schedule:353`): `{1000}`@2% / `{1000,200}`@3% / `{1000,200,50}`@4%. The $1000 tier is always on (structural rake never switched off). |
| `DIRECTOR_INEQUALITY_RAKE` | 220 | On a **flat** field (`field_inequality` `p90/median ≤ 2.5`, throttled), evaluate the rake one band lower so the even-skim leads the refill vice (no rich target) can't. |
| `DIRECTOR_POLICY_HOLD` | 236 | **Hold** the resolved rake schedule for a `POLICY_WINDOW_SECONDS` (300s) window (`cash_mode/director_policy.py`, recomputed in the lobby refresh) so the per-hand rake reads a cached `(stakes, rate)` instead of re-running the `signal()` ledger scan every hand. `resolve_rake_params` exposes a `_fresh=` bypass: the refresh recomputes `_fresh=True`; per-hand reads the held value (cold cache → live compute). Implies `RAKE_RESERVE_GATED`. |
| `CASINO_RELATIVE_THRESHOLDS` | 245 | Casino spawn/close/whale gates scale as fractions of holdings instead of absolute chip counts. |
| `CASINO_RESEED_ON_SPENT` | 255 | Lean casino fish: 1 fish/casino (2 at $2), leaner prefund — turns the lumpy casino pool→field drain into a steady trickle. |

The **tournament** is the big redistribution drain (gated separately by the
pre-existing `TOURNAMENT_CIRCUIT_ENABLED`): `should_offer_event` fires a Main
Event at `RESERVE_TRIGGER`; `tournament_funding` sizes the overlay to drain
reserves back to `RESERVE_HEALTHY` (keeping half), redistributing to the
field as prizes. Net effect across the layer is a **sawtooth**: reserves
climb `HEALTHY → TRIGGER` on the vice+rake faucet, fire one event, drain to
the floor, climb again. Sim-confirmed end-to-end (one fire, −163,607 overlay,
re-climb) on the 76-cast — see the plan doc §1.6a.

## Reference model: `LEVER_REFERENCE_MODE`

`cash_mode/economy_flags.py` exposes a single env flag governing all
three idle levers:

- **`own_start`** (default) — each lever keys off the AI's *own*
  `starting_bankroll`. Vice taxes anyone above ~1.2× its own start;
  side-hustle/grinder fire below their own start. This is *anti-mobility*:
  it taxes you for outgrowing your origin and bails the fallen-rich back
  toward their (high) baseline. (Real vice is the exception even here — it
  already keys off the cast-median bankroll.)
- **`field_liquid`** — all three key off the **field's liquid net worth
  distribution** via a single per-tick snapshot. Rich/poor is judged
  relative to the field, not your past self.

`lever_field_mode()` reads the env at call time so a sim/experiment can
flip it per-run regardless of import order.

### Liquid net worth + the `FieldWealthSnapshot`

`cash_mode/field_wealth.py` builds one immutable `FieldWealthSnapshot`
per tick: `{pid: liquid}` for every non-fish AI, where

```
liquid net worth = projected off-table bankroll + chips in table seats
```

It exposes `median()`, `percentile(q)`, `concentration(pid)` (= liquid /
field-median), and `pct_rank(pid)`. The snapshot is **read-only** — it
moves no chips, writes no ledger rows, so `field_liquid` is
**conservation-neutral by construction** (it only changes *where a lever
reads its reference point*).

### Two deliberate exclusions

1. **Liquid-only (receivables/outstanding excluded).** Net worth could
   include staking receivables (owed to you) minus outstanding (you owe).
   We exclude both. Rationale: the levers can only *act* on liquid chips
   (vice drains bankroll; side-hustle pays into bankroll), so triggering
   on illiquid wealth a lever can't touch is incoherent. Excluding stakes
   also keeps the measure **fully per-sandbox** (the `stakes` table is
   global). A loan-shark's owed chips get taxed automatically once they
   repay into bankroll — the "dodge" is temporary and self-correcting.
2. **Idle-only for vice/side-hustle.** Both fire only on *unseated* AIs.
   A leaving AI cashes out its seat *before* the lever sizes, so at
   trigger time all its wealth is in bankroll (seat = 0). This is why the
   liquid measure has no mid-hand-seat collection problem: seat stacks
   matter only for the **field reference** (the median across all AIs,
   including seated ones, so they don't depress it).

## Per-lever `field_liquid` behaviour + knobs

Tunables live in `economy_flags.py` (used only in `field_liquid`):

| Knob | Default | Effect |
|---|---|---|
| `FIELD_CONCENTRATION_FLOOR` | 2.5 | vice fires above N× the field median liquid |
| `MIN_FIELD_MEDIAN_FOR_VICE` | 5000 | suppress vice when the whole field is broke |
| `FIELD_HUSTLE_ELIGIBLE_PERCENTILE` | 0.10 | bottom X% of field liquid → hustle candidate |
| `FIELD_HUSTLE_TARGET_PERCENTILE` | 0.25 | hustle tops up toward this field percentile (`compute_field_hustle_amount`) |
| `FIELD_GRINDER_HUNGER_PERCENTILE` | 0.35 | below this field percentile → hungry grinder |

- **Vice:** `concentration(pid) > FIELD_CONCENTRATION_FLOOR`. Because
  seat stacks now count toward the median, seated AIs no longer depress
  it (and `MIN_FIELD_MEDIAN_FOR_VICE` stops misfiring). The drain is
  still capped to off-table bankroll.
- **Side-hustle:** eligible in the bottom decile (`pct_rank <
  FIELD_HUSTLE_ELIGIBLE_PERCENTILE`), tops up toward the target
  percentile instead of toward its own (possibly high) baseline — so a
  low-baseline persona that's field-poor still earns, and a fallen-rich
  one is **not** auto-bailed.
- **Grinder-hunger:** `pct_rank < FIELD_GRINDER_HUNGER_PERCENTILE`.

## Tuning findings (sim-validated)

From an honest-vice `own_start` vs `field_liquid` A/B and an 8-config
knob sweep (`scripts/sim_experiments/knob_sweep.py`, field_liquid + real
vice):

- **`field_liquid` compresses inequality** without breaking the casino /
  liveness loop or conservation (casinos still spawn, drift ≈ 0 across
  all configs).
- **Bottom support tightens** vs `own_start` (the fallen-rich stop being
  bailed) — intended, but if it's too aggressive, raise
  `FIELD_HUSTLE_TARGET_PERCENTILE` (0.25 → 0.35 roughly doubled hustle
  payout at no liveness cost).
- **Guardrail:** do **not** drop `FIELD_GRINDER_HUNGER_PERCENTILE` below
  0.35 — tightening casino access starves the bottom's wealth-building
  and was the worst config for equality.
- Gini differences between knob settings were within ~2-seed noise;
  firmer tuning wants ~5 seeds per config.

## Real vs fake vice, and the sim

- Production runs **real vice** (`VICE_MODE='real'`): cast-median /
  field concentration, LLM-narrated, off-grid + psych recovery.
- **Fake vice** is the LLM-free stub used by the headless sim; it shares
  the same `LEVER_REFERENCE_MODE` reference (own_start = own start;
  field_liquid = field median liquid).
- The economy sim (`cash_mode/sim_runner.py`) defaults to **real vice**
  via the deterministic *templated* narrator (no LLM), so its wealth
  dynamics match production. Set `vice_mode='fake'` / `'off'` to vary.

> Measurement caveat: `TickMetrics.per_pid_chips` (and gini/percentiles
> derived from it) are **off-table bankroll only**. For mobility / climb
> analysis use `per_pid_networth` (bankroll + seat) — a seated climber's
> stack is on the felt and bankroll-only mis-reads them as poor.

## Code map

| Concern | File |
|---|---|
| Flags + knobs + `lever_field_mode()` | `cash_mode/economy_flags.py` |
| Director read-model + reserve ladder + rake/overlay policy | `core/economy/economy_signal.py` |
| Director rake policy hold (cached schedule) | `cash_mode/director_policy.py` |
| Field inequality signal (instrument choice) | `cash_mode/field_inequality.py` |
| Field snapshot (single source of truth) | `cash_mode/field_wealth.py` |
| Real vice | `cash_mode/ai_vice_spending.py` |
| Fake vice (sim stub) | `cash_mode/closed_economy.py` |
| Side-hustle | `cash_mode/ai_side_hustle.py` |
| Grinder-hunger / casino demand | `cash_mode/closed_economy.py`, `cash_mode/casino_provisioning.py` |
| Rake | `cash_mode/economy_flags.py`, `cash_mode/full_sim.py` |
| Orchestration (per-tick) | `cash_mode/lobby.py:refresh_unseated_tables` |
| Headless economy sim | `cash_mode/sim_runner.py` |
| Experiment drivers | `scripts/sim_experiments/{knob_sweep,circuit_tax_ab,…}.py` |

See `CASH_MODE_ECONOMY.md` for the chip-flow / conservation / audit
layer this sits on top of.
