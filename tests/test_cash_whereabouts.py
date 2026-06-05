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
    STATUS_TOURNAMENT,
    STATUS_TOURNAMENT_BOUND,
    STATUS_VICE,
    STUCK_DOUBLE_SEAT,
    STUCK_NO_BANKROLL,
    STUCK_OVERDUE_HUSTLE,
    STUCK_SEATED_AND_IDLE,
    STUCK_SEATED_AND_TOURNAMENT,
    STUCK_SEATED_TOO_LONG,
    STUCK_STALE_IDLE,
    STUCK_TOURNAMENT_BOUND_AND_SEATED,
    STUCK_UNKNOWN_PERSONALITY,
    build_whereabouts,
)

NOW = datetime(2026, 5, 26, 12, 0, 0)
SANDBOX = "sb-1"
OWNER = "owner-1"


def _seat(kind, pid=None, chips=0, seated_at=None):
    slot = {"kind": kind}
    if pid:
        slot["personality_id"] = pid
    if chips:
        slot["chips"] = chips
    if seated_at:
        slot["seated_at"] = seated_at
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


class _TournamentSessionRepo:
    def __init__(self, participants=()):
        self._participants = set(participants)

    def active_participant_pids(self, owner_id):
        return set(self._participants)


class _InviteRepo:
    def __init__(self, reserved=(), expires_at=None):
        self._reserved = list(reserved)
        self._expires_at = expires_at

    def active_for_owner(self, owner_id):
        if not self._reserved and self._expires_at is None:
            return None
        return {"reserved_pids": list(self._reserved), "expires_at": self._expires_at}


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
    on_tournament=(),
    reserved=(),
    reserved_expires_at=None,
    stale_idle_seconds=30 * 60,
    seated_too_long_seconds=3 * 60 * 60,
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
        tournament_session_repo=_TournamentSessionRepo(on_tournament),
        tournament_invite_repo=_InviteRepo(reserved, reserved_expires_at),
        stale_idle_seconds=stale_idle_seconds,
        seated_too_long_seconds=seated_too_long_seconds,
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


def _seated_at(*, hours_ago):
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def test_seated_duration_reported():
    tables = [
        _Table(
            "t1",
            "$10",
            [_seat("ai", "grinder", chips=1000, seated_at=_seated_at(hours_ago=1))],
            name="The Lodge",
        )
    ]
    result = _run(tables=tables, names={"grinder": "Grinder"}, chips={"grinder": 1000})
    person = _by_pid(result)["grinder"]
    assert person["status"] == STATUS_SEATED
    # ~1h parked, comfortably under the default 3h watch threshold.
    assert abs(person["seconds_in_state"] - 3600) <= 1
    assert person["watch"] == []


def test_seated_too_long_flags_watch():
    tables = [
        _Table(
            "t1",
            "$200",
            [_seat("ai", "whale", chips=20000, seated_at=_seated_at(hours_ago=4))],
            name="The Quiet Room",
        )
    ]
    result = _run(tables=tables, names={"whale": "Whale"}, chips={"whale": 900000})
    person = _by_pid(result)["whale"]
    # Past the 3h default → soft watch flag, not a hard stuck flag.
    assert STUCK_SEATED_TOO_LONG in person["watch"]
    assert person["stuck"] == []
    assert result["counts"]["watch"] == 1
    assert abs(person["seconds_in_state"] - 4 * 3600) <= 1


def test_seated_without_stamp_has_no_duration():
    # Legacy seat saved before seated_at existed: no duration, no flag.
    tables = [_Table("t1", "$10", [_seat("ai", "legacy", chips=1000)], name="The Lodge")]
    result = _run(tables=tables, names={"legacy": "Legacy"}, chips={"legacy": 1000})
    person = _by_pid(result)["legacy"]
    assert person["seconds_in_state"] is None
    assert person["watch"] == []


# --- tournaments-as-a-draw (Phase C): tournament whereabouts ----------------


def test_in_running_tournament_is_status_tournament():
    # A participant in a running tournament reads as 'tournament' (away), even
    # though it has no cash seat / idle row.
    result = _run(on_tournament=["champ"], names={"champ": "Champ"})
    person = _by_pid(result)["champ"]
    assert person["status"] == STATUS_TOURNAMENT
    assert result["counts"][STATUS_TOURNAMENT] == 1


def test_reserved_and_not_seated_is_tournament_bound():
    # Reserved for the open Main Event + already vacated off cash (not seated,
    # not idle — called_up adds no idle row) → tournament_bound.
    result = _run(
        reserved=["drawn"],
        reserved_expires_at="2099-01-01T00:00:00",
        names={"drawn": "Drawn"},
        chips={"drawn": 5000},  # vacated personas keep their settled bankroll
    )
    person = _by_pid(result)["drawn"]
    assert person["status"] == STATUS_TOURNAMENT_BOUND
    assert result["counts"][STATUS_TOURNAMENT_BOUND] == 1
    assert person["stuck"] == []  # en route during an open window is healthy


def test_seated_and_in_tournament_is_double_presence_flag():
    # The cardinal double-presence: cash-seated AND in a running tournament.
    tables = [_Table("t1", "$10", [_seat("ai", "ghost", chips=1000)], name="The Lodge")]
    result = _run(
        tables=tables, on_tournament=["ghost"], names={"ghost": "Ghost"}, chips={"ghost": 1000}
    )
    person = _by_pid(result)["ghost"]
    assert person["status"] == STATUS_TOURNAMENT
    assert STUCK_SEATED_AND_TOURNAMENT in person["stuck"]


def test_reserved_and_seated_past_expiry_is_soft_flag():
    # Still cash-seated after the registration window closed → missed the gather.
    tables = [_Table("t1", "$10", [_seat("ai", "slow", chips=1000)], name="The Lodge")]
    result = _run(
        tables=tables,
        reserved=["slow"],
        reserved_expires_at="2000-01-01T00:00:00",  # already past NOW
        names={"slow": "Slow"},
        chips={"slow": 1000},
    )
    person = _by_pid(result)["slow"]
    assert person["status"] == STATUS_SEATED  # still seated is the live truth
    assert STUCK_TOURNAMENT_BOUND_AND_SEATED in person["watch"]


def test_reserved_and_seated_within_window_is_healthy():
    # During an OPEN window, reserved-and-still-seated is normal (not yet vacated).
    tables = [_Table("t1", "$10", [_seat("ai", "waiting", chips=1000)], name="The Lodge")]
    result = _run(
        tables=tables,
        reserved=["waiting"],
        reserved_expires_at="2099-01-01T00:00:00",  # future
        names={"waiting": "Waiting"},
        chips={"waiting": 1000},
    )
    person = _by_pid(result)["waiting"]
    assert person["status"] == STATUS_SEATED
    assert STUCK_TOURNAMENT_BOUND_AND_SEATED not in person["watch"]


def test_no_tournament_repos_is_inert():
    # Omitting the tournament repos (default None) → behaves exactly as before.
    tables = [_Table("t1", "$10", [_seat("ai", "x", chips=1000)], name="The Lodge")]
    result = build_whereabouts(
        sandbox_id=SANDBOX,
        owner_id=OWNER,
        now=NOW,
        cash_table_repo=_TableRepo(tables, []),
        side_hustle_repo=_StateRepo(),
        vice_repo=_StateRepo(),
        relationship_repo=_RelRepo(),
        bankroll_repo=_BankrollRepo({"x": 1000}),
        personality_repo=_PersonalityRepo({"x": "X"}),
    )
    assert _by_pid(result)["x"]["status"] == STATUS_SEATED
