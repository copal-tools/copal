"""
Unit tests for copal_core.versioning — tag parsing, incrementing, and validation.
"""

import pytest
from copal_core.versioning import ensure_prefix, increment_tag, validate_push_tag


class TestEnsurePrefix:
    def test_adds_v_when_missing(self):
        assert ensure_prefix("1.0") == "v1.0"

    def test_keeps_existing_v(self):
        assert ensure_prefix("v1.0") == "v1.0"

    def test_empty_string_returns_empty(self):
        assert ensure_prefix("") == ""

    def test_strips_surrounding_whitespace(self):
        assert ensure_prefix("  1.2  ") == "v1.2"


class TestIncrementTag:
    @pytest.mark.parametrize("before, after", [
        ("v1.0",  "v1.1"),
        ("v1.9",  "v1.10"),   # no digit capping
        ("v1.2.3","v1.2.4"),
        ("v1",    "v2"),
        ("v10",   "v11"),
    ])
    def test_increments_last_segment(self, before, after):
        assert increment_tag(before) == after

    def test_empty_string_returns_v1_0(self):
        assert increment_tag("") == "v1.0"

    def test_none_returns_v1_0(self):
        assert increment_tag(None) == "v1.0"

    def test_no_trailing_digit_appends_dot_1(self):
        # Fallback for tags with no trailing digit: appends ".1"
        assert increment_tag("v1.0-beta") == "v1.0-beta.1"


class TestValidatePushTag:
    def test_new_tag_is_valid(self):
        valid, msg = validate_push_tag("v1.1", ["v1.0", "v0.9"])
        assert valid is True
        assert msg == ""

    def test_duplicate_tag_is_invalid(self):
        valid, msg = validate_push_tag("v1.0", ["v1.0", "v0.9"])
        assert valid is False
        assert "already exists" in msg

    def test_ensure_prefix_applied_before_check(self):
        # "1.0" (no v) should still match "v1.0" in existing_tags
        valid, msg = validate_push_tag("1.0", ["v1.0"])
        assert valid is False

    def test_empty_existing_tags_always_valid(self):
        valid, _ = validate_push_tag("v1.0", [])
        assert valid is True
