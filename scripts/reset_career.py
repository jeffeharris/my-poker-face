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

    # Loose Larry is the scene-only fish — never auto-circulated. If a personality
    # sync ever flips his global flag to circulating, the world's eligible pool can
    # pull him (and busted-seat refills can seat strangers in his place). Pin it.
    con.execute("UPDATE personalities SET circulating=0 WHERE personality_id='loose_larry'")

    # 2. REBUILD the Scene-0 cast in place: re-open the human seat and re-seat
    #    Sal + Loose Larry in their pinned seats. A prior finale-bust refill could
    #    leave a stranger in the fish seat ("where'd Larry go, who's this dad-jokes
    #    guy?"); rebuilding from scratch is the clean fix. Conservation-safe — every
    #    current occupant's chips go back to their owner before we re-seat.
    import json

    from cash_mode.career_progression import (
        SAL_ID,
        SCENE0_FISH_ID,
        SCENE0_FISH_SEAT,
        SCENE0_SAL_SEAT,
        SCENE0_STAKE,
    )
    from cash_mode.stakes_ladder import table_buy_in_window
    from cash_mode.tables import ai_slot, ai_slot_fish

    row = con.execute(
        "SELECT seats_json FROM cash_tables WHERE table_id=? AND sandbox_id=?", (SCENE0, SB)
    ).fetchone()
    credited = 0
    if row:
        seats = json.loads(row["seats_json"])
        # Return every occupant's chips to their owner.
        for st in seats:
            chips = int(st.get("chips", 0) or 0)
            if chips <= 0:
                continue
            if st.get("kind") == "human":
                credited += chips
                con.execute(
                    "UPDATE player_bankroll_state SET chips=chips+? WHERE player_id=?",
                    (chips, OWNER),
                )
            elif st.get("kind") == "ai" and st.get("personality_id"):
                con.execute(
                    "UPDATE ai_bankroll_state SET chips=chips+? "
                    "WHERE personality_id=? AND sandbox_id=?",
                    (chips, st["personality_id"], SB),
                )

        # Fresh seats: all open, then Larry (fish) + Sal (deep, 3x to stack Larry
        # in the finale), each debited from its own (sandbox-scoped) bankroll.
        _, min_buy_in, _ = table_buy_in_window(SCENE0_STAKE)
        larry_buy = min_buy_in
        sal_buy = larry_buy * 3
        seats = [open_slot() for _ in range(len(seats))]
        con.execute(
            "UPDATE ai_bankroll_state SET chips=chips-? WHERE personality_id=? AND sandbox_id=?",
            (larry_buy, SCENE0_FISH_ID, SB),
        )
        seats[SCENE0_FISH_SEAT] = ai_slot_fish(SCENE0_FISH_ID, larry_buy)
        con.execute(
            "UPDATE ai_bankroll_state SET chips=chips-? WHERE personality_id=? AND sandbox_id=?",
            (sal_buy, SAL_ID, SB),
        )
        seats[SCENE0_SAL_SEAT] = ai_slot(SAL_ID, sal_buy)

        con.execute(
            "UPDATE cash_tables SET seats_json=? WHERE table_id=? AND sandbox_id=?",
            (json.dumps(seats), SCENE0, SB),
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
