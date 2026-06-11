#!/usr/bin/env python3
"""Render the preflop strategy charts as human-readable 13x13 grids.

This is the generator behind the grid blocks in
``docs/POKER_CHARTS_REVIEW.md`` (the shareable chart-review packet). Re-run it
whenever the chart JSON changes so the packet's grids stay in sync with the live
data — the grids are derived from ``poker/strategy/data/preflop_*_6max.json``, so
a chart regen (e.g. a vs_3bet/vs_4bet rebuild) is reflected automatically.

The grids show the DOMINANT action per hand + its weight. Pairs sit on the
diagonal, suited hands above it, offsuit below; rows/cols run A->2. A ``·`` cell
is a (near-)pure fold. The action vocabulary shifts by scenario and depth
(e.g. 3-bets become jams at 25bb), so each block prints its own legend.

Note: dominant-action grids cannot show mixing detail. Some structural facts —
e.g. that the 100bb vs_3bet 4-bet is *suited-only* — are invisible here and must
be described in prose; see the §3/§4 notes in the packet.

Usage (no deps beyond the stdlib; reads JSON directly, no DB / container needed):

    python3 experiments/render_chart_grids.py 100        # 100bb: RFI + vs_open samples + all vs_3bet + all vs_4bet
    python3 experiments/render_chart_grids.py depth       # 50bb + 25bb facing grids
    python3 experiments/render_chart_grids.py all          # both, concatenated
    python3 experiments/render_chart_grids.py all > /tmp/grids.md

Paste the relevant section into the packet (replacing the matching block), or
diff against the committed doc to confirm the grids are current.
"""

from __future__ import annotations

import json
import os
import sys

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "poker",
    "strategy",
    "data",
)

RANKS = ["A", "K", "Q", "J", "T", "9", "8", "7", "6", "5", "4", "3", "2"]
HEADER = "     " + " ".join(f"{x:>4}" for x in RANKS)


def _load(name: str) -> dict:
    with open(os.path.join(_DATA_DIR, name)) as f:
        return json.load(f)


def hand_at(r: int, c: int) -> str:
    """Hand label for grid cell (row r, col c). Suited above the diagonal,
    offsuit below (high card first), pair on it."""
    hi, lo = RANKS[r], RANKS[c]
    if r == c:
        return hi + lo
    if r < c:
        return hi + lo + "s"
    return lo + hi + "o"


def _combos(hand: str) -> int:
    if len(hand) == 2:
        return 6
    return 4 if hand.endswith("s") else 12


# ── action -> display-letter classifiers (one per scenario/regime) ──────────
# The same scenario uses different action keys at different depths (e.g. a
# vs_open 3-bet `raise_3x` at 100bb becomes a `jam` at 25bb), so each classifier
# covers every key it might see and collapses them to a single letter.


def _vsopen_letter(action: str) -> str:
    if action == "jam":
        return "J"
    if action.startswith("raise"):
        return "R"  # 3-bet
    if action == "call":
        return "C"
    return "?"


def _vs3bet_letter(action: str) -> str:
    if action == "jam":
        return "J"
    if action.startswith("raise"):
        return "4"  # 4-bet
    if action == "call":
        return "C"
    return "?"


def _vs4bet_letter(action: str) -> str:
    if action == "jam":
        return "J"
    if action == "call":
        return "C"
    return "?"


def rfi_grid(node: dict) -> str:
    """RFI is open-or-fold: render the open % per hand (`·` = pure fold)."""
    lines = [HEADER]
    for r in range(13):
        cells = []
        for c in range(13):
            v = node.get(hand_at(r, c), {})
            raise_f = sum(f for a, f in v.items() if a.startswith("raise") or a == "jam")
            cells.append("   ·" if raise_f <= 0.005 else f"{round(raise_f * 100):>4}")
        lines.append(f"{RANKS[r]:>3}  " + " ".join(cells))
    return "\n".join(lines)


def rfi_rate(node: dict) -> float:
    num = den = 0.0
    for h, v in node.items():
        cb = _combos(h)
        raise_f = sum(f for a, f in v.items() if a.startswith("raise") or a == "jam")
        num += cb * raise_f
        den += cb
    return 100 * num / den if den else 0.0


def dom_grid(node: dict, classify) -> str:
    """Render the dominant action letter + its % per hand; `·` = fold dominant."""
    lines = [HEADER]
    for r in range(13):
        cells = []
        for c in range(13):
            v = node.get(hand_at(r, c), {})
            agg: dict = {}
            for a, f in v.items():
                lab = "·" if a == "fold" else classify(a)
                agg[lab] = agg.get(lab, 0.0) + f
            best = max(agg.items(), key=lambda x: x[1]) if agg else ("·", 1.0)
            cells.append("   ·" if best[0] == "·" else f"{best[0]}{round(best[1] * 100):>3}")
        lines.append(f"{RANKS[r]:>3}  " + " ".join(cells))
    return "\n".join(lines)


def _block(title: str, grid: str) -> str:
    return f"\n**{title}**\n\n```\n{grid}\n```"


def render_100bb() -> str:
    d = _load("preflop_100bb_6max.json")
    out = ["### 1. RFI (open-raise) frequencies — all positions\n"]
    out.append("Cell = % of the time the hand opens (raise to 2.5bb); `·` = pure fold.")
    for pos in ["UTG", "HJ", "CO", "BTN", "SB"]:
        out.append(
            _block(
                f"{pos} RFI — {rfi_rate(d['rfi'][pos]):.1f}% of hands open", rfi_grid(d["rfi"][pos])
            )
        )

    out.append("\n### 2. Facing an open — sample defense nodes (`vs_open`)\n")
    out.append("Dominant action + %: `R`=3-bet, `C`=call, `·`=fold.")
    for n in ["BB_vs_BTN", "BTN_vs_UTG"]:
        out.append(_block(n.replace("_", " "), dom_grid(d["vs_open"][n], _vsopen_letter)))

    out.append("\n### 3. Our open got 3-bet — all 15 nodes (`vs_3bet`)\n")
    out.append("Dominant action + %: `4`=4-bet (~2.2×), `C`=call, `·`=fold. (No jam at 100bb.)")
    for n in d["vs_3bet"]:
        out.append(_block(n.replace("_", " "), dom_grid(d["vs_3bet"][n], _vs3bet_letter)))

    out.append("\n### 4. Our 3-bet got 4-bet — all 15 nodes (`vs_4bet`)\n")
    out.append("Dominant action + %: `J`=jam, `C`=call, `·`=fold.")
    for n in d["vs_4bet"]:
        out.append(_block(n.replace("_", " "), dom_grid(d["vs_4bet"][n], _vs4bet_letter)))
    return "\n".join(out)


def render_depth() -> str:
    out = []
    for depth in (50, 25):
        d = _load(f"preflop_{depth}bb_6max.json")
        out.append(f"\n## Depth charts — {depth}bb facing grids\n")
        out.append(
            "Same 15-node skeleton as 100bb, re-derived per cell by the depth rules. "
            "RFI rows are identical to the 100bb chart (not re-rendered). "
            "Action vocabulary collapses toward jams as stacks shorten."
        )
        out.append(f"\n### {depth}bb — `vs_open`  ·  `R`=3-bet `J`=jam `C`=call `·`=fold\n")
        for n in d["vs_open"]:
            out.append(_block(n.replace("_", " "), dom_grid(d["vs_open"][n], _vsopen_letter)))
        out.append(f"\n### {depth}bb — `vs_3bet`  ·  `4`=4-bet `J`=jam `C`=call `·`=fold\n")
        for n in d["vs_3bet"]:
            out.append(_block(n.replace("_", " "), dom_grid(d["vs_3bet"][n], _vs3bet_letter)))
        out.append(f"\n### {depth}bb — `vs_4bet`  ·  `J`=jam `C`=call `·`=fold\n")
        for n in d["vs_4bet"]:
            out.append(_block(n.replace("_", " "), dom_grid(d["vs_4bet"][n], _vs4bet_letter)))
    return "\n".join(out)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode == "100":
        print(render_100bb())
    elif mode == "depth":
        print(render_depth())
    elif mode == "all":
        print(render_100bb())
        print(render_depth())
    else:
        sys.exit(f"unknown mode {mode!r}; use one of: 100 | depth | all")


if __name__ == "__main__":
    main()
