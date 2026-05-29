"""Pure tests for table seating, balancing, and breaking.

These exercise the part of the engine most worth getting right — that players
move correctly as the field thins — with no poker engine involved.
"""

from tournament.seating import (
    SeatingManager,
    Seating,
    Table,
    build_initial_seating,
)


def _all_players(seating: Seating) -> list[str]:
    return sorted(seating.all_player_ids())


def _no_player_lost_or_duplicated(seating: Seating, expected: list[str]) -> None:
    ids = seating.all_player_ids()
    assert sorted(ids) == sorted(expected)
    assert len(ids) == len(set(ids)), "a player was duplicated across seats"


def _sizes(seating: Seating) -> list[int]:
    return sorted(t.size for t in seating.tables)


# ── initial seating ──────────────────────────────────────────────────────────


def test_initial_seating_is_even():
    ids = [f"P{i:02d}" for i in range(18)]
    seating = build_initial_seating(ids, table_size=6)
    assert len(seating.tables) == 3
    assert _sizes(seating) == [6, 6, 6]
    _no_player_lost_or_duplicated(seating, ids)


def test_initial_seating_uneven_field_within_one():
    ids = [f"P{i:02d}" for i in range(20)]
    seating = build_initial_seating(ids, table_size=6)
    # ceil(20/6) = 4 tables, distributed as evenly as possible
    assert len(seating.tables) == 4
    assert max(_sizes(seating)) - min(_sizes(seating)) <= 1
    _no_player_lost_or_duplicated(seating, ids)


# ── balancing ──────────────────────────────────────────────────────────────


def test_balance_evens_out_lopsided_tables():
    seating = Seating(
        tables=[
            Table(1, ['a', 'b', 'c', 'd', 'e', 'f']),
            Table(2, ['g', 'h']),
        ],
        table_size=6,
    )
    expected = _all_players(seating)
    SeatingManager().rebalance(seating)
    assert max(_sizes(seating)) - min(_sizes(seating)) <= 1
    _no_player_lost_or_duplicated(seating, expected)


def test_balance_reports_moves():
    seating = Seating(
        tables=[Table(1, ['a', 'b', 'c', 'd', 'e']), Table(2, ['f'])],
        table_size=6,
    )
    moves = SeatingManager().rebalance(seating)
    assert moves, "expected at least one move to balance 5 vs 1"
    # every move names a real player and two distinct tables
    for m in moves:
        assert m.from_table != m.to_table


# ── breaking ──────────────────────────────────────────────────────────────


def test_break_reduces_to_target_table_count():
    # 13 players over 4 tables → should collapse to ceil(13/6) = 3 tables
    seating = Seating(
        tables=[
            Table(1, ['a', 'b', 'c', 'd']),
            Table(2, ['e', 'f', 'g']),
            Table(3, ['h', 'i', 'j']),
            Table(4, ['k', 'l', 'm']),
        ],
        table_size=6,
    )
    expected = _all_players(seating)
    SeatingManager().rebalance(seating)
    assert len(seating.tables) == 3
    assert max(_sizes(seating)) - min(_sizes(seating)) <= 1
    _no_player_lost_or_duplicated(seating, expected)


def test_empty_tables_are_dropped():
    seating = Seating(
        tables=[Table(1, ['a', 'b', 'c']), Table(2, []), Table(3, ['d', 'e', 'f', 'g'])],
        table_size=6,
    )
    expected = _all_players(seating)
    SeatingManager().rebalance(seating)
    assert all(t.size > 0 for t in seating.tables)
    _no_player_lost_or_duplicated(seating, expected)


# ── final table ──────────────────────────────────────────────────────────────


def test_final_table_consolidation():
    seating = Seating(
        tables=[Table(1, ['a', 'b', 'c']), Table(2, ['d', 'e', 'f'])],
        table_size=6,
    )
    expected = _all_players(seating)
    moves = SeatingManager().rebalance(seating)
    assert len(seating.tables) == 1
    assert seating.tables[0].size == 6
    _no_player_lost_or_duplicated(seating, expected)
    assert moves  # three players moved onto the surviving table


def test_heads_up_stays_one_table():
    seating = Seating(tables=[Table(1, ['a', 'b'])], table_size=6)
    SeatingManager().rebalance(seating)
    assert len(seating.tables) == 1
    assert seating.tables[0].size == 2


# ── button stays valid ──────────────────────────────────────────────────────


def test_button_index_stays_in_range_after_rebalance():
    seating = Seating(
        tables=[Table(1, ['a', 'b', 'c', 'd', 'e', 'f'], button=5), Table(2, ['g'])],
        table_size=6,
    )
    SeatingManager().rebalance(seating)
    for t in seating.tables:
        assert 0 <= t.button < t.size
