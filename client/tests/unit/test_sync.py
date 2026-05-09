"""
Unit tests for copal_core.sync — specifically the C1 path-traversal fix.

These tests run entirely offline; no server or SeaweedFS required.
"""

import os
import pytest
from copal_core.sync import SyncEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _manifest(path, hash_suffix="a"):
    """Build a minimal server-manifest entry."""
    h = hash_suffix * 64
    return {"path": path, "hash": h, "size": 10, "fid": f"/blobs/{h}"}


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
