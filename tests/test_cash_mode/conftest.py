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

from typing import Any, Dict, Optional

import pytest

from poker.poker_player import AIPokerPlayer
from tests.conftest import load_personality_from_json

# `CASH_LEAVE_NARRATIVE_DISABLED` is set in the top-level
# `tests/conftest.py` so it applies to every cash-related test module,
# not just this package. Don't duplicate it here.


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


@pytest.fixture(autouse=True)
def reset_presence_cutover_flags():
    """Isolate cash tests from the ambient cutover flags.

    `PRESENCE_SHADOW_WRITE_ENABLED` / `PRESENCE_AUTHORITY_ENABLED` /
    `CHIP_CUSTODY_ENABLED` read the environment at import (so a dev/prod
    container can opt in). When the suite runs INSIDE a container that has a
    flip enabled, that env would leak into every test — e.g. shadow tests that
    only set the shadow flag would silently run under authority (where the
    call-site reconcile self-disables), failing for the wrong reason; likewise
    a chip-custody-on container would make conservation tests see unexpected
    `ai_buy_in`/`ai_cash_out` rows. Force all OFF before each test; tests that
    exercise a mode set the flag explicitly (which runs after this fixture and
    wins)."""
    import cash_mode.economy_flags as ef

    prior_shadow = ef.PRESENCE_SHADOW_WRITE_ENABLED
    prior_authority = ef.PRESENCE_AUTHORITY_ENABLED
    prior_custody = ef.CHIP_CUSTODY_ENABLED
    ef.PRESENCE_SHADOW_WRITE_ENABLED = False
    ef.PRESENCE_AUTHORITY_ENABLED = False
    ef.CHIP_CUSTODY_ENABLED = False
    try:
        yield
    finally:
        ef.PRESENCE_SHADOW_WRITE_ENABLED = prior_shadow
        ef.PRESENCE_AUTHORITY_ENABLED = prior_authority
        ef.CHIP_CUSTODY_ENABLED = prior_custody
