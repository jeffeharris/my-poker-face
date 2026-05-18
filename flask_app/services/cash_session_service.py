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

from cash_mode import PlayerBankrollState, new_table
from cash_mode.session import CashSession
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
    wire_socket_emitter: bool = True,
) -> CashSession:
    """Build a fresh `CashSession` for `owner_id`.

    Loads (or seeds) the player's bankroll from `BankrollRepository`.
    First-time players get `DEFAULT_PLAYER_STARTING_BANKROLL` chips.
    Constructs the per-session `AIMemoryManager` with cash_mode=True
    wiring.

    When `wire_socket_emitter=True` (production default), the
    session's `on_state_change` callback is wired to
    `update_and_emit_game_state(game_id)` so AI moves animate live to
    the player through SocketIO. The caller is responsible for
    calling `register_cash_session_with_game_service(session)` AFTER
    the player has sat (so the state machine has a meaningful initial
    game_state); the emitter is harmless in the meantime because the
    emit happens via game_state_service which won't find the game yet.

    Tests that don't want Flask-app coupling can pass
    `wire_socket_emitter=False`.

    Does NOT register the session in the cash_session_store — the
    caller (the cash route) is responsible via
    `cash_session_store.put(owner_id, session)`. Separating the
    construction from registration makes the test path cleaner.
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

    # Build the on_state_change callback if integrating with the
    # Flask SocketIO emitter. The callback closes over game_id; the
    # actual emit happens in update_and_emit_game_state which reads
    # the current state machine from game_state_service.
    on_state_change = None
    if wire_socket_emitter:
        from flask_app.handlers.game_handler import update_and_emit_game_state

        def on_state_change():
            update_and_emit_game_state(game_id)

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
        on_state_change=on_state_change,
    )


def register_cash_session_with_game_service(session: CashSession) -> None:
    """Insert the cash session into `game_state_service` keyed on game_id.

    The shape mirrors what tournament games put in: state_machine,
    ai_controllers, memory_manager, messages, plus a `cash_session`
    sentinel so the action route can recognize it.

    Idempotent — re-registering replaces the existing entry. The
    state_machine reference held here is the persistent one CashSession
    refreshes across hands (not a per-hand rebuild), so the entry
    stays valid for the session's lifetime.

    Call AFTER sit_player so the state machine has a meaningful
    initial game_state to emit. If session has no state_machine yet
    (no run_hand has fired), one is created here from the current
    table state so update_and_emit_game_state won't crash on None.

    Lazy import of game_state_service so tests that don't load the
    Flask app aren't forced to.
    """
    from flask_app.services import game_state_service

    if session._state_machine is None:
        session._build_state_machine()

    game_state_service.set_game(session.game_id, {
        "state_machine": session._state_machine,
        "ai_controllers": session.controllers,
        "memory_manager": session.memory_manager,
        "cash_session": session,
        "messages": [],
        "owner_id": session.player_bankroll.player_id,
    })


def unregister_cash_session_from_game_service(game_id: str) -> None:
    """Remove the cash session entry from game_state_service.

    Called when the session ends (leave / quit). Idempotent.
    """
    from flask_app.services import game_state_service
    game_state_service.delete_game(game_id)
