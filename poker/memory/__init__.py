"""
AI Memory and Learning System for Poker Players.

This module provides memory and learning capabilities for AI poker players:
- Hand history recording and persistence
- Session memory (context across hands within a game)
- Opponent modeling (learning player tendencies)
- End-of-hand commentary generation
- Memory manager orchestration
"""

from .commentary_generator import CommentaryGenerator, DecisionPlan, HandCommentary
from .hand_history import HandHistoryRecorder, RecordedAction, RecordedHand
from .memory_manager import AIMemoryManager
from .opponent_model import OpponentModel, OpponentModelManager, OpponentTendencies
from .session_memory import HandMemory, SessionMemory

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
    # Commentary & Decision Plans
    'DecisionPlan',
    'HandCommentary',
    'CommentaryGenerator',
    # Manager
    'AIMemoryManager',
]
