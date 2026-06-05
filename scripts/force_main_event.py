"""Dev helper: force a circuit Main Event invite to appear for testing.

Bypasses the chairman's FLUSH gate (`maybe_offer_main_event`) and offers an
invite directly, so the Main Event card shows in the cash lobby on the next load
without having to engineer a flush bank. Also ensures the owner's sandbox is
seeded (AI bankrolls + lobby tables) so the tournament can actually be fielded
when accepted.

Usage (inside the backend container):
    docker compose exec -T backend python -m scripts.force_main_event            # guest_jeff
    docker compose exec -T backend python -m scripts.force_main_event guest_jeff
    docker compose exec -T backend python -m scripts.force_main_event guest_jeff --buy-in 0 --field 18

Then open cash mode in the app and refresh the lobby — the Main Event card appears.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_root = str(Path(__file__).parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("owner_id", nargs="?", default="guest_jeff")
    p.add_argument("--buy-in", type=int, default=0)
    p.add_argument("--field", type=int, default=18)
    p.add_argument("--table", type=int, default=6)
    p.add_argument("--starting-stack", type=int, default=10_000)
    p.add_argument(
        "--expiry-seconds",
        type=int,
        default=600,
        help="registration window before auto-expiry→autonomous (0=no expiry)",
    )
    args = p.parse_args()

    from flask_app import create_app

    app = create_app()
    with app.app_context():
        from datetime import datetime, timedelta

        from cash_mode.lobby import ensure_ai_bankrolls_seeded, ensure_lobby_seeded
        from flask_app import extensions
        from flask_app.services import tournament_invites as inv
        from flask_app.services.sandbox_resolver import resolve_default_sandbox_for

        owner = args.owner_id
        sb = resolve_default_sandbox_for(owner, sandbox_repo=extensions.sandbox_repo)
        print(f"owner={owner} sandbox={sb}")

        # Make sure the sandbox can field a tournament + render a lobby.
        ensure_ai_bankrolls_seeded(
            bankroll_repo=extensions.bankroll_repo,
            personality_repo=extensions.personality_repo,
            sandbox_id=sb,
        )
        ensure_lobby_seeded(
            cash_table_repo=extensions.cash_table_repo,
            personality_repo=extensions.personality_repo,
            bankroll_repo=extensions.bankroll_repo,
            user_id=owner,
            sandbox_id=sb,
        )

        existing = inv.active_invite(extensions.tournament_invite_repo, owner)
        if existing is not None:
            print(
                f"an invite is already open: {existing['invite_id']} "
                f"(status={existing['status']}, expires_at={existing['expires_at']})"
            )
            print("→ open cash mode and look for the Main Event card.")
            return 0

        expires_at = None
        if args.expiry_seconds > 0:
            expires_at = (datetime.utcnow() + timedelta(seconds=args.expiry_seconds)).isoformat()

        invite = inv.offer(
            invite_repo=extensions.tournament_invite_repo,
            session_repo=extensions.tournament_session_repo,
            owner_id=owner,
            sandbox_id=sb,
            buy_in=args.buy_in,
            field_size=args.field,
            table_size=args.table,
            starting_stack=args.starting_stack,
            expires_at=expires_at,
        )
        if invite is None:
            print("offer suppressed — a tournament is probably already active for this owner.")
            return 1
        print(
            f"OFFERED Main Event: invite_id={invite['invite_id']} buy_in={invite['buy_in']} "
            f"field={invite['field_size']} expires_at={invite['expires_at']}"
        )
        print("→ open cash mode (The Circuit) and refresh the lobby — the Main Event card is up.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
