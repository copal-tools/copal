"""Unit tests for the `copalpm setup` / `copalpm teardown` umbrella commands.

Mocks out the underlying installers so these tests don't actually touch the
registry, launchd, or the file system. We're verifying the orchestration:
admin preflight, idempotency probes, skip flags, and the summary block.
"""

from __future__ import annotations

import argparse
import sys
import pytest

from copalpm import setup_cmd


def _ns(**kwargs):
    """Build an argparse-like Namespace with sane defaults for setup/teardown."""
    defaults = dict(
        service_only=False,
        shell_only=False,
        skip_service=False,
        skip_shell=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ── Admin preflight ──────────────────────────────────────────────────────────

@pytest.mark.skipif(sys.platform != "win32", reason="Windows admin gating only")
def test_setup_refuses_without_admin(monkeypatch, capsys):
    from copalpm import shell_integration as si
    monkeypatch.setattr(si, "_is_admin", lambda: False)

    rc = setup_cmd.cmd_setup(_ns())
    assert rc == 1
    err = capsys.readouterr().err
    assert "Administrator" in err
    assert "Win+X" in err


@pytest.mark.skipif(sys.platform != "win32", reason="Windows admin gating only")
def test_teardown_refuses_without_admin_when_things_are_installed(monkeypatch, capsys, tmp_path):
    from copalpm import shell_integration as si
    monkeypatch.setattr(si, "_is_admin", lambda: False)
    monkeypatch.setattr(setup_cmd, "_shell_integration_installed", lambda: True)
    # Force DATA_DIR to a tmp path that contains a config.json so service-uninstall
    # is in scope (and admin gating triggers).
    fake_data = tmp_path / "copalpm"
    fake_data.mkdir()
    (fake_data / "config.json").write_text("{}")
    monkeypatch.setattr(setup_cmd, "DATA_DIR", fake_data)

    rc = setup_cmd.cmd_teardown(_ns())
    assert rc == 1
    err = capsys.readouterr().err
    assert "Administrator" in err


# ── Skip flags ───────────────────────────────────────────────────────────────

def test_setup_shell_only_skips_service(monkeypatch, capsys):
    """--shell-only should leave the service installer untouched."""
    calls: list[str] = []
    monkeypatch.setattr(setup_cmd, "_do_service_install",
                        lambda: (calls.append("service"), (True, "stub"))[1])
    monkeypatch.setattr(setup_cmd, "_do_shell_install",
                        lambda: (calls.append("shell"), (True, "stub"))[1])
    # Skip Windows admin gate even on Windows since the test is platform-agnostic.
    if sys.platform == "win32":
        from copalpm import shell_integration as si
        monkeypatch.setattr(si, "_is_admin", lambda: True)

    rc = setup_cmd.cmd_setup(_ns(shell_only=True))
    assert rc == 0
    assert calls == ["shell"]


def test_setup_service_only_skips_shell(monkeypatch, capsys):
    calls: list[str] = []
    monkeypatch.setattr(setup_cmd, "_do_service_install",
                        lambda: (calls.append("service"), (True, "stub"))[1])
    monkeypatch.setattr(setup_cmd, "_do_shell_install",
                        lambda: (calls.append("shell"), (True, "stub"))[1])
    if sys.platform == "win32":
        from copalpm import shell_integration as si
        monkeypatch.setattr(si, "_is_admin", lambda: True)

    rc = setup_cmd.cmd_setup(_ns(service_only=True))
    assert rc == 0
    assert calls == ["service"]


def test_teardown_runs_shell_first(monkeypatch):
    """Shell-integration is removed before the service so users don't briefly
    have a working verb that points at a missing daemon."""
    order: list[str] = []
    monkeypatch.setattr(setup_cmd, "_do_shell_uninstall",
                        lambda: (order.append("shell"), (True, "stub"))[1])
    monkeypatch.setattr(setup_cmd, "_do_service_uninstall",
                        lambda: (order.append("service"), (True, "stub"))[1])
    if sys.platform == "win32":
        from copalpm import shell_integration as si
        monkeypatch.setattr(si, "_is_admin", lambda: True)
        monkeypatch.setattr(setup_cmd, "_shell_integration_installed", lambda: False)

    rc = setup_cmd.cmd_teardown(_ns())
    assert rc == 0
    assert order == ["shell", "service"]


# ── Summary / failure path ───────────────────────────────────────────────────

def test_setup_returns_nonzero_when_a_step_fails(monkeypatch, capsys):
    monkeypatch.setattr(setup_cmd, "_do_service_install",
                        lambda: (True, "stub-ok"))
    monkeypatch.setattr(setup_cmd, "_do_shell_install",
                        lambda: (False, "simulated failure"))
    if sys.platform == "win32":
        from copalpm import shell_integration as si
        monkeypatch.setattr(si, "_is_admin", lambda: True)

    rc = setup_cmd.cmd_setup(_ns())
    assert rc == 1
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "simulated failure" in out
    assert "Re-run after fixing" in out


def test_setup_summary_lists_each_step(monkeypatch, capsys):
    monkeypatch.setattr(setup_cmd, "_do_service_install",
                        lambda: (True, "installed"))
    monkeypatch.setattr(setup_cmd, "_do_shell_install",
                        lambda: (True, "installed"))
    if sys.platform == "win32":
        from copalpm import shell_integration as si
        monkeypatch.setattr(si, "_is_admin", lambda: True)

    rc = setup_cmd.cmd_setup(_ns())
    assert rc == 0
    out = capsys.readouterr().out
    assert "Background service" in out
    assert "Shell integration" in out
    assert "OK" in out
    assert "Setup complete" in out
