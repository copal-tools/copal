# CopalBlender

A Blender addon that auto-starts CopalPM time tracking for the project a `.blend` file belongs to.

When you open a `.blend` inside a registered CopalPM project, the addon starts a tracking session tagged `tool: "blender"`. Tracking stops on Blender quit, when you close the file (including loading a file from a different project or no project), after Blender stays unfocused for 5 minutes, or after your cursor sits still for two consecutive ping cycles. It restarts automatically when you move the cursor again.

CopalBlender ships two things in one package: a thin `copalblender` CLI for installing the addon, and the addon itself, bundled as an asset and copied into Blender's `scripts/addons/` at install time.

---

## Requirements

- Python 3.12+ on the host system (for the `copalblender` CLI).
- Blender 3.6+ (the addon's `bl_info` minimum).
- CopalPM installed and on `PATH`. Run `copalpm setup` to install the background service.

The addon talks to CopalPM via two channels:

| Channel | What it does |
|---------|--------------|
| Subprocess to `copalpm whose --json <blendfile>` | One-shot project lookup whenever a file is loaded. |
| HTTP to `127.0.0.1:5123` (`/start`, `/stop`, `/ping`, `/state`) | Frequent session lifecycle and heartbeat calls. Uses the `X-API-Key` from `<copalpm-data-dir>/config.json`. |

If CopalPM isn't installed or its service isn't running, the addon silently no-ops — Blender keeps working normally.

---

## Install

```bash
uv tool install "git+https://github.com/copal-tools/copal.git#subdirectory=copalblender"
copalblender install
```

`copalblender install` detects every Blender version installed on the system (per the OS-standard user config directory) and copies the addon into each `scripts/addons/copal_blender/`. After install, open Blender → Edit → Preferences → Add-ons → search "Copal" → tick the box.

```bash
copalblender status     # which versions have the addon installed
copalblender uninstall  # remove the addon from every detected version
```

### Per-OS install paths

| OS | Blender user config | Addons installed under |
|----|---------------------|------------------------|
| Windows | `%APPDATA%\Blender Foundation\Blender\<ver>\` | `<ver>\scripts\addons\copal_blender\` |
| macOS | `~/Library/Application Support/Blender/<ver>/` | `<ver>/scripts/addons/copal_blender/` |
| Linux | `~/.config/blender/<ver>/` | `<ver>/scripts/addons/copal_blender/` |

If no Blender versions are detected, open Blender once to create the user config directory, then re-run `copalblender install`.

---

## Addon preferences

Open Edit → Preferences → Add-ons → "Copal: time tracking" → expand. Settings:

| Setting | Default | Purpose |
|---------|---------|---------|
| Enabled | true | Master switch — disable to silence all tracking activity. |
| Ping interval (seconds) | 60 | How often the addon pings the CopalPM daemon and checks cursor/focus. |
| Unfocus stop threshold (seconds) | 300 | Stop the session after Blender stays unfocused this long. |
| Cursor-static threshold (pings) | 2 | Stop the session after this many consecutive ticks with no cursor movement. |
| `copalpm` path override | _empty_ | Manual path to the `copalpm` binary. Use when `shutil.which()` fails — common on macOS when Blender is launched from Finder with a stripped PATH. |

---

## How tracking decisions are made

1. **File loaded** — the addon calls `copalpm whose --json <filepath>`. If the file is inside a registered project, `/start` is sent with that project ID. If a session was already running for a different project, the daemon auto-switches (stops the old session with reason `"switch"`, starts the new one).
2. **File closed / untitled** — the addon stops the active session with reason `"manual"`.
3. **Tick (periodic)** — every `ping_interval_sec`, the addon sends `/ping` to keep the daemon's idle timer fresh. On the same tick:
    - It samples the OS cursor position. If two consecutive samples are identical, the session stops with reason `"inactivity"`.
    - It checks whether Blender's window is foreground. After `unfocus_stop_sec` of continuous unfocus, the session stops with reason `"inactivity"`.
4. **Resume after stop** — while no session is running, the addon keeps polling cursor + foreground. If the cursor moves and the current `.blend` still belongs to a project, the addon issues a fresh `/start`.
5. **Blender quit** — an `atexit` handler flushes a final `/stop`.

Daemon-side idle auto-stop (20 min default in CopalPM) remains a safety net for cases the addon misses (hard kill, OS crash).

---

## Troubleshooting

**Addon doesn't appear in Preferences → Add-ons.** Confirm `copalblender install` succeeded (`copalblender status` lists "ok" for at least one Blender version). Then in Blender: Edit → Preferences → Add-ons → "Refresh". If still missing, restart Blender.

**Sessions don't start.** Check the Blender system console (Window → Toggle System Console on Windows) for `[copal_blender]` lines. Most likely:
- `service down — skipping` — `copalpm service install` hasn't been run, or the service was uninstalled.
- `whose() not_installed` — `copalpm` not on PATH inside Blender. On macOS, set "copalpm path override" in the addon preferences to your absolute path (e.g. `/Users/<you>/.local/bin/copalpm`).
- `api 404 unknown_project_id` — the file's project is registered locally but the project ID didn't match. Run `copalpm project status` to see registered IDs.

**Cursor-static stops too aggressively while modeling.** Increase "cursor-static threshold (pings)" from 2 → 3 or 4, or raise "ping interval (seconds)" from 60 → 120.

**Cursor/focus heuristics on Linux Wayland.** Wayland blocks global cursor + foreground-window queries. On Wayland sessions the cursor-static + unfocus checks are disabled — only file-close and Blender-quit triggers fire. Use `XDG_SESSION_TYPE=x11` to opt back in if your distro supports X11 fallback.

---

## License

Apache 2.0 — see [LICENSE](./LICENSE) and [NOTICE](./NOTICE).
