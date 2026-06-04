"""Field wealth-inequality signal for the Director's instrument choice.

Vice (drains the rich) and rake (even skim) are complementary refill levers. The
Director leans on **vice** when a few AIs are running away from the pack (a
top-heavy field — vice's concentration gate already self-selects for this) and on
**rake** when the top is flat (no runaway to drain, so an even skim is the right
tool). This module supplies the inequality read the rake side needs.

The signal is a `p90 / median` ratio over AI bankrolls: ~1.0 = perfectly flat,
higher = more top-heavy. It is recomputed **at most once per
`INEQUALITY_RECOMPUTE_SECONDS` per sandbox and CACHED** — the Director steers the
economy slowly, it does not re-read the whole wealth distribution every hand or
every lobby tick. The bankroll scan is the cost; the per-hand rake just reads the
cached scalar via `field_inequality()`.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# sandbox_id -> (last_computed, factor-or-None). Module-level cache: it is a
# slow-moving steering signal, so a process restart just recomputes it on the
# next refresh (no persistence needed).
_cache: Dict[str, Tuple[datetime, Optional[float]]] = {}


def refresh_field_inequality(sandbox_id: str, bankroll_repo, now: datetime) -> Optional[float]:
    """Recompute `sandbox_id`'s inequality factor only if the cache is stale.

    Cheap when fresh (a timestamp compare); does the bankroll scan only when the
    cached value is older than `INEQUALITY_RECOMPUTE_SECONDS`. Safe to call every
    lobby refresh — the throttle keeps the actual recompute slow. Returns the
    (possibly cached) factor, or None when it can't be computed.
    """
    from cash_mode import economy_flags as _eflags

    cached = _cache.get(sandbox_id)
    if cached is not None:
        if (now - cached[0]).total_seconds() < _eflags.INEQUALITY_RECOMPUTE_SECONDS:
            return cached[1]
    factor = _compute_inequality(bankroll_repo, sandbox_id)
    _cache[sandbox_id] = (now, factor)
    return factor


def field_inequality(sandbox_id: str) -> Optional[float]:
    """Read the cached inequality factor (None if never computed for `sandbox_id`)."""
    cached = _cache.get(sandbox_id)
    return cached[1] if cached else None


def _compute_inequality(bankroll_repo, sandbox_id: str) -> Optional[float]:
    """p90 / median over positive AI bankrolls. None if too few / on error."""
    try:
        chips = sorted(
            int(c)
            for c in bankroll_repo.list_all_ai_bankroll_chips(sandbox_id=sandbox_id)
            if c and c > 0
        )
    except Exception as exc:
        logger.warning("[INEQUALITY] bankroll scan failed: %s", exc)
        return None
    if len(chips) < 2:
        return None
    median = chips[len(chips) // 2]
    if median <= 0:
        return None
    p90 = chips[min(len(chips) - 1, int(len(chips) * 0.9))]
    return p90 / median


def reset_cache() -> None:
    """Clear the cache — for tests and sim setup."""
    _cache.clear()
