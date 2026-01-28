"""Tests for CommentaryGenerator drama analysis and formatting."""

import pytest
from datetime import datetime

from poker.memory.commentary_generator import CommentaryGenerator
from poker.memory.hand_history import RecordedHand, RecordedAction, PlayerHandInfo, WinnerInfo


def make_hand(
    actions=(),
    was_showdown=False,
    pot_size=100,
    winners=(),
    players=None
) -> RecordedHand:
    """Create a minimal RecordedHand for testing."""
    if players is None:
        players = (
            PlayerHandInfo(name="Alice", starting_stack=1000, position="BTN", is_human=False),
            PlayerHandInfo(name="Bob", starting_stack=1000, position="BB", is_human=False),
        )
    return RecordedHand(
        game_id="test-game",
        hand_number=1,
        timestamp=datetime.now(),
        players=players,
        hole_cards={},
        community_cards=(),
        actions=tuple(actions),
        winners=tuple(winners),
        pot_size=pot_size,
        was_showdown=was_showdown,
    )


def make_action(player="Alice", action="call", phase="PRE_FLOP", amount=0) -> RecordedAction:
    """Create a minimal RecordedAction for testing."""
    return RecordedAction(
        player_name=player,
        action=action,
        amount=amount,
        phase=phase,
        pot_after=100,
    )


class TestAnalyzeHandDrama:
    """Tests for _analyze_hand_drama level and tone detection."""

    @pytest.fixture
    def generator(self):
        return CommentaryGenerator()

    # --- Drama Level Tests ---

    def test_routine_no_factors(self, generator):
        """No drama factors → routine level."""
        hand = make_hand(actions=[make_action(action="call")])
        result = generator._analyze_hand_drama(hand, "won", big_blind=10)
        assert result["level"] == "routine"

    def test_notable_single_factor_showdown(self, generator):
        """Single factor (showdown) → notable level."""
        hand = make_hand(was_showdown=True, actions=[make_action()])
        result = generator._analyze_hand_drama(hand, "won", big_blind=10)
        assert result["level"] == "notable"

    def test_notable_single_factor_heads_up(self, generator):
        """Single factor (heads_up) → notable level."""
        hand = make_hand(actions=[make_action("Alice"), make_action("Bob")])
        result = generator._analyze_hand_drama(hand, "won", big_blind=10)
        assert result["level"] == "notable"

    def test_high_stakes_two_factors(self, generator):
        """Two factors (showdown + heads_up) → high_stakes level."""
        hand = make_hand(
            was_showdown=True,
            actions=[make_action("Alice"), make_action("Bob")],
        )
        result = generator._analyze_hand_drama(hand, "won", big_blind=10)
        assert result["level"] == "high_stakes"

    def test_climactic_all_in(self, generator):
        """All-in action → climactic level."""
        hand = make_hand(actions=[make_action(action="all_in")])
        result = generator._analyze_hand_drama(hand, "won", big_blind=10)
        assert result["level"] == "climactic"

    def test_climactic_big_pot_showdown(self, generator):
        """Big pot + showdown → climactic level."""
        hand = make_hand(was_showdown=True, pot_size=250, actions=[make_action()])
        result = generator._analyze_hand_drama(hand, "won", big_blind=10)  # 250/10 = 25 BB
        assert result["level"] == "climactic"

    def test_big_pot_threshold(self, generator):
        """Pot must be >= 20 BB to count as big_pot."""
        # 190 chips / 10 BB = 19 BB (not big)
        hand = make_hand(was_showdown=True, pot_size=190, actions=[make_action()])
        result = generator._analyze_hand_drama(hand, "won", big_blind=10)
        assert result["level"] == "notable"  # Only showdown factor

        # 200 chips / 10 BB = 20 BB (big)
        hand = make_hand(was_showdown=True, pot_size=200, actions=[make_action()])
        result = generator._analyze_hand_drama(hand, "won", big_blind=10)
        assert result["level"] == "climactic"  # big_pot + showdown

    def test_big_blind_none_skips_big_pot(self, generator):
        """When big_blind is None, big_pot detection is skipped."""
        hand = make_hand(was_showdown=True, pot_size=10000, actions=[make_action()])
        result = generator._analyze_hand_drama(hand, "won", big_blind=None)
        # Only showdown factor (no big_pot since we can't calculate)
        assert result["level"] == "notable"

    # --- Tone Tests ---

    def test_tone_triumphant_climactic_win(self, generator):
        """Climactic + won → triumphant tone."""
        hand = make_hand(actions=[make_action(action="all_in")])
        result = generator._analyze_hand_drama(hand, "won", big_blind=10)
        assert result["tone"] == "triumphant"

    def test_tone_desperate_high_stakes_loss(self, generator):
        """High stakes + lost → desperate tone."""
        hand = make_hand(
            was_showdown=True,
            actions=[make_action("Alice"), make_action("Bob")],
        )
        result = generator._analyze_hand_drama(hand, "lost", big_blind=10)
        assert result["tone"] == "desperate"

    def test_tone_confident_notable_win(self, generator):
        """Notable + won → confident tone."""
        hand = make_hand(was_showdown=True, actions=[make_action()])
        result = generator._analyze_hand_drama(hand, "won", big_blind=10)
        assert result["tone"] == "confident"

    def test_tone_neutral_routine(self, generator):
        """Routine hand → neutral tone."""
        hand = make_hand(actions=[make_action(action="call")])
        result = generator._analyze_hand_drama(hand, "won", big_blind=10)
        assert result["tone"] == "neutral"


class TestFormatBeatsForChat:
    """Tests for _format_beats_for_chat static method."""

    def test_list_of_beats(self):
        """List of beats joins with newlines."""
        result = CommentaryGenerator._format_beats_for_chat(["*leans back*", "Nice hand."])
        assert result == "*leans back*\nNice hand."

    def test_list_filters_empty_strings(self):
        """Empty strings in list are filtered out."""
        result = CommentaryGenerator._format_beats_for_chat(["*nods*", "", "  ", "Good game."])
        assert result == "*nods*\nGood game."

    def test_list_strips_whitespace(self):
        """Whitespace around beats is stripped."""
        result = CommentaryGenerator._format_beats_for_chat(["  *smiles*  ", "  Thanks.  "])
        assert result == "*smiles*\nThanks."

    def test_empty_list_returns_none(self):
        """Empty list returns None."""
        assert CommentaryGenerator._format_beats_for_chat([]) is None

    def test_list_all_empty_returns_none(self):
        """List of only empty strings returns None."""
        assert CommentaryGenerator._format_beats_for_chat(["", "  ", ""]) is None

    def test_string_input(self):
        """Plain string passes through stripped."""
        result = CommentaryGenerator._format_beats_for_chat("  *waves*  ")
        assert result == "*waves*"

    def test_empty_string_returns_none(self):
        """Empty string returns None."""
        assert CommentaryGenerator._format_beats_for_chat("") is None

    def test_whitespace_string_returns_none(self):
        """Whitespace-only string returns None."""
        assert CommentaryGenerator._format_beats_for_chat("   ") is None

    def test_none_input(self):
        """None input returns None."""
        assert CommentaryGenerator._format_beats_for_chat(None) is None

    def test_non_string_in_list_filtered(self):
        """Non-string items in list are filtered out."""
        result = CommentaryGenerator._format_beats_for_chat(["*nods*", 123, None, "OK."])
        assert result == "*nods*\nOK."
