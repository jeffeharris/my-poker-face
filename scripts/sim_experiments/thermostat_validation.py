#!/usr/bin/env python3
"""Validate the Director reserve thermostat end-to-end in the cash sim.

Wires the whole reserve-band stack together and watches the reserves/holdings
ratio evolve over a run:
  - genesis seed (boot the bank pool to ~5% of holdings at sandbox birth),
  - VICE_RESERVE_GATED (vice refill scales with the deficit),
  - RAKE_RESERVE_GATED (rake tiers/rate graduate with the deficit).

It seeds a FRESH isolated tempdb sandbox (the 76-cast roster + bankrolls + lobby),
flips the Director flags on, seeds the genesis reserve, then runs the cash sim in
chunks — calling `economy_signal.signal()` between chunks to trace the ratio. The
sim drives cash hands + lobby refresh (vice/rake/side-hustle), so it exercises the
refill faucet; it does NOT run the tournament overlay (no drain), so this measures
the CLIMB rate toward the 0.12 trigger — i.e. how long a Main Event takes to earn,
the cadence input. A full sawtooth needs the tournament ticker in the loop (TODO).

Usage (in the backend container):
    docker compose exec -T backend python -m scripts.sim_experiments.thermostat_validation \
        --ticks 400 --chunk 10 --seed 0

Run in the background — a few hundred ticks of real hands takes minutes.
"""

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cash_mode import economy_flags  # noqa: E402
from cash_mode.closed_economy import ensure_genesis_reserve_seeded  # noqa: E402
from cash_mode.sim_runner import SimConfig, run_sim  # noqa: E402
from core.economy import economy_signal as chair  # noqa: E402
from poker.repositories import create_repos  # noqa: E402
from scripts.seed_sim_sandbox import seed_sim_sandbox  # noqa: E402

OWNER = 'sim-thermostat'


def _band(ratio: float) -> str:
    if ratio >= chair.RESERVE_TRIGGER:
        return 'TRIGGER'
    if ratio >= chair.RESERVE_HEALTHY:
        return 'healthy'
    if ratio >= chair.RESERVE_CRITICAL:
        return 'low'
    return 'critical'


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--ticks', type=int, default=400, help='Total sim ticks')
    ap.add_argument('--chunk', type=int, default=10, help='Ticks per ratio sample')
    ap.add_argument('--seed', type=int, default=0, help='RNG seed')
    ap.add_argument(
        '--genesis-ratio',
        type=float,
        default=None,
        help='Override GENESIS_RESERVE_RATIO (default: flag value)',
    )
    args = ap.parse_args()

    # Flip the Director levers on for the run (read at call-time, so setting the
    # module attrs is enough — same pattern the tests use).
    economy_flags.VICE_RESERVE_GATED = True
    economy_flags.RAKE_RESERVE_GATED = True
    economy_flags.GENESIS_RESERVE_ENABLED = True
    if args.genesis_ratio is not None:
        economy_flags.GENESIS_RESERVE_RATIO = args.genesis_ratio

    with tempfile.TemporaryDirectory() as d:
        db_path = str(Path(d) / 'thermostat.db')
        sandbox_id = seed_sim_sandbox(name='thermostat', owner_id=OWNER, db_path=db_path)
        repos = create_repos(db_path)
        ledger = repos['chip_ledger_repo']

        # Genesis: seed the pool to GENESIS_RESERVE_RATIO of holdings, once. The
        # seeder above just ran a fresh all-"created" bankroll pass, so pass a
        # synthetic all-created marker to satisfy the genesis guard.
        st0 = chair.signal(ledger, sandbox_id=sandbox_id)
        seeded = ensure_genesis_reserve_seeded(
            chip_ledger_repo=ledger,
            sandbox_id=sandbox_id,
            seed_actions={'_fresh_seed': 'created'},
        )
        st1 = chair.signal(ledger, sandbox_id=sandbox_id)
        print(f'roster holdings (seed): {st0.holdings:,}')
        print(f'genesis seeded: {seeded:,} chips  -> ratio {st1.ratio:.4f} ({_band(st1.ratio)})')
        print(
            f'bands: critical<{chair.RESERVE_CRITICAL}  healthy>={chair.RESERVE_HEALTHY}  '
            f'trigger>={chair.RESERVE_TRIGGER}\n'
        )
        print(f'{"tick":>6} {"reserves":>12} {"holdings":>12} {"ratio":>8}  band')

        samples = []
        done = 0
        first = True
        while done < args.ticks:
            n = min(args.chunk, args.ticks - done)
            cfg = SimConfig(
                sandbox_id=sandbox_id,
                num_ticks=n,
                rng_seed=args.seed + done,  # vary per chunk so it's not n identical ticks
                # genesis already seeded above; don't double-seed in run_sim.
                initial_bank_pool_seed=0,
                progress_every=0,
                metrics_every=max(1, n),
                audit_every=max(1, n),
            )
            run_sim(cfg, repos=repos)
            done += n
            st = chair.signal(ledger, sandbox_id=sandbox_id)
            samples.append((done, st))
            mark = ' <-- TRIGGER reached' if st.ratio >= chair.RESERVE_TRIGGER and first else ''
            if st.ratio >= chair.RESERVE_TRIGGER:
                first = False
            print(
                f'{done:>6} {st.reserves:>12,} {st.holdings:>12,} {st.ratio:>8.4f}  {_band(st.ratio)}{mark}'
            )

        # --- Summary ---
        ratios = [s.ratio for _, s in samples]
        from collections import Counter

        occ = Counter(_band(r) for r in ratios)
        first_trigger = next((t for t, s in samples if s.ratio >= chair.RESERVE_TRIGGER), None)
        print('\n=== summary ===')
        print(
            f'ratio: start {st1.ratio:.4f} -> end {ratios[-1]:.4f}  '
            f'(min {min(ratios):.4f}, max {max(ratios):.4f})'
        )
        print(
            f'band occupancy (of {len(ratios)} samples): '
            + ', '.join(f'{b}={occ.get(b,0)}' for b in ('critical', 'low', 'healthy', 'TRIGGER'))
        )
        if first_trigger is not None:
            print(
                f'reached TRIGGER (0.12) at tick {first_trigger} '
                f'(≈ {first_trigger} ticks of play to earn the first Main Event)'
            )
        else:
            print(
                f'did NOT reach TRIGGER (0.12) in {args.ticks} ticks — '
                f'faucet too slow at this horizon, or holdings too large'
            )
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
