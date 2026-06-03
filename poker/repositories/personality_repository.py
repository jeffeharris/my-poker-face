"""Repository for personality and avatar persistence.

Manages the personalities and avatar_images tables.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Set

from poker.personality_id import (
    assign_unique_personality_id,
    slugify_personality_name,
)
from poker.repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)

# Seeded rule-bot stand-ins (CaseBot / GTO-Lite / BaselineSolver) that
# live in the personalities table for tournament-mode picker symmetry
# but are NOT roleplay characters and shouldn't fill cash-mode seats —
# they'd otherwise leak into cash sessions and play as `sharp` purely
# on their authored poise. Filtered by stable personality_id so display
# names can be renamed without re-opening the gate.
CASH_INELIGIBLE_PERSONALITY_IDS = frozenset(
    {
        "casebot",
        "gto_lite",
        "baselinesolver",
    }
)


class PersonalityRepository(BaseRepository):
    """Handles CRUD operations for personalities and avatar images."""

    # --- Personality CRUD ---

    def save_personality(
        self,
        name: str,
        config: Dict[str, Any],
        source: str = 'ai_generated',
        owner_id: Optional[str] = None,
        visibility: Optional[str] = None,
        personality_id: Optional[str] = None,
        circulating: Optional[bool] = None,
    ) -> str:
        """Save a personality configuration to the database.

        Args:
            name: Display name (human-facing, may be edited later)
            config: Personality config dict (may include 'id' as a hint;
                explicit personality_id parameter wins if both provided)
            source: Provenance label (ai_generated, user_created, etc.)
            owner_id: Owning user. ``None`` preserves an existing row's owner
                on re-save (and stays ``None`` for a new row).
            visibility: 'public' | 'private' | 'disabled'. ``None`` preserves an
                existing row's visibility on re-save (PRH-27: editing e.g. an
                avatar description must not silently publish a private
                personality); a new row defaults to 'public'.
            circulating: Whether the persona is auto-seeded into the
                opponent pool (cash-mode seat-filler). ``None`` preserves
                an existing row's value on re-save; a NEW row defaults to
                0 (NOT circulating) — the safe default that stops sim/test/
                ownerless personas from silently entering everyone's games.
                Curated seeds set this to 1 explicitly. Distinct from
                `visibility`: a row can be public (visible/pickable) yet
                non-circulating (never auto-seated). See migration v123.
            personality_id: Stable identifier (slug-style). If omitted,
                generated from name via slugify_personality_name. The
                method preserves an existing row's personality_id when
                INSERT OR REPLACE fires on the name UNIQUE constraint.

        Returns:
            The personality_id assigned to the row (newly generated or
            preserved from existing). Callers persisting cross-session
            state (relationships, bankrolls, opponent_models) should use
            this returned id, not the display name.
        """
        elasticity_config = config.get('elasticity_config', {})
        config_without_elasticity = {
            k: v for k, v in config.items() if k not in ('elasticity_config', 'id')
        }

        with self._get_connection() as conn:
            cursor = conn.execute("PRAGMA table_info(personalities)")
            columns = [row[1] for row in cursor.fetchall()]

            has_elasticity = 'elasticity_config' in columns
            has_ownership = 'owner_id' in columns
            has_personality_id = 'personality_id' in columns
            has_visibility = 'visibility' in columns
            has_circulating = 'circulating' in columns

            # Fetch the existing row once so a re-save preserves identity,
            # ownership, and visibility the caller didn't explicitly set.
            existing = conn.execute(
                "SELECT * FROM personalities WHERE name = ?", (name,)
            ).fetchone()

            # Resolve the personality_id to write. Priority:
            #   1. Explicit parameter
            #   2. `id` hint inside config dict (from JSON seed source)
            #   3. Existing row's personality_id (preserve across re-saves)
            #   4. Freshly slugified from name, with collision resolution
            resolved_id = personality_id or config.get('id')
            if has_personality_id and not resolved_id and existing and existing['personality_id']:
                resolved_id = existing['personality_id']
            if has_personality_id and not resolved_id:
                base_slug = slugify_personality_name(name)
                if base_slug:
                    taken = {
                        row['personality_id']
                        for row in conn.execute(
                            "SELECT personality_id FROM personalities "
                            "WHERE personality_id IS NOT NULL AND name != ?",
                            (name,),
                        )
                    }
                    resolved_id = assign_unique_personality_id(base_slug, taken)
                else:
                    logger.warning(
                        "save_personality: name=%r slugifies to empty; "
                        "writing row without personality_id",
                        name,
                    )

            # Preserve owner_id / visibility on a re-save unless the caller
            # explicitly overrides them. INSERT OR REPLACE rewrites the whole
            # row, so without this an avatar/visual-identity edit (which passes
            # neither) would silently orphan + publish a private personality.
            resolved_owner_id = owner_id
            if resolved_owner_id is None and existing is not None and has_ownership:
                resolved_owner_id = existing['owner_id']
            resolved_visibility = visibility
            if resolved_visibility is None:
                if existing is not None and has_visibility and existing['visibility']:
                    resolved_visibility = existing['visibility']
                else:
                    resolved_visibility = 'public'

            # Resolve `circulating` the same way: explicit param wins, else
            # preserve the existing row's value, else a new row defaults to
            # 0 (NOT circulating). INSERT OR REPLACE below can't carry a
            # conditional column cleanly across the four schema branches, so
            # it's applied as a branch-agnostic follow-up UPDATE once the row
            # exists. Read here, before the REPLACE rewrites the row.
            resolved_circulating: Optional[int] = None
            if has_circulating:
                if circulating is not None:
                    resolved_circulating = 1 if circulating else 0
                elif existing is not None and existing['circulating'] is not None:
                    resolved_circulating = int(existing['circulating'])
                else:
                    resolved_circulating = 0

            if has_personality_id and has_elasticity and has_ownership:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO personalities
                    (name, config_json, elasticity_config, source, owner_id,
                     visibility, personality_id, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                    (
                        name,
                        json.dumps(config_without_elasticity),
                        json.dumps(elasticity_config),
                        source,
                        resolved_owner_id,
                        resolved_visibility,
                        resolved_id,
                    ),
                )
            elif has_elasticity and has_ownership:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO personalities
                    (name, config_json, elasticity_config, source, owner_id, visibility, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                    (
                        name,
                        json.dumps(config_without_elasticity),
                        json.dumps(elasticity_config),
                        source,
                        resolved_owner_id,
                        resolved_visibility,
                    ),
                )
            elif has_elasticity:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO personalities
                    (name, config_json, elasticity_config, source, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                    (
                        name,
                        json.dumps(config_without_elasticity),
                        json.dumps(elasticity_config),
                        source,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO personalities
                    (name, config_json, source, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """,
                    (name, json.dumps(config), source),
                )

            # INSERT OR REPLACE above either left `circulating` at the column
            # default (0) for a fresh row or reset it on a re-save; write the
            # resolved value explicitly so re-saves preserve it and seeds keep
            # their circulating=1. Branch-agnostic — runs for every path.
            if has_circulating and resolved_circulating is not None:
                conn.execute(
                    "UPDATE personalities SET circulating = ? WHERE name = ?",
                    (resolved_circulating, name),
                )

        return resolved_id or ""

    def load_personality(self, name: str) -> Optional[Dict[str, Any]]:
        """Load a personality configuration from the database.

        Returns the config dict augmented with `id` (the stable
        personality_id) when the column is available. Display name is
        still the lookup key here for back-compat; new callers should
        prefer `load_personality_by_id` for cross-session identity.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("PRAGMA table_info(personalities)")
            columns = [row[1] for row in cursor.fetchall()]

            select_cols = ["config_json"]
            if 'elasticity_config' in columns:
                select_cols.append("elasticity_config")
            if 'personality_id' in columns:
                select_cols.append("personality_id")

            cursor = conn.execute(
                f"SELECT {', '.join(select_cols)} FROM personalities WHERE name = ?",
                (name,),
            )
            row = cursor.fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE personalities
                    SET times_used = times_used + 1
                    WHERE name = ?
                """,
                    (name,),
                )

                config = json.loads(row['config_json'])

                if 'elasticity_config' in columns and row['elasticity_config']:
                    config['elasticity_config'] = json.loads(row['elasticity_config'])

                if 'personality_id' in columns and row['personality_id']:
                    config['id'] = row['personality_id']

                return config

            return None

    def load_personality_by_id(self, personality_id: str) -> Optional[Dict[str, Any]]:
        """Load a personality by its stable id.

        Preferred for cross-session state (relationship layer, AI
        bankrolls, opponent_models) where identity must survive display-
        name edits. Returns the config dict with `id` and `name`
        populated so callers can render the display name without a
        second query.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("PRAGMA table_info(personalities)")
            columns = [row[1] for row in cursor.fetchall()]

            if 'personality_id' not in columns:
                # Schema predates v85; can't satisfy by-id lookups.
                return None

            select_cols = ["name", "config_json"]
            if 'elasticity_config' in columns:
                select_cols.append("elasticity_config")

            cursor = conn.execute(
                f"SELECT {', '.join(select_cols)} FROM personalities " "WHERE personality_id = ?",
                (personality_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            conn.execute(
                "UPDATE personalities SET times_used = times_used + 1 " "WHERE personality_id = ?",
                (personality_id,),
            )

            config = json.loads(row['config_json'])
            config['id'] = personality_id
            config['name'] = row['name']
            if 'elasticity_config' in columns and row['elasticity_config']:
                config['elasticity_config'] = json.loads(row['elasticity_config'])
            return config

    def resolve_name_to_personality_id(self, name: str) -> Optional[str]:
        """Look up the stable personality_id for a display name.

        Returns None if the personality isn't in the DB or the column
        doesn't exist (pre-v85 schema).
        """
        with self._get_connection() as conn:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(personalities)")]
            if 'personality_id' not in columns:
                return None
            row = conn.execute(
                "SELECT personality_id FROM personalities WHERE name = ?",
                (name,),
            ).fetchone()
            return row['personality_id'] if row and row['personality_id'] else None

    def display_names_by_ids(self, personality_ids) -> Dict[str, str]:
        """Map a set of personality_ids → display names in one query.

        Side-effect-free (unlike `load_personality_by_id`, which bumps
        `times_used`) so it's safe on hot read paths like the lobby
        whereabouts view that resolve many ids per poll. Ids absent from
        the table are simply omitted from the result; callers fall back
        to the id as the display name and can treat the gap as an orphan.
        """
        ids = [pid for pid in dict.fromkeys(personality_ids) if pid]
        if not ids:
            return {}
        with self._get_connection() as conn:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(personalities)")]
            if 'personality_id' not in columns:
                return {}
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"SELECT personality_id, name FROM personalities "
                f"WHERE personality_id IN ({placeholders})",
                ids,
            ).fetchall()
            return {row['personality_id']: row['name'] for row in rows if row['personality_id']}

    def list_personalities(
        self,
        limit: int = 50,
        user_id: Optional[str] = None,
        include_disabled: bool = False,
        circulating_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """List personalities with metadata, filtered by visibility.

        Args:
            limit: Max number of results
            user_id: If provided, include this user's private personalities
            include_disabled: If True (admin), include disabled and all private personalities
            circulating_only: If True, the *public* branch is narrowed to
                circulating personas (v123) — i.e. a public-but-not-circulating
                persona (a demoted sim/test zombie) is hidden. The user's OWN
                personas (`owner_id = user_id`) are still included regardless,
                so this only trims the shared public pool. Use for player-facing
                surfaces that should mirror the cash circuit — the opponent
                picker and themed-game roster sampling — while leaving admin/
                management views (default False) showing everything for curation.
        """
        with self._get_connection() as conn:
            columns = [
                row[1] for row in conn.execute("PRAGMA table_info(personalities)").fetchall()
            ]
            has_ownership = 'owner_id' in columns
            has_circulating = 'circulating' in columns

            if has_ownership:
                public_clause = "visibility = 'public'"
                if circulating_only and has_circulating:
                    public_clause = "(visibility = 'public' AND circulating = 1)"
                conditions = [public_clause]
                params: list = []

                if user_id:
                    conditions.append("owner_id = ?")
                    params.append(user_id)

                if include_disabled:
                    conditions.append("visibility = 'disabled'")
                    conditions.append("visibility = 'private'")

                where_clause = "WHERE " + " OR ".join(conditions)

                # `circulating` surfaced (when present) so the management /
                # curation layer can show and toggle pool membership; it does
                # NOT filter this list — visibility governs who appears here,
                # circulating only governs auto-seeding (see v123).
                circ_col = ", circulating" if has_circulating else ""
                cursor = conn.execute(
                    f"""
                    SELECT name, source, created_at, updated_at, times_used, is_generated,
                           owner_id, visibility{circ_col}
                    FROM personalities
                    {where_clause}
                    ORDER BY times_used DESC, updated_at DESC
                    LIMIT ?
                """,
                    params + [limit],
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT name, source, created_at, updated_at, times_used, is_generated
                    FROM personalities
                    ORDER BY times_used DESC, updated_at DESC
                    LIMIT ?
                """,
                    (limit,),
                )

            personalities = []
            for row in cursor:
                entry = {
                    'name': row['name'],
                    'source': row['source'],
                    'created_at': row['created_at'],
                    'updated_at': row['updated_at'],
                    'times_used': row['times_used'],
                    'is_generated': bool(row['is_generated']),
                }
                if has_ownership:
                    entry['owner_id'] = row['owner_id']
                    entry['visibility'] = row['visibility']
                if has_circulating:
                    entry['circulating'] = bool(row['circulating'])
                personalities.append(entry)

            return personalities

    def list_eligible_for_cash_mode(
        self,
        *,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List personalities eligible to fill open cash-mode seats.

        Returns `[{personality_id, name}]` ordered by personality_id
        for determinism. The seat-filler downstream applies bankroll-
        eligibility filtering; this method's job is to enumerate the
        candidate pool by visibility/ownership.

        Filter rules:
          - `visibility = 'public' AND circulating = 1` included (the
            seeded corpus + any deliberately-published personalities).
            `circulating` (v123) gates AUTO-seeding: a public-but-not-
            circulating persona stays visible/pickable but is never
            auto-seated here, which is how sim/test/ownerless zombies are
            kept out of the live pool. Pre-v123 schemas (no column) fall
            back to plain `visibility = 'public'`.
          - `user_id` (if provided) → also include that user's
            'private' personalities. v1 doesn't surface this from
            the cash-mode home UI, but the parameter is here so v2's
            "play with your own custom personalities" feature drops
            in without a signature change.
          - Rows with NULL `personality_id` are excluded — they
            can't be keyed for cash persistence (pre-v85 leftovers
            that escaped backfill, malformed seeds).
          - Rule-bot stand-ins in :data:`CASH_INELIGIBLE_PERSONALITY_IDS`
            are excluded — they exist in the table for tournament-mode
            picker symmetry but aren't real cash opponents.
          - `archetype='fish'` personalities are excluded — fish are a
            casino-only player class (template + ephemeral instances).
            They live at `table_type='casino'` venues exclusively and
            never join the regular lobby pool. See
            `docs/plans/CASH_MODE_CLOSED_ECONOMY.md`.

        Distinct from `list_personalities`: that method returns
        display-name-keyed metadata for management UI; this method
        returns stable-ID-keyed candidates for game orchestration.
        """
        with self._get_connection() as conn:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(personalities)").fetchall()
            }
            has_ownership = 'owner_id' in columns
            has_personality_id = 'personality_id' in columns
            has_circulating = 'circulating' in columns

            if not has_personality_id:
                # Pre-v85 schema — no stable IDs to surface.
                return []

            conditions = ["personality_id IS NOT NULL"]
            params: list = []

            if CASH_INELIGIBLE_PERSONALITY_IDS:
                placeholders = ",".join("?" * len(CASH_INELIGIBLE_PERSONALITY_IDS))
                conditions.append(f"personality_id NOT IN ({placeholders})")
                params.extend(sorted(CASH_INELIGIBLE_PERSONALITY_IDS))

            # Fish are casino-only; exclude templates AND ephemeral
            # instances from the regular pool. `json_extract` reads
            # the `archetype` field directly from `config_json`.
            conditions.append(
                "(json_extract(config_json, '$.archetype') IS NULL "
                "OR json_extract(config_json, '$.archetype') != 'fish')"
            )

            if has_ownership:
                # Public personas auto-seat only when `circulating = 1`
                # (v123): public governs visibility, circulating governs
                # auto-seeding. A user's OWN private personas are not gated
                # by circulating — they explicitly belong to that user.
                public_clause = (
                    "(visibility = 'public' AND circulating = 1)"
                    if has_circulating
                    else "visibility = 'public'"
                )
                visibility_clauses = [public_clause]
                if user_id:
                    visibility_clauses.append("(owner_id = ? AND visibility = 'private')")
                    params.append(user_id)
                conditions.append("(" + " OR ".join(visibility_clauses) + ")")

            where_clause = " AND ".join(conditions)
            cursor = conn.execute(
                f"""
                SELECT personality_id, name
                FROM personalities
                WHERE {where_clause}
                ORDER BY personality_id
                """,
                params,
            )
            return [
                {"personality_id": row["personality_id"], "name": row["name"]} for row in cursor
            ]

    def list_fish_for_cash_mode(self) -> List[Dict[str, Any]]:
        """List the fish personas a casino can seat.

        The exact inverse of `list_eligible_for_cash_mode`'s archetype
        clause: returns `[{personality_id, name}]` for every persona
        with `archetype='fish'`, ordered by personality_id for
        determinism. These are real, curated DB personalities (e.g.
        Vacation Greg) — casino spawn/refill picks from this pool,
        seating each with pool-funded chips. They're excluded from the
        regular lobby pool, so this is the only place they surface.

        Rows with NULL `personality_id` are excluded — a fish needs a
        stable id for seat/ledger keying.
        """
        with self._get_connection() as conn:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(personalities)").fetchall()
            }
            if 'personality_id' not in columns:
                # Pre-v85 schema — no stable IDs to surface.
                return []

            cursor = conn.execute(
                """
                SELECT personality_id, name
                FROM personalities
                WHERE personality_id IS NOT NULL
                  AND json_extract(config_json, '$.archetype') = 'fish'
                ORDER BY personality_id
                """,
            )
            return [
                {"personality_id": row["personality_id"], "name": row["name"]} for row in cursor
            ]

    def list_all_personality_ids(self) -> Set[str]:
        """Return the set of every non-NULL `personality_id` in the table.

        A fast membership check for "does this seated AI still resolve to
        a real personality?" — used by the casino resolver's zombie-seat
        reclaim to spot AI seats whose persona no longer exists (e.g.
        old-model `tourist-<uuid>` seats from before the fish-as-personas
        migration). One query, no per-seat lookups.
        """
        with self._get_connection() as conn:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(personalities)").fetchall()
            }
            if 'personality_id' not in columns:
                return set()
            cursor = conn.execute(
                "SELECT personality_id FROM personalities WHERE personality_id IS NOT NULL"
            )
            return {row["personality_id"] for row in cursor}

    def delete_personality(self, name: str) -> bool:
        """Delete a personality from the database."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("DELETE FROM personalities WHERE name = ?", (name,))
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting personality {name}: {e}")
            return False

    def update_personality_config(
        self, name: str, config: Dict[str, Any], source: str = 'user_edited'
    ) -> bool:
        """Update only the config for an existing personality, preserving ownership fields.

        Unlike save_personality (which uses INSERT OR REPLACE and can wipe owner_id/visibility),
        this method uses UPDATE to modify only config_json, elasticity_config, source, and
        (if missing) personality_id.

        Personality_id semantics: never overwrites an existing non-NULL
        personality_id, even if the incoming config dict carries a
        different `id` field. The id is supposed to be stable across
        renames, so an established row's id wins. If the row is still
        NULL on personality_id (e.g., partial backfill state), and the
        config carries an `id`, the config's id is written. Otherwise
        slugify(name) is used.

        Returns:
            True if the personality was found and updated, False otherwise.
        """
        elasticity_config = config.get('elasticity_config', {})
        config_without_elasticity = {
            k: v for k, v in config.items() if k not in ('elasticity_config', 'id')
        }

        with self._get_connection() as conn:
            columns = [
                row[1] for row in conn.execute("PRAGMA table_info(personalities)").fetchall()
            ]
            has_elasticity = 'elasticity_config' in columns
            has_personality_id = 'personality_id' in columns

            # Decide whether we need to set personality_id as part of
            # this update. Only fill it in when the existing row is
            # missing one — never overwrite an established id.
            id_to_set: Optional[str] = None
            if has_personality_id:
                existing_row = conn.execute(
                    "SELECT personality_id FROM personalities WHERE name = ?",
                    (name,),
                ).fetchone()
                existing_id = existing_row['personality_id'] if existing_row else None
                if not existing_id:
                    candidate = config.get('id')
                    if not candidate:
                        candidate = slugify_personality_name(name)
                    if candidate:
                        taken = {
                            row['personality_id']
                            for row in conn.execute(
                                "SELECT personality_id FROM personalities "
                                "WHERE personality_id IS NOT NULL AND name != ?",
                                (name,),
                            )
                        }
                        id_to_set = assign_unique_personality_id(candidate, taken)

            if has_personality_id and has_elasticity and id_to_set:
                cursor = conn.execute(
                    """
                    UPDATE personalities
                    SET config_json = ?, elasticity_config = ?, source = ?,
                        personality_id = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE name = ?
                """,
                    (
                        json.dumps(config_without_elasticity),
                        json.dumps(elasticity_config),
                        source,
                        id_to_set,
                        name,
                    ),
                )
            elif has_elasticity:
                cursor = conn.execute(
                    """
                    UPDATE personalities
                    SET config_json = ?, elasticity_config = ?, source = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE name = ?
                """,
                    (
                        json.dumps(config_without_elasticity),
                        json.dumps(elasticity_config),
                        source,
                        name,
                    ),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE personalities
                    SET config_json = ?, source = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE name = ?
                """,
                    (json.dumps(config_without_elasticity), source, name),
                )

            return cursor.rowcount > 0

    def set_visibility(self, name: str, visibility: str) -> bool:
        """Set visibility for a personality. Returns True if updated."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE personalities SET visibility = ?, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
                (visibility, name),
            )
            return cursor.rowcount > 0

    def set_circulating(self, name: str, circulating: bool) -> bool:
        """Set whether a persona auto-seeds into the opponent pool (v123).

        Distinct from `set_visibility`: this flips ONLY the auto-seeding
        flag, leaving visibility (who can see/pick the persona) untouched.
        Promoting a persona into the live circuit — or demoting a leaked
        sim/test zombie out of it without hiding or deleting it — is this
        method. Returns True if a row was updated.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE personalities SET circulating = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE name = ?",
                (1 if circulating else 0, name),
            )
            return cursor.rowcount > 0

    def set_owner(self, name: str, owner_id: str, visibility: str = 'private') -> bool:
        """Assign an owner to a personality. Returns True if updated."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE personalities SET owner_id = ?, visibility = ?, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
                (owner_id, visibility, name),
            )
            return cursor.rowcount > 0

    def assign_unowned_disabled_to_owner(self, owner_id: str) -> int:
        """Assign disabled personalities with no owner to the given user.

        Changes their visibility to 'private' so the owner can use them.
        Idempotent: no-op if all disabled personalities already have owners.

        Returns:
            Count of personalities assigned.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE personalities
                SET owner_id = ?, visibility = 'private', updated_at = CURRENT_TIMESTAMP
                WHERE visibility = 'disabled' AND owner_id IS NULL
            """,
                (owner_id,),
            )
            return cursor.rowcount

    def get_personality_owner(self, name: str) -> Optional[str]:
        """Get the owner_id of a personality, or None if unowned/not found."""
        with self._get_connection() as conn:
            columns = [
                row[1] for row in conn.execute("PRAGMA table_info(personalities)").fetchall()
            ]
            if 'owner_id' not in columns:
                return None
            cursor = conn.execute("SELECT owner_id FROM personalities WHERE name = ?", (name,))
            row = cursor.fetchone()
            return row['owner_id'] if row else None

    def seed_personalities_from_json(
        self, json_path: str, overwrite: bool = False
    ) -> Dict[str, int]:
        """Seed database with personalities from JSON file.

        Args:
            json_path: Path to personalities.json file
            overwrite: If True, overwrite existing personalities

        Returns:
            Dict with counts: {'added': N, 'skipped': M, 'updated': P}
        """
        from pathlib import Path

        json_file = Path(json_path)
        if not json_file.exists():
            logger.warning(f"Personalities JSON file not found: {json_path}")
            return {'added': 0, 'skipped': 0, 'updated': 0, 'error': 'File not found'}

        try:
            with open(json_file) as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Error reading personalities JSON: {e}")
            return {'added': 0, 'skipped': 0, 'updated': 0, 'error': str(e)}

        personalities = data.get('personalities', {})
        added = 0
        skipped = 0
        updated = 0

        for name, config in personalities.items():
            existing = self.load_personality(name)

            if existing and not overwrite:
                skipped += 1
                continue

            # The JSON entries carry an `id` field that
            # save_personality / update_personality_config will pick up
            # and write to the personality_id column. That keeps the
            # JSON seed source and DB-stored ids aligned, so a fresh
            # DB rebuilt via this seed path lands at the same identity
            # state as the original.
            if existing:
                # Use config-only update to preserve ownership fields
                self.update_personality_config(name, config, source='personalities.json')
                updated += 1
            else:
                # The curated celebrity corpus IS the live pool, so seed it
                # circulating=1 explicitly. Without this a fresh-install DB
                # would land every celebrity at the new circulating=0 default
                # and start with an empty opponent pool. (Re-seeds of an
                # existing row go through update_personality_config above,
                # which leaves circulating untouched = preserved.)
                self.save_personality(
                    name, config, source='personalities.json', circulating=True
                )
                added += 1

        logger.info(
            f"Seeded personalities from JSON: {added} added, {updated} updated, {skipped} skipped"
        )
        return {'added': added, 'skipped': skipped, 'updated': updated}

    # --- Avatar CRUD ---

    def _resolve_avatar_pid(self, key: Optional[str]) -> Optional[str]:
        """Resolve an avatar key (a `personality_id` slug OR a display name) to the
        canonical `personality_id` — the SOLE key `avatar_images` is stored and
        looked up by (v147). Both `personalities.personality_id` and `.name` are
        UNIQUE, so the lookup is unambiguous. Returns None for a key that matches
        no persona (a guest / synthetic `P##` seat / orphan): such an entity has no
        persona art, so its avatar is neither stored nor found — by design."""
        if not key:
            return None
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT personality_id FROM personalities "
                "WHERE personality_id = ? OR name = ? LIMIT 1",
                (key, key),
            ).fetchone()
        return row['personality_id'] if row and row['personality_id'] else None

    def save_avatar_image(
        self,
        personality_name: str,
        emotion: str,
        image_data: bytes,
        width: int = 256,
        height: int = 256,
        content_type: str = 'image/png',
        full_image_data: Optional[bytes] = None,
        full_width: Optional[int] = None,
        full_height: Optional[int] = None,
    ) -> None:
        """Save an avatar image. `personality_name` is an avatar KEY — a display
        name (cash/regular) or a `personality_id` slug (tournaments) — resolved to
        the canonical `personality_id` (v147). The upsert dedups on
        `(personality_id, emotion)`. A key that matches no persona is skipped (an
        avatar can't be keyed without a pid)."""
        pid = self._resolve_avatar_pid(personality_name)
        if pid is None:
            logger.warning(
                "save_avatar_image: %r matches no persona — skipping (avatars are "
                "keyed by personality_id)", personality_name
            )
            return
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO avatar_images
                (personality_id, emotion, image_data, content_type,
                 width, height, file_size,
                 full_image_data, full_width, full_height, full_file_size, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
                (
                    pid,
                    emotion,
                    image_data,
                    content_type,
                    width,
                    height,
                    len(image_data),
                    full_image_data,
                    full_width,
                    full_height,
                    len(full_image_data) if full_image_data else None,
                ),
            )

    def load_avatar_image(self, personality_name: str, emotion: str) -> Optional[bytes]:
        """Load avatar image data. `personality_name` is an avatar key (name or
        pid) resolved to the canonical `personality_id`."""
        pid = self._resolve_avatar_pid(personality_name)
        if pid is None:
            return None
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT image_data FROM avatar_images "
                "WHERE personality_id = ? AND emotion = ?",
                (pid, emotion),
            )

            row = cursor.fetchone()
            return row[0] if row else None

    def load_avatar_image_with_metadata(
        self, personality_name: str, emotion: str
    ) -> Optional[Dict[str, Any]]:
        """Load avatar image with metadata from database."""
        pid = self._resolve_avatar_pid(personality_name)
        if pid is None:
            return None
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT image_data, content_type, width, height, file_size "
                "FROM avatar_images WHERE personality_id = ? AND emotion = ?",
                (pid, emotion),
            )

            row = cursor.fetchone()
            if not row:
                return None

            return {
                'image_data': row['image_data'],
                'content_type': row['content_type'],
                'width': row['width'],
                'height': row['height'],
                'file_size': row['file_size'],
            }

    def load_full_avatar_image(self, personality_name: str, emotion: str) -> Optional[bytes]:
        """Load full uncropped avatar image from database."""
        pid = self._resolve_avatar_pid(personality_name)
        if pid is None:
            return None
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT full_image_data FROM avatar_images "
                "WHERE personality_id = ? AND emotion = ?",
                (pid, emotion),
            )

            row = cursor.fetchone()
            return row[0] if row and row[0] else None

    def load_full_avatar_image_with_metadata(
        self, personality_name: str, emotion: str
    ) -> Optional[Dict[str, Any]]:
        """Load full avatar image with metadata from database."""
        pid = self._resolve_avatar_pid(personality_name)
        if pid is None:
            return None
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT full_image_data, content_type, full_width, full_height, "
                "full_file_size FROM avatar_images "
                "WHERE personality_id = ? AND emotion = ?",
                (pid, emotion),
            )

            row = cursor.fetchone()
            if not row or not row['full_image_data']:
                return None

            return {
                'image_data': row['full_image_data'],
                'content_type': row['content_type'],
                'width': row['full_width'],
                'height': row['full_height'],
                'file_size': row['full_file_size'],
            }

    def has_full_avatar_image(self, personality_name: str, emotion: str) -> bool:
        """Check if a full avatar image exists for the given personality and emotion."""
        pid = self._resolve_avatar_pid(personality_name)
        if pid is None:
            return False
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM avatar_images "
                "WHERE personality_id = ? AND emotion = ? AND full_image_data IS NOT NULL",
                (pid, emotion),
            )
            return cursor.fetchone() is not None

    def has_avatar_image(self, personality_name: str, emotion: str) -> bool:
        """Check if an avatar image exists for the given personality and emotion."""
        pid = self._resolve_avatar_pid(personality_name)
        if pid is None:
            return False
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM avatar_images WHERE personality_id = ? AND emotion = ?",
                (pid, emotion),
            )
            return cursor.fetchone() is not None

    def get_available_avatar_emotions(self, personality_name: str) -> List[str]:
        """Get list of emotions that have avatar images for a personality."""
        pid = self._resolve_avatar_pid(personality_name)
        if pid is None:
            return []
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT emotion FROM avatar_images "
                "WHERE personality_id = ? ORDER BY emotion",
                (pid,),
            )
            return [row[0] for row in cursor.fetchall()]

    def has_all_avatar_emotions(self, personality_name: str) -> bool:
        """Check if a personality has all 6 emotion avatars."""
        emotions = self.get_available_avatar_emotions(personality_name)
        required = {'confident', 'happy', 'thinking', 'nervous', 'angry', 'shocked'}
        return required.issubset(set(emotions))

    def delete_avatar_images(self, personality_name: str) -> int:
        """Delete all avatar images for a personality.

        Returns:
            Number of images deleted
        """
        pid = self._resolve_avatar_pid(personality_name)
        if pid is None:
            return 0
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM avatar_images WHERE personality_id = ?",
                (pid,),
            )
            return cursor.rowcount

    def list_personalities_with_avatars(self) -> List[Dict[str, Any]]:
        """Get list of all personalities that have at least one avatar image.
        Keyed by `personality_id` (v147); the display name is joined from
        `personalities` for the response (falls back to the id if unmatched)."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT a.personality_id AS pid,
                       COALESCE(p.name, a.personality_id) AS display_name,
                       COUNT(*) as emotion_count
                FROM avatar_images a
                LEFT JOIN personalities p ON p.personality_id = a.personality_id
                GROUP BY a.personality_id
                ORDER BY display_name
            """)
            return [
                {
                    'personality_id': row['pid'],
                    'personality_name': row['display_name'],
                    'emotion_count': row['emotion_count'],
                }
                for row in cursor.fetchall()
            ]

    def get_avatar_stats(self) -> Dict[str, Any]:
        """Get statistics about avatar images in the database."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) as count FROM avatar_images")
            total_count = cursor.fetchone()['count']

            cursor = conn.execute("SELECT SUM(file_size) as total_size FROM avatar_images")
            total_size = cursor.fetchone()['total_size'] or 0

            cursor = conn.execute(
                "SELECT COUNT(DISTINCT personality_id) as count FROM avatar_images"
            )
            personality_count = cursor.fetchone()['count']

            cursor = conn.execute("""
                SELECT COUNT(*) as count FROM (
                    SELECT personality_id FROM avatar_images
                    GROUP BY personality_id
                    HAVING COUNT(DISTINCT emotion) = 6
                )
            """)
            complete_count = cursor.fetchone()['count']

            return {
                'total_images': total_count,
                'total_size_bytes': total_size,
                'total_size_mb': round(total_size / (1024 * 1024), 2),
                'personality_count': personality_count,
                'complete_personality_count': complete_count,
            }

    # --- Reference Image CRUD ---

    def save_reference_image(
        self,
        reference_id: str,
        image_data: bytes,
        width: int,
        height: int,
        content_type: str,
        source: str,
        original_url: Optional[str] = None,
    ) -> None:
        """Save a reference image to the database."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO reference_images (id, image_data, width, height, content_type, source, original_url)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (reference_id, image_data, width, height, content_type, source, original_url),
            )

    def get_reference_image(self, reference_id: str) -> Optional[Dict[str, Any]]:
        """Load a reference image by ID.

        Returns:
            Dict with image_data and content_type, or None if not found.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT image_data, content_type FROM reference_images WHERE id = ?
            """,
                (reference_id,),
            )

            row = cursor.fetchone()
            if not row:
                return None

            return {'image_data': row['image_data'], 'content_type': row['content_type']}

    def assign_avatar(self, personality_name: str, emotion: str, image_data: bytes) -> None:
        """Assign an avatar image to a personality, updating if one already exists.
        `personality_name` is an avatar key (display name or `personality_id`)
        resolved to the canonical `personality_id` (v147). A key that matches no
        persona is skipped."""
        pid = self._resolve_avatar_pid(personality_name)
        if pid is None:
            logger.warning(
                "assign_avatar: %r matches no persona — skipping (avatars are keyed "
                "by personality_id)", personality_name
            )
            return
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT id FROM avatar_images WHERE personality_id = ? AND emotion = ?",
                (pid, emotion),
            )

            existing = cursor.fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE avatar_images
                    SET image_data = ?, content_type = 'image/png',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """,
                    (image_data, existing['id']),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO avatar_images (personality_id, emotion, image_data, content_type)
                    VALUES (?, ?, ?, 'image/png')
                """,
                    (pid, emotion, image_data),
                )
