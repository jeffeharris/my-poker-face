"""Global / per-owner LLM spend kill-switch (PRH-2).

The *read* side — rolling-24h ``SUM(estimated_cost)`` — lives on
:class:`~core.llm.tracking.UsageTracker`. This module holds the configured
ceilings and the gate decision, enforced centrally in
:class:`~core.llm.client.LLMClient` before any provider dispatch.

Layering: ``core.llm`` must not import ``flask_app``, so the operator's
configured limits are *pushed in* at app startup via
:func:`configure_spend_limits` (see ``flask_app.create_app``). Until then the
gate is disabled (no ceiling enforced), which is also the safe default for any
non-Flask entry point (sims, scripts, experiments).

Degradation model (tiered, audited safe — see ``docs/PRH_1_2_IMPLEMENTATION.md``):
when a ceiling is exceeded, the call is blocked and the client returns a failed
``LLMResponse``/``ImageResponse`` (``status="error"``, empty content). Cosmetic
calls (avatars, chatter, commentary, narration) simply vanish; ``PLAYER_DECISION``
calls make the ``chaos``/``standard`` bots fall back to their deterministic
engine; the default ``sharp`` bot is LLM-free for decisions anyway. A blocked
response therefore never stalls a hand.
"""

import logging
import threading
from typing import Optional

from .tracking import CallType

logger = logging.getLogger(__name__)

# Cosmetic / background spend — pure flavor. A blocked response here is
# invisible to gameplay (no avatar, no table talk, no commentary).
_COSMETIC_CALL_TYPES = frozenset(
    {
        CallType.IMAGE_GENERATION,
        CallType.IMAGE_DESCRIPTION,
        CallType.COMMENTARY,
        CallType.CHAT_SUGGESTION,
        CallType.TARGETED_CHAT,
        CallType.POST_ROUND_CHAT,
        CallType.PERSONALITY_GENERATION,
        CallType.PERSONALITY_PREVIEW,
        CallType.THEME_GENERATION,
        CallType.NARRATION_CLEANUP,
        CallType.VICE_NARRATION,
        CallType.SIDE_HUSTLE_NARRATION,
        CallType.COACHING,
        CallType.CATEGORIZATION,
    }
)

# The single decision call type. Blocking it makes the LLM-decision bots
# (chaos/standard/lean) fall back to the deterministic engine — verified safe
# by the decision-path resilience audit.
_DECISION_CALL_TYPES = frozenset({CallType.PLAYER_DECISION})


def classify_shed(call_type: Optional[CallType]) -> str:
    """Label a call by how it degrades when shed (for logging / intent)."""
    if call_type in _COSMETIC_CALL_TYPES:
        return "cosmetic"
    if call_type in _DECISION_CALL_TYPES:
        return "decision"
    return "other"


class SpendGate:
    """Holds the configured ceilings and answers "is a new call over budget?".

    Thread-safe: limits are read/written under a lock, and the spend read is
    delegated to ``UsageTracker.get_recent_spend`` (its own 30s TTL cache keeps
    a DB read off the hot path, and it fails open to $0 on a DB error — so a DB
    hiccup reads as under-budget and the call proceeds rather than freezing).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._global_daily_budget_usd = 0.0
        self._per_owner_daily_budget_usd = 0.0

    def configure(
        self,
        global_daily_budget_usd: float,
        per_owner_daily_budget_usd: float,
    ) -> None:
        """Set the ceilings. Non-positive (or None) disables that layer."""
        with self._lock:
            self._global_daily_budget_usd = max(0.0, float(global_daily_budget_usd or 0.0))
            self._per_owner_daily_budget_usd = max(0.0, float(per_owner_daily_budget_usd or 0.0))

    @property
    def enabled(self) -> bool:
        """True if at least one ceiling is armed."""
        with self._lock:
            return self._global_daily_budget_usd > 0 or self._per_owner_daily_budget_usd > 0

    def over_budget_reason(self, owner_id: Optional[str], tracker) -> Optional[str]:
        """Return a human-readable reason if a new call would breach a ceiling.

        Returns None when the gate is disabled or spend is under every armed
        ceiling. The global ceiling is checked first (the cheaper, always-warm
        cache key), then the per-owner ceiling.
        """
        with self._lock:
            global_limit = self._global_daily_budget_usd
            owner_limit = self._per_owner_daily_budget_usd

        if global_limit <= 0 and owner_limit <= 0:
            return None

        if global_limit > 0:
            spend = tracker.get_recent_spend()
            if spend >= global_limit:
                return (
                    f"global daily LLM budget exceeded "
                    f"(${spend:.2f} spent in 24h >= ${global_limit:.2f} cap)"
                )

        if owner_limit > 0 and owner_id:
            spend = tracker.get_recent_spend(owner_id=owner_id)
            if spend >= owner_limit:
                return (
                    f"owner '{owner_id}' daily LLM budget exceeded "
                    f"(${spend:.2f} spent in 24h >= ${owner_limit:.2f} cap)"
                )

        return None


# Process-wide gate, configured once at app startup. Disabled until then.
_gate = SpendGate()


def configure_spend_limits(
    global_daily_budget_usd: float,
    per_owner_daily_budget_usd: float,
) -> None:
    """Arm (or update) the process-wide spend gate. Called from app startup."""
    _gate.configure(global_daily_budget_usd, per_owner_daily_budget_usd)


def get_spend_gate() -> SpendGate:
    """Return the process-wide spend gate."""
    return _gate
