"""Guards for the test-only schema-template fast path.

`SchemaManager.ensure_schema()` (poker/repositories/schema_manager.py) seeds a
fresh test DB from a cached template instead of re-running the full migration
chain when POKER_TEST_SCHEMA_TEMPLATE=1. These tests pin the two safety
invariants the speedup relies on:

  1. a seeded DB is schema-identical to a real (migration-built) DB, and
  2. a non-empty DB (e.g. a migration test's pre-built old schema) is never
     overwritten by the seed.

See docs/plans/TEST_WAIT_TIME_REDUCTION.md.
"""

import sqlite3

import pytest

import poker.repositories.schema_manager as sm
from poker.repositories.schema_manager import SCHEMA_VERSION, SchemaManager

pytestmark = pytest.mark.integration


def _schema_fingerprint(db_path):
    """All user schema objects + recorded migration versions for a DB."""
    with sqlite3.connect(db_path) as conn:
        objects = sorted(
            conn.execute(
                "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
        )
        versions = sorted(r[0] for r in conn.execute("SELECT version FROM schema_version"))
    return objects, versions


def test_seeded_schema_matches_real_build(tmp_path, monkeypatch):
    """A template-seeded DB must be byte-for-byte the schema of a real build."""
    # Real build with the fast path OFF: runs the full migration chain.
    monkeypatch.setenv("POKER_TEST_SCHEMA_TEMPLATE", "0")
    real = str(tmp_path / "real.db")
    SchemaManager(real).ensure_schema()

    # Fast path ON with a fresh (uncached) template for this test.
    monkeypatch.setenv("POKER_TEST_SCHEMA_TEMPLATE", "1")
    monkeypatch.setattr(sm, "_test_schema_template_path", None)
    primer = str(tmp_path / "primer.db")  # first empty build -> snapshots template
    SchemaManager(primer).ensure_schema()
    seeded = str(tmp_path / "seeded.db")  # this one is seeded from the template
    SchemaManager(seeded).ensure_schema()

    objects, versions = _schema_fingerprint(real)
    assert versions[-1] == SCHEMA_VERSION
    assert _schema_fingerprint(seeded) == (objects, versions)


def test_nonempty_db_is_not_seeded(tmp_path, monkeypatch):
    """A DB that already has a (partial/old) schema must never be overwritten."""
    monkeypatch.setenv("POKER_TEST_SCHEMA_TEMPLATE", "1")
    # Prime a template so a seed *would* happen if the guard were wrong.
    monkeypatch.setattr(sm, "_test_schema_template_path", None)
    SchemaManager(str(tmp_path / "primer.db")).ensure_schema()

    # A migration-test-style DB: a single bespoke table, no full schema.
    target = str(tmp_path / "old.db")
    with sqlite3.connect(target) as conn:
        conn.execute("CREATE TABLE legacy_only (id INTEGER PRIMARY KEY)")

    mgr = SchemaManager(target)
    assert mgr._db_is_empty() is False  # the guard sees the bespoke table
    assert mgr._maybe_seed_from_template() is False  # so it refuses to seed

    # The bespoke table survives (DB was migrated forward, not replaced).
    mgr.ensure_schema()
    with sqlite3.connect(target) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "legacy_only" in names
