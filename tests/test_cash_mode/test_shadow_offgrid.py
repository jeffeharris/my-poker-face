"""Presence tests for the OFF-GRID presence writers.

Covers the four authoritative off-grid state writers in
``cash_mode.ai_side_hustle`` and ``cash_mode.ai_vice_spending``. Under
``PRESENCE_AUTHORITY_ENABLED`` (the cash-mode conftest pins it True), each
authoritative ``ai_side_hustle_state`` / ``ai_vice_state`` write ALSO drives the
Presence state machine (the ``entity_presence`` table via
``EntityPresenceRepository``).

The real writers live at:

  - side-hustle START  → ``_commit_hustle_start`` (insert), via
    ``resolve_ai_side_hustle``                       → ``START_HUSTLE``
  - side-hustle END    → ``tick_side_hustle_expirations`` (delete) → ``END_OFFGRID``
  - vice START         → ``_commit_vice_start`` (insert), via
    ``resolve_ai_vice_spending`` / ``commit_leave_vice``           → ``START_VICE``
  - vice END           → ``tick_vice_expirations`` (delete)        → ``END_OFFGRID``

(The migration doc §D/§E names hypothetical ``start_/end_*`` functions; the
actual authoritative writes on ``development`` are the insert/delete sites
above. See the agent report for that doc↔code mismatch.)

Isolation: the off-grid writers talk to repos, so we hand them lightweight
in-memory fakes (no chip schema needed) and assert against a REAL
``EntityPresenceRepository`` on a temp SQLite DB, wired in as the shadow
helper's default via ``flask_app.extensions.entity_presence_repo``.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

from cash_mode import ai_side_hustle, ai_vice_spending
from cash_mode.presence import Presence, PresenceEvent, ai_entity_id
from poker.repositories.entity_presence_repository import EntityPresenceRepository
from poker.repositories.schema_manager import SchemaManager

SANDBOX = "sbx_test"
PID = "ada_lovelace"
ENTITY = ai_entity_id(PID)
NOW = datetime(2026, 1, 1, 12, 0, 0)


# --------------------------------------------------------------------------- #
# Lightweight in-memory fakes for the repos the off-grid writers use.
# --------------------------------------------------------------------------- #


class _Knobs:
    def __init__(self, starting_bankroll: int):
        self.starting_bankroll = starting_bankroll
        self.bankroll_rate = 0.0


class _FakeBankrollRepo:
    """Just enough of the bankroll-repo surface for the off-grid resolvers."""

    def __init__(self, chips: int, starting: int):
        self._chips = chips
        self._starting = starting

    def load_ai_bankroll_current(self, pid, *, sandbox_id, now):
        return self._chips

    def load_personality_knobs(self, pid):
        return _Knobs(self._starting)

    def list_all_ai_bankroll_chips(self, *, sandbox_id):
        return [self._chips]

    # _commit_vice_start path
    class _Stored:
        def __init__(self, chips):
            self.chips = chips
            self.last_regen_tick = NOW

    def load_ai_bankroll(self, pid, *, sandbox_id):
        return self._Stored(self._chips)

    def save_ai_bankroll(self, state, *, sandbox_id):
        self._chips = state.chips


class _FakeStateRepo:
    """Stands in for side_hustle_repo / vice_repo. Records inserts/deletes and
    can hand back an 'expired' row for the tick passes."""

    def __init__(self):
        self.rows = {}  # personality_id -> object
        self._expired = []

    # start path (both modules call insert_*_state with a state object)
    def insert_side_hustle_state(self, state):
        self.rows[state.personality_id] = state

    def insert_vice_state(self, state):
        self.rows[state.personality_id] = state

    # end path
    def list_expired(self, *, sandbox_id, now):
        return list(self._expired)

    def delete(self, personality_id, *, sandbox_id):
        existed = personality_id in self.rows
        self.rows.pop(personality_id, None)
        return existed

    def seed_expired(self, row):
        self.rows[row.personality_id] = row
        self._expired = [row]


class _ExpiredRow:
    def __init__(self):
        self.personality_id = PID
        self.started_at = NOW - timedelta(hours=2)
        self.ends_at = NOW - timedelta(minutes=1)
        self.amount = 100
        self.duration_bucket = "medium"
        self.narration = "back from the grind"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def presence_repo(tmp_path):
    p = str(tmp_path / "presence.db")
    SchemaManager(p).ensure_schema()
    return EntityPresenceRepository(p)


@pytest.fixture()
def shadow_on(monkeypatch, presence_repo):
    """Route the helper's default repo to our temp presence repo. Presence
    writes are gated on `PRESENCE_AUTHORITY_ENABLED`, which the cash-mode
    conftest pins True for every test."""
    import flask_app.extensions as ext

    monkeypatch.setattr(ext, "entity_presence_repo", presence_repo, raising=False)
    return presence_repo


def _state(repo):
    return repo.load(ENTITY, SANDBOX).state


def _seed_idle(repo):
    """Drive the shadow store to IDLE via legal events (SIT then LEAVE)."""
    repo.persist_transition(ENTITY, SANDBOX, PresenceEvent.SIT, table_id="t1", seat_index=0)
    repo.persist_transition(ENTITY, SANDBOX, PresenceEvent.LEAVE)
    assert _state(repo) is Presence.IDLE


# --------------------------------------------------------------------------- #
# Side hustle: START (IDLE -> SIDE_HUSTLE) and END (-> IDLE)
# --------------------------------------------------------------------------- #


def test_side_hustle_start_and_end_round_trip(shadow_on):
    _seed_idle(shadow_on)

    sh_repo = _FakeStateRepo()
    # broke AI: chips well below starting -> a hustle target is rolled
    bankroll = _FakeBankrollRepo(chips=100, starting=10_000)

    starts = ai_side_hustle.resolve_ai_side_hustle(
        candidates={PID},
        side_hustle_repo=sh_repo,
        bankroll_repo=bankroll,
        sandbox_id=SANDBOX,
        rng=random.Random(1),
        now=NOW,
    )
    assert len(starts) == 1  # authoritative insert happened
    assert PID in sh_repo.rows
    assert _state(shadow_on) is Presence.SIDE_HUSTLE  # shadow mirrored START

    # END: seed an expired row and tick it.
    sh_repo.seed_expired(_ExpiredRow())
    ends = ai_side_hustle.tick_side_hustle_expirations(
        side_hustle_repo=sh_repo,
        bankroll_repo=bankroll,
        sandbox_id=SANDBOX,
        now=NOW,
    )
    assert len(ends) == 1
    assert PID not in sh_repo.rows  # authoritative delete happened
    assert _state(shadow_on) is Presence.IDLE  # shadow mirrored END_OFFGRID


# --------------------------------------------------------------------------- #
# Vice: START (IDLE -> VICE) and END (-> IDLE)
# --------------------------------------------------------------------------- #


def _commit_vice(vice_repo, bankroll):
    """Drive the authoritative vice-START write (``_commit_vice_start``) with
    explicit sizing, bypassing the concentration/probability roll so the test
    is deterministic. This is the exact insert site the shadow call hooks."""
    return ai_vice_spending._commit_vice_start(
        bankroll_repo=bankroll,
        chip_ledger_repo=None,
        vice_repo=vice_repo,
        sandbox_id=SANDBOX,
        personality_id=PID,
        amount=1_000,
        duration_bucket="medium",
        narration="treated themselves",
        started_at=NOW,
        ends_at=NOW + timedelta(hours=1),
        excess_ratio=2.0,
        pressure=0.5,
        field_median=10_000,
    )


def test_vice_start_and_end_round_trip(shadow_on):
    _seed_idle(shadow_on)

    vice_repo = _FakeStateRepo()
    # rich AI: well above floor protection so the vice debit commits.
    bankroll = _FakeBankrollRepo(chips=1_000_000, starting=10_000)

    committed = _commit_vice(vice_repo, bankroll)
    assert committed is not None  # authoritative insert happened
    assert PID in vice_repo.rows
    assert _state(shadow_on) is Presence.VICE  # shadow mirrored START_VICE

    # END: seed an expired vice row and tick it.
    vice_repo.seed_expired(_ExpiredRow())
    ends = ai_vice_spending.tick_vice_expirations(
        vice_repo=vice_repo,
        bankroll_repo=bankroll,
        personality_repo=None,
        sandbox_id=SANDBOX,
        now=NOW,
    )
    assert len(ends) == 1
    assert PID not in vice_repo.rows  # authoritative delete happened
    assert _state(shadow_on) is Presence.IDLE  # shadow mirrored END_OFFGRID


# --------------------------------------------------------------------------- #
# Expected divergence: START from a non-IDLE shadow state is swallowed.
# --------------------------------------------------------------------------- #


def test_start_hustle_from_non_idle_is_swallowed(shadow_on):
    # No IDLE shadow row — entity is OFFLINE in the shadow store (a broke AI
    # that went off-grid straight from being unseated). START_HUSTLE is illegal
    # from OFFLINE; the helper must swallow it (no crash, state unchanged).
    assert _state(shadow_on) is Presence.OFFLINE

    sh_repo = _FakeStateRepo()
    bankroll = _FakeBankrollRepo(chips=100, starting=10_000)
    starts = ai_side_hustle.resolve_ai_side_hustle(
        candidates={PID},
        side_hustle_repo=sh_repo,
        bankroll_repo=bankroll,
        sandbox_id=SANDBOX,
        rng=random.Random(1),
        now=NOW,
    )

    assert len(starts) == 1  # authoritative write still happened
    assert _state(shadow_on) is Presence.OFFLINE  # shadow did NOT advance


def test_start_vice_from_non_idle_is_swallowed(shadow_on):
    assert _state(shadow_on) is Presence.OFFLINE

    vice_repo = _FakeStateRepo()
    bankroll = _FakeBankrollRepo(chips=1_000_000, starting=10_000)
    committed = _commit_vice(vice_repo, bankroll)

    assert committed is not None  # authoritative write still happened
    assert PID in vice_repo.rows
    assert _state(shadow_on) is Presence.OFFLINE  # shadow did NOT advance
