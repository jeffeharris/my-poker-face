"""Tests for the adaptive overbet gate (PERSONALITY_PRICING_AND_VARIETY.md, attacker side).

The static overbet fires vs everyone; the adaptive overbet scales its fraction by
the live value-vs-station detection intensity so it only overbets a detected payer
and no-ops vs balanced / sizing-reading opponents.
"""

from poker.tiered_bot_controller import TieredBotController


def _ctrl(*, adaptive, intensity=None, base=1.0):
    """Minimal controller exercising _effective_overbet_fraction (parent __init__
    bypassed). Omit `intensity` to simulate 'attribute never set' (no manager)."""
    c = TieredBotController.__new__(TieredBotController)
    c.overbet_fraction = base
    c.adaptive_overbet = adaptive
    if intensity is not None:
        c._last_value_vs_station_intensity_raw = intensity
    return c


def test_static_ignores_intensity():
    assert _ctrl(adaptive=False, intensity=0.0)._effective_overbet_fraction() == 1.0
    assert _ctrl(adaptive=False, intensity=1.0, base=0.7)._effective_overbet_fraction() == 0.7


def test_adaptive_no_detection_is_no_op():
    # intensity 0 (balanced opponent) → fraction 0 → overbet skipped.
    assert _ctrl(adaptive=True, intensity=0.0)._effective_overbet_fraction() == 0.0


def test_adaptive_missing_signal_is_no_op():
    # No manager attached → attribute never set → getattr default 0.0 → no-op.
    assert _ctrl(adaptive=True)._effective_overbet_fraction() == 0.0


def test_adaptive_full_detection_is_full_overbet():
    assert _ctrl(adaptive=True, intensity=1.0)._effective_overbet_fraction() == 1.0


def test_adaptive_scales_with_confidence():
    assert _ctrl(adaptive=True, intensity=0.5)._effective_overbet_fraction() == 0.5
    assert _ctrl(adaptive=True, intensity=0.5, base=0.8)._effective_overbet_fraction() == 0.4


def test_adaptive_clamps_intensity():
    # Defensive clamp: intensity outside [0,1] can't blow past the configured base.
    assert _ctrl(adaptive=True, intensity=1.5)._effective_overbet_fraction() == 1.0
    assert _ctrl(adaptive=True, intensity=-0.3)._effective_overbet_fraction() == 0.0
