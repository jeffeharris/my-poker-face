"""Repository for ai_bankroll_state, player_bankroll_state, and personality bankroll knobs.

Three persistence surfaces, all introduced in schema v88:

  - `ai_bankroll_state`: per-personality persistent bankroll, keyed on
    personality_id. Stored `chips` is the snapshot at
    `last_regen_tick`; the `load_ai_bankroll_current` read returns the
    live value via `cash_mode.project_bankroll`.

  - `player_bankroll_state`: per-player persistent bankroll, keyed on
    player_id. No regen in v1 — fresh-grant on full bust is the only
    write that resets `chips` to `starting_bankroll`.

  - Personality bankroll knobs (`bankroll_cap`, `bankroll_rate`,
    `buy_in_multiplier`, `stop_loss_buy_ins`, `stop_win_buy_ins`,
    `stake_comfort_zone`) live inside the existing `config_json`
    column as a `bankroll_knobs` sub-dict. Same nesting convention
    as `anchors`. Reads fall back to `BANKROLL_KNOB_DEFAULTS`
    per-field when the sub-dict (or individual keys) is absent, so
    personalities without explicit JSON knobs land at sane defaults.

Spec: `docs/plans/CASH_MODE_AND_RELATIONSHIPS.md` Part 2.
"""

from __future__ import annotations

import json
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
        projected value should use `load_ai_bankroll_current`.

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

    def load_ai_bankroll_current(
        self,
        personality_id: str,
        *,
        now: Optional[datetime] = None,
    ) -> Optional[int]:
        """Load the current live bankroll chip count (projection applied).

        "Current" means "what the bankroll is right now," computed by
        projecting the stored snapshot through elapsed time via
        `project_bankroll`. Pair name with `load_ai_bankroll` (raw
        snapshot, no projection — admin/analytics only).

        Reads the stored snapshot, looks up the per-personality knob
        sub-dict (with default fallback), and returns
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
        """Upsert the player bankroll row.

        Writes the full state including v89 loan fields and the v90
        `active_loan_lender_id`. Callers clearing a settled loan must
        set `active_loan_amount=0`, `active_loan_floor=0.0`,
        `active_loan_rate=0.0`, `active_loan_lender_id=None` on the
        state before saving — no implicit reset here.
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO player_bankroll_state
                    (player_id, chips, starting_bankroll,
                     active_loan_amount, active_loan_floor, active_loan_rate,
                     active_loan_lender_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.player_id,
                    state.chips,
                    state.starting_bankroll,
                    state.active_loan_amount,
                    state.active_loan_floor,
                    state.active_loan_rate,
                    state.active_loan_lender_id,
                ),
            )

    def load_player_bankroll(self, player_id: str) -> Optional[PlayerBankrollState]:
        """Load the player bankroll row.

        Returns None when no row exists; the caller decides whether
        to grant a starting bankroll (first-time entry into cash
        mode) or refuse the operation.

        Legacy pre-v89 rows return with loan fields at their column
        defaults (0/0.0/0.0) — i.e., "no active loan." Legacy pre-v90
        rows return with `active_loan_lender_id=None` (anonymous house
        loan, matching v1 sponsorship semantics).
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT chips, starting_bankroll,
                       active_loan_amount, active_loan_floor, active_loan_rate,
                       active_loan_lender_id
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
                active_loan_amount=row["active_loan_amount"],
                active_loan_floor=row["active_loan_floor"],
                active_loan_rate=row["active_loan_rate"],
                active_loan_lender_id=row["active_loan_lender_id"],
            )

    # --- Personality bankroll knobs ---

    def load_personality_knobs(self, personality_id: str) -> BankrollKnobs:
        """Read the bankroll knobs from `config_json.bankroll_knobs`.

        Knobs nest inside `config_json` as a `bankroll_knobs` sub-dict
        (same convention as `anchors`). Per-field fallback to
        `BANKROLL_KNOB_DEFAULTS` covers three cases:
          - personality_id not in the table (unknown personality)
          - config_json has no `bankroll_knobs` sub-dict (untuned)
          - sub-dict is partial (only some keys set)

        Returning defaults for unknown ids lets every cash-mode call
        site assume knobs are always available — defaults are the
        right answer for "no specific tuning."
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT config_json FROM personalities WHERE personality_id = ?",
                (personality_id,),
            ).fetchone()
        if not row:
            return BANKROLL_KNOB_DEFAULTS

        try:
            config = json.loads(row["config_json"])
        except (TypeError, ValueError):
            logger.warning(
                "Personality %r has malformed config_json; using bankroll knob defaults",
                personality_id,
            )
            return BANKROLL_KNOB_DEFAULTS

        sub = config.get("bankroll_knobs") or {}
        if not isinstance(sub, dict):
            logger.warning(
                "Personality %r has non-dict bankroll_knobs; using defaults",
                personality_id,
            )
            return BANKROLL_KNOB_DEFAULTS

        defaults = BANKROLL_KNOB_DEFAULTS
        return BankrollKnobs(
            bankroll_cap=sub.get("bankroll_cap", defaults.bankroll_cap),
            bankroll_rate=sub.get("bankroll_rate", defaults.bankroll_rate),
            buy_in_multiplier=sub.get("buy_in_multiplier", defaults.buy_in_multiplier),
            stop_loss_buy_ins=sub.get("stop_loss_buy_ins", defaults.stop_loss_buy_ins),
            stop_win_buy_ins=sub.get("stop_win_buy_ins", defaults.stop_win_buy_ins),
            stake_comfort_zone=sub.get("stake_comfort_zone", defaults.stake_comfort_zone),
        )

    def save_personality_knobs(
        self,
        personality_id: str,
        knobs: BankrollKnobs,
    ) -> bool:
        """Merge knob values into `config_json.bankroll_knobs`.

        Reads the existing config, replaces the `bankroll_knobs`
        sub-dict, writes back. Preserves every other config key
        (anchors, verbal_tics, etc.) — this is a merge, not an
        overwrite of the whole row.

        Returns True if a row was updated, False if no row matches the
        personality_id. Used by personality-tuning tools / migrations;
        v1 doesn't call this on the gameplay path (knobs are read from
        the JSON seed source, not written from gameplay).
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT config_json FROM personalities WHERE personality_id = ?",
                (personality_id,),
            ).fetchone()
            if not row:
                return False
            try:
                config = json.loads(row["config_json"])
            except (TypeError, ValueError):
                logger.warning(
                    "Personality %r config_json is malformed; refusing to merge knobs",
                    personality_id,
                )
                return False
            config["bankroll_knobs"] = {
                "bankroll_cap": knobs.bankroll_cap,
                "bankroll_rate": knobs.bankroll_rate,
                "buy_in_multiplier": knobs.buy_in_multiplier,
                "stop_loss_buy_ins": knobs.stop_loss_buy_ins,
                "stop_win_buy_ins": knobs.stop_win_buy_ins,
                "stake_comfort_zone": knobs.stake_comfort_zone,
            }
            cursor = conn.execute(
                """
                UPDATE personalities
                SET config_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE personality_id = ?
                """,
                (json.dumps(config), personality_id),
            )
            return cursor.rowcount > 0
