"""Unit tests for the copalvx subprocess wrapper.

Specifically locks in the `--` separator before positionals so project names
starting with `-` (e.g. `-40-140526`) don't get parsed as argparse flags by
the receiving `copalvx pull`/`push` subprocess.
"""

import subprocess
from unittest import mock

from copalpm import copalvx_api


def _captured_argv(monkeypatch) -> list[str]:
    """Patch _popen + _resolve_copalvx to capture the args list the wrapper builds."""
    seen: dict[str, list[str]] = {}

    def fake_resolve(subcmd_args):
        return ["copalvx"] + subcmd_args, None

    def fake_popen(cmd, cwd):
        seen["cmd"] = cmd
        # Return a minimal stand-in; tests only inspect `seen["cmd"]`.
        return mock.MagicMock(spec=subprocess.Popen)

    monkeypatch.setattr(copalvx_api, "_resolve_copalvx", fake_resolve)
    monkeypatch.setattr(copalvx_api, "_popen", fake_popen)
    return seen


def test_run_pull_uses_double_dash_separator(monkeypatch):
    seen = _captured_argv(monkeypatch)
    copalvx_api.run_pull("MyProj", "v1.0", r"E:\Projects\MyProj")
    assert seen["cmd"] == [
        "copalvx", "pull", "--policy", "backup",
        "--", "MyProj", "v1.0", r"E:\Projects\MyProj",
    ]


def test_run_pull_handles_dash_prefixed_project_name(monkeypatch):
    """The bug: argparse on the receiver would treat '-40-140526' as a flag."""
    seen = _captured_argv(monkeypatch)
    copalvx_api.run_pull("-40-140526", "v1.0", r"E:\Some\Path")
    cmd = seen["cmd"]
    # `--` must appear before the project name so argparse sees it positionally.
    sep = cmd.index("--")
    assert cmd[sep + 1] == "-40-140526"
    assert cmd[sep + 2] == "v1.0"
    assert cmd[sep + 3] == r"E:\Some\Path"


def test_run_pull_with_prefixes(monkeypatch):
    seen = _captured_argv(monkeypatch)
    copalvx_api.run_pull("MyProj", "v1.0", "/tmp/x", prefixes=["a", "b"])
    cmd = seen["cmd"]
    # Prefixes (options) must come before `--`, positionals after.
    assert "--prefix" in cmd
    sep = cmd.index("--")
    assert all(opt_idx < sep for opt_idx, val in enumerate(cmd) if val == "--prefix")
    # The three positionals come right after `--`.
    assert cmd[sep + 1:] == ["MyProj", "v1.0", "/tmp/x"]


def test_run_pull_policy_override(monkeypatch):
    seen = _captured_argv(monkeypatch)
    copalvx_api.run_pull("MyProj", "v1.0", "/tmp/x", policy="overwrite")
    assert "--policy" in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("--policy") + 1] == "overwrite"


def test_run_push_uses_double_dash_separator(monkeypatch):
    seen = _captured_argv(monkeypatch)
    copalvx_api.run_push("MyProj", "v1.0", r"E:\Projects\MyProj")
    assert seen["cmd"] == ["copalvx", "push", "--", "MyProj", "v1.0", r"E:\Projects\MyProj"]


def test_run_push_handles_dash_prefixed_project_name(monkeypatch):
    seen = _captured_argv(monkeypatch)
    copalvx_api.run_push("-weird-name", "v2.0", r"E:\x", message="m", author="a")
    cmd = seen["cmd"]
    sep = cmd.index("--")
    # Options before separator, positionals after.
    assert "--message" in cmd[:sep] and "--author" in cmd[:sep]
    assert cmd[sep + 1:] == ["-weird-name", "v2.0", r"E:\x"]
