"""Repository for the v100 `sandboxes` table.

A **sandbox** is a save-file: one bundled world-state for cash-mode
runtime AI state (bankrolls, lobby tables, idle pool, activity
history). In v1 each user gets exactly one default sandbox, created
on first cash-mode access. The data model already admits multi-
sandbox-per-owner, shared sandboxes (N owners ↔ 1 sandbox), and
admin-provided templates without further migration; see
`docs/plans/CASH_MODE_PER_PLAYER_SANDBOX_HANDOFF.md` for the design.

The repo owns id generation (opaque UUID4) — callers never construct
their own. Decoupling sandbox identity from auth identity (`owner_id`)
keeps the future "rename / archive / fork" flows clean and avoids the
hash-based renaming dance the first time multi-sandbox UI ships.

`SandboxState` mirrors the row shape; `archived_at IS NULL` is the
"live sandbox" sentinel, which most production reads filter on via
`list_for_owner(include_archived=False)`.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from poker.repositories.base_repository import BaseRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SandboxState:
    """One row of the `sandboxes` table.

    Frozen so test fixtures can hash / equality-check sandbox values.
    `archived_at` is None for live sandboxes; soft-delete sets it to
    the archive timestamp without dropping the row (archived sandboxes
    can still be referenced by historical ledger entries — destroying
    the row would dangle those FKs).
    """

    sandbox_id: str
    owner_id: str
    name: str
    created_at: datetime
    archived_at: Optional[datetime] = None


def _parse_timestamp(value) -> Optional[datetime]:
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


class SandboxRepository(BaseRepository):
    """CRUD for `sandboxes`.

    Four operations: `create`, `load`, `list_for_owner`, `archive`.
    No hard delete; archived sandboxes are soft-removed from the
    default list view but stay in the table for audit traceability.

    Schema is created by `SchemaManager.ensure_schema()` (v100
    migration); this class only touches data.
    """

    def create(self, owner_id: str, name: str = "My Casino") -> SandboxState:
        """Create a new sandbox for `owner_id`. Returns the full state.

        Generates a fresh opaque UUID4 — callers must NOT construct
        their own ids. The decoupling from auth identity keeps the
        future multi-sandbox UI clean (rename / fork / share don't
        require renaming the sandbox_id).
        """
        sandbox_id = str(uuid.uuid4())
        now = datetime.utcnow()
        state = SandboxState(
            sandbox_id=sandbox_id,
            owner_id=owner_id,
            name=name,
            created_at=now,
            archived_at=None,
        )
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO sandboxes
                    (sandbox_id, owner_id, name, created_at, archived_at)
                VALUES (?, ?, ?, ?, NULL)
                """,
                (
                    state.sandbox_id,
                    state.owner_id,
                    state.name,
                    state.created_at.isoformat(),
                ),
            )
        logger.info(
            "[SANDBOX] Created sandbox_id=%r for owner=%r (name=%r)",
            sandbox_id,
            owner_id,
            name,
        )
        return state

    def load(self, sandbox_id: str) -> Optional[SandboxState]:
        """Load one sandbox by id, or None if not found.

        Returns the row regardless of archived state; archive filtering
        is a list-time concern.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT sandbox_id, owner_id, name, created_at, archived_at
                FROM sandboxes WHERE sandbox_id = ?
                """,
                (sandbox_id,),
            ).fetchone()
            if not row:
                return None
            return _row_to_state(row)

    def list_for_owner(
        self,
        owner_id: str,
        *,
        include_archived: bool = False,
    ) -> List[SandboxState]:
        """All sandboxes owned by `owner_id`, oldest first.

        `include_archived=False` (default) uses the partial index on
        live sandboxes; passing True falls back to the full scan
        (acceptable — owner-scoped queries are inherently small).
        """
        with self._get_connection() as conn:
            if include_archived:
                rows = conn.execute(
                    """
                    SELECT sandbox_id, owner_id, name, created_at, archived_at
                    FROM sandboxes WHERE owner_id = ?
                    ORDER BY created_at ASC
                    """,
                    (owner_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT sandbox_id, owner_id, name, created_at, archived_at
                    FROM sandboxes
                    WHERE owner_id = ? AND archived_at IS NULL
                    ORDER BY created_at ASC
                    """,
                    (owner_id,),
                ).fetchall()
            return [_row_to_state(r) for r in rows]

    def list_all(
        self,
        *,
        include_archived: bool = False,
    ) -> List[SandboxState]:
        """Every sandbox in the database, oldest first.

        For the admin chip-ledger view's sandbox dropdown — owner
        scoping doesn't apply there. `include_archived=False` keeps
        the dropdown clean; admins who need a historical row can
        flip to True.
        """
        with self._get_connection() as conn:
            if include_archived:
                rows = conn.execute(
                    """
                    SELECT sandbox_id, owner_id, name, created_at, archived_at
                    FROM sandboxes
                    ORDER BY created_at ASC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT sandbox_id, owner_id, name, created_at, archived_at
                    FROM sandboxes
                    WHERE archived_at IS NULL
                    ORDER BY created_at ASC
                    """
                ).fetchall()
            return [_row_to_state(r) for r in rows]

    def archive(self, sandbox_id: str, *, now: Optional[datetime] = None) -> bool:
        """Soft-delete: stamp `archived_at`. Returns True if updated.

        Idempotent — re-archiving a row updates the timestamp rather
        than failing. The row stays in the table for audit / ledger
        FK traceability; only `list_for_owner` filters it out by
        default.
        """
        if now is None:
            now = datetime.utcnow()
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE sandboxes SET archived_at = ? WHERE sandbox_id = ?",
                (now.isoformat(), sandbox_id),
            )
            return cursor.rowcount > 0


def _row_to_state(row) -> SandboxState:
    created_at = _parse_timestamp(row["created_at"])
    if created_at is None:
        raise ValueError(f"sandboxes row {row['sandbox_id']!r} has unparseable created_at")
    return SandboxState(
        sandbox_id=row["sandbox_id"],
        owner_id=row["owner_id"],
        name=row["name"],
        created_at=created_at,
        archived_at=_parse_timestamp(row["archived_at"]),
    )
