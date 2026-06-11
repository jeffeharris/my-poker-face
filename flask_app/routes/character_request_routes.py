"""Public endpoint for the marketing site's "suggest a character" form.

The Astro marketing site (marketing/) posts visitor suggestions here: a
character name plus an optional email to be notified when that character is
added to the game. This is a human-reviewed suggestion queue — we deliberately
do NOT run a live AI generator on the public site (abuse + cost). Adding a
vetted persona is a trivial config edit, after which we can email the requester.

Public, unauthenticated, and CSRF-exempt (see flask_app/csrf.py): the static
marketing page has no CSRF cookie/SPA wrapper, and this is a low-value append.
"""

import logging
import re
import sqlite3

from flask import Blueprint, jsonify, request

from ..config import DB_PATH

logger = logging.getLogger(__name__)

character_request_bp = Blueprint('character_request', __name__)

_MAX_CHARACTER_LEN = 80
_MAX_EMAIL_LEN = 120
_MAX_SOURCE_LEN = 40
# Loose sanity check only — real validation is "did we get a reply".
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Lazily create the suggestion table (kept out of the schema chain — it is
    a standalone marketing capture, not game state)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS character_requests (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            character  TEXT NOT NULL,
            email      TEXT,
            source     TEXT,
            ip         TEXT,
            status     TEXT NOT NULL DEFAULT 'new',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


@character_request_bp.route('/api/character-requests', methods=['POST'])
def create_character_request():
    """Store a visitor's character suggestion (+ optional notify email).

    Request body: { "character": str, "email": str|null, "source": str|null }
    Returns: { "success": true } on store, 400 on invalid input.
    """
    data = request.get_json(silent=True) or {}

    character = (data.get('character') or '').strip()
    email = (data.get('email') or '').strip()
    source = (data.get('source') or '').strip()[:_MAX_SOURCE_LEN]

    if not character:
        return jsonify({'success': False, 'error': 'A character name is required.'}), 400
    if len(character) > _MAX_CHARACTER_LEN:
        return jsonify({'success': False, 'error': 'That name is too long.'}), 400
    if email:
        if len(email) > _MAX_EMAIL_LEN or not _EMAIL_RE.match(email):
            return jsonify({'success': False, 'error': 'That email looks invalid.'}), 400

    # First proxied client IP if present, else the peer — light dedup/abuse signal.
    ip = (
        (request.headers.get('X-Forwarded-For', '') or request.remote_addr or '')
        .split(',')[0]
        .strip()
    )

    try:
        with sqlite3.connect(DB_PATH) as conn:
            _ensure_table(conn)
            conn.execute(
                'INSERT INTO character_requests (character, email, source, ip) VALUES (?, ?, ?, ?)',
                (character, email or None, source or None, ip or None),
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to store character request: {e}")
        return jsonify({'success': False, 'error': 'Could not save your suggestion.'}), 500

    logger.info(f"Character request stored: {character!r} (email={'yes' if email else 'no'})")
    return jsonify({'success': True}), 201
