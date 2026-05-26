#!/usr/bin/env python3
"""Single-table Winner-Take-All SNG eval (docs/plans/EVAL_HARNESS_PLAN.md §P1).

The honest, gold-standard absolute eval: equal starting stacks, **escalating
blinds**, **elimination**, **play to one winner**, **win-rate** — the structure
that matches the real game, not fixed-depth bb/100. It exercises the whole depth
progression (100bb → 50 → 25 → push/fold) that fixed-depth runs never touch, and
because it is winner-take-all, chip-EV = $-EV so accumulation and survival are
rewarded correctly.

The poker engine already does the hard parts (verified): one `PokerStateMachine`
plays continuously across hands — `hand_over_transition` carries stacks, drops
busted players (`reset_game_state_for_new_hand` filters `stack > 0`), rotates the
button over survivors, and escalates blinds via `BlindConfig`; heads-up blind
posting is handled. So this runner is a thin driver over that engine plus
win-rate bookkeeping. The per-hand action loop is reused from
`champion_challenger.run_cc_hand` (multistreet-aware, drives every seat).

Two modes (the field / the gate, by win-rate):
  - **field**  — N archetypes at the table; which archetype wins SNGs? The WTA
    analog of the Baseline-vs-TAG/LAG/Rock/Nit/GTO-Lite self-play check.
  - **champion_challenger** — N seats split challenger (change ON) / champion
    (change OFF), all one archetype; challenger-group win-rate vs the
    n_challenger/N null. The WTA-correct version of the P0 gate, hardened
    (EVAL_HARNESS_PLAN §P1-P4 / docs/plans/SNG_RUNNER_HARDENING.md): each seed
    is played twice with the challenger group **role-swapped** to the
    complementary seats, so fixed seat / first-button bias cancels; the verdict
    is a bootstrap CI over independent seed-blocks; and outcome accounting
    refuses a verdict on silent dropouts (None winners / max-hands fallbacks).

Usage:
    # field: which archetype wins single-table 6-max SNGs?
    docker compose exec backend python -m experiments.sng_runner \\
        --mode field --field Baseline,TAG,LAG,Rock,Nit,GTO-Lite --sngs 400

    # gate: does enabling multistreet win more SNGs than leaving it off?
    # (--sngs counts antithetic seed-blocks; each runs 2 role-swapped SNGs)
    docker compose exec backend python -m experiments.sng_runner \\
        --mode champion_challenger --change multistreet --sngs 400

    # calibrate the gate: A-A null (§P3) must cover 50%; the cripple pair (§P4)
    # must land CI-clear on the right side.
    docker compose exec backend python -m experiments.sng_runner \\
        --mode champion_challenger --change null --sngs 200
"""

import argparse
import logging
import math
import os
import random
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.getLogger('poker.bounded_options').setLevel(logging.ERROR)

from experiments.champion_challenger import (
    CHANGES,
    _apply_flags,
    _challenger_seat_indices,
    run_cc_hand,
)
from experiments.simulate_bb100 import ARCHETYPES, TERMINAL_PHASES, make_controller, make_game_state
from poker.poker_state_machine import PokerPhase, PokerStateMachine
from poker.strategy.strategy_table import load_strategy_table

# A turbo-ish ramp: start 100bb deep, +50% every 10 hands. Over a 6-handed SNG
# this walks stacks down through ~50/25/push-fold and reliably ends in well
# under MAX_HANDS, so the runner exercises the full depth progression P0 misses.
DEFAULT_BLIND = {'growth': 1.5, 'hands_per_level': 10, 'max_blind': 0}
MAX_HANDS = 1000  # hard safety cap; escalating blinds end real SNGs far sooner


# ── One SNG ─────────────────────────────────────────────────────────────────

# Terminal reasons for one SNG (gate accounting, EVAL_HARNESS_PLAN §P1):
TERMINAL_SINGLE = 'single_survivor'  # clean WTA finish — one player holds all chips
TERMINAL_CAP = 'max_hands_cap'  # hit max_hands with >1 left → chip-leader fallback
TERMINAL_NONE = 'no_survivors'  # no player with chips > 0 (winner is None)


@dataclass
class SngResult:
    """Outcome of one SNG, with enough to keep gate accounting honest.

    `terminal_reason` distinguishes a clean WTA finish (one survivor holds every
    chip) from a max-hands fallback (chip leader picked among multiple
    survivors) or the degenerate no-survivor case — both of which must be
    visible to the gate rather than silently counted as a win. `final_ante` is
    the blind level reached, surfacing the depth ramp the SNG exercised.
    """

    winner: Optional[str]
    hands_played: int
    final_stacks: Dict[str, int]
    terminal_reason: str
    final_ante: int


def play_sng(
    seat_specs: List[Tuple[str, dict, object, dict]],
    blind_config: dict,
    starting_stack: int,
    big_blind: int,
    sng_seed: int,
    max_hands: int = MAX_HANDS,
    on_hand_start: Optional[Callable[[int, object], None]] = None,
) -> SngResult:
    """Play one single-table WTA SNG to a winner.

    `seat_specs` is one (name, archetype_config, strategy_table, flags) per seat.
    Controllers are built once and persist across hands (the SM carries stacks);
    the engine drops busted players at each hand-over. The winner is the lone
    survivor at a clean finish (`TERMINAL_SINGLE`), or the chip leader if
    `max_hands` is hit (`TERMINAL_CAP`, which shouldn't happen with escalating
    blinds). `final_stacks` maps each surviving seat name to its stack — under
    WTA with no rake the winner holds every chip at a clean finish.

    `on_hand_start(hand_index, game_state)` is an optional observability hook
    fired at the top of each hand (used by the deck-determinism test, §P0); the
    game_state's `deck` at that point is exactly the deck the hand will deal.
    """
    names = [s[0] for s in seat_specs]
    gs = make_game_state(
        player_names=names,
        big_blind=big_blind,
        starting_stack=starting_stack,
        dealer_idx=0,
        seed=sng_seed,
    )
    # record_snapshots=False: this table lives for the whole tournament, so the
    # per-transition snapshot tuple would grow unbounded otherwise.
    sm = PokerStateMachine(gs, blind_config=blind_config, record_snapshots=False)
    sm.current_hand_seed = sng_seed

    controllers = []
    for i, (name, cfg, table, flags) in enumerate(seat_specs):
        ctrl = make_controller(name, cfg, table, sm, rng_seed=sng_seed + 1_000_000 * i)
        # Tiered/baseline seats touch opponent_model_manager (no-op at
        # anchors=None); the bypassed __init__ never set it. Rule bots ignore it.
        ctrl.opponent_model_manager = None
        _apply_flags(ctrl, flags)
        controllers.append(ctrl)

    hand_count = 0
    while hand_count < max_hands:
        if len([p for p in sm.game_state.players if p.stack > 0]) <= 1:
            break
        if on_hand_start is not None:
            on_hand_start(hand_count, sm.game_state)
        # Per-hand global-random seed so rule-bot / clone draws are reproducible
        # (the deck is seeded separately via the SM's own hand-seed progression).
        random.seed(sng_seed * 1_000_003 + hand_count)
        run_cc_hand(sm, controllers, big_blind)
        # run_cc_hand stops at HAND_OVER; one advance fires hand_over_transition
        # → drops busted players, rotates button, escalates blinds, deals next.
        if sm.phase == PokerPhase.HAND_OVER:
            sm.advance_state()
        hand_count += 1

    survivors = [p for p in sm.game_state.players if p.stack > 0]
    final_stacks = {p.name: p.stack for p in survivors}
    final_ante = sm.game_state.current_ante
    if not survivors:
        return SngResult(None, hand_count, final_stacks, TERMINAL_NONE, final_ante)
    if len(survivors) == 1:
        return SngResult(survivors[0].name, hand_count, final_stacks, TERMINAL_SINGLE, final_ante)
    # >1 survivor means the loop hit max_hands: a chip-leader fallback, not a
    # clean WTA finish. Surfaced as TERMINAL_CAP so the gate can refuse it.
    winner = max(survivors, key=lambda p: p.stack).name
    return SngResult(winner, hand_count, final_stacks, TERMINAL_CAP, final_ante)


# ── Seat construction per mode ──────────────────────────────────────────────


def _field_seat_specs(field: List[str], table, rotation: int) -> List[Tuple[str, dict, object, dict]]:
    """One archetype per seat, rotated by `rotation` so the field's starting
    seats (and thus first-button assignment) vary across SNGs."""
    rotated = field[rotation:] + field[:rotation]
    specs = []
    seen: Counter = Counter()
    for arch in rotated:
        seen[arch] += 1
        # Unique seat name even when the field repeats an archetype.
        name = f"{arch}#{seen[arch]}"
        specs.append((name, ARCHETYPES[arch], table, {}))
    return specs


def _cc_seat_specs(
    change: str, n_seats: int, challenger_idx: set, champion_table, challenger_table, archetype: str
) -> Tuple[List[Tuple[str, dict, object, dict]], set]:
    """N seats of one archetype; the seats in `challenger_idx` run the change ON
    (challenger), the rest run it OFF (champion).

    `challenger_idx` is passed explicitly (not derived from a count) so the
    role-swap protocol (§P2) can seat the challenger group in the base indices
    on one run and in their complement on the next. Seat names encode role
    (CHAL/CHMP) and seat index, so the winner alone tells us both who won and
    from which seat — no separate name table needed downstream.
    """
    spec = CHANGES[change]
    arch_cfg = ARCHETYPES[archetype]
    specs = []
    for i in range(n_seats):
        is_chal = i in challenger_idx
        name = f"{'CHAL' if is_chal else 'CHMP'}_{i}"
        specs.append(
            (
                name,
                arch_cfg,
                challenger_table if is_chal else champion_table,
                spec.challenger_flags if is_chal else spec.champion_flags,
            )
        )
    challenger_names = {specs[i][0] for i in challenger_idx}
    return specs, challenger_names


def _seat_index(name: str) -> int:
    """Seat index from a CHAL_i / CHMP_i seat name (for per-seat skew)."""
    return int(name.rsplit('_', 1)[1])


# ── Accounting (EVAL_HARNESS_PLAN §P1) ───────────────────────────────────────


@dataclass
class Accounting:
    """Per-run integrity counts the gate refuses a verdict without.

    A gate can't have invisible dropouts: every attempted SNG must land in
    exactly one bucket. `none` (no survivors) and `cap` (max-hands chip-leader
    fallback) are the non-clean outcomes that were silently dropped/miscounted
    before. `end_antes` records the blind level each SNG ended at (the depth
    ramp it exercised, §P6-lite).
    """

    attempted: int = 0
    decisive: int = 0  # clean single-survivor WTA finishes
    none: int = 0
    cap: int = 0
    end_antes: Counter = field(default_factory=Counter)

    def record(self, res: 'SngResult') -> bool:
        """Bucket one SNG. Returns True iff it was a clean WTA finish."""
        self.attempted += 1
        self.end_antes[res.final_ante] += 1
        if res.terminal_reason == TERMINAL_NONE:
            self.none += 1
            return False
        if res.terminal_reason == TERMINAL_CAP:
            self.cap += 1
            return False
        self.decisive += 1
        return True

    def merge(self, other: 'Accounting') -> None:
        self.attempted += other.attempted
        self.decisive += other.decisive
        self.none += other.none
        self.cap += other.cap
        self.end_antes.update(other.end_antes)


@dataclass
class CCBlock:
    """One antithetic seed-block: the paired (role-swapped) outcomes for a seed.

    The verdict unit (§P2). `chal_wins` is challenger-group wins across the two
    role-swapped SNGs for this seed (0, 1, or 2); `decisive` is how many of the
    two were clean WTA finishes. Pairing seats {0,2,4} on run A with their
    complement {1,3,5} on run B cancels any fixed seat/first-button advantage by
    construction. `seat_wins` keys winner *seat index* for the per-seat skew
    check (a clean harness wins evenly across seats).
    """

    seed: int
    chal_wins: int = 0
    decisive: int = 0
    seat_wins: Counter = field(default_factory=Counter)


# ── Workers (ProcessPool: each runs a batch of SNGs) ────────────────────────


def _field_worker(args) -> Tuple[Counter, Accounting]:
    field, blind_config, starting_stack, big_blind, seed_start, count = args
    logging.getLogger('poker.bounded_options').setLevel(logging.ERROR)
    table = load_strategy_table()
    wins: Counter = Counter()
    acct = Accounting()
    for k in range(count):
        seed = seed_start + k
        specs = _field_seat_specs(field, table, rotation=seed % len(field))
        res = play_sng(specs, blind_config, starting_stack, big_blind, seed)
        if acct.record(res):
            # Strip the "#n" seat suffix back to the archetype.
            wins[res.winner.rsplit('#', 1)[0]] += 1
    return wins, acct


def _cc_worker(args) -> Tuple[List[CCBlock], Accounting]:
    """Run a batch of antithetic seed-blocks for champion_challenger mode.

    Each seed is played twice: run A seats the challenger group in the base
    interleave (`_challenger_seat_indices`), run B in its complement. Splitting
    work by *whole seeds* (see `main`) keeps each block intact in one worker.
    """
    (
        change,
        n_seats,
        n_challenger,
        archetype,
        blind_config,
        starting_stack,
        big_blind,
        seed_start,
        count,
    ) = args
    logging.getLogger('poker.bounded_options').setLevel(logging.ERROR)
    spec = CHANGES[change]
    champion_table = spec.champion_table()
    challenger_table = spec.challenger_table()
    base_idx = set(_challenger_seat_indices(n_seats, n_challenger))
    comp_idx = set(range(n_seats)) - base_idx

    blocks: List[CCBlock] = []
    acct = Accounting()
    for k in range(count):
        seed = seed_start + k
        block = CCBlock(seed=seed)
        for challenger_idx in (base_idx, comp_idx):
            specs, challenger_names = _cc_seat_specs(
                change, n_seats, challenger_idx, champion_table, challenger_table, archetype
            )
            res = play_sng(specs, blind_config, starting_stack, big_blind, seed)
            if not acct.record(res):
                continue  # None / cap-fallback: not a clean win, excluded
            block.decisive += 1
            block.seat_wins[_seat_index(res.winner)] += 1
            if res.winner in challenger_names:
                block.chal_wins += 1
        blocks.append(block)
    return blocks, acct


def _split(n_sngs: int, base_seed: int) -> List[Tuple[int, int]]:
    """Split n_sngs into one (seed_start, count) chunk per worker."""
    workers = min(os.cpu_count() or 1, max(1, n_sngs))
    base = n_sngs // workers
    rem = n_sngs % workers
    chunks = []
    cursor = base_seed
    for w in range(workers):
        count = base + (1 if w < rem else 0)
        if count:
            chunks.append((cursor, count))
            cursor += count
    return chunks


# ── Reporting ────────────────────────────────────────────────────────────────


def _wilson(wins: int, n: int) -> Tuple[float, float, float]:
    """Wilson 95% CI for a proportion (robust near 0/1 and small n)."""
    if n == 0:
        return 0.0, 0.0, 0.0
    p = wins / n
    z = 1.96
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return p, max(0.0, center - half), min(1.0, center + half)


def _bootstrap_ci_blocks(
    blocks: List[CCBlock], iters: int = 5000, rng_seed: int = 1_234_567
) -> Tuple[float, float, float]:
    """95% CI for the challenger-group win-rate, bootstrapped over seed-blocks.

    The seed-block (the antithetic pair) is the independent unit — the two SNGs
    *within* a block share a seed and are correlated, so resampling individual
    SNGs would understate variance. We resample whole blocks with replacement
    and recompute the pooled win-rate (Σ chal_wins / Σ decisive), giving a CI
    that respects the paired structure. Returns (point, lo, hi)."""
    total_wins = sum(b.chal_wins for b in blocks)
    total_dec = sum(b.decisive for b in blocks)
    point = total_wins / total_dec if total_dec else 0.0
    n = len(blocks)
    if n == 0:
        return 0.0, 0.0, 0.0
    rng = random.Random(rng_seed)
    wins = [b.chal_wins for b in blocks]
    dec = [b.decisive for b in blocks]
    samples: List[float] = []
    for _ in range(iters):
        sw = sd = 0
        for _ in range(n):
            j = rng.randrange(n)
            sw += wins[j]
            sd += dec[j]
        if sd > 0:
            samples.append(sw / sd)
    if not samples:
        return point, 0.0, 1.0
    samples.sort()
    lo = samples[int(0.025 * len(samples))]
    hi = samples[min(len(samples) - 1, int(0.975 * len(samples)))]
    return point, lo, hi


def _print_accounting(acct: Accounting):
    """Print the integrity counts and return True iff the run is clean enough
    for a verdict.

    A cut-gate must not silently condition its win-rate on *clean finishes*:
    the rate is computed over decisive (single-survivor) SNGs only, so if the
    excluded outcomes correlate with strategy/seat the accepted verdict is
    biased. With escalating blinds a max-hands cap is pathological — the depth
    ramp failed to end the SNG — so we refuse on **any** cap, not merely a rate,
    and on any None winner. (Empirically both are 0 here; the guard is the
    point.)"""
    fallback_rate = acct.cap / acct.attempted if acct.attempted else 0.0
    print("\n── accounting (gate integrity) ──")
    print(f"  attempted SNGs:   {acct.attempted}")
    print(f"  clean finishes:   {acct.decisive}")
    print(f"  None (no surv.):  {acct.none}")
    print(f"  max-hands cap:    {acct.cap}  ({100*fallback_rate:.2f}%)")
    if acct.end_antes:
        ramp = ', '.join(f'{ante}:{cnt}' for ante, cnt in sorted(acct.end_antes.items()))
        print(f"  end-blind ramp:   {ramp}")
    clean = acct.none == 0 and acct.cap == 0
    if not clean:
        print(
            f"  ⚠ NOT CLEAN — {acct.none} None winner(s), {acct.cap} max-hands "
            f"cap(s); win-rate would be conditional on clean finishes — verdict REFUSED"
        )
    return clean


def report_field(field: List[str], wins: Counter, acct: Accounting):
    null = 1.0 / len(field)
    n_clean = acct.decisive
    print("\n" + "=" * 70)
    print(f"WTA-SNG FIELD: {acct.attempted} single-table SNGs | seats={len(field)}")
    print(f"  field: {', '.join(field)}")
    print(f"  null (equal skill): each archetype wins {100*null:.1f}%")
    print("=" * 70)
    clean = _print_accounting(acct)
    print(f"\n  {'archetype':<14} {'wins':>5} {'win%':>7}  {'95% CI':>16}")
    # Denominator is clean finishes (None/cap excluded), so win% sums to 100.
    for arch, _ in sorted(wins.items(), key=lambda kv: -kv[1]):
        p, lo, hi = _wilson(wins[arch], n_clean)
        flag = ''
        if lo > null:
            flag = '  ✅ > null'
        elif hi < null:
            flag = '  ❌ < null'
        print(f"  {arch:<14} {wins[arch]:>5} {100*p:>6.1f}%  [{100*lo:>4.1f},{100*hi:>4.1f}]{flag}")
    for arch in field:
        if arch not in wins:
            print(f"  {arch:<14} {0:>5} {0.0:>6.1f}%  (never won)")
    if not clean:
        print("\n  ⚠ accounting not clean — treat win-rates as suspect.")


def report_cc(change: str, n_seats: int, n_challenger: int, blocks: List[CCBlock], acct: Accounting):
    spec = CHANGES[change]
    null = n_challenger / n_seats
    point, lo, hi = _bootstrap_ci_blocks(blocks)
    total_wins = sum(b.chal_wins for b in blocks)
    total_dec = sum(b.decisive for b in blocks)
    print("\n" + "=" * 70)
    print(f"WTA-SNG CHAMPION vs CHALLENGER (antithetic role-swap): change={change!r}")
    print(f"  {spec.description}")
    print(
        f"  {n_challenger} challenger vs {n_seats - n_challenger} champion seats | "
        f"{len(blocks)} seed-blocks × 2 role-swapped configs = {acct.attempted} SNGs"
    )
    print(f"  null (equal skill): challenger group wins {100*null:.1f}% of SNGs")
    print("=" * 70)
    clean = _print_accounting(acct)
    print(
        f"\n  challenger win-rate: {100*point:.1f}%  ({total_wins}/{total_dec})  "
        f"95% CI [{100*lo:.1f}, {100*hi:.1f}]  (bootstrap over {len(blocks)} blocks)"
    )

    # Per-seat skew: with role-swap + the null, every seat index should win
    # ~1/n_seats. A persistent skew means residual seat bias survived the swap.
    seat_wins: Counter = Counter()
    for b in blocks:
        seat_wins.update(b.seat_wins)
    seat_null = 1.0 / n_seats
    print(f"\n  per-seat win share (null {100*seat_null:.1f}% each):")
    skews = []
    for i in range(n_seats):
        share = seat_wins[i] / total_dec if total_dec else 0.0
        skews.append(abs(share - seat_null))
        print(f"    seat {i}: {seat_wins[i]:>4}  {100*share:>5.1f}%")
    max_skew = max(skews) if skews else 0.0
    print(f"  max |seat share − null|: {100*max_skew:.1f}pp")

    if not clean:
        print("\n  VERDICT: ⚠ REFUSED — accounting not clean (see above)")
        return
    if lo > null:
        verdict = "✅ CI-CLEAR ABOVE null — challenger wins more SNGs (real improvement)"
    elif hi < null:
        verdict = "❌ CI-CLEAR BELOW null — challenger wins fewer SNGs (regression)"
    else:
        verdict = "➖ INCONCLUSIVE — CI spans the null (need more blocks, or no real effect)"
    print(f"\n  VERDICT: {verdict}")


def _run_pool(worker, work):
    if len(work) > 1:
        with ProcessPoolExecutor(max_workers=len(work)) as ex:
            return list(ex.map(worker, work))
    return [worker(work[0])]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=['field', 'champion_challenger'], default='field')
    p.add_argument(
        '--field',
        default='Baseline,TAG,LAG,Rock,Nit,GTO-Lite',
        help='field mode: comma-separated archetypes, one per seat',
    )
    p.add_argument('--change', choices=sorted(CHANGES), help='champion_challenger mode: the change to A/B')
    p.add_argument('--archetype', default='Baseline', help='champion_challenger mode: archetype for all seats')
    p.add_argument('--seats', type=int, default=6, help='champion_challenger mode: table size')
    p.add_argument('--challenger-seats', type=int, default=3, help='champion_challenger mode: seats with change ON')
    p.add_argument(
        '--sngs',
        type=int,
        default=400,
        help='field mode: number of SNGs. champion_challenger mode: number of '
        'antithetic seed-blocks (each runs 2 role-swapped SNGs)',
    )
    p.add_argument('--seed', type=int, default=42, help='base seed')
    p.add_argument('--start-bb', type=int, default=100, help='starting stack in big blinds (bb=100)')
    p.add_argument('--blind-growth', type=float, default=DEFAULT_BLIND['growth'])
    p.add_argument('--hands-per-level', type=int, default=DEFAULT_BLIND['hands_per_level'])
    p.add_argument('--max-blind', type=int, default=DEFAULT_BLIND['max_blind'])
    args = p.parse_args()

    big_blind = 100
    starting_stack = args.start_bb * big_blind
    blind_config = {
        'growth': args.blind_growth,
        'hands_per_level': args.hands_per_level,
        'max_blind': args.max_blind,
    }

    if args.mode == 'field':
        field = [a.strip() for a in args.field.split(',')]
        for a in field:
            if a not in ARCHETYPES:
                print(f"Unknown archetype: {a}")
                sys.exit(1)
        work = [
            (field, blind_config, starting_stack, big_blind, start, count)
            for start, count in _split(args.sngs, args.seed)
        ]
        merged: Counter = Counter()
        acct = Accounting()
        for wins, w_acct in _run_pool(_field_worker, work):
            merged.update(wins)
            acct.merge(w_acct)
        report_field(field, merged, acct)
    else:
        if not args.change:
            print("--change is required for champion_challenger mode")
            sys.exit(1)
        if ARCHETYPES.get(args.archetype, {}).get('kind') == 'rule_bot':
            print(f"{args.archetype!r} is a rule_bot — it ignores tables/flags; the A/B is a no-op.")
            sys.exit(1)
        # Antithetic role-swap requires a symmetric split: the challenger group
        # and its complement (the swapped seats) must be the same size, else
        # the two role-swapped runs have different nulls and the pair isn't a
        # clean cancellation of seat bias.
        if 2 * args.challenger_seats != args.seats:
            print(
                f"champion_challenger role-swap needs a symmetric split "
                f"(2 × challenger-seats == seats); got {args.challenger_seats} of "
                f"{args.seats}. Use e.g. --seats 6 --challenger-seats 3, or "
                f"--seats 2 --challenger-seats 1 (HU)."
            )
            sys.exit(1)
        _challenger_seat_indices(args.seats, args.challenger_seats)  # validates the split
        # `--sngs` here counts seed-blocks; each worker runs whole blocks so an
        # antithetic pair never straddles two processes.
        work = [
            (
                args.change,
                args.seats,
                args.challenger_seats,
                args.archetype,
                blind_config,
                starting_stack,
                big_blind,
                start,
                count,
            )
            for start, count in _split(args.sngs, args.seed)
        ]
        blocks: List[CCBlock] = []
        acct = Accounting()
        for w_blocks, w_acct in _run_pool(_cc_worker, work):
            blocks.extend(w_blocks)
            acct.merge(w_acct)
        report_cc(args.change, args.seats, args.challenger_seats, blocks, acct)


if __name__ == '__main__':
    main()
