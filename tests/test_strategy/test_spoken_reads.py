"""Tests for the spoken-reads surfacing (backlog #12 Phase 1).

Covers: arc-tier selection from a read's own sample count; cooldown advancing
on ELIGIBLE hands (not only voiced ones); no raw-stat/number leakage in the
intuition-framed text; the read priority ordering; None below the sample
threshold; the max-2 cap; graceful behaviour when the manager/model is absent;
and that the re-homed `deep_reads_from_tendencies` is importable from both the
new (`poker.memory`) and old (`flask_app.services`) call sites.
"""

import re

from poker.memory.opponent_model import OpponentModelManager, OpponentTendencies
from poker.strategy.spoken_reads import (
    ARC_CONFIDENT,
    ARC_SURE,
    ARC_TENTATIVE,
    READ_PRIORITY,
    SpokenReadConfig,
    SpokenReadState,
    _select_best_read,
    select_spoken_reads,
)

# ── Helpers ─────────────────────────────────────────────────────────────────


def _tendencies(
    *,
    cbet_faced=0,
    fold_to_cbet_count=0,
    postflop_seen_as_pfr=0,
    cbet_attempt=0,
    barrel_opportunities=0,
    barrel_count=0,
    hands_dealt=0,
    all_in_count=0,
    # Sizing-tell bins (Phase 4). Polarization needs BOTH equity bins; the
    # equity *means* must be set before _recalculate_stats derives the score.
    equity_big_count=0,
    equity_small_count=0,
    equity_big_mean=0.5,
    equity_small_mean=0.5,
    big_bet_faced=0,
    fold_to_big_bet_count=0,
) -> OpponentTendencies:
    """Build a tendency with the sample counters that gate the spoken reads."""
    t = OpponentTendencies()
    t._cbet_faced_count = cbet_faced
    t._fold_to_cbet_count = fold_to_cbet_count
    t._postflop_seen_as_pfr_count = postflop_seen_as_pfr
    t._cbet_attempt_count = cbet_attempt
    t._barrel_opportunity_count = barrel_opportunities
    t._barrel_count = barrel_count
    t.hands_dealt = hands_dealt
    t.hands_observed = hands_dealt
    t._all_in_count = all_in_count
    # Sizing polarization: bin counts + means feed _recalculate_stats, which
    # derives sizing_polarization_score = big_mean − small_mean when both bins
    # clear SIZING_MIN_BIN_SAMPLE.
    t._equity_betting_big_count = equity_big_count
    t._equity_betting_small_count = equity_small_count
    t.equity_when_betting_big = equity_big_mean
    t.equity_when_betting_small = equity_small_mean
    # fold_to_big_bet is updated incrementally live (not by _recalculate_stats).
    t._big_bet_faced_count = big_bet_faced
    t._fold_to_big_bet_count = fold_to_big_bet_count
    if big_bet_faced:
        t.fold_to_big_bet = fold_to_big_bet_count / big_bet_faced
    t._recalculate_stats()
    return t


def _manager_with(observer: str, models: dict) -> OpponentModelManager:
    """A real manager seeded with {opponent_name: OpponentTendencies}."""
    mgr = OpponentModelManager()
    for opp, tend in models.items():
        model = mgr.get_model(observer, opp)
        model.tendencies = tend
    return mgr


CONFIG = SpokenReadConfig()


# ── Arc-tier selection ──────────────────────────────────────────────────────


def test_arc_tier_tentative():
    t = _tendencies(cbet_faced=6, fold_to_cbet_count=4)
    reads = _select_best_read(t, {'fold_to_cbet': 0.67}, CONFIG)
    assert reads is not None
    assert reads[0] == 'fold_to_cbet'
    assert reads[1] == ARC_TENTATIVE


def test_arc_tier_confident():
    t = _tendencies(cbet_faced=30, fold_to_cbet_count=20)
    reads = _select_best_read(t, {'fold_to_cbet': 0.67}, CONFIG)
    assert reads[1] == ARC_CONFIDENT


def test_arc_tier_sure():
    t = _tendencies(cbet_faced=70, fold_to_cbet_count=50)
    reads = _select_best_read(t, {'fold_to_cbet': 0.71}, CONFIG)
    assert reads[1] == ARC_SURE


def test_none_below_sample_threshold():
    # 4 faced c-bets is below the read's min_samples (5).
    t = _tendencies(cbet_faced=4, fold_to_cbet_count=3)
    reads = _select_best_read(t, {'fold_to_cbet': 0.75}, CONFIG)
    assert reads is None


def test_none_when_deep_read_value_is_none():
    # Even with samples, a None deep-read value means the read's own gate
    # didn't pass — don't surface it.
    t = _tendencies(cbet_faced=30, fold_to_cbet_count=20)
    reads = _select_best_read(t, {'fold_to_cbet': None}, CONFIG)
    assert reads is None


# ── Priority ordering ───────────────────────────────────────────────────────


def test_priority_table_order():
    keys = [spec.read_key for spec in READ_PRIORITY]
    # Phase 4: sizing tells slot in below the action-frequency reads but above
    # the coarse global all-in rate.
    assert keys == [
        'fold_to_cbet',
        'cbet_attempt_rate',
        'barrel_frequency',
        'sizing_polarization_score',
        'fold_to_big_bet',
        'all_in_frequency',
    ]


def test_priority_prefers_fold_to_cbet_over_cbet_attempt():
    # Both matured; fold_to_cbet is higher priority.
    t = _tendencies(
        cbet_faced=30,
        fold_to_cbet_count=20,
        postflop_seen_as_pfr=30,
        cbet_attempt=20,
    )
    deep = {'fold_to_cbet': 0.67, 'cbet_attempt_rate': 0.67}
    reads = _select_best_read(t, deep, CONFIG)
    assert reads[0] == 'fold_to_cbet'


def test_falls_through_to_lower_priority_when_top_unmatured():
    # fold_to_cbet below threshold, cbet_attempt matured → pick cbet_attempt.
    t = _tendencies(
        cbet_faced=2,
        fold_to_cbet_count=1,
        postflop_seen_as_pfr=30,
        cbet_attempt=20,
    )
    deep = {'fold_to_cbet': 0.5, 'cbet_attempt_rate': 0.67}
    reads = _select_best_read(t, deep, CONFIG)
    assert reads is not None
    assert reads[0] == 'cbet_attempt_rate'


# ── Phase 4: sizing tells ───────────────────────────────────────────────────


def _matured_polarization(samples=30, gap=0.3):
    """A tendency whose sizing_polarization_score is matured + positive (bets
    big with strong hands). Both equity bins carry `samples` observations."""
    return _tendencies(
        equity_big_count=samples,
        equity_small_count=samples,
        equity_big_mean=0.5 + gap / 2,
        equity_small_mean=0.5 - gap / 2,
    )


def test_sizing_polarization_fires_at_maturity():
    t = _matured_polarization(samples=30)
    deep = {'sizing_polarization_score': 0.3}
    reads = _select_best_read(t, deep, CONFIG)
    assert reads is not None
    assert reads[0] == 'sizing_polarization_score'
    assert reads[1] == ARC_CONFIDENT  # 30 samples per bin → confident


def test_sizing_polarization_tier_scales_with_weaker_bin():
    # The WEAKER bin drives the tier: small bin only 6 → tentative even though
    # the big bin is deep.
    t = _tendencies(
        equity_big_count=80,
        equity_small_count=6,
        equity_big_mean=0.65,
        equity_small_mean=0.35,
    )
    deep = {'sizing_polarization_score': 0.3}
    reads = _select_best_read(t, deep, CONFIG)
    assert reads is not None
    assert reads[1] == ARC_TENTATIVE


def test_sizing_polarization_suppressed_below_bin_sample():
    # Both bins at 4 (== SIZING_MIN_BIN_SAMPLE, the deep_reads gate) is below
    # the tentative arc floor (5) → no read even though deep_reads would expose
    # it. And the bins below 4 → deep_reads itself is None.
    t = _tendencies(
        equity_big_count=4,
        equity_small_count=4,
        equity_big_mean=0.65,
        equity_small_mean=0.35,
    )
    reads = _select_best_read(t, {'sizing_polarization_score': 0.3}, CONFIG)
    assert reads is None  # below tentative arc floor


def test_sizing_polarization_none_when_deep_read_value_none():
    # Mature bins but deep_reads value None (e.g. one bin under the gate) →
    # don't surface.
    t = _matured_polarization(samples=30)
    reads = _select_best_read(t, {'sizing_polarization_score': None}, CONFIG)
    assert reads is None


def test_fold_to_big_bet_fires_at_maturity():
    t = _tendencies(big_bet_faced=30, fold_to_big_bet_count=24)
    deep = {'fold_to_big_bet': 0.8}
    reads = _select_best_read(t, deep, CONFIG)
    assert reads is not None
    assert reads[0] == 'fold_to_big_bet'
    assert reads[1] == ARC_CONFIDENT


def test_fold_to_big_bet_suppressed_below_sample():
    # 5 big bets faced is below this read's min_samples (6).
    t = _tendencies(big_bet_faced=5, fold_to_big_bet_count=4)
    reads = _select_best_read(t, {'fold_to_big_bet': 0.8}, CONFIG)
    assert reads is None


def test_sizing_outranks_all_in_but_yields_to_action_reads():
    # All four lower-half reads matured. fold_to_cbet (top) still wins.
    t = _tendencies(
        cbet_faced=30,
        fold_to_cbet_count=20,
        big_bet_faced=30,
        fold_to_big_bet_count=24,
        equity_big_count=30,
        equity_small_count=30,
        equity_big_mean=0.65,
        equity_small_mean=0.35,
        hands_dealt=80,
        all_in_count=10,
    )
    deep = {
        'fold_to_cbet': 0.67,
        'sizing_polarization_score': 0.3,
        'fold_to_big_bet': 0.8,
        'all_in_frequency': 0.12,
    }
    assert _select_best_read(t, deep, CONFIG)[0] == 'fold_to_cbet'

    # With the action reads gone, sizing_polarization outranks fold_to_big_bet,
    # which outranks all_in_frequency.
    deep_sizing = {
        'sizing_polarization_score': 0.3,
        'fold_to_big_bet': 0.8,
        'all_in_frequency': 0.12,
    }
    assert _select_best_read(t, deep_sizing, CONFIG)[0] == 'sizing_polarization_score'

    deep_fold_big = {'fold_to_big_bet': 0.8, 'all_in_frequency': 0.12}
    assert _select_best_read(t, deep_fold_big, CONFIG)[0] == 'fold_to_big_bet'


def test_sizing_reads_integration_over_manager():
    # End-to-end through select_spoken_reads: a matured fold_to_big_bet read
    # surfaces as number-free intuition text.
    t = _tendencies(big_bet_faced=70, fold_to_big_bet_count=56)
    mgr = _manager_with('Hero', {'Villain': t})
    obs, _state, reads = select_spoken_reads(
        observer_name='Hero',
        active_opponents=['Villain'],
        facing_opponent='Villain',
        opponent_model_manager=mgr,
        state=SpokenReadState(),
        config=CONFIG,
    )
    assert len(obs) == 1
    assert reads[0].read_key == 'fold_to_big_bet'
    assert reads[0].arc_tier == ARC_SURE
    assert not re.search(r'\d', obs[0][1])


# ── No raw-stat / number leakage ────────────────────────────────────────────


def test_no_number_or_stat_name_leak_in_text():
    """The intuition-framed text must never contain a digit or a raw stat name."""
    for spec in READ_PRIORITY:
        for tier, text in spec.phrasings.items():
            assert not re.search(r'\d', text), f"digit leaked in {spec.read_key}/{tier}: {text}"
            lowered = text.lower()
            for banned in (
                'fold_to',
                'cbet',
                'c-bet',
                'barrel',
                'frequency',
                'rate',
                'all_in',
                'all-in',
                '%',
                # Phase 4 sizing-tell stat-name jargon (the natural words
                # "bet", "big", "size" are fine intuition framing; the raw
                # stat identifiers are not).
                'polarization',
                'sizing_',
                'fold_to_big',
                'score',
            ):
                assert banned not in lowered, (
                    f"stat name '{banned}' leaked in {spec.read_key}/{tier}: {text}"
                )


# ── select_spoken_reads: integration over a manager ─────────────────────────


def test_select_returns_intuition_text_for_matured_opponent():
    t = _tendencies(cbet_faced=30, fold_to_cbet_count=20)
    mgr = _manager_with('Hero', {'Villain': t})
    obs, state, reads = select_spoken_reads(
        observer_name='Hero',
        active_opponents=['Villain'],
        facing_opponent='Villain',
        opponent_model_manager=mgr,
        state=SpokenReadState(),
        config=CONFIG,
    )
    assert len(obs) == 1
    assert obs[0][0] == 'Villain'
    assert not re.search(r'\d', obs[0][1])
    assert len(reads) == 1
    assert reads[0].arc_tier == ARC_CONFIDENT


def test_max_two_cap():
    matured = lambda: _tendencies(cbet_faced=30, fold_to_cbet_count=20)
    mgr = _manager_with(
        'Hero',
        {'A': matured(), 'B': matured(), 'C': matured()},
    )
    obs, _state, _reads = select_spoken_reads(
        observer_name='Hero',
        active_opponents=['A', 'B', 'C'],
        facing_opponent=None,
        opponent_model_manager=mgr,
        state=SpokenReadState(),
        config=CONFIG,
    )
    assert len(obs) == 2


def test_graceful_when_manager_absent():
    obs, state, reads = select_spoken_reads(
        observer_name='Hero',
        active_opponents=['Villain'],
        facing_opponent=None,
        opponent_model_manager=None,
        state=SpokenReadState(),
        config=CONFIG,
    )
    assert obs == []
    assert reads == []


def test_graceful_when_model_absent():
    mgr = OpponentModelManager()  # no models seeded
    obs, state, reads = select_spoken_reads(
        observer_name='Hero',
        active_opponents=['Villain'],
        facing_opponent=None,
        opponent_model_manager=mgr,
        state=SpokenReadState(),
        config=CONFIG,
    )
    assert obs == []
    assert reads == []
    # Must NOT have created a model as a side effect.
    assert mgr.get_model_if_exists('Hero', 'Villain') is None


def test_no_read_when_unmatured_leaves_state_untouched():
    t = _tendencies(cbet_faced=2, fold_to_cbet_count=1)  # below floor
    mgr = _manager_with('Hero', {'Villain': t})
    state0 = SpokenReadState()
    obs, state1, reads = select_spoken_reads(
        observer_name='Hero',
        active_opponents=['Villain'],
        facing_opponent=None,
        opponent_model_manager=mgr,
        state=state0,
        config=CONFIG,
    )
    assert obs == []
    # Nothing eligible → the eligible-hand counter must NOT advance.
    assert state1.eligible_hand_index == 0


# ── Anti-spam: cooldown advances on ELIGIBLE hands ──────────────────────────


def test_cooldown_advances_on_eligible_not_only_voiced():
    """Across a streak of eligible hands, the read is voiced once then suppressed
    for cooldown_hands ELIGIBLE hands — and the cooldown counts every eligible
    hand, not only the one it voiced on."""
    config = SpokenReadConfig(cooldown_hands=3)
    t = _tendencies(cbet_faced=30, fold_to_cbet_count=20)
    mgr = _manager_with('Hero', {'Villain': t})
    state = SpokenReadState()

    voiced_on = []
    for hand in range(8):
        obs, state, reads = select_spoken_reads(
            observer_name='Hero',
            active_opponents=['Villain'],
            facing_opponent=None,
            opponent_model_manager=mgr,
            state=state,
            config=config,
        )
        if obs:
            voiced_on.append(state.eligible_hand_index)

    # Eligible every hand → eligible index advanced to 8.
    assert state.eligible_hand_index == 8
    # Voiced on hand 1, then must wait cooldown_hands=3 eligible hands:
    # next eligible voice at index 4, then 7.
    assert voiced_on == [1, 4, 7]


def test_silent_hands_do_not_reset_or_spam_cooldown():
    """A hand where the opponent has no matured read (silent / not eligible)
    must NOT advance the eligible counter — so it neither resets nor spams the
    cooldown for an opponent who IS eligible."""
    config = SpokenReadConfig(cooldown_hands=3)
    matured = _tendencies(cbet_faced=30, fold_to_cbet_count=20)
    unmatured = _tendencies(cbet_faced=1, fold_to_cbet_count=0)
    mgr = _manager_with('Hero', {'V': matured, 'U': unmatured})
    state = SpokenReadState()

    # Hand 1: only V eligible → voiced, index 1.
    obs, state, _ = select_spoken_reads(
        'Hero', ['V'], None, mgr, state, config
    )
    assert obs and state.eligible_hand_index == 1

    # Several hands where ONLY the unmatured opponent is at the table:
    # nothing eligible → index must stay at 1.
    for _ in range(5):
        obs, state, _ = select_spoken_reads(
            'Hero', ['U'], None, mgr, state, config
        )
        assert obs == []
    assert state.eligible_hand_index == 1


# ── Re-home: importable from both old and new call sites ────────────────────


def test_deep_reads_importable_from_both_sites():
    from flask_app.services.opponent_reads import (
        deep_reads_from_tendencies as old_fn,
    )
    from poker.memory.opponent_reads import (
        deep_reads_from_tendencies as new_fn,
    )

    # Single owner — the shim re-exports the exact same object.
    assert old_fn is new_fn

    t = _tendencies(cbet_faced=10, fold_to_cbet_count=7)
    reads = new_fn(t)
    assert reads is not None
    assert reads['fold_to_cbet'] is not None
    assert new_fn(None) is None
