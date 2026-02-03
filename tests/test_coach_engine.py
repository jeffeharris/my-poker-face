"""Tests for coach_engine helper functions."""

import pytest

from flask_app.services.coach_engine import _get_style_label


class TestGetStyleLabel:
    """Test _get_style_label() style classification.

    Thresholds from poker.config:
    - VPIP_TIGHT_THRESHOLD = 0.3 (< 0.3 is tight)
    - AGGRESSION_FACTOR_HIGH = 1.5 (> 1.5 is aggressive)
    """

    def test_tight_aggressive(self):
        """Low VPIP + high aggression = tight-aggressive."""
        assert _get_style_label(vpip=0.2, aggression=2.0) == 'tight-aggressive'

    def test_loose_aggressive(self):
        """High VPIP + high aggression = loose-aggressive."""
        assert _get_style_label(vpip=0.5, aggression=2.0) == 'loose-aggressive'

    def test_tight_passive(self):
        """Low VPIP + low aggression = tight-passive."""
        assert _get_style_label(vpip=0.2, aggression=1.0) == 'tight-passive'

    def test_loose_passive(self):
        """High VPIP + low aggression = loose-passive."""
        assert _get_style_label(vpip=0.5, aggression=1.0) == 'loose-passive'

    def test_boundary_tight_threshold(self):
        """VPIP at exactly 0.3 (threshold) is not tight."""
        # VPIP_TIGHT_THRESHOLD = 0.3, so 0.3 is NOT < 0.3 → loose
        assert _get_style_label(vpip=0.3, aggression=2.0) == 'loose-aggressive'

    def test_boundary_aggression_threshold(self):
        """Aggression at exactly 1.5 (threshold) is not aggressive."""
        # AGGRESSION_FACTOR_HIGH = 1.5, so 1.5 is NOT > 1.5 → passive
        assert _get_style_label(vpip=0.2, aggression=1.5) == 'tight-passive'
