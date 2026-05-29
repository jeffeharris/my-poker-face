"""Repository for per-user settings.

`user_preferences` (schema v115) holds one row per user, keyed by
`user_id`. The first concrete setting is `world_pace` — how fast the
realtime background ticker advances the unseated-table world for that
user's sandbox. A `preferences_json` blob is reserved for future scalar
prefs so the next setting doesn't need a migration.

See `docs/plans/CASH_MODE_REALTIME_TICKER.md`.
"""

from __future__ import annotations

import json
import logging

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


# Valid world-pace values + the default. Kept here (not in the ticker
# service) so both the repo's validation and the route's input check
# share one source of truth without importing Flask-side modules.
WORLD_PACES = ("subtle", "lively", "bustling")
DEFAULT_WORLD_PACE = "lively"

# Cap on the human's self-description. Long enough for a sentence or two of
# flavor the AIs can riff on, short enough to keep it out of token budgets.
MAX_BIO_LENGTH = 500

# Valid coach modes + the default. New games are stamped with the user's
# default (so it applies cross-device); the in-game panel still overrides
# per game. Mirrors the frontend's CoachMode union.
COACH_MODES = ("off", "reactive", "proactive")
DEFAULT_COACH_MODE = "off"

# How fast the game resolves for this user:
#   standard    — full AI deliberation every turn
#   after_fold  — fast-forward the rest of the orbit once the human folds
#   always      — fast-forward every AI turn (no LLM deliberation)
# Fast-forward = no-LLM tiered controllers; trade-off is no AI table talk.
GAME_SPEEDS = ("standard", "after_fold", "always")
DEFAULT_GAME_SPEED = "standard"


class UserPreferencesRepository(BaseRepository):
    """CRUD for `user_preferences`."""

    def get_world_pace(self, user_id: str) -> str:
        """Return the user's world pace, or the default if unset.

        Never raises on a missing row — a user who has never touched the
        setting gets `DEFAULT_WORLD_PACE`. An unrecognized stored value
        (e.g. a future pace rolled back) also degrades to the default so
        the ticker always has a usable rate.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT world_pace FROM user_preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            return DEFAULT_WORLD_PACE
        pace = row[0]
        return pace if pace in WORLD_PACES else DEFAULT_WORLD_PACE

    def set_world_pace(self, user_id: str, pace: str) -> None:
        """Persist the user's world pace.

        Raises `ValueError` for an invalid pace so the route can return a
        400 rather than silently storing garbage. UPSERT keeps the call
        idempotent and avoids a read-before-write.
        """
        if pace not in WORLD_PACES:
            raise ValueError(f"invalid world_pace {pace!r}; expected one of {WORLD_PACES}")
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO user_preferences (user_id, world_pace, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    world_pace = excluded.world_pace,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, pace),
            )

    def get_bio(self, user_id: str) -> str:
        """Return the user's self-description, or empty string if unset.

        Never raises on a missing row/column — a user who has never written a
        bio gets ``""``. The AIs read this to trash-talk or comment on the
        human, so an empty string simply means "no extra color to riff on."
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT bio FROM user_preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row or row[0] is None:
            return ""
        return str(row[0])

    def set_bio(self, user_id: str, bio: str) -> str:
        """Persist the user's self-description; return the stored (trimmed) value.

        Trims and caps at ``MAX_BIO_LENGTH`` so an overlong paste degrades
        gracefully rather than 400-ing. UPSERT keeps it idempotent and avoids a
        read-before-write. Passing an empty/whitespace string clears the bio.
        """
        trimmed = (bio or "").strip()[:MAX_BIO_LENGTH]
        stored = trimmed or None
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO user_preferences (user_id, bio, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    bio = excluded.bio,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, stored),
            )
        return trimmed

    # === preferences_json scalar prefs ===
    # The v115 `preferences_json` blob is reserved for scalar prefs that don't
    # each warrant a column. Read-merge-write so settings sharing the blob don't
    # clobber each other.

    def _get_preferences_json(self, user_id: str) -> dict:
        """Return the parsed preferences_json blob, or {} if unset/malformed.

        Malformed JSON degrades to {} so one bad write can't break reads.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT preferences_json FROM user_preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row or not row[0]:
            return {}
        try:
            data = json.loads(row[0])
        except (ValueError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _set_preference_scalar(self, user_id: str, key: str, value) -> None:
        """Merge one scalar into preferences_json (read-merge-write, UPSERT)."""
        prefs = self._get_preferences_json(user_id)
        prefs[key] = value
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO user_preferences (user_id, preferences_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    preferences_json = excluded.preferences_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, json.dumps(prefs)),
            )

    def get_game_speed(self, user_id: str) -> str:
        """Game speed: 'standard' / 'after_fold' / 'always' (default 'standard').

        Falls back to the legacy boolean `auto_fast_fold` (True → 'after_fold')
        for users who set the preference before it became a 3-way choice. An
        unrecognized stored value degrades to the default.
        """
        prefs = self._get_preferences_json(user_id)
        speed = prefs.get('game_speed')
        if speed is None:
            # Legacy pre-3-way value.
            return 'after_fold' if prefs.get('auto_fast_fold') else DEFAULT_GAME_SPEED
        return speed if speed in GAME_SPEEDS else DEFAULT_GAME_SPEED

    def set_game_speed(self, user_id: str, speed: str) -> str:
        """Persist the game speed; return the stored value.

        Raises ValueError for an invalid speed so the route can 400.
        """
        if speed not in GAME_SPEEDS:
            raise ValueError(f"invalid game_speed {speed!r}; expected one of {GAME_SPEEDS}")
        self._set_preference_scalar(user_id, 'game_speed', speed)
        return speed

    def get_coach_default_mode(self, user_id: str) -> str:
        """The coaching mode new games start in (off / reactive / proactive).

        Default 'off'. New games are stamped with this so it carries across
        devices; the in-game coach panel still changes the mode per game. An
        unrecognized stored value degrades to the default.
        """
        mode = self._get_preferences_json(user_id).get('coach_default_mode', DEFAULT_COACH_MODE)
        return mode if mode in COACH_MODES else DEFAULT_COACH_MODE

    def set_coach_default_mode(self, user_id: str, mode: str) -> str:
        """Persist the default coaching mode; return the stored value.

        Raises ValueError for an invalid mode so the route can 400 rather than
        store garbage.
        """
        if mode not in COACH_MODES:
            raise ValueError(f"invalid coach mode {mode!r}; expected one of {COACH_MODES}")
        self._set_preference_scalar(user_id, 'coach_default_mode', mode)
        return mode
