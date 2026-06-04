"""Phase E — `audit_ledger_completeness`: re-align ledger-derived bankroll
balances with the authoritative stored ints by parking residual drift in the
`reconciliation` suspense account.

The atomic-write unit-of-work (T3-82) stops fresh drift at the high-frequency
chokepoints; this reconcile is the safety net that

  1. retires the historical residue once (the ~24k AI drift left by the
     pre-atomic non-atomic writes; the human-staking gap was already backfilled),
     and
  2. periodically mops up whatever the still-unconverted low-frequency paths
     (T3-84 tournament buy-in, T3-85 human cash_routes) leak.

With `CHIP_CUSTODY_DERIVE_READS` off the stored int is what's served, so a
drifted ledger is audit-only — but reconciling keeps the ledger trustworthy and
keeps the suspense-account balance as a live "net unexplained drift" signal (it
should hover near zero; a growing magnitude flags a fresh leak to chase).

For each (pid, sandbox) AI bankroll and each player bankroll it computes
`delta = stored − derived` and, in apply mode, writes ONE bank-neutral
`ledger_reconciliation` transfer that moves `delta` between the account and the
`reconciliation` suspense account — leaving the account's derived balance equal
to its stored int. Idempotent: a second run finds `delta == 0` everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from core.economy import ledger as L


@dataclass
class ReconcileReport:
    ai_checked: int = 0
    ai_adjusted: int = 0
    player_checked: int = 0
    player_adjusted: int = 0
    total_abs_drift: int = 0  # Σ |stored − derived| corrected
    net_drift: int = 0  # Σ (stored − derived) corrected (signed)
    applied: bool = False
    # (account, sandbox_id, delta) for each adjustment — for logging/inspection.
    adjustments: List[Tuple[str, Optional[str], int]] = field(default_factory=list)


def reconcile_ledger_completeness(
    *,
    bankroll_repo,
    ledger_repo,
    sandbox_id: Optional[str] = None,
    apply: bool = False,
) -> ReconcileReport:
    """Compare every bankroll's stored int to its ledger-derived balance and,
    when ``apply`` is True, emit `ledger_reconciliation` transfers so derived
    re-aligns with stored. Returns a `ReconcileReport`.

    `sandbox_id=None` scans every sandbox (AI bankrolls are per-sandbox; player
    bankrolls are global and only scanned when `sandbox_id is None`). Reads the
    RAW stored ints (`iter_*_raw`) so it is correct regardless of the
    derive-reads flag. No chip ledger repo / no-op safe: returns an empty report
    when `ledger_repo` is None.
    """
    report = ReconcileReport(applied=apply)
    if ledger_repo is None:
        return report

    ctx = {'site': 'audit_ledger_completeness'}

    # --- AI bankrolls (per (pid, sandbox)) ---
    for pid, sb, stored in bankroll_repo.iter_ai_bankrolls_raw(sandbox_id=sandbox_id):
        report.ai_checked += 1
        derived = ledger_repo.balance_of(L.ai(pid), sandbox_id=sb)
        delta = int(stored) - int(derived)
        if delta == 0:
            continue
        report.ai_adjusted += 1
        report.total_abs_drift += abs(delta)
        report.net_drift += delta
        report.adjustments.append((L.ai(pid), sb, delta))
        if apply:
            L.record_ledger_reconciliation(
                ledger_repo,
                account=L.ai(pid),
                delta=delta,
                context={**ctx, 'pid': pid},
                sandbox_id=sb,
            )

    # --- Player bankrolls (global; only on a full scan) ---
    if sandbox_id is None:
        for oid, stored in bankroll_repo.iter_player_bankrolls_raw():
            report.player_checked += 1
            derived = L.derive_player_balance(ledger_repo, owner_id=oid)
            if derived is None:
                continue
            delta = int(stored) - int(derived)
            if delta == 0:
                continue
            report.player_adjusted += 1
            report.total_abs_drift += abs(delta)
            report.net_drift += delta
            report.adjustments.append((L.player(oid), None, delta))
            if apply:
                L.record_ledger_reconciliation(
                    ledger_repo,
                    account=L.player(oid),
                    delta=delta,
                    context={**ctx, 'owner_id': oid},
                    sandbox_id=None,
                )

    return report
