"""Async-friends mode: game membership, per-turn state, and push devices.

Foundation schema for "poker by mail" — friends sharing ONE game and acting
turn-by-turn over days (still playable live if everyone is online). Three
purely-additive pieces, none of which touch existing single-human play:

  * ``game_members`` — the multi-human membership/seat ledger. Today a game has
    exactly one human, authorized by ``games.owner_id``; an async game has N
    humans, each owning a seat. The authoritative seat->user identity still
    travels INSIDE ``games.game_state_json`` (each ``Player.seat_id`` is a
    ``HumanSeat(owner_id)``); this table is the human-readable index + the
    invite ledger that authorization and the "my async games" lobby read.

  * ``user_devices`` — APNs (and later FCM/web/email) delivery targets, so a
    player can be notified it's their turn while the app is closed. Keyed
    ``(user_id, token)`` so a user with several devices gets several rows and a
    re-registered token upserts in place.

  * ``games`` turn columns — denormalized turn state so the lobby badge and the
    notify decision are a cheap indexed read instead of parsing the state JSON.
    ``game_state_json`` stays the source of truth; these are a read cache +
    notification trigger. ``turn_deadline`` is stored for future auto-fold but
    is NOT enforced yet. ``last_notified_turn_at`` dedupes pushes (one per turn).
    ``is_async`` flags games that should advance via the background orbit + fire
    notifications rather than assuming a live socket.

Additive, idempotent, forward-only — every statement guards with
``IF NOT EXISTS`` / a ``PRAGMA table_info`` check, so a re-run or a
partially-built DB is safe.
"""

import sqlite3

DESCRIPTION = "Async-friends: game_members, user_devices, games turn/async columns"


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Add ``column`` to ``table`` if it isn't already present (idempotent)."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def upgrade(conn: sqlite3.Connection) -> None:
    # --- Multi-human membership / seat ledger ---
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS game_members (
            game_id      TEXT NOT NULL,
            user_id      TEXT NOT NULL,
            seat_index   INTEGER,                  -- index into players tuple; NULL until claimed
            role         TEXT NOT NULL DEFAULT 'member',  -- 'owner' | 'member'
            status       TEXT NOT NULL DEFAULT 'joined',  -- 'invited' | 'joined' | 'left'
            display_name TEXT,
            joined_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (game_id, user_id),
            FOREIGN KEY (game_id) REFERENCES games(game_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_game_members_game ON game_members(game_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_game_members_user ON game_members(user_id)")

    # --- Invite ledger: a share code maps to an open seat in a game ---
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS game_invites (
            code        TEXT PRIMARY KEY,
            game_id     TEXT NOT NULL,
            created_by  TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at  TIMESTAMP,
            max_uses    INTEGER DEFAULT 0,         -- 0 = unlimited
            used_count  INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (game_id) REFERENCES games(game_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_game_invites_game ON game_invites(game_id)")

    # --- Push notification delivery targets ---
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_devices (
            user_id    TEXT NOT NULL,
            platform   TEXT NOT NULL,              -- 'ios' (PoC); 'android'/'web'/'email' later
            token      TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, token)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_devices_user ON user_devices(user_id)")

    # --- Denormalized turn state + async flag on games ---
    _add_column(conn, "games", "is_async", "BOOLEAN DEFAULT 0")
    _add_column(conn, "games", "current_turn_user_id", "TEXT")
    _add_column(conn, "games", "turn_started_at", "TIMESTAMP")
    _add_column(conn, "games", "turn_deadline", "TIMESTAMP")  # stored, not enforced yet
    _add_column(conn, "games", "last_notified_turn_at", "TIMESTAMP")  # notify dedupe
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_games_turn_user ON games(current_turn_user_id)"
    )
