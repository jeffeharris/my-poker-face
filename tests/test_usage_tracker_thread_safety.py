"""Tests for UsageTracker thread-safe singleton (T2-18)."""
import threading
from unittest.mock import patch

from core.llm.tracking import UsageTracker


class TestUsageTrackerThreadSafety:
    """Verify UsageTracker.get_default() is thread-safe."""

    def setup_method(self):
        """Reset singleton before each test."""
        UsageTracker._instance = None

    def teardown_method(self):
        """Reset singleton after each test."""
        UsageTracker._instance = None

    @patch.object(UsageTracker, '_ensure_table')
    @patch.object(UsageTracker, '_get_default_db_path', return_value=':memory:')
    def test_concurrent_get_default_returns_same_instance(self, mock_path, mock_table):
        """10 threads calling get_default() concurrently all get the same instance."""
        results = [None] * 10
        barrier = threading.Barrier(10)

        def worker(idx):
            barrier.wait()  # All threads start at the same time
            results[idx] = UsageTracker.get_default()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All results should be the same instance
        instance_ids = {id(r) for r in results}
        assert len(instance_ids) == 1, f"Expected 1 unique instance, got {len(instance_ids)}"
        assert all(r is results[0] for r in results)

    @patch.object(UsageTracker, '_ensure_table')
    @patch.object(UsageTracker, '_get_default_db_path', return_value=':memory:')
    def test_get_default_returns_existing_instance(self, mock_path, mock_table):
        """Once created, get_default() returns the same instance without locking."""
        first = UsageTracker.get_default()
        second = UsageTracker.get_default()
        assert first is second

    def test_instance_lock_is_class_level_threading_lock(self):
        """_instance_lock is a threading.Lock at class level."""
        assert isinstance(UsageTracker._instance_lock, type(threading.Lock()))
