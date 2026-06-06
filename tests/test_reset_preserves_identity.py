"""Regression: the between-hands reset must preserve a seat's stable identity.

`reset_game_state_for_new_hand` used to rebuild each `Player` with only
`name/stack/is_human`, dropping `personality_id`, `seat_id`, and `nickname`.
That stripped the tournament field id off every live seat after the first
hand, so `seat_key()` fell back to the display name and the multi-table
hand-boundary guard (`TournamentSession._guard_table_result`) rejected the
result — permanently freezing a human's tournament in HAND_OVER.

These tests lock in that the reset carries seat identity forward.
"""

import unittest

from poker.poker_game import (
    initialize_game_state,
    reset_game_state_for_new_hand,
)
from poker.table.seat import HumanSeat, PersonaSeat, seat_key


def _stamp_identity(game_state):
    """Stamp tournament-style identity onto a fresh game state's seats."""
    state = game_state
    for idx, player in enumerate(state.players):
        if player.is_human:
            state = state.update_player(
                idx,
                seat_id=HumanSeat("guest_jeff"),
            )
        else:
            pid = player.name.lower().replace(" ", "_")
            state = state.update_player(
                idx,
                personality_id=pid,
                seat_id=PersonaSeat(pid),
                nickname=player.name,
            )
    return state


class TestResetPreservesIdentity(unittest.TestCase):
    def test_reset_carries_personality_id_seat_id_and_nickname(self):
        state = initialize_game_state(["Joan of Arc", "King Tut"], human_name="Jeff")
        state = _stamp_identity(state)

        before = {p.name: (p.personality_id, p.seat_id, p.nickname) for p in state.players}
        new_state = reset_game_state_for_new_hand(state, deck_seed=0)

        # Every survivor keeps the identity it had before the reset.
        self.assertEqual(len(new_state.players), len(state.players))
        for p in new_state.players:
            self.assertIn(p.name, before)
            pid, sid, nick = before[p.name]
            self.assertEqual(p.personality_id, pid)
            self.assertEqual(p.seat_id, sid)
            self.assertEqual(p.nickname, nick)

    def test_seat_key_survives_reset_matches_field_id(self):
        """seat_key() must still resolve to the field id after a reset — this is
        the exact value the tournament hand-boundary keys on."""
        state = initialize_game_state(["Joan of Arc"], human_name="Jeff")
        state = _stamp_identity(state)

        new_state = reset_game_state_for_new_hand(state, deck_seed=0)
        keys = {seat_key(p) for p in new_state.players}

        # AI resolves to its personality_id, human to human:<owner_id>.
        self.assertIn("joan_of_arc", keys)
        self.assertIn("human:guest_jeff", keys)
        # The display name must NOT leak through as a key (the freeze symptom).
        self.assertNotIn("Joan of Arc", keys)

    def test_reset_without_identity_is_unaffected(self):
        """Regular/cash seats carry no identity; reset must leave them None."""
        state = initialize_game_state(["Alice", "Bob"], human_name="Player")
        new_state = reset_game_state_for_new_hand(state, deck_seed=0)
        for p in new_state.players:
            self.assertIsNone(p.personality_id)
            self.assertIsNone(p.seat_id)
            self.assertIsNone(p.nickname)


if __name__ == "__main__":
    unittest.main()
