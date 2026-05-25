"""Unit tests for `flask_app.services.holdings_view._collect_player_rows`.

Specifically covers the guest-player PnL lookup path: when a human seat
has no `users` row, the row builder must still resolve cash PnL by
falling back to the player's most-recent `games.owner_name` (the seat
display name historically written to `cash_pair_stats.observer_id`).
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from cash_mode.bankroll import PlayerBankrollState
from flask_app.services.holdings_view import (
    _collect_player_rows,
    _fetch_recent_owner_names,
)
from poker.repositories import create_repos


def _insert_game(
    db_path: str,
    *,
    game_id: str,
    owner_id: str,
    owner_name,
    created_at: str,
) -> None:
    """Insert a minimal `games` row for owner_name lookup tests."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO games (
                game_id, created_at, updated_at, phase, num_players,
                pot_size, game_state_json, owner_id, owner_name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game_id,
                created_at,
                created_at,
                'PRE_FLOP',
                2,
                0.0,
                '{}',
                owner_id,
                owner_name,
            ),
        )


class _HoldingsViewBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        self.repos = create_repos(self.tmp.name)
        self.db_path = self.tmp.name
        self.user_repo = self.repos['user_repo']
        self.bankroll_repo = self.repos['bankroll_repo']
        self.relationship_repo = self.repos['relationship_repo']

    def tearDown(self):
        try:
            os.unlink(self.tmp.name)
        except FileNotFoundError:
            pass

    def _seed_bankroll(self, player_id: str, chips: int = 5_000) -> None:
        self.bankroll_repo.save_player_bankroll(
            PlayerBankrollState(
                player_id=player_id,
                chips=chips,
                starting_bankroll=chips,
            )
        )


class TestCollectPlayerRowsResolution(_HoldingsViewBase):
    def test_user_row_present_uses_user_name(self):
        # When `users.name` is set, the user_name key is what we hit on —
        # owner_name fallback is irrelevant.
        self.user_repo.create_google_user(
            google_sub='abc',
            email='alice@example.com',
            name='Alice',
        )
        player_id = 'google_abc'
        self._seed_bankroll(player_id)

        cash_pnl = {
            'Alice': {
                'chips_won': 1_200,
                'chips_lost': 400,
                'net_pnl': 800,
                'hands_played_cash': 50,
            },
        }
        rows = _collect_player_rows(
            user_repo=self.user_repo,
            cash_pnl_by_observer=cash_pnl,
            db_path=self.db_path,
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['name'], 'Alice')
        self.assertEqual(row['net_pnl'], 800)
        self.assertEqual(row['chips_won'], 1_200)
        self.assertEqual(row['chips_lost'], 400)

    def test_guest_finds_pnl_via_owner_name_fallback(self):
        # No `users` row for guest_jeff. The relationship detector wrote
        # observer_id="Jeff" (display name from the cash seat). Fallback
        # must surface the PnL.
        player_id = 'guest_jeff'
        self._seed_bankroll(player_id)
        _insert_game(
            self.db_path,
            game_id='g1',
            owner_id=player_id,
            owner_name='Jeff',
            created_at='2026-05-20 12:00:00',
        )
        cash_pnl = {
            'Jeff': {
                'chips_won': 900,
                'chips_lost': 250,
                'net_pnl': 650,
                'hands_played_cash': 30,
            },
        }

        rows = _collect_player_rows(
            user_repo=self.user_repo,
            cash_pnl_by_observer=cash_pnl,
            db_path=self.db_path,
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['name'], 'Jeff')
        self.assertEqual(row['net_pnl'], 650)
        self.assertEqual(row['chips_won'], 900)
        self.assertEqual(row['chips_lost'], 250)

    def test_guest_with_no_games_degrades_to_zeros(self):
        # No users row, no games row — no fallback key available. Row
        # still renders with zero PnL rather than crashing.
        player_id = 'guest_orphan'
        self._seed_bankroll(player_id, chips=2_000)

        rows = _collect_player_rows(
            user_repo=self.user_repo,
            cash_pnl_by_observer={},
            db_path=self.db_path,
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['name'], 'guest_orphan')
        self.assertEqual(row['stored_chips'], 2_000)
        self.assertEqual(row['net_pnl'], 0)
        self.assertEqual(row['chips_won'], 0)
        self.assertEqual(row['chips_lost'], 0)

    def test_most_recent_owner_name_wins(self):
        # Player has played under multiple display names over time. The
        # PnL is keyed on the most recent owner_name.
        player_id = 'guest_multi'
        self._seed_bankroll(player_id)
        _insert_game(
            self.db_path,
            game_id='g_old',
            owner_id=player_id,
            owner_name='OldName',
            created_at='2026-01-01 12:00:00',
        )
        _insert_game(
            self.db_path,
            game_id='g_new',
            owner_id=player_id,
            owner_name='NewName',
            created_at='2026-05-20 12:00:00',
        )
        cash_pnl = {
            'NewName': {
                'chips_won': 500,
                'chips_lost': 0,
                'net_pnl': 500,
                'hands_played_cash': 10,
            },
            'OldName': {
                'chips_won': 9_999,
                'chips_lost': 0,
                'net_pnl': 9_999,
                'hands_played_cash': 99,
            },
        }

        rows = _collect_player_rows(
            user_repo=self.user_repo,
            cash_pnl_by_observer=cash_pnl,
            db_path=self.db_path,
        )

        self.assertEqual(rows[0]['name'], 'NewName')
        self.assertEqual(rows[0]['net_pnl'], 500)

    def test_pnl_keyed_directly_on_player_id_still_works(self):
        # When `cash_pair_stats.observer_id` IS the player_id (the modern
        # canonical key), no fallback is needed and the row still finds
        # its PnL.
        player_id = 'guest_modern'
        self._seed_bankroll(player_id)
        cash_pnl = {
            player_id: {
                'chips_won': 300,
                'chips_lost': 100,
                'net_pnl': 200,
                'hands_played_cash': 5,
            },
        }

        rows = _collect_player_rows(
            user_repo=self.user_repo,
            cash_pnl_by_observer=cash_pnl,
            db_path=self.db_path,
        )

        self.assertEqual(rows[0]['net_pnl'], 200)
        self.assertEqual(rows[0]['chips_won'], 300)

    def test_owner_name_null_on_all_games_falls_through(self):
        # If every game row for this owner has NULL/empty owner_name, the
        # bulk fetch returns nothing and PnL is zero.
        player_id = 'guest_null'
        self._seed_bankroll(player_id)
        _insert_game(
            self.db_path,
            game_id='g_null',
            owner_id=player_id,
            owner_name=None,
            created_at='2026-05-20 12:00:00',
        )
        _insert_game(
            self.db_path,
            game_id='g_empty',
            owner_id=player_id,
            owner_name='',
            created_at='2026-05-20 13:00:00',
        )

        rows = _collect_player_rows(
            user_repo=self.user_repo,
            cash_pnl_by_observer={
                'Jeff': {
                    'chips_won': 1,
                    'chips_lost': 0,
                    'net_pnl': 1,
                    'hands_played_cash': 1,
                }
            },
            db_path=self.db_path,
        )

        self.assertEqual(rows[0]['net_pnl'], 0)


class TestFetchRecentOwnerNames(_HoldingsViewBase):
    def test_empty_input_returns_empty(self):
        self.assertEqual(_fetch_recent_owner_names(self.db_path, []), {})

    def test_returns_most_recent_per_owner(self):
        _insert_game(
            self.db_path,
            game_id='g1',
            owner_id='guest_a',
            owner_name='Alpha',
            created_at='2026-01-01 12:00:00',
        )
        _insert_game(
            self.db_path,
            game_id='g2',
            owner_id='guest_a',
            owner_name='AlphaTwo',
            created_at='2026-05-01 12:00:00',
        )
        _insert_game(
            self.db_path,
            game_id='g3',
            owner_id='guest_b',
            owner_name='Bravo',
            created_at='2026-03-15 12:00:00',
        )

        result = _fetch_recent_owner_names(
            self.db_path,
            ['guest_a', 'guest_b', 'guest_missing'],
        )
        self.assertEqual(result.get('guest_a'), 'AlphaTwo')
        self.assertEqual(result.get('guest_b'), 'Bravo')
        self.assertNotIn('guest_missing', result)


if __name__ == '__main__':
    unittest.main()
