"""
Unit tests for copal_core.sync:
  - C1 path-traversal protection
  - Phase C smart per-file conflict resolution (last_manifest_hashes)

These tests run entirely offline; no server or SeaweedFS required.
"""

import hashlib
import os
import pytest
from copal_core.sync import SyncEngine


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_file(path, content: bytes) -> str:
    """Write bytes to path (creating parent dirs) and return SHA-256 hex."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return _sha256(content)


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------

def _manifest(path, hash_suffix="a", size=10):
    """Build a minimal server-manifest entry (fake hash unless size is real)."""
    h = hash_suffix * 64
    return {"path": path, "hash": h, "size": size, "fid": f"/blobs/{h}"}


# ---------------------------------------------------------------------------
# Path-traversal protection (C1)
# ---------------------------------------------------------------------------

class TestPathTraversal:

    def test_normal_relative_path_is_planned(self, tmp_path):
        """A well-formed relative path should appear in the sync plan."""
        engine = SyncEngine()
        plan = engine.generate_plan([_manifest("subdir/file.txt")], str(tmp_path))
        assert len(plan) == 1
        assert plan[0]["rel_path"] == "subdir/file.txt"

    def test_dotdot_traversal_is_skipped(self, tmp_path, capsys):
        """../../ paths must be silently dropped from the plan."""
        engine = SyncEngine()
        plan = engine.generate_plan([_manifest("../../etc/passwd")], str(tmp_path))
        assert plan == []
        assert "unsafe" in capsys.readouterr().out.lower()

    def test_absolute_unix_path_is_skipped(self, tmp_path, capsys):
        """An absolute path in the manifest must never escape local_root."""
        engine = SyncEngine()
        plan = engine.generate_plan([_manifest("/etc/evil")], str(tmp_path))
        assert plan == []

    def test_absolute_windows_path_is_skipped(self, tmp_path, capsys):
        """Windows-style absolute paths are also blocked."""
        engine = SyncEngine()
        plan = engine.generate_plan([_manifest("C:\\Windows\\evil.dll")], str(tmp_path))
        assert plan == []

    def test_mixed_manifest_only_bad_paths_skipped(self, tmp_path, capsys):
        """Safe and unsafe paths can coexist — only the unsafe ones are dropped."""
        engine = SyncEngine()
        manifest = [
            _manifest("good/texture.png", "a"),
            _manifest("../../evil.sh",    "b"),
            _manifest("also/fine.txt",    "c"),
        ]
        plan = engine.generate_plan(manifest, str(tmp_path))
        rel_paths = [t["rel_path"] for t in plan]
        assert "good/texture.png" in rel_paths
        assert "also/fine.txt"   in rel_paths
        assert "../../evil.sh"   not in rel_paths

    def test_path_that_looks_bad_but_normalises_safely(self, tmp_path):
        """Paths like 'a/../b/file.txt' that normalise inside root are allowed."""
        engine = SyncEngine()
        # 'a/../b/file.txt' normalises to 'b/file.txt' — still inside root
        plan = engine.generate_plan([_manifest("a/../b/file.txt")], str(tmp_path))
        assert len(plan) == 1


# ---------------------------------------------------------------------------
# Phase C — smart per-file conflict resolution
# ---------------------------------------------------------------------------

class TestSmartConflict:
    """
    When last_manifest_hashes is supplied, conflicts are resolved per-file:
      local hash == last hash  →  untouched since last sync  →  CONFLICT_OVERWRITE
      local hash != last hash  →  user edited locally         →  CONFLICT_BACKUP
    The global policy is only used when last_manifest_hashes is None.
    """

    # Sentinel: a fake "server" hash that never matches any real file content
    SERVER_HASH = "b" * 64

    def _entry(self, path):
        return {"path": path, "hash": self.SERVER_HASH,
                "size": 9999, "fid": f"/blobs/{self.SERVER_HASH}"}

    def test_untouched_file_gets_overwrite(self, tmp_path):
        """File is identical to last-synced state → auto-overwrite (no backup)."""
        local_hash = _write_file(tmp_path / "file.txt", b"original content")
        last_hashes = {"file.txt": local_hash}

        plan = SyncEngine().generate_plan(
            [self._entry("file.txt")], str(tmp_path),
            last_manifest_hashes=last_hashes,
        )
        assert len(plan) == 1
        assert plan[0].get("conflict_mode") == "OVERWRITE"

    def test_edited_file_gets_backup(self, tmp_path):
        """File differs from last-synced state → auto-backup (user has edits)."""
        _write_file(tmp_path / "file.txt", b"user-edited content")
        # last_manifest recorded a different hash — user has made changes
        last_hashes = {"file.txt": "a" * 64}

        plan = SyncEngine().generate_plan(
            [self._entry("file.txt")], str(tmp_path),
            last_manifest_hashes=last_hashes,
        )
        assert len(plan) == 1
        assert plan[0].get("conflict_mode") == "BACKUP"

    def test_file_absent_from_last_manifest_gets_backup(self, tmp_path):
        """File exists locally but was not part of the last synced version → backup."""
        _write_file(tmp_path / "extra.txt", b"user created this file")
        # last_manifest has no entry for "extra.txt"
        last_hashes = {"other_file.txt": "a" * 64}

        plan = SyncEngine().generate_plan(
            [self._entry("extra.txt")], str(tmp_path),
            last_manifest_hashes=last_hashes,
        )
        assert len(plan) == 1
        assert plan[0].get("conflict_mode") == "BACKUP"

    def test_perfect_match_always_skipped(self, tmp_path):
        """File already matches the server hash → SKIP, even if last_manifest differs."""
        content = b"already the server version"
        local_hash = _write_file(tmp_path / "file.txt", content)

        entry = {"path": "file.txt", "hash": local_hash,
                 "size": len(content), "fid": "/blobs/x"}
        last_hashes = {"file.txt": "a" * 64}   # different from server

        plan = SyncEngine().generate_plan(
            [entry], str(tmp_path), last_manifest_hashes=last_hashes,
        )
        assert len(plan) == 1
        assert plan[0]["action"] == "SKIP"

    def test_missing_file_downloads_regardless(self, tmp_path):
        """File not on disk at all → DOWNLOAD (no conflict, no backup needed)."""
        plan = SyncEngine().generate_plan(
            [self._entry("missing.txt")], str(tmp_path),
            last_manifest_hashes={},
        )
        assert len(plan) == 1
        assert plan[0]["action"] == "DOWNLOAD"
        assert plan[0].get("conflict_mode") is None

    def test_no_last_manifest_uses_policy_backup(self, tmp_path):
        """Without last_manifest_hashes, global policy='backup' is applied."""
        _write_file(tmp_path / "file.txt", b"local content")

        plan = SyncEngine(conflict_policy="backup").generate_plan(
            [self._entry("file.txt")], str(tmp_path),
        )
        assert plan[0].get("conflict_mode") == "BACKUP"

    def test_no_last_manifest_uses_policy_overwrite(self, tmp_path):
        """Without last_manifest_hashes, global policy='overwrite' is applied."""
        _write_file(tmp_path / "file.txt", b"local content")

        plan = SyncEngine(conflict_policy="overwrite").generate_plan(
            [self._entry("file.txt")], str(tmp_path),
        )
        assert plan[0].get("conflict_mode") == "OVERWRITE"

    def test_no_last_manifest_uses_policy_skip(self, tmp_path):
        """Without last_manifest_hashes, global policy='skip' is applied."""
        _write_file(tmp_path / "file.txt", b"local content")

        plan = SyncEngine(conflict_policy="skip").generate_plan(
            [self._entry("file.txt")], str(tmp_path),
        )
        assert plan[0]["action"] == "CONFLICT_SKIP"

    def test_backslash_path_in_last_manifest_normalised(self, tmp_path):
        """last_manifest_hashes keys with backslashes match forward-slash paths."""
        local_hash = _write_file(tmp_path / "dir" / "file.txt", b"original content")
        # Server manifest uses forward slashes; last_manifest_hashes might too
        # (both sides normalise to /) — just verifying the normalisation path
        last_hashes = {"dir/file.txt": local_hash}

        plan = SyncEngine().generate_plan(
            [{"path": "dir/file.txt", "hash": self.SERVER_HASH,
              "size": 9999, "fid": "/blobs/x"}],
            str(tmp_path),
            last_manifest_hashes=last_hashes,
        )
        assert plan[0].get("conflict_mode") == "OVERWRITE"

    def test_mixed_files_smart_mode(self, tmp_path):
        """Multiple files: untouched → overwrite, edited → backup, missing → download."""
        untouched_hash = _write_file(tmp_path / "untouched.txt", b"same as last sync")
        _write_file(tmp_path / "edited.txt", b"user changed this")
        # "new.txt" is not on disk at all

        last_hashes = {
            "untouched.txt": untouched_hash,   # same as local → overwrite
            "edited.txt":    "a" * 64,          # different from local → backup
            # "new.txt" absent → download (no conflict)
        }
        manifest = [
            self._entry("untouched.txt"),
            self._entry("edited.txt"),
            self._entry("new.txt"),
        ]

        plan = SyncEngine().generate_plan(
            manifest, str(tmp_path), last_manifest_hashes=last_hashes,
        )
        by_path = {t["rel_path"]: t for t in plan}
        assert by_path["untouched.txt"].get("conflict_mode") == "OVERWRITE"
        assert by_path["edited.txt"].get("conflict_mode")    == "BACKUP"
        assert by_path["new.txt"].get("conflict_mode")       is None
        assert by_path["new.txt"]["action"]                  == "DOWNLOAD"
