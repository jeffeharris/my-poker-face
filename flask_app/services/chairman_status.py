"""Economy-chairman status — the Director's current stage, levers, and lock.

A read-only projection of the economy-signal chairman (`core.economy.economy_signal`)
for the admin Chip Economy dashboard. It answers three questions an operator asks
of the thermostat:

  1. **What stage is the bank in right now?** — the live `EconomyState` snapshot
     (reserves / holdings / ratio / regime) bucketed onto the canonical reserve
     ladder (critical → low → climbing → trigger).
  2. **What stages COULD it be in, and what does each one do?** — the full band
     ladder, each band annotated with the policy it dictates (rake tiers + rate,
     vice refill intensity, whether a Main Event fires). The per-band lever values
     are derived by evaluating the SAME pure policy functions the live economy
     uses, so the dashboard can never drift from the real control law.
  3. **How long is the current policy locked in?** — the Director steers slowly:
     the cash-rake schedule is held for `POLICY_WINDOW_SECONDS` and a Main Event
     can't re-fire within `MAIN_EVENT_COOLDOWN_SECONDS`. We surface both windows
     plus, when the policy hold is active, this sandbox's last-recompute time and
     the seconds remaining on the held schedule.

Everything here is pure/derived (no chip moves) — it reads ONE `signal()` snapshot
and runs the pure policy functions over it, mirroring the chairman discipline of a
single snapshot per decision. Spec: docs/plans/PROD_STARTING_CONDITIONS.md §1.2–1.6.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from core.economy import economy_signal as sig

# --- Band ladder ----------------------------------------------------------
#
# The four stages the chairman can be in, as half-open reserve-ratio intervals
# on the canonical RESERVE_* ladder. `ratio_max=None` is the open-topped trigger
# band. Representative ratios (band midpoints; the trigger uses a point just above
# its edge) are fed through the real policy functions so each band advertises its
# ACTUAL levers, not a hand-copied table.
_BANDS = (
    ('critical', 'Critical', 0.0, sig.RESERVE_CRITICAL),
    ('low', 'Low', sig.RESERVE_CRITICAL, sig.RESERVE_HEALTHY),
    ('climbing', 'Climbing', sig.RESERVE_HEALTHY, sig.RESERVE_TRIGGER),
    ('trigger', 'Trigger', sig.RESERVE_TRIGGER, None),
)

_BAND_BLURB = {
    'critical': 'Bank starved. Rake widest + top rate; vice refilling at full intensity. No tournaments.',
    'low': 'Bank short. Rake adds the $200 tier at the mid rate; vice still at full intensity. No tournaments.',
    'climbing': 'Bank recovering. Rake back to $1000-only at base rate; vice tapers full→off as reserves build toward a Main Event.',
    'trigger': 'Bank flush. A Main Event fires and drains reserves back to the healthy floor; vice eases off as a brake.',
}


def _band_for_ratio(ratio: float) -> str:
    """Bucket a reserves/holdings ratio onto the band ladder."""
    if ratio < sig.RESERVE_CRITICAL:
        return 'critical'
    if ratio < sig.RESERVE_HEALTHY:
        return 'low'
    if ratio < sig.RESERVE_TRIGGER:
        return 'climbing'
    return 'trigger'


def _probe_ratio(lo: float, hi: Optional[float]) -> float:
    """A representative ratio inside [lo, hi) to evaluate that band's levers at.

    Midpoint for bounded bands; a touch above the edge for the open-topped
    trigger band (so it reads as "at the trigger", where vice is ~half-on).
    """
    if hi is None:
        return lo + 0.001
    return (lo + hi) / 2.0


def _levers_at(ratio: float) -> dict:
    """Evaluate the chairman's pure policy functions at a given ratio.

    Builds a throwaway `EconomyState` carrying only the ratio (the policy
    functions key off `ratio`/`regime`, not the absolute reserves), so this works
    for both the live snapshot and the synthetic per-band probes.
    """
    state = sig.EconomyState(
        reserves=0,
        holdings=0,
        ratio=ratio,
        regime=sig._classify(ratio),
    )
    rake = sig.cash_rake_schedule(state)
    from cash_mode.ai_vice_spending import reserve_vice_multiplier

    return {
        'rake': {
            'tiers': sorted(rake.stake_big_blinds, reverse=True),
            'rate': rake.rate,
        },
        'vice_multiplier': round(reserve_vice_multiplier(ratio), 3),
        # A Main Event is armed whenever the ratio clears the trigger (the cooldown
        # is a separate, time-based gate surfaced under policy_lock).
        'tournament_armed': ratio >= sig.RESERVE_TRIGGER,
    }


def _whale_status(state) -> dict:
    """The 5th lever: would the chairman fund a whale at the current reserves?

    Reports, per whale-eligible stake, the worst-case prefund cost and whether
    `can_fund_whale` clears it now — plus whether a live whale would be recalled
    (`should_recall_whale`) and whether the gate is actually wired
    (`WHALE_RESERVE_GATED`; when off, the live system still uses the legacy absolute
    watermarks and this block is advisory — what the chairman WOULD decide).
    """
    from cash_mode import economy_flags as eflags
    from cash_mode.casino_provisioning import WHALE_POOL_THRESHOLDS, WHALE_PREFUND_MAX_MULT
    from cash_mode.stakes_ladder import STAKES_ORDER, table_buy_in_window

    cold = state.holdings <= 0
    stakes = []
    # Biggest eligible stake first — the order the resolver prefers.
    for label in [s for s in reversed(STAKES_ORDER) if s in WHALE_POOL_THRESHOLDS]:
        max_buy_in = table_buy_in_window(label)[2]
        cost = int(max_buy_in * WHALE_PREFUND_MAX_MULT)
        stakes.append(
            {
                'stake': label,
                'prefund_cost': cost,
                'can_fund': (not cold) and sig.can_fund_whale(state, prefund_cost=cost),
            }
        )
    return {
        'gated': bool(eflags.WHALE_RESERVE_GATED),
        'recall_now': (not cold) and sig.should_recall_whale(state),
        'stakes': stakes,
    }


def _policy_lock(sandbox_id: Optional[str], now: datetime) -> dict:
    """The control-law lock windows, plus this sandbox's live held-schedule clock.

    `window_seconds` / `tournament_cooldown_seconds` / `registration_window_seconds`
    are the static control constants. When `DIRECTOR_POLICY_HOLD` is on and a
    schedule has been computed for this sandbox in this process, also report when
    it was last recomputed and how many seconds remain before the next recompute.
    """
    from cash_mode import director_policy, economy_flags as eflags

    lock = {
        'hold_enabled': bool(eflags.DIRECTOR_POLICY_HOLD),
        'window_seconds': int(eflags.POLICY_WINDOW_SECONDS),
        'tournament_cooldown_seconds': int(sig.MAIN_EVENT_COOLDOWN_SECONDS),
        'registration_window_seconds': int(sig.MAIN_EVENT_REGISTRATION_WINDOW_SECONDS),
        'last_computed': None,
        'seconds_remaining': None,
    }
    # Peek the held-schedule cache (module-level, per process). Only meaningful for
    # a concrete sandbox and when the hold is actually engaged.
    cached = director_policy._cache.get(sandbox_id) if sandbox_id else None
    if cached is not None:
        last_computed, _params = cached
        elapsed = (now - last_computed).total_seconds()
        lock['last_computed'] = last_computed.isoformat()
        lock['seconds_remaining'] = max(0, int(eflags.POLICY_WINDOW_SECONDS - elapsed))
    return lock


def compute_chairman_status(
    *,
    ledger_repo,
    sandbox_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Project the chairman's current stage, the full band ladder, and the lock.

    `sandbox_id=None` reads the cross-sandbox aggregate (the admin "All sandboxes"
    view); a concrete id scopes the signal to one save. Pure read — one `signal()`
    snapshot, the rest derived.
    """
    now = now or datetime.utcnow()
    state = sig.signal(ledger_repo, sandbox_id=sandbox_id)
    current_band = _band_for_ratio(state.ratio) if state.holdings > 0 else None

    bands = []
    for key, label, lo, hi in _BANDS:
        bands.append(
            {
                'key': key,
                'label': label,
                'ratio_min': lo,
                'ratio_max': hi,  # None → open-topped
                'blurb': _BAND_BLURB[key],
                'levers': _levers_at(_probe_ratio(lo, hi)),
            }
        )

    return {
        'sandbox_id': sandbox_id,
        'signal': {
            'reserves': state.reserves,
            'holdings': state.holdings,
            'ratio': round(state.ratio, 5),
            'regime': state.regime,
        },
        'thresholds': {
            'critical': sig.RESERVE_CRITICAL,
            'healthy': sig.RESERVE_HEALTHY,
            'trigger': sig.RESERVE_TRIGGER,
            'vice_ceiling': sig.RESERVE_VICE_CEILING,
        },
        # None when the universe is cold (no chips yet) — the bands carry no signal.
        'current_band': current_band,
        'bands': bands,
        'whale': _whale_status(state),
        # Live levers at the current ratio (same functions as the per-band probes).
        'levers': _levers_at(state.ratio) if current_band else None,
        'policy_lock': _policy_lock(sandbox_id, now),
        'as_of': now.isoformat(),
    }
