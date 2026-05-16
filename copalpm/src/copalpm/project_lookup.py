# src/copalpm/project_lookup.py
# `copalpm whose <path>` — given any filesystem path, report which registered
# project (if any) it belongs to.
#
# Hybrid lookup with strict drift recovery:
#   1. Registry-prefix fast path (zero filesystem stats): normalize the target
#      and every registry entry's stored path, pick the deepest registry root
#      that is an ancestor of the target.
#   2. Walk-up fallback: only on a Pass-1 miss. Walk parents looking for
#      project.yaml; if found, the YAML's id must ALSO be in the registry for
#      this to count as a match (we never return an unregistered project, since
#      downstream callers like "start timer for X" would fail).
#
# Pass-1 success → drift=False, matched_via="registry".
# Pass-2 success → drift=True,  matched_via="walk-up".
# Both miss     → None.

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from copalpm.pm import load_registry


@dataclass(frozen=True)
class ProjectMatch:
    project_id:   str
    project_name: str
    project_root: Path       # canonical root: registry's path on Pass 1, YAML's parent on Pass 2
    drift:        bool       # True iff registry's stored path differs from on-disk location
    matched_via:  str        # "registry" | "walk-up"


# ── Path normalization ────────────────────────────────────────────────────────

def _norm(p: Path) -> str:
    """Canonical string for prefix comparison.

    Resolves symlinks and relative components (strict=False so non-existent
    targets still work), then applies platform case folding via
    `os.path.normcase` — no-op on POSIX, lowercases on Windows.
    """
    try:
        resolved = p.resolve(strict=False)
    except OSError:
        # Pathological inputs (e.g. invalid drive on Windows) → fall back to
        # the raw path; the prefix check will simply fail to match.
        resolved = p
    return os.path.normcase(str(resolved))


def _is_under(child_norm: str, root_norm: str) -> bool:
    """True iff `child_norm` equals `root_norm` or is a descendant.

    Anchored at `os.sep` so `C:\\Work\\Alpha` does not spuriously match
    `C:\\Work\\AlphaBeta`.
    """
    if child_norm == root_norm:
        return True
    return child_norm.startswith(root_norm + os.sep)


# ── Walk-up (drift-recovery fallback) ─────────────────────────────────────────

def _walk_up_for_yaml(start: Path) -> Path | None:
    """Walk parents from `start` looking for project.yaml.

    Returns the path to the YAML file, or None if no project.yaml is found
    before the filesystem root. Unlike `project_record.find_project_yaml`,
    this never calls `sys.exit` — callers are expected to handle None.
    Permission errors during traversal are swallowed (treated as "not found").
    """
    try:
        current = start.resolve(strict=False)
    except OSError:
        return None
    for directory in [current, *current.parents]:
        candidate = directory / "project.yaml"
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return None


# ── Core primitive ────────────────────────────────────────────────────────────

def find_project_for_path(
    target: Path,
    registry: list[dict] | None = None,
) -> ProjectMatch | None:
    """Resolve `target` (a file or folder) to the registered project it lives in.

    Pass 1 (registry-prefix) finds the deepest registry root that is an
    ancestor of the target. Pass 2 (walk-up) handles the case where the
    project folder was renamed/moved out from under the registry: walks
    parents for project.yaml, and only returns a match if the YAML's id is
    still in the registry.

    Returns None when no registered project contains the target.
    """
    if registry is None:
        registry = load_registry()

    target_norm = _norm(target)

    # Pass 1 — registry-prefix, deepest match wins
    best_entry: dict | None = None
    best_root_len = -1
    for entry in registry:
        root = entry.get("path")
        if not root:
            continue
        root_norm = _norm(Path(root))
        if _is_under(target_norm, root_norm) and len(root_norm) > best_root_len:
            best_entry = entry
            best_root_len = len(root_norm)

    if best_entry is not None:
        return ProjectMatch(
            project_id=best_entry["id"],
            project_name=best_entry.get("name", "") or "",
            project_root=Path(best_entry["path"]),
            drift=False,
            matched_via="registry",
        )

    # Pass 2 — walk-up fallback (drift recovery)
    # Files: walk from the parent. Folders (and non-existent paths): walk from the target itself.
    try:
        start = target.parent if target.is_file() else target
    except OSError:
        start = target
    yaml_path = _walk_up_for_yaml(start)
    if yaml_path is None:
        return None

    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None

    project_id = data.get("id")
    if not project_id:
        return None

    # Strict: only return if the YAML's id is registered. The registry's stored
    # path is by definition stale (Pass 1 missed), so use the YAML's parent.
    for entry in registry:
        if entry.get("id") == project_id:
            return ProjectMatch(
                project_id=project_id,
                project_name=entry.get("name", "") or data.get("name", "") or "",
                project_root=yaml_path.parent,
                drift=True,
                matched_via="walk-up",
            )
    return None


# ── CLI handler ───────────────────────────────────────────────────────────────

def _match_to_json_dict(match: ProjectMatch) -> dict:
    d = asdict(match)
    d["project_root"] = str(match.project_root)
    return d


def cmd_whose(args) -> None:
    """`copalpm whose <path> [--json]` — exit 0 on match, 1 on miss."""
    target = Path(args.path)
    match = find_project_for_path(target)

    if args.json:
        if match is None:
            print("null")
            sys.exit(1)
        print(json.dumps(_match_to_json_dict(match)))
        return

    if match is None:
        print(f"{target}: not in any registered project", file=sys.stderr)
        sys.exit(1)

    label = f'{match.project_id} "{match.project_name}"' if match.project_name else match.project_id
    drift_note = "  (drift)" if match.drift else ""
    print(f"{label}{drift_note}")
    print(f"  root: {match.project_root}")
    print(f"  via:  {match.matched_via}")
