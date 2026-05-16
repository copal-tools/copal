# src/copalblender/cli.py
# `copalblender` — install, uninstall, or report the addon's state across
# every Blender version detected on the host OS.

import argparse
import sys

from copalblender import installer


def _print_results(results: list[tuple[str, bool, str]], verb: str) -> int:
    """Print one line per Blender version and return an aggregate exit code."""
    if not results:
        return 1
    for ver, ok, msg in results:
        tag = "ok  " if ok else "FAIL"
        print(f"  [{tag}] {ver}: {msg}")
    return 0 if all(ok for _, ok, _ in results) else 1


def cmd_install(_args: argparse.Namespace) -> int:
    versions = installer.detect_installs()
    if not versions:
        print(
            "error: No Blender installs detected. Open Blender at least once to create the user config directory.",
            file=sys.stderr,
        )
        return 1
    return _print_results(installer.install_addon(versions), "install")


def cmd_uninstall(_args: argparse.Namespace) -> int:
    versions = installer.detect_installs()
    if not versions:
        print("error: No Blender installs detected. Nothing to uninstall.", file=sys.stderr)
        return 1
    return _print_results(installer.uninstall_addon(versions), "uninstall")


def cmd_status(_args: argparse.Namespace) -> int:
    versions = installer.detect_installs()
    if not versions:
        print("No Blender installs detected.")
        return 1
    for ver, present, addon_path in installer.status(versions):
        marker = "installed" if present else "not installed"
        print(f"  {ver}: {marker}  ({addon_path})")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="copalblender",
        description="Install the CopalPM time-tracking addon into Blender.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("install", help="install the addon into every detected Blender version").set_defaults(
        func=cmd_install
    )
    sub.add_parser("uninstall", help="remove the addon from every detected Blender version").set_defaults(
        func=cmd_uninstall
    )
    sub.add_parser("status", help="report addon-installed state per Blender version").set_defaults(
        func=cmd_status
    )

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
