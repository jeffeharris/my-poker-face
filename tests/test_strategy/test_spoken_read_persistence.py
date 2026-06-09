"""Regression tests for `_last_spoken_read` persistence across the speech
cooldown (backlog #12 Phase 1).

The spoken-read design has TWO surfacing channels:
  - the SPEECH channel (`opponent_observations`) — cooldown-gated anti-spam;
  - the narration_facts channel (`_last_spoken_read`) — "always-in-context",
    documented to carry the read *even on hands the speech channel is gated*.

A prior implementation reset `_last_spoken_read = None` every decision and only
repopulated it from the cooldown-filtered `spoken_reads`, so the two channels
were perfectly coupled — the narration channel lost the read on exactly the
cooldown-gated hands it was meant to cover. These tests pin the intended
behaviour: persist the prior read across the cooldown while its opponent is
still in the hand, and drop it once that opponent leaves (no stale leak).
"""

from types import SimpleNamespace

from poker.memory.opponent_model import OpponentModelManager, OpponentTendencies
from poker.strategy.spoken_reads import SpokenReadConfig, SpokenReadState
from poker.tiered_bot_controller import TieredBotController


def _matured_fold_to_cbet() -> OpponentTendencies:
    """A confident-tier fold_to_cbet read (30 c-bets faced, 20 folds)."""
    t = OpponentTendencies()
    t._cbet_faced_count = 30
    t._fold_to_cbet_count = 20
    t.hands_dealt = 30
    t.hands_observed = 30
    t._recalculate_stats()
    return t


def _manager_with(observer: str, models: dict) -> OpponentModelManager:
    mgr = OpponentModelManager()
    for opp, tend in models.items():
        mgr.get_model(observer, opp).tendencies = tend
    return mgr


def _controller(manager) -> TieredBotController:
    """A bare controller with only the attrs `_select_opponent_observations`
    touches (parent __init__ is bypassed)."""
    c = TieredBotController.__new__(TieredBotController)
    c.player_name = 'Hero'
    c.opponent_model_manager = manager
    c._spoken_read_state = SpokenReadState()
    c._spoken_read_config = SpokenReadConfig()
    c._last_spoken_read = None
    return c


def _game_state(opponent_names):
    players = [SimpleNamespace(name='Hero', is_folded=False, bet=0)]
    players += [SimpleNamespace(name=n, is_folded=False, bet=0) for n in opponent_names]
    return SimpleNamespace(players=players)


_HERO = SimpleNamespace(name='Hero')


def test_persists_across_cooldown_while_opponent_active():
    """Hand 1 voices the read; hand 2 is cooldown-gated (empty speech channel)
    but the same opponent is still in the hand → the narration channel keeps
    the read so the 'figuring you out' arc stays in context."""
    mgr = _manager_with('Hero', {'Villain': _matured_fold_to_cbet()})
    c = _controller(mgr)
    gs = _game_state(['Villain'])

    # Hand 1: read is eligible and voiced → stashed for both channels.
    c._select_opponent_observations(gs, _HERO)
    assert c._last_spoken_read is not None
    assert c._last_spoken_read.opponent == 'Villain'
    voiced = c._last_spoken_read

    # Hand 2: default cooldown (8) gates the speech channel → spoken_reads empty.
    # The narration channel must still carry the prior read.
    c._select_opponent_observations(gs, _HERO)
    assert c._last_spoken_read is voiced


def test_cleared_when_read_opponent_leaves_the_hand():
    """A cooldown-gated hand where the read's opponent is no longer active must
    drop the stale read rather than leak it into an unrelated table."""
    mgr = _manager_with(
        'Hero',
        {
            'Villain': _matured_fold_to_cbet(),
            'Other': OpponentTendencies(),  # no matured read
        },
    )
    c = _controller(mgr)

    # Hand 1 with Villain present → read about Villain stashed.
    c._select_opponent_observations(_game_state(['Villain']), _HERO)
    assert c._last_spoken_read is not None
    assert c._last_spoken_read.opponent == 'Villain'

    # Hand 2: Villain is gone; only Other (no matured read) is in the hand.
    # No fresh read AND the prior read's opponent left → cleared.
    c._select_opponent_observations(_game_state(['Other']), _HERO)
    assert c._last_spoken_read is None
