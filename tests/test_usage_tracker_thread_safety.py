"""Tests for UsageTracker thread-safe singleton (T2-18)."""
import threading
from unittest.mock import patch

import pytest


class TestUsageTrackerThreadSafeSingleton:
    """Verify UsageTracker.get_default() is thread-safe."""

    def setup_method(self):
        """Reset singleton before each test."""
        from core.llm.tracking import UsageTracker
        UsageTracker._instance = None

    def teardown_method(self):
        """Reset singleton after each test."""
        from core.llm.tracking import UsageTracker
        UsageTracker._instance = None

    @patch("core.llm.tracking.UsageTracker._ensure_table")
    @patch("core.llm.tracking.UsageTracker._get_default_db_path", return_value=":memory:")
    def test_concurrent_get_default_returns_same_instance(self, _mock_db, _mock_table):
        """10 threads calling get_default() concurrently all get the same instance."""
        from core.llm.tracking import UsageTracker

        results = [None] * 10
        barrier = threading.Barrier(10)

        def worker(index):
            barrier.wait()  # All threads start at the same time
            results[index] = UsageTracker.get_default()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All results should be the same instance
        assert all(r is not None for r in results), "All threads should return an instance"
        ids = {id(r) for r in results}
        assert len(ids) == 1, f"Expected 1 unique instance, got {len(ids)}"

    @patch("core.llm.tracking.UsageTracker._ensure_table")
    @patch("core.llm.tracking.UsageTracker._get_default_db_path", return_value=":memory:")
    def test_get_default_returns_same_instance_on_repeated_calls(self, _mock_db, _mock_table):
        """Sequential calls to get_default() return the same instance."""
        from core.llm.tracking import UsageTracker

        a = UsageTracker.get_default()
        b = UsageTracker.get_default()
        assert a is b

    def test_instance_lock_is_class_level_threading_lock(self):
        """_instance_lock is a threading.Lock at the class level."""
        from core.llm.tracking import UsageTracker

        assert hasattr(UsageTracker, '_instance_lock')
        # threading.Lock() returns a _thread.lock object
        assert isinstance(UsageTracker._instance_lock, type(threading.Lock()))
