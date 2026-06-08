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
from poker.memory.journey import journey_arc_facts, session_story, voice_over

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
    ap.add_argument("--limit-games", type=int, default=10)
    args = ap.parse_args()

    c = sqlite3.connect(args.db)
    c.row_factory = sqlite3.Row
    # Games this player appears in, most-recently-played first.
    games = [
        r["game_id"]
        for r in c.execute(
            """SELECT game_id, MAX(hand_number) hn FROM hand_history
               WHERE players_json LIKE ? GROUP BY game_id ORDER BY MAX(id) DESC LIMIT ?""",
            (f'%"{args.player}"%', args.limit_games),
        )
    ]
    if not games:
        print(f"No hands found for player {args.player!r} in {args.db}")
        return 1

    print("=" * 72)
    print(f"  THE JOURNEY OF {args.player.upper()}")
    print("=" * 72)

    stories = []
    for gid in games:
        rows = c.execute(
            "SELECT * FROM hand_history WHERE game_id=? ORDER BY hand_number", (gid,)
        ).fetchall()
        hands = [_row_to_recorded_hand(r) for r in rows]
        story = session_story(hands, args.player)
        if story["stats"]["hands_played"] == 0:
            continue
        stories.append(story)
        kind = "MAIN EVENT" if gid.startswith("tourney-") else "Table"
        print(f"\n── {kind}  ({gid[:20]}…) ──")
        print(f"   {story['summary']}")
        for b in story["beats"]:
            print(f"     • hand {b['hand_number']}: {b['text']}")
        if args.voiced:
            facts = story["summary"] + "\n" + "\n".join(b["text"] for b in story["beats"])
            print("\n   STORY:", voice_over(facts, hero=args.player))

    if stories:
        arc = journey_arc_facts(stories, args.player)
        print("\n" + "=" * 72)
        print("  THE ARC SO FAR")
        print("=" * 72)
        line = (
            f"  {arc['sessions']} sessions, {arc['winning_sessions']} winning. "
            f"{arc['total_hands']} hands, {arc['total_hands_won']} won. "
            f"Net {'+' if arc['total_net_chips'] >= 0 else ''}{arc['total_net_chips']:,} chips. "
            f"Peak stack {arc['peak_stack']:,}."
        )
        print(line)
        if args.voiced:
            print("\n  STORY:", voice_over(line, hero=args.player, length="3-5 sentences"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
