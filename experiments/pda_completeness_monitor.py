#!/usr/bin/env python3
"""Parent(hand) <-> children(decisions) completeness monitor.

The Archetype Review / player stats read per-decision rows from
`player_decision_analysis` (PDA) and join hand-level outcomes from
`hand_history` on the natural key (game_id, hand_number). PDA is written
per-decision *during* a hand; `hand_history` is written at hand *end*. When a
code path plays a hand but skips the PDA write while still recording
`hand_history`, decisions silently vanish — the Fast-Forward bug
(`fix/ff-pda-decision-logging`) was exactly this: hand_history parents whose
decision children were dropped, producing a one-directional postflop gap.

This tool audits that invariant against a real DB. It is the standing detector
for that bug *class* (FF was the known instance; others could exist). Read-only.
CLI tool — not collected by pytest; the detection logic lives in
`analyze_completeness` and is unit-tested in tests/test_pda_completeness_monitor.py.

Usage::

    python -m experiments.pda_completeness_monitor --db /app/data/poker_games.db --mode cash
    # exits 1 if the postflop-gap rate exceeds --max-postflop-gap-pct (default 1.0)
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict

_POSTFLOP = {"FLOP", "TURN", "RIVER"}
# PDA rows are deduped on (game_id, player, hand, phase, node_key, board, action)
# before comparing — mirrors archetype_review_routes._aggregate (the analyzer
# double-logs; collapse to one logical decision first).


def _mode_like(mode: str) -> str:
    if mode == "tournament":
        return "tourney-%"
    return "cash-%"  # default


def analyze_completeness(conn: sqlite3.Connection, mode: str = "cash") -> dict:
    """Compare PDA vs hand_history on (game_id, hand_number). Returns a report
    dict of the parent<->children gaps. Pure read; no schema assumptions beyond
    the two tables existing."""
    like = _mode_like(mode)

    # hand_history: phases-with-an-action per (game,hand,player)
    hh = defaultdict(lambda: defaultdict(set))
    hh_hands = set()
    import json

    for g, h, aj in conn.execute(
        "SELECT game_id, hand_number, actions_json FROM hand_history WHERE game_id LIKE ?", (like,)
    ):
        hh_hands.add((g, h))
        for a in json.loads(aj or "[]"):
            hh[(g, h)][a["player_name"]].add(a["phase"])

    # PDA (deduped): phases-with-a-decision per (game,hand,player)
    pda = defaultdict(lambda: defaultdict(set))
    pda_hands = set()
    seen = set()
    for g, p, h, ph, node, board, act in conn.execute(
        "SELECT game_id, player_name, hand_number, phase, COALESCE(preflop_node_key,''), "
        "COALESCE(community_cards,''), action_taken FROM player_decision_analysis WHERE game_id LIKE ?",
        (like,),
    ):
        key = (g, p, h, ph, node, board, act)
        if key in seen:
            continue
        seen.add(key)
        pda_hands.add((g, h))
        pda[(g, h)][p].add(ph)

    both = hh_hands & pda_hands
    player_hands = 0
    postflop_gap = 0  # player reached postflop in HH but PDA has no postflop for them
    for gh in both:
        for pl in set(hh[gh]) | set(pda[gh]):
            player_hands += 1
            hp = hh[gh].get(pl, set())
            pp = pda[gh].get(pl, set())
            if (hp & _POSTFLOP) and not (pp & _POSTFLOP):
                postflop_gap += 1

    rate = (100.0 * postflop_gap / player_hands) if player_hands else 0.0
    return {
        "mode": mode,
        "hh_hands": len(hh_hands),
        "pda_hands": len(pda_hands),
        "intersection": len(both),
        "hh_only_hands": len(hh_hands - pda_hands),  # hands with outcomes but no decisions
        "pda_only_hands": len(pda_hands - hh_hands),  # decisions but no recorded outcome
        "player_hands": player_hands,
        "postflop_gap": postflop_gap,  # the FF-bug-class signal
        "postflop_gap_pct": round(rate, 2),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="/app/data/poker_games.db")
    ap.add_argument("--mode", default="cash", choices=["cash", "tournament"])
    ap.add_argument("--max-postflop-gap-pct", type=float, default=1.0)
    args = ap.parse_args(argv)

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    r = analyze_completeness(conn, args.mode)
    print(f"PDA<->hand_history completeness ({r['mode']}) @ {args.db}")
    print(f"  hand_history hands : {r['hh_hands']}")
    print(f"  PDA hands          : {r['pda_hands']}")
    print(f"  intersection       : {r['intersection']}")
    print(f"  hands with outcomes but NO decisions (FF-bug class): {r['hh_only_hands']}")
    print(f"  decisions but no recorded outcome                  : {r['pda_only_hands']}")
    print(f"  player-hands compared : {r['player_hands']}")
    print(f"  POSTFLOP gap (HH has, PDA missing): {r['postflop_gap']} ({r['postflop_gap_pct']}%)")
    over = r["postflop_gap_pct"] > args.max_postflop_gap_pct
    print(f"  -> {'FAIL' if over else 'OK'} (threshold {args.max_postflop_gap_pct}%)")
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main())
