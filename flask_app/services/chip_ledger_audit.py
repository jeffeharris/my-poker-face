"""Audit computation for the chip ledger (v93).

Reads the ledger + all chip-bearing surfaces (player bankrolls, AI
bankrolls, persisted cash table seats, active loan principals, live
cash-session AI stacks) and reports both the ledger view and the
actual view. The difference between them is `drift` — non-zero means
chips moved without a corresponding ledger entry.

v0 ships an *approximate* audit. Caveats called out in `compute_audit`:

  * Pre-existing chips (before the v93 migration shipped) have no
    `pre_ledger_universe` seed entry yet. Drift will start at the
    pre-existing total and only become meaningful as new sessions
    accumulate ledger writes.
  * Live cash-session AI table stacks come from in-memory game
    state; if a backend restart wipes that, the audit reports them
    as zero. This is a real (small) source of drift that resolves
    naturally at session end.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def compute_audit(
    *,
    ledger_repo,
    bankroll_repo,
    cash_table_repo,
    stake_repo,
    db_path: str,
    list_game_ids_fn=None,
    get_game_fn=None,
    now: Optional[datetime] = None,
    sandbox_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute the audit payload described in the chip-ledger handoff.

    Dependencies are injected so tests can drive the function with
    fixture repos / fakes without monkey-patching flask_app.extensions
    or flask_app.services.game_state_service. Production callers pass
    the live extensions + game_state_service helpers.

    Args:
        ledger_repo: ChipLedgerRepository for ledger totals + windows.
        bankroll_repo: BankrollRepository for projected AI chips
            (uses `project_bankroll` via `load_ai_bankroll_current`).
        cash_table_repo: CashTableRepository for persisted seat chips.
        stake_repo: StakeRepository for summing active stake principal
            owed by humans (chips on a human session seat aren't
            summed elsewhere). Required as of v99 — the legacy
            `active_loan_amount` column fallback was removed once the
            columns themselves were dropped.
        db_path: Raw SQLite path for the SUM queries that don't go
            through repo APIs (player_bankrolls).
        list_game_ids_fn: Optional callable returning live game ids.
            Defaults to `flask_app.services.game_state_service.list_game_ids`.
        get_game_fn: Optional callable taking a game id and returning
            its game_data dict.
        now: Defaults to `datetime.utcnow()` — explicit lets tests
            pin the 24h window boundary.
        sandbox_id: Optional sandbox scope. `None` (the default) means
            "aggregate across all sandboxes" — the admin view. When a
            specific sandbox_id is passed, every per-sandbox sum filters
            to that sandbox only. The cross-cutting surfaces
            (player_bankrolls, stake principal, live session stacks,
            chip-ledger totals) stay global in v1; only the AI runtime
            state aggregations honor the filter.

    Returns:
        Dict matching the audit shape in
        `docs/plans/CASH_MODE_CHIP_LEDGER_HANDOFF.md`.
    """
    if now is None:
        now = datetime.utcnow()

    # --- Ledger totals (all-time + 24h window) ---
    # Per-sandbox scoping (v103): when `sandbox_id` is provided, the
    # ledger sums filter to that sandbox's rows only. NULL-sandbox
    # rows (pre-v103 legacy bucket) are excluded by the WHERE clause
    # — they show up in the cross-sandbox / admin view (sandbox_id=None)
    # but not in any specific sandbox's audit.
    creations = ledger_repo.sum_creations_by_reason(sandbox_id=sandbox_id)
    destructions = ledger_repo.sum_destructions_by_reason(sandbox_id=sandbox_id)
    chips_created = sum(creations.values())
    chips_destroyed = sum(destructions.values())
    ledger_outstanding = chips_created - chips_destroyed

    since_24h_iso = (now - timedelta(hours=24)).isoformat()
    creations_24h = ledger_repo.sum_creations_by_reason(
        since_iso=since_24h_iso, sandbox_id=sandbox_id,
    )
    destructions_24h = ledger_repo.sum_destructions_by_reason(
        since_iso=since_24h_iso, sandbox_id=sandbox_id,
    )

    # --- Actual totals ---
    #
    # AI bankrolls are summed by *stored* chip value, not projected.
    # The ledger fires `ai_regen` at write time, not at projection
    # read time, so the canonical "chips in the universe" for drift
    # math matches what's persisted on disk. Projected value is
    # returned separately for the UI but doesn't enter the drift
    # calculation — otherwise drift would always include uncommitted
    # regen and never zero out.
    player_bankrolls = _sum_player_bankrolls(db_path)
    # Chips lent out via active stakes to *human* borrowers. Human
    # session table stacks aren't summed by `live_session_ai_stacks`
    # (which filters humans out), so without this term those chips
    # would silently disappear from `actual_outstanding` and inflate
    # drift. For AI borrowers (Phase 4+), both sides of the transfer
    # land in chip-bearing surfaces already counted (AI staker bankroll
    # decreases, AI borrower seat / live-stack increases), so the
    # stakes-table sum is restricted to human borrowers by design.
    active_loans_principal = _sum_active_stake_principal_for_humans(stake_repo)
    ai_bankrolls_stored = _sum_ai_bankrolls_stored(bankroll_repo, sandbox_id)
    ai_bankrolls_projected = _sum_ai_bankrolls_projected(bankroll_repo, now, sandbox_id)
    cash_table_seats_ai = _sum_cash_table_ai_seats(cash_table_repo, sandbox_id)
    live_session_ai_stacks, live_session_error = _sum_live_session_ai_stacks(
        list_game_ids_fn, get_game_fn,
    )

    actual_outstanding = (
        player_bankrolls
        + ai_bankrolls_stored
        + cash_table_seats_ai
        + active_loans_principal
        + live_session_ai_stacks
    )
    # Uncommitted regen — the gap between what AIs currently
    # read as (projected) and what they have stored. Informative
    # for tuning regen rates; doesn't affect drift.
    uncommitted_ai_regen = ai_bankrolls_projected - ai_bankrolls_stored

    by_reason = _merge_reasons(creations, destructions)
    by_reason_window_24h = _merge_reasons(creations_24h, destructions_24h)

    # Per-source error bookkeeping. live_session_ai_stacks is the
    # only term whose failure can't be expressed as 0 without
    # making drift look spuriously positive — surface it so callers
    # (and the admin UI) know the data is degraded.
    errors: Dict[str, str] = {}
    if live_session_error is not None:
        errors['live_session_ai_stacks'] = live_session_error

    return {
        'ledger_totals': {
            'chips_created': chips_created,
            'chips_destroyed': chips_destroyed,
            'outstanding': ledger_outstanding,
        },
        'actual_totals': {
            'player_bankrolls': player_bankrolls,
            'ai_bankrolls_stored': ai_bankrolls_stored,
            'ai_bankrolls_projected': ai_bankrolls_projected,
            'uncommitted_ai_regen': uncommitted_ai_regen,
            'cash_table_seats_ai': cash_table_seats_ai,
            'active_loans_principal': active_loans_principal,
            'live_session_ai_stacks': live_session_ai_stacks,
            'actual_outstanding': actual_outstanding,
        },
        'drift': ledger_outstanding - actual_outstanding,
        'by_reason': by_reason,
        'by_reason_window_24h': by_reason_window_24h,
        'errors': errors,
        'as_of': now.isoformat(),
    }


# --- internals ---


def _merge_reasons(
    creations: Dict[str, int], destructions: Dict[str, int],
) -> Dict[str, int]:
    """Per-reason signed totals: creations positive, destructions negative.

    Annotation entries (forgive_balance with amount=0) collapse to
    zero — they don't affect the bucket but appear in the dict so
    the UI can show they happened.
    """
    out: Dict[str, int] = {}
    for reason, amount in creations.items():
        out[reason] = out.get(reason, 0) + amount
    for reason, amount in destructions.items():
        out[reason] = out.get(reason, 0) - amount
    return out


def _sum_player_bankrolls(db_path: str) -> int:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(chips), 0) FROM player_bankroll_state"
        ).fetchone()
        return int(row[0] or 0)


def _sum_active_stake_principal_for_humans(stake_repo) -> int:
    """Sum principal+match across every active stake to a human borrower.

    These are the chips that live on a human session seat — the only
    surface the rest of the audit doesn't already count (human stacks
    are excluded from `_sum_live_session_ai_stacks`). For AI borrowers
    both ends of the transfer land in counted surfaces, so they're
    skipped.

    `match_amount` is included because for `match_share` stakes the
    borrower's own contribution sits on the seat too — bankroll
    decreased by the same amount at sit-down, so without summing it
    here the drift would go negative by that contribution.
    """
    return stake_repo.sum_active_principal_for_humans()


def _sum_ai_bankrolls_stored(bankroll_repo, sandbox_id: Optional[str]) -> int:
    """Sum AI bankroll chips as currently *stored* on disk.

    Stored chips are the canonical persistence value — they only
    change when `save_ai_bankroll` is called, which is the same
    moment the `ai_regen` / `cap_clamp` ledger entries fire. This
    is what drift math needs.

    `sandbox_id=None` aggregates across every sandbox (admin view).
    A specific id scopes the sum to a single sandbox.
    """
    return bankroll_repo.sum_ai_bankroll_chips_stored(sandbox_id=sandbox_id)


def _sum_ai_bankrolls_projected(
    bankroll_repo, now: datetime, sandbox_id: Optional[str],
) -> int:
    """Sum projected (regen-applied, cap-clamped) AI bankroll chips.

    Read-time view: what a live read of each AI's bankroll would
    return now. Differs from stored when time has elapsed since
    the last write — the gap is uncommitted regen, returned in the
    audit payload for tuning purposes.

    `sandbox_id=None` projects across every sandbox; a specific id
    scopes the projection to a single sandbox. Projection lookups
    pass the same scope through to `load_ai_bankroll_current`.
    """
    total = 0
    # When aggregating cross-sandbox, we need to iterate every
    # (sandbox_id, personality_id) pair so the per-row projection
    # carries the right scope. `iter_personality_ids_with_bankrolls`
    # returns the per-sandbox pid list; when called with
    # `sandbox_id=None` it returns pids across every sandbox.
    if sandbox_id is None:
        # Cross-sandbox path: iterate every (sandbox_id, pid) pair so
        # the projection load gets the right scope. The repo exposes
        # this via the same iterator returning pids per sandbox; we
        # fall back to summing the stored value when no projection
        # scope is unambiguous.
        try:
            iter_pairs = bankroll_repo.iter_personality_ids_with_bankrolls_by_sandbox()
        except AttributeError:
            # Repo doesn't yet expose the per-sandbox iterator — fall
            # back to the stored sum (drift math is unaffected; only
            # the uncommitted-regen line item degrades).
            return bankroll_repo.sum_ai_bankroll_chips_stored(sandbox_id=None)
        for pid, sid in iter_pairs:
            try:
                chips = bankroll_repo.load_ai_bankroll_current(
                    pid, sandbox_id=sid, now=now,
                )
            except Exception as e:
                logger.warning(
                    "chip-ledger audit: load_ai_bankroll_current(%r, sandbox=%r) failed: %s",
                    pid, sid, e,
                )
                chips = 0
            total += int(chips or 0)
        return total
    for pid in bankroll_repo.iter_personality_ids_with_bankrolls(sandbox_id=sandbox_id):
        try:
            chips = bankroll_repo.load_ai_bankroll_current(
                pid, sandbox_id=sandbox_id, now=now,
            )
        except Exception as e:
            logger.warning(
                "chip-ledger audit: load_ai_bankroll_current(%r, sandbox=%r) failed: %s",
                pid, sandbox_id, e,
            )
            chips = 0
        total += int(chips or 0)
    return total


def _sum_cash_table_ai_seats(cash_table_repo, sandbox_id: Optional[str]) -> int:
    """Sum AI seat chips across persisted cash tables.

    `sandbox_id=None` walks every sandbox's tables (admin view); a
    specific id scopes to a single sandbox.
    """
    total = 0
    for table in cash_table_repo.list_all_tables(sandbox_id=sandbox_id):
        for slot in table.seats:
            if slot.get('kind') == 'ai':
                total += int(slot.get('chips', 0) or 0)
    return total


def _sum_live_session_ai_stacks(list_game_ids_fn, get_game_fn):
    """Sum AI table stacks across in-memory active cash sessions.

    Approximation: if the backend restarts and a session resumes
    from DB, those chips will briefly appear as drift until the
    session ends and bankrolls credit back. v0 reports this as a
    line item rather than blending it into one number.

    Returns `(total, error_message_or_None)`. When the iteration
    raises, the caller surfaces the message in the audit payload's
    `errors` dict so the UI can flag degraded data — silently
    returning 0 would look like real positive drift.
    """
    if list_game_ids_fn is None or get_game_fn is None:
        return 0, None
    total = 0
    try:
        for game_id in list_game_ids_fn():
            if not isinstance(game_id, str) or not game_id.startswith('cash-'):
                continue
            game_data = get_game_fn(game_id)
            if not game_data:
                continue
            state_machine = game_data.get('state_machine')
            if state_machine is None:
                continue
            try:
                players = state_machine.game_state.players
            except AttributeError:
                continue
            for p in players:
                if getattr(p, 'is_human', False):
                    continue
                total += int(getattr(p, 'stack', 0) or 0)
    except Exception as e:
        logger.warning("chip-ledger audit: live-session sum failed: %s", e)
        return 0, f"live-session iteration failed: {e}"
    return total, None
