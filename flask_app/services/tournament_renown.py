"""Grant renown on a finished tournament (tournaments-as-a-draw, Phase D).

The draw (Phase B) pulls AIs toward the Main Event partly for the **renown** a
deep run earns; this is the other half of that loop — the payout-time grant that
actually moves the needle. After a tournament's escrow is distributed
(`tournament_economy_service.apply_payout_on_complete`), every in-the-money
finisher gets a renown bump scaled by where they finished: the champion gets the
full `base`, the bubble gets a fraction.

Model fit (see `poker/repositories/prestige_snapshots_repository.py`): renown is
an APPEND-ONLY snapshot history whose peak is `MAX(renown_v2)` at read time. A
grant is therefore just one new row with `renown_v2 = current_peak + bump`; the
peak ratchets up and the periodic recompute never erases it. We clone the
finisher's latest snapshot (quadrant / regard / components) and bump only
`renown_v2`, so a grant never clobbers the rest of their scoreboard.

Flag-gated behind `TOURNAMENT_DRAW_ENABLED` (the feature this renown feeds). The
AI grant is only *visible to the draw* when `RENOWN_V2_PERSIST_AI` is also on (the
draw reads AI renown only then) — but the rows are written regardless, so the
peak is already correct the moment persistence is enabled. Best-effort: a grant
failure must never affect chips or the payout's terminal status.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from cash_mode.prestige import QUADRANT_UP_AND_COMER

logger = logging.getLogger(__name__)

# Base renown a Main Event WIN grants (uncapped renown-v2 points; sim-tunable).
# Paid places below the win scale down from this toward the bubble.
DEFAULT_WIN_RENOWN = 1.0


def position_renown(position: int, paid_places: int, base: float = DEFAULT_WIN_RENOWN) -> float:
    """Renown bump for a `position`-th finish out of `paid_places` paid spots.

    Winner (1st) gets the full `base`; it scales linearly down to `0.2 * base`
    at the bubble (the last paid place). Positions outside the money get 0. A
    deep run earns *some* renown, not just the win — the curve the owner chose.
    """
    if position < 1 or position > paid_places or base <= 0:
        return 0.0
    if paid_places <= 1:
        return base
    # 1.0 at the win → 0.2 at the bubble.
    frac = 1.0 - 0.8 * (position - 1) / (paid_places - 1)
    return base * frac


def _position_to_player(session) -> dict:
    """Finishing position → player_id (1 = winner). Mirrors the payout mapping in
    `tournament_economy_service` (kept local so this module is self-contained and
    unit-testable). Only meaningful once the tournament is complete."""
    mapping = {e.finishing_position: e.player_id for e in session.field.eliminations}
    winner = session.winner()
    if winner is not None:
        mapping[1] = winner
    return mapping


def _grant_ai(prestige_repo, *, sandbox_id, pid, renown_v2, now_iso, field_size) -> None:
    """Write one v2-native AI snapshot row with the bumped renown, cloning the
    finisher's latest quadrant/regard so the grant doesn't reset them."""
    latest = prestige_repo.load_latest(sandbox_id, pid, entity_kind='ai') or {}
    prestige_repo.record_ai_many(
        sandbox_id=sandbox_id,
        captured_at=now_iso,
        rows=[
            {
                'owner_id': pid,
                'renown_v2': renown_v2,
                'regard': latest.get('regard', 0.0),
                # A fresh winner has renown without established regard → Up-and-comer.
                'quadrant': latest.get('quadrant') or QUADRANT_UP_AND_COMER,
                'victim_percentile': latest.get('victim_percentile'),
                'high_cut': latest.get('high_cut'),
                'components': None,
                'field_size': field_size,
            }
        ],
    )


def _grant_human(prestige_repo, *, sandbox_id, owner_id, renown_v2, now_iso, field_size) -> None:
    """Write one human snapshot row (entity_kind='player') tagged
    `formula_version='tournament_v1'` with the bumped renown_v2, cloning the
    player's latest v1 score so the v1 columns / quadrant are preserved."""
    latest = prestige_repo.load_latest(sandbox_id, owner_id, entity_kind='player') or {}

    # Duck-typed ReputationScore for record() — carry the latest v1 values so the
    # grant bumps ONLY renown_v2, never the capped v1 renown peak or the breakdown.
    score = _ClonedScore(latest)
    prestige_repo.record(
        captured_at=now_iso,
        sandbox_id=sandbox_id,
        owner_id=owner_id,
        score=score,
        entity_kind='player',
        formula_version='tournament_v1',
        renown_v2=renown_v2,
        field_size=field_size,
    )


class _ClonedScore:
    """A `ReputationScore` stand-in built from a latest-snapshot row dict (or zero
    when there's none), exposing exactly the attributes `record()` reads."""

    _FIELDS = (
        'renown',
        'regard',
        'renown_breadth',
        'renown_tenure',
        'renown_stake_tier',
        'renown_beat_respected',
        'renown_high_stakes',
        'regard_likability',
        'regard_respect',
        'regard_heat',
    )

    def __init__(self, latest: dict):
        for f in self._FIELDS:
            setattr(self, f, float(latest.get(f) or 0.0))
        self.quadrant = latest.get('quadrant') or QUADRANT_UP_AND_COMER
        self.opponent_count = int(latest.get('opponent_count') or 0)


def grant_on_payout(
    prestige_repo,
    *,
    sandbox_id: str,
    session: Any,
    human_owner_id: Optional[str],
    real_persona_ids,
    now_iso: Optional[str] = None,
    base: float = DEFAULT_WIN_RENOWN,
) -> int:
    """Grant renown to every in-the-money finisher of a completed tournament.

    Returns the number of finishers granted. Flag-gated (`TOURNAMENT_DRAW_ENABLED`)
    and fully best-effort: a None repo, the flag off, or any error → 0 grants and
    never raises (the caller runs this where a throw would wrongly strand the
    payout). Idempotency is the caller's: it grants inside the once-only payout
    block (the `claim_payout` CAS), so it fires exactly once per tournament.
    """
    from cash_mode import economy_flags

    if prestige_repo is None or not economy_flags.TOURNAMENT_DRAW_ENABLED:
        return 0
    try:
        # Use the SAME in-the-money count the payout schedule uses
        # (`tournament.economy`, via `compute_payout_schedule`) so renown's paid
        # places never diverge from who actually got paid chips.
        from tournament.economy import paid_places_for

        field_size = session.field.field_size
        paid_places = paid_places_for(field_size)
        pos_to_player = _position_to_player(session)
        human_id = session.human_id
        now_iso = now_iso or datetime.utcnow().isoformat()
        real_persona_ids = real_persona_ids or frozenset()

        granted = 0
        for position in range(1, paid_places + 1):
            pid = pos_to_player.get(position)
            if pid is None:
                continue
            bump = position_renown(position, paid_places, base)
            if bump <= 0:
                continue
            if human_owner_id is not None and pid == human_id:
                peak = prestige_repo.load_renown_v2_peak(sandbox_id, human_owner_id, 'player')
                _grant_human(
                    prestige_repo,
                    sandbox_id=sandbox_id,
                    owner_id=human_owner_id,
                    renown_v2=peak + bump,
                    now_iso=now_iso,
                    field_size=field_size,
                )
                granted += 1
            elif pid in real_persona_ids:
                peak = prestige_repo.load_renown_v2_peak(sandbox_id, pid, 'ai')
                _grant_ai(
                    prestige_repo,
                    sandbox_id=sandbox_id,
                    pid=pid,
                    renown_v2=peak + bump,
                    now_iso=now_iso,
                    field_size=field_size,
                )
                granted += 1
        return granted
    except Exception:  # noqa: BLE001 — renown is best-effort; never break the payout
        logger.exception("renown grant failed for sandbox=%s (payout unaffected)", sandbox_id)
        return 0
