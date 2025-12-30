"""
AI Memory and Learning System for Poker Players.

This module provides memory and learning capabilities for AI poker players:
- Hand history recording and persistence
- Session memory (context across hands within a game)
- Opponent modeling (learning player tendencies)
- End-of-hand commentary generation
- Memory manager orchestration
"""

from .hand_history import RecordedHand, RecordedAction, HandHistoryRecorder
from .session_memory import SessionMemory, HandMemory
from .opponent_model import OpponentTendencies, OpponentModel, OpponentModelManager
from .commentary_generator import HandCommentary, CommentaryGenerator
from .memory_manager import AIMemoryManager

__all__ = [
    # Hand history
    'RecordedHand',
    'RecordedAction',
    'HandHistoryRecorder',

    # Session memory
    'SessionMemory',
    'HandMemory',

    # Opponent modeling
    'OpponentTendencies',
    'OpponentModel',
    'OpponentModelManager',

    # Commentary
    'HandCommentary',
    'CommentaryGenerator',

    # Manager
    'AIMemoryManager',
]
