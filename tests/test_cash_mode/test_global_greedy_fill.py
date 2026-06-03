"""Integration test for the global greedy seat-fill wiring (Phase C2b).

Spec: `docs/plans/CASH_MODE_TABLE_ATTRACTIVENESS.md` §2.

Drives `_process_global_greedy_fills` directly (rather than the whole
`refresh_unseated_tables` burst machinery) to validate the WIRING the pure
core (`assign_seats_greedy`, tested in test_attractiveness.py) doesn't cover:
FillableTable construction from real seats (fish-chip gathering, fillable
open indices), the inline bankroll debit, seat placement, idle removal, and
the seated_globally mutation.
"""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime, timedelta

import pytest

from cash_mode.bankroll import AIBankrollState
from cash_mode.lobby import _process_global_greedy_fills
from cash_mode.movement import RosterRefreshResult
from cash_mode.tables import CashTableState, IdlePoolEntry, ai_slot, ai_slot_fish, open_slot
from poker.repositories.bankroll_repository import BankrollRepository
from poker.repositories.cash_table_repository import CashTableRepository
from poker.repositories.chip_ledger_repository import ChipLedgerRepository
from poker.repositories.personality_repository import PersonalityRepository
from poker.repositories.schema_manager import SchemaManager

SB = "sb-greedy-fill"

pytestmark = pytest.mark.integration


def _insert_personality(db_path, pid, *, name, knobs):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO personalities (name, config_json, personality_id, visibility) "
            "VALUES (?, ?, ?, 'public')",
            (name, json.dumps({"bankroll_knobs": knobs}), pid),
        )
        conn.commit()


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "greedy_fill.db")
    SchemaManager(path).ensure_schema()
    return path


def _open_indices(seats):
    return frozenset(i for i, s in enumerate(seats) if s["kind"] == "open")


def test_global_fill_seats_idle_grinder_at_the_fishier_table(db_path):
    cash_table_repo = CashTableRepository(db_path)
    bankroll_repo = BankrollRepository(db_path)
    personality_repo = PersonalityRepository(db_path)
    chip_ledger_repo = ChipLedgerRepository(db_path)
    now = datetime(2026, 5, 29, 12, 0, 0)

    # A non-fish grinder, comfortably rolled for $2, sitting idle.
    _insert_personality(
        db_path,
        "grinder_g",
        name="Grinder",
        knobs={
            "starting_bankroll": 5_000,
            "bankroll_rate": 0,
            "buy_in_multiplier": 1.0,
            "stake_comfort_zone": "$2",
        },
    )
    bankroll_repo.save_ai_bankroll(
        AIBankrollState(personality_id="grinder_g", chips=5_000, last_regen_tick=None),
        sandbox_id=SB,
    )

    # Two $2 tables: one casino table with a seated fish (chips on the
    # felt), one dead all-open table. The grinder should pick the fishy one.
    fishy_seats = [ai_slot_fish("vacation_greg", 600)] + [open_slot() for _ in range(5)]
    fishy = CashTableState(
        table_id="cash-fishy",
        stake_label="$2",
        seats=fishy_seats,
        name="Fishy",
        table_type="casino",
    )
    dead = CashTableState(
        table_id="cash-dead",
        stake_label="$2",
        seats=[open_slot() for _ in range(6)],
        name="Dead",
        table_type="casino",
    )
    cash_table_repo.save_table(fishy, sandbox_id=SB)
    cash_table_repo.save_table(dead, sandbox_id=SB)

    # The grinder is idle and well-rested (left long ago).
    idle_entry = IdlePoolEntry(
        personality_id="grinder_g",
        left_at=now - timedelta(hours=6),
        reason="bored_move",
        target_stake=None,
    )
    cash_table_repo.save_idle(idle_entry, sandbox_id=SB)

    fill_ctx = {
        "cash-fishy": (RosterRefreshResult(new_table=fishy), _open_indices(fishy.seats)),
        "cash-dead": (RosterRefreshResult(new_table=dead), _open_indices(dead.seats)),
    }
    seated_globally = {"vacation_greg"}

    _process_global_greedy_fills(
        fill_ctx=fill_ctx,
        idle_pool=[idle_entry],
        eligible=[],
        seated_globally=seated_globally,
        fish_ids={"vacation_greg"},
        bankroll_lookup=lambda pid: 5_000 if pid == "grinder_g" else None,
        bankroll_repo=bankroll_repo,
        cash_table_repo=cash_table_repo,
        chip_ledger_repo=chip_ledger_repo,
        personality_repo=personality_repo,
        sandbox_id=SB,
        now=now,
        rng=random.Random(0),
        seek_rate=1.0,  # force the grinder to go room-hunting
    )

    # Seated at the FISHY table, not the dead one.
    fishy_after = cash_table_repo.load_table("cash-fishy", sandbox_id=SB)
    dead_after = cash_table_repo.load_table("cash-dead", sandbox_id=SB)
    fishy_pids = {s["personality_id"] for s in fishy_after.seats if s["kind"] == "ai"}
    dead_pids = {s["personality_id"] for s in dead_after.seats if s["kind"] == "ai"}
    assert "grinder_g" in fishy_pids
    assert "grinder_g" not in dead_pids

    # Funded by an inline debit (no chip mint): bankroll dropped by the $2
    # buy-in (40bb = 80), and the seat carries those chips.
    after = bankroll_repo.load_ai_bankroll_current("grinder_g", sandbox_id=SB, now=now)
    assert after == 5_000 - 80
    seat = next(s for s in fishy_after.seats if s.get("personality_id") == "grinder_g")
    assert seat["chips"] == 80

    # seated_globally mutated in place; idle row removed.
    assert "grinder_g" in seated_globally
    remaining_idle = {e.personality_id for e in cash_table_repo.list_idle(sandbox_id=SB)}
    assert "grinder_g" not in remaining_idle


def test_global_fill_refuses_to_seat_unfundable_ai(db_path):
    # An AI whose bankroll can't cover the buy-in is never seated (no mint).
    cash_table_repo = CashTableRepository(db_path)
    bankroll_repo = BankrollRepository(db_path)
    personality_repo = PersonalityRepository(db_path)
    chip_ledger_repo = ChipLedgerRepository(db_path)
    now = datetime(2026, 5, 29, 12, 0, 0)

    _insert_personality(
        db_path,
        "broke_b",
        name="Broke",
        knobs={
            "starting_bankroll": 50,
            "bankroll_rate": 0,
            "buy_in_multiplier": 1.0,
            "stake_comfort_zone": "$2",
        },
    )
    bankroll_repo.save_ai_bankroll(
        AIBankrollState(personality_id="broke_b", chips=50, last_regen_tick=None),
        sandbox_id=SB,
    )
    table = CashTableState(
        table_id="cash-t",
        stake_label="$2",
        seats=[open_slot() for _ in range(6)],
        name="T",
        table_type="casino",
    )
    cash_table_repo.save_table(table, sandbox_id=SB)
    entry = IdlePoolEntry(
        personality_id="broke_b",
        left_at=now - timedelta(hours=6),
        reason="bored_move",
        target_stake=None,
    )

    _process_global_greedy_fills(
        fill_ctx={"cash-t": (RosterRefreshResult(new_table=table), _open_indices(table.seats))},
        idle_pool=[entry],
        eligible=[],
        seated_globally=set(),
        fish_ids=set(),
        bankroll_lookup=lambda pid: 50,  # below the $2 buy-in (80)
        bankroll_repo=bankroll_repo,
        cash_table_repo=cash_table_repo,
        chip_ledger_repo=chip_ledger_repo,
        personality_repo=personality_repo,
        sandbox_id=SB,
        now=now,
        rng=random.Random(0),
        seek_rate=1.0,
    )

    after = cash_table_repo.load_table("cash-t", sandbox_id=SB)
    assert all(s["kind"] == "open" for s in after.seats)  # never seated


def test_human_headroom_leaves_a_seat_open(db_path):
    """With `human_headroom=1`, the fill reserves one open seat per table
    for a human even when more affordable seekers are queued than seats —
    the fix for the world ticker crowding humans out of the lobby."""
    cash_table_repo = CashTableRepository(db_path)
    bankroll_repo = BankrollRepository(db_path)
    personality_repo = PersonalityRepository(db_path)
    chip_ledger_repo = ChipLedgerRepository(db_path)
    now = datetime(2026, 5, 29, 12, 0, 0)

    # Three well-rolled grinders idle, all hunting $2. Only ONE table with
    # two open seats: headroom reserves one, so at most one gets seated.
    seekers = ["grinder_a", "grinder_b", "grinder_c"]
    for pid in seekers:
        _insert_personality(
            db_path,
            pid,
            name=pid,
            knobs={
                "starting_bankroll": 5_000,
                "bankroll_rate": 0,
                "buy_in_multiplier": 1.0,
                "stake_comfort_zone": "$2",
            },
        )
        bankroll_repo.save_ai_bankroll(
            AIBankrollState(personality_id=pid, chips=5_000, last_regen_tick=None),
            sandbox_id=SB,
        )

    # 6-seat table with four AIs already seated and two open seats.
    table = CashTableState(
        table_id="cash-hr",
        stake_label="$2",
        seats=[
            ai_slot("pre_1", 80),
            ai_slot("pre_2", 80),
            ai_slot("pre_3", 80),
            ai_slot("pre_4", 80),
            open_slot(),
            open_slot(),
        ],
        name="Headroom",
        table_type="casino",
    )
    cash_table_repo.save_table(table, sandbox_id=SB)

    idle_entries = [
        IdlePoolEntry(
            personality_id=pid,
            left_at=now - timedelta(hours=6),
            reason="bored_move",
            target_stake=None,
        )
        for pid in seekers
    ]
    for e in idle_entries:
        cash_table_repo.save_idle(e, sandbox_id=SB)

    _process_global_greedy_fills(
        fill_ctx={"cash-hr": (RosterRefreshResult(new_table=table), _open_indices(table.seats))},
        idle_pool=idle_entries,
        eligible=[],
        seated_globally=set(),
        fish_ids=set(),
        bankroll_lookup=lambda pid: 5_000,
        bankroll_repo=bankroll_repo,
        cash_table_repo=cash_table_repo,
        chip_ledger_repo=chip_ledger_repo,
        personality_repo=personality_repo,
        sandbox_id=SB,
        now=now,
        rng=random.Random(0),
        seek_rate=1.0,
        human_headroom=1,
    )

    after = cash_table_repo.load_table("cash-hr", sandbox_id=SB)
    open_count = sum(1 for s in after.seats if s["kind"] == "open")
    seated_seekers = {
        s["personality_id"] for s in after.seats if s.get("personality_id") in seekers
    }
    # Two seats were open; headroom=1 lets the fill take only one, leaving
    # one reserved for a human. So exactly one seeker is seated.
    assert open_count == 1
    assert len(seated_seekers) == 1


def test_stake_up_unaffordable_target_relaxes_to_lower_table(db_path):
    """A stake-up AI whose bankroll lands in the dead band [target_min,
    target_min × buy_in_multiplier] must RELAX down to a tier it can actually
    afford, instead of stranding forever as "stale idle".

    Regression for the two-affordability-check mismatch: the stickiness gate
    (`_can_afford_target`) once used the raw min while the placement gate
    (`seeker_buy_in`) uses min × multiplier — so an AI rich enough to refuse
    lower tables could be too poor to ever be seated at its target.
    """
    cash_table_repo = CashTableRepository(db_path)
    bankroll_repo = BankrollRepository(db_path)
    personality_repo = PersonalityRepository(db_path)
    chip_ledger_repo = ChipLedgerRepository(db_path)
    now = datetime(2026, 5, 29, 12, 0, 0)

    # Bankroll 3616, multiplier 2.4, queued to stake up to $50.
    #   $50 raw min = 2000  -> old gate: "can afford" -> refuse lower tables
    #   $50 seeker buy-in = round(2000 * 2.4) = 4800 -> greedy can't place it
    # => stranded under the old gate. Fix: it relaxes to $10.
    #   $10 seeker buy-in = round(400 * 2.4) = 960 -> affordable, gets seated.
    _insert_personality(
        db_path,
        "stuck_s",
        name="Stuck",
        knobs={
            "starting_bankroll": 3_616,
            "bankroll_rate": 0,
            "buy_in_multiplier": 2.4,
            "stake_comfort_zone": "$10",
        },
    )
    bankroll_repo.save_ai_bankroll(
        AIBankrollState(personality_id="stuck_s", chips=3_616, last_regen_tick=None),
        sandbox_id=SB,
    )

    low = CashTableState(
        table_id="cash-low",
        stake_label="$10",
        seats=[open_slot() for _ in range(6)],
        name="Low",
        table_type="lobby",
    )
    cash_table_repo.save_table(low, sandbox_id=SB)

    entry = IdlePoolEntry(
        personality_id="stuck_s",
        left_at=now - timedelta(hours=6),
        reason="stake_up_queued",
        target_stake="$50",
    )
    cash_table_repo.save_idle(entry, sandbox_id=SB)

    _process_global_greedy_fills(
        fill_ctx={"cash-low": (RosterRefreshResult(new_table=low), _open_indices(low.seats))},
        idle_pool=[entry],
        eligible=[],
        seated_globally=set(),
        fish_ids=set(),
        bankroll_lookup=lambda pid: 3_616 if pid == "stuck_s" else None,
        bankroll_repo=bankroll_repo,
        cash_table_repo=cash_table_repo,
        chip_ledger_repo=chip_ledger_repo,
        personality_repo=personality_repo,
        sandbox_id=SB,
        now=now,
        rng=random.Random(0),
        seek_rate=1.0,
    )

    after = cash_table_repo.load_table("cash-low", sandbox_id=SB)
    seated = {s["personality_id"] for s in after.seats if s["kind"] == "ai"}
    assert "stuck_s" in seated  # relaxed down and seated, not stranded
    remaining_idle = {e.personality_id for e in cash_table_repo.list_idle(sandbox_id=SB)}
    assert "stuck_s" not in remaining_idle


def test_stake_up_affordable_target_still_holds_out(db_path):
    """The stickiness gate still works when the target IS affordable at the
    multiplier: the AI refuses a lower table and keeps waiting for its tier.
    """
    cash_table_repo = CashTableRepository(db_path)
    bankroll_repo = BankrollRepository(db_path)
    personality_repo = PersonalityRepository(db_path)
    chip_ledger_repo = ChipLedgerRepository(db_path)
    now = datetime(2026, 5, 29, 12, 0, 0)

    # Multiplier 1.0, bankroll 3000, target $50.
    #   $50 seeker buy-in = round(2000 * 1.0) = 2000 <= 3000 -> CAN afford
    # => holds out; only a $10 table is offered, so it stays idle.
    _insert_personality(
        db_path,
        "patient_p",
        name="Patient",
        knobs={
            "starting_bankroll": 3_000,
            "bankroll_rate": 0,
            "buy_in_multiplier": 1.0,
            "stake_comfort_zone": "$10",
        },
    )
    bankroll_repo.save_ai_bankroll(
        AIBankrollState(personality_id="patient_p", chips=3_000, last_regen_tick=None),
        sandbox_id=SB,
    )

    low = CashTableState(
        table_id="cash-low",
        stake_label="$10",
        seats=[open_slot() for _ in range(6)],
        name="Low",
        table_type="lobby",
    )
    cash_table_repo.save_table(low, sandbox_id=SB)

    entry = IdlePoolEntry(
        personality_id="patient_p",
        left_at=now - timedelta(hours=6),
        reason="stake_up_queued",
        target_stake="$50",
    )
    cash_table_repo.save_idle(entry, sandbox_id=SB)

    _process_global_greedy_fills(
        fill_ctx={"cash-low": (RosterRefreshResult(new_table=low), _open_indices(low.seats))},
        idle_pool=[entry],
        eligible=[],
        seated_globally=set(),
        fish_ids=set(),
        bankroll_lookup=lambda pid: 3_000 if pid == "patient_p" else None,
        bankroll_repo=bankroll_repo,
        cash_table_repo=cash_table_repo,
        chip_ledger_repo=chip_ledger_repo,
        personality_repo=personality_repo,
        sandbox_id=SB,
        now=now,
        rng=random.Random(0),
        seek_rate=1.0,
    )

    after = cash_table_repo.load_table("cash-low", sandbox_id=SB)
    assert all(s["kind"] == "open" for s in after.seats)  # held out, not seated
    remaining_idle = {e.personality_id for e in cash_table_repo.list_idle(sandbox_id=SB)}
    assert "patient_p" in remaining_idle  # still waiting for its tier


# --- B4 prestige-seeking (marquee pull) end-to-end wiring --------------------


def _insert_persona_with_anchors(db_path, pid, *, name, knobs, anchors):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO personalities (name, config_json, personality_id, visibility) "
            "VALUES (?, ?, ?, 'public')",
            (name, json.dumps({"bankroll_knobs": knobs, "anchors": anchors}), pid),
        )
        conn.commit()


def _two_lobby_tables(cash_table_repo, *, marquee_occupant, plain_occupant):
    """Two identical $2 lobby tables (one occupant each, one open seat) — the
    only difference is WHO sits there. 'cash-aaa-plain' sorts before
    'cash-zzz-marquee', so the greedy id-tiebreak picks plain when nothing else
    separates them; the marquee term has to OVERRIDE that to prove it works."""
    plain = CashTableState(
        table_id="cash-aaa-plain",
        stake_label="$2",
        name="Plain",
        table_type="lobby",
        seats=[ai_slot(plain_occupant, 80)] + [open_slot() for _ in range(5)],
    )
    marquee = CashTableState(
        table_id="cash-zzz-marquee",
        stake_label="$2",
        name="Marquee",
        table_type="lobby",
        seats=[ai_slot(marquee_occupant, 80)] + [open_slot() for _ in range(5)],
    )
    cash_table_repo.save_table(plain, sandbox_id=SB)
    cash_table_repo.save_table(marquee, sandbox_id=SB)
    fill_ctx = {
        "cash-aaa-plain": (RosterRefreshResult(new_table=plain), _open_indices(plain.seats)),
        "cash-zzz-marquee": (RosterRefreshResult(new_table=marquee), _open_indices(marquee.seats)),
    }
    return fill_ctx


def _seek(db_path, cash_table_repo, bankroll_repo, now):
    """A rolled-up status-seeker (high glory anchors) sitting idle."""
    _insert_persona_with_anchors(
        db_path,
        "seeker_s",
        name="Seeker",
        knobs={
            "starting_bankroll": 5_000,
            "bankroll_rate": 0,
            "buy_in_multiplier": 1.0,
            "stake_comfort_zone": "$2",
        },
        anchors={"expressiveness": 0.9, "ego": 0.9},  # a glory-hunter
    )
    bankroll_repo.save_ai_bankroll(
        AIBankrollState(personality_id="seeker_s", chips=5_000, last_regen_tick=None), sandbox_id=SB
    )
    idle = IdlePoolEntry(
        personality_id="seeker_s",
        left_at=now - timedelta(hours=6),
        reason="bored_move",
        target_stake=None,
    )
    cash_table_repo.save_idle(idle, sandbox_id=SB)
    return idle


def _run_fill(db_path, fill_ctx, idle, *, renown_percentiles, seated, now):
    _process_global_greedy_fills(
        fill_ctx=fill_ctx,
        idle_pool=[idle],
        eligible=[],
        seated_globally=seated,
        fish_ids=set(),
        bankroll_lookup=lambda pid: 5_000 if pid == "seeker_s" else None,
        bankroll_repo=BankrollRepository(db_path),
        cash_table_repo=CashTableRepository(db_path),
        chip_ledger_repo=ChipLedgerRepository(db_path),
        personality_repo=PersonalityRepository(db_path),
        sandbox_id=SB,
        now=now,
        rng=random.Random(0),
        seek_rate=1.0,
        renown_percentiles=renown_percentiles,
    )


def _seeker_table(db_path):
    repo = CashTableRepository(db_path)
    for tid in ("cash-aaa-plain", "cash-zzz-marquee"):
        t = repo.load_table(tid, sandbox_id=SB)
        if any(s.get("personality_id") == "seeker_s" for s in t.seats if s["kind"] == "ai"):
            return tid
    return None


def test_marquee_routes_status_seeker_to_famous_table(db_path):
    # Famous AI (renown percentile 0.90) at 'marquee', a nobody (0.15) at
    # 'plain'. With renown supplied + the seeker a glory-hunter, the marquee
    # term must override the id-tiebreak and seat the seeker at the famous table.
    cash_table_repo = CashTableRepository(db_path)
    bankroll_repo = BankrollRepository(db_path)
    now = datetime(2026, 6, 2, 12, 0, 0)
    fill_ctx = _two_lobby_tables(
        cash_table_repo, marquee_occupant="legend_l", plain_occupant="nobody_n"
    )
    idle = _seek(db_path, cash_table_repo, bankroll_repo, now)
    seated = {"legend_l", "nobody_n"}
    _run_fill(
        db_path,
        fill_ctx,
        idle,
        renown_percentiles={"legend_l": 0.90, "nobody_n": 0.15, "seeker_s": 0.5},
        seated=seated,
        now=now,
    )

    assert _seeker_table(db_path) == "cash-zzz-marquee"
    # Conservation: funded by an inline debit, no mint ($2 = 80 chips).
    assert bankroll_repo.load_ai_bankroll_current("seeker_s", sandbox_id=SB, now=now) == 5_000 - 80


def test_marquee_inert_without_renown_seeks_by_tiebreak(db_path):
    # Same scenario, renown_percentiles=None (flag-off path): the marquee term
    # is inert, the two tables tie on base attractiveness, and the greedy
    # id-tiebreak seats the seeker at 'cash-aaa-plain' — proving the routing in
    # the test above was CAUSED by the marquee term, not the table layout.
    cash_table_repo = CashTableRepository(db_path)
    bankroll_repo = BankrollRepository(db_path)
    now = datetime(2026, 6, 2, 12, 0, 0)
    fill_ctx = _two_lobby_tables(
        cash_table_repo, marquee_occupant="legend_l", plain_occupant="nobody_n"
    )
    idle = _seek(db_path, cash_table_repo, bankroll_repo, now)
    _run_fill(
        db_path, fill_ctx, idle, renown_percentiles=None, seated={"legend_l", "nobody_n"}, now=now
    )

    assert _seeker_table(db_path) == "cash-aaa-plain"
