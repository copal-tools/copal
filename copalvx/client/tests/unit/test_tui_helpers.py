"""
Unit tests for selective-pull helper functions in tui.py.

_changed_folders  — groups diff entries by immediate parent directory
_matches_prefix   — Option-A subtree filter

No server, network, or filesystem access required.
"""

import sys
from pathlib import Path

# Ensure the client root is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from tui import _changed_folders, _matches_prefix


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _diff(added=None, removed=None, changed=None, unchanged_count=0):
    return {
        "added":           added   or [],
        "removed":         removed or [],
        "changed":         changed or [],
        "unchanged_count": unchanged_count,
    }


def _added(path, size=10):
    return {"path": path, "size": size}

def _removed(path, size=10):
    return {"path": path, "size": size}

def _changed(path, old=10, new=20):
    return {"path": path, "old_size": old, "new_size": new}


# ---------------------------------------------------------------------------
# _changed_folders
# ---------------------------------------------------------------------------

class TestChangedFolders:

    def test_empty_diff_returns_empty_list(self):
        assert _changed_folders(_diff()) == []

    def test_single_file_in_subfolder(self):
        result = _changed_folders(_diff(added=[_added("renders/beauty/shot001.exr")]))
        assert len(result) == 1
        assert result[0]["folder"] == "renders/beauty"
        assert result[0]["count"] == 1

    def test_root_level_file_produces_empty_string_folder(self):
        result = _changed_folders(_diff(added=[_added("project.aep")]))
        assert len(result) == 1
        assert result[0]["folder"] == ""
        assert result[0]["count"] == 1

    def test_case1_changes_in_subfolders_only(self):
        """Case 1: files changed in Renders and Assets — 02_Workfiles itself not shown."""
        diff = _diff(changed=[
            _changed("02_Workfiles/Renders/scene.hip"),
            _changed("02_Workfiles/Assets/texture.png"),
        ])
        folders = _changed_folders(diff)
        names = [f["folder"] for f in folders]
        assert "02_Workfiles/Renders" in names
        assert "02_Workfiles/Assets"  in names
        assert "02_Workfiles"         not in names

    def test_case2_subfolder_and_direct_file(self):
        """Case 2: Renders changed + project.aep changed — both Renders and 02_Workfiles shown."""
        diff = _diff(
            changed=[_changed("02_Workfiles/Renders/scene.hip")],
            added  =[_added("02_Workfiles/project.aep")],
        )
        folders = _changed_folders(diff)
        names = [f["folder"] for f in folders]
        assert "02_Workfiles/Renders" in names
        assert "02_Workfiles"         in names
        assert "02_Workfiles/Assets"  not in names

    def test_count_aggregates_all_files_in_same_parent(self):
        diff = _diff(added=[
            _added("renders/shot_001.exr"),
            _added("renders/shot_002.exr"),
            _added("renders/shot_003.exr"),
        ])
        result = _changed_folders(diff)
        assert len(result) == 1
        assert result[0]["folder"] == "renders"
        assert result[0]["count"] == 3

    def test_counts_across_added_removed_changed(self):
        """Files from all three categories count toward the same parent."""
        diff = _diff(
            added  =[_added("workfiles/new.hip")],
            removed=[_removed("workfiles/old.hip")],
            changed=[_changed("workfiles/scene.hip")],
        )
        result = _changed_folders(diff)
        assert len(result) == 1
        assert result[0]["folder"] == "workfiles"
        assert result[0]["count"] == 3

    def test_backslash_paths_normalised(self):
        """Windows backslash separators are treated as forward slashes."""
        diff = _diff(added=[_added("renders\\shot001.exr")])
        result = _changed_folders(diff)
        assert result[0]["folder"] == "renders"

    def test_result_is_sorted_alphabetically(self):
        diff = _diff(added=[
            _added("zzz/file.txt"),
            _added("aaa/file.txt"),
            _added("mmm/file.txt"),
        ])
        result = _changed_folders(diff)
        names = [f["folder"] for f in result]
        assert names == sorted(names)

    def test_multiple_files_multiple_parents(self):
        diff = _diff(added=[
            _added("renders/beauty/shot001.exr"),
            _added("workfiles/houdini/scene.hip"),
            _added("plates/A001/frame.0001.exr"),
        ])
        result = _changed_folders(diff)
        assert len(result) == 3
        names = {f["folder"] for f in result}
        assert names == {"renders/beauty", "workfiles/houdini", "plates/A001"}


# ---------------------------------------------------------------------------
# _matches_prefix
# ---------------------------------------------------------------------------

class TestMatchesPrefix:

    def test_file_matches_its_parent_prefix(self):
        assert _matches_prefix("renders/shot001.exr", {"renders"})

    def test_file_in_deep_subtree_matches_ancestor_prefix(self):
        assert _matches_prefix("renders/beauty/shot001.exr", {"renders"})

    def test_file_does_not_match_unrelated_prefix(self):
        assert not _matches_prefix("workfiles/scene.hip", {"renders"})

    def test_root_file_matches_empty_prefix(self):
        assert _matches_prefix("project.aep", {""})

    def test_root_file_does_not_match_folder_prefix(self):
        assert not _matches_prefix("project.aep", {"renders"})

    def test_subfolder_file_does_not_match_empty_prefix(self):
        """Selecting root (empty prefix) must not pull files inside subfolders."""
        assert not _matches_prefix("renders/shot001.exr", {""})

    def test_partial_name_does_not_match(self):
        """'render' (no slash) must not match 'renders/file.txt'."""
        assert not _matches_prefix("renders/shot001.exr", {"render"})

    def test_multiple_prefixes_any_match_wins(self):
        assert _matches_prefix("renders/shot001.exr", {"workfiles", "renders"})

    def test_multiple_prefixes_no_match(self):
        assert not _matches_prefix("plates/A001/frame.exr", {"renders", "workfiles"})

    def test_backslash_paths_normalised(self):
        assert _matches_prefix("renders\\shot001.exr", {"renders"})

    def test_option_a_parent_folder_pulls_entire_subtree(self):
        """Selecting 02_Workfiles pulls everything under it (Option A)."""
        assert _matches_prefix("02_Workfiles/project.aep",          {"02_Workfiles"})
        assert _matches_prefix("02_Workfiles/Renders/shot001.exr",  {"02_Workfiles"})
        assert _matches_prefix("02_Workfiles/Assets/texture.png",   {"02_Workfiles"})

    def test_prefix_does_not_bleed_into_similar_name(self):
        """'work' prefix must not match 'workfiles/...'."""
        assert not _matches_prefix("workfiles/scene.hip", {"work"})
