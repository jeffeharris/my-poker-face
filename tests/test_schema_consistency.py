"""Schema consistency guard for the eventual migration squash.

Connected to TRIAGE T3-17 (no migration framework) / T3-44 (schema_manager.py
monolith) and docs/plans/SCHEMA_BASELINE_PLAN.md.

A fresh database is built two ways that MUST agree:

  A. ``_init_db()`` alone — the canonical head DDL.
  B. ``_init_db()`` + the full v1..vN migration chain — what every fresh install
     actually runs *today*. A brand-new DB has schema version 0, so
     ``_run_migrations()`` replays the whole chain over the just-built schema.
     The migrations are guarded no-ops (e.g. ``if 'owner_id' not in columns``),
     so they normally change nothing — but if any migration adds a column/table/
     index that ``_init_db`` does NOT also create, the fresh install gets it from
     the chain, masking the omission in ``_init_db``.

When the chain is squashed/baselined, fresh installs will run ``_init_db()``
ALONE. Anything path B adds beyond path A would then silently disappear from new
installs. This test makes that gap a hard failure: A and B must be identical
before the chain can be safely deleted. A green result is the squash precondition.
"""

from __future__ import annotations

import re
import sqlite3

import pytest

from poker.repositories.schema_manager import SCHEMA_VERSION, SchemaManager


def _schema_objects(db_path: str) -> dict[str, str]:
    """{'<type>:<name>': normalized_sql} for every user schema object.

    Both paths share the same ``_init_db()`` CREATE TABLE statements as their
    base, so logically-equal tables have byte-identical ``sql`` here — column
    *ordering* can't cause a false positive. A diff therefore means the chain
    genuinely creates an object (or column, via a fired ALTER) that ``_init_db``
    does not.
    """
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL"
        ).fetchall()
    return {f"{typ}:{name}": re.sub(r"\s+", " ", sql).strip() for typ, name, sql in rows}


def _init_only(path: str) -> None:
    SchemaManager(path)._init_db()


def _init_plus_migrations(path: str) -> None:
    sm = SchemaManager(path)
    sm._init_db()
    sm._run_migrations()


def test_fresh_install_replays_chain_to_head(tmp_path):
    """Sanity: init + chain lands a fresh DB at SCHEMA_VERSION."""
    db = str(tmp_path / "full.db")
    _init_plus_migrations(db)
    with sqlite3.connect(db) as conn:
        (version,) = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    assert version == SCHEMA_VERSION


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known drift: _init_db() is a partial skeleton — ~19 tables, ~41 indexes, "
        "and 12 table shapes are supplied only by the migration chain (cash/ledger/"
        "presence/stakes/prestige/coach/avatars were wired migration-only). This is "
        "the squash precondition (T3-44 / docs/plans/SCHEMA_BASELINE_PLAN.md). When "
        "_init_db is reconciled this test XPASSES and strict=True fails the run — "
        "that is the signal to delete this marker and keep it as a permanent guard."
    ),
)
def test_init_db_matches_full_migration_chain(tmp_path):
    """``_init_db()`` alone must equal ``_init_db()`` + the full chain.

    Squash precondition (T3-44 / docs/plans/SCHEMA_BASELINE_PLAN.md): once the
    chain is deleted, new installs run ``_init_db()`` only, so the two paths must
    already be identical. Any object listed below is something the migration chain
    adds that ``_init_db`` is missing — back-port it into ``_init_db`` before
    squashing.
    """
    init_only = str(tmp_path / "init_only.db")
    full = str(tmp_path / "full.db")
    _init_only(init_only)
    _init_plus_migrations(full)

    a = _schema_objects(init_only)
    b = _schema_objects(full)

    missing_from_init = sorted(b.keys() - a.keys())  # chain creates, _init_db lacks
    extra_in_init = sorted(a.keys() - b.keys())  # _init_db has, full path lacks
    differing = sorted(k for k in a.keys() & b.keys() if a[k] != b[k])

    problems = []
    if missing_from_init:
        problems.append(f"chain adds but _init_db lacks: {missing_from_init}")
    if extra_in_init:
        problems.append(f"in _init_db but absent after full chain: {extra_in_init}")
    if differing:
        problems.append(f"DDL differs between the two build paths: {differing}")

    assert not problems, (
        "_init_db() and the migration chain disagree on the head schema; "
        "the chain cannot be squashed until these are reconciled:\n  " + "\n  ".join(problems)
    )
