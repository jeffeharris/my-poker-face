"""Forward schema migrations (post-v154 baseline).

Each migration is a single file named ``YYYYMMDD_HHMM_short_slug.py`` exposing
``def upgrade(conn): ...``. They are discovered and applied by
``poker.repositories.migration_loader.FileMigrationLoader`` using the
applied-set model — see that module's docstring for the authoring contract and
the rationale (parallel-worktree merges without renumbering).

Do NOT add an integer ``_migrate_vN`` here; that legacy chain is frozen at v154.
"""
