"""Cash-mode test fixtures.

`isolate_personality_generator` (autouse): protects against zombie
personality writes to the production DB. `AIPokerPlayer.__init__`
calls `_load_personality_config` which uses a class-level
`PersonalityGenerator` singleton that, when unset, lazy-initializes
against `/app/data/poker_games.db`. Any tempdb-scoped test that
instantiates `AIPokerPlayer(name)` for a name not in the production DB
ends up triggering an LLM call and persisting a row in the real DB
(e.g. `Pers0`..`Pers11` rows seeded by `test_lobby_seat_chip_conservation`
leaking through tertiary controller-construction paths).

The fixture swaps the singleton for a JSON-backed stub for the
duration of each test, then restores the prior value.

`test_cash_sit_route` and similar tests already do this manually in
their `setUp`/`tearDownClass`; this fixture covers tests that don't
do it explicitly. Tests that need a DB-backed generator can override
the singleton inside their own setup (the fixture's tearDown restores
whatever was there before the test, not whatever the test installed).
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import pytest

from poker.poker_player import AIPokerPlayer
from tests.conftest import load_personality_from_json


# Disable cash_mode/leave_narrative LLM calls during the cash-mode
# test suite. The lobby's leave-event emission queues a fire-and-forget
# LLM call to generate in-character exit commentary; in tests that
# exercise the lobby refresh path, those calls would burn real tokens
# (gpt-5-nano FAST tier) without contributing to test signal. Setting
# this env var early — before any cash_mode import that snapshots it —
# turns `queue_leave_comment` into a no-op.
os.environ.setdefault("CASH_LEAVE_NARRATIVE_DISABLED", "1")


class _JSONOnlyPersonalityGenerator:
    """Minimal PersonalityGenerator stand-in for cash-mode tests.

    Implements only the `get_personality(name, ...)` method that
    `AIPokerPlayer._load_personality_config` calls. Reads from
    `personalities.json` via the shared `load_personality_from_json`
    helper; falls back to the helper's default config for unknown
    names. No LLM calls, no DB writes.
    """

    def get_personality(
        self,
        name: str,
        description: Optional[str] = None,
        force_generate: bool = False,
        owner_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return load_personality_from_json(name)


@pytest.fixture(autouse=True)
def isolate_personality_generator():
    prior = AIPokerPlayer._personality_generator
    AIPokerPlayer._personality_generator = _JSONOnlyPersonalityGenerator()
    try:
        yield
    finally:
        AIPokerPlayer._personality_generator = prior
