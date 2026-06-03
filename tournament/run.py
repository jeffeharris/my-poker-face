#!/usr/bin/env python3
"""CLI to run a headless multi-table tournament and print the standings.

    # Fast deterministic model (no poker engine, no LLM) — for orchestration checks:
    python -m tournament.run --field 18 --table-size 6 --fake

    # Real poker engine with no-LLM tiered/rule bots:
    docker compose exec backend python -m tournament.run --field 24 --table-size 6

The default field is 18-24 entrants across 3-4 tables — the Phase 1 target for
getting balancing and table-breaking right.
"""

import argparse
import sys

from .config import DEFAULT_FIELD_ARCHETYPES, TournamentConfig
from .director import FakeHandResolver, TournamentDirector


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--field', type=int, default=18, help='number of entrants')
    p.add_argument('--table-size', type=int, default=6, help='seats per table')
    p.add_argument('--stack', type=int, default=10_000, help='starting stack')
    p.add_argument('--seed', type=int, default=0, help='master seed (reproducible)')
    p.add_argument('--start-bb', type=int, default=100, help='starting big blind')
    p.add_argument('--blind-growth', type=float, default=1.5)
    p.add_argument('--rounds-per-level', type=int, default=5)
    p.add_argument(
        '--fake',
        action='store_true',
        help='use the deterministic FakeHandResolver (no poker engine / LLM)',
    )
    p.add_argument(
        '--archetypes',
        default=','.join(DEFAULT_FIELD_ARCHETYPES),
        help='comma-separated field composition (cycled across seats)',
    )
    args = p.parse_args(argv)

    config = TournamentConfig(
        field_size=args.field,
        table_size=args.table_size,
        starting_stack=args.stack,
        seed=args.seed,
        starting_big_blind=args.start_bb,
        blind_growth=args.blind_growth,
        rounds_per_level=args.rounds_per_level,
        field_archetypes=tuple(a.strip() for a in args.archetypes.split(',') if a.strip()),
    )

    if args.fake:
        resolver = FakeHandResolver()
    else:
        # Imported lazily so --fake runs don't require the poker engine.
        from .engine_resolver import EngineHandResolver

        # entries are built by the director; rebuild the same mapping here.
        player_ids = [f"P{i + 1:02d}" for i in range(config.field_size)]
        entries = {
            pid: config.field_archetypes[i % len(config.field_archetypes)]
            for i, pid in enumerate(player_ids)
        }
        resolver = EngineHandResolver(entries)

    director = TournamentDirector(config, resolver=resolver)
    result = director.run()

    print("=" * 60)
    print(
        f"TOURNAMENT: {config.field_size} entrants, {config.table_size}-max, "
        f"seed={config.seed}, resolver={'fake' if args.fake else 'engine'}"
    )
    print(f"  rounds played: {result.rounds_played}   terminal: {result.terminal_reason}")
    print(f"  total chips:   {result.total_chips}")
    print("=" * 60)
    total_moves = sum(len(r.seat_moves) for r in director.round_reports)
    total_elims = sum(len(r.eliminations) for r in director.round_reports)
    final_level = director.round_reports[-1].level if director.round_reports else None
    print(
        f"  seat moves: {total_moves}   eliminations: {total_elims}"
        + (
            f"   final blinds: {final_level.small_blind}/{final_level.big_blind}"
            if final_level
            else ""
        )
    )
    print("=" * 60)
    print(f"  {'pos':>4}  {'player':<6} {'archetype':<12}")
    for s in result.standings:
        marker = '  🏆' if s.finishing_position == 1 else ''
        print(f"  {s.finishing_position:>4}  {s.player_id:<6} {s.archetype:<12}{marker}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
