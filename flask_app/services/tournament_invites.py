"""Circuit Main Event invite lifecycle (P3 surfacing — backend).

The player's single decision point is the **invite**, not a running tournament:

    offer ──accept──▶ a tournament the human plays (real-persona field + buy-in)
          ──decline─▶ runs autonomously (AI-only), plays out at world pace
          ──expire──▶ same as decline (timer lapsed un-accepted)

There is no joining after start — accept means it starts WITH you; decline /
expire means it starts WITHOUT you. One open invite (and one active tournament)
per owner at a time. See `docs/plans/TOURNAMENT_CIRCUIT_SURFACING.md`.

Pure-ish orchestration over injected repos so it's testable without Flask; the
chip-moving work is delegated to `tournament_spawn` (autonomous + human builders)
and `tournament_economy_service` (the escrow/payout authority). Caller holds
`get_sandbox_lock(sandbox_id)`.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime
from typing import Optional

from flask_app.services import tournament_spawn
from poker.repositories.tournament_invite_repository import (
    STATUS_ACCEPTED,
    STATUS_DECLINED,
    STATUS_EXPIRED,
)

logger = logging.getLogger(__name__)


def _new_invite_id() -> str:
    return "invite_" + secrets.token_urlsafe(10)


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat()


def active_invite(invite_repo, owner_id: str) -> Optional[dict]:
    """The owner's currently-open invite (for the lobby card), or None."""
    if invite_repo is None:
        return None
    return invite_repo.active_for_owner(owner_id)


def offer(
    *,
    invite_repo,
    session_repo,
    owner_id: str,
    sandbox_id: str,
    buy_in: int = 0,
    field_size: int = 9,
    table_size: int = 3,
    starting_stack: int = 10_000,
    seed: int = 0,
    expires_at: Optional[str] = None,
) -> Optional[dict]:
    """Offer a Main Event to the owner — unless one is already open OR a
    tournament is already active for them (one at a time). Returns the new
    invite dict, or None if suppressed."""
    if invite_repo is None:
        return None
    if invite_repo.active_for_owner(owner_id) is not None:
        return None  # an invite is already open
    if session_repo is not None and session_repo.find_active_for_owner(owner_id) is not None:
        return None  # a tournament is already running

    invite_id = _new_invite_id()
    invite_repo.create(
        invite_id=invite_id,
        owner_id=owner_id,
        sandbox_id=sandbox_id,
        buy_in=buy_in,
        field_size=field_size,
        table_size=table_size,
        starting_stack=starting_stack,
        seed=seed,
        expires_at=expires_at,
    )
    return invite_repo.load(invite_id)


def accept(
    *,
    invite_repo,
    personality_repo,
    bankroll_repo,
    ledger_repo,
    session_repo,
    owner_id: str,
    invite_id: Optional[str] = None,
) -> Optional[dict]:
    """Accept the open invite → build a tournament the human plays IN. Returns
    `{tournament_id, human_id, entries, plan}` or None if there's no open invite.
    Raises `InsufficientFundsError` (re-raised) when the human can't cover the
    buy-in — nothing is consumed in that case (the invite stays open)."""
    invite = invite_repo.load(invite_id) if invite_id else invite_repo.active_for_owner(owner_id)
    if invite is None or invite['status'] != 'offered' or invite['owner_id'] != owner_id:
        return None

    built = tournament_spawn.create_human_tournament(
        owner_id=owner_id,
        sandbox_id=invite['sandbox_id'],
        personality_repo=personality_repo,
        bankroll_repo=bankroll_repo,
        ledger_repo=ledger_repo,
        session_repo=session_repo,
        buy_in=invite['buy_in'],
        field_size=invite['field_size'],
        table_size=invite['table_size'],
        starting_stack=invite['starting_stack'],
        seed=invite['seed'],
        rng_seed=invite['seed'],
    )
    if built is None:
        # Couldn't field enough seats — leave the invite open for a later retry.
        return None

    invite_repo.resolve(
        invite['invite_id'], status=STATUS_ACCEPTED, tournament_id=built['tournament_id']
    )
    return {
        'tournament_id': built['tournament_id'],
        'human_id': built['human_id'],
        'entries': built['entries'],
        'plan': built['plan'],
    }


def _resolve_autonomously(invite: dict, *, status: str, repos: dict) -> Optional[dict]:
    """Shared decline/expire body: spawn an AI-only tournament from the invite's
    params and terminal-transition the invite. Returns the spawned dict or None."""
    spawned = tournament_spawn.spawn_autonomous_tournament(
        owner_id=invite['owner_id'],
        sandbox_id=invite['sandbox_id'],
        personality_repo=repos['personality_repo'],
        bankroll_repo=repos['bankroll_repo'],
        ledger_repo=repos['ledger_repo'],
        session_repo=repos['session_repo'],
        field_size=invite['field_size'],
        table_size=invite['table_size'],
        starting_stack=invite['starting_stack'],
        seed=invite['seed'],
        rng_seed=invite['seed'],
    )
    tid = spawned['tournament_id'] if spawned else None
    repos['invite_repo'].resolve(invite['invite_id'], status=status, tournament_id=tid)
    return spawned


def decline(
    *,
    invite_repo,
    personality_repo,
    bankroll_repo,
    ledger_repo,
    session_repo,
    owner_id: str,
    invite_id: Optional[str] = None,
) -> Optional[dict]:
    """Decline the open invite → it starts autonomously (AI-only). Returns the
    spawned tournament dict, or None if there was no open invite."""
    invite = invite_repo.load(invite_id) if invite_id else invite_repo.active_for_owner(owner_id)
    if invite is None or invite['status'] != 'offered' or invite['owner_id'] != owner_id:
        return None
    return _resolve_autonomously(
        invite,
        status=STATUS_DECLINED,
        repos={
            'invite_repo': invite_repo,
            'personality_repo': personality_repo,
            'bankroll_repo': bankroll_repo,
            'ledger_repo': ledger_repo,
            'session_repo': session_repo,
        },
    )


def expire_due(
    *,
    invite_repo,
    personality_repo,
    bankroll_repo,
    ledger_repo,
    session_repo,
    now_iso: Optional[str] = None,
) -> list[dict]:
    """Expire every open invite past its `expires_at` → each starts
    autonomously. Returns the spawned tournaments. The expiry sweep the
    lobby/ticker calls; an absent player's invite simply waits until this runs."""
    if invite_repo is None:
        return []
    now_iso = now_iso or _utcnow_iso()
    spawned: list[dict] = []
    for invite in invite_repo.list_open_due(now_iso=now_iso):
        result = _resolve_autonomously(
            invite,
            status=STATUS_EXPIRED,
            repos={
                'invite_repo': invite_repo,
                'personality_repo': personality_repo,
                'bankroll_repo': bankroll_repo,
                'ledger_repo': ledger_repo,
                'session_repo': session_repo,
            },
        )
        if result:
            spawned.append(result)
    return spawned
