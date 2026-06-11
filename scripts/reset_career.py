"""Reset a player's cash/career story to a clean fresh start (dev/playtest only).

Thin CLI over `cash_mode.dev_reset.reset_career_to_intake` (shared with the
in-app dev "reset to intake" button). Clears the orphan-prone cash session state,
deletes any zombie Sal persona, rebuilds the Scene-0 cast, restores the comped
bankroll, and resets `career_progress` so the Lucky Stack intake + Scene 0 replay
from the top.

Run inside the backend container, then RESTART the backend (to evict the
in-memory game), e.g.:
    docker compose exec -T backend python scripts/reset_career.py guest_jeff
    docker compose restart backend
"""

from __future__ import annotations

import sys

from cash_mode.dev_reset import reset_career_to_intake

DB = "/app/data/poker_games.db"
OWNER = sys.argv[1] if len(sys.argv) > 1 else "guest_jeff"


def main() -> int:
    stats = reset_career_to_intake(DB, OWNER)
    if stats is None:
        print(f"no sandbox for {OWNER}", file=sys.stderr)
        return 1
    print(
        f"reset {OWNER} (sandbox {stats['sandbox_id'][:8]}): "
        f"-{stats['games']} games -{stats['sessions']} sessions -{stats['events']} events "
        f"-{stats['zombies']} zombies, credited {stats['credited']}, "
        f"bankroll→{stats['bankroll']}. Now: docker compose restart backend"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
