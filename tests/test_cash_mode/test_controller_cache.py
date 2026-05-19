"""Tests for cash_mode/controller_cache.py — LRU cache invariants.

The cache value type is generic (`Generic[T]`), so the tests use
plain string sentinels rather than real `TieredBotController`
instances. Constructing actual controllers in unit tests would
add ~77 ms per instance per Phase 0 spike — not worth it just to
exercise LRU semantics.
"""

from __future__ import annotations

import pytest

from cash_mode.controller_cache import LruControllerCache


class _CountingFactory:
    """Tracks how many times the factory was invoked, returning
    distinguishable values per call. Used to assert cache hits
    don't re-invoke the factory."""

    def __init__(self):
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return f"ctrl#{self.calls}"


class TestLruControllerCacheBasics:
    def test_miss_invokes_factory_and_records(self):
        cache = LruControllerCache[str](max_size=4)
        factory = _CountingFactory()

        value = cache.get_or_create("napoleon", factory)

        assert value == "ctrl#1"
        assert factory.calls == 1
        assert "napoleon" in cache
        assert len(cache) == 1

    def test_hit_returns_same_instance_and_skips_factory(self):
        cache = LruControllerCache[str](max_size=4)
        factory = _CountingFactory()

        first = cache.get_or_create("napoleon", factory)
        second = cache.get_or_create("napoleon", factory)

        assert first is second, "cache hit must return the same instance"
        assert factory.calls == 1

    def test_distinct_keys_are_independent(self):
        cache = LruControllerCache[str](max_size=4)
        factory = _CountingFactory()

        a = cache.get_or_create("napoleon", factory)
        b = cache.get_or_create("lincoln", factory)

        assert a != b
        assert factory.calls == 2

    def test_clear_empties_cache(self):
        cache = LruControllerCache[str](max_size=4)
        factory = _CountingFactory()
        cache.get_or_create("napoleon", factory)
        cache.get_or_create("lincoln", factory)
        assert len(cache) == 2

        cache.clear()

        assert len(cache) == 0
        # Next lookup must construct again.
        cache.get_or_create("napoleon", factory)
        assert factory.calls == 3


class TestLruEviction:
    def test_at_capacity_inserting_new_key_evicts_lru(self):
        cache = LruControllerCache[str](max_size=3)
        factory = _CountingFactory()

        # Fill cache in order: napoleon (LRU), lincoln, buddha (MRU)
        cache.get_or_create("napoleon", factory)
        cache.get_or_create("lincoln", factory)
        cache.get_or_create("buddha", factory)
        assert len(cache) == 3

        # Insert a fourth — napoleon (LRU) must drop out.
        cache.get_or_create("gatsby", factory)

        assert len(cache) == 3
        assert "napoleon" not in cache
        assert "gatsby" in cache
        assert "lincoln" in cache
        assert "buddha" in cache

    def test_hit_promotes_to_mru_and_protects_from_eviction(self):
        cache = LruControllerCache[str](max_size=3)
        factory = _CountingFactory()

        cache.get_or_create("napoleon", factory)
        cache.get_or_create("lincoln", factory)
        cache.get_or_create("buddha", factory)

        # Touch napoleon — it becomes MRU; lincoln is now the LRU.
        cache.get_or_create("napoleon", factory)

        # Insert a fourth — lincoln drops, napoleon survives.
        cache.get_or_create("gatsby", factory)

        assert "napoleon" in cache
        assert "lincoln" not in cache
        assert "buddha" in cache
        assert "gatsby" in cache

    def test_default_max_size_holds_enough_for_lobby(self):
        """Sanity check: default 50 holds 5 stakes × 6 seats with
        idle-pool churn headroom — the design target from the spike."""
        cache = LruControllerCache[str]()
        assert cache.max_size >= 30


class TestInvalidConfiguration:
    def test_max_size_zero_raises(self):
        with pytest.raises(ValueError):
            LruControllerCache[str](max_size=0)

    def test_max_size_negative_raises(self):
        with pytest.raises(ValueError):
            LruControllerCache[str](max_size=-1)


class TestGetDoesNotPromote:
    """`get()` is a peek — it must NOT update LRU order. Production
    code uses `get_or_create`; `get` is for test inspection."""

    def test_get_returns_value_without_changing_order(self):
        cache = LruControllerCache[str](max_size=3)
        factory = _CountingFactory()

        cache.get_or_create("napoleon", factory)
        cache.get_or_create("lincoln", factory)
        cache.get_or_create("buddha", factory)

        # Peek napoleon — must NOT bump it to MRU.
        peeked = cache.get("napoleon")
        assert peeked == "ctrl#1"

        # Force eviction — napoleon (still LRU) should drop.
        cache.get_or_create("gatsby", factory)

        assert "napoleon" not in cache
        assert "buddha" in cache  # untouched MRU side survives

    def test_get_miss_returns_none(self):
        cache = LruControllerCache[str](max_size=4)
        assert cache.get("nonexistent") is None
