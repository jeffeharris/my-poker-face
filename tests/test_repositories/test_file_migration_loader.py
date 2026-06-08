"""Behavioural tests for the per-file, applied-set migration loader.

These guard the two properties that motivated replacing the legacy integer
chain (docs/plans/SCHEMA_BASELINE_PLAN.md):

  1. Parallel-authored migrations apply regardless of MERGE ORDER — the
     applied-set model has no high-water-mark, so a late-merged "earlier" id
     still runs. This is the regression guard for the bug that made renumbering
     mandatory on every merge.
  2. Each migration + its applied-set record commit atomically, so a failing
     migration is never recorded (and therefore retried, not skipped).
"""

import sqlite3

from poker.repositories.migration_loader import FileMigrationLoader


def _factory(db_path):
    return lambda: sqlite3.connect(db_path)


def _write(migrations_dir, name, body):
    (migrations_dir / f"{name}.py").write_text(body)


def _table_names(db_path):
    with sqlite3.connect(db_path) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


ADD_A = "def upgrade(conn):\n    conn.execute('CREATE TABLE IF NOT EXISTS a (x INTEGER)')\n"
ADD_B = "def upgrade(conn):\n    conn.execute('CREATE TABLE IF NOT EXISTS b (x INTEGER)')\n"


def test_applies_all_and_is_idempotent(tmp_path):
    mig = tmp_path / "migrations"
    mig.mkdir()
    _write(mig, "20260101_0900_add_a", ADD_A)
    _write(mig, "20260101_1000_add_b", ADD_B)
    db = str(tmp_path / "t.db")
    loader = FileMigrationLoader(str(mig))

    applied = loader.run(_factory(db))

    assert applied == ["20260101_0900_add_a", "20260101_1000_add_b"]
    assert {"a", "b"} <= _table_names(db)
    # Re-run: nothing pending.
    assert loader.run(_factory(db)) == []


def test_late_merged_earlier_id_still_applies(tmp_path):
    """The headline guard: a migration merged AFTER a later-id one already ran
    still gets applied. Under the old range(current+1, MAX+1) high-water-mark
    this was silently skipped forever — forcing a renumber on merge."""
    mig = tmp_path / "migrations"
    mig.mkdir()
    db = str(tmp_path / "t.db")
    loader = FileMigrationLoader(str(mig))

    # Branch B (the "later" id) merges and deploys first.
    _write(mig, "20260101_1000_add_b", ADD_B)
    assert loader.run(_factory(db)) == ["20260101_1000_add_b"]
    assert "b" in _table_names(db)

    # Branch A (an "earlier" id) merges afterwards. It must still run.
    _write(mig, "20260101_0900_add_a", ADD_A)
    assert loader.run(_factory(db)) == ["20260101_0900_add_a"]
    assert {"a", "b"} <= _table_names(db)


def test_merge_order_converges_to_same_schema(tmp_path):
    """Two DBs that saw the same migrations in opposite merge orders end with
    identical schema — order-independence, the whole point."""

    def build(order):
        root = tmp_path / f"db_{'_'.join(order)}"
        mig = root / "migrations"
        mig.mkdir(parents=True)
        db = str(root / "t.db")
        loader = FileMigrationLoader(str(mig))
        bodies = {"a": ("20260101_0900_add_a", ADD_A), "b": ("20260101_1000_add_b", ADD_B)}
        for key in order:  # add one file, run, then the next — simulates staggered merges
            name, body = bodies[key]
            _write(mig, name, body)
            loader.run(_factory(db))
        return _table_names(db)

    assert build(["a", "b"]) == build(["b", "a"])


def test_depends_on_orders_before_lexical(tmp_path):
    mig = tmp_path / "migrations"
    mig.mkdir()
    # 'late' has an earlier-sorting id but DEPENDS_ON the later-sorting 'early'.
    _write(
        mig,
        "20260101_0800_needs_base",
        "DEPENDS_ON = '20260101_0900_base'\n"
        "def upgrade(conn):\n"
        "    conn.execute('ALTER TABLE base ADD COLUMN y INTEGER')\n",
    )
    _write(
        mig,
        "20260101_0900_base",
        "def upgrade(conn):\n    conn.execute('CREATE TABLE base (x INTEGER)')\n",
    )
    db = str(tmp_path / "t.db")
    applied = FileMigrationLoader(str(mig)).run(_factory(db))
    # base must run first despite its later id, or the ALTER would explode.
    assert applied == ["20260101_0900_base", "20260101_0800_needs_base"]


def test_failed_migration_is_not_recorded(tmp_path):
    mig = tmp_path / "migrations"
    mig.mkdir()
    _write(mig, "20260101_0900_add_a", ADD_A)
    _write(
        mig,
        "20260101_1000_boom",
        "def upgrade(conn):\n    raise RuntimeError('boom')\n",
    )
    db = str(tmp_path / "t.db")
    loader = FileMigrationLoader(str(mig))

    try:
        loader.run(_factory(db))
        assert False, "expected the failing migration to raise"
    except RuntimeError:
        pass

    with sqlite3.connect(db) as conn:
        recorded = {r[0] for r in conn.execute("SELECT id FROM applied_migrations")}
    # The good one committed; the failing one left no record (so it retries, not skips).
    assert recorded == {"20260101_0900_add_a"}


def test_malformed_filename_raises(tmp_path):
    mig = tmp_path / "migrations"
    mig.mkdir()
    _write(mig, "not_a_valid_id", ADD_A)
    try:
        FileMigrationLoader(str(mig)).discover()
        assert False, "expected a ValueError for the malformed id"
    except ValueError as e:
        assert "id format" in str(e)


def test_missing_directory_is_noop(tmp_path):
    loader = FileMigrationLoader(str(tmp_path / "does_not_exist"))
    assert loader.discover() == []
    assert loader.run(_factory(str(tmp_path / "t.db"))) == []
