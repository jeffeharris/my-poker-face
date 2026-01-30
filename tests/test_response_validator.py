"""Tests for response_validator, focusing on dramatic_sequence normalization."""
from poker.response_validator import normalize_dramatic_sequence, ResponseValidator


class TestNormalizeDramaticSequence:
    """Tests for splitting mixed action/speech beats."""

    def test_pure_action_unchanged(self):
        assert normalize_dramatic_sequence(["*leans forward*"]) == ["*leans forward*"]

    def test_pure_speech_unchanged(self):
        assert normalize_dramatic_sequence(["I'm going all in!"]) == ["I'm going all in!"]

    def test_mixed_action_then_speech(self):
        result = normalize_dramatic_sequence(["*leans forward* I'm going all in!"])
        assert result == ["*leans forward*", "I'm going all in!"]

    def test_mixed_speech_then_action(self):
        result = normalize_dramatic_sequence(["You're done! *slams table*"])
        assert result == ["You're done!", "*slams table*"]

    def test_multiple_actions_in_one_beat(self):
        result = normalize_dramatic_sequence(["*leans forward* *pushes chips*"])
        assert result == ["*leans forward*", "*pushes chips*"]

    def test_action_speech_action(self):
        result = normalize_dramatic_sequence(["*narrows eyes* Think you can bluff me? *pushes chips forward*"])
        assert result == ["*narrows eyes*", "Think you can bluff me?", "*pushes chips forward*"]

    def test_multiple_beats_some_mixed(self):
        beats = [
            "*narrows eyes*",
            "*leans forward* I'm all in!",
            "Good luck.",
        ]
        result = normalize_dramatic_sequence(beats)
        assert result == [
            "*narrows eyes*",
            "*leans forward*",
            "I'm all in!",
            "Good luck.",
        ]

    def test_empty_list(self):
        assert normalize_dramatic_sequence([]) == []

    def test_empty_and_whitespace_beats_filtered(self):
        assert normalize_dramatic_sequence(["", "  ", "hello"]) == ["hello"]

    def test_non_string_beats_filtered(self):
        assert normalize_dramatic_sequence([42, None, "*waves*"]) == ["*waves*"]

    def test_single_string_wrapped_in_list(self):
        result = normalize_dramatic_sequence(["*grins* Let's do this"])
        assert result == ["*grins*", "Let's do this"]

    def test_markdown_bold_action(self):
        """**leans forward** should normalize to *leans forward* without orphaned asterisks."""
        result = normalize_dramatic_sequence(["**leans forward**"])
        assert result == ["*leans forward*"]

    def test_markdown_bold_mixed_with_speech(self):
        result = normalize_dramatic_sequence(["**narrows eyes** You're bluffing."])
        assert result == ["*narrows eyes*", "You're bluffing."]

    def test_orphaned_asterisk_only(self):
        assert normalize_dramatic_sequence(["*"]) == []

    def test_trailing_comma_stripped(self):
        assert normalize_dramatic_sequence(["*leans forward*,"]) == ["*leans forward*"]

    def test_trailing_comma_newline_stripped(self):
        assert normalize_dramatic_sequence(["I'm all in!,\n"]) == ["I'm all in!"]

    def test_leading_comma_stripped(self):
        assert normalize_dramatic_sequence([",*waves*"]) == ["*waves*"]

    def test_semicolon_stripped(self):
        assert normalize_dramatic_sequence(["Let's go;"]) == ["Let's go"]

    def test_artifacts_in_mixed_beat(self):
        result = normalize_dramatic_sequence(["*grins*, Your move,"])
        assert result == ["*grins*", "Your move"]

    def test_only_artifacts_filtered(self):
        assert normalize_dramatic_sequence([",", ";\n", "  ,  "]) == []

    def test_code_comment_wrapped_action(self):
        """/* *action* */ should unwrap to just the action."""
        result = normalize_dramatic_sequence(["/* *staring down the table with a cold gaze* */"])
        assert result == ["*staring down the table with a cold gaze*"]


class TestCleanResponseNormalization:
    """Tests that clean_response integrates normalization."""

    def test_clean_response_normalizes_list(self):
        validator = ResponseValidator()
        response = {
            "action": "call",
            "inner_monologue": "thinking",
            "dramatic_sequence": ["*leans forward* I call!"],
        }
        cleaned = validator.clean_response(response)
        assert cleaned["dramatic_sequence"] == ["*leans forward*", "I call!"]

    def test_clean_response_normalizes_string(self):
        validator = ResponseValidator()
        response = {
            "action": "call",
            "inner_monologue": "thinking",
            "dramatic_sequence": "*grins* Your move.",
        }
        cleaned = validator.clean_response(response)
        assert cleaned["dramatic_sequence"] == ["*grins*", "Your move."]

    def test_clean_response_removes_for_quiet_player(self):
        validator = ResponseValidator()
        response = {
            "action": "call",
            "inner_monologue": "thinking",
            "dramatic_sequence": ["*waves*"],
        }
        cleaned = validator.clean_response(response, context={"should_speak": False})
        assert "dramatic_sequence" not in cleaned

    def test_clean_response_without_dramatic_sequence(self):
        validator = ResponseValidator()
        response = {"action": "fold", "inner_monologue": "meh"}
        cleaned = validator.clean_response(response)
        assert "dramatic_sequence" not in cleaned
