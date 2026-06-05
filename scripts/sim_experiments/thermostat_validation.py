#!/usr/bin/env python3
"""Validate the Director reserve thermostat end-to-end in the cash sim.

Wires the whole reserve-band stack together and watches the reserves/holdings
ratio evolve over a run:
  - genesis seed (boot the bank pool to ~5% of holdings at sandbox birth),
  - VICE_RESERVE_GATED (vice refill scales with the deficit),
  - RAKE_RESERVE_GATED (rake tiers/rate graduate with the deficit).

It seeds a FRESH isolated tempdb sandbox (the 76-cast roster + bankrolls + lobby),
flips the Director flags on, seeds the genesis reserve, then runs the cash sim in
chunks — tracing `economy_signal.signal().ratio` between chunks. The sim drives
the refill faucet (cash hands + lobby refresh: vice/rake/side-hustle) AND the
**tournament overlay**: each chunk it checks `should_offer_event`; when reserves
reach the trigger it fires a Main Event (`_fire_tournament`), draining the overlay
from the pool back to the field as prizes. So this measures the full SAWTOOTH
(climb to trigger → drain to floor → climb again) and the cadence.

Usage (in the backend container):
    # full run (climb to the real 0.12 trigger takes ~1000 ticks of play):
    docker compose exec -T backend python -m scripts.sim_experiments.thermostat_validation \
        --ticks 1000 --chunk 40 --seed 0
    # demo the sawtooth fast by lowering the trigger (must stay > the 0.06 floor):
    ... --ticks 500 --chunk 25 --trigger 0.08

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


def _fire_tournament(repos, sandbox_id, state, tid):
    """Apply one Main Event's economic effect: drain the bank overlay to the
    field as prizes. Returns the overlay (chips drained from reserves), 0 if none.

    Uses the real funding policy + ledger overlay/payout helpers, so the bank
    pool drains exactly as production would: `tournament_funding` sizes the
    overlay to bring reserves to the floor, `record_tournament_overlay` draws it
    from the pool, and `record_tournament_payout` redistributes it to AIs (here:
    split evenly across the cast — the freeroll prize returns the taxed chips to
    the field). Reserves ↓ overlay, holdings flat-to-up — the sawtooth drop.
    """
    from cash_mode.bankroll import AIBankrollState
    from core.economy import ledger as chip_ledger
    from core.economy.economy_signal import DEFAULT_MAIN_EVENT, tournament_funding

    plan = tournament_funding(
        state, field_size=DEFAULT_MAIN_EVENT.field_size, seat_price=0, human_in=False
    )
    overlay = plan.bank_overlay
    if overlay <= 0:
        return 0

    ledger = repos['chip_ledger_repo']
    bankroll = repos['bankroll_repo']
    chip_ledger.record_tournament_overlay(
        ledger, tournament_id=tid, amount=overlay, sandbox_id=sandbox_id
    )
    # Redistribute the prize pool across the cast (escrow -> AIs), crediting the
    # stored bankrolls so the next tick's wealth signal reflects the winnings.
    elig = repos['personality_repo'].list_eligible_for_cash_mode()
    pids = [e['personality_id'] for e in elig][: DEFAULT_MAIN_EVENT.field_size] or []
    if not pids:
        return overlay
    share = overlay // len(pids)
    paid = 0
    for pid in pids:
        if share <= 0:
            break
        chip_ledger.record_tournament_payout(
            ledger, sink=chip_ledger.ai(pid), tournament_id=tid, amount=share, sandbox_id=sandbox_id
        )
        stored = bankroll.load_ai_bankroll(pid, sandbox_id=sandbox_id)
        if stored is not None:
            bankroll.save_ai_bankroll(
                AIBankrollState(pid, int(stored.chips) + share, stored.last_regen_tick),
                sandbox_id=sandbox_id,
            )
        paid += share
    # Any rounding remainder is left in the escrow (negligible); reserves already
    # dropped by the full overlay.
    return overlay


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
    ap.add_argument(
        '--trigger',
        type=float,
        default=None,
        help='Override RESERVE_TRIGGER (lower it to demo the sawtooth without a '
        '~1000-tick climb; the overlay drains reserves back to the floor on each fire)',
    )
    ap.add_argument(
        '--no-casino',
        action='store_true',
        help='Disable casino spawning (the dominant drain) to isolate the '
        'tournament sawtooth — reserves climb cleanly on the vice/rake faucet',
    )
    args = ap.parse_args()

    if args.trigger is not None:
        chair.RESERVE_TRIGGER = args.trigger
    if args.no_casino:
        from cash_mode import casino_provisioning

        casino_provisioning.CASINO_SPAWN_THRESHOLDS = {}

    # Flip the Director levers on for the run (read at call-time, so setting the
    # module attrs is enough — same pattern the tests use).
    economy_flags.VICE_RESERVE_GATED = True
    economy_flags.RAKE_RESERVE_GATED = True
    economy_flags.GENESIS_RESERVE_ENABLED = True
    # Inequality-aware rake: inert on the top-heavy launch cast (vice leads), but
    # wired so the full Director stack runs — exercise it with a flatter roster.
    economy_flags.DIRECTOR_INEQUALITY_RAKE = True
    # Lean casino fish lifecycle: 1 fish/casino (2 at $2), leaner prefund, leaner
    # whale — turns the lumpy casino drain into a steady trickle.
    economy_flags.CASINO_RESEED_ON_SPENT = True
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

        from core.economy.economy_signal import should_offer_event

        samples = []
        tournaments = []  # (tick, overlay) for each fired Main Event
        done = 0
        last_tourney_tick = -(10**9)
        min_gap = max(args.chunk, 1)  # at most one Main Event per chunk
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

            # Tournament overlay: when reserves reach the trigger, the Director
            # opens a Main Event — drain the overlay back to the field as prizes
            # (the sawtooth drop). Economic gate paces it; the tick gap is a floor.
            fired = ''
            spec = should_offer_event(st, cooldown_elapsed=(done - last_tourney_tick >= min_gap))
            if spec is not None:
                overlay = _fire_tournament(repos, sandbox_id, st, tid=f'tourney-{done}')
                if overlay > 0:
                    tournaments.append((done, overlay))
                    last_tourney_tick = done
                    st = chair.signal(ledger, sandbox_id=sandbox_id)  # post-drain
                    fired = f'  *** MAIN EVENT: -{overlay:,} overlay -> ratio {st.ratio:.4f}'

            samples.append((done, st))
            print(
                f'{done:>6} {st.reserves:>12,} {st.holdings:>12,} {st.ratio:>8.4f}  {_band(st.ratio)}{fired}'
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
        trig = chair.RESERVE_TRIGGER
        if tournaments:
            ticks = [t for t, _ in tournaments]
            gaps = [b - a for a, b in zip(ticks, ticks[1:], strict=False)]
            avg_gap = sum(gaps) / len(gaps) if gaps else None
            print(
                f'MAIN EVENTS fired: {len(tournaments)} at ticks '
                + ', '.join(str(t) for t in ticks)
            )
            print(
                f'  total overlay distributed: {sum(o for _, o in tournaments):,} chips'
                + (f'  |  avg gap: {avg_gap:.0f} ticks between events' if avg_gap else '')
            )
            print(f'  → SAWTOOTH confirmed: reserves climb to {trig}, drain to floor, repeat.')
        elif first_trigger is not None:
            print(f'reached TRIGGER ({trig}) at tick {first_trigger} but fired no event (gap?)')
        else:
            print(
                f'did NOT reach TRIGGER ({trig}) in {args.ticks} ticks — '
                f'still climbing (raise --ticks or lower --trigger to see the sawtooth)'
            )

        # --- Where do the chips flow? (cumulative bank-pool ledger by reason) ---
        # Refill = deposit-reason destructions (chips INTO the pool); drain =
        # draw-reason creations (chips OUT of the pool). This pinpoints the
        # dominant drain (casino seed vs fish/tourist vs side-hustle) and the
        # refill mix (rake vs vice) so the rebalance lever can be chosen.
        from core.economy.ledger import BANK_POOL_DEPOSIT_REASONS, BANK_POOL_DRAW_REASONS

        creations = ledger.sum_creations_by_reason(sandbox_id=sandbox_id)
        destructions = ledger.sum_destructions_by_reason(sandbox_id=sandbox_id)
        refill = {
            r: destructions.get(r, 0) for r in BANK_POOL_DEPOSIT_REASONS if destructions.get(r, 0)
        }
        drain = {r: creations.get(r, 0) for r in BANK_POOL_DRAW_REASONS if creations.get(r, 0)}
        total_refill = sum(refill.values())
        total_drain = sum(drain.values())
        print('\n=== bank-pool flows (cumulative, chips) ===')
        print(f'REFILL  total {total_refill:>12,}')
        for r, v in sorted(refill.items(), key=lambda kv: -kv[1]):
            print(f'   {r:<24} {v:>12,}')
        print(f'DRAIN   total {total_drain:>12,}')
        for r, v in sorted(drain.items(), key=lambda kv: -kv[1]):
            print(f'   {r:<24} {v:>12,}')
        print(
            f'NET (refill − drain): {total_refill - total_drain:>+12,}  '
            f'(includes the one-time genesis seed in REFILL)'
        )
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
