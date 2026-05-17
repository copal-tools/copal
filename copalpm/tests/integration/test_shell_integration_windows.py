"""Windows-only integration test for shell-integration registry round-trip.

Installs the Explorer verbs into HKLM, asserts the keys exist, then
uninstalls and asserts they're gone. Always restores prior state.

Auto-skips on macOS / Linux. Auto-skips when not running as Administrator
(install/uninstall require HKLM write access on Win11 24H2+).
Safe to run repeatedly.
"""

import sys
import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only registry round-trip",
)

if sys.platform == "win32":
    import winreg
    from copalpm import shell_integration as si

    _ADMIN_REQUIRED = pytest.mark.skipif(
        not si._is_admin(),
        reason="Needs Administrator (install/uninstall write to HKLM)",
    )
else:
    _ADMIN_REQUIRED = pytest.mark.skip(reason="Windows-only")


def _key_exists(path: str) -> bool:
    try:
        winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path).Close()
        return True
    except FileNotFoundError:
        return False


def _all_verb_keys() -> list[str]:
    """Every (verb, parent) pair that the installer is expected to register.

    Folder verbs land under the two `Directory` parents; file verbs land
    under `*\\shell` (the all-files class). Returns the union as fully-
    qualified relative paths.
    """
    paths = []
    for verb in si.VERBS:
        for parent in si._win_parents_for(verb["target"]):
            paths.append(f"{parent}\\{verb['id']}")
    return paths


@_ADMIN_REQUIRED
def test_round_trip_install_uninstall():
    # Snapshot prior state so we don't clobber a user's existing install.
    keys = _all_verb_keys()
    prior = {k: _key_exists(k) for k in keys}

    try:
        # Install — all keys should exist afterwards.
        rc = si._install_windows()
        assert rc == 0
        for k in keys:
            assert _key_exists(k), f"missing after install: {k}"

        # Uninstall — all keys should be gone.
        rc = si._uninstall_windows()
        assert rc == 0
        for k in keys:
            assert not _key_exists(k), f"still present after uninstall: {k}"

    finally:
        # Restore prior state (best-effort) so we don't disturb the dev env.
        if any(prior.values()):
            si._install_windows()
        else:
            si._uninstall_windows()


@_ADMIN_REQUIRED
def test_command_strings_are_quoted_in_registry():
    """Verify the actual command value written under .../command quotes the binary.

    Snapshots prior state so a developer running the test suite doesn't lose
    a real shell-integration install.
    """
    keys = _all_verb_keys()
    prior = {k: _key_exists(k) for k in keys}
    si._install_windows()
    try:
        # Pick the start-timer verb on the foreground context.
        parent = si._WIN_PARENTS[0]
        cmd_path = f"{parent}\\CopalStartTimer\\command"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, cmd_path) as k:
            value, _ = winreg.QueryValueEx(k, "")
        assert value.startswith('"')
        assert "shell-trigger start" in value
        assert value.endswith('"%1"')
    finally:
        if any(prior.values()):
            si._install_windows()
        else:
            si._uninstall_windows()


def test_install_without_admin_explains_and_returns_nonzero(monkeypatch, capsys):
    """When not elevated, install/uninstall should refuse cleanly with guidance."""
    monkeypatch.setattr(si, "_is_admin", lambda: False)
    rc = si._install_windows()
    assert rc != 0
    err = capsys.readouterr().err
    assert "Administrator" in err
    assert "Win+X" in err

    # Uninstall is admin-gated only when HKLM has keys to remove.
    # If we're non-admin AND HKLM is clean (e.g. on a fresh dev machine),
    # uninstall just sweeps stale HKCU keys without elevation.
    rc = si._uninstall_windows()
    if any(_key_exists(k) for k in _all_verb_keys()):
        # HKLM had keys; uninstall should have refused without admin
        assert rc != 0
    else:
        # Nothing in HKLM; uninstall is allowed to clean HKCU silently
        assert rc == 0


def test_status_works_without_admin(monkeypatch, capsys):
    """Status is a read-only query; should always work regardless of elevation."""
    monkeypatch.setattr(si, "_is_admin", lambda: False)
    rc = si._status_windows()
    assert rc == 0
    out = capsys.readouterr().out
    assert "HKLM" in out
