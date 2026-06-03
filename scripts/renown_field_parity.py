#!/usr/bin/env python3
"""Parity gate: the PROD field loader == the offline oracle, on the real DB.

`poker.repositories.renown_field_repository.RenownFieldRepository._build_inputs`
is a production port of `scripts/renown_v2_rung2.py::load_field`. This script
runs BOTH against the same live main-worktree DB (read-only `immutable=1`) and
asserts the shared (non-scalp) `RenownInputsV2` fields match per entity — the
decisive validation that the port is faithful before anything reads it live.

Scalps are the one driver the prod loader adds on top of the oracle (the
oracle's --from-db read predates the cash_scalps table), so they're reported
separately, not diffed.

Run (host, read-only):  python3 scripts/renown_field_parity.py [sandbox_id]
"""

from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

# The real `poker/__init__.py` pulls in the LLM/anthropic stack (not installed
# on the host). Register lightweight fake parent packages pointing at the real
# dirs so the two repo modules load directly with correct package context,
# skipping the heavy __init__. cash_mode.prestige imports cleanly (stdlib only).
for _name, _rel in (("poker", "poker"), ("poker.repositories", "poker/repositories")):
    if _name not in sys.modules:
        _mod = types.ModuleType(_name)
        _mod.__path__ = [os.path.join(ROOT, _rel)]
        sys.modules[_name] = _mod

from renown_v2_rung2 import DEFAULT_SANDBOX, HUMAN_ID, connect, load_field  # noqa: E402
RenownFieldRepository = importlib.import_module(
    "poker.repositories.renown_field_repository"
).RenownFieldRepository

# Fields the prod loader and the oracle both produce (scalps excluded — the
# oracle has none). Compared with a tolerance for the float drivers.
SHARED_FIELDS = [
    "breadth_opponents", "total_hands", "wall_clock_hours", "roster_net",
    "peak_net_worth", "ticks_at_number_one", "backing_volume", "backing_profit",
    "stakes_hands", "regard_likability", "regard_respect", "regard_heat",
]
TOL = 1e-9


def _close(a, b):
    if isinstance(a, dict):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= TOL
    return a == b


def main():
    sandbox = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SANDBOX
    con = connect()
    oracle = load_field(con, sandbox)
    # Drive the prod loader with the SAME read-only connection (its _build_inputs
    # is static + connection-injected precisely so the parity gate can do this).
    con.row_factory = sqlite3.Row
    prod = RenownFieldRepository._build_inputs(con, sandbox, HUMAN_ID)
    con.close()

    o_ids, p_ids = set(oracle), set(prod)
    print(f"parity: oracle={len(o_ids)} entities  prod={len(p_ids)} entities")
    if o_ids != p_ids:
        print(f"  ENTITY SET MISMATCH: only-oracle={o_ids - p_ids}  "
              f"only-prod={p_ids - o_ids}")

    mismatches = 0
    scalp_entities = 0
    for eid in sorted(o_ids & p_ids):
        o, p = oracle[eid], prod[eid]
        for f in SHARED_FIELDS:
            ov, pv = getattr(o, f), getattr(p, f)
            if not _close(ov, pv):
                mismatches += 1
                print(f"  MISMATCH {eid[:18]:18} {f}: oracle={ov!r} prod={pv!r}")
        if getattr(p, "scalps", None):
            scalp_entities += 1

    ok = (o_ids == p_ids) and mismatches == 0
    print(f"\nshared-field mismatches: {mismatches}")
    print(f"prod entities with scalps (oracle has none): {scalp_entities}")
    print("VERDICT:", "PASS ✅ — prod loader is faithful to the oracle" if ok
          else "FAIL ❌")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
