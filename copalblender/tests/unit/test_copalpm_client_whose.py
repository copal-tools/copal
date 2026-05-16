"""Unit tests for the vendored copalpm_client.whose subprocess wrapper."""

import json
import subprocess

import pytest

import copalpm_client  # vendored module on sys.path via conftest.py


def _mk_run(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Build a subprocess.run replacement returning a fixed CompletedProcess."""
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], returncode, stdout, stderr)
    return fake_run


def test_whose_match_returns_dict(monkeypatch):
    monkeypatch.setattr(copalpm_client, "_resolve_copalpm", lambda override=None: "/fake/copalpm")
    payload = {
        "project_id": "PROJ-FOO-160526",
        "project_name": "Foo",
        "project_root": "/abs/Foo",
        "drift": False,
        "matched_via": "registry",
    }
    monkeypatch.setattr(subprocess, "run", _mk_run(stdout=json.dumps(payload)))
    assert copalpm_client.whose("/abs/Foo/scene.blend") == payload


def test_whose_null_returns_none(monkeypatch):
    """copalpm whose prints 'null' and exits 1 when the path is in no project."""
    monkeypatch.setattr(copalpm_client, "_resolve_copalpm", lambda override=None: "/fake/copalpm")
    monkeypatch.setattr(subprocess, "run", _mk_run(stdout="null", returncode=1))
    assert copalpm_client.whose("/somewhere/else.blend") is None


def test_whose_empty_stdout_returns_none(monkeypatch):
    monkeypatch.setattr(copalpm_client, "_resolve_copalpm", lambda override=None: "/fake/copalpm")
    monkeypatch.setattr(subprocess, "run", _mk_run(stdout="", returncode=1))
    assert copalpm_client.whose("/somewhere/else.blend") is None


def test_whose_invalid_json_raises(monkeypatch):
    monkeypatch.setattr(copalpm_client, "_resolve_copalpm", lambda override=None: "/fake/copalpm")
    monkeypatch.setattr(subprocess, "run", _mk_run(stdout="this is not json"))
    with pytest.raises(copalpm_client.CopalPMError):
        copalpm_client.whose("/x.blend")


def test_whose_filenotfound_raises_not_installed(monkeypatch):
    """If the resolved binary vanishes between resolve and exec, surface NotInstalledError."""
    monkeypatch.setattr(copalpm_client, "_resolve_copalpm", lambda override=None: "/fake/copalpm")

    def boom(*a, **kw):
        raise FileNotFoundError("[WinError 2] The system cannot find the file specified")

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(copalpm_client.NotInstalledError):
        copalpm_client.whose("/x.blend")


def test_whose_sets_utf8_encoding_env(monkeypatch):
    """copalpm gotcha #6: subprocess env must set PYTHONIOENCODING=utf-8."""
    monkeypatch.setattr(copalpm_client, "_resolve_copalpm", lambda override=None: "/fake/copalpm")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, "null", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    copalpm_client.whose("/x.blend")
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"


def test_whose_passes_override_through(monkeypatch):
    captured = {}

    def fake_resolve(override=None):
        captured["override"] = override
        return "/fake/copalpm"

    monkeypatch.setattr(copalpm_client, "_resolve_copalpm", fake_resolve)
    monkeypatch.setattr(subprocess, "run", _mk_run(stdout="null", returncode=1))
    copalpm_client.whose("/x.blend", copalpm_path_override="/custom/copalpm")
    assert captured["override"] == "/custom/copalpm"


# ── _resolve_copalpm ───────────────────────────────────────────────────────────

def test_resolve_uses_override_when_present(tmp_path, monkeypatch):
    binary = tmp_path / "copalpm"
    binary.write_text("#!/bin/sh\n")
    assert copalpm_client._resolve_copalpm(str(binary)) == str(binary)


def test_resolve_falls_through_when_override_missing(tmp_path, monkeypatch):
    """If override doesn't exist, fall through to shutil.which."""
    monkeypatch.setattr(copalpm_client.shutil, "which", lambda name: "/usr/local/bin/copalpm")
    result = copalpm_client._resolve_copalpm(str(tmp_path / "missing"))
    assert result == "/usr/local/bin/copalpm"


def test_resolve_uses_shutil_which(monkeypatch):
    monkeypatch.setattr(copalpm_client.shutil, "which", lambda name: "/from/which/copalpm")
    assert copalpm_client._resolve_copalpm() == "/from/which/copalpm"


def test_resolve_raises_when_nothing_found(monkeypatch):
    monkeypatch.setattr(copalpm_client.shutil, "which", lambda name: None)
    # Point all fallbacks at non-existent locations.
    monkeypatch.setattr(copalpm_client, "_FALLBACK_PATHS_WINDOWS", ["C:\\definitely\\not\\here.exe"])
    monkeypatch.setattr(copalpm_client, "_FALLBACK_PATHS_UNIX", ["/definitely/not/here"])
    with pytest.raises(copalpm_client.NotInstalledError):
        copalpm_client._resolve_copalpm()
