#!/usr/bin/env python3
"""Print a player's journey story from their hand history.

Hand beats roll up into per-session recaps, which roll up into the journey arc —
the player's ups and downs through the circuit. Facts come straight from
hand_history (deterministic, zero hallucination); pass --voiced to wrap each
session/arc in LLM prose ON TOP of those facts.

    docker compose run --rm --no-deps backend python scripts/journey.py Jeff
    docker compose run --rm --no-deps backend python scripts/journey.py Jeff --voiced
"""

import argparse
import json
import sqlite3
import sys

from poker.memory.hand_history import RecordedHand
from poker.memory.journey import (
    cash_pnl,
    journey_arc_facts,
    own_buy_in,
    session_facts,
    session_facts_text,
    summarize_session,
    voice_over,
)

DEFAULT_DB = "/app/data/poker_games.db"


def _row_to_recorded_hand(row: sqlite3.Row) -> RecordedHand:
    return RecordedHand.from_dict(
        {
            "game_id": row["game_id"],
            "hand_number": row["hand_number"],
            "timestamp": row["timestamp"],
            "players": json.loads(row["players_json"] or "[]"),
            "hole_cards": json.loads(row["hole_cards_json"] or "{}"),
            "community_cards": json.loads(row["community_cards_json"] or "[]"),
            "actions": json.loads(row["actions_json"] or "[]"),
            "winners": json.loads(row["winners_json"] or "[]"),
            "pot_size": row["pot_size"],
            "was_showdown": bool(row["showdown"]),
            "deck_seed": row["deck_seed"],
            "community_cards_by_phase": json.loads(row["community_cards_by_phase_json"] or "{}"),
        }
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("player", nargs="?", default="Jeff")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--voiced", action="store_true", help="LLM prose over the facts")
    ap.add_argument("--cash", action="store_true", help="circuit/cash games only (cash-*)")
    ap.add_argument("--limit-games", type=int, default=10)
    args = ap.parse_args()

    # Read-only + immutable so we can safely read a live (WAL) DB on a read-only
    # mount without needing -wal/-shm or taking locks.
    c = sqlite3.connect(f"file:{args.db}?mode=ro&immutable=1", uri=True)
    c.row_factory = sqlite3.Row
    # Games this player appears in, most-recently-played first.
    cash_clause = "AND game_id LIKE 'cash-%'" if args.cash else ""
    games = [
        r["game_id"]
        for r in c.execute(
            f"""SELECT game_id, MAX(hand_number) hn FROM hand_history
               WHERE players_json LIKE ? {cash_clause}
               GROUP BY game_id ORDER BY MAX(id) DESC LIMIT ?""",
            (f'%"{args.player}"%', args.limit_games),
        )
    ]
    if not games:
        print(f"No hands found for player {args.player!r} in {args.db}")
        return 1

    print("=" * 72)
    print(f"  THE JOURNEY OF {args.player.upper()}")
    print("=" * 72)

    session_stats = []
    for gid in games:
        rows = c.execute(
            "SELECT * FROM hand_history WHERE game_id=? ORDER BY hand_number", (gid,)
        ).fetchall()
        hands = [_row_to_recorded_hand(r) for r in rows]
        facts = session_facts(hands, args.player)
        if facts["hands_played"] == 0:
            continue
        # Money from the cash-session ledger (truth), not summed from hands.
        cs = c.execute(
            "SELECT total_buy_in, sponsor_principal, player_take_home, ended_at, stake_label "
            "FROM cash_sessions WHERE session_id=?",
            (gid,),
        ).fetchone()
        net = buy_in = take_home = stake = None
        if cs:
            net = cash_pnl(
                total_buy_in=cs["total_buy_in"],
                sponsor_principal=cs["sponsor_principal"],
                player_take_home=cs["player_take_home"],
                ended_at=cs["ended_at"],
            )
            buy_in = own_buy_in(cs["total_buy_in"], cs["sponsor_principal"])
            take_home = cs["player_take_home"]
            stake = cs["stake_label"]
        summary = summarize_session(
            args.player,
            hands_played=facts["hands_played"],
            hands_won=facts["hands_won"],
            biggest_pot_won=facts["biggest_pot_won"],
            net=net,
            buy_in=buy_in if net is not None else None,
            take_home=take_home if net is not None else None,
            stake_label=stake,
        )
        session_stats.append(
            {
                "hands_played": facts["hands_played"],
                "hands_won": facts["hands_won"],
                "biggest_pot_won": facts["biggest_pot_won"],
                "net_chips": net,
            }
        )
        print(f"\n── {stake or 'Session'}  ({gid[:20]}…) ──")
        print(f"   {summary}")
        for b in facts["beats"]:
            print(f"     • hand {b['hand_number']}: {b['text']}")
        if args.voiced:
            print(
                "\n   STORY:",
                voice_over(session_facts_text(summary, facts["beats"]), hero=args.player),
            )

    if session_stats:
        arc = journey_arc_facts(session_stats)
        print("\n" + "=" * 72)
        print("  THE ARC SO FAR")
        print("=" * 72)
        line = (
            f"  {arc['sessions']} sessions ({arc['ended_sessions']} finished, "
            f"{arc['winning_sessions']} winning). {arc['total_hands']} hands, "
            f"{arc['total_hands_won']} won. Net {arc['total_net_chips']:+,} chips. "
            f"Biggest pot {arc['biggest_pot']:,}."
        )
        print(line)
        if args.voiced:
            print("\n  STORY:", voice_over(line, hero=args.player, length="3-5 sentences"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
