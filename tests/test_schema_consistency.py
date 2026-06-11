"""Schema consistency guard (post-v157 squash).

After the baseline squash (docs/plans/SCHEMA_BASELINE_PLAN.md), fresh installs build
the head schema directly from the GENERATED baseline (``schema_baseline.py`` via
``SchemaManager._init_db``) and are stamped at the baseline version, so the archived
legacy v1..v157 integer chain (``legacy_migrations.py``) runs only for a restored
pre-baseline backup.

These tests guard that the baseline stays faithful to that archived chain: if the two
ever diverge, fresh installs would get a different schema than an upgraded old DB.
"""

from __future__ import annotations

import re
import sqlite3

from poker.repositories.legacy_migrations import LegacyMigrations
from poker.repositories.schema_manager import SCHEMA_VERSION, SchemaManager


def _schema_objects(db_path: str) -> dict[str, str]:
    """``{'<type>:<name>': normalized_sql}`` for every user schema object.

    Normalization collapses whitespace and erases two cosmetic differences that
    carry no schema meaning: the ``IF NOT EXISTS`` clause (the baseline adds it for
    idempotent replay; the chain's CREATEs mostly omit it) and identifier quoting (a
    historical table REBUILD left ``CREATE TABLE "opponent_models"`` quoted, while the
    baseline emits it bare). Column definitions and ordering are preserved, so a real
    divergence still fails.
    """
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL"
        ).fetchall()
    out: dict[str, str] = {}
    for typ, name, sql in rows:
        s = re.sub(r"\s+", " ", sql).strip()
        s = re.sub(r"(?i)\bIF NOT EXISTS ", "", s)
        s = s.replace('"', "").replace("`", "")
        out[f"{typ}:{name}"] = s
    return out


def test_init_db_builds_and_stamps_baseline(tmp_path):
    """A fresh ``_init_db`` lands at the baseline version, stamped once, idempotently."""
    db = str(tmp_path / "t.db")
    sm = SchemaManager(db)
    sm._init_db()
    first = _schema_objects(db)
    with sqlite3.connect(db) as conn:
        (version,) = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        (rows,) = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()
    assert version == SCHEMA_VERSION
    assert rows == 1, "baseline must stamp exactly one version row"

    # Idempotent: a second build changes nothing and does not re-stamp.
    sm._init_db()
    assert _schema_objects(db) == first
    with sqlite3.connect(db) as conn:
        (rows,) = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()
    assert rows == 1


def test_baseline_equals_legacy_chain_head(tmp_path):
    """The generated baseline MUST equal the archived chain's head schema.

    Build the head via the baseline, then force a full replay of the v1..vN chain
    over it. Every guarded migration must no-op (the schema is already at head), so
    the schema is unchanged. If the baseline and chain ever diverge — the chain is
    edited, or ``schema_baseline.py`` is regenerated incorrectly — a migration fires
    and this fails. Permanent successor to the pre-squash drift gate.
    """
    db = str(tmp_path / "t.db")
    sm = SchemaManager(db)
    sm._init_db()
    before = _schema_objects(db)

    # Clear the stamped versions so the chain replays in full without PK conflicts;
    # this drops rows only — the schema (sqlite_master) is untouched.
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM schema_version")
    LegacyMigrations().run(sm._get_connection, 0, SCHEMA_VERSION)
    after = _schema_objects(db)

    missing = sorted(before.keys() - after.keys())
    added = sorted(after.keys() - before.keys())
    differing = sorted(k for k in before.keys() & after.keys() if before[k] != after[k])
    problems = []
    if missing:
        problems.append(f"chain replay dropped vs baseline: {missing}")
    if added:
        problems.append(f"chain replay added beyond the baseline: {added}")
    if differing:
        problems.append(f"chain replay changed DDL vs baseline: {differing}")
    assert not problems, (
        "the legacy chain and the generated baseline have diverged — regenerate "
        "schema_baseline.py via scripts/_gen_schema_baseline.py:\n  " + "\n  ".join(problems)
    )


def test_unversioned_nonempty_db_migrates_via_chain(tmp_path):
    """A populated DB whose version stamp is gone (an ancient pre-versioning DB, or a
    wiped schema_version) must be brought up via the legacy chain — NOT silently
    stamped at the baseline and frozen, which would leave old table shapes unmigrated.

    Regression guard for the post-cutover routing: version-0 + non-empty -> chain.
    """
    db = str(tmp_path / "t.db")
    sm = SchemaManager(db)
    sm.ensure_schema()  # build to head
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM schema_version")  # drop the stamp; tables remain

    sm.ensure_schema()  # must route through _init_db(no stamp/seed) + the chain

    with sqlite3.connect(db) as conn:
        (version,) = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert version == SCHEMA_VERSION, "an unversioned populated DB must reach head via the chain"
    assert {"games", "groups"} <= tables
