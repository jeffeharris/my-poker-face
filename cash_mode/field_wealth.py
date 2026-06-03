"""Field-relative liquid-wealth snapshot for the closed-economy levers.

Single source of truth for "how rich is each AI relative to the field,
right now." Computed ONCE per lobby tick and passed (as an immutable
value) to the three wealth levers — real vice, side-hustle, and
grinder-hunger — so they all measure wealth the same way instead of
each keying off the AI's own starting bankroll.

"Liquid net worth" = off-table bankroll (regen-projected) + chips on a
table seat. Receivables/outstanding (staking) are deliberately EXCLUDED:
the levers can only act on liquid chips (vice can only drain bankroll,
side-hustle pays into bankroll), and excluding them also keeps the
measure fully per-sandbox (the stakes table is global). See
docs and reference_closed_economy_system_map.

Conservation note: this snapshot is READ-ONLY. It never moves chips —
it only changes where a lever reads its reference point. No ledger
entries, so it is drift-neutral by construction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Set, Tuple

logger = logging.getLogger(__name__)


def _collect_seat_chips(cash_table_repo, sandbox_id: str) -> Dict[str, int]:
    """Sum each AI's chips across all table seats in the sandbox.

    Keyed by raw personality_id (matching the bankroll repo's keys).
    Returns {} on any error so the caller degrades to bankroll-only.
    """
    out: Dict[str, int] = {}
    if cash_table_repo is None:
        return out
    try:
        for table in cash_table_repo.list_all_tables(sandbox_id=sandbox_id):
            for slot in table.seats or []:
                if slot.get("kind") != "ai":
                    continue
                pid = slot.get("personality_id")
                if pid:
                    out[pid] = out.get(pid, 0) + int(slot.get("chips", 0) or 0)
    except Exception as exc:  # noqa: BLE001 — best-effort; degrade gracefully
        logger.warning("[FIELD_WEALTH] seat-chip scan failed: %s", exc)
        return out
    return out


def _percentile(sorted_values: Tuple[int, ...], q: float) -> float:
    """Linear-interpolated q-percentile of a pre-sorted sequence."""
    n = len(sorted_values)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_values[0])
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    w = pos - lo
    return sorted_values[lo] * (1 - w) + sorted_values[hi] * w


@dataclass(frozen=True)
class FieldWealthSnapshot:
    """Immutable per-tick view of the field's liquid wealth.

    `liquid_chips[pid]` = projected bankroll + seat stack, for every
    non-fish AI in the sandbox. Construct directly with an arbitrary
    `liquid_chips` dict in tests; production builds it via
    `build_field_wealth_snapshot`.
    """

    liquid_chips: Dict[str, int]
    # Pre-sorted liquid values for O(1)/O(log n) stat queries. Defaults
    # so tests can construct with just `liquid_chips`.
    _sorted: Tuple[int, ...] = ()

    @classmethod
    def from_liquid(cls, liquid_chips: Dict[str, int]) -> FieldWealthSnapshot:
        return cls(
            liquid_chips=dict(liquid_chips),
            _sorted=tuple(sorted(liquid_chips.values())),
        )

    def is_empty(self) -> bool:
        return not self.liquid_chips

    def median(self) -> int:
        """Median liquid wealth across the field (0 if empty)."""
        n = len(self._sorted)
        if n == 0:
            return 0
        mid = n // 2
        if n % 2:
            return int(self._sorted[mid])
        return int((self._sorted[mid - 1] + self._sorted[mid]) / 2)

    def percentile(self, q: float) -> float:
        """q-percentile liquid value (q in [0,1])."""
        return _percentile(self._sorted, q)

    def concentration(self, pid: str) -> float:
        """An AI's liquid wealth as a multiple of the field median.

        Returns 0.0 when the field median is non-positive or the AI has
        no liquid entry. This is the input the vice tax compares against
        its concentration floor.
        """
        med = self.median()
        if med <= 0:
            return 0.0
        return self.liquid_chips.get(pid, 0) / med

    def pct_rank(self, pid: str) -> float:
        """Fraction of the field with liquid <= this AI's liquid, [0,1].

        0.0 if the AI isn't in the field. Used by side-hustle and
        grinder-hunger to gate on "bottom X% of the field."
        """
        n = len(self._sorted)
        if n == 0 or pid not in self.liquid_chips:
            return 0.0
        v = self.liquid_chips[pid]
        # Count values <= v (upper bound) / n.
        lo, hi = 0, n  # upper-bound bisect: count of values <= v
        while lo < hi:
            mid = (lo + hi) // 2
            if self._sorted[mid] <= v:
                lo = mid + 1
            else:
                hi = mid
        return lo / n


def build_field_wealth_snapshot(
    *,
    bankroll_repo,
    cash_table_repo,
    sandbox_id: str,
    now: datetime,
    fish_ids: Set[str],
) -> Optional[FieldWealthSnapshot]:
    """Build the per-tick liquid-wealth snapshot for non-fish AIs.

    One bankroll projection per AI + one seat scan. Returns None on
    error so callers fall back to own-start behaviour. Fish are excluded
    (pool-funded; not field members).
    """
    if bankroll_repo is None or sandbox_id is None:
        return None
    try:
        seat_chips = _collect_seat_chips(cash_table_repo, sandbox_id)
        pids = bankroll_repo.iter_personality_ids_with_bankrolls(sandbox_id=sandbox_id)
        liquid: Dict[str, int] = {}
        for pid in pids:
            if pid in fish_ids:
                continue
            bank = bankroll_repo.load_ai_bankroll_current(pid, sandbox_id=sandbox_id, now=now)
            liquid[pid] = int(bank or 0) + seat_chips.get(pid, 0)
        return FieldWealthSnapshot.from_liquid(liquid)
    except Exception as exc:  # noqa: BLE001 — best-effort; degrade to own-start
        logger.warning("[FIELD_WEALTH] snapshot build failed: %s", exc)
        return None
