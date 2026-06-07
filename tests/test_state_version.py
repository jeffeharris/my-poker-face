"""Tests for the monotonic game-state frame version stamp."""

from flask_app.state_version import next_state_version


def test_next_state_version_is_strictly_increasing():
    a = next_state_version()
    b = next_state_version()
    c = next_state_version()
    assert a < b < c


def test_next_state_version_unique_under_threads():
    """Concurrent callers each get a unique value (itertools.count is atomic)."""
    import threading

    results: list[int] = []
    results_lock = threading.Lock()

    def grab():
        v = next_state_version()
        with results_lock:
            results.append(v)

    threads = [threading.Thread(target=grab) for _ in range(200)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == len(set(results))  # all unique
