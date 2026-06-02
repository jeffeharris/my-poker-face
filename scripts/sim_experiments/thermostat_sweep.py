"""EXP_006 — bank-reserve thermostat sweep harness (flow-level).

Drives the REAL closed-economy machinery hands-OFF (so vice / casino /
side-hustle / movement are exact and fast — ~0.057s/tick) and injects the
two thermostat levers on top as conservation-clean chip moves:

  * modeled rake faucet  : debit a wealthy AI by `rake_amt`, record `table_rake`
                           (pool ↑, holdings ↓). Models per-tick table rake that
                           hands-off play would otherwise not generate; the
                           lever scales it up across tiers when the bank is low.
  * tournament overlay   : credit AIs by `overlay_amt`, record `tournament_overlay`
                           (pool ↓, holdings ↑). Models the bank distributing
                           reserves into the field when flush.

Both levers move REAL ai_bankroll_state chips + write the real ledger, so
`compute_bank_pool_reserves` and the audit stay consistent. The signal is
`reserves / holdings`. Lever constants are CLI args so a sweep is just N
invocations with different values (+ seeds), each on a FRESH sandbox.

Run (inside Docker, against the isolated copy DB):
    docker compose exec -T backend python -m scripts.sim_experiments.thermostat_sweep \
        --sandbox-id <uuid> --db-path /app/data/sim/econ_base.db \
        --ticks 4000 --rng-seed 42 --mode thermostat \
        --base-rake 130 --flush 0.10 --empty 0.03 \
        --overlay-pct 0.02 --rake-max-mult 3.0 \
        --out /app/data/sim/therm_run

Writes <out>.csv (per-tick trajectory) + <out>.summary.json.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

_project_root = str(Path(__file__).parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import random

import cash_mode.closed_economy as closed_economy
import core.economy.economy_signal as economy_signal
import core.economy.ledger as ledger
from cash_mode.lobby import refresh_unseated_tables
from poker.repositories import create_repos

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger("thermostat")
logger.setLevel(logging.INFO)

# Register the sim-only overlay reason so record() accepts it and
# compute_bank_pool_reserves counts it as a pool DRAW. Production P2 will
# add `tournament_overlay` to the real frozensets; here we patch it in so
# the model runs against the same ledger machinery.
if "tournament_overlay" not in ledger.LEDGER_REASONS:
    ledger.LEDGER_REASONS = frozenset(ledger.LEDGER_REASONS | {"tournament_overlay"})
if "tournament_overlay" not in closed_economy.BANK_POOL_DRAW_REASONS:
    closed_economy.BANK_POOL_DRAW_REASONS = frozenset(
        closed_economy.BANK_POOL_DRAW_REASONS | {"tournament_overlay"}
    )


def _holdings_and_top(conn: sqlite3.Connection, sandbox_id: str):
    """Return (total_ai_chips, [(pid, chips), ...] richest-first)."""
    rows = conn.execute(
        "SELECT personality_id, chips FROM ai_bankroll_state "
        "WHERE sandbox_id = ? ORDER BY chips DESC",
        (sandbox_id,),
    ).fetchall()
    total = sum(r[1] for r in rows)
    return total, rows


def _adjust_chips(conn: sqlite3.Connection, sandbox_id: str, pid: str, delta: int) -> None:
    conn.execute(
        "UPDATE ai_bankroll_state SET chips = chips + ? "
        "WHERE sandbox_id = ? AND personality_id = ?",
        (delta, sandbox_id, pid),
    )


def _rake_multiplier(signal: float, *, empty: float, max_mult: float) -> float:
    """Rake lever: 1.0 normally, scaling up to `max_mult` as signal → 0.

    Models turning on lower stake tiers ($200, then $50) when the bank is
    starved. Linear in the shortfall below `empty`.
    """
    if signal >= empty:
        return 1.0
    shortfall = (empty - signal) / empty  # 0..1
    return 1.0 + (max_mult - 1.0) * min(1.0, max(0.0, shortfall))


def _overlay_amount(signal: float, reserves: int, *, flush: float, overlay_pct: float) -> int:
    """Overlay lever: 0 normally, drawing up to `overlay_pct × reserves`/tick

    as signal climbs above `flush` (bank distributes into the field).
    """
    if signal <= flush:
        return 0
    over = (signal - flush) / flush  # 0..1+ ; clamp to 1
    return int(overlay_pct * reserves * min(1.0, over))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sandbox-id", required=True)
    p.add_argument("--db-path", required=True)
    p.add_argument("--ticks", type=int, required=True)
    p.add_argument("--tick-seconds", type=int, default=8)
    p.add_argument("--rng-seed", type=int, default=42)
    p.add_argument("--mode", choices=["baseline", "rake_only", "overlay_only", "thermostat",
                                       "tournament_cadence"],
                   default="thermostat")
    p.add_argument("--cooldown-seconds", type=int, default=economy_signal.MAIN_EVENT_COOLDOWN_SECONDS,
                   help="tournament_cadence: min spacing between overlay events (wall-clock)")
    p.add_argument("--field-size", type=int, default=economy_signal.DEFAULT_MAIN_EVENT.field_size,
                   help="tournament_cadence: Main Event field size (logging/spec only)")
    p.add_argument("--cadence-sizing", choices=["production", "to_setpoint"], default="production",
                   help="tournament_cadence overlay sizing: production (2%% of reserves) "
                        "or to_setpoint (drain back to FLUSH_SETPOINT each event)")
    p.add_argument("--base-rake", type=int, default=130,
                   help="Base modeled table_rake chips/tick ($1000-tier proxy)")
    p.add_argument("--flush", type=float, default=0.10, help="signal above which overlay fires")
    p.add_argument("--empty", type=float, default=0.03, help="signal below which rake scales up")
    p.add_argument("--overlay-pct", type=float, default=0.02,
                   help="fraction of reserves distributed per tick when fully flush")
    p.add_argument("--rake-max-mult", type=float, default=3.0,
                   help="max rake multiplier when bank is starved")
    p.add_argument("--preload-reserves", type=int, default=0,
                   help="seed the bank pool to ~N chips at tick 0 (ai→bank), to "
                        "exercise the regulated regime without the slow natural fill")
    p.add_argument("--metrics-every", type=int, default=10)
    p.add_argument("--progress-every", type=int, default=500)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    repos = create_repos(args.db_path)
    chip_ledger_repo = repos["chip_ledger_repo"]
    rng = random.Random(args.rng_seed)
    start_at = datetime(2026, 5, 30, 0, 0, 0)
    sb = args.sandbox_id

    lever_rake = args.mode in ("rake_only", "thermostat")
    lever_overlay = args.mode in ("overlay_only", "thermostat")
    # Discrete per-tournament overlay (the production cadence — §6 re-validation):
    # gate on the REAL chairman policy (FLUSH + cooldown) and size via the REAL
    # tournament_funding, instead of the per-tick continuous draw above.
    cadence_overlay = args.mode == "tournament_cadence"
    cooldown_ticks = max(1, args.cooldown_seconds // max(1, args.tick_seconds))
    ticks_since_event = cooldown_ticks  # eligible to fire on the first FLUSH tick
    n_events = 0

    conn = sqlite3.connect(args.db_path)
    rows_out = []

    # Optional pre-charge: the natural fill to FLUSH is slow (~1600 ticks), so to
    # exercise the *regulated* regime within a tractable horizon we can seed the
    # bank pool up front. Conservation-clean: debit the richest AIs (real
    # ai_bankroll_state) and record matching `bank_pool_deposit` rows (ai → bank),
    # so both the ledger and the chip stocks move together and `signal()` reads a
    # genuine FLUSH state.
    if args.preload_reserves > 0:
        _, top0 = _holdings_and_top(conn, sb)
        remaining = args.preload_reserves
        for pid, chips in top0:
            if remaining <= 0:
                break
            take = min(remaining, max(0, chips - 1000))  # leave each AI solvent
            if take <= 0:
                continue
            _adjust_chips(conn, sb, pid, -take)
            conn.commit()
            ledger.record(chip_ledger_repo, source=ledger.ai(pid), sink=ledger.bank(),
                          amount=take, reason="bank_pool_deposit",
                          context={"sim": "preload"}, sandbox_id=sb)
            remaining -= take
        logger.info("[therm] preloaded %d chips into the bank pool",
                    args.preload_reserves - remaining)

    for tick in range(args.ticks):
        now = start_at + timedelta(seconds=tick * args.tick_seconds)

        # 1. Advance the real cash world hands-off (vice/casino/side-hustle/movement).
        refresh_unseated_tables(
            cash_table_repo=repos["cash_table_repo"],
            personality_repo=repos["personality_repo"],
            bankroll_repo=repos["bankroll_repo"],
            sandbox_id=sb,
            now=now,
            rng=rng,
            hand_sim_prob=0.0,
            live_fill_prob=0.05,
            chip_ledger_repo=chip_ledger_repo,
            relationship_repo=repos["relationship_repo"],
            stake_repo=repos["stake_repo"],
            side_hustle_repo=repos.get("side_hustle_state_repo"),
            vice_mode="fake",
        )

        # 2. Read economy state.
        reserves = closed_economy.compute_bank_pool_reserves(chip_ledger_repo, sandbox_id=sb)
        holdings, top = _holdings_and_top(conn, sb)
        signal = reserves / max(1, holdings)

        rake_amt = 0
        overlay_amt = 0

        # 3a. The faucet — modeled per-tick table rake (the economy's inflow,
        # which hands-off play doesn't generate). Always on at mult 1.0; the rake
        # LEVER scales it up across tiers when the bank is starved.
        mult = _rake_multiplier(signal, empty=args.empty, max_mult=args.rake_max_mult) \
            if lever_rake else 1.0
        rake_amt = int(args.base_rake * mult)
        # Debit the richest solvent AI; record table_rake (ai → bank).
        if rake_amt > 0 and top:
            payer, chips = top[0]
            if chips > rake_amt:
                _adjust_chips(conn, sb, payer, -rake_amt)
                conn.commit()
                ledger.record(chip_ledger_repo, source=ledger.ai(payer), sink=ledger.bank(),
                              amount=rake_amt, reason="table_rake",
                              context={"sim": "thermostat"}, sandbox_id=sb)
            else:
                rake_amt = 0

        # 3b. Overlay lever — distribute reserves into the field when flush.
        # Two cadences:
        #   * per-tick (overlay_only / thermostat): a continuous proportional draw
        #     (EXP_006's tuning harness);
        #   * tournament_cadence (§6): discrete events gated by the REAL chairman
        #     policy — fire iff `should_offer_event` (FLUSH + cooldown elapsed),
        #     sized by the REAL `tournament_funding`. This is the production
        #     cadence we must re-validate before flipping the thermostat on.
        if lever_overlay:
            overlay_amt = _overlay_amount(signal, reserves, flush=args.flush,
                                          overlay_pct=args.overlay_pct)
        elif cadence_overlay:
            ticks_since_event += 1
            state = economy_signal.signal(chip_ledger_repo, sandbox_id=sb)
            spec = economy_signal.should_offer_event(
                state, cooldown_elapsed=ticks_since_event >= cooldown_ticks
            )
            if spec is not None:
                if args.cadence_sizing == "to_setpoint":
                    # Candidate fix: each discrete event drains reserves back to
                    # the FLUSH setpoint (sawtooth band), instead of a fixed 2%
                    # of reserves (which is far too weak across a 30-min cooldown).
                    target = int(economy_signal.FLUSH_SETPOINT * state.holdings)
                    overlay_amt = min(max(0, state.reserves - target),
                                      economy_signal.OVERLAY_CAP)
                else:  # "production" — the current tournament_funding sizing
                    plan = economy_signal.tournament_funding(
                        state, field_size=spec.field_size, seat_price=0, human_in=False
                    )
                    overlay_amt = plan.bank_overlay

        if overlay_amt > 0 and top:
            # Distribute across the bottom-half (the field's finishers get
            # paid; spread so it doesn't all pile on one whale).
            recipients = [r[0] for r in top[len(top) // 2:]] or [top[-1][0]]
            share = max(1, overlay_amt // len(recipients))
            paid = 0
            for pid in recipients:
                _adjust_chips(conn, sb, pid, share)
                paid += share
            conn.commit()
            ledger.record(chip_ledger_repo, source=ledger.bank(),
                          sink=ledger.ai(recipients[0]), amount=paid,
                          reason="tournament_overlay",
                          context={"sim": "thermostat"}, sandbox_id=sb)
            overlay_amt = paid
            if cadence_overlay:
                ticks_since_event = 0
                n_events += 1

        # 4. Metrics.
        if tick % args.metrics_every == 0 or tick == args.ticks - 1:
            rows_out.append({
                "tick": tick, "reserves": reserves, "holdings": holdings,
                "signal": round(signal, 5), "rake_amt": rake_amt,
                "overlay_amt": overlay_amt, "n_ai": len(top),
            })

        if args.progress_every and (tick + 1) % args.progress_every == 0:
            logger.info("[therm] tick %d/%d reserves=%d holdings=%d signal=%.4f",
                        tick + 1, args.ticks, reserves, holdings, signal)

    conn.close()

    # Write CSV + summary.
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    import csv as _csv
    with out.with_suffix(".csv").open("w", newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=list(rows_out[0].keys()))
        w.writeheader()
        w.writerows(rows_out)

    back = rows_out[len(rows_out) // 2:]
    xs = [r["tick"] for r in back]
    ys = [r["reserves"] for r in back]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs) or 1
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / denom
    summary = {
        "mode": args.mode, "ticks": args.ticks, "rng_seed": args.rng_seed,
        "base_rake": args.base_rake, "flush": args.flush, "empty": args.empty,
        "overlay_pct": args.overlay_pct, "rake_max_mult": args.rake_max_mult,
        "cooldown_seconds": args.cooldown_seconds, "cooldown_ticks": cooldown_ticks,
        "cadence_sizing": args.cadence_sizing, "n_overlay_events": n_events,
        "reserves_first": rows_out[0]["reserves"], "reserves_final": rows_out[-1]["reserves"],
        "reserves_back_min": min(ys), "reserves_back_max": max(ys),
        "back_half_slope_chips_per_tick": round(slope, 3),
        "signal_final": rows_out[-1]["signal"],
    }
    out.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2))
    logger.info("Done — mode=%s slope=%.2f chips/tick  reserves %d→%d  signal_final=%.4f",
                args.mode, slope, rows_out[0]["reserves"], rows_out[-1]["reserves"],
                rows_out[-1]["signal"])
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
