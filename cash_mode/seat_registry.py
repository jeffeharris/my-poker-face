"""Seat-occupancy registry — audited wrapper around the `seated_globally` set.

Background
----------
Cash mode tracks "who is seated somewhere right now" as a plain ``Set[str]`` of
``personality_id``s. The set is built once per refresh
(``cash_mode.lobby.refresh_unseated_tables`` / the hand-boundary refresh in
``flask_app.handlers.game_handler``) and threaded **by reference** through
``refresh_table_roster`` and ``_process_global_greedy_fills``, which mutate it in
place. Correctness of the one-seat-per-AI invariant depends on every code path
remembering to ``.add`` on seat and ``.discard`` on vacate. Any missed mutation
silently breaks the invariant (the recurring "ghost-seat" bug class).

``SeatOccupancyRegistry`` is a thin, **per-refresh** wrapper (not a global
singleton) around an internal ``set`` that makes illegal mutations loud:

* ``seat(pid)`` (a.k.a. ``.add``) logs an ERROR + increments a collision counter
  if ``pid`` is already present, then **no-ops** — leaving the internal set in the
  exact state a plain ``set.add`` would (add-on-duplicate is a no-op). This is
  what keeps the wrapper **behavior-preserving**: production posture is
  log-and-continue, NEVER raise.
* ``vacate(pid)`` (a.k.a. ``.discard``) removes ``pid`` if present (no-op when
  absent) — identical to ``set.discard``.
* ``vacate_or_retain(pid, retain_reason=...)`` is an explicit, grep-able, logged
  no-op for the ``take_stake`` branch (the AI stays seated, freshly funded by a
  staker) so the deliberate non-discard is documented in code, not just a
  comment.

Because every method matches the corresponding ``set`` operation's resulting
state, swapping ``set()`` / ``_global_seated_set(...)`` for this registry at the
construction sites is a drop-in: existing ``.add`` / ``.discard`` / ``in`` /
``|=`` / ``.update`` / iteration / ``len`` call sites keep working unchanged.
"""

from __future__ import annotations

import logging
from typing import Iterable, Iterator, Set

logger = logging.getLogger(__name__)


class SeatOccupancyRegistry:
    """Audited, per-refresh wrapper around the ``seated_globally`` set.

    The internal set state is kept identical to what a plain ``set`` would hold
    after the same sequence of operations, so this is a behavior-preserving
    drop-in. Anomalies (double-seat via :meth:`seat`) are logged + counted but
    never raise in production.
    """

    def __init__(self, initial: Iterable[str] | None = None, *, label: str = ""):
        self._seated: Set[str] = set(initial or ())
        self._label = label
        self._collision_count = 0

    # --- Named API (the new, explicit surface) ---------------------------

    def seat(self, pid: str) -> None:
        """Mark ``pid`` as seated (replaces ``set.add``).

        If ``pid`` is already present this is a double-seat anomaly: log ERROR +
        increment the collision counter, then **no-op** (matching ``set.add``'s
        no-op-on-duplicate resulting state). Never raises.
        """
        if pid in self._seated:
            self._collision_count += 1
            logger.error(
                "SeatOccupancyRegistry%s: double-seat detected for personality_id=%r "
                "(already seated; ignoring — this is a ghost-seat anomaly). "
                "collision_count=%d",
                f"[{self._label}]" if self._label else "",
                pid,
                self._collision_count,
            )
            return
        self._seated.add(pid)

    def vacate(self, pid: str) -> None:
        """Mark ``pid`` as no longer seated (replaces ``set.discard``).

        No-op when ``pid`` is absent — identical to ``set.discard``.
        """
        self._seated.discard(pid)

    def vacate_or_retain(self, pid: str, *, retain_reason: str) -> None:
        """Explicit, logged no-op: keep ``pid`` seated.

        Used by the ``take_stake`` branch where the AI deliberately stays seated
        (freshly funded by a staker) rather than vacating. Makes the intentional
        non-discard grep-able and audited instead of relying on a comment.
        """
        logger.debug(
            "SeatOccupancyRegistry%s: retaining seat for personality_id=%r (reason=%s)",
            f"[{self._label}]" if self._label else "",
            pid,
            retain_reason,
        )
        # Intentional no-op — pid keeps its seat.

    def contains(self, pid: str) -> bool:
        return pid in self._seated

    def snapshot(self) -> frozenset:
        """Return an immutable copy of the current occupancy set."""
        return frozenset(self._seated)

    def add_without_collision_check(self, pids: Iterable[str]) -> None:
        """Union ``pids`` in WITHOUT treating overlap as a collision.

        Used for the live-seated / cold-load union where overlap with the
        persisted snapshot is *expected* (a persona the human is playing live may
        also still show seated in the snapshot). Logs at DEBUG; does not touch the
        collision counter.
        """
        incoming = set(pids)
        overlap = self._seated & incoming
        if overlap:
            logger.debug(
                "SeatOccupancyRegistry%s: union overlap (expected for live/cold-load): %r",
                f"[{self._label}]" if self._label else "",
                overlap,
            )
        self._seated |= incoming

    @property
    def collision_count(self) -> int:
        """Number of double-seat anomalies seen via :meth:`seat`. Tests assert ==0."""
        return self._collision_count

    # --- set-compatible aliases / operators (drop-in compat) -------------

    # `.add` / `.discard` keep existing call sites untouched while routing
    # through the audited methods.
    add = seat
    discard = vacate

    def update(self, pids: Iterable[str]) -> None:
        """Mirror ``set.update`` — used at the hand-boundary refresh union.

        Overlap here is expected (synced live AI seats), so route through
        :meth:`add_without_collision_check`.
        """
        self.add_without_collision_check(pids)

    def __contains__(self, pid: object) -> bool:
        return pid in self._seated

    def __ior__(self, other: Iterable[str]) -> SeatOccupancyRegistry:
        self.add_without_collision_check(other)
        return self

    def __iter__(self) -> Iterator[str]:
        return iter(self._seated)

    def __len__(self) -> int:
        return len(self._seated)

    def __repr__(self) -> str:
        return (
            f"SeatOccupancyRegistry(label={self._label!r}, "
            f"size={len(self._seated)}, collisions={self._collision_count})"
        )
