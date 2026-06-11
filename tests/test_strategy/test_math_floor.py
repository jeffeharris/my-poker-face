"""Tests for the pot-odds math floor on tiered decisions."""

from poker.strategy.math_floor import apply_pot_odds_floor
from poker.strategy.strategy_profile import StrategyProfile


def make_strategy(probs):
    return StrategyProfile(action_probabilities=probs)


def test_short_stack_triggers_push():
    """Stack < 3 BB with all_in legal should override to push.

    Emits the abstract action 'jam' (not engine-level 'all_in') so
    downstream action_mapper.resolve_*_sizing handles it correctly.
    """
    base = make_strategy({'fold': 0.8, 'call': 0.15, 'raise': 0.05})
    out, trace = apply_pot_odds_floor(
        strategy=base,
        cost_to_call=200,
        pot_total=600,
        player_stack=200,  # 2 BB
        player_bet=100,
        big_blind=100,
        legal_actions=['fold', 'call', 'raise', 'all_in'],
    )
    assert trace.reason_code == 'short_stack'
    assert out.action_probabilities['jam'] == 1.0
    # No fold mass in override
    assert out.action_probabilities.get('fold', 0) == 0
    # Engine-level 'all_in' must not leak into the abstract distribution.
    assert 'all_in' not in out.action_probabilities


def test_short_stack_no_all_in_falls_back_to_call():
    """Short stack without all_in option still has to call."""
    base = make_strategy({'fold': 0.8, 'call': 0.2})
    out, trace = apply_pot_odds_floor(
        strategy=base,
        cost_to_call=200,
        pot_total=600,
        player_stack=200,
        player_bet=100,
        big_blind=100,
        legal_actions=['fold', 'call'],  # no all_in
    )
    assert trace.reason_code == 'short_stack'
    # When no residual non-fold/non-target actions exist, call gets 1.0
    # (no need to reserve 5% residual mass). Either 0.95 or 1.0 is valid.
    assert out.action_probabilities['call'] >= 0.95
    assert out.action_probabilities.get('fold', 0) == 0


def test_pot_committed_triggers_call():
    """Player_bet > player_stack means they've invested more than remaining."""
    base = make_strategy({'fold': 0.7, 'call': 0.3})
    out, trace = apply_pot_odds_floor(
        strategy=base,
        cost_to_call=100,
        pot_total=2000,
        player_stack=200,  # already invested $500, only $200 left -> committed
        player_bet=500,
        big_blind=100,  # 2 BB left (also short, but pot_committed wins on order)
        legal_actions=['fold', 'call'],
    )
    # short_stack triggers first per priority, fine — both rules want call.
    assert trace.reason_code in ('short_stack', 'pot_committed')
    # When no residual non-fold/non-target actions exist, call gets 1.0
    # (no need to reserve 5% residual mass). Either 0.95 or 1.0 is valid.
    assert out.action_probabilities['call'] >= 0.95
    assert out.action_probabilities.get('fold', 0) == 0


def test_pot_committed_only():
    """Pot-committed with deep enough stack to skip short_stack rule."""
    base = make_strategy({'fold': 0.7, 'call': 0.3})
    out, trace = apply_pot_odds_floor(
        strategy=base,
        cost_to_call=100,
        pot_total=5000,
        player_stack=400,  # 4 BB — above short_stack threshold (3)
        player_bet=800,  # invested more than remaining stack
        big_blind=100,
        legal_actions=['fold', 'call'],
    )
    assert trace.reason_code == 'pot_committed'
    # When no residual non-fold/non-target actions exist, call gets 1.0
    # (no need to reserve 5% residual mass). Either 0.95 or 1.0 is valid.
    assert out.action_probabilities['call'] >= 0.95
    assert out.action_probabilities.get('fold', 0) == 0


def test_tiny_pot_odds_triggers_call():
    """Cost <= ~5% of (cost+pot) AND cost < 5 BB — calling needs no equity."""
    base = make_strategy({'fold': 0.85, 'call': 0.15})
    out, trace = apply_pot_odds_floor(
        strategy=base,
        cost_to_call=200,  # 2 BB call into 5000 pot -> 200/5200 = ~3.8%, under 5%
        pot_total=5000,
        player_stack=8000,
        player_bet=400,
        big_blind=100,
        legal_actions=['fold', 'call'],
    )
    assert trace.reason_code == 'tiny_pot_odds'
    assert out.action_probabilities['call'] == 1.0
    assert out.action_probabilities.get('fold', 0) == 0


def test_tiny_pot_odds_skipped_when_call_is_large_in_BB():
    """Even at favorable pot odds, big absolute calls (>5 BB) skip the floor.

    Mirrors the spec's pot_committed prompt-injection rule: both pot odds
    AND a small absolute call are required (poker/prompts/CLAUDE.md).
    """
    base = make_strategy({'fold': 0.6, 'call': 0.4})
    out, trace = apply_pot_odds_floor(
        strategy=base,
        cost_to_call=2000,  # 20 BB call — large in absolute terms
        pot_total=80000,  # gives ~2.4% pot odds, but the call is huge
        player_stack=15000,
        player_bet=500,
        big_blind=100,
        legal_actions=['fold', 'call'],
    )
    assert trace.fired is False
    assert out is base


def test_no_trigger_normal_spot():
    """Healthy stack, reasonable pot odds — floor doesn't fire."""
    base = make_strategy({'fold': 0.5, 'call': 0.3, 'raise': 0.2})
    out, trace = apply_pot_odds_floor(
        strategy=base,
        cost_to_call=500,  # 500 into 1000 -> 33% required equity
        pot_total=1000,
        player_stack=8000,
        player_bet=200,
        big_blind=100,
        legal_actions=['fold', 'call', 'raise'],
    )
    assert trace.fired is False
    # Strategy returned unchanged
    assert out is base


def test_no_call_skips():
    """Free street (cost_to_call=0) doesn't fire — there's nothing to override."""
    base = make_strategy({'check': 0.7, 'raise': 0.3})
    out, trace = apply_pot_odds_floor(
        strategy=base,
        cost_to_call=0,
        pot_total=1000,
        player_stack=200,  # short stack but we're not facing a bet
        player_bet=0,
        big_blind=100,
        legal_actions=['check', 'raise'],
    )
    assert trace.fired is False
    assert out is base


def test_action_closed_skips():
    """Truly closed action — neither 'call' nor 'all_in' legal — doesn't fire."""
    base = make_strategy({'fold': 1.0})
    out, trace = apply_pot_odds_floor(
        strategy=base,
        cost_to_call=200,
        pot_total=600,
        player_stack=200,
        player_bet=100,
        big_blind=100,
        legal_actions=['fold'],  # no continue action available
    )
    assert trace.fired is False
    assert out is base


def test_call_off_short_stack_fires_jam():
    """Facing a bet bigger than our stack (call-off) must NOT be skipped.

    The engine offers ['fold', 'all_in'] (no flat 'call') when the bet
    exceeds our stack. That's a call-off, not a closed action — the floor
    must still fire. Here stack is 2 BB so short_stack wins -> jam.
    """
    base = make_strategy({'fold': 1.0})
    out, trace = apply_pot_odds_floor(
        strategy=base,
        cost_to_call=300,  # more than our 200 stack -> call-off
        pot_total=600,
        player_stack=200,  # 2 BB
        player_bet=100,
        big_blind=100,
        legal_actions=['fold', 'all_in'],  # no flat 'call'
    )
    assert trace.reason_code == 'short_stack'
    assert out.action_probabilities == {'jam': 1.0}


def test_call_off_pot_committed_fires_call():
    """Pot-committed call-off (P.T. Barnum #9716): fold:1.0 chart -> call.

    Turn spot: ~98 stack behind, already 380 in, facing a 150 bet that
    exceeds the stack, so legal_actions = ['fold', 'all_in']. The chart
    sampled fold:1.0; pot_committed (player_bet > player_stack) must
    override to 'call', which resolve_postflop_sizing later turns into the
    all_in call-off. Above the short_stack depth so pot_committed is the
    rule that fires.
    """
    base = make_strategy({'fold': 1.0})
    out, trace = apply_pot_odds_floor(
        strategy=base,
        cost_to_call=150,
        pot_total=910,
        player_stack=98,  # 9.8 BB — above the 3 BB short_stack floor
        player_bet=380,  # invested more than remaining stack -> committed
        big_blind=10,
        legal_actions=['fold', 'all_in'],  # no flat 'call' (bet > stack)
    )
    assert trace.reason_code == 'pot_committed'
    assert out.action_probabilities == {'call': 1.0}
    assert out.action_probabilities.get('fold', 0) == 0


def test_floor_is_deterministic_on_target():
    """Math floor fully overrides with target=1.0 — no residual mass.

    The residual was originally spread across non-fold non-target legal
    actions to preserve a sliver of personality (e.g. Maniac raising),
    but engine-level 'raise' has no sizing info and breaks postflop
    sizing resolution. Math floor commits fully on the target action.
    """
    base = make_strategy({'fold': 0.7, 'call': 0.2, 'raise': 0.1})
    out, _ = apply_pot_odds_floor(
        strategy=base,
        cost_to_call=100,
        pot_total=2000,
        player_stack=200,
        player_bet=100,
        big_blind=100,
        legal_actions=['fold', 'call', 'raise', 'all_in'],
    )
    # Short stack with all_in legal -> target=jam (abstract action that
    # resolves to engine-level all_in), no residual mass.
    assert out.action_probabilities == {'jam': 1.0}


def test_probabilities_sum_to_one():
    """Mass conservation under override."""
    base = make_strategy({'fold': 1.0})  # pure-fold pathological strategy
    out, _ = apply_pot_odds_floor(
        strategy=base,
        cost_to_call=100,
        pot_total=2000,
        player_stack=200,
        player_bet=100,
        big_blind=100,
        legal_actions=['fold', 'call'],
    )
    total = sum(out.action_probabilities.values())
    assert abs(total - 1.0) < 1e-9
