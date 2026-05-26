"""Unit tests for cash_mode.whereabouts.build_whereabouts.

Pure tests over in-memory fakes — no DB. They lock in the two things
the feature hinges on: correct status classification (where is each AI)
and correct `stuck` invariant detection (is anyone wedged), since the
admin tripwire is only as good as these flags.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

from cash_mode.whereabouts import (
    STATUS_IDLE,
    STATUS_SEATED,
    STATUS_SIDE_HUSTLE,
    STATUS_VICE,
    STUCK_DOUBLE_SEAT,
    STUCK_NO_BANKROLL,
    STUCK_OVERDUE_HUSTLE,
    STUCK_SEATED_AND_IDLE,
    STUCK_STALE_IDLE,
    STUCK_UNKNOWN_PERSONALITY,
    build_whereabouts,
)

NOW = datetime(2026, 5, 26, 12, 0, 0)
SANDBOX = "sb-1"
OWNER = "owner-1"


def _seat(kind, pid=None, chips=0):
    slot = {"kind": kind}
    if pid:
        slot["personality_id"] = pid
    if chips:
        slot["chips"] = chips
    return slot


class _Table:
    def __init__(self, table_id, stake_label, seats, name=None, table_type="lobby"):
        self.table_id = table_id
        self.stake_label = stake_label
        self.seats = seats
        self.name = name
        self.table_type = table_type


class _TableRepo:
    def __init__(self, tables=(), idle=()):
        self._tables = list(tables)
        self._idle = list(idle)

    def list_all_tables(self, *, sandbox_id):
        return self._tables

    def list_idle(self, *, sandbox_id):
        return self._idle


class _StateRepo:
    """Stands in for both the side-hustle and vice repos (same shape)."""

    def __init__(self, active=(), expired=()):
        self._active = list(active)
        self._expired = list(expired)

    def list_active(self, *, sandbox_id, now):
        return self._active

    def list_expired(self, *, sandbox_id, now):
        return self._expired


class _RelRepo:
    def __init__(self, stats=()):
        self._stats = list(stats)

    def list_cash_pair_stats_for_observer(self, observer_id, *, sandbox_id=None):
        return self._stats


class _BankrollRepo:
    def __init__(self, chips_by_pid=None):
        self._chips = chips_by_pid or {}

    def load_ai_bankroll(self, pid, *, sandbox_id):
        if pid in self._chips:
            return SimpleNamespace(chips=self._chips[pid])
        return None


class _PersonalityRepo:
    def __init__(self, names=None):
        self._names = names or {}

    def display_names_by_ids(self, ids):
        return {pid: self._names[pid] for pid in ids if pid in self._names}


def _idle(pid, *, minutes_ago=5, reason="take_break", target_stake=None):
    return SimpleNamespace(
        personality_id=pid,
        left_at=NOW - timedelta(minutes=minutes_ago),
        reason=reason,
        target_stake=target_stake,
    )


def _offgrid(pid, *, started_min_ago=10, ends_in_min=20, amount=500, narration="off doing a thing"):
    return SimpleNamespace(
        personality_id=pid,
        started_at=NOW - timedelta(minutes=started_min_ago),
        ends_at=NOW + timedelta(minutes=ends_in_min),
        amount=amount,
        narration=narration,
    )


def _run(
    *,
    tables=(),
    idle=(),
    hustle_active=(),
    hustle_expired=(),
    vice_active=(),
    vice_expired=(),
    stats=(),
    chips=None,
    names=None,
    stale_idle_seconds=30 * 60,
):
    return build_whereabouts(
        sandbox_id=SANDBOX,
        owner_id=OWNER,
        now=NOW,
        cash_table_repo=_TableRepo(tables, idle),
        side_hustle_repo=_StateRepo(hustle_active, hustle_expired),
        vice_repo=_StateRepo(vice_active, vice_expired),
        relationship_repo=_RelRepo(stats),
        bankroll_repo=_BankrollRepo(chips),
        personality_repo=_PersonalityRepo(names),
        stale_idle_seconds=stale_idle_seconds,
    )


def _by_pid(result):
    return {p["personality_id"]: p for p in result["people"]}


def test_classifies_each_status():
    tables = [
        _Table(
            "t1", "$10", [_seat("ai", "seated_guy", chips=1000), _seat("open")], name="The Lodge"
        )
    ]
    result = _run(
        tables=tables,
        idle=[_idle("idle_guy")],
        hustle_active=[_offgrid("hustle_guy")],
        vice_active=[_offgrid("vice_guy")],
        names={
            "seated_guy": "Seated Guy",
            "idle_guy": "Idle Guy",
            "hustle_guy": "Hustle Guy",
            "vice_guy": "Vice Guy",
        },
        chips={"idle_guy": 100, "hustle_guy": 50, "vice_guy": 9000},
    )
    people = _by_pid(result)
    assert people["seated_guy"]["status"] == STATUS_SEATED
    assert people["seated_guy"]["table_name"] == "The Lodge"
    assert people["seated_guy"]["chips_on_table"] == 1000
    assert people["idle_guy"]["status"] == STATUS_IDLE
    assert people["hustle_guy"]["status"] == STATUS_SIDE_HUSTLE
    assert people["vice_guy"]["status"] == STATUS_VICE
    assert result["counts"]["seated"] == 1
    assert result["counts"]["idle"] == 1
    assert result["counts"]["side_hustle"] == 1
    assert result["counts"]["vice"] == 1
    # All healthy → no stuck.
    assert result["counts"]["stuck"] == 0


def test_met_filter_and_pnl():
    result = _run(
        idle=[_idle("known"), _idle("stranger")],
        names={"known": "Known", "stranger": "Stranger"},
        chips={"known": 100, "stranger": 100},
        stats=[SimpleNamespace(opponent_id="known", cumulative_pnl=420, hands_played_cash=12)],
    )
    people = _by_pid(result)
    assert people["known"]["met"] is True
    assert people["known"]["net_pnl"] == 420
    assert people["known"]["hands_played"] == 12
    assert people["stranger"]["met"] is False
    assert people["stranger"]["net_pnl"] == 0


def test_double_seat_is_flagged():
    tables = [
        _Table("t1", "$10", [_seat("ai", "ghost", chips=500), _seat("ai", "ghost", chips=500)]),
    ]
    result = _run(tables=tables, names={"ghost": "Ghost"}, chips={"ghost": 1000})
    person = _by_pid(result)["ghost"]
    assert person["status"] == STATUS_SEATED
    assert STUCK_DOUBLE_SEAT in person["stuck"]
    assert person["seat_count"] == 2
    assert result["counts"]["stuck"] == 1


def test_seated_and_idle_split_brain():
    tables = [_Table("t1", "$10", [_seat("ai", "split", chips=500), _seat("open")])]
    result = _run(
        tables=tables,
        idle=[_idle("split")],
        names={"split": "Split"},
        chips={"split": 1000},
    )
    person = _by_pid(result)["split"]
    # Seated wins as the primary status; the contradiction is recorded.
    assert person["status"] == STATUS_SEATED
    assert STUCK_SEATED_AND_IDLE in person["stuck"]


def test_forced_leave_hustler_reads_as_side_hustle():
    # A broke AI stays in the idle pool (forced_leave) while off earning.
    # That coexistence is NORMAL: status must be side_hustle (the useful
    # read), and it must NOT be flagged as stuck or watch.
    result = _run(
        idle=[_idle("grinder", reason="forced_leave")],
        hustle_active=[_offgrid("grinder")],
        names={"grinder": "Grinder"},
        chips={"grinder": 10},
    )
    person = _by_pid(result)["grinder"]
    assert person["status"] == STATUS_SIDE_HUSTLE
    assert person["stuck"] == []
    assert person["watch"] == []
    assert person["narration"] == "off doing a thing"


def test_overdue_hustle_is_a_watch_not_stuck():
    # Overdue is temporal — expected after the player's been away — so it
    # lands in `watch`, not `stuck` (keeps the alarm tier trustworthy).
    overdue = SimpleNamespace(
        personality_id="late",
        started_at=NOW - timedelta(hours=2),
        ends_at=NOW - timedelta(minutes=15),  # ended 15m ago, still present
        amount=500,
        narration="should be back by now",
    )
    result = _run(hustle_expired=[overdue], names={"late": "Late"}, chips={"late": 50})
    person = _by_pid(result)["late"]
    assert person["status"] == STATUS_SIDE_HUSTLE
    assert STUCK_OVERDUE_HUSTLE in person["watch"]
    assert person["stuck"] == []
    assert person["seconds_remaining"] is not None and person["seconds_remaining"] < 0
    assert result["counts"]["watch"] == 1
    assert result["counts"]["stuck"] == 0


def test_stale_idle_is_a_watch():
    result = _run(
        idle=[_idle("napper", minutes_ago=120)],  # 2h, past the 30m default
        names={"napper": "Napper"},
        chips={"napper": 100},
    )
    person = _by_pid(result)["napper"]
    assert STUCK_STALE_IDLE in person["watch"]
    assert person["stuck"] == []


def test_orphan_personality_and_no_bankroll():
    # Idle pid with no personality row and no bankroll row → two flags.
    result = _run(idle=[_idle("orphan")], names={}, chips={})
    person = _by_pid(result)["orphan"]
    assert person["name"] == "orphan"  # falls back to pid
    assert STUCK_UNKNOWN_PERSONALITY in person["stuck"]
    assert STUCK_NO_BANKROLL in person["stuck"]


def test_stuck_sort_to_top():
    tables = [
        _Table("t1", "$10", [_seat("ai", "ghost"), _seat("ai", "ghost")]),  # double-seat
    ]
    result = _run(
        tables=tables,
        idle=[_idle("calm")],
        names={"ghost": "Ghost", "calm": "Calm"},
        chips={"ghost": 1000, "calm": 100},
    )
    # The stuck person sorts ahead of the healthy one.
    assert result["people"][0]["personality_id"] == "ghost"
