"""Where is everyone? — a unified read of off-table AI world-state.

The cash world keeps each AI in exactly one place at a time: seated at a
table, resting in the idle pool, off-grid on a side hustle (broke, earning
back a buy-in), or off-grid on a vice (flush, blowing chips). Those four
states live in four different tables (`cash_tables`, `cash_idle_pool`,
`ai_side_hustle_state`, `vice_state`) and until now there was no single
place to ask "where is persona X, and is anyone stuck?".

`build_whereabouts` unions all four into one record per personality, with:

  - `status`        — seated / idle / side_hustle / vice / unknown
  - location        — table + seat (seated) or reason + timing (off-grid)
  - `met` / pnl     — has the human tangled with them, and for how much
  - `stuck`         — invariant-violation flags (the debug surface)

Two consumers, opposite filters:

  - The player-facing lobby drawer scopes to `met` personas and ignores
    `stuck` — it's the immersive "where'd everyone go?" view.
  - The admin panel shows everyone and leans on `stuck` — a live tripwire
    for the ghost-seat / split-brain / overdue-return bug classes.

This module is deliberately Flask-free and repo-injected so it can be
unit-tested with fakes. Name/avatar/emotion enrichment that needs the
web layer (avatar fallback, live controller emotion) is done by the
route, not here.

Spec context: docs/plans/CASH_MODE_SIDE_HUSTLE.md (side hustle / idle),
the leave mechanism (movement → idle pool → re-seat).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from cash_mode.movement import project_idle_energy

# --- status values (single source of truth for the wire contract) ---

STATUS_SEATED = "seated"
STATUS_IDLE = "idle"
STATUS_SIDE_HUSTLE = "side_hustle"
STATUS_VICE = "vice"
STATUS_UNKNOWN = "unknown"

# --- stuck-flag values ---
#
# Each names a *contradiction* or *overdue* condition that the normal
# world loop should never leave standing. An empty `stuck` list is a
# healthy persona; any flag is something for the admin tripwire.

# Hard flags — true invariant violations, wrong regardless of timing.
STUCK_DOUBLE_SEAT = "double_seat"  # same pid occupies >1 seat (ghost seat)
STUCK_SEATED_AND_IDLE = "seated_and_idle"  # seated AND in idle pool (split-brain)
STUCK_SEATED_AND_OFFGRID = "seated_and_offgrid"  # seated AND on hustle/vice
STUCK_UNKNOWN_PERSONALITY = "unknown_personality"  # referenced pid not in DB
STUCK_NO_BANKROLL = "no_bankroll"  # off-grid but no bankroll row (orphan)

# Soft / temporal flags — "watch", not "stuck". The world only advances
# while the player is present (the realtime ticker is presence-gated), so
# these are measured against wall-clock time the world hasn't caught up
# to yet. After the player's been away they fire en masse and then clear
# over the next few ticks — informational, not alarms. Only a real
# concern if they persist while the world is actively ticking.
STUCK_OVERDUE_HUSTLE = "overdue_hustle"  # hustle ends_at passed, not yet processed
STUCK_OVERDUE_VICE = "overdue_vice"  # vice ends_at passed, not yet processed
STUCK_STALE_IDLE = "stale_idle"  # idle far longer than expected
STUCK_SEATED_TOO_LONG = "seated_too_long"  # parked at one table far longer than expected

# NOTE: idle + side-hustle/vice is the NORMAL forced-leave representation —
# a broke AI stays in the idle pool (reason='forced_leave') while off
# earning a buy-in back, so that combination is deliberately NOT a flag,
# and off-grid status outranks idle below.

HARD_FLAGS = frozenset(
    {
        STUCK_DOUBLE_SEAT,
        STUCK_SEATED_AND_IDLE,
        STUCK_SEATED_AND_OFFGRID,
        STUCK_UNKNOWN_PERSONALITY,
        STUCK_NO_BANKROLL,
    }
)
SOFT_FLAGS = frozenset(
    {
        STUCK_OVERDUE_HUSTLE,
        STUCK_OVERDUE_VICE,
        STUCK_STALE_IDLE,
        STUCK_SEATED_TOO_LONG,
    }
)

# An idle AI normally walks back up within a few re-entry ticks. Sitting
# idle past this is a sign the re-seat path stalled (cooldown stuck, full
# tables, or a movement bug). Generous default — tune via the param.
DEFAULT_STALE_IDLE_SECONDS = 30 * 60

# A healthy table churns its seats — winners book and move up, losers
# rotate out. An AI parked at one table past this is a soft "watch": often
# a hoarding winner the rotation/economy isn't recycling (its off-table
# wealth never re-enters the idle pool where vice can reach it). Generous
# default so normal grinding sessions don't trip it; tune via the param.
DEFAULT_SEATED_TOO_LONG_SECONDS = 3 * 60 * 60


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt is not None else None


def _seconds_between(later: datetime, earlier: Optional[datetime]) -> Optional[int]:
    if earlier is None:
        return None
    return int((later - earlier).total_seconds())


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Best-effort ISO-8601 → datetime; None on absent/malformed input."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _idle_recharge_fraction(
    bankroll_repo: Any, pid: str, sandbox_id: str, idle_seconds: Optional[int]
) -> Optional[float]:
    """Recharge fraction (0..1) for an idle AI: how far its energy has sprung
    back toward its *own* baseline through the rest it's had — 1.0 = fully
    rested, matching the (baseline-relative) re-seat gate's view. None when
    there's no persisted psychology to read. Best-effort."""
    if bankroll_repo is None:
        return None
    try:
        blob = bankroll_repo.load_emotional_state_json(pid, sandbox_id=sandbox_id)
        if not blob:
            return None
        state = json.loads(blob)
        stored = float(state.get("axes", {}).get("energy", 0.5))
        baseline = float(state.get("anchors", {}).get("baseline_energy", stored))
    except Exception:
        return None
    if baseline <= 0:
        return 1.0
    projected = project_idle_energy(stored, baseline, float(idle_seconds or 0))
    return round(min(1.0, projected / baseline), 3)


def _recent_events_for(
    bankroll_repo: Any,
    pid: str,
    sandbox_id: str,
    names: Dict[str, str],
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """The AI's last few notable hand events (bust/suckout/big pot), newest
    last, with the opponent pid resolved to a display name. [] when none —
    the ring buffer only holds drama, so most AIs have nothing. Best-effort."""
    if bankroll_repo is None:
        return []
    try:
        events = bankroll_repo.load_recent_events(pid, sandbox_id=sandbox_id)
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for ev in events[-limit:]:
        if not isinstance(ev, dict):
            continue
        opp = ev.get("opponent")
        out.append(
            {
                "type": ev.get("type"),
                "amount": ev.get("amount"),
                "opponent": names.get(opp, opp) if opp else None,
            }
        )
    return out


def _seat_location(table: Any, seat_index: int, slot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "table_id": table.table_id,
        "table_name": table.name,
        "stake_label": table.stake_label,
        "table_type": getattr(table, "table_type", "lobby"),
        "seat_index": seat_index,
        "chips_on_table": int(slot.get("chips", 0)),
        # When this AI sat at THIS table (ISO; stamped by save_table).
        # Absent on legacy rows saved before the feature shipped.
        "seated_at": slot.get("seated_at"),
    }


def build_whereabouts(
    *,
    sandbox_id: str,
    owner_id: str,
    now: datetime,
    cash_table_repo: Any,
    side_hustle_repo: Any,
    vice_repo: Any,
    relationship_repo: Any,
    bankroll_repo: Any,
    personality_repo: Any,
    stale_idle_seconds: int = DEFAULT_STALE_IDLE_SECONDS,
    seated_too_long_seconds: int = DEFAULT_SEATED_TOO_LONG_SECONDS,
) -> Dict[str, Any]:
    """Assemble the per-personality whereabouts list for one sandbox.

    Returns ``{"now", "sandbox_id", "people": [...], "counts": {...}}``.
    Each person is a plain dict (see the module docstring for fields).
    Personalities can surface in more than one underlying table when
    something has gone wrong; this collapses them to one record whose
    primary `status` follows the precedence
    seated > idle > side_hustle > vice, with the contradiction recorded
    in `stuck` rather than hidden.
    """
    # 1. Seated — collect every AI seat across the sandbox's tables. A
    #    list per pid so a double-seat (the recurring ghost-seat bug)
    #    shows up as len > 1 rather than silently overwriting.
    seated: Dict[str, List[Dict[str, Any]]] = {}
    for table in cash_table_repo.list_all_tables(sandbox_id=sandbox_id):
        for seat_index, slot in enumerate(table.seats):
            if slot.get("kind") != "ai":
                continue
            pid = slot.get("personality_id")
            if not pid:
                continue
            seated.setdefault(pid, []).append(_seat_location(table, seat_index, slot))

    # 2. Idle pool.
    idle: Dict[str, Any] = {
        entry.personality_id: entry for entry in cash_table_repo.list_idle(sandbox_id=sandbox_id)
    }

    # 3. Side hustle — active + expired. "Expired but still present" is
    #    the overdue-return signal, so we read both and remember which.
    hustle: Dict[str, Any] = {}
    hustle_overdue: set = set()
    for state in side_hustle_repo.list_active(sandbox_id=sandbox_id, now=now):
        hustle[state.personality_id] = state
    for state in side_hustle_repo.list_expired(sandbox_id=sandbox_id, now=now):
        hustle[state.personality_id] = state
        hustle_overdue.add(state.personality_id)

    # 4. Vice — same active/expired split.
    vice: Dict[str, Any] = {}
    vice_overdue: set = set()
    for state in vice_repo.list_active(sandbox_id=sandbox_id, now=now):
        vice[state.personality_id] = state
    for state in vice_repo.list_expired(sandbox_id=sandbox_id, now=now):
        vice[state.personality_id] = state
        vice_overdue.add(state.personality_id)

    # 5. "Met" set + lifetime PnL for the human (one query).
    met_stats: Dict[str, Any] = {
        s.opponent_id: s
        for s in relationship_repo.list_cash_pair_stats_for_observer(
            owner_id, sandbox_id=sandbox_id
        )
    }

    all_pids: List[str] = list(
        dict.fromkeys([*seated.keys(), *idle.keys(), *hustle.keys(), *vice.keys()])
    )

    # 6. Names in one query (side-effect-free; does not bump times_used).
    names = personality_repo.display_names_by_ids(all_pids)

    people: List[Dict[str, Any]] = []
    counts = {
        STATUS_SEATED: 0,
        STATUS_IDLE: 0,
        STATUS_SIDE_HUSTLE: 0,
        STATUS_VICE: 0,
        STATUS_UNKNOWN: 0,
        "stuck": 0,
        "watch": 0,
    }

    for pid in all_pids:
        in_seated = pid in seated
        in_idle = pid in idle
        in_hustle = pid in hustle
        in_vice = pid in vice

        # Primary status by precedence — the truest current activity wins.
        # Seated is the live truth; among the rest, off-grid (actively
        # earning/indulging) outranks idle, because a forced-leave hustler
        # is BOTH idle and on a hustle and "off earning" is the useful read.
        if in_seated:
            status = STATUS_SEATED
        elif in_hustle:
            status = STATUS_SIDE_HUSTLE
        elif in_vice:
            status = STATUS_VICE
        elif in_idle:
            status = STATUS_IDLE
        else:
            status = STATUS_UNKNOWN

        # Accumulate every detected flag; partition into hard/soft at the
        # end. idle + off-grid is intentionally NOT flagged (see the
        # constants block) — it's the normal forced-leave hustle state.
        flags: List[str] = []
        if in_seated and len(seated[pid]) > 1:
            flags.append(STUCK_DOUBLE_SEAT)
        if in_seated and in_idle:
            flags.append(STUCK_SEATED_AND_IDLE)
        if in_seated and (in_hustle or in_vice):
            flags.append(STUCK_SEATED_AND_OFFGRID)
        if pid in hustle_overdue:
            flags.append(STUCK_OVERDUE_HUSTLE)
        if pid in vice_overdue:
            flags.append(STUCK_OVERDUE_VICE)

        # Bankroll (best-effort). Orphan = off-grid with no row.
        bankroll: Optional[int] = None
        try:
            bk = bankroll_repo.load_ai_bankroll(pid, sandbox_id=sandbox_id)
            if bk is not None:
                bankroll = int(bk.chips)
        except Exception:
            bankroll = None
        if not in_seated and bankroll is None:
            flags.append(STUCK_NO_BANKROLL)

        if pid not in names:
            flags.append(STUCK_UNKNOWN_PERSONALITY)

        met = met_stats.get(pid)
        record: Dict[str, Any] = {
            "personality_id": pid,
            "name": names.get(pid, pid),
            "status": status,
            "met": met is not None,
            "hands_played": int(met.hands_played_cash) if met else 0,
            # PnL is from the human's perspective: + = human is up on them.
            "net_pnl": int(met.cumulative_pnl) if met else 0,
            "bankroll": bankroll,
            # location (seated) — populated below per status
            "table_id": None,
            "table_name": None,
            "stake_label": None,
            "seat_index": None,
            "seat_count": len(seated.get(pid, [])) or None,
            "chips_on_table": 0,
            # off-grid detail
            "reason": None,
            "target_stake": None,
            "narration": None,
            "amount": None,
            "started_at": None,
            "ends_at": None,
            "left_at": None,
            "seconds_in_state": None,
            "seconds_remaining": None,
            # recovery — recharge fraction (0..1, toward the AI's baseline)
            # while resting in the idle pool; None for seated/off-grid AIs.
            # Lets the lobby show how rested an idle AI is (1.0 = fully
            # recharged, ~ready to return to a seat).
            "recharge": None,
            # recent notable hand events (bust/suckout/big pot), newest last;
            # [] for AIs with no recent drama. The world's short-term memory.
            "recent": _recent_events_for(bankroll_repo, pid, sandbox_id, names),
            # health — partitioned below into hard (stuck) vs soft (watch)
            "stuck": [],
            "watch": [],
        }

        if in_seated:
            loc = seated[pid][0]
            record["table_id"] = loc["table_id"]
            record["table_name"] = loc["table_name"]
            record["stake_label"] = loc["stake_label"]
            record["seat_index"] = loc["seat_index"]
            record["chips_on_table"] = loc["chips_on_table"]
            # How long this AI has been parked at the current table. None
            # on legacy seats saved before seated_at existed. Past the
            # threshold it's a soft "watch" — usually a hoarding winner the
            # rotation isn't recycling.
            record["seconds_in_state"] = _seconds_between(now, _parse_iso(loc.get("seated_at")))
            age = record["seconds_in_state"]
            if age is not None and age > seated_too_long_seconds:
                flags.append(STUCK_SEATED_TOO_LONG)

        if status == STATUS_IDLE:
            entry = idle[pid]
            record["reason"] = entry.reason
            record["target_stake"] = entry.target_stake
            record["left_at"] = _iso(entry.left_at)
            record["seconds_in_state"] = _seconds_between(now, entry.left_at)
            record["recharge"] = _idle_recharge_fraction(
                bankroll_repo, pid, sandbox_id, record["seconds_in_state"]
            )
            # Stale-idle only applies to genuinely-idle AIs (status==idle);
            # a forced-leave hustler reads as side_hustle and isn't "stale".
            age = record["seconds_in_state"]
            if age is not None and age > stale_idle_seconds:
                flags.append(STUCK_STALE_IDLE)
        elif status in (STATUS_SIDE_HUSTLE, STATUS_VICE):
            state = hustle[pid] if status == STATUS_SIDE_HUSTLE else vice[pid]
            record["narration"] = state.narration
            record["amount"] = int(state.amount)
            record["started_at"] = _iso(state.started_at)
            record["ends_at"] = _iso(state.ends_at)
            record["seconds_in_state"] = _seconds_between(now, state.started_at)
            record["seconds_remaining"] = _seconds_between(state.ends_at, now)

        # Partition: hard flags are real bugs (alarm); soft flags are
        # temporal and expected after the player's been away (watch).
        stuck = [f for f in flags if f in HARD_FLAGS]
        watch = [f for f in flags if f in SOFT_FLAGS]
        record["stuck"] = stuck
        record["watch"] = watch

        counts[status] = counts.get(status, 0) + 1
        if stuck:
            counts["stuck"] += 1
        if watch:
            counts["watch"] += 1
        people.append(record)

    # Sort: hard-stuck first (real bugs), then watch (temporal), then
    # healthy; within a tier, off-table before seated, then by name. The
    # player view re-sorts/filters client-side.
    status_rank = {
        STATUS_IDLE: 0,
        STATUS_SIDE_HUSTLE: 1,
        STATUS_VICE: 2,
        STATUS_UNKNOWN: 3,
        STATUS_SEATED: 4,
    }
    people.sort(
        key=lambda r: (
            0 if r["stuck"] else 1 if r["watch"] else 2,
            status_rank.get(r["status"], 9),
            (r["name"] or "").lower(),
        )
    )

    return {
        "now": _iso(now),
        "sandbox_id": sandbox_id,
        "people": people,
        "counts": counts,
    }
