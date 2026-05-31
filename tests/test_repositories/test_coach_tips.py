"""Tests for the coach_tips log + tip-effectiveness measurement."""

import sqlite3

import pytest

from poker.decision_analyzer import DecisionAnalysis
from poker.repositories.coach_repository import CoachRepository
from poker.repositories.decision_analysis_repository import DecisionAnalysisRepository


@pytest.fixture
def coach_repo(db_path):
    r = CoachRepository(db_path)
    yield r
    r.close()


@pytest.fixture
def da_repo(db_path):
    r = DecisionAnalysisRepository(db_path)
    yield r
    r.close()


def _tip(**over):
    base = {
        'game_id': 'g1',
        'owner_id': 'guest_jeff',
        'player_name': 'Jeff',
        'hand_number': 1,
        'phase': 'PRE_FLOP',
        'tip_text': "You're in the SB — raise or fold here?",
        'leak_fired': True,
        'leak_scenario': 'rfi',
        'leak_position': 'SB',
        'leak_kind': 'limp',
        'leak_status': 'confirmed',
        'leak_granularity': 'spot',
        'player_hand_canonical': 'KQs',
        'player_position': 'Small Blind',
    }
    base.update(over)
    return base


def _decision(da_repo, *, game_id, hand_number, action, player='Jeff'):
    da_repo.save_decision_analysis(
        DecisionAnalysis(
            game_id=game_id, player_name=player, hand_number=hand_number,
            phase='PRE_FLOP', action_taken=action,
        )
    )


class TestRecordTip:
    def test_roundtrip(self, coach_repo, db_path):
        rid = coach_repo.record_tip(_tip())
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM coach_tips WHERE id=?", (rid,)).fetchone()
        conn.close()
        assert row['game_id'] == 'g1'
        assert row['leak_fired'] == 1
        assert row['leak_kind'] == 'limp'
        assert row['player_hand_canonical'] == 'KQs'

    def test_non_leak_tip_records_zero(self, coach_repo, db_path):
        rid = coach_repo.record_tip(_tip(leak_fired=False, leak_kind=None, leak_scenario=None))
        conn = sqlite3.connect(db_path)
        val = conn.execute("SELECT leak_fired FROM coach_tips WHERE id=?", (rid,)).fetchone()[0]
        conn.close()
        assert val == 0


class TestTipEffectiveness:
    def test_limp_nudge_follow_rate(self, coach_repo, da_repo):
        # Two SB-limp nudges: one followed (raised), one not (called again).
        coach_repo.record_tip(_tip(game_id='g1', hand_number=1))
        _decision(da_repo, game_id='g1', hand_number=1, action='raise')   # followed
        coach_repo.record_tip(_tip(game_id='g1', hand_number=2))
        _decision(da_repo, game_id='g1', hand_number=2, action='call')    # not followed

        eff = coach_repo.get_tip_effectiveness('guest_jeff')
        assert eff['by_kind']['limp']['nudges'] == 2
        assert eff['by_kind']['limp']['followed'] == 1
        assert eff['by_kind']['limp']['follow_rate'] == 0.5
        assert eff['overall']['follow_rate'] == 0.5

    def test_over_fold_followed_when_continued(self, coach_repo, da_repo):
        # over_fold leak: "followed" = did NOT fold.
        coach_repo.record_tip(_tip(game_id='g2', hand_number=1, leak_kind='over_fold',
                                   leak_scenario='vs_open', leak_position='BB'))
        _decision(da_repo, game_id='g2', hand_number=1, action='call')
        eff = coach_repo.get_tip_effectiveness('guest_jeff')
        assert eff['by_kind']['over_fold']['follow_rate'] == 1.0

    def test_ignores_non_leak_tips(self, coach_repo, da_repo):
        coach_repo.record_tip(_tip(leak_fired=False, leak_kind=None))
        _decision(da_repo, game_id='g1', hand_number=1, action='call')
        eff = coach_repo.get_tip_effectiveness('guest_jeff')
        assert eff['overall']['nudges'] == 0
        assert eff['overall']['follow_rate'] is None

    def test_scopes_to_owner(self, coach_repo, da_repo):
        coach_repo.record_tip(_tip(owner_id='someone_else'))
        _decision(da_repo, game_id='g1', hand_number=1, action='raise')
        assert coach_repo.get_tip_effectiveness('guest_jeff')['overall']['nudges'] == 0
