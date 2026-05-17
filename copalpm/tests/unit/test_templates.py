"""Tests for the new dynamic-fields template system.

Covers:
  * Validation of templates and field declarations (kind / options / reserved keys)
  * Folder-path safety guard (`_validate_template_folder_path`)
  * Save / load round-trip (preserves field order — critical, see Plan critique)
  * apply_to_record well-known-key unpacking + custom-key passthrough
  * Migration from the legacy `templates.json` format, with .bak rename
  * Idempotency of `_migrate_if_needed`
  * Invalid YAML in `templates/` is skipped with a stderr warning (not crash)
  * Nested folders create as expected on disk
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


# ── Fixture: redirect TEMPLATES_DIR / TEMPLATES_FILE to a tmp_path ─────────

@pytest.fixture
def templates_tmp(tmp_path, monkeypatch):
    """Point copalpm.templates and copalpm.config at an isolated tmp data dir."""
    from copalpm import templates as tmod
    fake_dir  = tmp_path / "templates"
    fake_file = tmp_path / "templates.json"
    monkeypatch.setattr(tmod, "TEMPLATES_DIR",  fake_dir)
    monkeypatch.setattr(tmod, "TEMPLATES_FILE", fake_file)
    return tmod, fake_dir, fake_file


# ── Helpers ────────────────────────────────────────────────────────────────

def _basic_template(id_="tactical", name="Tactical") -> dict:
    return {
        "schema_version": 1,
        "id":      id_,
        "name":    name,
        "folders": ["01_Intake", "02_Workfiles", "03_Exports"],
        "fields": [
            {"key": "type", "label": "Type", "kind": "select", "default": "tlc",
             "options": [{"value": "tlc", "label": "Internal"}]},
            {"key": "client", "label": "Client", "kind": "text", "default": ""},
        ],
    }


# ── Folder-path validation ─────────────────────────────────────────────────

class TestFolderPathValidation:
    def test_simple_relative(self, templates_tmp):
        tmod, *_ = templates_tmp
        assert tmod._validate_template_folder_path("01_Intake") is None
        assert tmod._validate_template_folder_path("a/b") is None
        assert tmod._validate_template_folder_path("a/b/c/d") is None
        # Windows-style is accepted (will be normalized)
        assert tmod._validate_template_folder_path("a\\b\\c") is None

    def test_rejects_absolute(self, templates_tmp):
        tmod, *_ = templates_tmp
        assert tmod._validate_template_folder_path("/etc/passwd") is not None
        assert tmod._validate_template_folder_path("\\Windows") is not None
        assert tmod._validate_template_folder_path("C:\\foo") is not None
        assert tmod._validate_template_folder_path("D:") is not None

    def test_rejects_traversal(self, templates_tmp):
        tmod, *_ = templates_tmp
        assert tmod._validate_template_folder_path("..") is not None
        assert tmod._validate_template_folder_path("a/../b") is not None
        assert tmod._validate_template_folder_path("..\\foo") is not None

    def test_rejects_home_and_empty(self, templates_tmp):
        tmod, *_ = templates_tmp
        assert tmod._validate_template_folder_path("~") is not None
        assert tmod._validate_template_folder_path("~/Documents") is not None
        assert tmod._validate_template_folder_path("") is not None
        assert tmod._validate_template_folder_path("  ") is not None
        assert tmod._validate_template_folder_path(".") is not None


# ── Template validation ────────────────────────────────────────────────────

class TestValidateTemplate:
    def test_basic_template_valid(self, templates_tmp):
        tmod, *_ = templates_tmp
        assert tmod.validate_template(_basic_template()) == []

    def test_reserved_field_key_rejected(self, templates_tmp):
        tmod, *_ = templates_tmp
        t = _basic_template()
        t["fields"].append({"key": "people", "label": "People",
                            "kind": "text", "default": ""})
        errs = tmod.validate_template(t)
        assert any("reserved" in e for e in errs)

    def test_duplicate_field_keys_rejected(self, templates_tmp):
        tmod, *_ = templates_tmp
        t = _basic_template()
        t["fields"].append({"key": "client", "label": "Other Client",
                            "kind": "text", "default": ""})
        errs = tmod.validate_template(t)
        assert any("duplicates" in e for e in errs)

    def test_unknown_kind_rejected(self, templates_tmp):
        tmod, *_ = templates_tmp
        t = _basic_template()
        t["fields"].append({"key": "extra", "label": "Extra",
                            "kind": "nonexistent", "default": ""})
        errs = tmod.validate_template(t)
        assert any("kind" in e for e in errs)

    def test_select_without_options_rejected(self, templates_tmp):
        tmod, *_ = templates_tmp
        t = _basic_template()
        t["fields"].append({"key": "extra", "label": "Extra",
                            "kind": "select", "default": "a"})
        errs = tmod.validate_template(t)
        assert any("options" in e for e in errs)

    def test_select_default_must_match_options(self, templates_tmp):
        """Regression: a Select widget mounted with `value` not in `options`
        raises InvalidSelectValueError and crashes the InitScreen."""
        tmod, *_ = templates_tmp
        t = _basic_template()
        t["fields"].append({
            "key": "tier",
            "label": "Tier",
            "kind": "select",
            "default": "BBDO",   # not a valid option value
            "options": [
                {"value": "internal", "label": "Internal"},
                {"value": "client",   "label": "Client"},
            ],
        })
        errs = tmod.validate_template(t)
        assert any("default" in e and "BBDO" in e for e in errs), errs

    def test_select_default_matching_options_accepted(self, templates_tmp):
        tmod, *_ = templates_tmp
        t = _basic_template()
        t["fields"].append({
            "key": "tier",
            "label": "Tier",
            "kind": "select",
            "default": "client",
            "options": [
                {"value": "internal", "label": "Internal"},
                {"value": "client",   "label": "Client"},
            ],
        })
        assert tmod.validate_template(t) == []

    def test_bad_id_slug_rejected(self, templates_tmp):
        tmod, *_ = templates_tmp
        t = _basic_template(id_="Has Spaces")
        errs = tmod.validate_template(t)
        assert any("id" in e for e in errs)

    def test_bool_default_validated(self, templates_tmp):
        tmod, *_ = templates_tmp
        t = _basic_template()
        t["fields"].append({"key": "is_final", "label": "Final?",
                            "kind": "bool", "default": "yes"})
        errs = tmod.validate_template(t)
        assert any("boolean" in e for e in errs)

    def test_invalid_folder_rejected(self, templates_tmp):
        tmod, *_ = templates_tmp
        t = _basic_template()
        t["folders"] = ["01_Intake", "../escape"]
        errs = tmod.validate_template(t)
        assert any("folders" in e for e in errs)


# ── apply_to_record ────────────────────────────────────────────────────────

class TestApplyToRecord:
    def _empty_record(self) -> dict:
        from copalpm.pm import build_project_record
        return build_project_record("PROJ-X", "X", field_values={}, deadline=None)

    def test_well_known_keys_unpack(self, templates_tmp):
        tmod, *_ = templates_tmp
        record = self._empty_record()
        tmod.apply_to_record(
            {
                "client":         "ACME",
                "client_contact": "ada@acme.test",
                "director":       "Dir",
                "producer":       "Prod",
                "collaborators":  ["a", "b"],
                "budget":         1000.0,
                "rate":           50.0,
                "est_hours":      20.0,
            },
            record,
        )
        assert record["client"]["name"]              == "ACME"
        assert record["client"]["contact"]           == "ada@acme.test"
        assert record["people"]["director"]          == "Dir"
        assert record["people"]["producer"]          == "Prod"
        assert record["people"]["collaborators"]     == ["a", "b"]
        assert record["financial"]["quoted_budget"]  == 1000.0
        assert record["financial"]["rate_per_hour"]  == 50.0
        assert record["financial"]["estimated_hours"] == 20.0

    def test_custom_keys_land_top_level(self, templates_tmp):
        tmod, *_ = templates_tmp
        record = self._empty_record()
        tmod.apply_to_record(
            {"supervisor": "Sup", "final_pass": True, "pass_count": "3"},
            record,
        )
        assert record["supervisor"] == "Sup"
        assert record["final_pass"] is True
        assert record["pass_count"] == "3"

    def test_type_and_category_land_top_level(self, templates_tmp):
        tmod, *_ = templates_tmp
        record = self._empty_record()
        tmod.apply_to_record({"type": "r_and_d", "category": "experimental"}, record)
        assert record["type"]     == "r_and_d"
        assert record["category"] == "experimental"

    def test_reserved_keys_silently_skipped(self, templates_tmp):
        tmod, *_ = templates_tmp
        record = self._empty_record()
        tmod.apply_to_record({"id": "HACKED", "name": "HACKED"}, record)
        assert record["id"]   == "PROJ-X"
        assert record["name"] == "X"


# ── Save / load round-trip ─────────────────────────────────────────────────

class TestRoundTrip:
    def test_save_and_load_preserves_field_order(self, templates_tmp):
        """Critical: PyYAML can resort keys; assert insertion order is preserved."""
        tmod, fake_dir, _ = templates_tmp
        t = _basic_template()
        t["fields"] = [
            {"key": "z_last", "label": "Z", "kind": "text", "default": ""},
            {"key": "a_first", "label": "A", "kind": "text", "default": ""},
            {"key": "m_middle", "label": "M", "kind": "text", "default": ""},
        ]
        tmod.save_template(t)
        loaded = tmod.load_by_id("tactical")
        assert loaded is not None
        assert [f["key"] for f in loaded["fields"]] == ["z_last", "a_first", "m_middle"]

    def test_save_then_load_returns_equivalent(self, templates_tmp):
        tmod, *_ = templates_tmp
        t = _basic_template()
        t["fields"].append(
            {"key": "is_final", "label": "Final?", "kind": "bool", "default": False}
        )
        tmod.save_template(t)
        loaded = tmod.load_by_id("tactical")
        assert loaded is not None
        loaded.pop("_filename", None)
        assert loaded == t

    def test_save_assigns_prefix_filename(self, templates_tmp):
        tmod, fake_dir, _ = templates_tmp
        tmod.save_template(_basic_template(id_="tactical", name="Tactical"))
        tmod.save_template(_basic_template(id_="other",    name="Other"))
        files = sorted(p.name for p in fake_dir.glob("*.yaml"))
        # First template gets 00-, second gets 01-.
        assert files[0].startswith("00-")
        assert files[1].startswith("01-")
        assert files[0].endswith("tactical.yaml")
        assert files[1].endswith("other.yaml")

    def test_save_overwrites_same_id(self, templates_tmp):
        tmod, fake_dir, _ = templates_tmp
        t1 = _basic_template(id_="tactical", name="Original")
        tmod.save_template(t1)
        t2 = _basic_template(id_="tactical", name="Renamed")
        tmod.save_template(t2, allow_id_collision=True)
        files = list(fake_dir.glob("*.yaml"))
        assert len(files) == 1   # not 2
        loaded = tmod.load_by_id("tactical")
        assert loaded["name"] == "Renamed"

    def test_save_rejects_bad_folder(self, templates_tmp):
        tmod, *_ = templates_tmp
        t = _basic_template()
        t["folders"] = ["a/b", "../escape", "c"]
        with pytest.raises(ValueError, match="traversal"):
            tmod.save_template(t)

    def test_save_normalizes_backslash_folders(self, templates_tmp):
        tmod, *_ = templates_tmp
        t = _basic_template()
        t["folders"] = ["a\\b\\c", "d/e"]
        tmod.save_template(t)
        loaded = tmod.load_by_id("tactical")
        assert loaded["folders"] == ["a/b/c", "d/e"]


# ── Delete ─────────────────────────────────────────────────────────────────

class TestDelete:
    def test_delete_removes_file(self, templates_tmp):
        tmod, fake_dir, _ = templates_tmp
        tmod.save_template(_basic_template())
        assert any(fake_dir.glob("*tactical.yaml"))
        assert tmod.delete_template("tactical") is True
        assert not any(fake_dir.glob("*tactical.yaml"))

    def test_delete_missing_returns_false(self, templates_tmp):
        tmod, *_ = templates_tmp
        assert tmod.delete_template("does-not-exist") is False


# ── Import / export ────────────────────────────────────────────────────────

class TestImportExport:
    def test_import_validates(self, templates_tmp, tmp_path):
        tmod, *_ = templates_tmp
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            yaml.dump({"schema_version": 1, "id": "x", "name": "X",
                       "folders": ["a"], "fields": [
                           {"key": "f", "label": "F", "kind": "nonexistent",
                            "default": ""}]}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="kind"):
            tmod.import_template(bad)

    def test_import_collision_rejected(self, templates_tmp, tmp_path):
        tmod, *_ = templates_tmp
        tmod.save_template(_basic_template())
        # Try to import another template with the same id
        clone = tmp_path / "clone.yaml"
        clone.write_text(yaml.dump(_basic_template(name="Other")), encoding="utf-8")
        with pytest.raises(ValueError, match="already exists"):
            tmod.import_template(clone)

    def test_import_force_overwrites(self, templates_tmp, tmp_path):
        tmod, *_ = templates_tmp
        tmod.save_template(_basic_template(name="Original"))
        clone = tmp_path / "clone.yaml"
        clone.write_text(yaml.dump(_basic_template(name="Replaced")), encoding="utf-8")
        loaded = tmod.import_template(clone, force=True)
        assert loaded["name"] == "Replaced"

    def test_export_writes_yaml(self, templates_tmp, tmp_path):
        tmod, *_ = templates_tmp
        tmod.save_template(_basic_template())
        out = tmp_path / "out.yaml"
        written = tmod.export_template("tactical", out)
        assert written == out
        loaded = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert loaded["id"] == "tactical"

    def test_export_to_directory_uses_id_name(self, templates_tmp, tmp_path):
        tmod, *_ = templates_tmp
        tmod.save_template(_basic_template())
        out_dir = tmp_path / "exports"
        out_dir.mkdir()
        written = tmod.export_template("tactical", out_dir)
        assert written == out_dir / "tactical.yaml"
        assert written.exists()

    def test_export_missing_id_raises(self, templates_tmp, tmp_path):
        tmod, *_ = templates_tmp
        with pytest.raises(ValueError, match="no template"):
            tmod.export_template("does-not-exist", tmp_path / "out.yaml")


# ── Migration ──────────────────────────────────────────────────────────────

class TestMigration:
    def _legacy_two(self) -> list[dict]:
        return [
            {
                "name":          "Tactical",
                "type":          "tlc",
                "category":      "tvc",
                "client":        "",
                "director":      "",
                "producer":      "",
                "collaborators": [],
                "folders":       ["01_Intake", "02_Workfiles", "03_Exports"],
            },
            {
                "name":          "Digital Signage",
                "type":          "tlc",
                "category":      "digital-signage",
                "client":        "",
                "director":      None,
                "producer":      None,
                "collaborators": [],
                "folders":       ["01_Intake", "02_Workfiles", "03_Exports"],
            },
        ]

    def test_migration_writes_per_file(self, templates_tmp):
        tmod, fake_dir, fake_file = templates_tmp
        fake_file.write_text(json.dumps(self._legacy_two()), encoding="utf-8")
        tmod._migrate_if_needed()
        assert fake_dir.exists()
        names = sorted(p.name for p in fake_dir.glob("*.yaml"))
        assert any("tactical" in n for n in names)
        assert any("digital-signage" in n for n in names)
        # Legacy file is .bak-renamed, NOT deleted
        assert not fake_file.exists()
        assert any(p.name.startswith("templates.json.migrated-") for p in fake_file.parent.iterdir())

    def test_migration_preserves_folder_lists(self, templates_tmp):
        tmod, fake_dir, fake_file = templates_tmp
        fake_file.write_text(json.dumps(self._legacy_two()), encoding="utf-8")
        tmod._migrate_if_needed()
        tactical = tmod.load_by_id("tactical")
        assert tactical is not None
        assert tactical["folders"] == ["01_Intake", "02_Workfiles", "03_Exports"]

    def test_migration_omits_explicit_none_legacy_fields(self, templates_tmp):
        """DS template had director=None and producer=None — those should NOT appear in fields."""
        tmod, fake_dir, fake_file = templates_tmp
        fake_file.write_text(json.dumps(self._legacy_two()), encoding="utf-8")
        tmod._migrate_if_needed()
        ds = tmod.load_by_id("digital-signage")
        assert ds is not None
        keys = [f["key"] for f in ds["fields"]]
        assert "director" not in keys
        assert "producer" not in keys
        # But type/category and client (empty string) are still there
        assert "type" in keys
        assert "category" in keys

    def test_migration_idempotent(self, templates_tmp):
        tmod, fake_dir, fake_file = templates_tmp
        fake_file.write_text(json.dumps(self._legacy_two()), encoding="utf-8")
        tmod._migrate_if_needed()
        first_files = sorted(p.name for p in fake_dir.glob("*.yaml"))
        # Second call must NOT clobber existing files
        tmod._migrate_if_needed()
        second_files = sorted(p.name for p in fake_dir.glob("*.yaml"))
        assert first_files == second_files

    def test_clean_install_seeds_defaults(self, templates_tmp):
        tmod, fake_dir, fake_file = templates_tmp
        # No legacy file, no templates dir → seed
        tmod._migrate_if_needed()
        ids = {t["id"] for t in tmod.load_all(_run_migration=False)}
        assert "tactical"        in ids
        assert "digital-signage" in ids
        assert "custom"          in ids

    def test_warn_if_legacy_alongside_new(self, templates_tmp, capsys):
        tmod, fake_dir, fake_file = templates_tmp
        # Set up post-migration state with both dir and legacy file
        tmod.save_template(_basic_template())
        fake_file.write_text("[]", encoding="utf-8")
        tmod._migrate_if_needed()
        captured = capsys.readouterr()
        assert "legacy" in captured.err.lower() or "ignoring" in captured.err.lower()


# ── Load — malformed templates gracefully skipped ──────────────────────────

class TestLoadResilience:
    def test_malformed_yaml_skipped(self, templates_tmp, capsys):
        tmod, fake_dir, _ = templates_tmp
        fake_dir.mkdir(parents=True, exist_ok=True)
        (fake_dir / "bad.yaml").write_text("this is: not valid: : yaml ::", encoding="utf-8")
        tmod.save_template(_basic_template())
        loaded = tmod.load_all(_run_migration=False)
        ids = [t["id"] for t in loaded]
        assert "tactical" in ids
        captured = capsys.readouterr()
        # Either malformed or invalid warning written
        assert "bad.yaml" in captured.err

    def test_invalid_structure_skipped(self, templates_tmp, capsys):
        tmod, fake_dir, _ = templates_tmp
        fake_dir.mkdir(parents=True, exist_ok=True)
        # Valid YAML but missing schema_version
        (fake_dir / "00-broken.yaml").write_text(
            yaml.dump({"id": "broken", "name": "Broken", "folders": ["a"], "fields": []}),
            encoding="utf-8",
        )
        tmod.save_template(_basic_template())
        loaded = tmod.load_all(_run_migration=False)
        ids = [t["id"] for t in loaded]
        assert "tactical" in ids
        assert "broken" not in ids
        captured = capsys.readouterr()
        assert "broken" in captured.err.lower() or "schema" in captured.err.lower()


# ── Nested folders create on disk ──────────────────────────────────────────

class TestNestedFolders:
    def test_nested_paths_create_tree(self, templates_tmp, tmp_path):
        """A template with nested paths should produce the expected tree under a project root."""
        tmod, *_ = templates_tmp
        t = _basic_template()
        t["folders"] = [
            "01_Intake",
            "02_Workfiles/Plates",
            "02_Workfiles/Renders/EXR",
            "03_Exports/h264",
        ]
        tmod.save_template(t)
        loaded = tmod.load_by_id("tactical")
        assert loaded is not None

        # Simulate project init folder creation
        project = tmp_path / "PROJ-X"
        project.mkdir()
        for d in loaded["folders"]:
            (project / d).mkdir(parents=True, exist_ok=True)

        assert (project / "01_Intake").is_dir()
        assert (project / "02_Workfiles" / "Plates").is_dir()
        assert (project / "02_Workfiles" / "Renders" / "EXR").is_dir()
        assert (project / "03_Exports" / "h264").is_dir()


# ── template_field_defaults helper ─────────────────────────────────────────

class TestFieldDefaults:
    def test_collects_defaults_in_order(self, templates_tmp):
        tmod, *_ = templates_tmp
        t = _basic_template()
        t["fields"].append({"key": "supervisor", "label": "Sup", "kind": "text",
                            "default": "Default Sup"})
        defaults = tmod.template_field_defaults(t)
        assert defaults["type"]       == "tlc"
        assert defaults["client"]     == ""
        assert defaults["supervisor"] == "Default Sup"


# ── End-to-end: build_project_record honors field_values ───────────────────

class TestBuildProjectRecordIntegration:
    def test_unknown_fields_land_top_level(self, templates_tmp):
        from copalpm.pm import build_project_record
        rec = build_project_record(
            "PROJ-X", "X",
            field_values={
                "type":       "r_and_d",       # unknown enum-wise, but no enum check now
                "category":   "experimental",
                "supervisor": "S",
                "client":     "C",
            },
            deadline=None,
        )
        assert rec["type"]            == "r_and_d"
        assert rec["category"]        == "experimental"
        assert rec["supervisor"]      == "S"
        assert rec["client"]["name"]  == "C"

    def test_validate_no_longer_enum_checks(self, templates_tmp, tmp_path):
        """Custom type/category values must NOT fail `copalpm record validate`."""
        from copalpm.pm import build_project_record
        from copalpm.project_record import save_yaml, cmd_validate

        rec = build_project_record(
            "PROJ-X", "X",
            field_values={"type": "r_and_d", "category": "experimental"},
            deadline=None,
        )
        yaml_path = tmp_path / "project.yaml"
        save_yaml(yaml_path, rec)

        # cmd_validate reads project.yaml from the CWD or via --file. Build a
        # mock args object with --file.
        class _A:
            file = str(yaml_path)
            project = None
        # cmd_validate prints + sys.exits(1) on errors. Successful validate
        # returns None and does NOT exit.
        try:
            cmd_validate(_A())
        except SystemExit as e:
            pytest.fail(f"cmd_validate failed unexpectedly: exit={e.code}")
