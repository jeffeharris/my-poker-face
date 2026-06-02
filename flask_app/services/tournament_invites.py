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
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from core.economy import economy_signal
from flask_app.services import tournament_spawn
from poker.repositories.tournament_invite_repository import (
    STATUS_ACCEPTED,
    STATUS_DECLINED,
    STATUS_EXPIRED,
)

logger = logging.getLogger(__name__)


class CannotFieldTournamentError(Exception):
    """Accept failed because the sandbox couldn't draft enough players for the
    field (e.g. no circulating personas). The invite IS open and is re-opened —
    distinct from 'no open invite' so the UI can say "not enough players right
    now" instead of a misleading not-found."""


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
    try:
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
    except sqlite3.IntegrityError:
        # The one-open-invite-per-owner partial unique index (schema v136) fired:
        # a concurrent worker won the race between the active_for_owner check above
        # and this insert. The other offer stands — surface theirs, not an error.
        logger.info("offer race for owner=%s lost to a concurrent offer; using the open one", owner_id)
        return invite_repo.active_for_owner(owner_id)
    return invite_repo.load(invite_id)


def _cooldown_elapsed(last_created_at_iso: Optional[str], now: datetime, cooldown_seconds: int) -> bool:
    if not last_created_at_iso:
        return True
    try:
        last = datetime.fromisoformat(last_created_at_iso)
    except ValueError:
        return True
    return now - last >= timedelta(seconds=cooldown_seconds)


def maybe_offer_main_event(
    *,
    invite_repo,
    session_repo,
    ledger_repo,
    owner_id: str,
    sandbox_id: str,
    now: Optional[datetime] = None,
    cooldown_seconds: int = economy_signal.MAIN_EVENT_COOLDOWN_SECONDS,
    expiry_seconds: Optional[int] = economy_signal.MAIN_EVENT_REGISTRATION_WINDOW_SECONDS,
    spec: economy_signal.EventSpec = economy_signal.DEFAULT_MAIN_EVENT,
) -> Optional[dict]:
    """The chairman-driven trigger: offer a Main Event iff the bank is FLUSH and
    the cooldown has elapsed (`economy_signal.should_offer_event`). This is what
    *decides there should be a tournament* — the same signal that sizes the
    overlay also gates whether an event runs at all. Returns the new invite, or
    None (not flush / on cooldown / one already open / a tournament active).

    Run on the world tick or lobby load. `expiry_seconds` sets the invite's
    `expires_at` registration window — defaults to
    `MAIN_EVENT_REGISTRATION_WINDOW_SECONDS` so an un-acted offer auto-expires to
    autonomous play (the "decline by inaction" timer). Pass None to keep an offer
    open until the player decides; pass a computed value for a scheduled window.
    """
    if invite_repo is None or ledger_repo is None:
        return None
    now = now or datetime.utcnow()
    cooldown_ok = _cooldown_elapsed(
        invite_repo.last_created_at(owner_id), now, cooldown_seconds
    )
    state = economy_signal.signal(ledger_repo, sandbox_id=sandbox_id)
    event = economy_signal.should_offer_event(state, cooldown_elapsed=cooldown_ok, spec=spec)
    if event is None:
        return None

    expires_at = None
    if expiry_seconds is not None:
        expires_at = (now + timedelta(seconds=expiry_seconds)).isoformat()

    return offer(
        invite_repo=invite_repo,
        session_repo=session_repo,
        owner_id=owner_id,
        sandbox_id=sandbox_id,
        buy_in=event.buy_in,
        field_size=event.field_size,
        table_size=event.table_size,
        starting_stack=event.starting_stack,
        expires_at=expires_at,
    )


def accept(
    *,
    invite_repo,
    personality_repo,
    bankroll_repo,
    ledger_repo,
    session_repo,
    cash_table_repo=None,
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

    # Cross-worker compare-and-swap: claim the invite BEFORE the buy-in/build so
    # two gunicorn workers can't both pass the read-check above and both charge
    # the human (the in-memory sandbox lock doesn't span worker processes). Only
    # the worker that flips offered→accepted proceeds; the loser bails.
    if not invite_repo.claim(invite['invite_id'], to_status=STATUS_ACCEPTED, owner_id=owner_id):
        return None

    try:
        built = tournament_spawn.create_human_tournament(
            owner_id=owner_id,
            sandbox_id=invite['sandbox_id'],
            personality_repo=personality_repo,
            bankroll_repo=bankroll_repo,
            ledger_repo=ledger_repo,
            session_repo=session_repo,
            cash_table_repo=cash_table_repo,
            buy_in=invite['buy_in'],
            field_size=invite['field_size'],
            table_size=invite['table_size'],
            starting_stack=invite['starting_stack'],
            seed=invite['seed'],
            rng_seed=invite['seed'],
        )
    except Exception:
        # Build/charge failed (e.g. InsufficientFundsError, raised before any
        # chips move) — re-open the invite so the player can retry (preserves the
        # "insufficient funds keeps the invite open" semantic), then propagate.
        invite_repo.revert_to_offered(invite['invite_id'])
        raise
    if built is None:
        # Couldn't field enough seats — re-open the invite for a later retry and
        # signal the distinct cause (not "no open invite", which is what a bare
        # None becomes at the route). The invite stays open.
        invite_repo.revert_to_offered(invite['invite_id'])
        raise CannotFieldTournamentError(
            "not enough players available to field this Main Event right now"
        )

    # Status is already 'accepted' (the claim); this stamps the tournament_id link.
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
    params and terminal-transition the invite. Returns the spawned dict or None.

    Claims the invite (offered→`status`) via a cross-worker CAS BEFORE spawning,
    so a concurrent accept/decline/expire on the same invite can't double-spawn
    (or spawn an autonomous run after another worker already accepted it into a
    human tournament). Only the claim winner proceeds; a loser returns None.

    Returns None ONLY when the claim is lost (the invite was already resolved by
    someone else). When the claim is won the invite IS consumed
    (declined/expired) — so even if the autonomous tournament can't be fielded
    (too few personas), this returns a result marker (`tournament_id: None`), not
    None, so decline/expire report the dismissal as the success it is rather than
    a misleading 'no open invite'."""
    if not repos['invite_repo'].claim(invite['invite_id'], to_status=status):
        return None
    spawned = tournament_spawn.spawn_autonomous_tournament(
        owner_id=invite['owner_id'],
        sandbox_id=invite['sandbox_id'],
        personality_repo=repos['personality_repo'],
        bankroll_repo=repos['bankroll_repo'],
        ledger_repo=repos['ledger_repo'],
        session_repo=repos['session_repo'],
        cash_table_repo=repos.get('cash_table_repo'),
        field_size=invite['field_size'],
        table_size=invite['table_size'],
        starting_stack=invite['starting_stack'],
        seed=invite['seed'],
        rng_seed=invite['seed'],
    )
    tid = spawned['tournament_id'] if spawned else None
    repos['invite_repo'].resolve(invite['invite_id'], status=status, tournament_id=tid)
    if spawned:
        return spawned
    return {'tournament_id': None, 'spawned': False}


def decline(
    *,
    invite_repo,
    personality_repo,
    bankroll_repo,
    ledger_repo,
    session_repo,
    cash_table_repo=None,
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
            'cash_table_repo': cash_table_repo,
        },
    )


def expire_due(
    *,
    invite_repo,
    personality_repo,
    bankroll_repo,
    ledger_repo,
    session_repo,
    cash_table_repo=None,
    now_iso: Optional[str] = None,
    sandbox_id: Optional[str] = None,
) -> list[dict]:
    """Expire every open invite past its `expires_at` → each starts
    autonomously. Returns the spawned tournaments. The expiry sweep the
    lobby/ticker calls; an absent player's invite simply waits until this runs.

    `sandbox_id` scopes the sweep to one sandbox — pass the sandbox whose lock the
    caller holds, so the autonomous spawn (which mutates that invite's OWN
    sandbox's escrow) never runs un-serialized for a foreign sandbox. None = a
    global sweep (no caller today; reserved for an admin/reconcile job)."""
    if invite_repo is None:
        return []
    now_iso = now_iso or _utcnow_iso()
    spawned: list[dict] = []
    for invite in invite_repo.list_open_due(now_iso=now_iso, sandbox_id=sandbox_id):
        result = _resolve_autonomously(
            invite,
            status=STATUS_EXPIRED,
            repos={
                'invite_repo': invite_repo,
                'personality_repo': personality_repo,
                'bankroll_repo': bankroll_repo,
                'ledger_repo': ledger_repo,
                'session_repo': session_repo,
                'cash_table_repo': cash_table_repo,
            },
        )
        if result:
            spawned.append(result)
    return spawned
