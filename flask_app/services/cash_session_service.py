"""In-memory cash-session store.

v1 cash mode has at most one `CashSession` per user at a time. The
session lives in process memory (no persistence — v2 may serialize
for crash recovery). Keyed on `owner_id` (Google OAuth user id) for
authed users; guests get the guest tracking id.

This service intentionally stays thin: it's a singleton store and a
factory for new sessions. Routes own the request lifecycle; this
module owns the in-memory bookkeeping.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime
from typing import Callable, Dict, Optional

from cash_mode import (
    CashSession,
    PlayerBankrollState,
    new_table,
)
from poker.memory.memory_manager import AIMemoryManager

logger = logging.getLogger(__name__)


DEFAULT_PLAYER_STARTING_BANKROLL = 5_000
"""Fresh-grant amount for new players. Spec doesn't pin a value;
5k chips is enough for a $10 table buy-in (400 chips min) plus
headroom for top-ups."""


class CashSessionStore:
    """Thread-safe singleton of active cash sessions, keyed on owner_id.

    v1 invariant: one session per owner at a time. Starting a new
    session while an old one is active replaces it (the spec says
    sessions terminate when the human leaves; UI-driven leave goes
    through `end_session` first).
    """

    def __init__(self):
        self._sessions: Dict[str, CashSession] = {}
        self._lock = threading.Lock()

    def get(self, owner_id: str) -> Optional[CashSession]:
        with self._lock:
            return self._sessions.get(owner_id)

    def has(self, owner_id: str) -> bool:
        return self.get(owner_id) is not None

    def put(self, owner_id: str, session: CashSession) -> None:
        with self._lock:
            self._sessions[owner_id] = session

    def end(self, owner_id: str) -> Optional[CashSession]:
        with self._lock:
            return self._sessions.pop(owner_id, None)

    def clear(self) -> None:
        """Test helper — clear all sessions."""
        with self._lock:
            self._sessions.clear()


# Module-level singleton — routes import this.
cash_session_store = CashSessionStore()


def create_cash_session(
    owner_id: str,
    *,
    stake_label: str,
    big_blind: int,
    bankroll_repo,
    relationship_repo,
    personality_repo,
    hand_history_repo,
    db_path: str,
    controller_factory: Callable,
    seat_count: int = 6,
    user_id: Optional[str] = None,
) -> CashSession:
    """Build a fresh `CashSession` for `owner_id`.

    Loads (or seeds) the player's bankroll from `BankrollRepository`.
    First-time players get `DEFAULT_PLAYER_STARTING_BANKROLL` chips.
    Constructs the per-session `AIMemoryManager` with cash_mode=True
    wiring.

    `controller_factory` produces controllers for AI seats; the
    production wiring uses the existing AI controller stack.

    Does NOT register the session in the store — caller is responsible
    via `cash_session_store.put(owner_id, session)`. Separating the
    construction from registration makes the test path cleaner (build
    + assert without polluting the store).
    """
    bankroll = bankroll_repo.load_player_bankroll(owner_id)
    if bankroll is None:
        # First-time entry to cash mode — seed at default starting bankroll
        bankroll = PlayerBankrollState(
            player_id=owner_id,
            chips=DEFAULT_PLAYER_STARTING_BANKROLL,
            starting_bankroll=DEFAULT_PLAYER_STARTING_BANKROLL,
        )
        bankroll_repo.save_player_bankroll(bankroll)
        logger.info(
            "cash_session: seeded fresh bankroll for owner_id=%r at %d chips",
            owner_id, DEFAULT_PLAYER_STARTING_BANKROLL,
        )

    table = new_table(
        table_id=f"cash-{uuid.uuid4().hex[:8]}",
        stake_label=stake_label,
        big_blind=big_blind,
        seat_count=seat_count,
    )

    # Each session gets a unique game_id so hand_history rows don't
    # collide across sessions for the same player.
    game_id = f"cash-{owner_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    memory_manager = AIMemoryManager(
        game_id=game_id,
        db_path=db_path,
        owner_id=owner_id,
        commentary_enabled=False,
    )
    memory_manager.set_hand_history_repo(hand_history_repo)

    return CashSession(
        table=table,
        player_bankroll=bankroll,
        bankroll_repo=bankroll_repo,
        relationship_repo=relationship_repo,
        personality_repo=personality_repo,
        memory_manager=memory_manager,
        controller_factory=controller_factory,
        game_id=game_id,
        big_blind=big_blind,
        user_id=user_id,
    )
