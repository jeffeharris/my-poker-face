"""Guard: the conftest economy-flag reset list must cover every flag.

`tests/conftest.py::RESET_ECONOMY_FLAGS` drives the autouse fixture that forces
every `cash_mode.economy_flags` toggle OFF so a developer's armed `.env` can't
pollute the suite (the casino/rake/reserve failures that were green in CI but
red locally). That defence only holds if the list stays complete, so this test
parses the `_env_flag("NAME", ...)` calls straight from the module source and
asserts the reset list matches them exactly.

If this fails, you changed the flags in `cash_mode/economy_flags.py` — update
`RESET_ECONOMY_FLAGS` to match:
  * new flag    -> add it (else its `.env` value leaks into tests again)
  * flag removed -> drop it (a flag retirement)
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.conftest import RESET_ECONOMY_FLAGS

_SOURCE = Path(__file__).resolve().parent.parent / "cash_mode" / "economy_flags.py"
_ENV_FLAG = re.compile(r'_env_flag\(\s*"(\w+)"')


def _flag_names_from_source() -> set[str]:
    return set(_ENV_FLAG.findall(_SOURCE.read_text()))


def test_reset_list_covers_every_economy_flag():
    from_source = _flag_names_from_source()
    assert from_source, "no _env_flag(...) calls parsed — regex or source path is wrong"
    reset = set(RESET_ECONOMY_FLAGS)
    assert reset == from_source, (
        "tests/conftest.py::RESET_ECONOMY_FLAGS is out of sync with "
        "cash_mode/economy_flags.py.\n"
        f"  missing from reset list (NEW flag — add it): {from_source - reset}\n"
        f"  stale in reset list (retired flag — drop it): {reset - from_source}"
    )


def test_reset_list_has_no_duplicates():
    assert len(RESET_ECONOMY_FLAGS) == len(set(RESET_ECONOMY_FLAGS))
