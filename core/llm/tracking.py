"""Usage tracking for LLM operations."""

import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from .capture_config import should_capture_prompt
from .response import ImageResponse, LLMResponse

logger = logging.getLogger(__name__)

# Cache TTL in seconds (1 hour)
PRICING_CACHE_TTL = 3600

# Spend-reader cache TTL in seconds. Short, because this backs the per-call
# spend gate (PRH-2): we must not add a DB read to every LLM call on the hot
# decision path, but the running total may only lag reality by a few seconds.
SPEND_CACHE_TTL = 30

# Default rolling window for the daily spend ceiling.
SPEND_WINDOW_HOURS = 24

# Configurable database path for prompt captures
_capture_db_path: Optional[str] = None


def set_capture_db_path(path: str) -> None:
    """Configure the database path for prompt captures.

    Call this at startup to set a custom database path.
    If not set, defaults to LLM_CAPTURE_DB_PATH env var or auto-detection.
    """
    global _capture_db_path
    _capture_db_path = path


def get_capture_db_path() -> str:
    """Get the database path for prompt captures.

    Priority:
    1. Path set via set_capture_db_path()
    2. LLM_CAPTURE_DB_PATH environment variable
    3. Auto-detect based on /app/data existence
    """
    if _capture_db_path:
        return _capture_db_path

    env_path = os.environ.get('LLM_CAPTURE_DB_PATH')
    if env_path:
        return env_path

    # Auto-detect based on environment
    from poker.db_utils import get_default_db_path

    return get_default_db_path()


@dataclass
class PricingEntry:
    """A cached pricing entry."""

    id: int
    cost: float


class CallType(str, Enum):
    """Validated call types for usage tracking."""

    UNKNOWN = "unknown"  # Default when call_type not specified
    PLAYER_DECISION = "player_decision"
    COMMENTARY = "commentary"
    CHAT_SUGGESTION = "chat_suggestion"
    TARGETED_CHAT = "targeted_chat"
    POST_ROUND_CHAT = "post_round_chat"
    PERSONALITY_GENERATION = "personality_generation"
    PERSONALITY_PREVIEW = "personality_preview"
    THEME_GENERATION = "theme_generation"
    IMAGE_GENERATION = "image_generation"
    IMAGE_DESCRIPTION = "image_description"
    CATEGORIZATION = "categorization"
    NARRATION_CLEANUP = "narration_cleanup"
    JOURNEY_NARRATION = "journey_narration"  # Circuit-story session/arc narration (Assistant tier)
    VICE_NARRATION = "vice_narration"
    SIDE_HUSTLE_NARRATION = "side_hustle_narration"
    DEBUG_REPLAY = "debug_replay"
    DEBUG_INTERROGATE = "debug_interrogate"
    EXPERIMENT_DESIGN = "experiment_design"
    EXPERIMENT_ANALYSIS = "experiment_analysis"
    COACHING = "coaching"


class UsageTracker:
    """Tracks and persists API usage for cost analysis."""

    _instance: Optional["UsageTracker"] = None
    _instance_lock = threading.Lock()

    def __init__(self, db_path: Optional[str] = None):
        """Initialize usage tracker.

        Args:
            db_path: Path to SQLite database. If None, uses default location.
        """
        if db_path is None:
            db_path = self._get_default_db_path()
        self.db_path = db_path
        self._ensure_table()

        # Pricing cache: {(provider, model, unit): PricingEntry}
        self._pricing_cache: Dict[Tuple[str, str, str], PricingEntry] = {}
        self._cache_loaded_at: Optional[float] = None
        self._cache_lock = threading.Lock()

        # Spend cache (PRH-2): {(owner_id_or_None, window_hours): (computed_at_epoch, total_usd)}.
        # owner_id == None is the global (all-owners) running total. The window
        # is part of the key so two different windows can't alias each other.
        self._spend_cache: Dict[Tuple[Optional[str], int], Tuple[float, float]] = {}
        self._spend_cache_lock = threading.Lock()

    @classmethod
    def get_default(cls) -> "UsageTracker":
        """Get or create the default singleton tracker (thread-safe)."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def set_default(cls, tracker: "UsageTracker") -> None:
        """Set the default tracker (useful for testing)."""
        cls._instance = tracker

    def _get_default_db_path(self) -> str:
        """Get the default database path based on environment."""
        from poker.db_utils import get_default_db_path

        return get_default_db_path()

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
        message_count: Optional[int] = None,
        system_prompt_tokens: Optional[int] = None,
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
            message_count: Number of messages in conversation history
            system_prompt_tokens: Token count of system prompt (via tiktoken)
        """
        # Always log (backwards compat with existing log analysis)
        self._log_stats(response, call_type)

        # Persist to database
        try:
            estimated_cost = self._insert_usage(
                response=response,
                call_type=call_type,
                game_id=game_id,
                owner_id=owner_id,
                player_name=player_name,
                hand_number=hand_number,
                prompt_template=prompt_template,
                message_count=message_count,
                system_prompt_tokens=system_prompt_tokens,
            )
            # Keep the spend cache (PRH-2) honest without a DB re-read: fold this
            # call's cost into any warm cached totals so the budget gate sees it
            # immediately, instead of lagging up to SPEND_CACHE_TTL behind it.
            self._bump_spend_cache(owner_id, estimated_cost)
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

    def _refresh_pricing_cache(self, conn: sqlite3.Connection) -> None:
        """Load all current pricing into memory cache."""
        with self._cache_lock:
            try:
                cursor = conn.execute("""
                    SELECT id, provider, model, unit, cost FROM model_pricing
                    WHERE (valid_from IS NULL OR valid_from <= datetime('now'))
                      AND (valid_until IS NULL OR valid_until > datetime('now'))
                """)
                self._pricing_cache.clear()
                for row in cursor:
                    key = (row[1], row[2], row[3])  # (provider, model, unit)
                    self._pricing_cache[key] = PricingEntry(id=row[0], cost=row[4])
                self._cache_loaded_at = datetime.now(timezone.utc).timestamp()
                logger.debug(f"Pricing cache refreshed: {len(self._pricing_cache)} entries")
            except sqlite3.OperationalError:
                # Table doesn't exist yet
                pass

    def _ensure_cache_fresh(self, conn: sqlite3.Connection) -> None:
        """Ensure pricing cache is loaded and not stale."""
        now = datetime.now(timezone.utc).timestamp()
        if self._cache_loaded_at is None or now - self._cache_loaded_at > PRICING_CACHE_TTL:
            self._refresh_pricing_cache(conn)

    def invalidate_pricing_cache(self) -> None:
        """Force cache refresh on next lookup. Call after updating pricing."""
        with self._cache_lock:
            self._cache_loaded_at = None

    # ------------------------------------------------------------------
    # Spend reader (PRH-2 — the read side of the global/per-owner kill-switch)
    # ------------------------------------------------------------------
    def get_recent_spend(
        self,
        owner_id: Optional[str] = None,
        window_hours: int = SPEND_WINDOW_HOURS,
    ) -> float:
        """Return summed `estimated_cost` (USD) over a rolling window.

        Backs the spend gate, so it is cached in memory with a short TTL
        (`SPEND_CACHE_TTL`) to keep a DB read off the hot per-call path.

        Args:
            owner_id: If given, sum only rows for that owner; otherwise sum
                across all owners (the global total).
            window_hours: Rolling window size in hours (default 24h).

        Returns:
            Total estimated USD spend over the window. Rows with a NULL
            `estimated_cost` (missing pricing) count as $0. Fails open: on any
            DB error this returns 0.0 (and logs) so a spend backstop can never
            freeze the game over a DB hiccup.
        """
        cache_key = (owner_id, window_hours)
        now = datetime.now(timezone.utc).timestamp()
        with self._spend_cache_lock:
            cached = self._spend_cache.get(cache_key)
            if cached is not None and now - cached[0] < SPEND_CACHE_TTL:
                return cached[1]

        # Recompute outside the lock — the SUM query is the slow part and we
        # don't want to serialize concurrent callers behind it.
        total = self._query_recent_spend(owner_id, window_hours)

        with self._spend_cache_lock:
            self._spend_cache[cache_key] = (now, total)
        return total

    def _query_recent_spend(self, owner_id: Optional[str], window_hours: int) -> float:
        """Run the rolling-window SUM(estimated_cost) query. Fails open (0.0)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                if owner_id is None:
                    row = conn.execute(
                        "SELECT COALESCE(SUM(estimated_cost), 0) FROM api_usage "
                        "WHERE created_at >= ?",
                        (cutoff,),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT COALESCE(SUM(estimated_cost), 0) FROM api_usage "
                        "WHERE created_at >= ? AND owner_id = ?",
                        (cutoff, owner_id),
                    ).fetchone()
            return float(row[0]) if row and row[0] is not None else 0.0
        except Exception as e:
            # Fail open: a cost backstop must never freeze the game on a DB error.
            logger.error(f"Failed to read recent LLM spend (failing open, returning $0): {e}")
            return 0.0

    def invalidate_spend_cache(self) -> None:
        """Drop the cached spend totals so the next read recomputes immediately."""
        with self._spend_cache_lock:
            self._spend_cache.clear()

    def find_recent_null_cost_combos(
        self, window_hours: int = SPEND_WINDOW_HOURS
    ) -> List[Tuple[str, str, int]]:
        """Find (provider, model) pairs with recent api_usage rows missing a cost.

        Rows where ``estimated_cost IS NULL`` slip the budget cap silently
        (``COALESCE(SUM, 0)`` treats them as $0) — almost always because the
        ``model_pricing`` row is missing for that SKU. Surfaces them as a list
        of ``(provider, model, count)`` so a startup check can warn loudly.

        Fails open to an empty list on any DB error — this is observability,
        not enforcement.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT provider, model, COUNT(*) FROM api_usage "
                    "WHERE estimated_cost IS NULL AND created_at >= ? "
                    "GROUP BY provider, model "
                    "ORDER BY COUNT(*) DESC",
                    (cutoff,),
                ).fetchall()
            return [(row[0], row[1], int(row[2])) for row in rows]
        except Exception as e:
            logger.debug(f"Could not query NULL-cost api_usage rows: {e}")
            return []

    def prune_old_usage(self, retention_days: int) -> int:
        """Delete api_usage rows older than ``retention_days`` (PRH-32).

        0 or negative = keep everything (no-op). Compares against an ISO cutoff
        string, matching how ``created_at`` is written (UTC isoformat) and how
        ``find_recent_null_cost_combos`` reads it. Fails open (logs, returns 0)
        — this is housekeeping, never gameplay-critical.
        """
        if retention_days <= 0:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("DELETE FROM api_usage WHERE created_at < ?", (cutoff,))
                deleted = cursor.rowcount
            if deleted:
                logger.info(
                    "[RETENTION] purged %d api_usage row(s) older than %d days",
                    deleted,
                    retention_days,
                )
            return deleted
        except Exception as e:
            logger.warning("[RETENTION] api_usage purge failed: %s", e)
            return 0

    def _bump_spend_cache(self, owner_id: Optional[str], cost: Optional[float]) -> None:
        """Add ``cost`` to any warm cached spend totals this call counts toward.

        A just-recorded call falls inside every active rolling window, so we bump
        the global key (``None``) and the call's owner key for *all* window sizes.
        The cached entry's original timestamp is preserved, so the TTL still
        forces a full DB recompute within ``SPEND_CACHE_TTL`` — this bump is an
        eager correction between recomputes, not a replacement for them, so it
        cannot drift or double-count (the recompute reads the persisted row).
        Missing cache keys are left alone: the next read computes them fresh from
        the DB, which already includes this row.
        """
        if not cost or cost <= 0:
            return
        with self._spend_cache_lock:
            for (key_owner, window), (computed_at, total) in list(self._spend_cache.items()):
                if key_owner is None or key_owner == owner_id:
                    self._spend_cache[(key_owner, window)] = (computed_at, total + cost)

    def _get_sku_pricing(
        self,
        conn: sqlite3.Connection,
        provider: str,
        model: str,
        unit: str,
    ) -> Optional[PricingEntry]:
        """Look up pricing entry from cache.

        Args:
            conn: Database connection (used to refresh cache if needed)
            provider: Provider name (e.g., 'openai')
            model: Model name (e.g., 'gpt-4o')
            unit: The pricing unit (e.g., 'input_tokens_1m', 'image_1024x1024')

        Returns:
            PricingEntry with id and cost, or None if not found
        """
        self._ensure_cache_fresh(conn)
        return self._pricing_cache.get((provider, model, unit))

    @dataclass
    class CostResult:
        """Result of cost calculation with pricing IDs for audit trail."""

        cost: float
        pricing_ids: Dict[str, int] = field(default_factory=dict)

    def _calculate_cost(
        self,
        conn: sqlite3.Connection,
        response: LLMResponse | ImageResponse,
    ) -> Optional["UsageTracker.CostResult"]:
        """Calculate estimated cost for an API call.

        Args:
            conn: Database connection
            response: The LLM or Image response

        Returns:
            CostResult with cost and pricing IDs, or None if pricing not found
        """
        is_image = isinstance(response, ImageResponse)
        provider = response.provider
        model = response.model

        if is_image:
            # Prefer the provider-reported cost when the API returns one (e.g.
            # Runware with includeCost) — it's the actual billed amount, exact
            # for the model/size/steps, and avoids the SKU lookup missing on
            # sizes we never priced. SKU pricing is the fallback below.
            reported = getattr(response, 'cost', None)
            if reported is not None:
                return self.CostResult(cost=float(reported), pricing_ids={"provider_reported": 1})
            # Image pricing: look up image_<size> SKU
            size = response.size or '1024x1024'
            unit = f'image_{size}'
            pricing = self._get_sku_pricing(conn, provider, model, unit)
            if pricing is not None:
                return self.CostResult(
                    cost=response.image_count * pricing.cost, pricing_ids={"image": pricing.id}
                )
        else:
            # Text pricing: look up input, output, and optionally cached/reasoning SKUs
            input_pricing = self._get_sku_pricing(conn, provider, model, 'input_tokens_1m')
            output_pricing = self._get_sku_pricing(conn, provider, model, 'output_tokens_1m')

            if input_pricing is None or output_pricing is None:
                return None

            # Get cached pricing (fallback to half of input cost if not specified)
            cached_pricing = self._get_sku_pricing(conn, provider, model, 'cached_input_tokens_1m')
            cached_cost_per_m = cached_pricing.cost if cached_pricing else input_pricing.cost / 2

            # Get reasoning pricing (fallback to output rate if not specified)
            reasoning_pricing = self._get_sku_pricing(conn, provider, model, 'reasoning_tokens_1m')
            reasoning_cost_per_m = (
                reasoning_pricing.cost if reasoning_pricing else output_pricing.cost
            )

            # Calculate cost
            uncached_input = response.input_tokens - response.cached_tokens
            input_cost = uncached_input * input_pricing.cost / 1_000_000
            cached_cost = response.cached_tokens * cached_cost_per_m / 1_000_000
            output_cost = response.output_tokens * output_pricing.cost / 1_000_000
            reasoning_cost = response.reasoning_tokens * reasoning_cost_per_m / 1_000_000

            pricing_ids = {"input": input_pricing.id, "output": output_pricing.id}
            if cached_pricing:
                pricing_ids["cached"] = cached_pricing.id
            if reasoning_pricing:
                pricing_ids["reasoning"] = reasoning_pricing.id

            return self.CostResult(
                cost=input_cost + cached_cost + output_cost + reasoning_cost,
                pricing_ids=pricing_ids,
            )

        return None

    def _insert_usage(
        self,
        response: LLMResponse | ImageResponse,
        call_type: Optional[CallType],
        game_id: Optional[str],
        owner_id: Optional[str],
        player_name: Optional[str],
        hand_number: Optional[int],
        prompt_template: Optional[str],
        message_count: Optional[int],
        system_prompt_tokens: Optional[int],
    ) -> Optional[float]:
        """Insert usage record into database. Returns the estimated cost (USD)."""
        is_image = isinstance(response, ImageResponse)

        with sqlite3.connect(self.db_path) as conn:
            # Calculate cost using pricing table (returns CostResult with pricing IDs)
            cost_result = self._calculate_cost(conn, response)

            # Extract values from cost result
            estimated_cost = cost_result.cost if cost_result else None
            pricing_ids_json = json.dumps(cost_result.pricing_ids) if cost_result else None

            conn.execute(
                """
                INSERT INTO api_usage (
                    created_at, game_id, owner_id, player_name, hand_number,
                    call_type, prompt_template, provider, model,
                    input_tokens, output_tokens, cached_tokens, reasoning_tokens,
                    reasoning_effort, max_tokens, image_count, image_size, latency_ms, status,
                    finish_reason, error_code, error_message, request_id, message_count, system_prompt_tokens,
                    estimated_cost, pricing_ids
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
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
                    None if is_image else getattr(response, 'max_tokens', None),
                    response.image_count if is_image else 0,
                    response.size if is_image else None,
                    int(response.latency_ms),
                    response.status,
                    None if is_image else getattr(response, 'finish_reason', None),
                    getattr(response, 'error_code', None),
                    getattr(response, 'error_message', None),
                    getattr(response, 'request_id', None),
                    message_count,
                    system_prompt_tokens,
                    estimated_cost,
                    pricing_ids_json,
                ),
            )

        return estimated_cost


def capture_prompt(
    messages: List[Dict[str, str]],
    response: LLMResponse,
    call_type: CallType,
    game_id: Optional[str] = None,
    owner_id: Optional[str] = None,
    player_name: Optional[str] = None,
    hand_number: Optional[int] = None,
    debug_mode: bool = False,
    enricher: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    prompt_template: Optional[str] = None,
) -> bool:
    """Capture prompt and response to prompt_captures table.

    This is called after a successful LLM call to optionally store
    the full prompt/response for debugging and replay.

    Args:
        messages: The messages array sent to the LLM
        response: The LLM response
        call_type: Type of call (player_decision, commentary, etc.)
        game_id: Optional game ID (nullable for non-game calls)
        owner_id: Optional owner/user ID
        player_name: Optional player name
        hand_number: Optional hand number
        debug_mode: True if game has debug capture explicitly enabled
        enricher: Optional callback to add domain-specific fields (e.g., game state).
                  Receives capture dict, returns enriched dict.

    Returns:
        True if capture was saved, False if skipped or failed
    """
    # Check if we should capture this prompt
    if not should_capture_prompt(call_type, debug_mode):
        return False

    # Skip image responses
    if isinstance(response, ImageResponse):
        return False

    try:
        # Extract prompt components
        system_prompt = ""
        conversation_history = []
        user_message = ""

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                system_prompt = content
            elif role == "user":
                if user_message:
                    # Previous user message goes to history
                    conversation_history.append({"role": "user", "content": user_message})
                user_message = content
            elif role == "assistant":
                conversation_history.append({"role": "assistant", "content": content})

        # Build base capture data
        capture_data = {
            'game_id': game_id,
            'owner_id': owner_id,
            'player_name': player_name,
            'hand_number': hand_number,
            'phase': call_type.value,  # Default phase from call_type
            'call_type': call_type.value,
            'prompt_template': prompt_template,
            'system_prompt': system_prompt or "(no system prompt)",
            'user_message': user_message or "(no user message)",
            'ai_response': response.content or "",
            'conversation_history': conversation_history,
            'raw_api_response': response.raw_response,
            'provider': response.provider,
            'model': response.model,
            'reasoning_effort': getattr(response, 'reasoning_effort', None),
            'latency_ms': int(response.latency_ms),
            'input_tokens': response.input_tokens,
            'output_tokens': response.output_tokens,
            'original_request_id': getattr(response, 'request_id', None),
        }

        # Apply enricher callback if provided (adds game state, etc.)
        if enricher:
            try:
                capture_data = enricher(capture_data)
            except Exception as e:
                logger.warning(f"Capture enricher failed: {e}")

        db_path = get_capture_db_path()

        # Capture enricher-provided extra fields as JSON metadata
        _STANDARD_KEYS = {
            'game_id',
            'owner_id',
            'player_name',
            'hand_number',
            'phase',
            'call_type',
            'system_prompt',
            'user_message',
            'ai_response',
            'conversation_history',
            'raw_api_response',
            'provider',
            'model',
            'reasoning_effort',
            'latency_ms',
            'input_tokens',
            'output_tokens',
            'original_request_id',
            'pot_total',
            'cost_to_call',
            'pot_odds',
            'player_stack',
            'community_cards',
            'player_hand',
            'valid_actions',
            'action_taken',
            'raise_amount',
            'parent_id',
            'error_type',
            'error_description',
            'correction_attempt',
            'prompt_template',
        }
        extra = {
            k: v
            for k, v in capture_data.items()
            if k not in _STANDARD_KEYS and not k.startswith('_')
        }
        metadata_json = json.dumps(extra, default=str) if extra else None

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO prompt_captures (
                    game_id, owner_id, player_name, hand_number, phase, call_type,
                    system_prompt, user_message, ai_response,
                    conversation_history, raw_api_response,
                    provider, model, reasoning_effort,
                    latency_ms, input_tokens, output_tokens,
                    original_request_id,
                    pot_total, cost_to_call, pot_odds, player_stack,
                    community_cards, player_hand, valid_actions,
                    action_taken, raise_amount,
                    parent_id, error_type, error_description, correction_attempt,
                    prompt_template,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    capture_data.get('game_id'),
                    capture_data.get('owner_id'),
                    capture_data.get('player_name'),
                    capture_data.get('hand_number'),
                    capture_data.get('phase'),
                    capture_data.get('call_type'),
                    capture_data.get('system_prompt'),
                    capture_data.get('user_message'),
                    capture_data.get('ai_response'),
                    json.dumps(capture_data.get('conversation_history'))
                    if capture_data.get('conversation_history')
                    else None,
                    json.dumps(capture_data.get('raw_api_response'), default=str)
                    if capture_data.get('raw_api_response')
                    else None,
                    capture_data.get('provider'),
                    capture_data.get('model'),
                    capture_data.get('reasoning_effort'),
                    capture_data.get('latency_ms'),
                    capture_data.get('input_tokens'),
                    capture_data.get('output_tokens'),
                    capture_data.get('original_request_id'),
                    # Enriched fields (may be None for non-game captures)
                    capture_data.get('pot_total'),
                    capture_data.get('cost_to_call'),
                    capture_data.get('pot_odds'),
                    capture_data.get('player_stack'),
                    json.dumps(capture_data.get('community_cards'))
                    if capture_data.get('community_cards')
                    else None,
                    json.dumps(capture_data.get('player_hand'))
                    if capture_data.get('player_hand')
                    else None,
                    json.dumps(capture_data.get('valid_actions'))
                    if capture_data.get('valid_actions')
                    else None,
                    capture_data.get('action_taken'),
                    capture_data.get('raise_amount'),
                    # Resilience fields (for error recovery tracking)
                    capture_data.get('parent_id'),
                    capture_data.get('error_type'),
                    capture_data.get('error_description'),
                    capture_data.get('correction_attempt', 0),
                    capture_data.get('prompt_template'),
                    metadata_json,
                ),
            )

        capture_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Call on_captured callback if provided (allows caller to get capture_id without coupling)
        on_captured = capture_data.get('_on_captured')
        if callable(on_captured):
            try:
                on_captured(capture_id)
            except Exception as e:
                logger.warning(f"on_captured callback failed: {e}")

        logger.debug(f"Captured prompt {capture_id} for {call_type.value}: {response.model}")
        return capture_id

    except Exception as e:
        logger.error(f"Failed to capture prompt: {e}")
        return None


def update_prompt_capture(capture_id: int, **fields) -> bool:
    """Update a prompt capture with additional fields (e.g., action_taken after parsing).

    Args:
        capture_id: The ID of the capture to update
        **fields: Fields to update (action_taken, raise_amount, etc.)

    Returns:
        True if update succeeded, False otherwise
    """
    if not capture_id or not fields:
        return False

    try:
        db_path = get_capture_db_path()

        # Build UPDATE statement for provided fields
        allowed_fields = {
            'action_taken',
            'raise_amount',
            'parent_id',
            'error_type',
            'error_description',
            'correction_attempt',
        }
        update_fields = {k: v for k, v in fields.items() if k in allowed_fields}

        if not update_fields:
            return False

        set_clause = ", ".join(f"{k} = ?" for k in update_fields.keys())
        values = list(update_fields.values()) + [capture_id]

        with sqlite3.connect(db_path) as conn:
            conn.execute(f"UPDATE prompt_captures SET {set_clause} WHERE id = ?", values)

        return True
    except Exception as e:
        logger.error(f"Failed to update prompt capture {capture_id}: {e}")
        return False


def capture_image_prompt(
    prompt: str,
    response: ImageResponse,
    call_type: CallType,
    target_personality: Optional[str] = None,
    target_emotion: Optional[str] = None,
    reference_image_id: Optional[str] = None,
    game_id: Optional[str] = None,
    owner_id: Optional[str] = None,
) -> Optional[int]:
    """Capture image generation prompt and result to prompt_captures table.

    This downloads the image from the URL (before it expires) and stores
    it as a BLOB in the database for later viewing and replay.

    Args:
        prompt: The image generation prompt
        response: The ImageResponse from the provider
        call_type: Type of call (IMAGE_GENERATION, etc.)
        target_personality: Optional personality name (e.g., for avatar generation)
        target_emotion: Optional emotion (e.g., for avatar generation)
        reference_image_id: Optional reference image ID (for img2img)
        game_id: Optional game ID
        owner_id: Optional owner/user ID

    Returns:
        capture_id if capture was saved, None if skipped or failed
    """
    import requests

    # Check if we should capture this prompt
    if not should_capture_prompt(call_type, debug_mode=False):
        return None

    # Skip error responses
    if response.is_error:
        return None

    try:
        # Download image bytes from URL (before it expires)
        image_data = None
        image_width = None
        image_height = None

        if response.url:
            try:
                img_response = requests.get(response.url, timeout=30)
                img_response.raise_for_status()
                image_data = img_response.content

                # Try to get image dimensions
                try:
                    import io

                    from PIL import Image

                    img = Image.open(io.BytesIO(image_data))
                    image_width, image_height = img.size
                except ImportError:
                    # PIL not available, try to parse from size string
                    if response.size:
                        try:
                            w, h = response.size.split('x')
                            image_width, image_height = int(w), int(h)
                        except ValueError:
                            # Malformed size string; continue without dimensions
                            pass
                except Exception as e:
                    logger.debug(f"Could not get image dimensions: {e}")
            except Exception as e:
                logger.warning(f"Failed to download image for capture: {e}")
        db_path = get_capture_db_path()

        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO prompt_captures (
                    game_id, player_name, phase, call_type,
                    system_prompt, user_message, ai_response,
                    provider, model, latency_ms,
                    is_image_capture, image_prompt, image_url, image_data,
                    image_size, image_width, image_height,
                    target_personality, target_emotion, reference_image_id,
                    owner_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    game_id,
                    target_personality,  # Use as player_name for filtering
                    call_type.value,  # Use as phase for compatibility
                    call_type.value,
                    "(image generation)",  # system_prompt placeholder
                    prompt,  # user_message = the prompt
                    response.url or "",  # ai_response = the URL
                    response.provider,
                    response.model,
                    int(response.latency_ms) if response.latency_ms else None,
                    1,  # is_image_capture = True
                    prompt,
                    response.url,
                    image_data,
                    response.size,
                    image_width,
                    image_height,
                    target_personality,
                    target_emotion,
                    reference_image_id,
                    owner_id,
                ),
            )

            capture_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        logger.debug(f"Captured image prompt {capture_id} for {call_type.value}: {response.model}")
        return capture_id

    except Exception as e:
        logger.error(f"Failed to capture image prompt: {e}")
        return None
