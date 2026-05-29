"""In-process integration test for the live-table bridge (Phase 2c).

Drives the hand-boundary coordination end-to-end WITHOUT the Flask app, socket,
or DB: a lightweight fake state-machine (an object with a settable `game_state`),
stub AI controllers, and `memory_manager=None`. Each "hand" is resolved with the
FakeHandResolver and written into the live game state, then
`advance_tournament_after_hand` folds it into the field, paces the AI tables,
settles, and reconciles the live roster — exactly what the production hook calls.

This exercises the risky new code (boundary coordination + roster reconcile)
against a real TournamentSession, asserting conservation and roster invariants
every step, to completion.
"""

from types import SimpleNamespace

import pytest

from flask_app.handlers.tournament_handler import (
    COMPLETE,
    HUMAN_OUT,
    RELOCATED,
    advance_tournament_after_hand,
    human_table_seat_specs,
)
from poker.poker_game import Player, PokerGameState
from tournament.config import TournamentConfig
from tournament.director import FakeHandResolver
from tournament.session import TournamentSession


def _session(field_size: int, table_size: int = 6, seed: int = 0) -> TournamentSession:
    config = TournamentConfig(
        field_size=field_size, table_size=table_size, starting_stack=10_000, seed=seed,
        rounds_per_level=3,
    )
    return TournamentSession(config, ai_resolver=FakeHandResolver())


def _make_live_state(session: TournamentSession):
    specs = human_table_seat_specs(session)
    players = tuple(Player(name=s.player_id, stack=s.stack, is_human=s.is_human) for s in specs)
    bb = session.current_level().big_blind
    gs = PokerGameState(
        players=players, deck=(), current_ante=bb, last_raise_amount=bb, current_dealer_idx=0
    )
    return SimpleNamespace(game_state=gs)


def _play_live_hand(session: TournamentSession, sm) -> None:
    """Simulate the live game engine playing one hand at the human's table and
    leaving the (conserved) result on the game state, as it would at HAND_OVER."""
    gs = sm.game_state
    seat_order = [p.name for p in gs.players]
    stacks = {p.name: p.stack for p in gs.players}
    result = FakeHandResolver().resolve(
        seat_order=seat_order,
        stacks=stacks,
        level=session.current_level(),
        button=gs.current_dealer_idx,
        seed=session.rounds * 13 + 1,
    )
    new_players = tuple(
        Player(name=p.name, stack=result[p.name], is_human=p.is_human) for p in gs.players
    )
    sm.game_state = gs.update(players=new_players)


def _game_data(session: TournamentSession):
    specs = human_table_seat_specs(session)
    ai = {s.player_id: SimpleNamespace() for s in specs if not s.is_human}
    return {
        'tournament_session': session,
        'tournament_table_id': session.human_table.table_id,
        'tournament_human_id': session.human_id,
        'ai_controllers': ai,
        'memory_manager': None,
    }


def _stub_make(name, sm):
    return SimpleNamespace()


def _drive(session: TournamentSession):
    sm = _make_live_state(session)
    game_data = _game_data(session)
    total = session.config.total_chips
    last = None
    guard = 0
    while True:
        _play_live_hand(session, sm)
        last = advance_tournament_after_hand(game_data, sm, make_controller=_stub_make)
        guard += 1
        assert guard < 100_000
        # field conservation holds at every boundary
        assert session.field.chip_sum() == total
        if last.kind in (HUMAN_OUT, COMPLETE):
            break
        # continue / relocated: the live game was reconciled to the field's view
        live_names = sorted(p.name for p in sm.game_state.players)
        assert live_names == sorted(session.human_table.players)
        assert sm.game_state.current_ante == session.current_level().big_blind
        expected_ai = {n for n in session.human_table.players if n != session.human_id}
        assert set(game_data['ai_controllers']) == expected_ai
        # the human is always present while in
        assert any(p.is_human for p in sm.game_state.players)
    return last, game_data, sm


def test_live_bridge_runs_to_completion():
    last, _gd, _sm = _drive(_session(8, table_size=4, seed=1))
    assert last.kind in (HUMAN_OUT, COMPLETE)


def test_live_bridge_reconciles_and_conserves_across_a_run():
    # The invariants inside _drive (roster match, ante, conservation, controllers)
    # are the substance; this just runs a larger field through them.
    last, _gd, _sm = _drive(_session(18, table_size=6, seed=4))
    assert last.kind in (HUMAN_OUT, COMPLETE)


def test_live_bridge_follows_relocation():
    # 24/6 collapses tables; if the human survives a break, the bridge must
    # reconcile their live game onto the new table (roster changes, conserved).
    session = _session(24, table_size=6, seed=5)
    sm = _make_live_state(session)
    game_data = _game_data(session)
    relocated = False
    while not session.is_complete() and not session.human_out:
        _play_live_hand(session, sm)
        out = advance_tournament_after_hand(game_data, sm, make_controller=_stub_make)
        assert session.field.chip_sum() == session.config.total_chips
        if out.kind == RELOCATED:
            relocated = True
            # live game now reflects the new table
            assert sorted(p.name for p in sm.game_state.players) == sorted(
                session.human_table.players
            )
        if out.kind in (HUMAN_OUT, COMPLETE):
            break
    if not session.human_out:
        assert relocated


def test_human_out_stops_the_bridge():
    for seed in range(12):
        session = _session(12, table_size=4, seed=seed)
        last, _gd, _sm = _drive(session)
        if last.kind == HUMAN_OUT:
            # finishing position recorded, and the field can still be played out
            assert last.standings['human']['out'] is True
            assert 2 <= last.standings['human']['rank'] <= session.config.field_size
            session.play_out()
            assert session.is_complete()
            return
    pytest.skip("human won every sampled seed")
