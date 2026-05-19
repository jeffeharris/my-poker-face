"""LRU cache of TieredBotController instances for the full-sim path.

Motivation: the Phase 0 spike measured controller setup at ~77 ms
per instance (mostly `AIPokerPlayer.__init__` constructing an
`Assistant` object even though full sim never invokes the LLM).
With 6 seats × 4 unseated tables × catch-up bursts of up to 30
hands, rebuilding controllers per hand would blow the 500 ms
lobby-response budget on the very first refresh. Caching by
`personality_id` keeps a hot pool warm across ticks.

The cache is generic over the value type — see `get_or_create`'s
factory parameter — so it could in principle hold any per-
personality object, but in practice the only caller is the
full-sim hand engine in Phase 2, holding `TieredBotController`
instances.

**Bounded LRU.** `max_size=50` is sized for: 5 stake tables ×
6 seats = 30 slots that could rotate, plus headroom for
recently-departed AIs that may re-enter via the idle pool. Lower
caps risk thrash; higher caps waste memory holding controllers
nobody will ask for again. Tune if production telemetry shows
high miss rates after warm-up.

**Not thread-safe by design.** v1 of full sim runs in the Flask
request thread (per spike + handoff). If we move to a background
worker (Phase 6+), wrap accessors with a lock or switch to a
concurrent-safe LRU.

Spec: `docs/plans/CASH_MODE_FULL_SIM_HANDOFF.md` Commit 1 — moved
ahead of the original Commit 6 slot because the spike showed cold
setup is the load-bearing variable, not a "pure optimization."
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Callable, Generic, TypeVar


DEFAULT_MAX_SIZE = 50

T = TypeVar("T")


class LruControllerCache(Generic[T]):
    """OrderedDict-backed LRU keyed by personality_id.

    Access semantics:
      - `get_or_create(pid, factory)` returns the cached entry,
        constructing via `factory()` and recording the entry on miss.
        Touching an entry promotes it to most-recently-used.
      - When the cache is at capacity and a miss occurs, the
        least-recently-used entry is evicted.
      - `get(pid)` peeks without recording (used by tests).
      - `clear()` empties the cache.

    The cache holds whatever the factory returns; it does NOT
    interpret the value. Callers are responsible for any setup that
    the value requires (e.g. pointing a controller's `state_machine`
    at the table's current SM after a cache hit).
    """

    def __init__(self, max_size: int = DEFAULT_MAX_SIZE):
        if max_size < 1:
            raise ValueError(f"max_size must be >= 1, got {max_size}")
        self._max_size = max_size
        self._items: "OrderedDict[str, T]" = OrderedDict()

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, personality_id: str) -> bool:
        return personality_id in self._items

    @property
    def max_size(self) -> int:
        return self._max_size

    def get(self, personality_id: str) -> "T | None":
        """Return the cached value or None. Does NOT update LRU order.

        Use this when inspecting cache state in tests. Production code
        should call `get_or_create` so cache hits get the MRU bump.
        """
        return self._items.get(personality_id)

    def get_or_create(
        self,
        personality_id: str,
        factory: Callable[[], T],
    ) -> T:
        """Return cached value for `personality_id`, calling `factory`
        on miss. Hits promote the entry to MRU; misses insert the new
        value and evict the LRU entry if the cache is over capacity.
        """
        existing = self._items.get(personality_id)
        if existing is not None:
            # Promote to MRU.
            self._items.move_to_end(personality_id)
            return existing

        value = factory()
        self._items[personality_id] = value
        # Evict from the LRU end until we're back at capacity. A miss
        # inserts at most one entry, so this loop runs zero or one
        # iterations; using `while` keeps the invariant local to read.
        while len(self._items) > self._max_size:
            self._items.popitem(last=False)
        return value

    def clear(self) -> None:
        """Drop all entries. For tests and shutdown."""
        self._items.clear()
