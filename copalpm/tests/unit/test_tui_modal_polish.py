"""Tests for `_pull_dest_invalid`.

The full `PullDestinationModal` UX (live-disabled Continue button, red
preview text) is driven by this helper running on every Input.Changed
event. Testing the modal end-to-end needs Textual `Pilot`; we exercise
the validator in isolation here.
"""

import os
from pathlib import Path

import pytest

from copalpm.tui_app import _pull_dest_invalid


def test_empty_string_is_invalid():
    assert _pull_dest_invalid("") is not None


def test_whitespace_only_is_invalid():
    assert _pull_dest_invalid("   ") is not None


def test_none_is_invalid():
    assert _pull_dest_invalid(None) is not None  # type: ignore[arg-type]


def test_relative_path_is_invalid():
    err = _pull_dest_invalid("projects/foo")
    assert err is not None
    assert "absolute" in err.lower()


def test_dot_path_is_invalid():
    assert _pull_dest_invalid(".") is not None
    assert _pull_dest_invalid("./relative") is not None


def test_absolute_path_is_valid_even_when_missing():
    """The folder doesn't need to exist — `mkdir` runs on confirm."""
    if os.name == "nt":
        assert _pull_dest_invalid("C:/no/such/folder") is None
    else:
        assert _pull_dest_invalid("/no/such/folder") is None


def test_home_relative_path_is_valid():
    """`~` expansion yields an absolute path."""
    assert _pull_dest_invalid("~/Projects") is None
    assert _pull_dest_invalid("~") is None


def test_existing_absolute_path_is_valid(tmp_path):
    assert _pull_dest_invalid(str(tmp_path)) is None


def test_path_is_stripped_before_validation():
    """Surrounding whitespace shouldn't fail validation."""
    if os.name == "nt":
        assert _pull_dest_invalid("  C:/Users  ") is None
    else:
        assert _pull_dest_invalid("  /tmp  ") is None
