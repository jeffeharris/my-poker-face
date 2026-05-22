"""Repository for ai_bankroll_state, player_bankroll_state, and personality bankroll knobs.

Three persistence surfaces, all introduced in schema v88:

  - `ai_bankroll_state`: per-personality persistent bankroll, keyed on
    personality_id. Stored `chips` is the snapshot at
    `last_regen_tick`; the `load_ai_bankroll_current` read returns the
    live value via `cash_mode.project_bankroll`.

  - `player_bankroll_state`: per-player persistent bankroll, keyed on
    player_id. No regen in v1 and no auto-refill — busted players
    must use the staking system to play again.

  - Personality bankroll knobs (`starting_bankroll`, `bankroll_rate`,
    `buy_in_multiplier`, `stake_comfort_zone`) live inside the
    existing `config_json`
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
from typing import List, Optional

from cash_mode.bankroll import (
    AIBankrollState,
    BANKROLL_KNOB_DEFAULTS,
    BankrollKnobs,
    PlayerBankrollState,
    project_bankroll,
)
from cash_mode.staker_profile import (
    BORROWER_PROFILE_DEFAULTS,
    BorrowerProfile,
    STAKER_PROFILE_DEFAULTS,
    StakerProfile,
    compute_default_aspiration_bias,
    compute_default_payoff_eagerness,
    compute_default_willingness_threshold,
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
    #
    # Every method that reads or writes ai_bankroll_state takes
    # `sandbox_id` as a required kwarg (Phase 2.5 v102). The repo
    # never falls back to a default sandbox — silent fallbacks were
    # the bug class the per-sandbox handoff was designed to prevent.
    # Admin / audit paths that legitimately want cross-sandbox totals
    # pass `sandbox_id=None` to the methods that explicitly accept it
    # (`sum_ai_bankroll_chips_stored`, `iter_personality_ids_with_bankrolls`).

    def save_ai_bankroll(
        self,
        state: AIBankrollState,
        *,
        sandbox_id: str,
        chip_ledger_repo=None,
    ) -> None:
        """Upsert the AI bankroll row in the given sandbox.

        Writes the stored `chips` snapshot verbatim — callers writing
        a post-event value must have already projected through
        elapsed time and reset `last_regen_tick = now` on the state
        before calling this.

        First-write-per-sandbox emits an `ai_seed` ledger entry for
        `state.chips` when `chip_ledger_repo` is provided. Closes the
        chip-ledger gap from `CASH_MODE_ECONOMY.md` Known Issues §2:
        without this, new sandboxes would create AI chips from thin
        air with no audit trail.
        """
        with self._get_connection() as conn:
            is_first_write = chip_ledger_repo is not None and conn.execute(
                "SELECT 1 FROM ai_bankroll_state "
                "WHERE personality_id = ? AND sandbox_id = ?",
                (state.personality_id, sandbox_id),
            ).fetchone() is None
            conn.execute(
                """
                INSERT OR REPLACE INTO ai_bankroll_state
                    (personality_id, sandbox_id, chips, last_regen_tick)
                VALUES (?, ?, ?, ?)
                """,
                (
                    state.personality_id,
                    sandbox_id,
                    state.chips,
                    state.last_regen_tick.isoformat() if state.last_regen_tick else None,
                ),
            )
        if is_first_write and state.chips > 0:
            from core.economy import ledger as chip_ledger
            chip_ledger.record_ai_seed(
                chip_ledger_repo,
                personality_id=state.personality_id,
                amount=int(state.chips),
                context={'sandbox_id': sandbox_id, 'site': 'save_ai_bankroll'},
                sandbox_id=sandbox_id,
            )

    def load_ai_bankroll(
        self,
        personality_id: str,
        *,
        sandbox_id: str,
    ) -> Optional[AIBankrollState]:
        """Load the raw stored snapshot in the given sandbox."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT chips, last_regen_tick
                FROM ai_bankroll_state
                WHERE personality_id = ? AND sandbox_id = ?
                """,
                (personality_id, sandbox_id),
            ).fetchone()
            if not row:
                return None
            return AIBankrollState(
                personality_id=personality_id,
                chips=row["chips"],
                last_regen_tick=_parse_timestamp(row["last_regen_tick"]),
            )

    def save_emotional_state_json(
        self,
        personality_id: str,
        state_json: Optional[str],
        *,
        sandbox_id: str,
    ) -> None:
        """Persist the AI's emotional-state blob in the given sandbox."""
        with self._get_connection() as conn:
            existing = conn.execute(
                "SELECT 1 FROM ai_bankroll_state "
                "WHERE personality_id = ? AND sandbox_id = ?",
                (personality_id, sandbox_id),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE ai_bankroll_state
                    SET emotional_state_json = ?
                    WHERE personality_id = ? AND sandbox_id = ?
                    """,
                    (state_json, personality_id, sandbox_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO ai_bankroll_state
                        (personality_id, sandbox_id, chips, last_regen_tick,
                         emotional_state_json)
                    VALUES (?, ?, 0, NULL, ?)
                    """,
                    (personality_id, sandbox_id, state_json),
                )

    def load_emotional_state_json(
        self,
        personality_id: str,
        *,
        sandbox_id: str,
    ) -> Optional[str]:
        """Return the persisted emotional-state JSON blob in the sandbox."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT emotional_state_json
                FROM ai_bankroll_state
                WHERE personality_id = ? AND sandbox_id = ?
                """,
                (personality_id, sandbox_id),
            ).fetchone()
            if not row:
                return None
            return row["emotional_state_json"]

    def load_emotional_state_json_for_pids(
        self,
        personality_ids: List[str],
        *,
        sandbox_id: str,
    ) -> dict:
        """Batched read of emotional_state_json for multiple AIs in one sandbox."""
        result = {pid: None for pid in personality_ids}
        if not personality_ids:
            return result
        placeholders = ",".join("?" for _ in personality_ids)
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT personality_id, emotional_state_json
                FROM ai_bankroll_state
                WHERE sandbox_id = ?
                  AND personality_id IN ({placeholders})
                """,
                (sandbox_id, *personality_ids),
            ).fetchall()
        for row in rows:
            result[row["personality_id"]] = row["emotional_state_json"]
        return result

    def load_aspiration_cooldown_until(
        self,
        personality_id: str,
        *,
        sandbox_id: str,
    ) -> Optional[datetime]:
        """Return the AI's aspiration cooldown expiry, or None if clear.

        v107 column on `ai_bankroll_state`. NULL means "no cooldown
        active" (the common case). The trigger inside
        `refresh_table_roster` compares this against the current
        `now` and skips the aspiration roll if the AI is still
        cooling off.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT aspiration_cooldown_until
                FROM ai_bankroll_state
                WHERE personality_id = ? AND sandbox_id = ?
                """,
                (personality_id, sandbox_id),
            ).fetchone()
        if not row or row["aspiration_cooldown_until"] is None:
            return None
        try:
            return datetime.fromisoformat(row["aspiration_cooldown_until"])
        except (TypeError, ValueError):
            # Malformed timestamp — treat as cleared rather than crash.
            logger.warning(
                "Personality %r in sandbox %r has malformed "
                "aspiration_cooldown_until %r; treating as cleared",
                personality_id, sandbox_id, row["aspiration_cooldown_until"],
            )
            return None

    def save_aspiration_cooldown_until(
        self,
        personality_id: str,
        *,
        sandbox_id: str,
        until: Optional[datetime],
    ) -> bool:
        """Stamp or clear the AI's aspiration cooldown.

        `until=None` clears the column (back to "no cooldown active").
        Targets an existing `ai_bankroll_state` row — returns False
        when no row matches. Callers driving this from the lobby
        refresh path can assume the row exists (the bankroll-seed
        helper creates it on first sit-down).
        """
        value = until.isoformat() if until is not None else None
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE ai_bankroll_state
                SET aspiration_cooldown_until = ?
                WHERE personality_id = ? AND sandbox_id = ?
                """,
                (value, personality_id, sandbox_id),
            )
            return cursor.rowcount > 0

    def sum_ai_bankroll_chips_stored(
        self,
        *,
        sandbox_id: Optional[str] = None,
    ) -> int:
        """Return the sum of stored chips. `sandbox_id=None` = all sandboxes."""
        with self._get_connection() as conn:
            if sandbox_id is None:
                row = conn.execute(
                    "SELECT COALESCE(SUM(chips), 0) FROM ai_bankroll_state"
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COALESCE(SUM(chips), 0) FROM ai_bankroll_state "
                    "WHERE sandbox_id = ?",
                    (sandbox_id,),
                ).fetchone()
            return int(row[0] or 0)

    def iter_personality_ids_with_bankrolls(
        self,
        *,
        sandbox_id: Optional[str] = None,
    ) -> List[str]:
        """Return personality_ids with a row in ai_bankroll_state.

        `sandbox_id=None` returns the union across all sandboxes
        (admin / audit). Passing a sandbox_id scopes to one save-file.
        """
        with self._get_connection() as conn:
            if sandbox_id is None:
                rows = conn.execute(
                    "SELECT DISTINCT personality_id FROM ai_bankroll_state"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT personality_id FROM ai_bankroll_state "
                    "WHERE sandbox_id = ?",
                    (sandbox_id,),
                ).fetchall()
        return [row[0] if not hasattr(row, 'keys') else row['personality_id'] for row in rows]

    def iter_personality_ids_with_bankrolls_by_sandbox(
        self,
    ) -> List[tuple]:
        """Return `[(personality_id, sandbox_id), ...]` across every sandbox.

        Drives the chip-ledger audit's cross-sandbox `ai_bankrolls_projected`
        sum — each (pid, sandbox_id) pair gets its bankroll projected
        independently and summed. Per-sandbox audits don't need this
        helper (they walk a single sandbox via the scoped
        `iter_personality_ids_with_bankrolls(sandbox_id=...)`).
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT personality_id, sandbox_id FROM ai_bankroll_state"
            ).fetchall()
        return [
            (
                row[0] if not hasattr(row, 'keys') else row['personality_id'],
                row[1] if not hasattr(row, 'keys') else row['sandbox_id'],
            )
            for row in rows
        ]

    def load_ai_bankroll_current(
        self,
        personality_id: str,
        *,
        sandbox_id: str,
        now: Optional[datetime] = None,
    ) -> Optional[int]:
        """Load the current live bankroll chip count in the sandbox."""
        state = self.load_ai_bankroll(personality_id, sandbox_id=sandbox_id)
        if state is None:
            return None
        knobs = self.load_personality_knobs(personality_id)
        if now is None:
            now = datetime.utcnow()
        return project_bankroll(state, knobs.starting_bankroll, knobs.bankroll_rate, now)

    # --- Player bankroll ---

    def save_player_bankroll(self, state: PlayerBankrollState) -> None:
        """Upsert the player bankroll row.

        Only `(chips, starting_bankroll)` are read from the state —
        the legacy `active_loan_*` columns (v89/v90) were dropped in
        v99 once the stakes-table cutover completed. Active stakes
        live in `StakeRepository` now; this row carries no loan state.
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO player_bankroll_state
                    (player_id, chips, starting_bankroll)
                VALUES (?, ?, ?)
                """,
                (
                    state.player_id,
                    state.chips,
                    state.starting_bankroll,
                ),
            )

    def load_player_bankroll(self, player_id: str) -> Optional[PlayerBankrollState]:
        """Load the player bankroll row.

        Returns None when no row exists; the caller decides whether
        to grant a starting bankroll (first-time entry into cash
        mode) or refuse the operation.

        The legacy `active_loan_*` columns (v89/v90) were dropped in
        v99 — stake state lives in `StakeRepository`, not here.
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
        # Accept the legacy `bankroll_cap` key for personalities whose
        # config_json hasn't been migrated to `starting_bankroll` yet.
        # The two names are aliases — same chip value, just renamed.
        starting = sub.get(
            "starting_bankroll",
            sub.get("bankroll_cap", defaults.starting_bankroll),
        )
        return BankrollKnobs(
            starting_bankroll=starting,
            bankroll_rate=sub.get("bankroll_rate", defaults.bankroll_rate),
            buy_in_multiplier=sub.get("buy_in_multiplier", defaults.buy_in_multiplier),
            stake_comfort_zone=sub.get("stake_comfort_zone", defaults.stake_comfort_zone),
        )

    def load_staker_profile(self, personality_id: str) -> StakerProfile:
        """Read the staker profile from `config_json.staker_profile`.

        Mirrors `load_personality_knobs`: same nesting convention, same
        per-field fallback to `STAKER_PROFILE_DEFAULTS`. Three fallback
        cases all return the default profile:
          - personality_id not in the table (unknown personality)
          - config_json has no `staker_profile` sub-dict (untuned)
          - sub-dict is partial (only some keys set; missing keys fall
            back per-field)

        Defaults are conservative (`max_loan_pct=0.05`, `floor=1.20`,
        `rate=0.30`, `respect_floor=-0.5`, `heat_ceiling=0.7`) so a
        personality without an explicit profile lends like a cautious
        small-stake banker rather than refusing outright.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT config_json FROM personalities WHERE personality_id = ?",
                (personality_id,),
            ).fetchone()
        if not row:
            return STAKER_PROFILE_DEFAULTS

        try:
            config = json.loads(row["config_json"])
        except (TypeError, ValueError):
            logger.warning(
                "Personality %r has malformed config_json; using staker profile defaults",
                personality_id,
            )
            return STAKER_PROFILE_DEFAULTS

        sub = config.get("staker_profile") or {}
        if not isinstance(sub, dict):
            logger.warning(
                "Personality %r has non-dict staker_profile; using defaults",
                personality_id,
            )
            return STAKER_PROFILE_DEFAULTS

        defaults = STAKER_PROFILE_DEFAULTS
        return StakerProfile(
            willing=sub.get("willing", defaults.willing),
            max_loan_pct_of_bankroll=sub.get(
                "max_loan_pct_of_bankroll", defaults.max_loan_pct_of_bankroll,
            ),
            floor_anchor=sub.get("floor_anchor", defaults.floor_anchor),
            rate_anchor=sub.get("rate_anchor", defaults.rate_anchor),
            respect_floor=sub.get("respect_floor", defaults.respect_floor),
            heat_ceiling=sub.get("heat_ceiling", defaults.heat_ceiling),
        )

    def load_borrower_profile(self, personality_id: str) -> BorrowerProfile:
        """Read the borrower profile from `config_json.borrower_profile`.

        Phase 4 + Phase 5 of the backing system. Mirrors
        `load_staker_profile`'s per-field fallback to
        `BORROWER_PROFILE_DEFAULTS` with one nuance: when the sub-dict
        is missing `willingness_threshold`, the value is **derived
        from `config.anchors.ego`** (already curated per personality)
        rather than falling back to the flat default. This populates
        every character's stake-acceptance threshold without per-
        personality JSON edits — proud AIs get harder thresholds,
        humble AIs easier ones, all from one canonical anchor.

        Hard-fallback cases (return BORROWER_PROFILE_DEFAULTS verbatim,
        no derivation attempted):
          - personality_id not in the table
          - config_json malformed (JSON parse error)
          - sub-dict is non-dict shape

        Default `willing=True` so unannotated personalities accept
        stakes when bust. Stoic personalities override `willing=False`
        via their config sub-dict. Explicit
        `borrower_profile.willingness_threshold` in config_json
        always wins over the ego-derived value.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT config_json FROM personalities WHERE personality_id = ?",
                (personality_id,),
            ).fetchone()
        if not row:
            return BORROWER_PROFILE_DEFAULTS

        try:
            config = json.loads(row["config_json"])
        except (TypeError, ValueError):
            logger.warning(
                "Personality %r has malformed config_json; using borrower profile defaults",
                personality_id,
            )
            return BORROWER_PROFILE_DEFAULTS

        sub = config.get("borrower_profile") or {}
        if not isinstance(sub, dict):
            logger.warning(
                "Personality %r has non-dict borrower_profile; using defaults",
                personality_id,
            )
            return BORROWER_PROFILE_DEFAULTS

        defaults = BORROWER_PROFILE_DEFAULTS
        anchors = config.get("anchors") if isinstance(config, dict) else None
        anchors = anchors if isinstance(anchors, dict) else None

        # Willingness threshold derivation: explicit override in the
        # borrower_profile sub-dict wins; otherwise derive from the
        # personality's `anchors.ego` (already curated per character).
        # This lets every personality get a defensible threshold
        # without per-personality JSON edits while leaving the
        # explicit-override escape hatch intact for characters that
        # need hand-tuning (e.g. a humble-but-paranoid stoic that
        # wants a higher floor than ego alone would predict).
        if "willingness_threshold" in sub:
            willingness_threshold = float(sub["willingness_threshold"])
        elif anchors is not None and "ego" in anchors:
            try:
                ego = float(anchors["ego"])
                willingness_threshold = compute_default_willingness_threshold(ego)
            except (TypeError, ValueError):
                willingness_threshold = defaults.willingness_threshold
        else:
            willingness_threshold = defaults.willingness_threshold

        # Aspiration bias derivation: same pattern. Explicit override
        # wins; otherwise compose from `ego` + `risk_identity` anchors.
        # When `willing=False` the field is forced to 0 regardless —
        # refusing stakes outright is incompatible with asking for one
        # (character consistency, locked decision in the aspiration
        # spec).
        willing = sub.get("willing", defaults.willing)
        if not willing:
            aspiration_bias = 0.0
        elif "aspiration_bias" in sub:
            try:
                aspiration_bias = max(0.0, min(1.0, float(sub["aspiration_bias"])))
            except (TypeError, ValueError):
                aspiration_bias = defaults.aspiration_bias
        elif (
            anchors is not None
            and "ego" in anchors
            and "risk_identity" in anchors
        ):
            try:
                aspiration_bias = compute_default_aspiration_bias(
                    float(anchors["ego"]),
                    float(anchors["risk_identity"]),
                )
            except (TypeError, ValueError):
                aspiration_bias = defaults.aspiration_bias
        else:
            aspiration_bias = defaults.aspiration_bias

        # Payoff eagerness derivation: same explicit-override-then-anchor
        # pattern. Composed from `risk_identity` (inverse) + `poise`
        # — captures conscientiousness about clearing debts. Unlike
        # aspiration_bias, this is NOT gated on `willing` — a stoic
        # who refuses stakes still has opinions about settling
        # obligations they already incurred, so we honor the
        # derivation regardless.
        if "payoff_eagerness" in sub:
            try:
                payoff_eagerness = max(
                    0.0, min(1.0, float(sub["payoff_eagerness"])),
                )
            except (TypeError, ValueError):
                payoff_eagerness = defaults.payoff_eagerness
        elif (
            anchors is not None
            and "risk_identity" in anchors
            and "poise" in anchors
        ):
            try:
                payoff_eagerness = compute_default_payoff_eagerness(
                    float(anchors["risk_identity"]),
                    float(anchors["poise"]),
                )
            except (TypeError, ValueError):
                payoff_eagerness = defaults.payoff_eagerness
        else:
            payoff_eagerness = defaults.payoff_eagerness

        return BorrowerProfile(
            willing=willing,
            willingness_threshold=willingness_threshold,
            aspiration_bias=aspiration_bias,
            payoff_eagerness=payoff_eagerness,
        )

    def save_borrower_profile(
        self,
        personality_id: str,
        *,
        willing: bool,
        willingness_threshold: Optional[float],
        aspiration_bias: Optional[float] = None,
        payoff_eagerness: Optional[float] = None,
    ) -> bool:
        """Merge borrower-profile values into `config_json.borrower_profile`.

        Phase 5 admin surface. Mirrors `save_personality_knobs` but
        carries the "explicit override vs derived" semantic the
        loader relies on for both `willingness_threshold` and
        `aspiration_bias`:

          - `<field> = None` → drop the key entirely so the loader
            falls through to the anchor-derived default
          - `<field> = float` → store the override value; the loader
            returns it verbatim (clamped to [0, 1] for aspiration_bias)

        Reads the full config, mutates only `borrower_profile`,
        writes back — every other key (anchors, staker_profile,
        bankroll_knobs, verbal_tics, etc.) is preserved.

        Returns True if a row was updated, False if no row matches.
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
                    "Personality %r config_json is malformed; refusing "
                    "to merge borrower_profile",
                    personality_id,
                )
                return False
            bp = config.get("borrower_profile")
            if not isinstance(bp, dict):
                bp = {}
            bp["willing"] = bool(willing)
            if willingness_threshold is None:
                # Clear the override so the loader falls back to the
                # ego-derived default. Dropping the key (vs writing
                # null) keeps the JSON clean.
                bp.pop("willingness_threshold", None)
            else:
                bp["willingness_threshold"] = float(willingness_threshold)
            if aspiration_bias is None:
                bp.pop("aspiration_bias", None)
            else:
                bp["aspiration_bias"] = max(
                    0.0, min(1.0, float(aspiration_bias)),
                )
            if payoff_eagerness is None:
                bp.pop("payoff_eagerness", None)
            else:
                bp["payoff_eagerness"] = max(
                    0.0, min(1.0, float(payoff_eagerness)),
                )
            config["borrower_profile"] = bp
            cursor = conn.execute(
                """
                UPDATE personalities
                SET config_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE personality_id = ?
                """,
                (json.dumps(config), personality_id),
            )
            return cursor.rowcount > 0

    def save_staker_profile(
        self,
        personality_id: str,
        profile: StakerProfile,
    ) -> bool:
        """Merge staker-profile values into `config_json.staker_profile`.

        Admin surface for the staker side of the backing system (the
        AI's behavior when OTHER players ask them for a stake-up loan).
        Mirrors `save_borrower_profile`'s read-modify-write shape:
        reads the full config, mutates only `staker_profile`, writes
        back — every other key (anchors, borrower_profile,
        bankroll_knobs, etc.) is preserved.

        Stores all six fields verbatim. The loader's per-field default
        fallback (`load_staker_profile`) still applies if a future
        partial write drops a key, so callers can safely write a full
        StakerProfile here without worrying about schema drift.

        Returns True if a row was updated, False if no row matches.
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
                    "Personality %r config_json is malformed; refusing "
                    "to merge staker_profile",
                    personality_id,
                )
                return False
            config["staker_profile"] = {
                "willing": bool(profile.willing),
                "max_loan_pct_of_bankroll": float(profile.max_loan_pct_of_bankroll),
                "floor_anchor": float(profile.floor_anchor),
                "rate_anchor": float(profile.rate_anchor),
                "respect_floor": float(profile.respect_floor),
                "heat_ceiling": float(profile.heat_ceiling),
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
                "starting_bankroll": knobs.starting_bankroll,
                "bankroll_rate": knobs.bankroll_rate,
                "buy_in_multiplier": knobs.buy_in_multiplier,
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
