"""Unit tests for `build_cash_mode_payload` table-identity fields.

The payload feeds the in-game header location chip + arrival toast, so it
must surface `table_id` / `table_name` for a seated cash session and
degrade to None (rather than raising) for legacy sessions that never
resolved a name. Tournament games get no cash_mode block at all.
"""

from __future__ import annotations

from flask_app.handlers.game_handler import build_cash_mode_payload


class _GameStateStub:
    """Minimal stand-in — the payload only reads `current_ante`."""

    current_ante = 100


def test_payload_surfaces_table_identity():
    game_data = {
        "cash_mode": True,
        "cash_stake_label": "$50",
        "cash_table_id": "cash-table-$50-002",
        "cash_table_name": "The Lodge",
    }
    payload = build_cash_mode_payload(game_data, _GameStateStub())
    assert payload is not None
    assert payload["table_id"] == "cash-table-$50-002"
    assert payload["table_name"] == "The Lodge"
    assert payload["stake_label"] == "$50"


def test_payload_table_identity_defaults_to_none():
    # Legacy cash session: cash_table_id/name never stamped on game_data.
    game_data = {"cash_mode": True, "cash_stake_label": "$2"}
    payload = build_cash_mode_payload(game_data, _GameStateStub())
    assert payload is not None
    assert payload["table_id"] is None
    assert payload["table_name"] is None


def test_payload_none_for_tournament():
    payload = build_cash_mode_payload({}, _GameStateStub())
    assert payload is None
