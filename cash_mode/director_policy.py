"""Held Director rake policy — the slow-moving cash-rake schedule, cached.

The reserve-gated rake schedule (`economy_flags.resolve_rake_params` →
`economy_signal.cash_rake_schedule`) keys off the bank's reserve band, which only
drifts across many hands. Recomputing it PER HAND means a `signal()` ledger
aggregate scan on the hot rake path for a value that barely moves. This module
HOLDS the resolved schedule for a `POLICY_WINDOW_SECONDS` window and recomputes it
only in the lobby refresh — the same throttle discipline as the field-inequality
read (`cash_mode/field_inequality.py`), and the same rationale: the Director
steers the economy slowly, so its levers don't need to re-derive every hand.

Only the cash-RAKE schedule is held. Vice and side-hustle stay per-tick (they are
the always-on bounds that must react immediately); the casino lifecycle is already
window-stable. Gated by `economy_flags.DIRECTOR_POLICY_HOLD` (default OFF) — when
off, `resolve_rake_params` computes live every call exactly as before.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Held schedule, as `resolve_rake_params` returns it.
RakeParams = Tuple[Optional[frozenset], Optional[float]]

# sandbox_id -> (last_computed, (stake_big_blinds, rate)). Module-level cache: a
# slow-moving steering signal, so a process restart just recomputes it on the
# next refresh (no persistence needed). Mirrors `field_inequality._cache`.
_cache: Dict[str, Tuple[datetime, RakeParams]] = {}


def refresh_director_policy(sandbox_id: str, chip_ledger_repo, now: datetime) -> RakeParams:
    """Recompute `sandbox_id`'s held rake schedule only if the window has lapsed.

    Cheap when fresh (a timestamp compare); does the live `resolve_rake_params`
    compute (the `signal()` scan) only when the held value is older than
    `POLICY_WINDOW_SECONDS`. Safe to call every lobby refresh — the throttle keeps
    the actual recompute slow. The live compute goes through
    `resolve_rake_params(..., _fresh=True)` so the held value and the per-hand
    read are the same code path (inequality adjustment included). Returns the
    (possibly cached) `(stake_big_blinds, rate)`.
    """
    from cash_mode import economy_flags as _eflags

    cached = _cache.get(sandbox_id)
    if cached is not None:
        if (now - cached[0]).total_seconds() < _eflags.POLICY_WINDOW_SECONDS:
            return cached[1]
    params = _eflags.resolve_rake_params(chip_ledger_repo, sandbox_id, _fresh=True)
    _cache[sandbox_id] = (now, params)
    return params


def director_rake_policy(sandbox_id: str) -> Optional[RakeParams]:
    """Read the held `(stake_big_blinds, rate)` for `sandbox_id`.

    Returns None when nothing has been cached yet (no lobby refresh has run for
    this sandbox in this process) — the caller falls through to a live compute so
    the opening hands still rake correctly.
    """
    cached = _cache.get(sandbox_id)
    return cached[1] if cached else None


def reset_cache() -> None:
    """Clear the cache — for tests and sim setup."""
    _cache.clear()
