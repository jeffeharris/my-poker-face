"""Presence shadow-write divergence audit (cutover Step 1 — the gate for the flip).

Seeds a FRESH ISOLATED sandbox, turns the Presence dual-write shadow ON
(`economy_flags.PRESENCE_SHADOW_WRITE_ENABLED` + wires
`flask_app.extensions.entity_presence_repo` so the sim writers can reach it),
runs the economy sim, then compares the shadow `entity_presence` rows against
the AUTHORITATIVE stores:

  * `cash_tables` seat map   -> SEATED(table_id, seat_index)
  * `cash_idle_pool`         -> IDLE
  * `ai_side_hustle_state`   -> SIDE_HUSTLE
  * `ai_vice_state`          -> VICE

and classifies every (presence vs truth) mismatch. The point of the audit is
NOT "zero divergence" — the shadow is deliberately partial (see
CASH_MODE_PRESENCE_MIGRATION.md §C/§D). It is "every divergence is one of the
KNOWN-BENIGN classes; no UNEXPECTED class appears." Unexpected classes
(SEAT_MISMATCH, MISSING_SEAT) are real wiring gaps that gate the flip.

SAFETY: pass an explicit `--db-path` to a throwaway file. NEVER point this at
`/app/data/poker_games.db` (prod/dev) — it flips a global flag and seeds rows.
Writes a JSON report to `--out` so results are read from a file, not stdout.

Usage (in the backend container):
    docker compose exec backend python -m scripts.validate_presence_shadow \\
        --db-path /tmp/presence_validation.db \\
        --ticks 400 --rng-seed 7 \\
        --out /tmp/presence_audit.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


# --- classification ---------------------------------------------------------
#
# KNOWN-BENIGN: documented, expected gaps of the shadow phase. Their presence
# is fine; the flip will close them by construction (table-as-projection).
#
#   STALE_SEAT            presence=SEATED, truth=IDLE      (seat->IDLE LEAVE not
#                         emitted by lobby reconcile when a seat vacates to the
#                         idle pool — the §C dedup decision; closed at flip)
#   STALE_SEAT_GONE       presence=SEATED, truth=absent    (entity left the
#                         sandbox entirely; no LEAVE/GO_OFFLINE shadowed)
#   OFFGRID_NOT_TRACKED   truth=SIDE_HUSTLE/VICE, presence!=that (START_* only
#                         legal from IDLE; a broke AI going off-grid straight
#                         from unseated has no IDLE shadow row -> swallowed §D)
#   MISSING_IDLE          presence=absent/other, truth=IDLE (idle adds are NOT
#                         shadow-wired at the repo layer by design §C)
#   POOL_BENIGN           presence=POOL, truth=absent       (pool-funded fish
#                         returned to POOL; POOL has no old-store analogue §6.2)
#
# UNEXPECTED (gates the flip): a real wiring bug.
#
#   SEAT_MISMATCH         presence=SEATED@X, truth=SEATED@Y (a move not tracked)
#   MISSING_SEAT          presence=absent/other, truth=SEATED (a SIT never fired)
#   OFFGRID_STALE         presence=SIDE_HUSTLE/VICE, truth=neither (END_OFFGRID
#                         not shadowed back to IDLE)
#   OTHER                 anything the classifier didn't anticipate

BENIGN = {"STALE_SEAT", "STALE_SEAT_GONE", "OFFGRID_NOT_TRACKED",
          "MISSING_IDLE", "POOL_BENIGN"}


def _truth_states(repos: dict, sandbox_id: str, now: datetime) -> Dict[str, Dict[str, Any]]:
    """entity_id -> {state, table_id, seat_index, in_stores:[...]} from the
    authoritative stores. `in_stores` records EVERY store the entity appears in
    so we can also surface authoritative split-brain (the pre-existing bug class
    the machine is meant to kill)."""
    from cash_mode.presence import ai_entity_id, player_entity_id

    cash_table_repo = repos["cash_table_repo"]
    side_hustle_repo = repos["side_hustle_state_repo"]
    vice_repo = repos["vice_state_repo"]

    seated: Dict[str, Any] = {}
    for table in cash_table_repo.list_all_tables(sandbox_id=sandbox_id):
        for idx, slot in enumerate(table.seats):
            kind = slot.get("kind")
            if kind == "ai":
                pid = slot.get("personality_id")
                if pid:
                    seated[ai_entity_id(pid)] = (table.table_id, idx)
            elif kind == "human":
                owner = slot.get("owner_id") or slot.get("player_id") or slot.get("user_id")
                if owner:
                    seated[player_entity_id(owner)] = (table.table_id, idx)

    idle = {ai_entity_id(e.personality_id) for e in cash_table_repo.list_idle(sandbox_id=sandbox_id)}
    hustle = {ai_entity_id(p) for p in side_hustle_repo.active_pids(sandbox_id=sandbox_id, now=now)}
    vice = {ai_entity_id(p) for p in vice_repo.active_pids(sandbox_id=sandbox_id, now=now)}

    out: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"state": "absent", "table_id": None, "seat_index": None, "in_stores": []}
    )
    # Precedence seated > idle > side_hustle > vice (matches whereabouts.py).
    for eid, (tid, sidx) in seated.items():
        out[eid] = {"state": "seated", "table_id": tid, "seat_index": sidx, "in_stores": ["seated"]}
    for eid in idle:
        out[eid]["in_stores"].append("idle")
        if out[eid]["state"] == "absent":
            out[eid].update(state="idle", table_id=None, seat_index=None)
    for eid in hustle:
        out[eid]["in_stores"].append("side_hustle")
        if out[eid]["state"] == "absent":
            out[eid].update(state="side_hustle")
    for eid in vice:
        out[eid]["in_stores"].append("vice")
        if out[eid]["state"] == "absent":
            out[eid].update(state="vice")
    return dict(out)


def _classify(presence: Optional[Dict[str, Any]], truth: Dict[str, Any]) -> str:
    p_state = presence["state"] if presence else "absent"
    p_tid = presence.get("table_id") if presence else None
    p_sidx = presence.get("seat_index") if presence else None
    t_state = truth["state"]
    t_tid = truth.get("table_id")
    t_sidx = truth.get("seat_index")

    if p_state == t_state:
        if p_state == "seated":
            if (p_tid, p_sidx) == (t_tid, t_sidx):
                return "MATCH"
            return "SEAT_MISMATCH"
        return "MATCH"

    # presence says SEATED
    if p_state == "seated":
        if t_state == "idle":
            return "STALE_SEAT"
        if t_state == "absent":
            return "STALE_SEAT_GONE"
        if t_state == "seated":  # unreachable (handled above) but defensive
            return "SEAT_MISMATCH"
        return "OTHER"  # presence SEATED, truth offgrid -> unexpected

    # truth says SEATED, presence does not
    if t_state == "seated":
        return "MISSING_SEAT"

    # truth off-grid, presence not
    if t_state in ("side_hustle", "vice"):
        return "OFFGRID_NOT_TRACKED"

    # truth idle, presence not seated
    if t_state == "idle":
        return "MISSING_IDLE"

    # presence off-grid, truth neither seated/offgrid
    if p_state in ("side_hustle", "vice"):
        return "OFFGRID_STALE"

    # presence POOL with no authoritative analogue
    if p_state == "pool" and t_state == "absent":
        return "POOL_BENIGN"

    return "OTHER"


def _audit_once(repos: dict, sandbox_id: str, now: datetime) -> dict:
    """Compare entity_presence vs the authoritative stores at clock `now`."""
    truth = _truth_states(repos, sandbox_id, now)
    presence_rows = {
        s.entity_id: {"state": s.state.value, "table_id": s.table_id, "seat_index": s.seat_index}
        for s in repos["entity_presence_repo"].list_for_sandbox(sandbox_id)
    }
    all_entities = set(truth) | set(presence_rows)
    counts: Counter = Counter()
    unexpected: List[dict] = []
    authoritative_split: List[dict] = []
    for eid in sorted(all_entities):
        t = truth.get(eid, {"state": "absent", "table_id": None, "seat_index": None, "in_stores": []})
        p = presence_rows.get(eid)
        cls = _classify(p, t)
        counts[cls] += 1
        if cls not in BENIGN and cls != "MATCH":
            unexpected.append({"entity": eid, "class": cls, "presence": p, "truth": t})
        if len(t.get("in_stores", [])) > 1:
            authoritative_split.append({"entity": eid, "in_stores": t["in_stores"]})
    return {
        "now": now.isoformat(),
        "n_entities_compared": len(all_entities),
        "n_presence_rows": len(presence_rows),
        "n_truth_entities": len([e for e, t in truth.items() if t["state"] != "absent"]),
        "classification_counts": dict(sorted(counts.items())),
        "n_unexpected": len(unexpected),
        "unexpected_samples": unexpected[:50],
        "n_authoritative_split_brain": len(authoritative_split),
        "authoritative_split_samples": authoritative_split[:50],
    }


# Deterministic clock origin so off-grid durations (relative to `now`) and the
# audit clock line up reproducibly across runs.
_BASE_START = datetime(2026, 1, 1, 0, 0, 0)


def run(db_path: str, ticks: int, rng_seed: int, out_path: str,
        hand_sim_prob: Optional[float], live_fill_prob: Optional[float],
        checkpoints: int) -> dict:
    # Hard guard: never run against the live DBs.
    forbidden = {"/app/data/poker_games.db",
                 str(Path(_project_root) / "data" / "poker_games.db")}
    if db_path in forbidden:
        raise SystemExit(f"REFUSING to run against live DB {db_path!r} — pass a throwaway --db-path")

    from poker.repositories import create_repos
    from scripts.seed_sim_sandbox import seed_sim_sandbox
    from cash_mode import economy_flags, presence_shadow
    from cash_mode.sim_runner import SimConfig, run_sim
    import flask_app.extensions as extensions

    sandbox_id = seed_sim_sandbox(name="presence-shadow-validation",
                                  owner_id="sim-bot", db_path=db_path)
    logger.info("Seeded sandbox %s", sandbox_id)

    repos = create_repos(db_path)

    # Turn the shadow ON and wire the repo the sim writers resolve via
    # flask_app.extensions (None outside the Flask app -> would silently no-op).
    economy_flags.PRESENCE_SHADOW_WRITE_ENABLED = True
    extensions.entity_presence_repo = repos["entity_presence_repo"]
    assert presence_shadow.is_enabled(), "flag flip did not take"
    logger.info("Shadow ENABLED; entity_presence_repo wired -> %s", db_path)

    tick_seconds = 8
    checkpoints = max(1, checkpoints)
    seg_ticks = max(1, ticks // checkpoints)
    snapshots: List[dict] = []
    agg_counts: Counter = Counter()
    all_unexpected: List[dict] = []
    classes_seen: set = set()
    total_wall = 0.0
    cumulative = 0  # ticks already simulated

    for seg in range(checkpoints):
        seg_start = _BASE_START + timedelta(seconds=cumulative * tick_seconds)
        config = SimConfig(
            sandbox_id=sandbox_id,
            num_ticks=seg_ticks,
            tick_seconds=tick_seconds,
            start_at=seg_start,
            rng_seed=rng_seed,
            progress_every=0,
            **({"hand_sim_prob": hand_sim_prob} if hand_sim_prob is not None else {}),
            **({"live_fill_prob": live_fill_prob} if live_fill_prob is not None else {}),
        )
        result = run_sim(config, repos=repos)
        total_wall += result.wall_seconds
        cumulative += seg_ticks
        # The segment's last simulated clock; next segment continues one tick on.
        now = datetime.fromisoformat(result.final_now) if result.final_now else seg_start
        snap = _audit_once(repos, sandbox_id, now)
        snap["segment"] = seg
        snap["cumulative_ticks"] = cumulative
        snapshots.append(snap)
        agg_counts.update(snap["classification_counts"])
        classes_seen.update(snap["classification_counts"].keys())
        for u in snap["unexpected_samples"]:
            all_unexpected.append({"segment": seg, **u})
        logger.info("checkpoint %d/%d @%d ticks: %s unexpected=%d",
                    seg + 1, checkpoints, cumulative,
                    snap["classification_counts"], snap["n_unexpected"])
        cumulative += 1  # gap so next segment's clock is strictly after this audit

    report = {
        "sandbox_id": sandbox_id,
        "db_path": db_path,
        "total_ticks": cumulative,
        "checkpoints": checkpoints,
        "seg_ticks": seg_ticks,
        "rng_seed": rng_seed,
        "wall_seconds": round(total_wall, 1),
        "classes_ever_seen": sorted(classes_seen),
        "offgrid_exercised": bool({"OFFGRID_NOT_TRACKED", "OFFGRID_STALE"} & classes_seen)
                             or any("side_hustle" in str(s) or "vice" in str(s) for s in snapshots),
        "aggregate_classification_counts": dict(sorted(agg_counts.items())),
        "n_unexpected_total": len(all_unexpected),
        "unexpected_samples": all_unexpected[:100],
        "checkpoint_snapshots": snapshots,
        "verdict": "PASS — only known-benign divergences across all checkpoints"
                   if not all_unexpected
                   else f"FAIL — {len(all_unexpected)} unexpected divergence(s)",
    }

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(report, indent=2))
    logger.info("Wrote audit report -> %s", out_path)
    logger.info("VERDICT: %s", report["verdict"])
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", required=True, help="Throwaway DB path (NOT the live DB)")
    ap.add_argument("--ticks", type=int, default=400)
    ap.add_argument("--rng-seed", type=int, default=7)
    ap.add_argument("--hand-sim-prob", type=float, default=None)
    ap.add_argument("--live-fill-prob", type=float, default=None)
    ap.add_argument("--checkpoints", type=int, default=1,
                    help="Audit after each of N equal sim segments (captures "
                         "transient off-grid/idle states, not just end-state)")
    ap.add_argument("--out", default="/tmp/presence_audit.json")
    args = ap.parse_args()
    report = run(args.db_path, args.ticks, args.rng_seed, args.out,
                 args.hand_sim_prob, args.live_fill_prob, args.checkpoints)
    return 0 if report["n_unexpected_total"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
