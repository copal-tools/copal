# copal_core/pm_hooks.py
# Integration hooks that wire CopalVX push/pull events into the pm project
# management system (ProjectRegistry).
#
# Design contract:
#   - Every hook is non-fatal.  If the pm tools are missing from PATH or a
#     project.yaml cannot be found, a warning is printed and CopalVX continues.
#   - Hooks communicate with pm entirely via subprocess (the `project` and `pm`
#     CLI entry points).  No direct YAML parsing here — that lives in
#     project_record.py where pyyaml is available.
#
# Hook summary:
#   hook_pre_push(root_dir)
#       → `project sync-time`  — flush pending time sessions into project.yaml
#         before the CopalVX scan so time data travels with the push.
#
#   hook_post_push(root_dir, project_name, version_tag)
#       → `project copalvx-update`  — write the CopalVX block (project_name,
#         last_push, last_push_version) into project.yaml after a successful push.
#
#   hook_post_pull(target_dir, project_name, version_tag)
#       → `pm register <path>`   — add the pulled project to the machine-local
#         pm registry so it is visible to `pm list` and the `project` CLI.
#       → `project get copalvx.*` — read back the copalvx block and display it
#         so the user can confirm the project identity recorded in the record.

import os
import shutil
import subprocess


# ── Internal helpers ───────────────────────────────────────────────────────────

def _find_project_yaml(start_dir):
    """Walk up the directory tree from start_dir until project.yaml is found.

    Returns the absolute path to the first project.yaml found, or None if the
    filesystem root is reached without finding one.
    """
    path = os.path.abspath(start_dir)
    while True:
        candidate = os.path.join(path, "project.yaml")
        if os.path.exists(candidate):
            return candidate
        parent = os.path.dirname(path)
        if parent == path:
            # Reached the filesystem root with no match
            return None
        path = parent


def _run(cmd, label=""):
    """Run a CLI command via subprocess and return its stdout, or None on failure.

    Failures are non-fatal: a warning is printed and None is returned so the
    caller can decide how to proceed.  The timeout is intentionally short (30s)
    — pm operations should be instantaneous; anything slower is a bug.
    """
    # Verify the executable exists on PATH before attempting to run it
    exe = shutil.which(cmd[0])
    if not exe:
        print(f"⚠️  [pm] '{cmd[0]}' not found in PATH — skipping {label} hook.")
        return None

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            # Print stderr so the user knows why the hook failed
            print(f"⚠️  [pm] {label} hook failed: {result.stderr.strip()}")
            return None
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        print(f"⚠️  [pm] {label} hook timed out after 30s.")
        return None
    except Exception as e:
        print(f"⚠️  [pm] {label} hook error: {e}")
        return None


# ── Public hooks ───────────────────────────────────────────────────────────────

def hook_pre_push(root_dir):
    """Hook 1 — pre-push: sync pending time sessions into project.yaml.

    Runs `project sync-time` against the project.yaml found at or above
    root_dir.  This ensures all locally-tracked time data is embedded in the
    record before CopalVX uploads the folder, so the time history travels with
    the push to other machines.

    Silently skips if no project.yaml is found (not every CopalVX project is a
    pm project).
    """
    yaml_path = _find_project_yaml(root_dir)
    if not yaml_path:
        # Not a pm-managed project — nothing to do
        return

    print("🔄 [pm] Syncing time entries into project.yaml...")
    _run(["project", "sync-time", "--file", yaml_path], "pre-push sync-time")


def hook_post_push(root_dir, project_name, version_tag):
    """Hook 2 — post-push: write CopalVX metadata into project.yaml.

    Runs `project copalvx-update` to record the CopalVX project name and the
    version tag that was just pushed.  This stamps the project record so any
    machine that pulls later can see which CopalVX project/version the folder
    corresponds to.

    Silently skips if no project.yaml is found.
    """
    yaml_path = _find_project_yaml(root_dir)
    if not yaml_path:
        return

    _run(
        [
            "project", "copalvx-update",
            "--file",         yaml_path,
            "--project-name", project_name,
            "--version",      version_tag,
        ],
        "post-push copalvx-update",
    )
    print("📋 [pm] project.yaml CopalVX block updated.")


def hook_post_pull(target_dir, project_name, version_tag):
    """Hooks 3 & 4 — post-pull: register the project and display CopalVX metadata.

    Hook 3: Runs `pm register <target_dir>` so the pulled project is added to
    the machine-local pm registry (~/.config/project-registry/registry.json).
    This makes the folder visible to `pm list` and discoverable by `project` CLI
    CWD detection.

    Hook 4: Reads the copalvx block from the pulled project.yaml (if present)
    and prints the project name and last-push timestamp for user confirmation.

    Both steps are non-fatal — a missing project.yaml or absent pm tool only
    produces a warning.
    """
    abs_path = os.path.abspath(target_dir)

    # Hook 3 — register with pm so `pm list` and `project` CWD detection work
    print("📋 [pm] Registering project in pm registry...")
    _run(["pm", "register", abs_path], "post-pull register")

    # Hook 4 — display the CopalVX block from the pulled project.yaml
    yaml_path = _find_project_yaml(abs_path)
    if not yaml_path:
        # No project.yaml in the pulled folder — nothing more to do
        return

    copal_name  = _run(["project", "get", "copalvx.project_name", "--file", yaml_path],  "post-pull read name")
    last_push   = _run(["project", "get", "copalvx.last_push",    "--file", yaml_path],  "post-pull read last_push")

    if copal_name:
        print(f"ℹ️  [pm] CopalVX project: {copal_name} | last push: {last_push or 'unknown'}")
