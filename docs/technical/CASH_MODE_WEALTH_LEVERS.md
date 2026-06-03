---
purpose: How the cash economy regulates wealth distribution — the vice / side-hustle / grinder-hunger / rake levers and the own_start vs field_liquid reference model.
type: reference
created: 2026-06-03
last_updated: 2026-06-03
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
