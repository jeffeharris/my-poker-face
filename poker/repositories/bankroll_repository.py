"""Repository for ai_bankroll_state, player_bankroll_state, and personality bankroll knobs.

Three persistence surfaces, all introduced in schema v88:

  - `ai_bankroll_state`: per-personality persistent bankroll, keyed on
    personality_id. Stored `chips` is the snapshot at
    `last_regen_tick`; the `load_*_projected` reads return the live
    value via `cash_mode.project_bankroll`.

  - `player_bankroll_state`: per-player persistent bankroll, keyed on
    player_id. No regen in v1 — fresh-grant on full bust is the only
    write that resets `chips` to `starting_bankroll`.

  - Personality bankroll knobs (`bankroll_cap`, `bankroll_rate`,
    `buy_in_multiplier`, `stop_loss_buy_ins`, `stop_win_buy_ins`,
    `stake_comfort_zone`) live as columns on `personalities`.
    Reads fall back to `BANKROLL_KNOB_DEFAULTS` when columns are
    NULL, so personalities seeded before per-row tuning land at sane
    defaults without a re-migration.

Spec: `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 2.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from cash_mode.bankroll import (
    AIBankrollState,
    BANKROLL_KNOB_DEFAULTS,
    BankrollKnobs,
    PlayerBankrollState,
    project_bankroll,
)
from poker.repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)


def _parse_timestamp(value) -> Optional[datetime]:
    """SQLite returns timestamps as strings; coerce to datetime.

    Returns None for NULL (the no-event-yet state — `project_bankroll`
    handles None by returning stored chips verbatim).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


class BankrollRepository(BaseRepository):
    """CRUD for ai_bankroll_state, player_bankroll_state, and personality bankroll knobs.

    Tables are created by `SchemaManager.ensure_schema()`; this class
    only touches data. Callers go through this rather than emitting
    raw SQL so the projection-on-read invariant for AI bankrolls
    stays enforced at one location.
    """

    # --- AI bankroll ---

    def save_ai_bankroll(self, state: AIBankrollState) -> None:
        """Upsert the AI bankroll row.

        Writes the stored `chips` snapshot verbatim — callers writing
        a post-event value must have already projected through
        elapsed time and reset `last_regen_tick = now` on the state
        before calling this. The repo doesn't project on write; that
        would be a hidden mutation surface.
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO ai_bankroll_state
                    (personality_id, chips, last_regen_tick)
                VALUES (?, ?, ?)
                """,
                (
                    state.personality_id,
                    state.chips,
                    state.last_regen_tick.isoformat() if state.last_regen_tick else None,
                ),
            )

    def load_ai_bankroll(self, personality_id: str) -> Optional[AIBankrollState]:
        """Load the raw stored snapshot — no projection applied.

        Returns the AIBankrollState exactly as persisted (`chips` is
        the snapshot at `last_regen_tick`). Callers wanting the live
        projected value should use `load_ai_bankroll_projected`.

        Returns None when no row exists; treat that as "AI has never
        sat down at a cash table" — the caller decides whether to
        seed a row or refuse seating.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT chips, last_regen_tick
                FROM ai_bankroll_state
                WHERE personality_id = ?
                """,
                (personality_id,),
            ).fetchone()
            if not row:
                return None
            return AIBankrollState(
                personality_id=personality_id,
                chips=row["chips"],
                last_regen_tick=_parse_timestamp(row["last_regen_tick"]),
            )

    def load_ai_bankroll_projected(
        self,
        personality_id: str,
        *,
        now: Optional[datetime] = None,
    ) -> Optional[int]:
        """Load the projected live bankroll chip count.

        Reads the stored snapshot, looks up the per-personality knob
        columns (with default fallback), and returns
        `project_bankroll(state, cap, rate, now)`. Returns None when
        the personality has no bankroll row yet.

        `now` defaults to `datetime.utcnow()`; explicit `now` lets
        callers pin the projection point for replay/test stability
        (mirrors the relationship repo's pattern).
        """
        state = self.load_ai_bankroll(personality_id)
        if state is None:
            return None
        knobs = self.load_personality_knobs(personality_id)
        if now is None:
            now = datetime.utcnow()
        return project_bankroll(state, knobs.bankroll_cap, knobs.bankroll_rate, now)

    # --- Player bankroll ---

    def save_player_bankroll(self, state: PlayerBankrollState) -> None:
        """Upsert the player bankroll row."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO player_bankroll_state
                    (player_id, chips, starting_bankroll)
                VALUES (?, ?, ?)
                """,
                (state.player_id, state.chips, state.starting_bankroll),
            )

    def load_player_bankroll(self, player_id: str) -> Optional[PlayerBankrollState]:
        """Load the player bankroll row.

        Returns None when no row exists; the caller decides whether
        to grant a starting bankroll (first-time entry into cash
        mode) or refuse the operation.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT chips, starting_bankroll
                FROM player_bankroll_state
                WHERE player_id = ?
                """,
                (player_id,),
            ).fetchone()
            if not row:
                return None
            return PlayerBankrollState(
                player_id=player_id,
                chips=row["chips"],
                starting_bankroll=row["starting_bankroll"],
            )

    # --- Personality bankroll knobs ---

    def load_personality_knobs(self, personality_id: str) -> BankrollKnobs:
        """Read the six bankroll knob columns for one personality.

        Columns are nullable; NULLs fall back to
        `BANKROLL_KNOB_DEFAULTS` per-field. A personality with no
        row at all (unknown personality_id) also returns the full
        defaults — the alternative would force every cash-mode call
        site to handle "no knobs" specially, when defaults are
        already the right answer.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT bankroll_cap, bankroll_rate, buy_in_multiplier,
                       stop_loss_buy_ins, stop_win_buy_ins, stake_comfort_zone
                FROM personalities
                WHERE personality_id = ?
                """,
                (personality_id,),
            ).fetchone()
        if not row:
            return BANKROLL_KNOB_DEFAULTS
        defaults = BANKROLL_KNOB_DEFAULTS
        return BankrollKnobs(
            bankroll_cap=(
                row["bankroll_cap"] if row["bankroll_cap"] is not None
                else defaults.bankroll_cap
            ),
            bankroll_rate=(
                row["bankroll_rate"] if row["bankroll_rate"] is not None
                else defaults.bankroll_rate
            ),
            buy_in_multiplier=(
                row["buy_in_multiplier"] if row["buy_in_multiplier"] is not None
                else defaults.buy_in_multiplier
            ),
            stop_loss_buy_ins=(
                row["stop_loss_buy_ins"] if row["stop_loss_buy_ins"] is not None
                else defaults.stop_loss_buy_ins
            ),
            stop_win_buy_ins=(
                row["stop_win_buy_ins"] if row["stop_win_buy_ins"] is not None
                else defaults.stop_win_buy_ins
            ),
            stake_comfort_zone=(
                row["stake_comfort_zone"] if row["stake_comfort_zone"] is not None
                else defaults.stake_comfort_zone
            ),
        )

    def save_personality_knobs(
        self,
        personality_id: str,
        knobs: BankrollKnobs,
    ) -> bool:
        """Write the six bankroll knob columns for one personality.

        Returns True if a row was updated, False if no row matches the
        personality_id. Used by the personality-seed bridge when JSON
        carries explicit per-personality knobs; v1 doesn't call this
        on the gameplay path.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE personalities
                SET bankroll_cap = ?,
                    bankroll_rate = ?,
                    buy_in_multiplier = ?,
                    stop_loss_buy_ins = ?,
                    stop_win_buy_ins = ?,
                    stake_comfort_zone = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE personality_id = ?
                """,
                (
                    knobs.bankroll_cap,
                    knobs.bankroll_rate,
                    knobs.buy_in_multiplier,
                    knobs.stop_loss_buy_ins,
                    knobs.stop_win_buy_ins,
                    knobs.stake_comfort_zone,
                    personality_id,
                ),
            )
            return cursor.rowcount > 0
