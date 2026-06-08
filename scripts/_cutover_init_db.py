"""Phase-3 baseline cutover: rewire _init_db to replay the generated baseline.

Replaces the 1,286-line hand-written _init_db skeleton with a baseline replay +
version stamp, and restructures ensure_schema to route sub-baseline DBs through the
archived legacy chain. Idempotent span-based splice with assertions.

Run from repo root:  python scripts/_cutover_init_db.py
"""

SM = "poker/repositories/schema_manager.py"
lines = open(SM).read().splitlines(keepends=True)


def span(def_line):
    """[start, end) line indices of a method: its `def` to the next `    def `."""
    starts = [i for i, ln in enumerate(lines) if ln.startswith(def_line)]
    if len(starts) != 1:
        raise SystemExit(f"expected 1 {def_line!r}, found {len(starts)}")
    s = starts[0]
    for j in range(s + 1, len(lines)):
        if lines[j].startswith("    def "):
            return s, j
    raise SystemExit(f"no method follows {def_line!r}")


NEW_ENSURE = '''    def ensure_schema(self):
        """Create tables and run migrations. Idempotent.

        Post-squash routing:
          * empty / seeded-template / already-at-baseline DB → ``_init_db`` lays the
            head schema directly from the generated baseline and stamps the baseline
            version on a fresh DB, so the legacy chain never runs.
          * existing PRE-baseline DB (e.g. a restored old backup) → the archived
            legacy v1..v157 chain brings it up to the baseline.
        Per-file migrations (applied-set) then run in every case.
        """
        # Test-only fast path: seed a fresh DB from a cached, fully-built template
        # instead of building from scratch. Inert in prod.
        seeded = self._maybe_seed_from_template()
        started_empty = (not seeded) and self._db_is_empty()
        self._enable_wal_mode()
        current = self._get_current_schema_version()
        if seeded or started_empty or current >= SCHEMA_VERSION:
            self._init_db()  # head baseline; CREATE ... IF NOT EXISTS no-ops on a built DB
        else:
            self._run_migrations()  # sub-baseline existing DB → archived legacy chain
        self._run_file_migrations()  # per-file migrations (applied-set model)
        if started_empty:
            self._maybe_save_as_template()

'''

NEW_INIT = '''    def _init_db(self):
        """Build the head schema directly from the generated baseline.

        Replays ``schema_baseline.BASELINE_STATEMENTS`` (every statement is
        ``CREATE ... IF NOT EXISTS``, so this is a no-op on a DB already at head)
        and stamps ``schema_version`` at the baseline on a fresh DB, so the legacy
        v1..v157 chain in ``legacy_migrations.py`` is skipped on fresh installs.

        The baseline is GENERATED from that chain (scripts/_gen_schema_baseline.py)
        and proven equivalent to it by tests/test_schema_consistency.py.
        """
        from poker.repositories.schema_baseline import BASELINE_STATEMENTS, BASELINE_VERSION

        with self._get_connection() as conn:
            for statement in BASELINE_STATEMENTS:
                conn.execute(statement)
            already_versioned = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
            if not already_versioned:
                conn.execute(
                    "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                    (BASELINE_VERSION, f"baseline v{BASELINE_VERSION}"),
                )

'''

# Splice in reverse line order so earlier indices stay valid.
i_init_s, i_init_e = span("    def _init_db(self):")
i_ens_s, i_ens_e = span("    def ensure_schema(self):")
assert i_ens_e <= i_init_s, "(ensure_schema must precede _init_db)"

lines[i_init_s:i_init_e] = [NEW_INIT]
lines[i_ens_s:i_ens_e] = [NEW_ENSURE]

text = "".join(lines)

# Drop the now-unused `import random` (migration methods that used it were extracted).
before = text
text = text.replace("import random\n", "", 1)
assert text != before, "import random not found to remove"

open(SM, "w").write(text)
print(f"schema_manager.py: {text.count(chr(10))} lines after cutover")
