"""Reset a player's cash/career story back to the Lucky Stack intake (dev only).

The shared core behind both the `scripts/reset_career.py` CLI and the dev-only
`POST /api/cash/dev/reset-intake` route (the in-app "reset to intake" button we
use while playtesting the Circuit). It clears the orphan-prone cash session
state (games + cash_sessions + events) that otherwise resurrects a deleted game,
deletes any zombie Sal persona, rebuilds the Scene-0 cast in their pinned seats
(conservation-safe — occupants' chips return to their owner first), restores the
comped starting bankroll, and resets `career_progress` so intake + Scene 0 replay
from the top.

It does NOT touch the in-memory game registry — a long-running server still
holds the evicted game in memory, so the CLI tells you to restart the backend
and the route evicts it via `game_state_service.delete_game` before calling here.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

# Mirrors flask_app.routes.cash_routes.DEFAULT_PLAYER_STARTING_BANKROLL — the
# comp granted once on first entry; reset to it for a clean fresh run.
COMP_BANKROLL = 200


def reset_career_to_intake(db_path: str, owner_id: str) -> Optional[dict]:
    """Wipe `owner_id`'s career back to the pre-intake state.

    Returns a stats dict (games/sessions/events cleared, chips credited, new
    bankroll) on success, or None if the owner has no sandbox. Does not restart
    the backend or evict the in-memory game — callers handle that.
    """
    from cash_mode.career_progression import (
        SAL_ID,
        SCENE0_FISH_ID,
        SCENE0_FISH_SEAT,
        SCENE0_SAL_SEAT,
        SCENE0_STAKE,
        SCENE0_TABLE_ID,
    )
    from cash_mode.stakes_ladder import table_buy_in_window
    from cash_mode.tables import ai_slot, ai_slot_fish, open_slot
    from poker.repositories.career_progress_repository import (
        CareerProgress,
        CareerProgressRepository,
    )
    from poker.repositories.user_preferences_repository import UserPreferencesRepository

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        sb = con.execute(
            "SELECT sandbox_id FROM sandboxes WHERE owner_id=? LIMIT 1", (owner_id,)
        ).fetchone()
        if not sb:
            return None
        sandbox_id = sb["sandbox_id"]

        # 0. Reset the GATING state FIRST — on the repos' own connections, before
        #    any destructive commit below. intake_complete=False is what lets the
        #    player redo intake, so writing it up front means a repo failure here
        #    aborts cleanly (nothing destructive committed yet), and a failure in
        #    the destructive block below still leaves the gate reading "intake
        #    pending" (the reset is re-runnable). con has only done a SELECT so
        #    far and holds no write lock, so these separate connections don't
        #    contend. (Not a single transaction — true atomicity would mean
        #    threading one connection through the repos; overkill for a dev tool.)
        CareerProgressRepository(db_path).save(
            CareerProgress(
                sandbox_id=sandbox_id,
                owner_id=owner_id,
                career_active=True,
                intake_complete=False,
                revealed_table_ids=[SCENE0_TABLE_ID],
                scene0_seeded=True,
                scene0_table_id=SCENE0_TABLE_ID,
                scene0_fish_id="loose_larry",
                tutorial_complete=False,
                home_court_table_id=None,
                vouched_by=[],
            )
        )
        UserPreferencesRepository(db_path).set_bio(owner_id, "")

        # 1. Clear all session state (the orphan that resurrects dead games).
        games = con.execute(
            "DELETE FROM games WHERE owner_id=? AND game_id LIKE 'cash-%'", (owner_id,)
        ).rowcount
        sessions = con.execute("DELETE FROM cash_sessions WHERE owner_id=?", (owner_id,)).rowcount
        events = con.execute(
            "DELETE FROM cash_session_events WHERE owner_id=?", (owner_id,)
        ).rowcount
        zombies = con.execute(
            "DELETE FROM personalities WHERE personality_id LIKE 'sal_moretti_v%' "
            "OR (name='Sal Moretti' AND personality_id!='sal_moretti')"
        ).rowcount
        # Loose Larry is the scene-only fish — pin him non-circulating so a sync
        # can't let the world pull him into the eligible pool.
        con.execute("UPDATE personalities SET circulating=0 WHERE personality_id='loose_larry'")

        # 2. REBUILD the Scene-0 cast in place: re-open the human seat and re-seat
        #    Sal + Loose Larry in their pinned seats. Conservation-safe — every
        #    current occupant's chips go back to their owner before we re-seat.
        row = con.execute(
            "SELECT seats_json FROM cash_tables WHERE table_id=? AND sandbox_id=?",
            (SCENE0_TABLE_ID, sandbox_id),
        ).fetchone()
        credited = 0
        if row:
            seats = json.loads(row["seats_json"])
            for st in seats:
                chips = int(st.get("chips", 0) or 0)
                if chips <= 0:
                    continue
                if st.get("kind") == "human":
                    credited += chips
                    con.execute(
                        "UPDATE player_bankroll_state SET chips=chips+? WHERE player_id=?",
                        (chips, owner_id),
                    )
                elif st.get("kind") == "ai" and st.get("personality_id"):
                    con.execute(
                        "UPDATE ai_bankroll_state SET chips=chips+? "
                        "WHERE personality_id=? AND sandbox_id=?",
                        (chips, st["personality_id"], sandbox_id),
                    )

            # Fresh seats: all open, then Larry (fish) + Sal (deep, 3x to stack
            # Larry in the finale), each debited from its own bankroll.
            _, min_buy_in, _ = table_buy_in_window(SCENE0_STAKE)
            larry_buy = min_buy_in
            sal_buy = larry_buy * 3
            seats = [open_slot() for _ in range(len(seats))]
            con.execute(
                "UPDATE ai_bankroll_state SET chips=chips-? "
                "WHERE personality_id=? AND sandbox_id=?",
                (larry_buy, SCENE0_FISH_ID, sandbox_id),
            )
            seats[SCENE0_FISH_SEAT] = ai_slot_fish(SCENE0_FISH_ID, larry_buy)
            con.execute(
                "UPDATE ai_bankroll_state SET chips=chips-? "
                "WHERE personality_id=? AND sandbox_id=?",
                (sal_buy, SAL_ID, sandbox_id),
            )
            seats[SCENE0_SAL_SEAT] = ai_slot(SAL_ID, sal_buy)
            con.execute(
                "UPDATE cash_tables SET seats_json=? WHERE table_id=? AND sandbox_id=?",
                (json.dumps(seats), SCENE0_TABLE_ID, sandbox_id),
            )

        # 2b. Restore the comped starting bankroll (drains to 0 across replays).
        bk = con.execute(
            "UPDATE player_bankroll_state SET chips=?, starting_bankroll=? WHERE player_id=?",
            (COMP_BANKROLL, COMP_BANKROLL, owner_id),
        ).rowcount
        if bk == 0:
            con.execute(
                "INSERT INTO player_bankroll_state (player_id, chips, starting_bankroll) "
                "VALUES (?,?,?)",
                (owner_id, COMP_BANKROLL, COMP_BANKROLL),
            )
        con.commit()
    finally:
        con.close()

    return {
        "owner_id": owner_id,
        "sandbox_id": sandbox_id,
        "games": games,
        "sessions": sessions,
        "events": events,
        "zombies": zombies,
        "credited": credited,
        "bankroll": COMP_BANKROLL,
    }
