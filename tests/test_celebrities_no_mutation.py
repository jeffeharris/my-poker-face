"""Tests for T2-14: get_celebrities() should not mutate CELEBRITIES_LIST."""
from poker.utils import get_celebrities, CELEBRITIES_LIST


def test_shuffled_does_not_mutate_module_list():
    """Calling get_celebrities(shuffled=True) must not alter CELEBRITIES_LIST."""
    original_order = list(CELEBRITIES_LIST)

    get_celebrities(shuffled=True)

    assert CELEBRITIES_LIST == original_order, (
        "CELEBRITIES_LIST was mutated by get_celebrities(shuffled=True)"
    )


def test_shuffled_returns_all_celebrities():
    """Shuffled result contains the same elements as the original list."""
    result = get_celebrities(shuffled=True)
    assert sorted(result) == sorted(CELEBRITIES_LIST)
    assert len(result) == len(CELEBRITIES_LIST)


def test_non_shuffled_returns_original_order():
    """Non-shuffled call returns celebrities in the original order."""
    original_order = list(CELEBRITIES_LIST)
    result = get_celebrities(shuffled=False)
    assert result == original_order


def test_returns_new_list_object():
    """Returned list must be a new object, not a reference to the constant."""
    result = get_celebrities(shuffled=False)
    assert result is not CELEBRITIES_LIST

    result_shuffled = get_celebrities(shuffled=True)
    assert result_shuffled is not CELEBRITIES_LIST


def test_multiple_shuffled_calls_do_not_accumulate_mutations():
    """Multiple shuffled calls should all leave CELEBRITIES_LIST unchanged."""
    original_order = list(CELEBRITIES_LIST)

    for _ in range(10):
        get_celebrities(shuffled=True)

    assert CELEBRITIES_LIST == original_order
