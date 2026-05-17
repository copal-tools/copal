"""Tests for the deliverable batch marker.

The marker file is the mechanism that groups multiple invocations of the
right-click `mark-deliverable` verb (one per selected file in Explorer)
into a single deliverable entry. A 5-second TTL means consecutive
invocations within the same right-click bundle into one entry; the next
right-click after >5s starts a new entry.
"""

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from copalpm import shell_integration as si


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    """Point `_batch_marker_path()` at a tmp dir for the duration of the test."""
    fake_data = tmp_path / "copalpm-data"
    fake_data.mkdir()
    monkeypatch.setattr(
        si, "_batch_marker_path",
        lambda: fake_data / si._BATCH_MARKER_NAME,
    )
    yield fake_data


# ── Read / write roundtrip ────────────────────────────────────────────────────

def test_write_then_read_marker(isolated_data_dir):
    si._write_batch_marker("PROJ-X", 5)
    marker = si._read_batch_marker()
    assert marker is not None
    assert marker["project_id"] == "PROJ-X"
    assert marker["deliverable_index"] == 5


def test_missing_marker_returns_none(isolated_data_dir):
    assert si._read_batch_marker() is None


def test_malformed_json_marker_returns_none(isolated_data_dir):
    si._batch_marker_path().write_text("not json at all", encoding="utf-8")
    assert si._read_batch_marker() is None


def test_marker_missing_fields_returns_none(isolated_data_dir):
    si._batch_marker_path().write_text(
        json.dumps({"project_id": "PROJ-X"}),  # missing index + expires_at
        encoding="utf-8",
    )
    assert si._read_batch_marker() is None


def test_marker_wrong_field_types_returns_none(isolated_data_dir):
    si._batch_marker_path().write_text(
        json.dumps({"project_id": "PROJ-X", "deliverable_index": "five", "expires_at": "2099-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    assert si._read_batch_marker() is None


# ── TTL semantics ─────────────────────────────────────────────────────────────

def test_expired_marker_returns_none(isolated_data_dir):
    past = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    si._batch_marker_path().write_text(
        json.dumps({"project_id": "PROJ-X", "deliverable_index": 0, "expires_at": past}),
        encoding="utf-8",
    )
    assert si._read_batch_marker() is None


def test_unparseable_expiry_returns_none(isolated_data_dir):
    si._batch_marker_path().write_text(
        json.dumps({"project_id": "PROJ-X", "deliverable_index": 0, "expires_at": "yesterday"}),
        encoding="utf-8",
    )
    assert si._read_batch_marker() is None


def test_marker_refreshes_ttl(isolated_data_dir):
    """Successive writes push the expiry forward."""
    si._write_batch_marker("PROJ-X", 0)
    first_expiry = si._read_batch_marker()["expires_at"]
    time.sleep(0.05)
    si._write_batch_marker("PROJ-X", 0)
    second_expiry = si._read_batch_marker()["expires_at"]
    assert second_expiry >= first_expiry


# ── Atomic write ──────────────────────────────────────────────────────────────

def test_write_marker_leaves_no_tmp(isolated_data_dir):
    si._write_batch_marker("PROJ-X", 0)
    leftovers = [p for p in isolated_data_dir.iterdir() if p.name.startswith(si._BATCH_MARKER_NAME + ".tmp.")]
    assert not leftovers, f"orphan tmp files: {leftovers}"


def test_write_marker_overwrites_previous(isolated_data_dir):
    si._write_batch_marker("PROJ-X", 1)
    si._write_batch_marker("PROJ-Y", 7)
    marker = si._read_batch_marker()
    assert marker["project_id"] == "PROJ-Y"
    assert marker["deliverable_index"] == 7


# ── Clear ─────────────────────────────────────────────────────────────────────

def test_clear_marker_removes_file(isolated_data_dir):
    si._write_batch_marker("PROJ-X", 0)
    si._clear_batch_marker()
    assert not si._batch_marker_path().exists()


def test_clear_marker_when_missing_does_not_raise(isolated_data_dir):
    si._clear_batch_marker()  # nothing to remove, must not raise


# ── Cross-project guard (semantics covered at handler level) ──────────────────

def test_marker_project_id_round_trip(isolated_data_dir):
    """The handler keys off project_id; round-trip it faithfully."""
    si._write_batch_marker("PROJ-Greek-Κ", 3)  # unicode tolerated
    marker = si._read_batch_marker()
    assert marker["project_id"] == "PROJ-Greek-Κ"
    assert marker["deliverable_index"] == 3
