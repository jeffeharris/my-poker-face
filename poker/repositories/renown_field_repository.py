"""Batched field-input read for the Renown-v2 scorer.

The v2 quadrant is **field-relative** — `quadrant_label_relative` needs the
field-wide `high_cut`, and the scalp driver weights by the *victim's* field
percentile. So scoring the human's v2 renown requires building
`RenownInputsV2` for the WHOLE field (every entity with cash activity in the
sandbox, plus the human) and running `score_renown_field` over it.

This repo is the production port of the offline oracle
`scripts/renown_v2_rung2.py::load_field` (the instrument the v2 balance was
validated on). It reads the same tables with the same semantics so the prod
field score matches the oracle on the same DB — a parity test pins that.

Why one repo over many tables: the field build is a handful of BATCHED scans
bucketed in memory (NOT per-entity queries — `ticks_at_#1` is inherently
field-relative: it needs every entity's net worth per holdings tick, so the
standing pass can't be sliced per entity). Reading them over one connection
keeps it to ~6 queries total. It runs on the world ticker's prestige
recompute, which is throttled to once per `PRESTIGE_INTERVAL_SECONDS` (300s)
per sandbox — a ~0.5s field build at that cadence is a rounding error.

Volume is **wall-clock denominated** (the validated anti-treadmill governor):
an entity's presence = its count of distinct `holdings_snapshots` ticks (a
clock-time proxy independent of how many hands it crammed into those ticks),
falling back to hand-count only when it has no holdings rows.

Read-only. See docs/plans/CASH_MODE_PLAYER_PRESTIGE.md (Renown v2) and
docs/plans/RENOWN_V2_HANDOFF.md.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict

from cash_mode.prestige import RenownInputsV2
from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class RenownFieldRepository(BaseRepository):
    """Batched `{entity_id: RenownInputsV2}` read for a sandbox's renown field."""

    def build_inputs(self, sandbox_id: str, human_id: str) -> Dict[str, RenownInputsV2]:
        """Return one `RenownInputsV2` per field entity, keyed by raw entity id.

        Field = every observer with cash pair-stat activity in `sandbox_id`,
        plus `human_id` (so the human is always scored even before they've
        logged a co-seat). Ids are raw (no `ai:`/`player:` prefix), matching
        `cash_pair_stats`/`stakes`/`cash_scalps`. Each driver degrades to its
        zero-value if its source table is empty; the method never raises on
        missing data (it runs on the world ticker).
        """
        with self._get_connection() as conn:
            return self._build_inputs(conn, sandbox_id, human_id)

    @staticmethod
    def _build_inputs(conn, sandbox_id: str, human_id: str) -> Dict[str, RenownInputsV2]:
        """Pure builder over an open connection — the parity target.

        Kept static + connection-injected so the parity test can drive it with
        a read-only `immutable=1` handle on the real DB, exactly like the
        oracle, with no Flask/extensions wiring. Each source read degrades to
        its zero-value on any error (a missing table on an unmigrated DB, a
        lock) so a single broken source never breaks the world tick — the same
        per-source contract as `cash_mode.prestige.compute_prestige`.
        """
        c = conn.cursor()

        def _rows(sql, params=()):
            """Fetch all rows for `sql`; [] on any error (degrade-to-zero)."""
            try:
                return c.execute(sql, params).fetchall()
            except Exception:
                logger.warning("renown_field: source read failed: %s", sql[:60])
                return []

        # --- entities = everyone with cash pair-stat activity here (+ human) ---
        #   breadth_opponents[obs][opp] = hands;  roster_net[obs] = Σ pnl
        pair: Dict[str, Dict[str, int]] = defaultdict(dict)
        roster_net: Dict[str, float] = defaultdict(float)
        for obs, opp, pnl, hands in _rows(
            "SELECT observer_id, opponent_id, cumulative_pnl, hands_played_cash "
            "FROM cash_pair_stats WHERE sandbox_id=? AND hands_played_cash>0",
            (sandbox_id,),
        ):
            pair[obs][opp] = hands
            roster_net[obs] += (pnl or 0)
        entities = set(pair) | {human_id}

        # --- holdings: peak net worth + time-at-#1 (per-tick net-worth rank) +
        # presence (distinct tick count = the wall-clock proxy). holdings ids
        # are prefixed ('ai:deadpool'/'player:guest_jeff'); strip to join the
        # raw-id tables. ---
        peak: Dict[str, float] = defaultdict(float)
        tick_best: Dict[str, tuple] = {}  # captured_at -> (best_net, entity)
        presence: Dict[str, set] = defaultdict(set)  # entity -> {distinct ticks}
        for ts, raw_eid, nw in _rows(
            "SELECT captured_at, entity_id, net_worth FROM holdings_snapshots "
            "WHERE sandbox_id=?",
            (sandbox_id,),
        ):
            eid = raw_eid.split(":", 1)[-1]
            nw = nw or 0
            if nw > peak[eid]:
                peak[eid] = nw
            presence[eid].add(ts)
            cur = tick_best.get(ts)
            if cur is None or nw > cur[0]:
                tick_best[ts] = (nw, eid)
        ticks_at_one: Dict[str, int] = defaultdict(int)
        for _, eid in tick_best.values():
            ticks_at_one[eid] += 1

        # --- backing (staker perspective): volume + settled profit. NOT
        # sandbox-scoped — mirrors the validated oracle (backing reputation is
        # cross-sandbox). ---
        backing_vol: Dict[str, float] = defaultdict(float)
        backing_profit: Dict[str, float] = defaultdict(float)
        for sid, principal, status, payout in _rows(
            "SELECT staker_id, principal, status, staker_payout FROM stakes "
            "WHERE staker_id IS NOT NULL AND staker_id != 'anonymous'"
        ):
            backing_vol[sid] += (principal or 0)
            if status == "settled" and payout is not None:
                backing_profit[sid] += (payout - (principal or 0))

        # --- per-tier hands + tenure, per owner (in practice the human; AIs
        # have no cash_sessions rows). ---
        stakes_hands: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for owner, label, hands in _rows(
            "SELECT owner_id, stake_label, hands_played FROM cash_sessions "
            "WHERE sandbox_id=? AND ended_at IS NOT NULL",
            (sandbox_id,),
        ):
            stakes_hands[owner][label] += (hands or 0)

        # --- inbound regard edges (relationship_states is not sandbox-scoped). ---
        inbound: Dict[str, list] = defaultdict(list)  # target -> [(lik,resp,heat)]
        for tgt, lik, resp, heat in _rows(
            "SELECT opponent_id, likability, respect, heat FROM relationship_states"
        ):
            inbound[tgt].append((lik, resp, heat))

        # --- scalps: {eliminator: {victim: count}} for this sandbox. The
        # cash_scalps tracker is live; the oracle's --from-db read predates it
        # (scalps=0 there), so this is the one driver the prod loader adds on
        # top of the oracle's shared inputs. ---
        scalps: Dict[str, Dict[str, int]] = defaultdict(dict)
        for elim, vic, count in _rows(
            "SELECT eliminator_id, victim_id, count FROM cash_scalps "
            "WHERE sandbox_id=? AND count>0",
            (sandbox_id,),
        ):
            scalps[elim][vic] = count

        # --- assemble RenownInputsV2 per entity ---
        field: Dict[str, RenownInputsV2] = {}
        for eid in entities:
            opps = pair.get(eid, {})
            total_hands = sum(opps.values())
            edges = inbound.get(eid, [])
            if edges:
                rl = sum(lk - 0.5 for lk, _, _ in edges) / len(edges)
                rr = sum(rp - 0.5 for _, rp, _ in edges) / len(edges)
                rh = sum(ht for _, _, ht in edges) / len(edges)
            else:
                rl = rr = rh = 0.0
            field[eid] = RenownInputsV2(
                label=eid[:22],
                breadth_opponents=dict(opps),
                total_hands=total_hands,
                # presence ticks = wall-clock proxy; fall back to hands when an
                # entity has no holdings rows (keeps a brand-new entity nonzero).
                wall_clock_hours=float(len(presence.get(eid, ())) or total_hands),
                roster_net=float(roster_net.get(eid, 0)),
                peak_net_worth=peak.get(eid, 0.0),
                ticks_at_number_one=ticks_at_one.get(eid, 0),
                backing_volume=backing_vol.get(eid, 0.0),
                backing_profit=backing_profit.get(eid, 0.0),
                stakes_hands=dict(stakes_hands.get(eid, {})),
                scalps=dict(scalps.get(eid, {})),
                regard_likability=rl,
                regard_respect=rr,
                regard_heat=rh,
            )
        return field
