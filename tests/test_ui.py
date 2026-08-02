"""Tests for ui.py's pure formatting helpers (no Streamlit runtime needed)."""

from ui import pid


def test_pid_strips_trailing_dot_zero():
    assert pid(31035.0) == "31035"
    assert pid("31035.0") == "31035"


def test_pid_leaves_real_values_untouched():
    assert pid("31035") == "31035"
    assert pid("ABC-123") == "ABC-123"


def test_pid_strips_surrounding_whitespace():
    assert pid("  31035.0  ") == "31035"
