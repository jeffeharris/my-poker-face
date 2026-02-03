"""Tests for board texture analysis."""

import pytest
from poker.board_analyzer import (
    analyze_board_texture,
    get_texture_description,
    _is_connected,
)


class TestAnalyzeBoardTexture:
    """Tests for the analyze_board_texture function."""

    def test_empty_board_returns_num_cards_zero(self):
        """Pre-flop (no community cards) returns minimal dict."""
        result = analyze_board_texture([])
        assert result == {"num_cards": 0}

    def test_partial_board_returns_num_cards(self):
        """Incomplete board (1-2 cards) returns just num_cards."""
        result = analyze_board_texture(["Ah"])
        assert result == {"num_cards": 1}

        result = analyze_board_texture(["Ah", "Kd"])
        assert result == {"num_cards": 2}

    def test_dry_rainbow_flop(self):
        """K-7-2 rainbow is a classic dry flop."""
        result = analyze_board_texture(["Kh", "7d", "2s"])

        assert result["num_cards"] == 3
        assert result["paired"] is False
        assert result["monotone"] is False
        assert result["two_tone"] is False
        assert result["rainbow"] is True
        assert result["connected"] is False
        assert result["texture_category"] == "dry"

    def test_monotone_flop_is_wet(self):
        """All same suit flop is wet (flush draw present)."""
        result = analyze_board_texture(["Ah", "Jh", "4h"])

        assert result["monotone"] is True
        assert result["two_tone"] is False
        assert result["rainbow"] is False
        assert result["texture_category"] in ("wet", "very_wet")

    def test_two_tone_flop(self):
        """Two suits on the flop."""
        result = analyze_board_texture(["Ah", "Jh", "4d"])

        assert result["monotone"] is False
        assert result["two_tone"] is True
        assert result["rainbow"] is False

    def test_paired_board(self):
        """Board with a pair."""
        result = analyze_board_texture(["Kh", "Kd", "2s"])

        assert result["paired"] is True
        assert result["double_paired"] is False
        assert result["trips_on_board"] is False

    def test_double_paired_board(self):
        """Board with two pairs."""
        result = analyze_board_texture(["Kh", "Kd", "2s", "2c", "7h"])

        assert result["paired"] is True
        assert result["double_paired"] is True
        assert result["trips_on_board"] is False

    def test_trips_on_board(self):
        """Board with three of a kind."""
        result = analyze_board_texture(["Kh", "Kd", "Ks", "2c", "7h"])

        assert result["paired"] is True
        assert result["trips_on_board"] is True

    def test_connected_board(self):
        """9-8-7 is connected (straight draw possible)."""
        result = analyze_board_texture(["9h", "8d", "7s"])

        assert result["connected"] is True

    def test_broadway_heavy_board(self):
        """Board with multiple high cards."""
        result = analyze_board_texture(["Ah", "Kd", "Qs"])

        assert result["high_card_count"] == 3
        assert result["connected"] is True  # A-K-Q is connected

    def test_low_card_board(self):
        """Board with no broadway cards."""
        result = analyze_board_texture(["7h", "4d", "2s"])

        assert result["high_card_count"] == 0

    def test_very_wet_board(self):
        """Monotone connected broadway is very wet."""
        result = analyze_board_texture(["Qh", "Jh", "Th"])

        assert result["monotone"] is True
        assert result["connected"] is True
        assert result["high_card_count"] == 3
        assert result["texture_category"] == "very_wet"

    def test_turn_card_adds_to_texture(self):
        """Turn card can change texture category."""
        flop_result = analyze_board_texture(["Kh", "7d", "2s"])
        turn_result = analyze_board_texture(["Kh", "7d", "2s", "Kc"])

        assert flop_result["paired"] is False
        assert turn_result["paired"] is True

    def test_river_full_board(self):
        """River has all 5 community cards."""
        result = analyze_board_texture(["Ah", "Kd", "Qs", "Jc", "Th"])

        assert result["num_cards"] == 5
        assert result["connected"] is True  # Broadway straight
        assert result["high_card_count"] == 5


class TestIsConnected:
    """Tests for the _is_connected helper function."""

    def test_sequential_cards_connected(self):
        """9-8-7 (indices 4, 5, 6) is connected."""
        assert _is_connected([4, 5, 6]) is True

    def test_gapped_cards_connected(self):
        """T-8-7 has gap but still within 4-rank window."""
        # T=3, 8=5, 7=6 -> sorted [3, 5, 6]
        assert _is_connected([3, 5, 6]) is True

    def test_wider_gap_not_connected(self):
        """K-8-2 is too spread out."""
        # K=1, 8=5, 2=12 -> sorted [1, 5, 12]
        assert _is_connected([1, 5, 12]) is False

    def test_wheel_connected(self):
        """A-2-3 wheel is connected."""
        # A=0, 2=12, 3=11 -> sorted [0, 11, 12]
        assert _is_connected([0, 11, 12]) is True

    def test_not_enough_cards(self):
        """Less than 3 cards can't be connected."""
        assert _is_connected([0, 1]) is False
        assert _is_connected([0]) is False
        assert _is_connected([]) is False


class TestGetTextureDescription:
    """Tests for human-readable texture descriptions."""

    def test_preflop_description(self):
        """Pre-flop returns 'pre-flop'."""
        texture = {"num_cards": 0}
        assert get_texture_description(texture) == "pre-flop"

    def test_dry_flop_description(self):
        """Dry rainbow flop has descriptive text."""
        texture = analyze_board_texture(["Kh", "7d", "2s"])
        desc = get_texture_description(texture)

        assert "dry" in desc
        assert "rainbow" in desc
        assert "flop" in desc

    def test_wet_monotone_description(self):
        """Wet monotone board mentions both."""
        texture = analyze_board_texture(["Qh", "Jh", "Th"])
        desc = get_texture_description(texture)

        assert "monotone" in desc

    def test_turn_description(self):
        """Turn board says 'turn'."""
        texture = analyze_board_texture(["Kh", "7d", "2s", "Ac"])
        desc = get_texture_description(texture)

        assert "turn" in desc

    def test_river_description(self):
        """River board says 'river'."""
        texture = analyze_board_texture(["Kh", "7d", "2s", "Ac", "5h"])
        desc = get_texture_description(texture)

        assert "river" in desc


class TestWetnessScoring:
    """Tests for wetness score calculation."""

    def test_pure_dry_board(self):
        """K-7-2 rainbow is dry (score 0)."""
        result = analyze_board_texture(["Kh", "7d", "2s"])
        assert result["texture_category"] == "dry"

    def test_semi_wet_board(self):
        """Two-tone or single factor is semi_wet."""
        # Two-tone only (+1) = semi_wet
        result = analyze_board_texture(["Kh", "Jh", "2s"])
        assert result["texture_category"] in ("dry", "semi_wet")

    def test_wet_board(self):
        """Connected two-tone with high cards is wet."""
        # Two-tone (+1), connected (+2), high cards (+1) = 4 -> wet
        result = analyze_board_texture(["Qh", "Jh", "Ts"])
        assert result["texture_category"] in ("wet", "very_wet")

    def test_very_wet_board(self):
        """Monotone connected broadway is very wet."""
        # Monotone (+3), connected (+2), high cards (+1) = 6 -> very_wet
        result = analyze_board_texture(["Qh", "Jh", "Th"])
        assert result["texture_category"] == "very_wet"
