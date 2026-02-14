"""Tests for looseness-aware preflop range classification."""

import pytest

from poker.range_guidance import (
    classify_preflop_hand_for_player,
    _game_position_to_range_key,
    _position_display_name,
)


class TestGamePositionMapping:
    """Test that game position names map correctly to range keys."""

    def test_under_the_gun(self):
        assert _game_position_to_range_key('under_the_gun') == 'early'

    def test_middle_position_1(self):
        assert _game_position_to_range_key('middle_position_1') == 'middle'

    def test_middle_position_2(self):
        assert _game_position_to_range_key('middle_position_2') == 'middle'

    def test_cutoff(self):
        assert _game_position_to_range_key('cutoff') == 'late'

    def test_button(self):
        assert _game_position_to_range_key('button') == 'button'

    def test_small_blind_player(self):
        assert _game_position_to_range_key('small_blind_player') == 'small_blind'

    def test_big_blind_player(self):
        assert _game_position_to_range_key('big_blind_player') == 'big_blind'


class TestPositionDisplayName:
    """Test human-readable position names."""

    def test_early(self):
        assert _position_display_name('early') == 'early position'

    def test_button(self):
        assert _position_display_name('button') == 'the button'

    def test_big_blind(self):
        assert _position_display_name('big_blind') == 'the big blind'


class TestPremiumHands:
    """Premium hands should always show raise/re-raise guidance."""

    @pytest.mark.parametrize('hand', ['AA', 'KK', 'QQ', 'JJ', 'AKs'])
    def test_premium_always_raise(self, hand):
        result = classify_preflop_hand_for_player(hand, 0.1, 'under_the_gun')
        assert 'premium hand' in result
        assert 'raise' in result

    @pytest.mark.parametrize('looseness', [0.0, 0.5, 1.0])
    def test_premium_regardless_of_looseness(self, looseness):
        result = classify_preflop_hand_for_player('AA', looseness, 'under_the_gun')
        assert 'premium hand' in result


class TestSolidHands:
    """Strong hands in range should be 'raise-worthy'."""

    def test_strong_hand_loose_player_button(self):
        result = classify_preflop_hand_for_player('TT', 0.7, 'button')
        assert 'raise-worthy' in result

    def test_aqo_medium_looseness_late(self):
        result = classify_preflop_hand_for_player('AQo', 0.5, 'cutoff')
        assert 'raise-worthy' in result


class TestOutsideRange:
    """Trash hands should get fold guidance scaled by looseness."""

    def test_trash_tight_player_strong_fold(self):
        # Tight player (0.1) should get "you should fold this"
        result = classify_preflop_hand_for_player('72o', 0.1, 'under_the_gun')
        assert 'should fold' in result
        assert '~' in result

    def test_trash_medium_player_moderate_fold(self):
        # Medium player (0.5) should get "fold from here"
        result = classify_preflop_hand_for_player('72o', 0.5, 'under_the_gun')
        assert 'fold' in result.lower()

    def test_trash_loose_player_soft_nudge(self):
        # Loose player (0.9) on button → 95% range, 72o is barely in-range
        # Should get soft "edge of range" wording, NOT "should fold"
        result = classify_preflop_hand_for_player('72o', 0.9, 'button')
        assert 'edge' in result or 'playable' in result or 'speculative' in result
        assert 'should fold' not in result


class TestLoosenesScaling:
    """Verify that wording strength scales with looseness."""

    def test_tight_player_gets_strong_language(self):
        result = classify_preflop_hand_for_player('T8o', 0.2, 'under_the_gun')
        # Should contain "should fold" or "fold unless"
        assert 'fold' in result.lower()

    def test_medium_player_gets_moderate_language(self):
        result = classify_preflop_hand_for_player('T8o', 0.5, 'under_the_gun')
        assert 'fold' in result.lower()
        assert 'should fold' not in result  # not the strongest form

    def test_loose_player_gets_soft_language(self):
        result = classify_preflop_hand_for_player('T8o', 0.8, 'under_the_gun')
        # Should NOT say "should fold" or "fold from here"
        assert 'should fold' not in result
        assert 'fold from here' not in result

    def test_same_hand_different_looseness(self):
        # Same hand, same position, but different looseness → different wording
        tight_result = classify_preflop_hand_for_player('J6o', 0.2, 'button')
        loose_result = classify_preflop_hand_for_player('J6o', 0.8, 'button')
        # Both should mention the hand but with different tones
        assert 'J6o' in tight_result
        assert 'J6o' in loose_result
        assert tight_result != loose_result


class TestBoundaryHands:
    """Hands near the range boundary should get appropriate guidance."""

    def test_edge_of_range(self):
        result = classify_preflop_hand_for_player('J9s', 0.5, 'button')
        assert result
        assert 'J9s' in result

    def test_just_outside_tight_player(self):
        # K9s at looseness=0.3 early → just outside, tight player
        result = classify_preflop_hand_for_player('K9s', 0.3, 'under_the_gun')
        assert 'fold' in result.lower()

    def test_just_outside_loose_player(self):
        # Same hand but for loose player → softer language
        result = classify_preflop_hand_for_player('K9s', 0.7, 'under_the_gun')
        assert 'K9s' in result
        assert 'should fold' not in result


class TestEmptyInput:
    """Empty canonical should return empty string."""

    def test_empty_canonical(self):
        assert classify_preflop_hand_for_player('', 0.5, 'button') == ''

    def test_empty_canonical_tight(self):
        assert classify_preflop_hand_for_player('', 0.2, 'under_the_gun') == ''


class TestOutputFormat:
    """Verify output format matches expected patterns."""

    def test_premium_format(self):
        result = classify_preflop_hand_for_player('AA', 0.5, 'button')
        assert result.startswith('AA - ')

    def test_solid_no_percentage(self):
        result = classify_preflop_hand_for_player('AQs', 0.7, 'button')
        assert 'raise-worthy' in result
        assert '%' not in result

    def test_outside_range_includes_percentage(self):
        result = classify_preflop_hand_for_player('72o', 0.3, 'under_the_gun')
        assert '~' in result
        assert '%' in result

    def test_marginal_includes_percentage(self):
        result = classify_preflop_hand_for_player('98s', 0.5, 'middle_position_1')
        if 'marginal' in result:
            assert '%' in result
