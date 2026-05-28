"""Profile the real LLM-personality field for the sticky-mid-passive audit (EXP_004).

This is **axis A + axis B** of EXP_004: characterize how the AI-personality
population actually plays, so we can answer whether a *hidden* sticky-mid-passive
station class exists that the current exploitation layer is blind to.

Why this approach (see docs/experiments/EXP_004_STICKY_MID_PASSIVE_POPULATION_AUDIT.md):
  - We can't use the human "Jeff" as ground truth (his data is test/real
    contaminated). So instead we measure the LLM-personality pool — the bot's
    actual production opponents — from existing play, no fresh sims / LLM cost.
  - The data lives in `player_decision_analysis` (per-decision rows: phase +
    action_taken + capture_id). `capture_id` separates LLM-driven play (a real
    prompt was captured) from tiered/rule play (sentinel -1) — critical because
    the two regimes play very differently (~59% vs ~23% VPIP) and averaging them
    is meaningless.

The archetype doc's core claim is that "balanced-looking" players are secretly
stations: low *global* AF hides per-street passivity (Jeff: global 1.36 but
per-street 0.35-0.46, WtSD ~60%). So we compute BOTH:
  - global aggression_factor  → what the current detector reads
  - per-street + postflop AF + WtSD → the better station signal

Then for each personality we (a) flag sticky-mid-zone membership and (b) run the
current `classify_opponent_archetype` to see whether it would catch them. The
gap between those two is H1.1b (the "blindness" the detector has today).

Usage:
    python3 experiments/profile_population.py \
        --db /home/jeffh/projects/my-poker-face/data/poker_games.db \
        --min-hands 200 \
        --out docs/experiments/EXP_004_STICKY_MID_PASSIVE_POPULATION_AUDIT/llm_field.csv

Read-only: opens the DB with `mode=ro` so it never contends with the live app's
writes (the main DB is actively written; reads in WAL mode don't block writers).
"""

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Optional, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from poker.strategy.exploitation import (  # noqa: E402
    AggregatedOpponentStats,
    classify_opponent_archetype,
)

DEFAULT_DB = "/home/jeffh/projects/my-poker-face/data/poker_games.db"

# action_taken vocabulary in player_decision_analysis (postflop bets are 'raise')
AGGRESSIVE = {"raise", "all_in"}
CALL = {"call"}
POSTFLOP_PHASES = ("FLOP", "TURN", "RIVER")

# Sticky-mid-passive zone (EXP_004 H1.1a). Overridable from the CLI so we can
# re-slice once we see where the field actually clusters — the zone boundaries
# were theory-derived and may not fit the real LLM distribution.
DEFAULT_WTSD_MIN = 0.55
DEFAULT_POSTFLOP_AF_MAX = 0.60
DEFAULT_VPIP_LO = 0.25
DEFAULT_VPIP_HI = 0.50


@dataclass
class PlayerAccumulator:
    """Per-personality counters accumulated in a single pass over the rows."""

    hands_dealt: Set[Tuple[str, int]] = field(default_factory=set)
    hands_vpip: Set[Tuple[str, int]] = field(default_factory=set)
    hands_pfr: Set[Tuple[str, int]] = field(default_factory=set)
    hands_saw_flop: Set[Tuple[str, int]] = field(default_factory=set)
    hands_saw_river: Set[Tuple[str, int]] = field(default_factory=set)
    # per-street aggressive / call counts (checks excluded — standard AF convention)
    street_aggr: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    street_call: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    all_in_count: int = 0
    decisions: int = 0

    def observe(self, key: Tuple[str, int], phase: str, action: str) -> None:
        self.decisions += 1
        self.hands_dealt.add(key)
        if action == "all_in":
            self.all_in_count += 1
        if phase == "PRE_FLOP":
            if action in ("call", "raise", "all_in"):
                self.hands_vpip.add(key)
            if action in ("raise", "all_in"):
                self.hands_pfr.add(key)
        else:  # postflop
            if phase == "FLOP":
                self.hands_saw_flop.add(key)
            elif phase == "RIVER":
                self.hands_saw_river.add(key)
            if action in AGGRESSIVE:
                self.street_aggr[phase] += 1
            elif action in CALL:
                self.street_call[phase] += 1


@dataclass
class PlayerProfile:
    name: str
    hands: int
    vpip: float
    pfr: float
    vpip_pfr_gap: float
    global_af: float
    flop_af: Optional[float]
    turn_af: Optional[float]
    river_af: Optional[float]
    postflop_af: Optional[float]
    wtsd: Optional[float]
    all_in_freq: float
    in_sticky_mid_zone: bool
    detector_label: Optional[str]  # what classify_opponent_archetype returns today
    detector_misses: bool  # in zone AND detector returns None → the blindness


def _af(aggr: int, call: int) -> Optional[float]:
    """Standard AF = aggressive / call. None if no postflop sample at all."""
    if aggr == 0 and call == 0:
        return None
    if call == 0:
        return float(aggr)  # all aggression, no calls — report the raw count
    return aggr / call


def build_profile(
    name: str,
    acc: PlayerAccumulator,
    wtsd_min: float,
    paf_max: float,
    vpip_lo: float,
    vpip_hi: float,
) -> PlayerProfile:
    hands = len(acc.hands_dealt)
    vpip = len(acc.hands_vpip) / hands if hands else 0.0
    pfr = len(acc.hands_pfr) / hands if hands else 0.0

    flop_af = _af(acc.street_aggr["FLOP"], acc.street_call["FLOP"])
    turn_af = _af(acc.street_aggr["TURN"], acc.street_call["TURN"])
    river_af = _af(acc.street_aggr["RIVER"], acc.street_call["RIVER"])
    pf_aggr = sum(acc.street_aggr[s] for s in POSTFLOP_PHASES)
    pf_call = sum(acc.street_call[s] for s in POSTFLOP_PHASES)
    postflop_af = _af(pf_aggr, pf_call)
    # global AF mirrors the legacy field the detector reads: all-street
    # aggressive / call. Preflop raises/calls fold in here, inflating it for
    # players who open a lot then play passively postflop (the Jeff effect).
    global_af = _af(pf_aggr + len(acc.hands_pfr), pf_call + len(acc.hands_vpip))

    saw_flop = len(acc.hands_saw_flop)
    wtsd = (len(acc.hands_saw_river) / saw_flop) if saw_flop >= 5 else None
    all_in_freq = acc.all_in_count / hands if hands else 0.0

    in_zone = (
        wtsd is not None
        and postflop_af is not None
        and wtsd >= wtsd_min
        and postflop_af < paf_max
        and vpip_lo <= vpip <= vpip_hi
    )

    # Run the CURRENT detector with the stats it actually reads today.
    stats = AggregatedOpponentStats(
        hands_observed=hands,
        vpip=vpip,
        pfr=pfr,
        aggression_factor=global_af if global_af is not None else 1.0,
        all_in_frequency=all_in_freq,
        vpip_per_voluntary_opportunity=vpip,
        pfr_per_open_opportunity=pfr,
        aggression_factor_postflop=postflop_af if postflop_af is not None else 1.0,
    )
    label = classify_opponent_archetype(stats)

    return PlayerProfile(
        name=name,
        hands=hands,
        vpip=vpip,
        pfr=pfr,
        vpip_pfr_gap=vpip - pfr,
        global_af=global_af if global_af is not None else float("nan"),
        flop_af=flop_af,
        turn_af=turn_af,
        river_af=river_af,
        postflop_af=postflop_af,
        wtsd=wtsd,
        all_in_freq=all_in_freq,
        in_sticky_mid_zone=in_zone,
        detector_label=label,
        detector_misses=in_zone and label is None,
    )


def load_accumulators(db_path: str, llm_only: bool) -> Dict[str, PlayerAccumulator]:
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        where = "phase IS NOT NULL AND action_taken IS NOT NULL"
        if llm_only:
            # capture_id real (not NULL, not the -1 tiered sentinel) → LLM-driven
            where += " AND capture_id IS NOT NULL AND capture_id <> -1"
        accs: Dict[str, PlayerAccumulator] = defaultdict(PlayerAccumulator)
        cur = conn.execute(
            f"SELECT player_name, game_id, hand_number, phase, action_taken "
            f"FROM player_decision_analysis WHERE {where}"
        )
        for name, game_id, hand_number, phase, action in cur:
            if name is None or game_id is None or hand_number is None:
                continue
            accs[name].observe((game_id, hand_number), phase, action)
        return accs
    finally:
        conn.close()


def _fmt(v: Optional[float]) -> str:
    return "  n/a" if v is None else f"{v:5.2f}"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=DEFAULT_DB, help="path to poker_games.db (read-only)")
    p.add_argument("--min-hands", type=int, default=200, help="min hands to include a personality")
    p.add_argument(
        "--all-controllers",
        action="store_true",
        help="include tiered/sentinel rows too (default: LLM-driven play only)",
    )
    p.add_argument("--wtsd-min", type=float, default=DEFAULT_WTSD_MIN)
    p.add_argument("--postflop-af-max", type=float, default=DEFAULT_POSTFLOP_AF_MAX)
    p.add_argument("--vpip-lo", type=float, default=DEFAULT_VPIP_LO)
    p.add_argument("--vpip-hi", type=float, default=DEFAULT_VPIP_HI)
    p.add_argument("--out", default=None, help="optional CSV output path")
    args = p.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f"DB not found: {args.db}")

    accs = load_accumulators(args.db, llm_only=not args.all_controllers)
    profiles = [
        build_profile(name, acc, args.wtsd_min, args.postflop_af_max, args.vpip_lo, args.vpip_hi)
        for name, acc in accs.items()
        if len(acc.hands_dealt) >= args.min_hands
    ]
    profiles.sort(key=lambda pr: (not pr.in_sticky_mid_zone, -(pr.wtsd or 0)))

    regime = "ALL controllers" if args.all_controllers else "LLM-driven only"
    zone = (
        f"WtSD>={args.wtsd_min} & postflop_AF<{args.postflop_af_max} "
        f"& VPIP in [{args.vpip_lo},{args.vpip_hi}]"
    )
    print(f"\n=== LLM personality field profile  ({regime}, >={args.min_hands} hands) ===")
    print(f"Sticky-mid zone: {zone}\n")
    print(
        f"{'personality':<24}{'hands':>6}{'VPIP':>6}{'PFR':>6}{'gap':>6}"
        f"{'gAF':>6}{'pfAF':>6}{'flop':>6}{'turn':>6}{'rivr':>6}{'WtSD':>6}"
        f"  {'detector':<16}{'ZONE':>5}"
    )
    for pr in profiles:
        zflag = "★" if pr.in_sticky_mid_zone else ""
        miss = " (MISS)" if pr.detector_misses else ""
        print(
            f"{pr.name[:23]:<24}{pr.hands:>6}{pr.vpip:>6.2f}{pr.pfr:>6.2f}{pr.vpip_pfr_gap:>6.2f}"
            f"{pr.global_af:>6.2f}{_fmt(pr.postflop_af):>6}{_fmt(pr.flop_af):>6}"
            f"{_fmt(pr.turn_af):>6}{_fmt(pr.river_af):>6}{_fmt(pr.wtsd):>6}"
            f"  {str(pr.detector_label or '—'):<16}{zflag:>5}{miss}"
        )

    # --- Summary: the H1 axes ---
    n = len(profiles)
    in_zone = [pr for pr in profiles if pr.in_sticky_mid_zone]
    missed = [pr for pr in in_zone if pr.detector_misses]
    detected_any = [pr for pr in profiles if pr.detector_label is not None]
    vpips = sorted(pr.vpip for pr in profiles)
    wtsds = sorted(pr.wtsd for pr in profiles if pr.wtsd is not None)

    def _median(xs):
        return xs[len(xs) // 2] if xs else float("nan")

    print(f"\n=== Summary ({n} personalities) ===")
    print(f"  field VPIP  median {_median(vpips):.2f}  (min {vpips[0]:.2f} / max {vpips[-1]:.2f})")
    print(f"  field WtSD  median {_median(wtsds):.2f}" if wtsds else "  field WtSD  n/a")
    print(f"  H1.1a prevalence : {len(in_zone)}/{n} = {100*len(in_zone)/n:.0f}% in sticky-mid zone (bar: >=10%)")
    if in_zone:
        rate = 100 * len(missed) / len(in_zone)
        print(f"  H1.1b detector-miss: {len(missed)}/{len(in_zone)} = {rate:.0f}% of in-zone players return None (bar: >=80%)")
    print(f"  current detector fires on {len(detected_any)}/{n} personalities total")

    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(
                ["personality", "hands", "vpip", "pfr", "vpip_pfr_gap", "global_af",
                 "flop_af", "turn_af", "river_af", "postflop_af", "wtsd",
                 "all_in_freq", "in_sticky_mid_zone", "detector_label", "detector_misses"]
            )
            for pr in profiles:
                w.writerow(
                    [pr.name, pr.hands, f"{pr.vpip:.4f}", f"{pr.pfr:.4f}",
                     f"{pr.vpip_pfr_gap:.4f}", f"{pr.global_af:.4f}",
                     pr.flop_af, pr.turn_af, pr.river_af, pr.postflop_af, pr.wtsd,
                     f"{pr.all_in_freq:.4f}", pr.in_sticky_mid_zone,
                     pr.detector_label or "", pr.detector_misses]
                )
        print(f"\nWrote {len(profiles)} rows → {args.out}")


if __name__ == "__main__":
    main()
