"""Whale-gate cadence sim — legacy absolute watermarks vs. the chairman ratio gate.

Question (the one the design change hinges on): does routing whale spawn/recall
through the chairman (`economy_signal.can_fund_whale` / `should_recall_whale`,
keyed on the reserve RATIO) keep a sane whale cadence, and does it fix the
scale-invariance bug the frozen absolute `WHALE_POOL_THRESHOLDS` / `WHALE_POOL_FLOORS`
have as holdings grow?

This is NOT a full hand-play economy. It drives a realistic reserve trajectory —
a rake faucet (inflow) plus a tournament drain-to-floor (the real sawtooth) — and
runs the ACTUAL decision functions for both gates over it, modelling the whale as a
one-at-a-time state machine (spawn draws the prefund from reserves; the whale is
farmed and busts after a while, or is recalled early when the gate says so). So it
isolates exactly what changed (the gate decision) from everything else.

Two scenarios:
  * STABLE holdings (~2.64M, where the absolute watermarks were tuned) — the gate
    should roughly REPRODUCE legacy here (a calibration sanity check).
  * GROWING holdings (roster/economy inflates) — where the absolute watermarks
    break: 500k stops meaning "flush" once holdings are 10M, so legacy over-spawns
    and under-recalls; the ratio gate stays proportionate.

Run (inside the backend container):
    docker compose exec -T backend python scripts/sim_experiments/whale_gate_sim.py
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from cash_mode.casino_provisioning import (
    WHALE_POOL_FLOORS,
    WHALE_POOL_THRESHOLDS,
    WHALE_PREFUND_MAX_MULT,
    WHALE_PREFUND_MIN_MULT,
)
from cash_mode.stakes_ladder import table_buy_in_window
from core.economy import economy_signal as chair
from core.economy.economy_signal import EconomyState

WHALE_STAKES = ['$200', '$50']  # biggest first (matches resolve_whale_provisioning)
MAX_BUY_IN = {s: table_buy_in_window(s)[2] for s in WHALE_STAKES}
RESIDUAL_FRAC = 0.4  # fraction of the prefund still on the felt when recalled early
LIFETIME = (200, 600)  # ticks a whale survives being farmed before it busts


def _state(reserves: int, holdings: int) -> EconomyState:
    ratio = reserves / max(1, holdings)
    return EconomyState(
        reserves=reserves, holdings=holdings, ratio=ratio, regime=chair._classify(ratio)
    )


def _prefund(stake: str, rng: random.Random) -> int:
    return int(MAX_BUY_IN[stake] * rng.uniform(WHALE_PREFUND_MIN_MULT, WHALE_PREFUND_MAX_MULT))


# --- the two gates, as the real code decides them -------------------------


def legacy_spawn(reserves: int, holdings: int, stake: str) -> bool:
    return reserves >= WHALE_POOL_THRESHOLDS[stake]


def legacy_recall(reserves: int, holdings: int, stake: str) -> bool:
    return reserves < WHALE_POOL_FLOORS[stake]


def gated_spawn(reserves: int, holdings: int, stake: str) -> bool:
    cost = int(MAX_BUY_IN[stake] * WHALE_PREFUND_MAX_MULT)  # worst-case draw
    return chair.can_fund_whale(_state(reserves, holdings), prefund_cost=cost)


def gated_recall(reserves: int, holdings: int, stake: str) -> bool:
    return chair.should_recall_whale(_state(reserves, holdings))


@dataclass
class Result:
    spawns: int = 0
    recalls: int = 0
    busts: int = 0
    ticks_live: int = 0
    spawn_stakes: list = field(default_factory=list)
    ratios: list = field(default_factory=list)
    spawn_ratios: list = field(default_factory=list)  # reserve ratio at each spawn


def run(*, spawn_fn, recall_fn, ticks: int, h0: int, h1: int, seed: int) -> Result:
    """Drive the reserve sawtooth and the whale state machine under one gate."""
    rng = random.Random(seed)
    res = Result()
    reserves = int(h0 * 0.05)  # start mid-band
    # Whale state: None or (stake, ticks_lived, prefund_drawn).
    whale = None
    for t in range(ticks):
        holdings = int(h0 + (h1 - h0) * (t / max(1, ticks - 1)))  # linear growth
        # Faucet: rake+vice inflow, ~0.12% of holdings/tick (scales with economy).
        reserves += int(holdings * 0.0012)
        # Tournament drain-to-floor: at the trigger, hand reserves back to the field.
        if reserves >= chair.RESERVE_TRIGGER * holdings:
            reserves = int(chair.RESERVE_HEALTHY * holdings)

        if whale is None:
            for stake in WHALE_STAKES:  # biggest the bank can fund
                if spawn_fn(reserves, holdings, stake):
                    pre = _prefund(stake, rng)
                    if pre > reserves:
                        continue
                    reserves -= pre
                    whale = (stake, 0, pre)
                    res.spawns += 1
                    res.spawn_stakes.append(stake)
                    res.spawn_ratios.append(reserves / max(1, holdings))
                    break
        else:
            stake, lived, pre = whale
            if recall_fn(reserves, holdings, stake):
                reserves += int(pre * RESIDUAL_FRAC)  # residual back to pool
                whale = None
                res.recalls += 1
            elif lived >= rng.randint(*LIFETIME):
                whale = None  # farmed out / busts — nothing returns to the pool
                res.busts += 1
            else:
                whale = (stake, lived + 1, pre)
                res.ticks_live += 1

        res.ratios.append(reserves / max(1, holdings))
    return res


def _fmt(r: Result, ticks: int) -> str:
    from statistics import mean

    big = sum(1 for s in r.spawn_stakes if s == '$200')
    live_pct = 100 * r.ticks_live / ticks
    sr = r.spawn_ratios
    return (
        f"spawns={r.spawns:3d} (${'200' if big else '?'}×{big}, $50×{r.spawns - big})  "
        f"recalls={r.recalls:3d}  busts={r.busts:3d}  "
        f"live={live_pct:4.1f}%  "
        f"reserve-ratio min={min(r.ratios):.3f} mean={mean(r.ratios):.3f}  "
        f"spawn@ratio mean={mean(sr) if sr else 0:.3f}"
    )


def main() -> int:
    TICKS = 8000
    scenarios = [
        ("STABLE  holdings 2.64M→2.64M", 2_640_000, 2_640_000),
        ("GROWING holdings 2.64M→10M  ", 2_640_000, 10_000_000),
    ]
    for label, h0, h1 in scenarios:
        print(f"\n=== {label}  ({TICKS} ticks) ===")
        for name, spawn_fn, recall_fn in [
            ("legacy (absolute)", legacy_spawn, legacy_recall),
            ("gated  (ratio)   ", gated_spawn, gated_recall),
        ]:
            r = run(spawn_fn=spawn_fn, recall_fn=recall_fn, ticks=TICKS, h0=h0, h1=h1, seed=42)
            print(f"  {name}: {_fmt(r, TICKS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
