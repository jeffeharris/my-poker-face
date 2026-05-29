#!/usr/bin/env python3
"""Phase 1 tests for Training / Coaching mode (docs/plans/TRAINING_MODE.md).

Covers the non-counting contract that's easy to regress:
- /api/training/start creates a `train-` game with training_mode + auto-coach
- the game wires NO relationship repo and NO tournament tracker (the only safe
  suppression — relationship_states is not cash_mode-gated)
- saved bot_types round-trip so cold-load rebuilds identical controllers
- training games are excluded from the "continue games" list
- the difficulty roster maps/cycles as specified
"""

import os
import sys
import tempfile
import unittest
import unittest.mock
from unittest.mock import patch

import pytest

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from flask_app import create_app
from poker.repositories import create_repos
from training.opponent_roster import (
    DEFAULT_DIFFICULTY,
    DIFFICULTY_ROSTERS,
    resolve_opponents,
)
from training.scenario import DEFAULT_PRESET_ID, TABLE_PRESETS, get_table_preset

pytestmark = [pytest.mark.flask, pytest.mark.integration]


class TestOpponentRoster(unittest.TestCase):
    """Pure unit tests for difficulty → roster resolution (no DB/app)."""

    def test_each_tier_returns_requested_count(self):
        for tier in DIFFICULTY_ROSTERS:
            self.assertEqual(len(resolve_opponents(tier, 5)), 5)

    def test_easy_is_loose_passive_rule_bots(self):
        self.assertEqual(resolve_opponents('easy', 2), ['fish', 'foldy'])

    def test_roster_cycles_when_seats_exceed_roster(self):
        # easy has 2 entries; 5 seats cycle fish/foldy/fish/foldy/fish.
        self.assertEqual(
            resolve_opponents('easy', 5), ['fish', 'foldy', 'fish', 'foldy', 'fish']
        )

    def test_hard_is_the_sharp_solver(self):
        self.assertEqual(resolve_opponents('hard', 3), ['sharp', 'sharp', 'sharp'])

    def test_unknown_difficulty_falls_back_to_default(self):
        self.assertEqual(
            resolve_opponents('impossible', 3),
            resolve_opponents(DEFAULT_DIFFICULTY, 3),
        )

    def test_zero_or_negative_seats_is_empty(self):
        self.assertEqual(resolve_opponents('medium', 0), [])
        self.assertEqual(resolve_opponents('medium', -1), [])


class TestTablePresets(unittest.TestCase):
    """Pure unit tests for table-preset resolution (no DB/app)."""

    def test_default_preset_exists_and_is_six_max(self):
        p = get_table_preset(DEFAULT_PRESET_ID)
        self.assertEqual(p.id, 'standard')
        self.assertEqual(p.opponents, 5)
        self.assertEqual(p.starting_stack_bb, 100)

    def test_unknown_and_none_fall_back_to_default(self):
        self.assertEqual(get_table_preset('nope').id, DEFAULT_PRESET_ID)
        self.assertEqual(get_table_preset(None).id, DEFAULT_PRESET_ID)

    def test_starting_stack_is_depth_times_bb(self):
        p = TABLE_PRESETS['short_stack']
        self.assertEqual(p.starting_stack, p.starting_stack_bb * p.big_blind)
        self.assertEqual(p.starting_stack, 2500)

    def test_heads_up_has_one_opponent(self):
        self.assertEqual(TABLE_PRESETS['heads_up'].opponents, 1)


class TestScriptedSpotFactory(unittest.TestCase):
    """The Phase 3 from_saved_state injection — the highest-risk new code.

    Pure (no DB/app context needed): asserts the factory produces a legal,
    human-to-act mid-street state with a consistent deck.
    """

    def _spot(self, **kw):
        from training.scenario import ScriptedSpot

        base = dict(
            phase='FLOP',
            big_blind=100,
            hero_hole=['Ah', 'Ks'],
            community=['Kc', '7d', '2h'],
            hero_stack_bb=40,
            villain_stacks_bb=[38],
            pot_bb=4.5,
            hero_bet_bb=0,
            villain_bets_bb=[3.0],  # villain has bet ~2/3 pot
        )
        base.update(kw)
        return ScriptedSpot(**base)

    def test_builds_human_to_act_flop_state(self):
        from training.state_builder import build_scripted_spot_state_machine

        sm = build_scripted_spot_state_machine(self._spot(), 'Hero', ['Villain'], seed=1)
        gs = sm.game_state
        self.assertTrue(gs.awaiting_action)
        self.assertTrue(gs.players[gs.current_player_idx].is_human)
        self.assertEqual(gs.players[0].name, 'Hero')
        self.assertEqual(len(gs.players[0].hand), 2)
        self.assertEqual(len(gs.community_cards), 3)

    def test_hero_faces_the_villain_bet(self):
        from training.state_builder import build_scripted_spot_state_machine

        sm = build_scripted_spot_state_machine(self._spot(), 'Hero', ['Villain'], seed=1)
        gs = sm.game_state
        # Villain bet 3bb=300; hero bet 0 → highest_bet 300, so a real call cost.
        self.assertEqual(gs.highest_bet, 300)
        self.assertEqual(gs.players[0].bet, 0)
        self.assertEqual(gs.current_ante, 100)

    def test_deck_excludes_placed_cards_auto_dealt_villains(self):
        from core.card import Card
        from training.state_builder import build_scripted_spot_state_machine

        sm = build_scripted_spot_state_machine(self._spot(), 'Hero', ['Villain'], seed=7)
        gs = sm.game_state
        # placed = 2 hero + 3 board; villains auto-dealt 2 → 52-5-2 = 45 left.
        self.assertEqual(len(gs.deck), 45)
        for c in [Card.from_short(x) for x in ['Ah', 'Ks', 'Kc', '7d', '2h']]:
            self.assertNotIn(c, gs.deck)

    def test_pinned_villain_holes_leave_deck_consistent(self):
        from training.state_builder import build_scripted_spot_state_machine

        spot = self._spot(villain_holes=[['Qd', 'Qs']])
        sm = build_scripted_spot_state_machine(spot, 'Hero', ['Villain'], seed=3)
        gs = sm.game_state
        # placed = 2 hero + 3 board + 2 villain = 7 → 52-7 = 45 left.
        self.assertEqual(len(gs.deck), 45)
        self.assertEqual(gs.players[1].hand[0].rank, 'Q')

    def test_river_needs_five_board_cards(self):
        from training.state_builder import build_scripted_spot_state_machine

        with self.assertRaises(ValueError):
            # FLOP-length board on a RIVER spot is rejected.
            build_scripted_spot_state_machine(
                self._spot(phase='RIVER'), 'Hero', ['Villain'], seed=1
            )

    def test_river_spot_builds_with_five_cards(self):
        from training.state_builder import build_scripted_spot_state_machine

        spot = self._spot(phase='RIVER', community=['Kc', '7d', '2h', '9s', 'Jc'])
        sm = build_scripted_spot_state_machine(spot, 'Hero', ['Villain'], seed=1)
        self.assertEqual(len(sm.game_state.community_cards), 5)


class TestTrainingStartRoute(unittest.TestCase):
    """End-to-end tests of /api/training/start and the resulting game_data."""

    def setUp(self):
        self.test_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.test_db.close()
        repos = create_repos(self.test_db.name)
        self._repos = repos

        def mock_init_persistence():
            import flask_app.extensions as ext

            ext.game_repo = repos['game_repo']
            ext.user_repo = repos['user_repo']
            ext.settings_repo = repos['settings_repo']
            ext.personality_repo = repos['personality_repo']
            ext.decision_analysis_repo = repos['decision_analysis_repo']
            ext.capture_label_repo = repos['capture_label_repo']
            ext.hand_history_repo = repos['hand_history_repo']
            ext.coach_repo = repos['coach_repo']
            ext.persistence_db_path = repos['db_path']

        with patch('flask_app.extensions.init_persistence', mock_init_persistence):
            self.app = create_app()
        self.app.testing = True

        from flask_app.extensions import limiter

        with self.app.app_context():
            try:
                limiter.reset()
            except Exception:
                pass
        self.client = self.app.test_client()

        self._route_patchers = [
            patch('flask_app.extensions.game_repo', repos['game_repo']),
            patch('flask_app.extensions.user_repo', repos['user_repo']),
            patch('flask_app.extensions.personality_repo', repos['personality_repo']),
            patch('flask_app.extensions.hand_history_repo', repos['hand_history_repo']),
            patch(
                'flask_app.extensions.decision_analysis_repo',
                repos['decision_analysis_repo'],
            ),
            patch('flask_app.extensions.capture_label_repo', repos['capture_label_repo']),
            patch('flask_app.extensions.coach_repo', repos['coach_repo']),
            patch('flask_app.extensions.persistence_db_path', repos['db_path']),
        ]
        for patcher in self._route_patchers:
            patcher.start()

    def tearDown(self):
        # Evict any in-memory training games this test created.
        from flask_app.services import game_state_service

        for gid in list(game_state_service.list_game_ids()):
            if gid.startswith('train-'):
                game_state_service.delete_game(gid)
        for patcher in self._route_patchers:
            patcher.stop()
        os.unlink(self.test_db.name)

    def _mock_auth(self):
        mock_auth = unittest.mock.MagicMock()
        mock_auth.get_current_user.return_value = {
            'id': f'test-user-{self.id()}',
            'name': 'TestUser',
        }
        return patch('flask_app.extensions.auth_manager', mock_auth)

    def _start(self, **body):
        with self._mock_auth():
            return self.client.post(
                '/api/training/start',
                json=body,
                environ_overrides={'REMOTE_ADDR': '10.77.0.1'},
            )

    def test_requires_auth(self):
        mock_auth = unittest.mock.MagicMock()
        mock_auth.get_current_user.return_value = None
        with patch('flask_app.extensions.auth_manager', mock_auth):
            resp = self.client.post('/api/training/start', json={'difficulty': 'easy'})
        self.assertEqual(resp.status_code, 401)

    def test_invalid_difficulty_returns_400(self):
        resp = self._start(difficulty='nightmare')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('valid_difficulties', resp.get_json())

    def test_invalid_preset_returns_400(self):
        resp = self._start(difficulty='easy', preset_id='moon')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('valid_presets', resp.get_json())

    def test_lists_table_presets(self):
        with self._mock_auth():
            resp = self.client.get(
                '/api/training/scenarios', environ_overrides={'REMOTE_ADDR': '10.77.0.1'}
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        ids = {p['id'] for p in body['presets']}
        self.assertEqual(ids, {'standard', 'heads_up', 'short_stack', 'deep', 'full_ring'})
        self.assertEqual(body['default_preset_id'], 'standard')

    def test_creates_non_counting_training_game(self):
        from flask_app.services import game_state_service

        resp = self._start(difficulty='easy', preset_id='standard')
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        gid = data['game_id']

        # Identified by the train- prefix, flagged training, auto-coach on.
        self.assertTrue(gid.startswith('train-'))
        self.assertTrue(data['training_mode'])
        self.assertEqual(data['preset_id'], 'standard')

        gd = game_state_service.get_game(gid)
        self.assertIsNotNone(gd)
        self.assertTrue(gd['training_mode'])
        self.assertEqual(gd['training_preset'], 'standard')

        # Non-counting via wiring-absence: no tournament tracker, no
        # relationship repo (relationship_states is NOT cash_mode-gated).
        self.assertNotIn('tournament_tracker', gd)
        mm = gd['memory_manager']
        self.assertIsNone(
            getattr(mm, '_relationship_repo', None),
            'training games must never wire a relationship repo',
        )

        # Coach is forced on (persisted on the games row → survives cold-load).
        self.assertEqual(self._repos['game_repo'].load_coach_mode(gid), 'proactive')

        # standard = 5 opponents; easy roster → loose-passive rule bots.
        from poker.rule_bot_controller import RuleBotController

        ctrls = gd['ai_controllers']
        self.assertEqual(len(ctrls), 5)
        for c in ctrls.values():
            self.assertIsInstance(c, RuleBotController)

    def test_short_stack_preset_sets_stack_depth(self):
        from flask_app.services import game_state_service

        resp = self._start(difficulty='medium', preset_id='short_stack')
        self.assertEqual(resp.status_code, 200, resp.get_json())
        gid = resp.get_json()['game_id']
        gs = game_state_service.get_game(gid)['state_machine'].game_state
        # 25bb at bb=100 → 2500-chip stacks (human exact; AIs minus posted blinds).
        self.assertEqual(gs.current_ante, 100)
        human = next(p for p in gs.players if p.is_human)
        self.assertEqual(human.stack, 2500)

    def test_heads_up_preset_is_two_handed(self):
        from flask_app.services import game_state_service

        resp = self._start(difficulty='hard', preset_id='heads_up')
        gid = resp.get_json()['game_id']
        gs = game_state_service.get_game(gid)['state_machine'].game_state
        self.assertEqual(len(gs.players), 2)

    def test_saved_bot_types_roundtrip_for_coldload(self):
        # Cold-load rebuilds controllers from the persisted bot_types via
        # restore_ai_controllers; assert they were saved.
        resp = self._start(difficulty='easy', preset_id='heads_up')
        gid = resp.get_json()['game_id']
        cfgs = self._repos['game_repo'].load_llm_configs(gid) or {}
        bot_types = cfgs.get('bot_types', {})
        self.assertEqual(len(bot_types), 1)
        self.assertTrue(set(bot_types.values()) <= {'fish', 'foldy'})

    def test_elimination_flow_suppressed_without_tracker(self):
        # The non-counting guarantee for placement/elimination: with no
        # tournament_tracker on game_data, handle_eliminations no-ops
        # (mirrors the cash-mode contract).
        from flask_app.handlers.game_handler import handle_eliminations

        resp = self._start(difficulty='easy', preset_id='heads_up')
        gid = resp.get_json()['game_id']
        from flask_app.services import game_state_service

        gd = game_state_service.get_game(gid)
        result = handle_eliminations(gid, gd, unittest.mock.MagicMock(), ['TestUser'], 100)
        # No tracker → the elimination/placement flow is skipped entirely.
        self.assertIsNone(result)

    def test_excluded_from_continue_games_list(self):
        start = self._start(difficulty='medium', preset_id='standard')
        gid = start.get_json()['game_id']
        with self._mock_auth():
            resp = self.client.get(
                '/api/games', environ_overrides={'REMOTE_ADDR': '10.77.0.1'}
            )
        self.assertEqual(resp.status_code, 200)
        listed = {g['game_id'] for g in resp.get_json()['games']}
        self.assertNotIn(gid, listed)

    def test_inline_skill_feedback_in_action_response(self):
        """Training action responses carry the coach's per-action verdict.

        Locks the FE/BE contract: the field is named `skill_evaluation` and is
        only emitted in training mode. The coach evaluation itself is the coach
        system's concern, so we patch it to a canned verdict and assert the
        route threads it into the response.
        """
        from flask_app.services import game_state_service

        canned = {
            'skill_id': 'raise_or_fold',
            'skill_name': 'Raise or Fold',
            'verdict': 'correct',
            'reasoning': 'Folding a weak hand to a raise is the disciplined play.',
            'confidence': 0.9,
        }

        start = self._start(difficulty='easy', preset_id='heads_up')
        gid = start.get_json()['game_id']

        # In heads-up the human (button/SB) acts first preflop, so no AI driving
        # is needed. We patch the post-action `progress_game` to a no-op so the
        # action route doesn't reach engine paths that touch repos this test's
        # setUp didn't patch (the documented extensions-globals trap — see
        # tests/CLAUDE.md). The contract under test is purely the response
        # assembly: training mode threads the eval into `skill_evaluation`.
        gs = game_state_service.get_game(gid)['state_machine'].game_state
        self.assertTrue(
            gs.awaiting_action and gs.current_player.is_human,
            'heads-up human should be first to act preflop',
        )

        with patch(
            'flask_app.routes.game_routes._evaluate_coach_progression', return_value=canned
        ), patch('flask_app.routes.game_routes.progress_game'), patch(
            'flask_app.routes.game_routes.send_message'
        ):
            body = None
            for act in ('fold', 'check', 'call'):
                with self._mock_auth():
                    act_resp = self.client.post(
                        f'/api/game/{gid}/action',
                        json={'action': act, 'amount': 0},
                        environ_overrides={'REMOTE_ADDR': '10.77.0.1'},
                    )
                if act_resp.status_code == 200:
                    body = act_resp.get_json()
                    break

            self.assertIsNotNone(body, 'no legal human action succeeded')
            self.assertEqual(body.get('skill_evaluation'), canned)

        game_state_service.delete_game(gid)


if __name__ == '__main__':
    unittest.main()
