import pytest

from ebay_claw.adapters.read_only import ReadOnlyViolationError, assert_read_only_method


def test_assert_get_ok():
    assert_read_only_method("GET")
    assert_read_only_method("get")


def test_assert_post_blocked():
    with pytest.raises(ReadOnlyViolationError):
        assert_read_only_method("POST")
