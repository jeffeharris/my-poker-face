"""Usage tracking for LLM operations."""
import logging
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from .response import LLMResponse, ImageResponse

logger = logging.getLogger(__name__)


class CallType(str, Enum):
    """Validated call types for usage tracking."""
    UNKNOWN = "unknown"  # Default when call_type not specified
    PLAYER_DECISION = "player_decision"
    COMMENTARY = "commentary"
    CHAT_SUGGESTION = "chat_suggestion"
    TARGETED_CHAT = "targeted_chat"
    PERSONALITY_GENERATION = "personality_generation"
    PERSONALITY_PREVIEW = "personality_preview"
    THEME_GENERATION = "theme_generation"
    IMAGE_GENERATION = "image_generation"
    IMAGE_DESCRIPTION = "image_description"
    CATEGORIZATION = "categorization"
    SPADES_DECISION = "spades_decision"


class UsageTracker:
    """Tracks and persists API usage for cost analysis."""

    _instance: Optional["UsageTracker"] = None

    def __init__(self, db_path: Optional[str] = None):
        """Initialize usage tracker.

        Args:
            db_path: Path to SQLite database. If None, uses default location.
        """
        if db_path is None:
            db_path = self._get_default_db_path()
        self.db_path = db_path
        self._ensure_table()

    @classmethod
    def get_default(cls) -> "UsageTracker":
        """Get or create the default singleton tracker."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def set_default(cls, tracker: "UsageTracker") -> None:
        """Set the default tracker (useful for testing)."""
        cls._instance = tracker

    def _get_default_db_path(self) -> str:
        """Get the default database path based on environment."""
        if Path('/app/data').exists():
            return '/app/data/poker_games.db'
        return str(Path(__file__).parent.parent.parent / 'poker_games.db')

    def _ensure_table(self) -> None:
        """Ensure the api_usage table exists."""
        # Table creation is handled by persistence.py migrations
        # This just verifies we can connect
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("SELECT 1")
        except Exception as e:
            logger.warning(f"Could not connect to database at {self.db_path}: {e}")

    def record(
        self,
        response: LLMResponse | ImageResponse,
        call_type: Optional[CallType] = None,
        game_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        player_name: Optional[str] = None,
        hand_number: Optional[int] = None,
        prompt_template: Optional[str] = None,
        fallback_used: bool = False,
    ) -> None:
        """Record API usage to database and log.

        Args:
            response: The LLM or Image response
            call_type: Type of call (validated enum)
            game_id: Associated game ID
            owner_id: User who owns this game/request
            player_name: AI player name if applicable
            hand_number: Hand number within game
            prompt_template: Name of prompt template used
            fallback_used: Whether fallback was triggered
        """
        # Always log (backwards compat with existing log analysis)
        self._log_stats(response, call_type)

        # Persist to database
        try:
            self._insert_usage(
                response=response,
                call_type=call_type,
                game_id=game_id,
                owner_id=owner_id,
                player_name=player_name,
                hand_number=hand_number,
                prompt_template=prompt_template,
                fallback_used=fallback_used,
            )
        except Exception as e:
            logger.error(f"Failed to persist usage data: {e}")

    def _log_stats(
        self,
        response: LLMResponse | ImageResponse,
        call_type: Optional[CallType],
    ) -> None:
        """Log usage stats in the existing format for backwards compat."""
        call_type_str = call_type.value if call_type else "unknown"

        if isinstance(response, LLMResponse):
            stats = (
                f"[AI_STATS] provider={response.provider} model={response.model} | "
                f"latency={response.latency_ms:.0f}ms | "
                f"tokens: in={response.input_tokens}, out={response.output_tokens}, "
                f"reasoning={response.reasoning_tokens} | "
                f"call_type={call_type_str} | status={response.status}"
            )
        else:
            stats = (
                f"[AI_STATS] provider={response.provider} model={response.model} | "
                f"latency={response.latency_ms:.0f}ms | "
                f"images={response.image_count} size={response.size} | "
                f"call_type={call_type_str} | status={response.status}"
            )

        if response.is_error:
            logger.error(stats)
        else:
            logger.info(stats)

    def _insert_usage(
        self,
        response: LLMResponse | ImageResponse,
        call_type: Optional[CallType],
        game_id: Optional[str],
        owner_id: Optional[str],
        player_name: Optional[str],
        hand_number: Optional[int],
        prompt_template: Optional[str],
        fallback_used: bool,
    ) -> None:
        """Insert usage record into database."""
        is_image = isinstance(response, ImageResponse)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO api_usage (
                    created_at, game_id, owner_id, player_name, hand_number,
                    call_type, prompt_template, provider, model,
                    input_tokens, output_tokens, cached_tokens, reasoning_tokens,
                    reasoning_effort, image_count, image_size, latency_ms, status,
                    finish_reason, error_code, fallback_used, request_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now(timezone.utc).isoformat(),
                game_id,
                owner_id,
                player_name,
                hand_number,
                (call_type or CallType.UNKNOWN).value,
                prompt_template,
                response.provider,
                response.model,
                0 if is_image else response.input_tokens,
                0 if is_image else response.output_tokens,
                0 if is_image else response.cached_tokens,
                0 if is_image else response.reasoning_tokens,
                None if is_image else getattr(response, 'reasoning_effort', None),
                response.image_count if is_image else 0,
                response.size if is_image else None,
                int(response.latency_ms),
                response.status,
                None if is_image else getattr(response, 'finish_reason', None),
                response.error_code,
                fallback_used,
                response.request_id,
            ))
