"""
Unit tests for the persistent hash cache added in Phase Perf-A.1.

The cache lives at ``<root>/.copal/hash_cache.json`` and stores
``{rel_path: {size, mtime_ns, hash}}``. ``fs.scan_directory`` populates and
re-uses it so unchanged files don't get re-hashed on every push/pull.
"""

import os
import time
from copal_core import fs


def _make_file(root, rel, content):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


class TestHashCachePersistence:
    def test_cache_file_created_after_scan(self, tmp_path):
        _make_file(tmp_path, "a.txt", b"hello")
        fs.scan_directory(str(tmp_path))
        cache_path = tmp_path / ".copal" / "hash_cache.json"
        assert cache_path.exists()

    def test_unchanged_file_reuses_cached_hash(self, tmp_path, monkeypatch):
        """Second scan must NOT re-read the file body."""
        _make_file(tmp_path, "a.txt", b"hello world")
        fs.scan_directory(str(tmp_path))

        # Trip the real hasher — anything that calls it on the second scan
        # will fail the test loudly.
        def boom(_path):
            raise AssertionError("hash recomputed for an unchanged file")

        monkeypatch.setattr(fs, "calculate_hash", boom)
        result = fs.scan_directory(str(tmp_path))
        assert len(result) == 1
        assert result[0]["path"] == "a.txt"

    def test_mtime_change_invalidates_cache(self, tmp_path):
        path = _make_file(tmp_path, "a.txt", b"first")
        first = fs.scan_directory(str(tmp_path))[0]["hash"]

        # Rewrite with different bytes; mtime + size will both differ.
        # On fast filesystems mtime resolution can be coarse — sleep briefly
        # so the new mtime is detectably different.
        time.sleep(0.01)
        path.write_bytes(b"second-different-length")

        second = fs.scan_directory(str(tmp_path))[0]["hash"]
        assert first != second, "hash must update when bytes change"

    def test_deleted_file_removed_from_cache(self, tmp_path):
        path = _make_file(tmp_path, "a.txt", b"to be deleted")
        fs.scan_directory(str(tmp_path))
        assert (tmp_path / ".copal" / "hash_cache.json").exists()

        path.unlink()
        fs.scan_directory(str(tmp_path))

        import json
        with open(tmp_path / ".copal" / "hash_cache.json") as f:
            cache = json.load(f)
        assert "a.txt" not in cache


class TestCompiledIgnoreRules:
    def test_folder_rule_skips_subtree(self, tmp_path):
        _make_file(tmp_path, "keep.txt", b"x")
        _make_file(tmp_path, "renders/v1/big.png", b"y")
        (tmp_path / ".copalignore").write_text("renders/\n")

        scanned = {f["path"] for f in fs.scan_directory(str(tmp_path))}
        assert "keep.txt" in scanned
        assert not any(p.startswith("renders/") for p in scanned)

    def test_wildcard_rule_filters_by_pattern(self, tmp_path):
        _make_file(tmp_path, "keep.txt", b"x")
        _make_file(tmp_path, "ignore.tmp", b"y")
        _make_file(tmp_path, "deep/also.tmp", b"z")
        (tmp_path / ".copalignore").write_text("*.tmp\n")

        scanned = {f["path"] for f in fs.scan_directory(str(tmp_path))}
        assert "keep.txt" in scanned
        assert "ignore.tmp" not in scanned
        assert "deep/also.tmp" not in scanned

    def test_exact_name_rule_matches_basename_anywhere(self, tmp_path):
        _make_file(tmp_path, "Thumbs.db", b"x")
        _make_file(tmp_path, "deep/Thumbs.db", b"y")
        _make_file(tmp_path, "keep.png", b"z")

        scanned = {f["path"] for f in fs.scan_directory(str(tmp_path))}
        assert "keep.png" in scanned
        # Thumbs.db is in the built-in default ignore set
        assert "Thumbs.db" not in scanned
        assert "deep/Thumbs.db" not in scanned
