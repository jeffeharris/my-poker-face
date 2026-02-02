"""Tests for GuestTrackingRepository."""
import pytest
from poker.repositories.guest_tracking_repository import GuestTrackingRepository


@pytest.fixture
def repo(db_path):
    r = GuestTrackingRepository(db_path)
    yield r
    r.close()


def test_get_hands_played_returns_zero_for_unknown(repo):
    assert repo.get_hands_played("unknown_guest") == 0


def test_increment_hands_played(repo):
    count = repo.increment_hands_played("guest_123")
    assert count == 1


def test_increment_hands_played_multiple(repo):
    repo.increment_hands_played("guest_abc")
    repo.increment_hands_played("guest_abc")
    count = repo.increment_hands_played("guest_abc")
    assert count == 3


def test_get_hands_played_after_increment(repo):
    repo.increment_hands_played("guest_xyz")
    repo.increment_hands_played("guest_xyz")
    assert repo.get_hands_played("guest_xyz") == 2


def test_separate_tracking_ids(repo):
    repo.increment_hands_played("guest_a")
    repo.increment_hands_played("guest_a")
    repo.increment_hands_played("guest_b")

    assert repo.get_hands_played("guest_a") == 2
    assert repo.get_hands_played("guest_b") == 1
