"""Tests for the deliverables schema migration (legacy `path` → `paths: list`).

The `normalize_deliverable` helper runs on every load so old YAMLs keep
working: a deliverable written before 2026-05-17 has `path: str`, and on
read we synthesize `paths: [path]`. New writes always use `paths`.
"""

from copalpm.deliver_cli import normalize_deliverable, normalize_deliverables


# ── Single-entry normalization ────────────────────────────────────────────────

def test_legacy_path_promoted_to_paths_list():
    legacy = {"name": "Final", "path": "old/Final.mp4", "type": "final"}
    out = normalize_deliverable(legacy)
    assert out["paths"] == ["old/Final.mp4"]
    assert "path" not in out
    # Other fields preserved
    assert out["name"] == "Final"
    assert out["type"] == "final"


def test_new_paths_passes_through_unchanged():
    new = {"name": "Final", "paths": ["a.mp4", "b.mp4"], "type": "final"}
    out = normalize_deliverable(new)
    assert out["paths"] == ["a.mp4", "b.mp4"]


def test_paths_wins_on_collision_with_legacy_path():
    """If both fields are present (manual edit gone wrong), `paths` wins."""
    hybrid = {
        "name":  "X",
        "path":  "ignored.mp4",
        "paths": ["kept.mp4"],
    }
    out = normalize_deliverable(hybrid)
    assert out["paths"] == ["kept.mp4"]
    assert "path" not in out


def test_empty_paths_tolerated():
    """Malformed entries should be kept (not silently dropped)."""
    out = normalize_deliverable({"name": "broken", "paths": []})
    assert out["paths"] == []
    assert out["name"] == "broken"


def test_paths_strips_empty_strings():
    out = normalize_deliverable({"paths": ["a.mp4", "", "  ", "b.mp4"]})
    assert out["paths"] == ["a.mp4", "b.mp4"]


def test_no_path_and_no_paths_yields_empty_list():
    out = normalize_deliverable({"name": "loose"})
    assert out["paths"] == []


def test_string_paths_value_coerced_to_list():
    """A defensive coercion: legacy hand-edits that wrote `paths: 'x.mp4'` (no list)."""
    out = normalize_deliverable({"paths": "single.mp4"})
    assert out["paths"] == ["single.mp4"]


def test_normalize_does_not_mutate_input():
    legacy = {"name": "Final", "path": "f.mp4"}
    legacy_copy = dict(legacy)
    normalize_deliverable(legacy)
    assert legacy == legacy_copy, "input dict was mutated"


def test_paths_coerced_to_strings():
    """Defensive: int / Path objects collapse to str."""
    from pathlib import Path
    out = normalize_deliverable({"paths": [Path("a.mp4"), "b.mp4"]})
    assert out["paths"] == ["a.mp4", "b.mp4"]


# ── Record-level normalization ────────────────────────────────────────────────

def test_normalize_deliverables_record_in_place():
    record = {
        "deliverables": [
            {"name": "Old",  "path":  "old.mp4"},
            {"name": "New",  "paths": ["new.mp4"]},
        ],
    }
    normalize_deliverables(record)
    assert record["deliverables"][0]["paths"] == ["old.mp4"]
    assert "path" not in record["deliverables"][0]
    assert record["deliverables"][1]["paths"] == ["new.mp4"]


def test_normalize_deliverables_handles_missing_key():
    record = {}
    normalize_deliverables(record)
    assert record["deliverables"] == []


def test_normalize_deliverables_handles_none():
    record = {"deliverables": None}
    normalize_deliverables(record)
    assert record["deliverables"] == []


# ── Save/load roundtrip ───────────────────────────────────────────────────────

def test_legacy_yaml_roundtrip_through_save_yaml(tmp_path):
    """Legacy YAML loads → normalized → saved → reload yields the new shape."""
    from copalpm.project_record import save_yaml, load_yaml

    yaml_path = tmp_path / "project.yaml"
    legacy = {
        "id":   "PROJ-X-010125",
        "name": "X",
        "schema_version": 1,
        "deliverables": [
            {"name": "Old", "path": "old/file.mp4", "type": "draft",
             "recipient": "client", "delivered_at": "2026-05-01T10:00:00Z", "notes": ""},
        ],
    }
    save_yaml(yaml_path, legacy)

    reloaded = load_yaml(yaml_path)
    normalize_deliverables(reloaded)
    # Round-trip: save normalized, reload
    save_yaml(yaml_path, reloaded)
    final = load_yaml(yaml_path)

    entry = final["deliverables"][0]
    assert entry["paths"] == ["old/file.mp4"]
    assert "path" not in entry
    assert entry["name"] == "Old"
