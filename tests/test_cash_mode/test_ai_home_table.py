"""Tests for the Career-M2 home-table counter + resolver.

A vouch reveals the voucher's *home table* — the lobby room where that AI has
played the most hands (>= a floor) — rather than wherever it happens to sit this
tick. This covers the new `ai_table_hand_counts` counter on
`RelationshipRepository` (`increment_ai_table_hands`, `load_ai_table_hands`,
`resolve_home_table`) and the ticker-side `_resolve_ai_home_table`, which
intersects the counter against the sandbox's live lobby tables.

See `poker/repositories/relationship_repository.py`,
`flask_app/services/ticker_service.py`, and
`docs/plans/CASH_MODE_CAREER_M2_PLAN.md`.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

pytestmark = pytest.mark.integration

from cash_mode.tables import TABLE_SEAT_COUNT, CashTableState, ai_slot, open_slot
from flask_app.services.ticker_service import _resolve_ai_home_table


def _bump(rel_repo, ai_id: str, table_id: str, sandbox_id: str, n: int) -> None:
    """Record `n` hands for `ai_id` at `table_id` via the real increment path."""
    for _ in range(n):
        rel_repo.increment_ai_table_hands(ai_id, table_id, sandbox_id=sandbox_id)


def _save_lobby_table(
    repo,
    table_id: str,
    ai_id: str,
    sandbox_id: str,
    *,
    table_type: str = "lobby",
    name: str | None = None,
    stake: str = "$2",
) -> None:
    seats = [ai_slot(ai_id, 200)] + [open_slot() for _ in range(TABLE_SEAT_COUNT - 1)]
    repo.save_table(
        CashTableState(
            table_id=table_id,
            stake_label=stake,
            seats=seats,
            table_type=table_type,
            name=name,
        ),
        sandbox_id=sandbox_id,
    )


def _insert_personality(db_path: str, pid: str, name: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO personalities "
            "(name, config_json, personality_id, visibility, circulating) "
            "VALUES (?, ?, ?, 'public', 0)",
            (name, json.dumps({}), pid),
        )
        conn.commit()


# --- counter: increment + load ----------------------------------------------


def test_increment_accumulates_per_table(repos):
    rel = repos["relationship_repo"]
    _bump(rel, "zeus", "t-a", "sb1", 3)
    rel.increment_ai_table_hands("zeus", "t-b", sandbox_id="sb1")
    assert rel.load_ai_table_hands("zeus", sandbox_id="sb1") == {"t-a": 3, "t-b": 1}


def test_increment_is_sandbox_scoped(repos):
    rel = repos["relationship_repo"]
    rel.increment_ai_table_hands("zeus", "t-a", sandbox_id="sb1")
    rel.increment_ai_table_hands("zeus", "t-a", sandbox_id="sb2")
    assert rel.load_ai_table_hands("zeus", sandbox_id="sb1") == {"t-a": 1}
    assert rel.load_ai_table_hands("zeus", sandbox_id="sb2") == {"t-a": 1}


def test_load_empty_when_no_hands(repos):
    assert repos["relationship_repo"].load_ai_table_hands("nobody", sandbox_id="sb1") == {}


# --- resolve_home_table ------------------------------------------------------


def test_resolve_home_table_picks_argmax(repos):
    rel = repos["relationship_repo"]
    _bump(rel, "zeus", "t-a", "sb1", 60)
    _bump(rel, "zeus", "t-b", "sb1", 80)
    home = rel.resolve_home_table(
        "zeus", sandbox_id="sb1", eligible_table_ids=frozenset({"t-a", "t-b"})
    )
    assert home == "t-b"


def test_resolve_home_table_respects_min_hands_floor(repos):
    # Floor mechanism tested at an EXPLICIT min_hands so it's robust to tuning
    # the default (a fluke guard, currently 30 — not a vouch gate).
    rel = repos["relationship_repo"]
    _bump(rel, "zeus", "t-a", "sb1", 29)
    elig = frozenset({"t-a"})
    assert (
        rel.resolve_home_table("zeus", sandbox_id="sb1", eligible_table_ids=elig, min_hands=30)
        is None
    )
    rel.increment_ai_table_hands("zeus", "t-a", sandbox_id="sb1")  # 30 — clears the floor
    assert (
        rel.resolve_home_table("zeus", sandbox_id="sb1", eligible_table_ids=elig, min_hands=30)
        == "t-a"
    )


def test_resolve_home_table_skips_ineligible_even_if_most_played(repos):
    """The most-played room being ineligible (casino / closed) must not win —
    the next eligible room does. No fallback to the absolute max."""
    rel = repos["relationship_repo"]
    _bump(rel, "zeus", "casino-x", "sb1", 200)  # most hands, but not eligible
    _bump(rel, "zeus", "lobby-y", "sb1", 60)
    home = rel.resolve_home_table(
        "zeus", sandbox_id="sb1", eligible_table_ids=frozenset({"lobby-y"})
    )
    assert home == "lobby-y"


def test_resolve_home_table_empty_eligible_is_none(repos):
    rel = repos["relationship_repo"]
    _bump(rel, "zeus", "t-a", "sb1", 60)
    assert rel.resolve_home_table("zeus", sandbox_id="sb1", eligible_table_ids=frozenset()) is None


def test_resolve_home_table_ties_break_deterministically(repos):
    """Equal hand counts → lowest table_id (ORDER BY hands DESC, table_id ASC)."""
    rel = repos["relationship_repo"]
    _bump(rel, "zeus", "t-b", "sb1", 60)
    _bump(rel, "zeus", "t-a", "sb1", 60)
    home = rel.resolve_home_table(
        "zeus", sandbox_id="sb1", eligible_table_ids=frozenset({"t-a", "t-b"})
    )
    assert home == "t-a"


def test_resolve_home_table_no_rows_is_none(repos):
    assert (
        repos["relationship_repo"].resolve_home_table(
            "nobody", sandbox_id="sb1", eligible_table_ids=frozenset({"t-a"})
        )
        is None
    )


# --- ticker resolver: _resolve_ai_home_table --------------------------------


def test_resolve_ai_home_table_returns_max_lobby_room(repos):
    rel, tbl = repos["relationship_repo"], repos["cash_table_repo"]
    _save_lobby_table(tbl, "lobby-a", "zeus", "sb1", name="The Garage")
    _save_lobby_table(tbl, "lobby-b", "zeus", "sb1", name="Murphy's Bar", stake="$5")
    _bump(rel, "zeus", "lobby-a", "sb1", 60)
    _bump(rel, "zeus", "lobby-b", "sb1", 90)

    res = _resolve_ai_home_table(tbl, rel, repos["personality_repo"], "sb1", "zeus")
    assert res is not None
    table_id, stake_label, table_name, ai_name = res
    assert table_id == "lobby-b"
    assert stake_label == "$5"
    assert table_name == "Murphy's Bar"


def test_resolve_ai_home_table_ignores_casino_room(repos):
    """A casino room with more hands isn't a vouchable home — only lobby rooms
    are eligible, so the lobby room wins even with fewer hands."""
    rel, tbl = repos["relationship_repo"], repos["cash_table_repo"]
    _save_lobby_table(tbl, "lobby-a", "zeus", "sb1", name="The Garage")
    _save_lobby_table(tbl, "casino-x", "zeus", "sb1", table_type="casino", name="Fish Floor")
    _bump(rel, "zeus", "casino-x", "sb1", 300)
    _bump(rel, "zeus", "lobby-a", "sb1", 60)

    res = _resolve_ai_home_table(tbl, rel, repos["personality_repo"], "sb1", "zeus")
    assert res is not None
    assert res[0] == "lobby-a"


def test_resolve_ai_home_table_none_without_established_home(repos):
    rel, tbl = repos["relationship_repo"], repos["cash_table_repo"]
    _save_lobby_table(tbl, "lobby-a", "zeus", "sb1", name="The Garage")
    _bump(rel, "zeus", "lobby-a", "sb1", 20)  # below the 30-hand fluke-guard floor
    assert _resolve_ai_home_table(tbl, rel, repos["personality_repo"], "sb1", "zeus") is None


def test_resolve_ai_home_table_name_falls_back_to_personality_repo(repos, db_path):
    """ai_slot seats carry no display name, so the resolver should resolve the
    voucher's name via the personality repo rather than leaking the raw id."""
    rel, tbl = repos["relationship_repo"], repos["cash_table_repo"]
    _insert_personality(db_path, "zeus", "Zeus")
    _save_lobby_table(tbl, "lobby-a", "zeus", "sb1", name="The Garage")
    _bump(rel, "zeus", "lobby-a", "sb1", 60)

    res = _resolve_ai_home_table(tbl, rel, repos["personality_repo"], "sb1", "zeus")
    assert res is not None
    assert res[3] == "Zeus"
