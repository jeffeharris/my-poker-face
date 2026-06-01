"""Regression: off-grid AIs must not be seated at the human's table.

Bug (observed in the field — "Zeus seated off grid"): the autonomous
lobby refresh (`cash_mode/lobby.py:refresh_unseated_tables`) excludes AIs
that are currently off-grid (on a vice or a side hustle) from every
seating surface. The player-facing seat-fill paths in
`flask_app/handlers/game_handler.py` did NOT — they built their candidate
pool straight from `list_eligible_for_cash_mode` (the full public corpus,
regardless of off-grid state). So when a seat opened at the human's table
the refill could pull a broke, hustling AI into the seat — the
`seated_and_offgrid` split-brain. With nearly the whole cast off-grid on
hustles, this was almost guaranteed: Zeus got seated mid-hustle while the
world ticker still narrated him "stepping out to earn on the side."

These tests pin the exclusion at the source: `_off_grid_pids` unions the
two off-grid repos, and `select_rejoin_candidates` (the gate shared by the
solo-table "Stay & play" prompt and `/api/cash/reseat`) drops an off-grid
candidate.
"""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from flask_app.handlers import game_handler


def _fake_extensions(*, eligible, on_hustle=None, on_vice=None, starting_bankroll=5000):
    """Build a `flask_app.extensions` stand-in for the seat-fill paths.

    `eligible` is the public corpus the repo would return; `on_hustle` /
    `on_vice` are the off-grid pid sets the respective repos report.
    """
    on_hustle = set(on_hustle or set())
    on_vice = set(on_vice or set())

    personality_repo = SimpleNamespace(
        list_eligible_for_cash_mode=lambda *, user_id=None: list(eligible),
    )
    bankroll_repo = SimpleNamespace(
        # No stored row → _project_candidate_buy_in falls back to the
        # personality cap, so every candidate can afford the seat (keeps
        # the test focused on the off-grid filter, not affordability).
        load_personality_knobs=lambda pid: SimpleNamespace(
            buy_in_multiplier=1.0,
            starting_bankroll=starting_bankroll,
            bankroll_rate=0.0,
        ),
        load_ai_bankroll=lambda pid, *, sandbox_id: None,
    )
    side_hustle_state_repo = SimpleNamespace(
        active_pids=lambda *, sandbox_id, now: set(on_hustle),
    )
    vice_state_repo = SimpleNamespace(
        active_pids=lambda *, sandbox_id, now: set(on_vice),
    )
    return dict(
        personality_repo=personality_repo,
        bankroll_repo=bankroll_repo,
        side_hustle_state_repo=side_hustle_state_repo,
        vice_state_repo=vice_state_repo,
    )


class TestOffGridPids(unittest.TestCase):
    def test_unions_vice_and_hustle(self):
        repos = _fake_extensions(eligible=[], on_hustle={'zeus'}, on_vice={'batman'})
        with patch.multiple('flask_app.extensions', **repos):
            from datetime import datetime

            pids = game_handler._off_grid_pids('sb-1', datetime.utcnow())
        self.assertEqual(pids, {'zeus', 'batman'})

    def test_no_sandbox_returns_empty(self):
        # No sandbox → no off-grid lookup (defensive; tournament/legacy).
        from datetime import datetime

        self.assertEqual(game_handler._off_grid_pids(None, datetime.utcnow()), set())

    def test_repo_error_is_fail_soft(self):
        def _boom(*, sandbox_id, now):
            raise RuntimeError("db locked")

        repos = _fake_extensions(eligible=[], on_hustle={'zeus'})
        repos['side_hustle_state_repo'] = SimpleNamespace(active_pids=_boom)
        with patch.multiple('flask_app.extensions', **repos):
            from datetime import datetime

            # The hustle repo blows up; the vice repo is empty → empty set,
            # not an exception.
            self.assertEqual(game_handler._off_grid_pids('sb-1', datetime.utcnow()), set())


class TestSelectRejoinExcludesOffGrid(unittest.TestCase):
    def _game(self):
        game_data = {'owner_id': 'guest_test', 'sandbox_id': 'sb-1'}
        # Human alone (the solo-table precondition); current_ante drives
        # the buy-in window (min = 40 BB = 400, max = 100 BB = 1000).
        you = SimpleNamespace(name='You', is_human=True, stack=500)
        game_state = SimpleNamespace(current_ante=10, players=(you,))
        return game_data, game_state

    def test_hustling_candidate_is_dropped(self):
        eligible = [
            {'personality_id': 'zeus', 'name': 'Zeus'},
            {'personality_id': 'batman', 'name': 'Batman'},
        ]
        repos = _fake_extensions(eligible=eligible, on_hustle={'zeus'})
        game_data, game_state = self._game()
        with patch.multiple('flask_app.extensions', **repos):
            picks = game_handler.select_rejoin_candidates(game_data, game_state, limit=2)
        names = {p['personality_id'] for p in picks}
        self.assertNotIn('zeus', names)
        self.assertIn('batman', names)

    def test_no_offgrid_keeps_all(self):
        eligible = [
            {'personality_id': 'zeus', 'name': 'Zeus'},
            {'personality_id': 'batman', 'name': 'Batman'},
        ]
        repos = _fake_extensions(eligible=eligible)
        game_data, game_state = self._game()
        with patch.multiple('flask_app.extensions', **repos):
            picks = game_handler.select_rejoin_candidates(game_data, game_state, limit=2)
        self.assertEqual({p['personality_id'] for p in picks}, {'zeus', 'batman'})


if __name__ == '__main__':
    unittest.main()
