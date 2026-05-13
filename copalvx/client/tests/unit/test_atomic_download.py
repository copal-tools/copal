"""
Unit tests for atomic download semantics in transport.download_file().

After Phase Sec-A.1 a download writes bytes to ``<dest>.partial`` and only
``os.replace`` onto the final name once the hash check passes. The final file
must never be visible in a half-written or hash-mismatched state.
"""

import os
import pytest
from unittest.mock import MagicMock, patch
from copal_core import transport


def _mock_response(content=b"data", status=200):
    r = MagicMock()
    r.status_code = status
    r.iter_content = lambda chunk_size: iter([content])
    r.__enter__ = lambda s: s
    r.__exit__ = MagicMock(return_value=False)
    return r


class TestAtomicDownload:
    def test_success_publishes_via_replace(self, tmp_path):
        """Happy path: .partial is renamed to dest after hash passes."""
        dest = str(tmp_path / "file.bin")
        good_hash = "a" * 64

        with patch.object(transport.session, "get", return_value=_mock_response()), \
             patch("copal_core.transport._hash_file", return_value=good_hash), \
             patch("time.sleep"):
            success, _ = transport.download_file("/blobs/x", dest, 4, good_hash)

        assert success is True
        assert os.path.exists(dest), "final file should be present"
        assert not os.path.exists(dest + ".partial"), ".partial should be cleaned up"

    def test_hash_mismatch_leaves_no_final_file(self, tmp_path):
        """On exhausted retries with hash mismatch, the final dest must not exist."""
        dest = str(tmp_path / "broken.bin")
        good_hash = "a" * 64

        with patch.object(transport.session, "get", return_value=_mock_response()), \
             patch("copal_core.transport._hash_file", return_value="wrong" * 10), \
             patch("time.sleep"):
            success, msg = transport.download_file("/blobs/x", dest, 4, good_hash)

        assert success is False
        assert "mismatch" in msg.lower()
        assert not os.path.exists(dest), "no half-written file may be left at dest"
        assert not os.path.exists(dest + ".partial"), ".partial must also be cleaned"

    def test_exception_during_write_cleans_partial(self, tmp_path):
        """If an exception fires mid-stream, .partial is removed before bailing."""
        dest = str(tmp_path / "boom.bin")
        # Pre-create a stale partial so we can verify it gets cleaned.
        (tmp_path / "boom.bin.partial").write_bytes(b"stale")

        with patch.object(
            transport.session, "get",
            side_effect=RuntimeError("simulated network blow-up"),
        ), patch("time.sleep"):
            success, _ = transport.download_file("/blobs/x", dest, 4, "a" * 64)

        assert success is False
        assert not os.path.exists(dest)
        assert not os.path.exists(dest + ".partial")

    def test_existing_dest_is_atomically_replaced_on_success(self, tmp_path):
        """An existing file at dest is replaced atomically — old bytes never linger."""
        dest = str(tmp_path / "existing.bin")
        (tmp_path / "existing.bin").write_bytes(b"OLD bytes")
        good_hash = "a" * 64

        with patch.object(transport.session, "get", return_value=_mock_response(content=b"NEW")), \
             patch("copal_core.transport._hash_file", return_value=good_hash), \
             patch("time.sleep"):
            success, _ = transport.download_file("/blobs/x", dest, 3, good_hash)

        assert success is True
        with open(dest, "rb") as f:
            assert f.read() == b"NEW"


class TestSafeFidUrl:
    def test_normal_fid_passes_through(self):
        url = transport._safe_fid_url("/blobs/abcdef")
        assert url.endswith("/blobs/abcdef")

    def test_fid_with_space_is_quoted(self):
        url = transport._safe_fid_url("/blobs/has space")
        assert " " not in url
        assert "%20" in url

    def test_fid_with_question_mark_preserved(self):
        # Query separator is intentionally preserved (some SeaweedFS deployments
        # expect ?disk=ssd etc); ensures we don't double-quote URL syntax.
        url = transport._safe_fid_url("/blobs/abc?disk=ssd")
        assert url.endswith("/blobs/abc?disk=ssd")
