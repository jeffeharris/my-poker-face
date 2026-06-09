"""In-memory per-archetype stat recorder for the background cash sim.

The lobby sim plays full AI-vs-AI hands but is LEAN — it never wires the
decision-analysis repo, so its (perpetual) decision stream would otherwise be
discarded. This recorder tallies the behavioral stats the Archetype Review tool
needs (VPIP/PFR/3-bet/4-bet/fold-to-3bet/AF/AFq/WTSD/W$SD/per-street-AF/
c-bet/fold-to-c-bet/all-in) in memory, per archetype, and flushes them to
`archetype_stat_counts` as deltas every N hands.

Bounded by design: memory is O(archetypes), the DB table is
O(sandboxes × archetypes). It never grows per-hand, so it's safe for a process
that plays hands forever. One recorder per sandbox (module-level cache); the sim
loop is single-threaded per tick so per-hand scratch needs no locking beyond the
cache lookup.

Node classification (rfi / vs_open / vs_3bet / vs_4bet) is derived from the
preflop raise count at the call site — controller-independent, so it works even
if a seat is a non-tiered bot.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_VOLUNTARY = {'call', 'raise', 'all_in'}
_AGGRESSIVE = {'raise', 'all_in'}
_POSTFLOP = {'FLOP', 'TURN', 'RIVER'}

# Flush cadence: accumulate this many hands before writing deltas. Keeps the
# write rate trivial even during catch-up bursts.
_FLUSH_EVERY_HANDS = 100


class ArchetypeStatRecorder:
    """Accumulates per-archetype behavioral counters and flushes deltas."""

    def __init__(self, sandbox_id: str):
        self.sandbox_id = sandbox_id
        # archetype -> {column: running delta since last flush}
        self._totals: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # (archetype, player) -> per-hand booleans, rolled up at end_hand()
        self._hand: Dict[tuple, Dict[str, bool]] = {}
        self._hands_since_flush = 0

    def record_decision(
        self,
        archetype: Optional[str],
        player: str,
        phase: str,
        node: str,
        action: str,
        is_opener: bool = True,
        *,
        is_cbet_opportunity: bool = False,
        is_cbet: bool = False,
        is_facing_cbet: bool = False,
    ) -> None:
        """Record one decision. ``node`` is '' for postflop or one of
        rfi/vs_open/vs_3bet/vs_4bet preflop. No-ops without an archetype.

        ``is_opener`` = the actor made this hand's first preflop raise (RFI). The
        ``vs_3bet`` columns (which drive the fourbet / fold_to_3bet stats) are
        counted ONLY for the opener — facing a 3-bet *as the raiser* is what the
        standard "Fold to 3-Bet" / "4-Bet" stats (and ARCHETYPE_TARGETS) mean. A
        ``vs_3bet`` node reached as a cold-caller is SQUEEZE defence (a different
        stat that folds ~100%); counting it crushed fold_to_3bet for the
        wide-flatting archetypes. vs_open (the 3-bet stat) is unaffected — every
        actor at a vs_open node is facing an open, never the opener.

        C-bet flags (backlog #6, FLOP-only, kw-defaulted for back-compat):
        ``is_cbet_opportunity`` — the actor IS the preflop aggressor and is first
        in on an un-bet flop (the chance to continuation-bet). ``is_cbet`` — that
        opportunity was taken (a flop bet/raise). ``is_facing_cbet`` — a flop
        c-bet has already been made this hand and this (non-aggressor) actor is
        now facing it; ``fold_to_cbet`` is bumped when such an actor folds."""
        if not archetype:
            return
        t = self._totals[archetype]
        scratch = self._hand.setdefault(
            (archetype, player),
            {'vpip': False, 'pfr': False, 'allin': False, 'saw_flop': False},
        )
        if action == 'all_in':
            scratch['allin'] = True
        if phase == 'PRE_FLOP':
            t['pf_decisions'] += 1
            if action in _VOLUNTARY:
                scratch['vpip'] = True
            if action in _AGGRESSIVE:
                scratch['pfr'] = True
            if node == 'vs_open':
                t['vs_open'] += 1
                if action in _AGGRESSIVE:
                    t['vs_open_agg'] += 1
            elif node == 'vs_3bet' and is_opener:
                t['vs_3bet'] += 1
                if action in _AGGRESSIVE:
                    t['vs_3bet_agg'] += 1
                elif action == 'fold':
                    t['vs_3bet_fold'] += 1
        elif phase in _POSTFLOP:
            # The player saw the flop (≥1 postflop decision) — WTSD denominator.
            scratch['saw_flop'] = True
            street = phase.lower()  # flop / turn / river
            # Aggregate AF/AFq components (back-compat) + per-street split.
            if action in _AGGRESSIVE:
                t['postflop_agg'] += 1
                t[f'{street}_agg'] += 1
            elif action == 'call':
                t['postflop_call'] += 1
                t[f'{street}_call'] += 1
            elif action == 'fold':
                # AFq denominator (folds count); per-street fold for street AFq.
                t[f'{street}_fold'] += 1
            # C-bet family (FLOP-only flags, set by the caller).
            if is_cbet_opportunity:
                t['cbet_opportunity'] += 1
                if is_cbet:
                    t['cbet_made'] += 1
            if is_facing_cbet:
                t['cbet_faced'] += 1
                if action == 'fold':
                    t['fold_to_cbet'] += 1

    def end_hand(
        self,
        db_path: Optional[str] = None,
        *,
        was_showdown: bool = False,
        winner_names: Optional[set] = None,
    ) -> None:
        """Roll up this hand's per-(archetype, player) booleans into totals and
        flush to ``db_path`` once the cadence is reached. Best-effort.

        ``was_showdown`` — the hand reached a showdown (≥2 players still live at
        the end). ``winner_names`` — names that won chips this hand. Together
        these drive WTSD (showdowns / saw-flop) and W$SD (won / showdowns): a
        flop-seeing player reaches showdown when ``was_showdown`` and wins it
        when in ``winner_names``. Keyword-defaulted for back-compat with callers
        that don't have hand-outcome context."""
        winners = winner_names or set()
        for (arch, player), s in self._hand.items():
            t = self._totals[arch]
            t['hands'] += 1
            if s['vpip']:
                t['vpip'] += 1
            if s['pfr']:
                t['pfr'] += 1
            if s['allin']:
                t['allin_hands'] += 1
            if s['saw_flop']:
                t['saw_flop'] += 1
                if was_showdown:
                    t['showdowns'] += 1
                    if player in winners:
                        t['showdowns_won'] += 1
        self._hand.clear()
        self._hands_since_flush += 1
        if db_path and self._hands_since_flush >= _FLUSH_EVERY_HANDS:
            self.flush(db_path)

    def flush(self, db_path: str) -> None:
        """Write accumulated deltas to `archetype_stat_counts` and reset the
        in-memory tallies. Best-effort — a write failure must never break the
        world tick (the counters are observability, not game state)."""
        self._hands_since_flush = 0
        if not self._totals:
            return
        deltas = {arch: dict(cols) for arch, cols in self._totals.items() if cols}
        if not deltas:
            self._totals.clear()
            return
        try:
            from poker.repositories.archetype_stat_repository import ArchetypeStatRepository

            ArchetypeStatRepository(db_path).add_stats(
                self.sandbox_id, deltas, now=datetime.now().isoformat()
            )
        except Exception as exc:  # noqa: BLE001 — observability, never fatal
            logger.debug("[ARCHETYPE_STATS] flush failed: %s", exc)
        self._totals.clear()


_recorders: Dict[str, ArchetypeStatRecorder] = {}
_lock = threading.Lock()


def get_recorder(sandbox_id: Optional[str]) -> Optional[ArchetypeStatRecorder]:
    """Return the per-sandbox recorder (created on first use). None without a
    sandbox_id."""
    if not sandbox_id:
        return None
    with _lock:
        rec = _recorders.get(sandbox_id)
        if rec is None:
            rec = ArchetypeStatRecorder(sandbox_id)
            _recorders[sandbox_id] = rec
        return rec
