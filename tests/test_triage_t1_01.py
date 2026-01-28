"""Test T1-01: Verify poker_action.py dead code was removed."""
import importlib
import os


def test_poker_action_file_deleted():
    """The dead code file poker/poker_action.py should not exist."""
    poker_dir = os.path.dirname(importlib.import_module("poker").__file__)
    assert not os.path.exists(os.path.join(poker_dir, "poker_action.py"))


def test_poker_package_imports_without_poker_action():
    """The poker package should import cleanly without poker_action."""
    import poker
    # PokerAction and PlayerAction should not be in the package
    assert not hasattr(poker, "PokerAction")
    assert not hasattr(poker, "PlayerAction")
    # Core classes should still be importable
    assert hasattr(poker, "PokerGameState")
    assert hasattr(poker, "Player")
    assert hasattr(poker, "PokerPlayer")


def test_poker_player_options_type_annotation():
    """PokerPlayer.options should work with plain strings (no PlayerAction enum)."""
    from poker import PokerPlayer
    player = PokerPlayer(name="Test")
    player.options = ["fold", "check", "call"]
    assert player.options == ["fold", "check", "call"]
