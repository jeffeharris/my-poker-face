"""End-to-end orchestration tests using the deterministic FakeHandResolver.

These prove the director runs a full multi-table tournament to a single winner,
keeps chips conserved every round, produces a complete and unique set of
standings, and is reproducible from its seed — all without the poker engine.
"""

from tournament.config import TournamentConfig
from tournament.director import (
    TERMINAL_WINNER,
    FakeHandResolver,
    TournamentDirector,
)


def _run(field_size: int, table_size: int = 6, seed: int = 0) -> "tuple":
    config = TournamentConfig(
        field_size=field_size,
        table_size=table_size,
        starting_stack=10_000,
        seed=seed,
        rounds_per_level=3,
    )
    director = TournamentDirector(config, resolver=FakeHandResolver())
    result = director.run()
    return config, result


def test_fake_tournament_runs_to_a_single_winner():
    config, result = _run(18)
    assert result.terminal_reason == TERMINAL_WINNER
    assert result.winner is not None
    assert result.rounds_played > 0


def test_standings_are_complete_and_unique():
    config, result = _run(24, table_size=6)
    assert len(result.standings) == config.field_size
    positions = sorted(s.finishing_position for s in result.standings)
    assert positions == list(range(1, config.field_size + 1))
    player_ids = {s.player_id for s in result.standings}
    assert len(player_ids) == config.field_size


def test_winner_is_position_one():
    _config, result = _run(18)
    winner_standing = next(s for s in result.standings if s.finishing_position == 1)
    assert winner_standing.player_id == result.winner


def test_chip_conservation_holds_to_the_end():
    config, result = _run(20, table_size=6)
    # The director asserts conservation every round; at the finish the lone
    # survivor must hold every chip.
    director = TournamentDirector(config, resolver=FakeHandResolver())
    director.run()
    assert director.field.is_complete()
    assert director.field.chip_sum() == config.total_chips
    assert director.field.stacks[result.winner] == config.total_chips


def test_reproducible_from_seed():
    _c1, r1 = _run(18, seed=7)
    _c2, r2 = _run(18, seed=7)
    assert r1.winner == r2.winner
    assert [s.player_id for s in r1.standings] == [s.player_id for s in r2.standings]
    assert r1.rounds_played == r2.rounds_played


def test_different_seeds_can_differ():
    winners = {_run(18, seed=s)[1].winner for s in range(8)}
    # With 18 entrants and 8 seeds we expect more than one distinct winner.
    assert len(winners) > 1


def test_three_and_four_table_targets():
    # 18 → 3 tables, 24 → 4 tables; both must finish cleanly.
    for n in (18, 24):
        _c, result = _run(n)
        assert result.terminal_reason == TERMINAL_WINNER
        assert result.winner is not None


def _director(field_size: int, table_size: int = 6, seed: int = 0):
    config = TournamentConfig(
        field_size=field_size, table_size=table_size, starting_stack=10_000,
        seed=seed, rounds_per_level=3,
    )
    director = TournamentDirector(config, resolver=FakeHandResolver())
    director.run()
    return director


def test_round_reports_cover_every_round():
    director = _director(18)
    assert len(director.round_reports) == director.rounds_played
    assert [r.round_index for r in director.round_reports] == list(range(director.rounds_played))


def test_seat_moves_are_recorded_when_tables_break():
    # 18 entrants over 3 tables must break down to 1 final table — that requires
    # moving players, so the event log must contain seat moves referencing real
    # entrants and two distinct tables.
    director = _director(18)
    all_moves = [m for r in director.round_reports for m in r.seat_moves]
    assert all_moves, "a 3-table field collapsing to a final table must move players"
    entries = set(director.entries)
    for m in all_moves:
        assert m.player_id in entries
        assert m.from_table != m.to_table


def test_eliminations_have_valid_eliminators():
    director = _director(18)
    elims = [e for r in director.round_reports for e in r.eliminations]
    assert len(elims) == director.config.field_size - 1  # everyone but the winner
    entries = set(director.entries)
    attributed = 0
    for e in elims:
        if e.eliminator is not None:
            assert e.eliminator in entries
            assert e.eliminator != e.player_id
            attributed += 1
    # The fake model awards every busting hand's pot to a live gainer, so the
    # vast majority of knockouts should be attributed.
    assert attributed >= len(elims) - 1
