"""Reset a player's cash/career story to a clean fresh start (dev/playtest only).

Clears the orphan-prone cash session state (games + cash_sessions + events) that
otherwise resurrects a deleted game (the "game no longer existed" 409→404 loop),
deletes any zombie Sal persona, re-opens the Scene-0 human seat, and resets
career_progress so the Lucky Stack intake + Scene 0 replay from the top.

Run inside the backend container, then RESTART the backend (to evict the
in-memory game), e.g.:
    docker compose exec -T backend python scripts/reset_career.py guest_jeff
    docker compose restart backend
"""
from __future__ import annotations

import sqlite3
import sys

from cash_mode.tables import open_slot
from poker.repositories.career_progress_repository import CareerProgress, CareerProgressRepository
from poker.repositories.user_preferences_repository import UserPreferencesRepository

DB = "/app/data/poker_games.db"
SCENE0 = "cash-scene0-001"
OWNER = sys.argv[1] if len(sys.argv) > 1 else "guest_jeff"


def main() -> int:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    sb = con.execute("SELECT sandbox_id FROM sandboxes WHERE owner_id=? LIMIT 1", (OWNER,)).fetchone()
    if not sb:
        print(f"no sandbox for {OWNER}", file=sys.stderr)
        return 1
    SB = sb["sandbox_id"]

    # 1. Clear all session state (the orphan that resurrects dead games).
    g = con.execute("DELETE FROM games WHERE owner_id=? AND game_id LIKE 'cash-%'", (OWNER,)).rowcount
    s = con.execute("DELETE FROM cash_sessions WHERE owner_id=?", (OWNER,)).rowcount
    e = con.execute("DELETE FROM cash_session_events WHERE owner_id=?", (OWNER,)).rowcount
    z = con.execute(
        "DELETE FROM personalities WHERE personality_id LIKE 'sal_moretti_v%' "
        "OR (name='Sal Moretti' AND personality_id!='sal_moretti')"
    ).rowcount

    # 2. Re-open the Scene-0 human seat (credit any table chips back) + normalise the cast.
    row = con.execute(
        "SELECT seats_json FROM cash_tables WHERE table_id=? AND sandbox_id=?", (SCENE0, SB)
    ).fetchone()
    credited = 0
    if row:
        import json

        seats = json.loads(row["seats_json"])
        sal_idx = None
        larry_chips = 0
        for i, st in enumerate(seats):
            if st.get("kind") == "human":
                credited = int(st.get("chips", 0))
                seats[i] = open_slot()
            elif st.get("kind") == "ai":
                pid = st.get("personality_id", "")
                if pid.startswith("sal_moretti"):
                    st["personality_id"] = "sal_moretti"
                    sal_idx = i
                elif pid == "loose_larry":
                    larry_chips = int(st.get("chips", 0))
        # The fresh seeder gives Sal 3x the fish buy-in so he can STACK Larry in
        # the finale. This dev reset reuses the existing table, so top Sal's seat
        # up to 3x Larry's stack and debit the difference from his (sandbox-scoped)
        # bankroll — a clean transfer, no minting.
        sal_bumped = 0
        if sal_idx is not None and larry_chips:
            target = larry_chips * 3
            cur = int(seats[sal_idx].get("chips", 0))
            if target > cur:
                sal_bumped = target - cur
                seats[sal_idx]["chips"] = target
                con.execute(
                    "UPDATE ai_bankroll_state SET chips = chips - ? "
                    "WHERE personality_id='sal_moretti' AND sandbox_id=?",
                    (sal_bumped, SB),
                )
        con.execute(
            "UPDATE cash_tables SET seats_json=? WHERE table_id=? AND sandbox_id=?",
            (json.dumps(seats), SCENE0, SB),
        )
        if credited:
            con.execute(
                "UPDATE player_bankroll_state SET chips=chips+? WHERE player_id=?", (credited, OWNER)
            )
    con.commit()

    # 3. Fresh-but-active career + clear bio.
    CareerProgressRepository(DB).save(
        CareerProgress(
            sandbox_id=SB, owner_id=OWNER, career_active=True, intake_complete=False,
            revealed_table_ids=[SCENE0], scene0_seeded=True, scene0_table_id=SCENE0,
            scene0_fish_id="loose_larry", tutorial_complete=False, home_court_table_id=None,
            vouched_by=[],
        )
    )
    UserPreferencesRepository(DB).set_bio(OWNER, "")
    print(
        f"reset {OWNER} (sandbox {SB[:8]}): -{g} games -{s} sessions -{e} events "
        f"-{z} zombies, credited {credited}. Now: docker compose restart backend"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
