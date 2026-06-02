"""Tests for Surface B — opponent sizing-tell over time (coach/dossier).

Covers the pure core ``compute_opponent_sizing_tell``: the big/small bin gating,
verdict classification (face_up / balanced / reverse), confidence tiering, the
per-block stability trend (stable vs mixing), and the under-sample no-op. DB-free.
See docs/plans/SIZING_COACH_SURFACES.md (Surface B).
"""

from __future__ import annotations

from flask_app.services.coach_sizing_tells import (
    CONFIRM_MIN_BETS,
    MIN_PER_BIN,
    compute_opponent_sizing_tell,
    sizing_label,
)


def _bet(frac, eq, i):
    return {
        'bet_fraction': frac,
        'equity': eq,
        'created_at': f'2026-06-01T00:00:{i:02d}',
        'hand_number': i,
    }


def _mix(n, *, big_frac=1.0, small_frac=0.4, big_eq=0.85, small_eq=0.45):
    """n decisions alternating big/small, oldest→newest."""
    out = []
    for i in range(n):
        if i % 2 == 0:
            out.append(_bet(big_frac, big_eq, i))
        else:
            out.append(_bet(small_frac, small_eq, i))
    return out


class TestGatingAndVerdict:
    def test_insufficient_when_a_bin_is_undersampled(self):
        # 10 big bets, only 2 small → small bin below MIN_PER_BIN.
        decs = [_bet(1.0, 0.8, i) for i in range(10)] + [_bet(0.3, 0.4, 10 + i) for i in range(2)]
        tell = compute_opponent_sizing_tell(decs)
        assert tell.confidence == 'insufficient'
        assert tell.verdict == 'unknown'
        assert tell.stability == 'insufficient'
        assert tell.n_small == 2 and tell.n_big == 10

    def test_face_up(self):
        tell = compute_opponent_sizing_tell(_mix(24, big_eq=0.85, small_eq=0.45))
        assert tell.verdict == 'face_up'
        assert tell.score == 0.4
        assert tell.exploit and 'Fold' in tell.exploit

    def test_balanced(self):
        # big and small bets show the same equity → no tell.
        tell = compute_opponent_sizing_tell(_mix(24, big_eq=0.55, small_eq=0.50))
        assert tell.verdict == 'balanced'
        assert tell.exploit is None

    def test_reverse(self):
        # big bets are WEAKER than small (sizes up with air = bluffs big).
        tell = compute_opponent_sizing_tell(_mix(24, big_eq=0.35, small_eq=0.60))
        assert tell.verdict == 'reverse'
        assert tell.exploit and "Don't fold" in tell.exploit


class TestConfidence:
    def test_low_confidence_small_sample(self):
        # exactly MIN_PER_BIN each, total 8 < CONFIRM_MIN_BETS.
        decs = _mix(2 * MIN_PER_BIN)
        tell = compute_opponent_sizing_tell(decs)
        assert tell.confidence == 'low'

    def test_high_confidence_large_sample(self):
        tell = compute_opponent_sizing_tell(_mix(CONFIRM_MIN_BETS + 4))
        assert tell.confidence == 'high'


class TestStabilityTrend:
    def test_stable_series(self):
        tell = compute_opponent_sizing_tell(_mix(36))
        assert tell.stability == 'stable'
        assert len(tell.series) == 6
        assert all(s is not None for s in tell.series)  # every block has both bins

    def test_mixing_when_recent_blocks_collapse(self):
        # First 24 face-up; last 12 the big bets drop to small-equity (they start
        # bluffing big) → the latest block's score collapses below the trailing mean.
        early = _mix(24, big_eq=0.85, small_eq=0.45)
        late = _mix(12, big_eq=0.45, small_eq=0.45)  # big now == small → score ~0
        for j, d in enumerate(late):
            d['created_at'] = f'2026-06-02T00:00:{j:02d}'
            d['hand_number'] = 100 + j
        tell = compute_opponent_sizing_tell(early + late)
        assert tell.stability == 'mixing'

    def test_series_nulls_where_block_lacks_a_bin(self):
        # All big in the first half, all small in the second → no block straddles,
        # so blocks are ungradeable (None) except where a chunk mixes at a boundary.
        decs = [_bet(1.0, 0.8, i) for i in range(12)] + [_bet(0.3, 0.4, 12 + i) for i in range(12)]
        tell = compute_opponent_sizing_tell(decs)
        assert tell.verdict == 'face_up'  # overall still gradeable (12 big, 12 small)
        assert any(s is None for s in tell.series)  # but most blocks are single-bin


class TestLabels:
    def test_labels(self):
        assert sizing_label('face_up') == 'Big bets = strength'
        assert sizing_label('reverse') == 'Big bets = bluffs'
        assert sizing_label('balanced') == 'Balanced sizing'
