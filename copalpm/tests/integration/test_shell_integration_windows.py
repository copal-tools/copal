"""Windows-only integration test for shell-integration registry round-trip.

Installs the Explorer verbs into HKCU, asserts the keys exist, then
uninstalls and asserts they're gone. Always restores prior state.

Auto-skips on macOS / Linux. Safe to run repeatedly.
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


def _key_exists(path: str) -> bool:
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, path).Close()
        return True
    except FileNotFoundError:
        return False


def _all_verb_keys() -> list[str]:
    paths = []
    for parent in si._WIN_PARENTS:
        for verb in si.VERBS:
            paths.append(f"{parent}\\{verb['id']}")
    return paths


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


def test_command_strings_are_quoted_in_registry():
    """Verify the actual command value written under .../command quotes the binary."""
    si._install_windows()
    try:
        # Pick the start-timer verb on the foreground context.
        parent = si._WIN_PARENTS[0]
        cmd_path = f"{parent}\\CopalStartTimer\\command"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cmd_path) as k:
            value, _ = winreg.QueryValueEx(k, "")
        assert value.startswith('"')
        assert "shell-trigger start" in value
        assert value.endswith('"%1"')
    finally:
        si._uninstall_windows()
