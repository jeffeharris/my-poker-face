"""Pure tests for table seating, balancing, and breaking.

These exercise the part of the engine most worth getting right — that players
move correctly as the field thins — with no poker engine involved.
"""

from tournament.seating import (
    Seating,
    SeatingManager,
    Table,
    build_initial_seating,
)

TABLE_SIZE = 6


def mk_table(table_id: int, players: list[str], button: int = 0, size: int = TABLE_SIZE) -> Table:
    """Build a fixed-seat Table: players occupy seats 0..n-1, rest are empty."""
    seats: list[str | None] = list(players) + [None] * (size - len(players))
    return Table(table_id=table_id, seats=seats, button=button)


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
            mk_table(1, ['a', 'b', 'c', 'd', 'e', 'f']),
            mk_table(2, ['g', 'h']),
        ],
        table_size=6,
    )
    expected = _all_players(seating)
    SeatingManager().rebalance(seating)
    assert max(_sizes(seating)) - min(_sizes(seating)) <= 1
    _no_player_lost_or_duplicated(seating, expected)


def test_balance_reports_moves():
    seating = Seating(
        tables=[mk_table(1, ['a', 'b', 'c', 'd', 'e']), mk_table(2, ['f'])],
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
            mk_table(1, ['a', 'b', 'c', 'd']),
            mk_table(2, ['e', 'f', 'g']),
            mk_table(3, ['h', 'i', 'j']),
            mk_table(4, ['k', 'l', 'm']),
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
        tables=[mk_table(1, ['a', 'b', 'c']), mk_table(2, []), mk_table(3, ['d', 'e', 'f', 'g'])],
        table_size=6,
    )
    expected = _all_players(seating)
    SeatingManager().rebalance(seating)
    assert all(t.size > 0 for t in seating.tables)
    _no_player_lost_or_duplicated(seating, expected)


# ── final table ──────────────────────────────────────────────────────────────


def test_final_table_consolidation():
    seating = Seating(
        tables=[mk_table(1, ['a', 'b', 'c']), mk_table(2, ['d', 'e', 'f'])],
        table_size=6,
    )
    expected = _all_players(seating)
    moves = SeatingManager().rebalance(seating)
    assert len(seating.tables) == 1
    assert seating.tables[0].size == 6
    _no_player_lost_or_duplicated(seating, expected)
    assert moves  # three players moved onto the surviving table


def test_heads_up_stays_one_table():
    seating = Seating(tables=[mk_table(1, ['a', 'b'])], table_size=6)
    SeatingManager().rebalance(seating)
    assert len(seating.tables) == 1
    assert seating.tables[0].size == 2


# ── button / seat realism ────────────────────────────────────────────────────


def test_button_resolves_to_valid_dealer_after_rebalance():
    seating = Seating(
        tables=[mk_table(1, ['a', 'b', 'c', 'd', 'e', 'f'], button=5), mk_table(2, ['g'])],
        table_size=6,
    )
    SeatingManager().rebalance(seating)
    for t in seating.tables:
        # button is a seat index within table capacity ...
        assert 0 <= t.button < t.capacity
        # ... and always resolves to a real seated player's position.
        di = t.dealer_index_in_occupied()
        assert 0 <= di < t.size


def test_advance_button_skips_empty_seats():
    # Seats: a(0) _ c(2) _ e(4) _ ; button starts on seat 0.
    t = mk_table(1, [])
    t.seats = ['a', None, 'c', None, 'e', None]
    t.button = 0
    t.advance_button()
    assert t.button == 2  # skipped empty seat 1
    t.advance_button()
    assert t.button == 4
    t.advance_button()
    assert t.button == 0  # wrapped past empty seat 5


def test_dealer_index_snaps_forward_when_button_seat_empty():
    # Button rests on an empty seat (a player just left it); the dealer index
    # used to build a hand must snap forward to the next occupied seat.
    t = mk_table(1, [])
    t.seats = ['a', None, 'c', 'd', None, None]  # players: a(0), c(2), d(3)
    t.button = 1  # empty
    # next occupied after seat 1 is seat 2 ('c'), which is players[1]
    assert t.dealer_index_in_occupied() == 1
    assert t.players == ['a', 'c', 'd']


def test_incoming_player_takes_lowest_open_seat():
    t = mk_table(1, [])
    t.seats = ['a', None, 'c', None, None, None]
    seat = t.add('x')
    assert seat == 1
    assert t.seats[1] == 'x'
