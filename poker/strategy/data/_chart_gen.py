"""Shared primitives for the per-node preflop chart generators.

`build_vs_open`, `build_vs3bet_defense`, and `build_vs4bet_defense` each grew an
identical copy of these hand-classification / distribution helpers. Hoisted here
(rule-of-three) so there is one definition of `_playability`, `_norm`, etc.

Pure functions only — no I/O, no chart-specific policy (weights, pools, targets
stay in the individual build scripts). Importing this module must not change any
generated chart: re-running the full regen cascade after the hoist is a no-op.
"""

from __future__ import annotations

from typing import Dict

RANKS = "AKQJT98765432"


def _is_pair(h: str) -> bool:
    return len(h) == 2


def _is_suited(h: str) -> bool:
    return len(h) == 3 and h[2] == "s"


def _playability(h: str) -> float:
    """Cheap suitedness + connectedness + high-card score in ~[0,1].

    Only orders the *call* range within the equity-cleared set, so precision
    doesn't matter much — flatting realizes through playability (suited,
    connected), which pure all-in-equity ordering under-weights.
    """
    hi, lo = RANKS.index(h[0]), RANKS.index(h[1])
    high_card = (12 - hi) / 12 * 0.4               # A-high ~0.4 … 2-high ~0
    if _is_pair(h):
        conn = 0.2
    else:
        conn = max(0, 4 - abs(hi - lo)) / 4 * 0.3  # connected/small-gap bonus
    suit = 0.3 if _is_suited(h) else 0.0
    return high_card + conn + suit


def _norm(d: Dict[str, float]) -> Dict[str, float]:
    s = sum(d.values())
    return {k: round(v / s, 4) for k, v in d.items() if v > 0} if s > 0 else {"fold": 1.0}


def _open_range(rfi_node: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    """Villain's open range as {hand: open_weight} from an rfi node."""
    return {
        h: d.get("raise_2.5bb", 0.0)
        for h, d in rfi_node.items()
        if d.get("raise_2.5bb", 0.0) > 0
    }
