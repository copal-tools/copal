"""CopalVX integration for pm-tui: version fetching and subprocess push/pull."""
import json
import os
import platform
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


def _config() -> dict:
    cfg_path = Path.home() / ".copal" / "config.json"
    if not cfg_path.exists():
        return {}
    return json.loads(cfg_path.read_text(encoding="utf-8-sig"))


def _base_url() -> str:
    cfg = _config()
    ip   = cfg.get("server_ip", "192.168.178.161")
    port = cfg.get("api_port", 8005)
    return f"http://{ip}:{port}"


def _client_path() -> str | None:
    return _config().get("client_path")


def get_versions(project_name: str) -> list[str]:
    """Returns version list (newest first), or [] on any error."""
    try:
        url = f"{_base_url()}/projects/{project_name}/versions"
        with urllib.request.urlopen(url, timeout=8) as r:
            return json.loads(r.read())
    except Exception:
        return []


def health() -> dict:
    """Returns health dict, or {"healthy": False} on error."""
    try:
        url = f"{_base_url()}/health"
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return {"healthy": False, "services": {}}


def _resolve_copalvx(subcmd_args: list[str]) -> tuple[list[str], str | None]:
    """Return (full command list, cwd-or-None) for running copalvx.

    Strategy:
      1. If `copalvx` is on PATH (e.g. installed via `uv tool install .`), run it
         directly — no cwd required.
      2. Otherwise fall back to `uv run copalvx` from client_path (dev setup).
      3. If neither is available, raise a helpful RuntimeError.
    """
    if shutil.which("copalvx"):
        return ["copalvx"] + subcmd_args, None

    client_dir = _client_path()
    if client_dir:
        return ["uv", "run", "copalvx"] + subcmd_args, client_dir

    if platform.system() == "Windows":
        example = r"C:\Users\You\Development\Copal-VX\client"
    else:
        example = "/Users/you/Development/Copal-VX/client"

    raise RuntimeError(
        "CopalVX not found. Fix one of:\n"
        f"  A) Install as a tool: cd <client-dir> && uv tool install .\n"
        f"  B) Add to ~/.copal/config.json: \"client_path\": \"{example}\""
    )


def _popen(cmd: list[str], cwd: str | None) -> subprocess.Popen:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    return subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        text=True,
        encoding="utf-8",
        env=env,
    )


def rename_project(old_name: str, new_name: str) -> None:
    """Renames a CopalVX project on the server. Raises RuntimeError on failure."""
    url  = f"{_base_url()}/projects/{old_name}"
    data = json.dumps({"new_name": new_name}).encode("utf-8")
    req  = urllib.request.Request(
        url, data=data, method="PATCH",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Rename failed ({e.code}): {body}")


def delete_project(project_name: str, delete_orphan_files: bool = False) -> dict:
    """Deletes a CopalVX project from the server. Raises RuntimeError on failure."""
    url  = f"{_base_url()}/projects/{project_name}"
    data = json.dumps({"delete_orphan_files": delete_orphan_files}).encode("utf-8")
    req  = urllib.request.Request(
        url, data=data, method="DELETE",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Delete failed ({e.code}): {body}")


def run_push(project: str, tag: str, path: str, message: str = "", author: str = "") -> subprocess.Popen:
    """Starts a non-interactive push subprocess. Returns the Popen object."""
    args = ["push", project, tag, path]
    if message:
        args += ["--message", message]
    if author:
        args += ["--author", author]
    cmd, cwd = _resolve_copalvx(args)
    return _popen(cmd, cwd)


def run_pull(project: str, tag: str, target: str, policy: str = "backup") -> subprocess.Popen:
    """Starts a non-interactive pull subprocess. Returns the Popen object."""
    args = ["pull", project, tag, target, "--policy", policy]
    cmd, cwd = _resolve_copalvx(args)
    return _popen(cmd, cwd)
