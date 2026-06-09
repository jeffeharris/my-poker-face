"""Forward schema migrations: per-file, applied-set model.

Replaces the legacy monotonic-integer chain in ``schema_manager.py`` for every
migration authored after the v154 baseline. See
``docs/plans/SCHEMA_BASELINE_PLAN.md``.

Why per-file + applied-set (the two root-cause fixes for parallel-worktree pain):

  * **Per-file** — each migration is its own module under ``migrations/``, so two
    branches authoring migrations in parallel touch *different files* and merge
    cleanly. There is no shared ``SCHEMA_VERSION`` constant or ``migrations`` dict
    literal to conflict on.
  * **Applied-set, not high-water-mark** — a DB tracks the *set* of applied
    migration ids (``applied_migrations`` table) and runs any discovered file not
    in that set. A late-merged migration whose id sorts "earlier" than ones
    already applied still runs, so merging never requires renumbering *for
    correctness*. The legacy ``range(current+1, SCHEMA_VERSION+1)`` loop silently
    skipped such a migration forever — that skip-bug is what made renumbering
    mandatory, not merely tidy.

Authoring a migration — drop a file in ``poker/repositories/migrations/`` named::

    YYYYMMDD_HHMM_short_slug.py

exposing::

    def upgrade(conn: sqlite3.Connection) -> None: ...

and optionally::

    DESCRIPTION = "one-line summary"
    DEPENDS_ON = "20260607_1430_other"   # str | list[str]; rarely needed

Make ``upgrade`` idempotent (guard with ``PRAGMA table_info`` checks) — the same
discipline the legacy chain used, so a re-run or a partially-built DB is safe.

Ordering is lexicographic by id (the ``YYYYMMDD_HHMM`` prefix sorts
chronologically), refined by a topological pass that honours ``DEPENDS_ON``. For
purely additive migrations (new tables/columns/indexes) order is irrelevant and
``DEPENDS_ON`` is unnecessary.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Callable, Dict, List, Set

logger = logging.getLogger(__name__)

# A migration id is the file stem: YYYYMMDD_HHMM_slug (lowercase slug).
_ID_RE = re.compile(r"^\d{8}_\d{4}_[a-z0-9][a-z0-9_]*$")


@dataclass(frozen=True)
class Migration:
    """One discovered migration file."""

    id: str
    path: str
    # upgrade(conn) -> None by contract; widened to `object` because a function
    # loaded via getattr has an inferred `-> object` return (it returns None).
    upgrade: Callable[[sqlite3.Connection], object]
    description: str = ""
    depends_on: tuple = ()


class FileMigrationLoader:
    """Discovers and applies per-file migrations using the applied-set model.

    Stateless apart from the directory it reads. Inject the directory so tests
    can point it at a temp dir; production points it at
    ``poker/repositories/migrations``.
    """

    def __init__(self, migrations_dir: str):
        self.migrations_dir = migrations_dir

    # ---- discovery -------------------------------------------------------

    def discover(self) -> List[Migration]:
        """Load every migration file, returned in apply order.

        Raises ``ValueError`` on a malformed filename, a missing/uncallable
        ``upgrade``, an unknown ``DEPENDS_ON`` target, or a dependency cycle —
        all author errors that should fail loudly at startup, never silently.
        """
        if not os.path.isdir(self.migrations_dir):
            return []
        migs: List[Migration] = []
        for fname in sorted(os.listdir(self.migrations_dir)):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            stem = fname[:-3]
            if not _ID_RE.match(stem):
                raise ValueError(
                    f"Migration file {fname!r} does not match the required id "
                    f"format YYYYMMDD_HHMM_slug.py"
                )
            migs.append(self._load(stem, os.path.join(self.migrations_dir, fname)))
        return self._order(migs)

    @staticmethod
    def _load(stem: str, path: str) -> Migration:
        spec = importlib.util.spec_from_file_location(f"_poker_mig_{stem}", path)
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise ValueError(f"Could not load migration {stem!r} from {path!r}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        upgrade = getattr(module, "upgrade", None)
        if not callable(upgrade):
            raise ValueError(f"Migration {stem!r} has no callable upgrade(conn)")
        dep = getattr(module, "DEPENDS_ON", ())
        dep = (dep,) if isinstance(dep, str) else tuple(dep)
        description = str(getattr(module, "DESCRIPTION", ""))
        return Migration(
            id=stem, path=path, upgrade=upgrade, description=description, depends_on=dep
        )

    @staticmethod
    def _order(migs: List[Migration]) -> List[Migration]:
        """Stable lexicographic order, refined by a DEPENDS_ON topological sort."""
        by_id: Dict[str, Migration] = {m.id: m for m in migs}
        for m in migs:
            for d in m.depends_on:
                if d not in by_id:
                    raise ValueError(f"Migration {m.id!r} DEPENDS_ON unknown id {d!r}")

        ordered: List[Migration] = []
        state: Dict[str, int] = {}  # id -> 1 visiting, 2 done

        def visit(m: Migration, stack: List[str]) -> None:
            s = state.get(m.id)
            if s == 2:
                return
            if s == 1:
                raise ValueError("Cyclic migration DEPENDS_ON: " + " -> ".join(stack + [m.id]))
            state[m.id] = 1
            for d in sorted(m.depends_on):
                visit(by_id[d], stack + [m.id])
            state[m.id] = 2
            ordered.append(m)

        for m in sorted(migs, key=lambda x: x.id):
            visit(m, [])
        return ordered

    # ---- application -----------------------------------------------------

    @staticmethod
    def _ensure_table(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS applied_migrations (
                id TEXT PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                description TEXT
            )
            """
        )

    @classmethod
    def applied_ids(cls, conn: sqlite3.Connection) -> Set[str]:
        cls._ensure_table(conn)
        return {row[0] for row in conn.execute("SELECT id FROM applied_migrations")}

    def run(self, connection_factory: Callable[[], sqlite3.Connection]) -> List[str]:
        """Apply every discovered migration whose id is not yet recorded.

        Each migration runs in its own transaction *together with* its
        ``applied_migrations`` insert, so a crash never leaves a migration
        applied-but-unrecorded (which would re-run it next boot). Returns the
        ids applied this run, in apply order.
        """
        migs = self.discover()
        if not migs:
            return []

        conn = connection_factory()
        try:
            applied = self.applied_ids(conn)
        finally:
            conn.close()

        pending = [m for m in migs if m.id not in applied]
        done: List[str] = []
        for m in pending:
            conn = connection_factory()
            try:
                with conn:
                    self._ensure_table(conn)
                    m.upgrade(conn)
                    conn.execute(
                        "INSERT INTO applied_migrations (id, description) VALUES (?, ?)",
                        (m.id, m.description),
                    )
                logger.info(
                    "Applied file migration %s: %s",
                    m.id,
                    m.description or "(no description)",
                )
                done.append(m.id)
            except Exception:
                logger.error("File migration %s failed", m.id, exc_info=True)
                raise
            finally:
                conn.close()
        return done
