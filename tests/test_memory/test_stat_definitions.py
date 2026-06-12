"""Unit tests for the shared opponent-stat definition module.

These lock the canonical formulas + counter rules. The cross-site agreement
test (bottom) is the load-bearing one: it asserts the live `OpponentTendencies`
and a hand-rolled reducer produce identical stat values from the same event
stream — the regression that drift in the old per-site formulas would cause.
"""

import math

import pytest

from poker.memory import stat_definitions as sd

# ── Action / phase vocabularies ────────────────────────────────────────────


def test_voluntary_preflop_includes_bet_the_superset():
    # 'bet' is the canonical-superset member that the old per-site sets dropped.
    assert sd.is_voluntary_preflop("bet")
    assert all(sd.is_voluntary_preflop(a) for a in ("call", "raise", "all_in"))
    assert not sd.is_voluntary_preflop("fold")
    assert not sd.is_voluntary_preflop("check")


def test_pfr_actions_have_no_first_in_bet():
    assert sd.is_pfr_action("raise") and sd.is_pfr_action("all_in")
    assert not sd.is_pfr_action("bet")  # no first-in bet exists preflop
    assert not sd.is_pfr_action("call")


def test_aggressive_actions_include_postflop_bet():
    assert all(sd.is_aggressive_action(a) for a in ("bet", "raise", "all_in"))
    assert not sd.is_aggressive_action("call")
    assert not sd.is_aggressive_action("check")


def test_postflop_phase():
    assert all(sd.is_postflop_phase(p) for p in ("FLOP", "TURN", "RIVER"))
    assert not sd.is_postflop_phase("PRE_FLOP")


# ── safe_ratio ──────────────────────────────────────────────────────────────


def test_safe_ratio_zero_denominator_returns_default():
    assert sd.safe_ratio(5, 0) == 0.0
    assert sd.safe_ratio(5, 0, default=0.5) == 0.5
    assert sd.safe_ratio(5, -1, default=0.5) == 0.5  # negative denom guarded too


def test_safe_ratio_basic():
    assert sd.safe_ratio(1, 4) == 0.25


# ── aggression_factor (the three-way unification) ───────────────────────────


def test_af_no_actions_returns_neutral():
    assert sd.aggression_factor(0, 0, zero_call_cap=2.0) == 1.0
    assert sd.aggression_factor(0, 0, zero_call_cap=2.0, no_action_default=0.5) == 0.5


def test_af_with_calls_is_plain_ratio():
    assert sd.aggression_factor(6, 3, zero_call_cap=2.0) == 2.0
    assert sd.aggression_factor(1, 4, zero_call_cap=99.0) == 0.25


def test_af_zero_calls_is_capped_live_style():
    # Live model: cap a noisy zero-call sample at MEDIUM so it can't read EXTREME.
    assert sd.aggression_factor(6, 0, zero_call_cap=2.0) == 2.0
    assert sd.aggression_factor(1, 0, zero_call_cap=2.0) == 1.0


def test_af_zero_calls_uncapped_clone_per_street_style():
    # Clone per-street: cap = inf → caps at the raw count, i.e. float(bet_raise).
    assert sd.aggression_factor(7, 0, zero_call_cap=math.inf) == 7.0


# ── WTSD clamp / proxy numerator ────────────────────────────────────────────


def test_wtsd_clamped_by_default():
    # A preflop-allin showdown can bump showdowns past saw_flop; clamp holds [0,1].
    assert sd.wtsd(5, 4) == 1.0
    assert sd.wtsd(3, 4) == 0.75


def test_wtsd_unclamped_for_clone_proxy():
    assert sd.wtsd(5, 4, clamp=False) == 1.25


def test_wtsd_no_flop_is_zero():
    assert sd.wtsd(0, 0) == 0.0


# ── Named ratio wrappers name the right numerator/denominator ───────────────


@pytest.mark.parametrize(
    "fn, num, den, expected",
    [
        (sd.vpip, 3, 10, 0.3),
        (sd.pfr, 2, 10, 0.2),
        (sd.all_in_frequency, 1, 10, 0.1),
        (sd.fold_to_cbet, 7, 10, 0.7),
        (sd.showdown_win_rate, 4, 8, 0.5),
        (sd.call_rate_facing_bet, 9, 10, 0.9),
        (sd.all_in_per_facing_bet, 1, 20, 0.05),
        (sd.postflop_jam_open_rate, 2, 8, 0.25),
        (sd.vpip_per_voluntary_opportunity, 13, 20, 0.65),
        (sd.pfr_per_open_opportunity, 4, 16, 0.25),
        (sd.limp_rate, 1, 5, 0.2),
        # niche live-model rates
        (sd.fold_to_big_bet, 3, 5, 0.6),
        (sd.stab_frequency, 4, 10, 0.4),
        (sd.cbet_attempt_rate, 7, 10, 0.7),
        (sd.barrel_frequency, 3, 6, 0.5),
        (sd.third_barrel_frequency, 1, 4, 0.25),
        (sd.flop_check_then_barrel_rate, 2, 8, 0.25),
        # iso-over-limper scaffolding
        (sd.fold_to_iso, 6, 10, 0.6),
        (sd.limp_call_rate, 3, 10, 0.3),
        (sd.limp_reraise_rate, 1, 10, 0.1),
    ],
)
def test_named_ratios(fn, num, den, expected):
    assert fn(num, den) == pytest.approx(expected)
    assert fn(num, 0) == 0.0  # all guard the zero denominator


def test_mean_running_average():
    assert sd.mean(6.0, 3) == 2.0
    assert sd.mean(0.0, 0) == 0.0  # no samples → default
    assert sd.mean(0.0, 0, default=0.5) == 0.5


def test_polarization_is_high_minus_low():
    assert sd.polarization(0.80, 0.50) == pytest.approx(0.30)
    assert sd.polarization(0.40, 0.55) == pytest.approx(-0.15)  # negative = anti-polar


def test_iso_over_limper_responses_partition_the_denominator():
    # fold + call + reraise rates over the same faced-iso denominator sum to 1.0
    faced = 20
    assert sd.fold_to_iso(11, faced) + sd.limp_call_rate(6, faced) + sd.limp_reraise_rate(
        3, faced
    ) == pytest.approx(1.0)


# ── Cross-site agreement: live model == independent reducer ─────────────────


def test_live_model_agrees_with_reducer_on_same_event_stream():
    """Feed the same (action, phase) stream to the live `OpponentTendencies`
    and to an independent reducer built from the shared predicates + formulas.
    The derived stats must match — this is the drift the module exists to kill."""
    from poker.memory.opponent_model import OpponentTendencies
    from poker.strategy.phase_7_5_config import CONFIG

    # (action, phase, is_voluntary, was_facing_bet) — a small mixed hand stream.
    events = [
        ("raise", "PRE_FLOP", True, False),  # open
        ("bet", "FLOP", True, False),  # cbet (postflop bet → aggression)
        ("call", "TURN", True, True),  # call facing a bet
        ("fold", "RIVER", True, True),  # fold facing a bet
        ("call", "PRE_FLOP", True, True),  # limp/call (2nd hand)
        ("check", "FLOP", True, False),
        ("raise", "TURN", True, True),
    ]
    # Hand boundaries: events[0..3] = hand 1, events[4..6] = hand 2.
    hand_starts = {0, 4}

    t = OpponentTendencies()
    # Independent reducer counters.
    vpip_count = pfr_count = all_in_count = 0
    bet_raise = call_ct = 0
    saw_flop = 0
    seen_vpip_this_hand = seen_pfr_this_hand = seen_saw_flop_this_hand = False

    for i, (action, phase, vol, facing) in enumerate(events):
        count_hand = i in hand_starts
        if count_hand:
            seen_vpip_this_hand = seen_pfr_this_hand = seen_saw_flop_this_hand = False
        t.update_from_action(
            action, phase, is_voluntary=vol, count_hand=count_hand, was_facing_bet=facing
        )

        # Mirror the canonical counter rules via the shared predicates.
        if sd.is_postflop_phase(phase) and not seen_saw_flop_this_hand:
            saw_flop += 1
            seen_saw_flop_this_hand = True
        if phase == "PRE_FLOP" and vol and not seen_vpip_this_hand:
            if sd.is_voluntary_preflop(action):
                vpip_count += 1
                seen_vpip_this_hand = True
        if phase == "PRE_FLOP" and sd.is_pfr_action(action) and not seen_pfr_this_hand:
            pfr_count += 1
            seen_pfr_this_hand = True
        if sd.is_aggressive_action(action):
            bet_raise += 1
            if action == "all_in":
                all_in_count += 1
        elif action == "call":
            call_ct += 1

    denom = t.hands_dealt if t.hands_dealt > 0 else t.hands_observed
    cap = CONFIG.signal_thresholds.medium_af_postflop

    assert t._saw_flop == saw_flop
    assert t._vpip_count == vpip_count
    assert t._pfr_count == pfr_count
    assert t._bet_raise_count == bet_raise
    assert t._call_count == call_ct
    # Derived stats agree through the shared formulas.
    assert t.vpip == pytest.approx(sd.vpip(vpip_count, denom))
    assert t.pfr == pytest.approx(sd.pfr(pfr_count, denom))
    assert t.aggression_factor == pytest.approx(
        sd.aggression_factor(bet_raise, call_ct, zero_call_cap=cap)
    )


# ── Sim showdown feed (the runtime behavior change) ─────────────────────────


@pytest.mark.simulation
def test_sim_showdown_feed_records_showdowns_and_holds_wtsd_clamp():
    """`run_hand` must feed `observe_showdown` so WTSD is non-zero in sims (the
    bug this PR fixes). Drive a short real matchup, capture the manager it builds
    internally, and assert: at least one showdown was recorded (the feed fired),
    every recorded showdown sits on a TERMINAL hand, and `wtsd` stays clamped to
    [0, 1]. Previously `_showdowns` stayed 0 for every opponent in every sim."""
    import experiments.simulate_bb100 as sim
    from poker.memory.opponent_model import OpponentModelManager

    captured = []

    class _CapturingManager(OpponentModelManager):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            captured.append(self)

    st = sim.load_strategy_table()
    orig = sim.OpponentModelManager
    sim.OpponentModelManager = _CapturingManager
    try:
        # TAG vs a sticky station → reliably reaches showdown within a few dozen hands.
        sim.run_matchup("TAG", "CallStation", 80, st, base_seed=42)
    finally:
        sim.OpponentModelManager = orig

    models = [m for mgr in captured for opp in mgr.models.values() for m in opp.values()]
    assert models, "matchup built no opponent models"

    total_showdowns = sum(m.tendencies._showdowns for m in models)
    assert total_showdowns > 0, "showdown feed never fired — WTSD would read 0 in sims"

    for m in models:
        t = m.tendencies
        # Showdowns are credited on saw-flop hands (or rare preflop all-ins); the
        # clamp keeps the ratio defined regardless.
        assert 0.0 <= t.wtsd <= 1.0
        # A showdown implies the opponent was dealt in — guards against the
        # max_actions false-positive (counting a never-settled aborted hand).
        assert t._showdowns <= t.hands_observed
