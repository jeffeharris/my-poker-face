"""Guard: the conftest economy-flag test baseline must cover the registry.

`tests/conftest.py` forces a deterministic test baseline for the economy flags so
a developer's armed `.env` (or a prod container's env) can't pollute the suite
(the casino/rake/reserve failures that were green in CI but red locally). Every
non-locked economy flag must be accounted for in exactly one of two sets:

  * `RESET_ECONOMY_FLAGS` — forced OFF before each test.
  * `TEST_BASELINE_ON_ECONOMY_FLAGS` — intentionally left ON (the always-on
    shipped features).

GRADUATED / RETIRED flags are env-locked (no override possible), so they can't be
polluted and are excluded.

If this fails, you changed the economy flags in `core/feature_flags.py` — assign
each new/changed non-locked flag to exactly one of the two sets in conftest:
  * tests should see it OFF -> RESET_ECONOMY_FLAGS
  * tests should see it ON  -> TEST_BASELINE_ON_ECONOMY_FLAGS
"""

from __future__ import annotations

from core.feature_flags import REGISTRY, Stage
from tests.conftest import RESET_ECONOMY_FLAGS, TEST_BASELINE_ON_ECONOMY_FLAGS

_ECON_OWNER = "cash_mode.economy"
_LOCKED = (Stage.GRADUATED, Stage.RETIRED)


def _non_locked_economy_flags() -> set[str]:
    return {f.name for f in REGISTRY.values() if f.owner == _ECON_OWNER and f.stage not in _LOCKED}


def test_baseline_partitions_every_non_locked_economy_flag():
    expected = _non_locked_economy_flags()
    assert expected, "no non-locked economy flags found — registry/owner changed?"
    reset = set(RESET_ECONOMY_FLAGS)
    baseline_on = set(TEST_BASELINE_ON_ECONOMY_FLAGS)

    overlap = reset & baseline_on
    assert not overlap, f"flags in BOTH reset and baseline-on sets: {overlap}"

    covered = reset | baseline_on
    assert covered == expected, (
        "conftest test-baseline sets are out of sync with the non-locked economy "
        "flags in core/feature_flags.py.\n"
        f"  uncovered (add to RESET or BASELINE_ON): {expected - covered}\n"
        f"  stale (remove from conftest): {covered - expected}"
    )


def test_no_duplicates():
    assert len(RESET_ECONOMY_FLAGS) == len(set(RESET_ECONOMY_FLAGS))
    assert len(TEST_BASELINE_ON_ECONOMY_FLAGS) == len(set(TEST_BASELINE_ON_ECONOMY_FLAGS))
