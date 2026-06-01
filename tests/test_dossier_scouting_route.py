"""Live HTTP integration test for the dossier scouting gate.

Exercises the real `/api/character/<id>/dossier` route end to end — request
context, extensions wiring, the kill-switch read, the real lifetime fold +
load, and the gate — against a real (temp) schema. Complements the pure unit
tests in test_dossier_scouting.py by proving the wiring, not just the logic.

This is the integration check standing in for a 25-hand human playthrough:
it seeds an opponent's observed-hand count via the real fold path, then hits
the actual endpoint and asserts the gate declassifies reads as the count
crosses thresholds.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.integration

from flask_app import create_app
from poker.repositories import create_repos
from poker.repositories.schema_manager import SchemaManager
from flask_app.services import sandbox_resolver

OBSERVER = "obs_jeff"
PERSONALITY = "greg"


def _seed_opponent_model(db_path, game_id, observer_id, opponent_id, hands):
    """Insert a per-game opponent_models row with `hands` observed."""
    counts = {
        'hands_dealt': hands, 'hands_observed': hands,
        '_vpip_count': max(1, hands // 3), '_pfr_count': max(1, hands // 5),
        '_bet_raise_count': max(1, hands // 4), '_call_count': max(1, hands // 6),
        '_showdowns': max(1, hands // 10), '_showdowns_won': max(0, hands // 20),
        # Deep postflop opportunity counts (v125) scaled to hands so the
        # Tier-2 sample gates clear at high hand counts (a 500-hand sample has
        # plenty of c-bets, barrels, equity reads).
        '_all_in_count': max(0, hands // 50),
        '_fold_to_cbet_count': max(1, hands // 4),
        '_cbet_faced_count': max(1, hands // 3),
        '_cbet_attempt_count': max(1, hands // 4),
        '_postflop_seen_as_pfr_count': max(1, hands // 4),
        '_barrel_count': max(1, hands // 8),
        '_barrel_opportunity_count': max(1, hands // 5),
        '_third_barrel_count': max(0, hands // 12),
        '_third_barrel_opportunity_count': max(1, hands // 8),
        '_postflop_bet_raise_count': max(1, hands // 3),
        '_postflop_call_count': max(1, hands // 4),
        '_equity_betting_count': max(1, hands // 4),
        '_equity_raising_count': max(1, hands // 6),
        '_equity_calling_count': max(1, hands // 5),
        # Preflop opportunity counts (limp_rate gate's denominator) + the
        # limp numerator, scaled to hands so the limp_rate tier clears at
        # high hand counts.
        '_preflop_voluntary_action_count': max(1, hands // 3),
        '_preflop_voluntary_opportunities': max(1, hands // 2),
        '_preflop_open_raise_count': max(1, hands // 5),
        '_preflop_open_opportunities': max(1, hands // 2),
        '_limp_count': max(1, hands // 6),
        # Sizing-aware counts/sums (v133), scaled so the sizing tiers clear at
        # high hand counts (big bets faced + both equity bins).
        '_big_bet_faced_count': max(1, hands // 5),
        '_fold_to_big_bet_count': max(1, hands // 8),
        '_equity_betting_big_count': max(1, hands // 6),
        '_equity_betting_small_count': max(1, hands // 6),
        '_equity_betting_big_sum': max(1, hands // 6) * 0.8,
        '_equity_betting_small_sum': max(1, hands // 6) * 0.3,
    }
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO opponent_models
                (game_id, observer_name, opponent_name, observer_id,
                 opponent_id, hands_observed, tendencies_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (game_id, "Jeff", "Greg", observer_id, opponent_id, hands,
             json.dumps(counts)),
        )
        conn.commit()
    finally:
        conn.close()


class TestDossierScoutingRoute(unittest.TestCase):
    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        SchemaManager(self.test_db.name).ensure_schema()
        self.repos = create_repos(self.test_db.name)

        def mock_init_persistence():
            import flask_app.extensions as ext
            for key, repo in self.repos.items():
                if key == 'db_path':
                    ext.persistence_db_path = repo
                    continue
                setattr(ext, key, repo)

        with patch('flask_app.extensions.init_persistence', mock_init_persistence):
            self.app = create_app()
        self.app.testing = True
        self.client = self.app.test_client()

        # Fresh resolver cache so the sandbox we seed under matches the one
        # the route resolves for this observer.
        sandbox_resolver.clear_cache()
        self.sandbox_id = sandbox_resolver.resolve_default_sandbox_for(
            OBSERVER, sandbox_repo=self.repos['sandbox_repo']
        )

        # Authenticated observer + a resolvable personality id.
        self._auth = patch(
            'flask_app.extensions.auth_manager',
            MagicMock(get_current_user=MagicMock(return_value={'id': OBSERVER})),
        )
        self._auth.start()
        self._pid = patch(
            'flask_app.routes.character_routes._resolve_personality_id',
            return_value=PERSONALITY,
        )
        self._pid.start()

    def tearDown(self):
        self._auth.stop()
        self._pid.stop()
        sandbox_resolver.clear_cache()

    def _fold(self, hands):
        _seed_opponent_model(
            self.test_db.name, "g1", OBSERVER, PERSONALITY, hands
        )
        self.repos['game_repo'].fold_observations_into_lifetime(
            "g1", self.sandbox_id
        )

    def _dossier(self):
        resp = self.client.get(f'/api/character/{PERSONALITY}/dossier')
        self.assertEqual(resp.status_code, 200)
        return resp.get_json()

    def test_below_floor_classified(self):
        self._fold(10)
        body = self._dossier()
        scouting = body.get('scouting')
        self.assertIsNotNone(scouting)
        self.assertEqual(scouting['hands_observed'], 10)
        self.assertFalse(scouting['floor_met'])
        # Earnable reads stripped from the live payload.
        self.assertIsNone(body['observation'])

    def test_partial_unlock_reveals_basic_read(self):
        self._fold(50)  # >= floor(25), pfr(40); < aggression(60)
        body = self._dossier()
        scouting = body['scouting']
        self.assertTrue(scouting['floor_met'])
        self.assertIn('pfr', scouting['unlocked'])
        self.assertNotIn('aggression_factor', scouting['unlocked'])
        # Observation present with the unlocked bits, AF redacted.
        self.assertIsNotNone(body['observation'])
        self.assertIsNotNone(body['observation']['vpip'])
        self.assertIsNone(body['observation']['aggression_factor'])

    def test_full_unlock(self):
        self._fold(500)
        body = self._dossier()
        self.assertEqual(body['scouting']['locked'], [])
        self.assertIsNotNone(body['observation']['aggression_factor'])

    # --- Informant purchase flow (Phase 3) ---------------------------------

    def _seed_bankroll(self, chips):
        from cash_mode.bankroll import PlayerBankrollState
        self.repos['bankroll_repo'].save_player_bankroll(
            PlayerBankrollState(player_id=OBSERVER, chips=chips, starting_bankroll=chips)
        )

    def _buy(self, section_id):
        return self.client.post(
            f'/api/character/{PERSONALITY}/informant',
            json={'section_id': section_id},
        )

    def test_informant_buy_debits_and_unlocks(self):
        self._fold(5)            # below floor — track_record locked by grind
        self._seed_bankroll(5000)

        resp = self._buy('track_record')   # price 1000
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        body = resp.get_json()
        self.assertEqual(body['bankroll'], 4000)        # 5000 - 1000 debited
        self.assertNotIn(
            'track_record', {o['id'] for o in body['scouting']['informant_offers']}
        )
        # The purchase persists across requests: the dossier no longer offers
        # the bought section, and its items count as unlocked despite < floor.
        dossier = self._dossier()
        self.assertNotIn(
            'track_record',
            {o['id'] for o in dossier['scouting']['informant_offers']},
        )
        self.assertIn('track_record', dossier['scouting']['unlocked'])

    def test_informant_double_buy_does_not_double_charge(self):
        self._fold(5)
        self._seed_bankroll(5000)
        self.assertEqual(self._buy('track_record').status_code, 200)
        again = self._buy('track_record')
        self.assertEqual(again.status_code, 409)        # already owned
        # Bankroll only debited once.
        self.assertEqual(
            self.repos['bankroll_repo'].load_player_bankroll(OBSERVER).chips, 4000
        )

    def test_informant_insufficient_bankroll(self):
        self._fold(5)
        self._seed_bankroll(100)                        # < 1000
        resp = self._buy('track_record')
        self.assertEqual(resp.status_code, 402)
        # Nothing charged, nothing unlocked.
        self.assertEqual(
            self.repos['bankroll_repo'].load_player_bankroll(OBSERVER).chips, 100
        )
        self.assertEqual(
            self.repos['game_repo'].load_informant_unlocks(
                self.sandbox_id, OBSERVER, PERSONALITY
            ),
            set(),
        )

    def test_informant_unknown_section_400(self):
        self._seed_bankroll(5000)
        self.assertEqual(self._buy('not_a_section').status_code, 400)

    # --- Durable pressure + memorable (Tier 1) -----------------------------

    def _seed_pressure_and_memorable(self):
        """Seed a finished game (owner-stamped) with pressure events and a
        memorable hand for PERSONALITY — the cross-game history the durable
        dossier should surface even with no live game in memory."""
        conn = sqlite3.connect(self.test_db.name)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO games "
                "(game_id, phase, num_players, pot_size, game_state_json, "
                " owner_id, owner_name) "
                "VALUES ('g1', 'PRE_FLOP', 2, 0, '{}', ?, 'Jeff')",
                (OBSERVER,),
            )
            for et, pot in [('successful_bluff', 0), ('big_win', 1800), ('headsup_win', 0)]:
                conn.execute(
                    "INSERT INTO pressure_events (game_id, player_name, event_type, details_json) "
                    "VALUES ('g1', ?, ?, ?)",
                    (PERSONALITY, et, json.dumps({'pot_size': pot})),
                )
            conn.execute(
                "INSERT INTO memorable_hands "
                "(observer_name, opponent_name, hand_id, game_id, memory_type, "
                " impact_score, narrative) "
                "VALUES ('Jeff', ?, 1, 'g1', 'cooler', 0.9, 'Rivered a boat on me')",
                (PERSONALITY,),
            )
            conn.commit()
        finally:
            conn.close()

    def test_durable_pressure_and_memorable_survive_between_games(self):
        # No live game in memory — everything must come from durable history.
        self._seed_pressure_and_memorable()
        self._fold(200)  # unlock everything (pressure@100, memorable@140)

        body = self._dossier()
        ps = body['pressure_summary']
        self.assertIsNotNone(ps, "lifetime pressure should populate from history")
        self.assertEqual(ps['successful_bluffs'], 1)
        self.assertEqual(ps['biggest_pot_won'], 1800)
        self.assertEqual(ps['headsup_wins'], 1)

        mem = body['memorable_hands']
        self.assertTrue(mem)
        self.assertEqual(mem[0]['narrative'], 'Rivered a boat on me')

    def test_durable_pressure_still_gated_below_threshold(self):
        self._seed_pressure_and_memorable()
        self._fold(30)  # past floor(25) but below pressure(100)/memorable(140)
        body = self._dossier()
        self.assertIsNone(body['pressure_summary'])
        self.assertEqual(body['memorable_hands'], [])

    # --- File cabinet route (Phase 4) --------------------------------------

    def test_file_cabinet_route_lists_roster(self):
        # Tier-2 opportunity columns the roster exposes; saturate greg so the
        # sample-gated deep reads fully unlock, leave cleo at 0.
        sample_cols = (
            'cbet_faced_count', 'postflop_seen_as_pfr_count',
            'postflop_bet_raise_count', 'postflop_call_count',
            'barrel_opportunity_count', 'equity_betting_count',
            'equity_raising_count', 'equity_calling_count',
            'preflop_open_opportunities', 'showdowns_seen',
            'big_bet_faced_count', 'equity_betting_big_count',
            'equity_betting_small_count',
        )
        col_sql = ", ".join(sample_cols)
        ph = ", ".join("?" for _ in sample_cols)
        conn = sqlite3.connect(self.test_db.name)
        try:
            for oid, hands, samp in [('greg', 500, 100), ('cleo', 30, 0)]:
                conn.execute(
                    f"INSERT OR REPLACE INTO opponent_observation_lifetime "
                    f"(sandbox_id, observer_id, opponent_id, hands_observed, "
                    f" hands_dealt, {col_sql}, first_seen, last_updated) "
                    f"VALUES (?, ?, ?, ?, ?, {ph}, "
                    f"        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                    (self.sandbox_id, OBSERVER, oid, hands, hands,
                     *([samp] * len(sample_cols))),
                )
            conn.commit()
        finally:
            conn.close()

        resp = self.client.get('/api/cash/file-cabinet')
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        body = resp.get_json()
        self.assertEqual(body['people_met'], 2)
        self.assertEqual(body['dossiers_unlocked'], 1)  # greg (500h) fully unlocked
        # Sorted most-observed first.
        self.assertEqual(body['people'][0]['personality_id'], 'greg')
