"""
Unit tests for copal_core.transport — specifically the M5 hash-mismatch retry fix.

All HTTP calls and filesystem writes are mocked; no network required.
"""

import pytest
from unittest.mock import MagicMock, patch, call
from copal_core import transport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(content=b"data", status=200):
    """Return a MagicMock that behaves like a requests streaming response."""
    r = MagicMock()
    r.status_code = status
    r.iter_content = lambda chunk_size: iter([content])
    # Support `with session.get(...) as r:`
    r.__enter__ = lambda s: s
    r.__exit__ = MagicMock(return_value=False)
    return r


# ---------------------------------------------------------------------------
# Hash-mismatch retry (M5)
# ---------------------------------------------------------------------------

class TestDownloadHashMismatchRetry:

    def test_success_on_first_attempt(self, tmp_path):
        """Happy path: correct hash on first try → success, one HTTP call."""
        dest = str(tmp_path / "file.bin")
        good_hash = "a" * 64

        with patch.object(transport.session, "get", return_value=_mock_response()) as mock_get, \
             patch("copal_core.transport._hash_file", return_value=good_hash), \
             patch("time.sleep"):
            success, msg = transport.download_file("/blobs/x", dest, 4, good_hash)

        assert success is True
        assert mock_get.call_count == 1

    def test_hash_mismatch_triggers_retry(self, tmp_path):
        """M5 fix: hash mismatch on attempt 1 → retried → succeeds on attempt 2."""
        dest = str(tmp_path / "file.bin")
        good_hash = "a" * 64
        bad_hash  = "b" * 64

        # First call: hash mismatch.  Second call: correct hash.
        hash_results = iter([bad_hash, good_hash])

        with patch.object(transport.session, "get", return_value=_mock_response()) as mock_get, \
             patch("copal_core.transport._hash_file", side_effect=hash_results), \
             patch("time.sleep"):
            success, msg = transport.download_file("/blobs/x", dest, 4, good_hash)

        assert success is True
        assert mock_get.call_count == 2, "Should have retried once after mismatch"

    def test_hash_mismatch_exhausts_all_retries(self, tmp_path):
        """After MAX_RETRIES mismatches the download gives up and returns False."""
        dest = str(tmp_path / "file.bin")
        good_hash = "a" * 64

        # Always return the wrong hash
        with patch.object(transport.session, "get", return_value=_mock_response()) as mock_get, \
             patch("copal_core.transport._hash_file", return_value="wrong" * 10), \
             patch("time.sleep"):
            success, msg = transport.download_file("/blobs/x", dest, 4, good_hash)

        assert success is False
        assert "mismatch" in msg.lower()
        assert mock_get.call_count == transport.MAX_RETRIES

    def test_hash_mismatch_sleeps_between_retries(self, tmp_path):
        """Each retry after a mismatch should respect the backoff schedule."""
        dest = str(tmp_path / "file.bin")
        good_hash = "a" * 64

        # Always fail so we can measure all sleep calls
        with patch.object(transport.session, "get", return_value=_mock_response()), \
             patch("copal_core.transport._hash_file", return_value="wrong" * 10), \
             patch("time.sleep") as mock_sleep:
            transport.download_file("/blobs/x", dest, 4, good_hash)

        # RETRY_BACKOFF = [1, 2, 4]; only first MAX_RETRIES-1 sleeps happen
        expected_sleeps = transport.RETRY_BACKOFF[: transport.MAX_RETRIES - 1]
        actual_sleeps   = [c.args[0] for c in mock_sleep.call_args_list]
        assert actual_sleeps == expected_sleeps

    def test_404_is_not_retried(self, tmp_path):
        """A 404 from the server is a permanent failure — no retry."""
        dest = str(tmp_path / "file.bin")

        with patch.object(transport.session, "get", return_value=_mock_response(status=404)) \
                as mock_get, \
             patch("time.sleep"):
            success, msg = transport.download_file("/blobs/x", dest, 4, "a" * 64)

        assert success is False
        assert "not found" in msg.lower()
        assert mock_get.call_count == 1
