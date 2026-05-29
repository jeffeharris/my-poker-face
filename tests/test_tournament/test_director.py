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
