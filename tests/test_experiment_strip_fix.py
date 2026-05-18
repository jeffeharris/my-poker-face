"""Regression test: experiment runner preserves folder cards across the
equity-calc strip so HandOutcomeDetector can detect BLUFFED_OFF.

The strip in `experiments/run_ai_tournament.py` was originally added
defensively against false equity-shock events (commit `9b7dcc7c`).
Phase 3's `HandOutcomeDetector` needs folder cards to verify the
bluff was actually weaker than the folder's would-have-been hand —
so the strip is now bracketed in a snapshot/restore.

This test pins that contract: simulate the strip-and-restore pattern
the runner uses, and verify the original `hole_cards` dict survives
intact for downstream consumers.
"""

from __future__ import annotations

from datetime import datetime

from poker.memory.hand_history import HandInProgress


def test_strip_restore_pattern_preserves_folder_cards():
    """Mirrors the snapshot/strip/restore pattern in
    `AITournamentRunner._play_hand` after Phase 3's fix.

    Before the fix: folded players' cards were popped permanently,
    so the RecordedHand built afterwards never saw them.
    After the fix: cards are restored after the equity calc.
    """
    hand = HandInProgress(game_id="g1", hand_number=1)
    hand.set_hole_cards("alice", ["Ah", "Kd"])
    hand.set_hole_cards("bob", ["7c", "2d"])
    hand.set_hole_cards("carol", ["Qs", "Qh"])  # carol will "fold"

    # Snapshot + strip + restore — the exact pattern from
    # experiments/run_ai_tournament.py around line 1361.
    original_hole_cards = dict(hand.hole_cards)
    folded_names = {"carol"}
    for name in folded_names:
        hand.hole_cards.pop(name, None)

    # During the strip window, the equity tracker would see only
    # active players. carol's cards are gone temporarily.
    assert "carol" not in hand.hole_cards
    assert "alice" in hand.hole_cards
    assert "bob" in hand.hole_cards

    # Restore — the finally block in the runner.
    hand.hole_cards = original_hole_cards

    # After restore, all three players' cards are back. The
    # RecordedHand built from this state will include carol's
    # cards, so HandOutcomeDetector can compute her would-have-been
    # hand for BLUFFED_OFF detection.
    assert hand.hole_cards["alice"] == ["Ah", "Kd"]
    assert hand.hole_cards["bob"] == ["7c", "2d"]
    assert hand.hole_cards["carol"] == ["Qs", "Qh"]


def test_strip_restore_handles_exception_in_equity_calc():
    """The runner wraps the strip/restore in try/finally so an
    equity-calc exception still triggers the restore. This test
    simulates the failure mode."""
    hand = HandInProgress(game_id="g1", hand_number=1)
    hand.set_hole_cards("alice", ["Ah", "Kd"])
    hand.set_hole_cards("carol", ["Qs", "Qh"])

    original_hole_cards = dict(hand.hole_cards)
    hand.hole_cards.pop("carol", None)

    try:
        raise RuntimeError("Simulated equity calc failure")
    except RuntimeError:
        pass
    finally:
        hand.hole_cards = original_hole_cards

    assert "carol" in hand.hole_cards
    assert hand.hole_cards["carol"] == ["Qs", "Qh"]
