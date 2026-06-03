"""Tests for the post-tournament renown grant (tournaments-as-a-draw, Phase D)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from flask_app.services import tournament_renown as tr
from tournament.economy import paid_places_for  # same source grant_on_payout uses

SB = 'sb-d'


class FakeField:
    def __init__(self, field_size, eliminations):
        self.field_size = field_size
        self.eliminations = eliminations


class FakeSession:
    """Minimal stand-in: a completed tournament's finishing order."""

    def __init__(self, field_size, winner, eliminations, human_id='human:x'):
        self.field = FakeField(field_size, eliminations)
        self._winner = winner
        self.human_id = human_id

    def winner(self):
        return self._winner


def _elim(position, pid):
    return SimpleNamespace(finishing_position=position, player_id=pid)


class FakePrestigeRepo:
    def __init__(self, peaks=None, latest=None):
        self._peaks = peaks or {}
        self._latest = latest or {}
        self.ai_rows = []
        self.human_rows = []

    def load_renown_v2_peak(self, sandbox_id, owner_id, entity_kind='player'):
        return self._peaks.get((entity_kind, owner_id), 0.0)

    def load_latest(self, sandbox_id, owner_id, entity_kind='player'):
        return self._latest.get((entity_kind, owner_id))

    def record_ai_many(self, *, sandbox_id, captured_at, rows):
        self.ai_rows.extend(rows)
        return len(rows)

    def record(self, **kwargs):
        self.human_rows.append(kwargs)


def _ai_session(field_size=4):
    # Survivor 'a' is 1st; b/c/d eliminated 2nd/3rd/4th.
    elims = [_elim(4, 'd'), _elim(3, 'c'), _elim(2, 'b')]
    return FakeSession(field_size, winner='a', eliminations=elims)


class TestPositionRenown:
    def test_winner_full_bubble_fraction_out_of_money_zero(self):
        assert tr.position_renown(1, 3, base=1.0) == pytest.approx(1.0)
        assert tr.position_renown(3, 3, base=1.0) == pytest.approx(0.2)  # bubble
        assert tr.position_renown(4, 3, base=1.0) == 0.0  # out of the money
        assert 0.2 < tr.position_renown(2, 3, base=1.0) < 1.0  # scales between

    def test_single_paid_place_gets_full(self):
        assert tr.position_renown(1, 1, base=1.0) == 1.0


class TestGrantOnPayout:
    def _grant(self, repo, session, monkeypatch, *, human_owner_id=None, real=None, flag=True):
        from cash_mode import economy_flags

        monkeypatch.setattr(economy_flags, 'TOURNAMENT_DRAW_ENABLED', flag)
        return tr.grant_on_payout(
            repo,
            sandbox_id=SB,
            session=session,
            human_owner_id=human_owner_id,
            real_persona_ids=real if real is not None else frozenset({'a', 'b', 'c', 'd'}),
            now_iso='2026-06-03T00:00:00',
        )

    def test_grants_scaled_to_paid_ai_finishers(self, monkeypatch):
        session = _ai_session(field_size=4)
        paid = paid_places_for(4)  # how many are in the money
        repo = FakePrestigeRepo(peaks={('ai', 'a'): 2.0})
        n = self._grant(repo, session, monkeypatch)
        assert n == paid
        by_pid = {r['owner_id']: r for r in repo.ai_rows}
        # Winner 'a' bumped from its peak (2.0) by the full base (1.0).
        assert by_pid['a']['renown_v2'] == 2.0 + tr.position_renown(1, paid)
        # No human row (autonomous).
        assert repo.human_rows == []

    def test_flag_off_is_inert(self, monkeypatch):
        repo = FakePrestigeRepo()
        n = self._grant(repo, _ai_session(), monkeypatch, flag=False)
        assert n == 0
        assert repo.ai_rows == [] and repo.human_rows == []

    def test_none_repo_is_inert(self, monkeypatch):
        from cash_mode import economy_flags

        monkeypatch.setattr(economy_flags, 'TOURNAMENT_DRAW_ENABLED', True)
        assert (
            tr.grant_on_payout(
                None,
                sandbox_id=SB,
                session=_ai_session(),
                human_owner_id=None,
                real_persona_ids=frozenset({'a'}),
            )
            == 0
        )

    def test_human_winner_gets_player_row(self, monkeypatch):
        # Human 'human:x' wins; the rest are AI.
        elims = [_elim(4, 'd'), _elim(3, 'c'), _elim(2, 'b')]
        session = FakeSession(4, winner='human:x', eliminations=elims, human_id='human:x')
        repo = FakePrestigeRepo(peaks={('player', 'x'): 5.0})
        n = self._grant(repo, session, monkeypatch, human_owner_id='x', real={'b', 'c', 'd'})
        assert n == paid_places_for(4)
        assert len(repo.human_rows) == 1
        row = repo.human_rows[0]
        assert row['entity_kind'] == 'player'
        assert row['formula_version'] == 'tournament_v1'
        assert row['owner_id'] == 'x'
        assert row['renown_v2'] == 5.0 + tr.position_renown(1, paid_places_for(4))

    def test_ai_grant_clones_latest_quadrant(self, monkeypatch):
        session = _ai_session(field_size=4)
        repo = FakePrestigeRepo(
            peaks={('ai', 'a'): 1.0},
            latest={('ai', 'a'): {'quadrant': 'Beloved Legend', 'regard': 0.7}},
        )
        self._grant(repo, session, monkeypatch)
        winner_row = next(r for r in repo.ai_rows if r['owner_id'] == 'a')
        assert winner_row['quadrant'] == 'Beloved Legend'  # carried, not reset
        assert winner_row['regard'] == 0.7
