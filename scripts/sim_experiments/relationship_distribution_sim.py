"""Measure the AI<->AI relationship distribution produced by the cash lobby sim.

Runs many multiway hands through `cash_mode.full_sim.play_one_hand` against a
THROWAWAY temp DB (never the live one), with the Phase 3 relationship wiring
active, then reports:

  - how often each relationship event fires (instruments record_event),
  - the distribution of heat / respect / likability across all pair rows
    (mean/median/spread, % still at default, % saturated),
  - per-pair extremes (biggest grudges / bonds).

Usage (inside backend container):
    docker compose exec -T backend python -m scripts.sim_experiments.relationship_distribution_sim
Env knobs: REL_SIM_HANDS (default 3000), REL_SIM_BB (100), REL_SIM_BUYIN (5000),
REL_SIM_SEED (123).
"""

from __future__ import annotations

import collections
import os
import random
import statistics
import tempfile

PERSONAS = [
    "Napoleon",
    "Abraham Lincoln",
    "Buddha",
    "Bob Ross",
    "Jay Gatsby",
    "Shakespeare",
]


def _pct(xs, p):
    if not xs:
        return 0.0
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def _summarize(name, xs, default):
    moved = [x for x in xs if abs(x - default) > 1e-9]
    line = (
        f"{name:11s} n={len(xs):4d} moved={len(moved):4d} "
        f"min={min(xs):+.3f} p25={_pct(xs,25):+.3f} med={statistics.median(xs):+.3f} "
        f"p75={_pct(xs,75):+.3f} p95={_pct(xs,95):+.3f} max={max(xs):+.3f} "
        f"mean={statistics.fmean(xs):+.3f}"
    )
    return line


def main() -> None:
    hands = int(os.environ.get("REL_SIM_HANDS", "3000"))
    big_blind = int(os.environ.get("REL_SIM_BB", "100"))
    buy_in = int(os.environ.get("REL_SIM_BUYIN", "5000"))
    seed = int(os.environ.get("REL_SIM_SEED", "123"))

    # Quiet the per-hand memory_manager WARNING spam (e.g. the known
    # fold_to_big_bet dict/int gripe) so the summary is readable.
    import logging

    logging.getLogger("poker.memory.memory_manager").setLevel(logging.ERROR)

    tmpdir = tempfile.mkdtemp(prefix="rel_sim_")
    db_path = os.path.join(tmpdir, "rel_sim.db")

    from poker.repositories import create_repos

    create_repos(db_path)  # builds full schema on the throwaway DB

    import cash_mode.full_sim as fs
    from cash_mode.controller_cache import LruControllerCache
    from cash_mode.tables import ai_slot
    from poker.memory.opponent_model import OpponentModelManager
    from poker.repositories.bankroll_repository import BankrollRepository

    # Instrument record_event to count event types as they fire.
    event_counter: collections.Counter = collections.Counter()
    _orig_record = OpponentModelManager.record_event

    def _counting_record(self, actor_id, target_id, event, *a, **k):
        event_counter[getattr(event, "value", str(event))] += 1
        return _orig_record(self, actor_id, target_id, event, *a, **k)

    OpponentModelManager.record_event = _counting_record

    sandbox = "reltune"
    fs._session_memory_managers.pop(sandbox, None)
    fs._session_hand_counters.pop(sandbox, None)

    bankroll = BankrollRepository(db_path)
    cache = LruControllerCache(max_size=20)
    rng = random.Random(seed)

    # Movement proxy: in the real world-tick, the `stake_up` leave term
    # relocates any stack over the table cap to a higher tier. This standalone
    # harness doesn't run movement, so without a proxy a chip leader sits
    # forever and STACK_DOMINANCE saturates. REL_SIM_RELOCATE_AT (multiple of
    # cap, 0 = disabled) reseats an over-cap stack back to a fresh buy-in,
    # standing in for that relocation so the dominance signal is realistic.
    relocate_at = float(os.environ.get("REL_SIM_RELOCATE_AT", "0"))
    relocations = 0

    seats = [ai_slot(p, buy_in) for p in PERSONAS]
    big_pot_hands = 0  # hands that wrote >=1 relationship event

    for i in range(hands):
        before = sum(event_counter.values())
        r = fs.play_one_hand(
            seats,
            big_blind=big_blind,
            rng=rng,
            sandbox_id=sandbox,
            name_for=lambda p: p,
            controller_cache=cache,
            bankroll_repo=bankroll,
            table_max_buy_in=buy_in,
        )
        seats = list(r.new_seats)
        # Rebuy any busted AI seat back to the cap (keeps 6 distinct personas
        # seated and lets a chip leader grow past the STACK_DOMINANCE 1.5x gate).
        for s in seats:
            if s.get("kind") != "ai":
                continue
            if s.get("chips", 0) <= 0:
                s["chips"] = buy_in
            elif relocate_at > 0 and s.get("chips", 0) >= relocate_at * buy_in:
                s["chips"] = buy_in  # "moved up a tier" — reset to a fresh seat
                relocations += 1
        if sum(event_counter.values()) > before:
            big_pot_hands += 1

    OpponentModelManager.record_event = _orig_record

    import sqlite3

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT observer_id, opponent_id, heat, respect, likability "
        "FROM relationship_states"
    ).fetchall()
    cash_pairs = conn.execute("SELECT COUNT(*) FROM cash_pair_stats").fetchone()[0]
    conn.close()

    heat = [r[2] for r in rows]
    respect = [r[3] for r in rows]
    likability = [r[4] for r in rows]

    print("=" * 78)
    print(
        f"REL DIST SIM  hands={hands} bb={big_blind} buyin={buy_in} seed={seed} "
        f"personas={len(PERSONAS)}"
    )
    print(f"db={db_path}")
    print("-" * 78)
    print(
        f"hands with >=1 relationship event (big-pot gate cleared): "
        f"{big_pot_hands}/{hands} ({100.0*big_pot_hands/max(1,hands):.1f}%)"
    )
    print(
        f"relationship_states rows: {len(rows)}   cash_pair_stats rows: {cash_pairs}   "
        f"relocations(stake_up proxy @ {relocate_at}x): {relocations}"
    )
    print("-" * 78)
    print("event frequencies (record_event calls):")
    if event_counter:
        total_ev = sum(event_counter.values())
        for ev, c in event_counter.most_common():
            print(f"  {ev:22s} {c:6d}  ({100.0*c/total_ev:4.1f}%)")
        print(f"  {'TOTAL':22s} {total_ev:6d}")
    else:
        print("  (none fired)")
    print("-" * 78)
    if rows:
        print(_summarize("heat", heat, 0.0))
        print(_summarize("respect", respect, 0.5))
        print(_summarize("likability", likability, 0.5))
        print("-" * 78)
        sat_heat = sum(1 for h in heat if h >= 0.9)
        sat_resp = sum(1 for x in respect if x <= 0.05 or x >= 0.95)
        sat_like = sum(1 for x in likability if x <= 0.05 or x >= 0.95)
        print(
            f"saturated: heat>=0.9 {sat_heat}/{len(rows)}  "
            f"respect@edge {sat_resp}/{len(rows)}  "
            f"likability@edge {sat_like}/{len(rows)}"
        )
        print("-" * 78)
        top_heat = sorted(rows, key=lambda r: r[2], reverse=True)[:5]
        print("biggest grudges (heat):")
        for o, t, h, rs, lk in top_heat:
            print(f"  {o:16s} -> {t:16s} heat={h:+.3f} respect={rs:+.3f} like={lk:+.3f}")
        lo_like = sorted(rows, key=lambda r: r[4])[:5]
        print("lowest likability:")
        for o, t, h, rs, lk in lo_like:
            print(f"  {o:16s} -> {t:16s} like={lk:+.3f} heat={h:+.3f} respect={rs:+.3f}")
        hi_resp = sorted(rows, key=lambda r: r[3], reverse=True)[:5]
        print("highest respect:")
        for o, t, h, rs, lk in hi_resp:
            print(f"  {o:16s} -> {t:16s} respect={rs:+.3f} heat={h:+.3f} like={lk:+.3f}")
    print("=" * 78)


if __name__ == "__main__":
    main()
