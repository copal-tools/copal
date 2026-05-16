"""
copalpm tui — Textual TUI for CopalPM.

Screens:
  DashboardScreen     — all registered projects, live timer, click to open
  ProjectDetailScreen — full project view + actions
"""

import json
import platform
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
import yaml
from datetime import datetime, timezone
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical, Horizontal
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button, Checkbox, DataTable, Footer, Header, Input, Label,
    ProgressBar, RadioButton, RadioSet, RichLog, Rule, Select, Static,
)
from textual.widget import Widget
from textual_fspicker import SelectDirectory

from copalpm.config import DATA_DIR, SESSIONS_LOG
from copalpm import copalvx_api
from copalpm.pm import (
    _YAML_HEADER, build_project_record, compute_id_and_path,
    days_ago, fmt_h, load_project_yaml, load_registry, save_registry,
    load_templates, save_templates, slug_title, upsert_registry,
)
from copalpm.project_doctor import find_orphan_sessions, find_path_drift


# ── Service helpers ────────────────────────────────────────────────────────────

class ServiceUnavailable(Exception):
    """Background task-tracker service is not configured or not running."""


_SERVICE_DOWN_MSG = (
    "Background service isn't running. Run `copalpm setup` to install it, "
    "or `copalpm service install` if it's already configured."
)


def _active_session() -> dict | None:
    cfg_path = DATA_DIR / "config.json"
    if not cfg_path.exists():
        return None
    try:
        cfg  = json.loads(cfg_path.read_text(encoding="utf-8"))
        port = cfg.get("port", 5123)
        req  = urllib.request.Request(
            f"http://127.0.0.1:{port}/state",
            headers={"X-API-Key": cfg["api_key"]},
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read()) or None
    except Exception:
        return None


def _service_call(method: str, endpoint: str, body: dict | None = None) -> dict:
    cfg_path = DATA_DIR / "config.json"
    if not cfg_path.exists():
        raise ServiceUnavailable(_SERVICE_DOWN_MSG)
    try:
        cfg  = json.loads(cfg_path.read_text(encoding="utf-8"))
        port = cfg.get("port", 5123)
        data = json.dumps(body).encode() if body is not None else None
        req  = urllib.request.Request(
            f"http://127.0.0.1:{port}{endpoint}",
            data=data, method=method,
            headers={"X-API-Key": cfg["api_key"], "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except urllib.error.URLError as e:
        raise ServiceUnavailable(_SERVICE_DOWN_MSG) from e


def _elapsed(start_iso: str) -> str:
    try:
        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        secs  = int((datetime.now(timezone.utc) - start).total_seconds())
        h, m  = divmod(secs // 60, 60)
        s     = secs % 60
        return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"
    except Exception:
        return "?"


def _open_folder(path: str) -> None:
    """Open a directory in the system file manager (non-blocking)."""
    try:
        if platform.system() == "Windows":
            subprocess.Popen(["explorer", path])
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def _cvx_next_tag(versions: list[str]) -> str:
    if not versions:
        return "v1.0"
    try:
        parts = versions[0].lstrip("v").split(".")
        parts[-1] = str(int(parts[-1]) + 1)
        return "v" + ".".join(parts)
    except Exception:
        return "v1.0"


_STREAM_PATTERN = re.compile(r"\[(UPLOAD|DOWNLOAD)\]\s+(\d+)/(\d+)\s+(.*)")


def _cvx_stream(proc, modal: "CopalVXProgressModal", app: App,
                on_success=None) -> None:
    """Stream subprocess stdout to a CopalVXProgressModal. Runs in a thread."""
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            m = _STREAM_PATTERN.match(line)
            if m:
                done, total = int(m.group(2)), int(m.group(3))
                app.call_from_thread(modal.update_progress, done, total)
                app.call_from_thread(modal.write_line, m.group(4))
            else:
                app.call_from_thread(modal.write_line, line)
        proc.wait()
        success = proc.returncode == 0
        app.call_from_thread(modal.mark_done, success)
        if success and on_success:
            app.call_from_thread(on_success)
    except Exception as e:
        app.call_from_thread(modal.write_line, f"[red]{e}[/red]")
        app.call_from_thread(modal.mark_done, False)


def _fmt_size(b: int | None) -> str:
    """Human-readable byte count (e.g. 1.4 MB)."""
    if not b:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024 or unit == "TB":
            return f"{b:.0f} {unit}" if unit == "B" else f"{b:.1f} {unit}"
        b /= 1024
    return "?"


# ── Data loaders ───────────────────────────────────────────────────────────────

def _pull_dest_invalid(raw: str) -> str | None:
    """Validate a parent-folder input for `PullDestinationModal`.

    Returns a user-facing error message if the path is unusable, or None
    if it's acceptable. The folder itself need not exist — we `mkdir` it
    on confirm — but the input must be a non-empty, absolute path.
    """
    if raw is None or not raw.strip():
        return "Pick a parent folder first."
    raw = raw.strip()
    try:
        p = Path(raw).expanduser()
    except Exception:
        return "Invalid path."
    if not p.is_absolute():
        return "Enter an absolute path."
    return None


def _elide_path(path: str, max_chars: int = 50) -> str:
    """Middle-elide a path so head and tail are preserved.

    The PullDestinationModal preview line lives inside a 70-col modal box
    (1 2 padding) with a 16-char `"Will pull into: "` prefix — long paths
    wrap and shove the modal taller mid-typing. Eliding the middle keeps
    the project-name suffix visible (what the user cares about) while
    the full path stays available in the Input above.
    """
    if len(path) <= max_chars:
        return path
    keep     = max_chars - 3                  # room for "..."
    head_len = keep // 2
    tail_len = keep - head_len
    return f"{path[:head_len]}...{path[-tail_len:]}"


def _doctor_banner_text(drift_count: int, orphan_count: int) -> str | None:
    """Banner string for the Dashboard. None when there's nothing to surface."""
    if not drift_count and not orphan_count:
        return None
    parts = []
    if drift_count:
        parts.append(
            f"{drift_count} stale registry "
            f"{'entry' if drift_count == 1 else 'entries'}"
        )
    if orphan_count:
        parts.append(
            f"{orphan_count} orphan session "
            f"{'group' if orphan_count == 1 else 'groups'}"
        )
    return f"[bold]⚠[/bold] {' · '.join(parts)} — press [b]D[/b] for details."


def _dashboard_rows() -> list[dict]:
    rows     = []
    registry = load_registry()
    drift_by_id = {d["id"]: d["reason"] for d in find_path_drift(registry)}
    for entry in registry:
        pid       = entry["id"]
        path      = Path(entry.get("path", ""))
        yaml_path = path / "project.yaml"
        record    = load_project_yaml(yaml_path) if yaml_path.exists() else {}

        phase_log = record.get("phase_log") or []
        phase     = phase_log[-1].get("phase", "?") if phase_log else "missing"
        total_sec = sum(int(te.get("duration_sec", 0))
                        for te in record.get("time_entries", []))
        deadline  = str(record["deadline"]) if record.get("deadline") else "—"

        delivs     = record.get("deliverables") or []
        last_deliv = "—"
        if delivs:
            d          = delivs[-1]
            last_deliv = f"{d.get('name','?')} ({days_ago(d.get('delivered_at',''))})"

        cvx = record.get("copalvx") or {}
        rows.append({
            "id":                pid,
            "name":              entry.get("name", pid),
            "phase":             phase,
            "total_sec":         total_sec,
            "time_str":          fmt_h(total_sec) if total_sec else "—",
            "deadline":          deadline,
            "last_delivery":     last_deliv,
            "path":              str(path),
            "cvx_name":          cvx.get("project_name"),
            "cvx_local_version": cvx.get("last_push_version"),
            "drift_reason":      drift_by_id.get(pid),
        })
    return rows


def _detail_data(project: dict) -> dict:
    record    = load_project_yaml(Path(project["path"]) / "project.yaml")
    phase_log = record.get("phase_log") or []
    phase     = phase_log[-1].get("phase", "?") if phase_log else "?"

    days_in_phase = "?"
    if phase_log:
        try:
            dt            = datetime.fromisoformat(
                phase_log[-1]["entered_at"].replace("Z", "+00:00"))
            days_in_phase = str((datetime.now(timezone.utc) - dt).days)
        except Exception:
            pass

    entries   = record.get("time_entries") or []
    by_phase: dict[str, int] = {}
    for te in entries:
        p           = te.get("phase") or "unknown"
        by_phase[p] = by_phase.get(p, 0) + int(te.get("duration_sec", 0))

    people  = record.get("people") or {}
    client  = (record.get("client") or {}).get("name") or "—"
    fin     = record.get("financial") or {}
    delivs  = record.get("deliverables") or []
    cvx     = record.get("copalvx") or {}

    return {
        "id":           record.get("id", project["id"]),
        "name":         record.get("name", project["name"]),
        "type":         record.get("type", "—"),
        "category":     record.get("category", "—"),
        "client":       client,
        "director":     people.get("director") or "—",
        "producer":     people.get("producer") or "—",
        "created_at":   str(record.get("created_at", "—"))[:10],
        "deadline":     str(record["deadline"]) if record.get("deadline") else "—",
        "phase":        phase,
        "days_in_phase": days_in_phase,
        "total_sec":    sum(by_phase.values()),
        "by_phase":     by_phase,
        "financial":    fin,
        "deliverables": delivs,
        "copalvx":      cvx,
        "notes":        record.get("notes") or "",
    }


# ── Screens ────────────────────────────────────────────────────────────────────

class TimerStartModal(ModalScreen):
    """Overlay that captures an optional work description before starting a timer."""

    DEFAULT_CSS = """
    TimerStartModal {
        align: center middle;
    }
    #modal-box {
        width: 52;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: solid $accent;
    }
    #modal-hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-box"):
            yield Label("What are you working on?")
            yield Input(placeholder="description (optional)", id="desc-input")
            yield Static("[dim]Enter to start  •  Esc to cancel[/dim]", id="modal-hint")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)
            event.stop()


class InitScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Cancel"),
    ]

    DEFAULT_CSS = """
    InitScreen {
        align: center middle;
    }
    #init-box {
        width: 62;
        height: 85vh;
        padding: 1 2;
        background: $surface;
        border: solid $accent;
    }
    #init-box .field-label {
        color: $text-muted;
        margin-top: 1;
    }
    #init-scroll {
        height: 1fr;
    }
    #init-buttons {
        margin-top: 1;
        height: auto;
    }
    #init-buttons Button {
        margin-right: 1;
    }
    #dir-row {
        height: auto;
    }
    #dir-row #dir-input {
        width: 1fr;
    }
    #dir-row #dir-browse {
        min-width: 5;
        width: 5;
        margin-left: 1;
    }
    #name-preview {
        color: $text-muted;
        margin-top: 0;
        margin-bottom: 1;
    }
    """

    def __init__(self, initial_dir: str | None = None) -> None:
        super().__init__()
        self._preset_index  = 0  # 0=Custom, 1..N=template index
        self._templates     = load_templates()
        self._initial_dir   = initial_dir

    def compose(self) -> ComposeResult:
        with Vertical(id="init-box"):
            yield Label("[bold]New Project[/bold]")
            yield Rule()
            with ScrollableContainer(id="init-scroll"):
                yield Label("Name *", classes="field-label")
                yield Input(placeholder="Project name", id="name-input")
                yield Static(self._preview_text(""), id="name-preview")
                yield Label("Preset", classes="field-label")
                yield RadioSet(
                    RadioButton("Custom"),
                    *[RadioButton(t["name"]) for t in self._templates],
                    id="preset-radio",
                )
                # Custom fields are flat direct children so the scroll container
                # can compute their full virtual height (nested Vertical clips them).
                yield Label("Type", classes="field-label custom-field")
                yield Select(
                    [("Internal", "tlc"), ("Client", "client"), ("Personal", "personal")],
                    value="tlc", allow_blank=False, id="type-select", classes="custom-field",
                )
                yield Label("Category", classes="field-label custom-field")
                yield Select(
                    [("TVC", "tvc"), ("Digital Signage", "digital-signage"),
                     ("B2B", "b2b"), ("Digital", "digital")],
                    value="tvc", allow_blank=False, id="category-select", classes="custom-field",
                )
                yield Label("Client", classes="field-label custom-field")
                yield Input(placeholder="Client name (optional)", id="client-input",
                            classes="custom-field")
                yield Label("Director", classes="field-label custom-field")
                yield Input(placeholder="Agency / director (optional)", id="director-input",
                            classes="custom-field")
                yield Label("Producer", classes="field-label custom-field")
                yield Input(placeholder="Producer (optional)", id="producer-input",
                            classes="custom-field")
                yield Label("Deadline", classes="field-label custom-field")
                yield Input(placeholder="YYYY-MM-DD (optional)", id="deadline-input",
                            classes="custom-field")
                yield Label("Project folder", classes="field-label")
                with Horizontal(id="dir-row"):
                    yield Input(id="dir-input")
                    yield Button("📁", id="dir-browse",
                                 tooltip="Browse for project folder")
                yield Checkbox("Append _NNN suffix to folder name", id="inc-check")
            with Horizontal(id="init-buttons"):
                yield Button("Create", variant="primary", id="btn-create")
                yield Button("Cancel", variant="default", id="btn-cancel")

    def on_mount(self) -> None:
        self.query_one("#dir-input", Input).value = self._initial_dir or self._default_dir()
        self.query_one("#name-input", Input).focus()

    @staticmethod
    def _default_dir() -> str:
        try:
            cfg = json.loads((DATA_DIR / "config.json").read_text(encoding="utf-8"))
            if cfg.get("projects_dir"):
                return cfg["projects_dir"]
        except Exception:
            pass
        return str(Path.home() / "Projects")

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        self._preset_index = event.index
        show = (event.index == 0)
        for w in self.query(".custom-field"):
            w.display = show

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "name-input":
            return
        try:
            self.query_one("#name-preview", Static).update(self._preview_text(event.value))
        except Exception:
            # Preview Static may not be mounted yet during early input events.
            pass

    def _preview_text(self, raw_name: str) -> str:
        """Show the user the exact ID and CopalVX project name their input will produce."""
        slug = slug_title(raw_name or "")
        if not slug:
            return "[yellow]Add at least one letter or digit (emojis alone don't count)[/yellow]"
        date = datetime.now().strftime("%d%m%y")
        return (
            f"[dim]ID:[/dim] PROJ-{slug}-{date}  "
            f"[dim]•[/dim]  [dim]CopalVX:[/dim] {slug}-{date}"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.app.pop_screen()
        elif event.button.id == "btn-create":
            self._do_create()
        elif event.button.id == "dir-browse":
            self._open_dir_picker()

    def _open_dir_picker(self) -> None:
        current = self.query_one("#dir-input", Input).value.strip() or self._default_dir()
        start = Path(current).expanduser()
        while not start.exists() and start != start.parent:
            start = start.parent
        if not start.exists():
            start = Path.home()

        def on_pick(path: Path | None) -> None:
            if path is not None:
                self.query_one("#dir-input", Input).value = str(path)

        self.app.push_screen(SelectDirectory(str(start)), on_pick)

    def _do_create(self) -> None:
        name = self.query_one("#name-input", Input).value.strip()
        if not name:
            self.notify("Project name is required.", severity="error")
            self.query_one("#name-input", Input).focus()
            return

        # Guard against names that transliterate to an empty slug (emoji-only,
        # pure symbols, etc.). Without this, the folder name and CopalVX
        # project name would degrade to just the date suffix (or worse, a
        # leading hyphen). See pm._to_ascii and CLAUDE.md gotcha #13.
        if not slug_title(name):
            self.notify(
                "Project name must contain at least one letter or digit "
                "(emojis alone don't count).",
                title="Project name", severity="warning",
            )
            self.query_one("#name-input", Input).focus()
            return

        base_dir = Path(self.query_one("#dir-input", Input).value.strip() or self._default_dir())

        idx = self._preset_index
        if idx == 0:  # Custom
            proj_type     = self.query_one("#type-select",     Select).value
            category      = self.query_one("#category-select", Select).value
            client        = self.query_one("#client-input",    Input).value.strip() or None
            director      = self.query_one("#director-input",  Input).value.strip() or None
            producer      = self.query_one("#producer-input",  Input).value.strip() or None
            deadline      = self.query_one("#deadline-input",  Input).value.strip() or None
            collaborators = None
            folders       = ["01_Intake", "02_Workfiles", "03_Exports"]
        else:
            tmpl          = self._templates[idx - 1]
            proj_type     = tmpl.get("type", "tlc")
            category      = tmpl.get("category", "tvc")
            client        = tmpl.get("client")
            director      = tmpl.get("director")
            producer      = tmpl.get("producer")
            collaborators = tmpl.get("collaborators", [])
            deadline      = None
            folders       = tmpl.get("folders", ["01_Intake", "02_Workfiles", "03_Exports"])

        try:
            use_inc = self.query_one("#inc-check", Checkbox).value
            pid, root = compute_id_and_path(name, base_dir, use_increment=use_inc)
            if not use_inc and root.exists():
                raise ValueError(f"Folder '{root.name}' already exists.")
            root.mkdir(parents=True, exist_ok=use_inc)
            for d in folders:
                (root / d).mkdir(parents=True, exist_ok=True)

            record    = build_project_record(
                pid, name, proj_type, category,
                client, None, director, producer,
                deadline, None, None, None,
                collaborators=collaborators,
            )
            yaml_path = root / "project.yaml"
            yaml_path.write_text(
                _YAML_HEADER + yaml.dump(
                    record, default_flow_style=False, allow_unicode=True, sort_keys=False,
                ),
                encoding="utf-8",
            )
            upsert_registry(pid, name, root)

            project_info = {"id": pid, "name": name, "path": str(root)}
            self.app.pop_screen()
            self.app.push_screen(ProjectDetailScreen(project_info, auto_push=True))

        except Exception as e:
            self.notify(str(e), title="Create failed", severity="error")


class DashboardScreen(Screen):
    BINDINGS = [
        Binding("n", "new_project",      "New project"),
        Binding("t", "manage_templates", "Templates"),
        Binding("d", "open_doctor",      "Doctor"),
        Binding("r", "refresh",          "Refresh"),
        Binding("o", "open_folder",      "Open folder"),
        Binding("p", "push",             "Push"),
        Binding("l", "pull",             "Pull"),
        Binding("q", "app.quit",         "Quit"),
    ]

    DEFAULT_CSS = """
    #doctor-banner {
        background: $warning 20%;
        color: $warning;
        padding: 0 1;
        height: 1;
    }
    #project-table {
        height: 1fr;
    }
    #empty-state {
        height: 1fr;
        padding: 4 2;
        content-align: center middle;
    }
    """

    _local_rows:  list[dict]            = []
    _server_rows: list[dict]            = []
    _cvx_latest:  dict[str, str | None] = {}
    _drift_count:   int                 = 0
    _orphan_count:  int                 = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="search-row"):
            yield Input(placeholder="Search projects…", id="search-input")
            yield Checkbox("Server projects", value=True, id="server-check")
        banner = Static("", id="doctor-banner")
        banner.display = False
        yield banner
        yield DataTable(id="project-table", cursor_type="cell", zebra_stripes=True)
        empty = Static("", id="empty-state")
        empty.display = False
        yield empty
        yield Footer()

    def on_mount(self) -> None:
        self._session_cache: dict | None = None
        self._stop_session_poller = threading.Event()
        self._row_data: dict[str, dict] = {}
        self._ordered_keys: list[str]   = []
        table = self.query_one("#project-table", DataTable)
        table.add_columns("Project", "📁", "▲", "▼")
        self._refresh_data()
        self.set_interval(1,  self._tick_timer)
        self.set_interval(30, self._refresh_data)
        self.set_interval(60, self._poll_server)
        threading.Thread(target=self._fetch_server_data, daemon=True).start()
        threading.Thread(target=self._session_poll_loop, daemon=True).start()
        # Focus the DataTable rather than the search input. Otherwise Textual
        # auto-focuses the first focusable widget (the search Input), which
        # captures arrow keys for text-cursor movement instead of row
        # navigation. Users can click the search box or Tab to it to type.
        self.call_after_refresh(self._focus_first_row)

    def _focus_first_row(self) -> None:
        table = self.query_one("#project-table", DataTable)
        table.focus()
        if table.row_count:
            table.move_cursor(row=0, column=0)

    def on_unmount(self) -> None:
        self._stop_session_poller.set()

    def _session_poll_loop(self) -> None:
        while not self._stop_session_poller.is_set():
            self._session_cache = _active_session()
            self._stop_session_poller.wait(5.0)

    # ── Data ──────────────────────────────────────────────────────────────────

    def _refresh_data(self) -> None:
        self._local_rows = _dashboard_rows()
        registry         = load_registry()
        self._drift_count  = sum(1 for r in self._local_rows if r.get("drift_reason"))
        self._orphan_count = len(find_orphan_sessions(registry, SESSIONS_LOG))
        self._refresh_doctor_banner()
        self._rebuild_list()

    def _refresh_doctor_banner(self) -> None:
        try:
            banner = self.query_one("#doctor-banner", Static)
        except Exception:
            return
        text = _doctor_banner_text(self._drift_count, self._orphan_count)
        if text is None:
            banner.display = False
            return
        banner.update(text)
        banner.display = True

    def _rebuild_list(self) -> None:
        query   = self.query_one("#search-input", Input).value.strip().lower()
        inc_srv = self.query_one("#server-check", Checkbox).value
        table   = self.query_one("#project-table", DataTable)
        empty   = self.query_one("#empty-state",   Static)

        table.clear()
        self._row_data.clear()
        self._ordered_keys.clear()

        local_cvx = {r["cvx_name"] for r in self._local_rows if r.get("cvx_name")}

        local = [
            r for r in self._local_rows
            if not query or query in r["name"].lower() or query in r["id"].lower()
        ]

        server_only = []
        if inc_srv:
            for sr in self._server_rows:
                if sr["name"] in local_cvx:
                    continue
                if query and query not in sr["name"].lower():
                    continue
                server_only.append({
                    "id":                None,
                    "name":              sr["name"],
                    "path":              None,
                    "cvx_name":          sr["name"],
                    "cvx_local_version": None,
                    "is_server_only":    True,
                })

        if not local and not server_only:
            table.display = False
            empty.display = True
            if query:
                empty.update(
                    "[dim]No projects match your search.[/dim]\n"
                    "[dim]Clear the search box to see everything.[/dim]"
                )
            elif self._local_rows:
                empty.update(
                    "[dim]No projects match your filters.[/dim]\n"
                    "[dim]Toggle the 'Server projects' checkbox or clear the search.[/dim]"
                )
            else:
                empty.update(
                    "No projects yet.\n\n"
                    "[dim]Press[/dim] [b]N[/b] [dim]to create your first project,"
                    " or wait for server projects to load.[/dim]"
                )
            return

        table.display = True
        empty.display = False

        for row in local:
            svr        = self._cvx_latest.get(row.get("cvx_name")) if row.get("cvx_name") else None
            has_update = bool(svr and svr != row.get("cvx_local_version"))
            self._add_table_row(row, has_update=has_update)

        for row in server_only:
            self._add_table_row(row, has_update=False)

    def _add_table_row(self, project: dict, has_update: bool) -> None:
        """Append one project to the DataTable and register it in `_row_data`."""
        is_stale = bool(project.get("drift_reason"))
        is_so    = bool(project.get("is_server_only"))
        name     = project.get("name", "?")
        path     = project.get("path")
        cvx_name = project.get("cvx_name")

        name_cell = f"⚠ {name}" if is_stale else name
        if has_update:
            name_cell = f"{name_cell} ↑"

        folder_cell = "📁" if (path and not is_stale) else "·"
        push_cell   = "▲"  if (cvx_name and not is_so and not is_stale) else "·"
        pull_cell   = "▼"  if cvx_name else "·"

        # Row-level dim styling for stale and server-only rows. DataTable
        # row-label classes are limited, so we wrap each cell explicitly.
        if is_stale or is_so:
            name_cell   = f"[dim]{name_cell}[/dim]"
            folder_cell = f"[dim]{folder_cell}[/dim]"
            push_cell   = f"[dim]{push_cell}[/dim]"
            pull_cell   = f"[dim]{pull_cell}[/dim]"

        pid     = project.get("id")
        row_key = f"local:{pid}" if pid else f"srv:{cvx_name or name}"

        # DataTable cell content is top-aligned by default. For true vertical
        # centering, the row needs an odd height (so there is a middle line)
        # and each cell content gets a leading newline so the visible text
        # lands on row line 1 of 3 — empty line above, empty line below.
        table = self.query_one("#project-table", DataTable)
        table.add_row(
            f"\n{name_cell}",
            f"\n{folder_cell}",
            f"\n{push_cell}",
            f"\n{pull_cell}",
            height=3, key=row_key,
        )
        self._row_data[row_key] = project
        self._ordered_keys.append(row_key)

    def _poll_server(self) -> None:
        threading.Thread(target=self._fetch_server_data, daemon=True).start()

    def _fetch_server_data(self) -> None:
        self._server_rows = copalvx_api.list_projects()
        result: dict[str, str | None] = {}
        for entry in load_registry():
            yaml_path = Path(entry.get("path", "")) / "project.yaml"
            if not yaml_path.exists():
                continue
            record   = load_project_yaml(yaml_path)
            cvx_name = (record.get("copalvx") or {}).get("project_name")
            if not cvx_name:
                continue
            versions         = copalvx_api.get_versions(cvx_name)
            result[cvx_name] = versions[0] if versions else None
        self._cvx_latest = result
        self.app.call_from_thread(self._rebuild_list)

    # ── Timer ─────────────────────────────────────────────────────────────────

    def _tick_timer(self) -> None:
        session = self._session_cache
        if session:
            pid     = session.get("project_id", "")
            name    = next((r["name"] for r in self._local_rows if r["id"] == pid), pid)
            elapsed = _elapsed(session.get("start", ""))
            if name and name != pid:
                self.app.title = f"PM  ●  {pid}  ▸  {name}  {elapsed}"
            else:
                self.app.title = f"PM  ●  {pid}  {elapsed}"
        else:
            self.app.title = "PM"

    # ── Events ────────────────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search-input":
            self._rebuild_list()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "server-check":
            self._rebuild_list()

    # ── Row dispatch ──────────────────────────────────────────────────────────
    #
    # The dashboard's project table is a DataTable with cursor_type="cell".
    # Clicking a cell fires CellSelected; the column index decides which
    # action runs. P/L/O keybindings reuse the same handlers against the
    # cursor row, so mouse and keyboard paths share code.

    def _focused_project(self) -> dict | None:
        table = self.query_one("#project-table", DataTable)
        row   = table.cursor_row
        if row is None or row < 0 or row >= len(self._ordered_keys):
            return None
        return self._row_data.get(self._ordered_keys[row])

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        row_key = event.cell_key.row_key
        # Textual wraps the key in a RowKey object; .value is the string we set.
        key     = getattr(row_key, "value", row_key)
        project = self._row_data.get(key)
        if not project:
            return
        col = event.coordinate.column
        if   col == 0: self._handle_row_open(project)
        elif col == 1: self._handle_open_folder(project)
        elif col == 2: self._handle_push(project)
        elif col == 3: self._handle_pull(project)

    def _handle_row_open(self, project: dict) -> None:
        if project.get("is_server_only"):
            # Same flow as clicking the ▼ cell — opens the pull-destination
            # picker because server-only rows have no local path yet.
            self._start_pull_flow(project)
            return
        if project.get("drift_reason"):
            self.notify(
                "Folder missing — press [D] for cleanup options.",
                title="Stale registry entry",
                severity="warning",
            )
            return
        self.app.push_screen(ProjectDetailScreen(project))

    def _handle_open_folder(self, project: dict) -> None:
        path = project.get("path")
        if path:
            _open_folder(path)

    def _handle_push(self, project: dict) -> None:
        cvx_name = project.get("cvx_name", "")
        path     = project.get("path", "")
        if (not cvx_name or not path
                or project.get("is_server_only")
                or project.get("drift_reason")):
            return
        versions  = copalvx_api.get_versions(cvx_name)
        suggested = _cvx_next_tag(versions)

        def on_confirm(result: dict | None) -> None:
            if result is None:
                return
            tag      = result["tag"]
            msg      = result.get("message", "")
            progress = CopalVXProgressModal(f"Push: {cvx_name} @ {tag}")
            self.app.push_screen(progress)
            def _run():
                try:
                    proc = copalvx_api.run_push(cvx_name, tag, path, msg, "")
                    _cvx_stream(proc, progress, self.app, self._refresh_data)
                except Exception as e:
                    self.app.call_from_thread(progress.write_line, f"[red]{e}[/red]")
                    self.app.call_from_thread(progress.mark_done, False)
            threading.Thread(target=_run, daemon=True).start()

        self.app.push_screen(CopalVXPushModal(cvx_name, suggested), on_confirm)

    def _handle_pull(self, project: dict) -> None:
        if not project.get("cvx_name"):
            return
        self._start_pull_flow(project)

    def _start_pull_flow(self, project: dict) -> None:
        cvx_name = project.get("cvx_name", "")
        path     = project.get("path") or ""
        versions = copalvx_api.get_versions(cvx_name)
        if not versions:
            self.notify("No versions on server.", title="CopalVX", severity="warning")
            return

        def _continue_with_path(resolved_path: str) -> None:
            def on_confirm(result: dict | None) -> None:
                if result is None:
                    return
                tag           = result["tag"]
                local_version = project.get("cvx_local_version")

                def _fetch_diff() -> None:
                    folders = []
                    if local_version and local_version != tag:
                        try:
                            diff = copalvx_api.get_diff(cvx_name, local_version, tag)
                            if diff:
                                folders = copalvx_api.extract_changed_folders(diff)
                        except Exception:
                            pass
                    if folders:
                        modal = SelectivePullModal(cvx_name, tag, folders)
                        self.app.call_from_thread(self.app.push_screen, modal, on_folder_select)
                    else:
                        self.app.call_from_thread(_start_pull, [])

                def on_folder_select(sel: dict | None) -> None:
                    if sel is None:
                        return
                    _start_pull(sel["prefixes"])

                def _start_pull(prefixes: list[str]) -> None:
                    progress = CopalVXProgressModal(f"Pull: {cvx_name} @ {tag}")
                    self.app.push_screen(progress)
                    def _run() -> None:
                        try:
                            self.app.call_from_thread(
                                progress.write_line,
                                f"[dim]args:[/dim] project={cvx_name!r}  "
                                f"tag={tag!r}  target={resolved_path!r}  "
                                f"prefixes={prefixes!r}",
                            )
                            if not (cvx_name and tag and resolved_path):
                                self.app.call_from_thread(
                                    progress.write_line,
                                    "[red bold]Aborted:[/red bold] one of the three "
                                    "required args is empty. The subprocess would have "
                                    "shifted args and given a confusing error.",
                                )
                                self.app.call_from_thread(progress.mark_done, False)
                                return
                            proc = copalvx_api.run_pull(cvx_name, tag, resolved_path, prefixes=prefixes)
                            _cvx_stream(proc, progress, self.app, self._refresh_data)
                        except Exception as e:
                            self.app.call_from_thread(progress.write_line, f"[red]{e}[/red]")
                            self.app.call_from_thread(progress.mark_done, False)
                    threading.Thread(target=_run, daemon=True).start()

                threading.Thread(target=_fetch_diff, daemon=True).start()

            self.app.push_screen(CopalVXPullModal(cvx_name, versions, resolved_path), on_confirm)

        if path:
            _continue_with_path(path)
            return

        # First pull of a server-only project — ask the user which parent
        # folder to drop it into; we append the project name as the actual
        # target so files always land in their own subfolder.
        default_parent = self._default_pull_parent()

        def on_pick_dest(result: dict | None) -> None:
            if result is None:
                return
            _continue_with_path(result["path"])

        self.app.push_screen(PullDestinationModal(cvx_name, default_parent), on_pick_dest)

    @staticmethod
    def _default_pull_parent() -> str:
        """Suggested parent folder for first pull: projects_dir or ~/Projects."""
        try:
            cfg  = json.loads((DATA_DIR / "config.json").read_text(encoding="utf-8"))
            root = cfg.get("projects_dir") or str(Path.home() / "Projects")
        except Exception:
            root = str(Path.home() / "Projects")
        return root

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_refresh(self) -> None:
        self._refresh_data()
        self._poll_server()

    def action_new_project(self) -> None:
        self.app.push_screen(InitScreen())

    def action_manage_templates(self) -> None:
        self.app.push_screen(TemplateScreen())

    def action_open_doctor(self) -> None:
        self.app.push_screen(DoctorModal(), self._on_doctor_dismiss)

    def action_open_folder(self) -> None:
        project = self._focused_project()
        if project:
            self._handle_open_folder(project)

    def action_push(self) -> None:
        project = self._focused_project()
        if project:
            self._handle_push(project)

    def action_pull(self) -> None:
        project = self._focused_project()
        if project:
            self._handle_pull(project)

    def _on_doctor_dismiss(self, _result) -> None:
        # Entries may have been dropped or re-registered — reload.
        self._refresh_data()


class CopalVXPushModal(ModalScreen):
    """Confirm push: shows suggested tag + optional message, then runs copalvx push."""

    DEFAULT_CSS = """
    CopalVXPushModal { align: center middle; }
    #cvx-modal-box {
        width: 60;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: solid $accent;
    }
    #cvx-modal-hint { margin-top: 1; color: $text-muted; }
    """

    def __init__(self, project_name: str, suggested_tag: str) -> None:
        super().__init__()
        self._project_name = project_name
        self._suggested_tag = suggested_tag

    def compose(self) -> ComposeResult:
        with Vertical(id="cvx-modal-box"):
            yield Label(f"[bold]Push:[/bold] {self._project_name}")
            yield Rule()
            yield Label("Version tag:")
            yield Input(value=self._suggested_tag, id="tag-input")
            yield Label("Message (optional):")
            yield Input(placeholder="e.g. final grade pass", id="msg-input")
            yield Static("[dim]Enter to push  •  Esc to cancel[/dim]", id="cvx-modal-hint")

    def on_mount(self) -> None:
        self.query_one("#tag-input", Input).focus()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)
            event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        tag = self.query_one("#tag-input", Input).value.strip()
        msg = self.query_one("#msg-input", Input).value.strip()
        if tag:
            self.dismiss({"tag": tag, "message": msg})


class PullDestinationModal(ModalScreen):
    """First-pull destination picker for a server-only CopalVX project.

    User picks a **parent** folder; we append the project name to it as the
    actual pull target. Shown when the project has no local row yet
    (no `project.path`). Mirrors the F2 folder-picker pattern from InitScreen.
    """

    DEFAULT_CSS = """
    PullDestinationModal { align: center middle; }
    #pull-dest-box {
        width: 70;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: solid $accent;
    }
    #pull-dest-row { height: auto; margin-top: 1; }
    #pull-dest-row #pull-dest-input { width: 1fr; }
    #pull-dest-row #pull-dest-browse {
        min-width: 5;
        width: 5;
        margin-left: 1;
    }
    #pull-dest-preview { margin-top: 1; color: $text-muted; }
    #pull-dest-hint { margin-top: 1; color: $text-muted; }
    #pull-dest-buttons { margin-top: 1; height: auto; }
    #pull-dest-buttons Button { margin-right: 1; }
    """

    def __init__(self, project_name: str, default_parent: str) -> None:
        super().__init__()
        self._project_name  = project_name
        self._default_parent = default_parent

    def compose(self) -> ComposeResult:
        with Vertical(id="pull-dest-box"):
            yield Label(f"[bold]Pull:[/bold] {self._project_name}")
            yield Rule()
            yield Label("Parent folder (this machine):")
            with Horizontal(id="pull-dest-row"):
                yield Input(value=self._default_parent, id="pull-dest-input")
                yield Button("\U0001F4C1", id="pull-dest-browse",
                             tooltip="Browse for parent folder")
            yield Static("", id="pull-dest-preview")
            yield Static(
                "[dim]The project folder is created inside the parent.[/dim]",
                id="pull-dest-hint",
            )
            with Horizontal(id="pull-dest-buttons"):
                yield Button("Continue", variant="primary", id="pull-dest-ok")
                yield Button("Cancel", variant="default", id="pull-dest-cancel")

    def on_mount(self) -> None:
        self.query_one("#pull-dest-input", Input).focus()
        self._validate()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)
            event.stop()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._validate()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._confirm()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pull-dest-cancel":
            self.dismiss(None)
        elif event.button.id == "pull-dest-ok":
            self._confirm()
        elif event.button.id == "pull-dest-browse":
            self._open_dir_picker()

    def _validate(self) -> None:
        """Live-update preview text and Continue-button enabled state."""
        raw     = self.query_one("#pull-dest-input", Input).value
        err     = _pull_dest_invalid(raw)
        preview = self.query_one("#pull-dest-preview", Static)
        if err:
            preview.update(f"[red]{err}[/red]")
        else:
            preview.update(self._preview_text(raw))
        self.query_one("#pull-dest-ok", Button).disabled = err is not None

    def _preview_text(self, parent_raw: str) -> str:
        parent_raw = (parent_raw or "").strip()
        target     = Path(parent_raw).expanduser() / self._project_name
        return f"[dim]Will pull into:[/dim] {_elide_path(str(target))}"

    def _confirm(self) -> None:
        raw = self.query_one("#pull-dest-input", Input).value
        # Validation has already gated the Continue button, but a keyboard
        # Enter on an invalid input would still hit `_confirm` — guard here.
        if _pull_dest_invalid(raw) is not None:
            return
        target = Path(raw.strip()).expanduser() / self._project_name
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.notify(f"Could not create folder: {e}", severity="error")
            return
        self.dismiss({"path": str(target)})

    def _open_dir_picker(self) -> None:
        current = self.query_one("#pull-dest-input", Input).value.strip() or self._default_parent
        start = Path(current).expanduser()
        while not start.exists() and start != start.parent:
            start = start.parent
        if not start.exists():
            start = Path.home()

        def on_pick(path: Path | None) -> None:
            if path is not None:
                self.query_one("#pull-dest-input", Input).value = str(path)

        self.app.push_screen(SelectDirectory(str(start)), on_pick)


class CopalVXPullModal(ModalScreen):
    """Select a version to pull, then runs copalvx pull."""

    DEFAULT_CSS = """
    CopalVXPullModal { align: center middle; }
    #cvx-pull-box {
        width: 60;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: solid $accent;
    }
    #cvx-modal-hint { margin-top: 1; color: $text-muted; }
    """

    def __init__(self, project_name: str, versions: list[str], project_path: str) -> None:
        super().__init__()
        self._project_name = project_name
        self._versions      = versions
        self._project_path  = project_path

    def compose(self) -> ComposeResult:
        options = [(v, v) for v in self._versions]
        with Vertical(id="cvx-pull-box"):
            yield Label(f"[bold]Pull:[/bold] {self._project_name}")
            yield Rule()
            yield Label("Select version:")
            yield Select(options, value=self._versions[0] if self._versions else None, id="ver-select")
            yield Static("[dim]Enter to pull  •  Esc to cancel[/dim]", id="cvx-modal-hint")

    def on_mount(self) -> None:
        self.query_one(Select).focus()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)
            event.stop()
        elif event.key == "enter":
            sel = self.query_one(Select)
            if sel.value:
                self.dismiss({"tag": sel.value})
            event.stop()


class SelectivePullModal(ModalScreen):
    """Checkbox list of changed folders for a selective pull."""

    DEFAULT_CSS = """
    SelectivePullModal { align: center middle; }
    #sel-pull-box {
        width: 70;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: solid $accent;
    }
    #sel-pull-scroll { max-height: 15; margin: 1 0; }
    #sel-pull-buttons { margin-top: 1; }
    #sel-pull-buttons Button { margin-right: 1; }
    """

    def __init__(self, project_name: str, tag: str, folders: list[dict]) -> None:
        super().__init__()
        self._project_name = project_name
        self._tag          = tag
        self._folders      = folders

    def compose(self) -> ComposeResult:
        with Vertical(id="sel-pull-box"):
            yield Label(f"[bold]Selective Pull:[/bold] {self._project_name} @ {self._tag}")
            yield Rule()
            yield Label("Changed folders — uncheck what you don't need:")
            with ScrollableContainer(id="sel-pull-scroll"):
                for i, f in enumerate(self._folders):
                    label = f["folder"] if f["folder"] else "(root)"
                    yield Checkbox(
                        f"{label}  ({f['count']} changed)",
                        value=True,
                        id=f"chk-folder-{i}",
                    )
            with Horizontal(id="sel-pull-buttons"):
                yield Button("Pull Selected", variant="primary", id="btn-pull-sel")
                yield Button("Pull Full Version", id="btn-pull-full")
                yield Button("Cancel", variant="error", id="btn-cancel")

    def on_mount(self) -> None:
        self.query_one("#btn-pull-sel").focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-cancel":
            self.dismiss(None)
        elif bid == "btn-pull-full":
            self.dismiss({"full": True, "prefixes": []})
        elif bid == "btn-pull-sel":
            prefixes = []
            for i, f in enumerate(self._folders):
                if self.query_one(f"#chk-folder-{i}", Checkbox).value:
                    prefixes.append(f["folder"])
            if not prefixes:
                self.notify("Select at least one folder.", severity="warning")
                return
            self.dismiss({"full": False, "prefixes": prefixes})

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)
            event.stop()


class CopalVXFilesModal(ModalScreen):
    """Browse the file list of a CopalVX version and diff any two versions."""

    DEFAULT_CSS = """
    CopalVXFilesModal { align: center middle; }
    #files-modal-box {
        width: 94;
        height: 36;
        padding: 1 2;
        background: $surface;
        border: solid $accent;
    }
    #files-ver-row { height: 3; margin: 1 0; }
    #files-ver-row Label { width: 14; content-align: left middle; }
    #files-ver-row Select { width: 1fr; margin-right: 2; }
    #files-act-row { height: 3; }
    #files-act-row Button { margin-right: 1; }
    #files-output { height: 1fr; border: solid $panel; margin-top: 1; }
    """

    def __init__(self, project_name: str, versions: list[str],
                 local_version: str | None = None) -> None:
        super().__init__()
        self._project_name = project_name
        self._versions     = versions
        self._local_version = local_version

    def compose(self) -> ComposeResult:
        opts   = [(v, v) for v in self._versions]
        latest = self._versions[0] if self._versions else None
        # "From" defaults to the locally recorded version (what you have),
        # "To" defaults to the latest version on the server.
        from_val = (
            self._local_version
            if self._local_version and self._local_version in self._versions
            else (self._versions[-1] if len(self._versions) > 1 else latest)
        )
        with Vertical(id="files-modal-box"):
            yield Label(f"[bold]Files & Diff:[/bold] {self._project_name}")
            yield Rule()
            with Horizontal(id="files-ver-row"):
                yield Label("From:")
                yield Select(opts, value=from_val or Select.BLANK, id="sel-from")
                yield Label("To:")
                yield Select(opts, value=latest or Select.BLANK, id="sel-to")
            with Horizontal(id="files-act-row"):
                yield Button("View Diff", variant="primary", id="btn-diff")
                yield Button("File List (To)", id="btn-files")
                yield Button("Close", variant="error", id="btn-close")
            yield RichLog(id="files-output", markup=True, wrap=False, highlight=False)

    def on_mount(self) -> None:
        # Auto-load: diff if local differs from latest, otherwise file list
        latest = self._versions[0] if self._versions else None
        if latest and self._local_version and self._local_version != latest:
            self._load_diff(self._local_version, latest)
        elif latest:
            self._load_files(latest)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-close":
            self.dismiss()
            return
        from_v = self.query_one("#sel-from", Select).value
        to_v   = self.query_one("#sel-to",   Select).value
        if bid == "btn-diff" and from_v and to_v:
            self._load_diff(str(from_v), str(to_v))
        elif bid == "btn-files" and to_v:
            self._load_files(str(to_v))

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss()
            event.stop()

    def _log(self) -> RichLog:
        return self.query_one("#files-output", RichLog)

    # ── Diff ──────────────────────────────────────────────────────────────────

    def _load_diff(self, v1: str, v2: str) -> None:
        log = self._log()
        log.clear()
        log.write(f"[dim]Loading diff {v1} → {v2}...[/dim]")

        def _fetch():
            diff = copalvx_api.get_diff(self._project_name, v1, v2)
            self.app.call_from_thread(self._render_diff, diff, v1, v2)

        threading.Thread(target=_fetch, daemon=True).start()

    def _render_diff(self, diff: dict | None, v1: str, v2: str) -> None:
        log = self._log()
        log.clear()
        if not diff:
            log.write("[yellow]Diff unavailable (version not found or server error).[/yellow]")
            return

        added   = diff.get("added",   [])
        removed = diff.get("removed", [])
        changed = diff.get("changed", [])
        unc     = diff.get("unchanged_count", 0)

        log.write(f"[bold]Diff: {v1} → {v2}[/bold]")
        log.write("")
        for f in removed:
            log.write(f"[red]  - {f['path']:<58}  {_fmt_size(f.get('size'))}[/red]")
        for f in added:
            log.write(f"[green]  + {f['path']:<58}  {_fmt_size(f.get('size'))}[/green]")
        for f in changed:
            old_s = _fmt_size(f.get("old_size"))
            new_s = _fmt_size(f.get("new_size"))
            log.write(f"[yellow]  ~ {f['path']:<58}  {old_s} → {new_s}[/yellow]")
        log.write("")
        total = len(added) + len(removed) + len(changed)
        if total == 0:
            log.write("  [dim]No differences — versions are identical.[/dim]")
        else:
            log.write(
                f"  [dim]+ {len(added)} added  "
                f"- {len(removed)} removed  "
                f"~ {len(changed)} changed  "
                f"= {unc} unchanged[/dim]"
            )

    # ── File list ─────────────────────────────────────────────────────────────

    def _load_files(self, tag: str) -> None:
        log = self._log()
        log.clear()
        log.write(f"[dim]Loading file list for {tag}...[/dim]")

        def _fetch():
            manifest = copalvx_api.get_manifest(self._project_name, tag)
            self.app.call_from_thread(self._render_files, manifest, tag)

        threading.Thread(target=_fetch, daemon=True).start()

    def _render_files(self, manifest: dict | None, tag: str) -> None:
        log = self._log()
        log.clear()
        if not manifest:
            log.write("[yellow]Version not found or server error.[/yellow]")
            return

        files      = manifest.get("files", [])
        total_size = sum(f.get("size", 0) for f in files)
        log.write(
            f"[bold]Files in {tag}[/bold]  "
            f"({len(files)} files, {_fmt_size(total_size)} total)"
        )
        log.write("")
        for f in sorted(files, key=lambda x: x["path"]):
            sz = _fmt_size(f.get("size"))
            log.write(f"  {f['path']:<62}  [dim]{sz:>8}[/dim]")


class CopalVXProgressModal(ModalScreen):
    """Shows streaming progress for a CopalVX push/pull subprocess.

    Press `c` to copy the entire log to the system clipboard — useful for
    sharing errors.
    """

    BINDINGS = [
        Binding("c", "copy_log", "Copy log", show=True),
    ]

    DEFAULT_CSS = """
    CopalVXProgressModal { align: center middle; }
    #cvx-progress-box {
        width: 80;
        height: 24;
        padding: 1 2;
        background: $surface;
        border: solid $accent;
    }
    #cvx-progress-bar { margin: 1 0; }
    #cvx-progress-log { height: 1fr; }
    #cvx-progress-hint { color: $text-muted; }
    """

    def __init__(self, title: str) -> None:
        super().__init__()
        self._title = title
        self._done = False
        self._lines: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="cvx-progress-box"):
            yield Label(f"[bold]{self._title}[/bold]")
            yield ProgressBar(total=100, show_eta=False, id="cvx-progress-bar")
            yield RichLog(highlight=True, markup=True, id="cvx-progress-log")
            yield Static("[dim]C to copy log  •  Esc to dismiss when done[/dim]",
                         id="cvx-progress-hint")

    def on_key(self, event) -> None:
        if event.key == "escape" and self._done:
            self.dismiss(None)
            event.stop()

    def update_progress(self, completed: int, total: int) -> None:
        bar = self.query_one("#cvx-progress-bar", ProgressBar)
        bar.update(total=total, progress=completed)

    def write_line(self, text: str) -> None:
        self._lines.append(_strip_markup(text))
        self.query_one("#cvx-progress-log", RichLog).write(text)

    def mark_done(self, success: bool) -> None:
        self._done = True
        log = self.query_one("#cvx-progress-log", RichLog)
        msg = "Done." if success else "Failed — see above."
        self._lines.append(msg)
        log.write(f"[green bold]{msg}[/green bold]" if success
                  else f"[red bold]{msg}[/red bold]")
        self.query_one("#cvx-progress-hint", Static).update(
            "[dim]C to copy log  •  Esc to close[/dim]"
        )

    def action_copy_log(self) -> None:
        text = "\n".join(self._lines).strip()
        if not text:
            self.notify("Nothing to copy yet.", severity="warning")
            return
        try:
            self.app.copy_to_clipboard(text)
        except Exception as e:
            self.notify(f"Clipboard error: {e}", severity="error")
            return
        self.notify(f"Copied {len(self._lines)} line(s) to clipboard.",
                    title="CopalVX")


_MARKUP_RE = re.compile(r"\[/?[^\[\]]*?\]")

def _strip_markup(text: str) -> str:
    """Strip Rich/Textual markup so clipboard content is plain readable text."""
    return _MARKUP_RE.sub("", text)


class DoctorDriftRow(Widget):
    """One row in the DoctorModal — a stale registry entry with Re-register / Drop buttons."""

    class RegisterRequested(Message):
        def __init__(self, project_id: str, project_name: str) -> None:
            super().__init__()
            self.project_id   = project_id
            self.project_name = project_name

    class DropRequested(Message):
        def __init__(self, project_id: str, project_name: str) -> None:
            super().__init__()
            self.project_id   = project_id
            self.project_name = project_name

    DEFAULT_CSS = """
    DoctorDriftRow {
        height: 3;
        padding: 0 1;
        layout: horizontal;
        align: left middle;
        border-bottom: solid $panel;
    }
    DoctorDriftRow #drift-label { width: 1fr; }
    DoctorDriftRow Button { margin-left: 1; min-width: 12; }
    """

    def __init__(self, drift: dict) -> None:
        super().__init__()
        self._drift = drift

    def compose(self) -> ComposeResult:
        d        = self._drift
        label    = f'{d["id"]}'
        if d.get("name"):
            label += f'  [dim]{d["name"]}[/dim]'
        reason   = d.get("reason", "")
        explain  = {
            "missing_path": "folder is gone",
            "missing_yaml": "folder exists but no project.yaml",
        }.get(reason, reason)
        path     = d.get("path") or "(no path)"
        yield Label(
            f"{label}  [yellow]·[/yellow] [dim]{explain}: {path}[/dim]",
            id="drift-label",
        )
        yield Button("Re-register", id="btn-drift-register")
        yield Button("Drop",        id="btn-drift-drop", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-drift-register":
            self.post_message(self.RegisterRequested(
                self._drift["id"], self._drift.get("name", "") or ""
            ))
        elif event.button.id == "btn-drift-drop":
            self.post_message(self.DropRequested(
                self._drift["id"], self._drift.get("name", "") or ""
            ))


class DoctorModal(ModalScreen):
    """Surface `project doctor` findings in the TUI.

    Lists path-drift registry entries with Re-register / Drop actions, plus a
    read-only orphan-sessions section. Consumes `find_path_drift` and
    `find_orphan_sessions` from project_doctor — no helper reimplementation.
    """

    DEFAULT_CSS = """
    DoctorModal { align: center middle; }
    #doctor-box {
        width: 90;
        height: 32;
        padding: 1 2;
        background: $surface;
        border: solid $accent;
    }
    #doctor-scroll { height: 1fr; }
    #doctor-empty  { padding: 2 1; color: $text-muted; content-align: center middle; }
    #doctor-hint   { margin-top: 1; color: $text-muted; }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="doctor-box"):
            yield Label("[bold]Registry doctor[/bold]")
            yield Rule()
            yield ScrollableContainer(id="doctor-scroll")
            yield Static("[dim]Esc to close[/dim]", id="doctor-hint")

    def on_mount(self) -> None:
        self._refresh()

    def action_close(self) -> None:
        self.dismiss(None)

    def _refresh(self) -> None:
        registry = load_registry()
        drift    = find_path_drift(registry)
        orphans  = find_orphan_sessions(registry, SESSIONS_LOG)

        scroll = self.query_one("#doctor-scroll", ScrollableContainer)
        scroll.remove_children()

        if not drift and not orphans:
            scroll.mount(Static(
                "[green]All checks passed.[/green]\n"
                "[dim]No stale registry entries or orphan sessions.[/dim]",
                id="doctor-empty",
            ))
            return

        if drift:
            scroll.mount(Label("[bold]Path drift[/bold]"))
            for d in drift:
                scroll.mount(DoctorDriftRow(d))

        if orphans:
            if drift:
                scroll.mount(Static(""))
            scroll.mount(Label("[bold]Orphan sessions[/bold]"))
            scroll.mount(Static(
                "[dim]Sessions in sessions.jsonl whose project_id is no longer in "
                "the registry. Re-register the project (if its folder still exists) "
                "to flush these on the next sync.[/dim]"
            ))
            for pid, count in sorted(orphans.items()):
                plural = "session" if count == 1 else "sessions"
                scroll.mount(Static(f"  [yellow]·[/yellow] {pid} — {count} {plural}"))

    def on_doctor_drift_row_register_requested(
        self, event: DoctorDriftRow.RegisterRequested
    ) -> None:
        pid  = event.project_id
        name = event.project_name

        def on_pick(path: Path | None) -> None:
            if path is None:
                return
            try:
                upsert_registry(pid, name or pid, path)
                self.notify(f"Re-registered {pid} → {path}", title="Doctor")
            except Exception as e:
                self.notify(str(e), title="Re-register failed", severity="error")
                return
            self._refresh()

        start = Path.home() / "Projects"
        if not start.exists():
            start = Path.home()
        self.app.push_screen(SelectDirectory(str(start)), on_pick)

    def on_doctor_drift_row_drop_requested(
        self, event: DoctorDriftRow.DropRequested
    ) -> None:
        pid = event.project_id
        try:
            save_registry([p for p in load_registry() if p.get("id") != pid])
        except Exception as e:
            self.notify(str(e), title="Drop failed", severity="error")
            return
        self.notify(f"Dropped {pid} from registry.", title="Doctor")
        self._refresh()


class DeleteProjectModal(ModalScreen):
    """Confirm deletion of a ProjectRegistry project, with optional folder + server cleanup."""

    DEFAULT_CSS = """
    DeleteProjectModal { align: center middle; }
    #del-proj-box {
        width: 60;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: solid $error;
    }
    #del-proj-warning { color: $warning; margin-bottom: 1; }
    #del-proj-buttons { margin-top: 1; height: auto; }
    #del-proj-buttons Button { margin-right: 1; }
    """

    def __init__(
        self,
        project_name: str,
        cvx_project_name: str | None,
        local_folder_exists: bool = True,
    ) -> None:
        super().__init__()
        self._project_name        = project_name
        self._cvx_project_name    = cvx_project_name
        self._local_folder_exists = local_folder_exists

    def compose(self) -> ComposeResult:
        with Vertical(id="del-proj-box"):
            yield Label(f"[bold red]Delete project:[/bold red] {self._project_name}",
                        id="del-proj-warning")
            yield Rule()
            yield Static("Removes the project from the registry.")
            if self._local_folder_exists:
                yield Checkbox("Also delete local folder", id="del-folder-check")
            else:
                yield Static("[dim]Local folder is already gone.[/dim]")
            if self._cvx_project_name:
                yield Rule()
                yield Static(f"[dim]CopalVX: {self._cvx_project_name}[/dim]")
                yield Checkbox("Also delete from CopalVX server", id="del-cvx-check")
                yield Checkbox("  Include orphan blobs",          id="del-blobs-check")
            with Horizontal(id="del-proj-buttons"):
                yield Button("Delete", variant="error",   id="btn-del-confirm")
                yield Button("Cancel", variant="default", id="btn-del-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-del-cancel":
            self.dismiss(None)
        elif event.button.id == "btn-del-confirm":
            delete_folder = False
            if self._local_folder_exists:
                delete_folder = self.query_one("#del-folder-check", Checkbox).value
            delete_cvx    = False
            delete_blobs  = False
            if self._cvx_project_name:
                delete_cvx   = self.query_one("#del-cvx-check",   Checkbox).value
                delete_blobs = self.query_one("#del-blobs-check", Checkbox).value
            self.dismiss({
                "delete_folder": delete_folder,
                "delete_cvx":    delete_cvx,
                "delete_blobs":  delete_blobs,
            })

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)
            event.stop()


class CopalVXRenameModal(ModalScreen):
    """Prompt for a new CopalVX project name."""

    DEFAULT_CSS = """
    CopalVXRenameModal { align: center middle; }
    #cvx-rename-box {
        width: 56;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: solid $accent;
    }
    #cvx-rename-hint { margin-top: 1; color: $text-muted; }
    """

    def __init__(self, project_name: str) -> None:
        super().__init__()
        self._project_name = project_name

    def compose(self) -> ComposeResult:
        with Vertical(id="cvx-rename-box"):
            yield Label(f"[bold]Rename:[/bold] {self._project_name}")
            yield Rule()
            yield Label("New name:")
            yield Input(placeholder="New CopalVX project name", id="new-name-input")
            yield Static("[dim]Enter to rename  •  Esc to cancel[/dim]", id="cvx-rename-hint")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)
            event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        new_name = event.value.strip()
        if new_name:
            self.dismiss(new_name)


class CopalVXDeleteModal(ModalScreen):
    """Confirm deletion of a CopalVX project from the server."""

    DEFAULT_CSS = """
    CopalVXDeleteModal { align: center middle; }
    #cvx-delete-box {
        width: 56;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: solid $error;
    }
    #cvx-delete-warning { color: $warning; margin-bottom: 1; }
    #cvx-delete-buttons { margin-top: 1; height: auto; }
    #cvx-delete-buttons Button { margin-right: 1; }
    """

    def __init__(self, project_name: str) -> None:
        super().__init__()
        self._project_name = project_name

    def compose(self) -> ComposeResult:
        with Vertical(id="cvx-delete-box"):
            yield Label(f"[bold red]Delete from server:[/bold red] {self._project_name}",
                        id="cvx-delete-warning")
            yield Rule()
            yield Static("Removes all version history. Cannot be undone.")
            yield Checkbox("Also delete orphan blobs from storage", id="orphan-check")
            with Horizontal(id="cvx-delete-buttons"):
                yield Button("Delete", variant="error",   id="btn-confirm-delete")
                yield Button("Cancel", variant="default", id="btn-cancel-delete")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel-delete":
            self.dismiss(None)
        elif event.button.id == "btn-confirm-delete":
            orphans = self.query_one("#orphan-check", Checkbox).value
            self.dismiss({"delete_orphans": orphans})

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)
            event.stop()


class EditTemplateModal(ModalScreen):
    """Create or edit a project template."""

    DEFAULT_CSS = """
    EditTemplateModal { align: center middle; }
    #tmpl-edit-box {
        width: 64;
        height: 85vh;
        padding: 1 2;
        background: $surface;
        border: solid $accent;
    }
    #tmpl-edit-box .field-label { color: $text-muted; margin-top: 1; }
    #tmpl-edit-scroll { height: 1fr; }
    #tmpl-edit-buttons { margin-top: 1; height: auto; }
    #tmpl-edit-buttons Button { margin-right: 1; }
    """

    def __init__(self, template: dict | None = None) -> None:
        super().__init__()
        self._template = template or {}
        self._is_new   = template is None

    def compose(self) -> ComposeResult:
        title = "[bold]New Template[/bold]" if self._is_new else "[bold]Edit Template[/bold]"
        with Vertical(id="tmpl-edit-box"):
            yield Label(title)
            yield Rule()
            with ScrollableContainer(id="tmpl-edit-scroll"):
                yield Label("Name *", classes="field-label")
                yield Input(value=self._template.get("name", ""), placeholder="Template name", id="tmpl-name")
                yield Label("Type", classes="field-label")
                yield Select(
                    [("Internal", "tlc"), ("Client", "client"), ("Personal", "personal")],
                    value=self._template.get("type", "tlc"), allow_blank=False, id="tmpl-type",
                )
                yield Label("Category", classes="field-label")
                yield Select(
                    [("TVC", "tvc"), ("Digital Signage", "digital-signage"),
                     ("B2B", "b2b"), ("Digital", "digital")],
                    value=self._template.get("category", "tvc"), allow_blank=False, id="tmpl-category",
                )
                yield Label("Client", classes="field-label")
                yield Input(value=self._template.get("client") or "", placeholder="Client name", id="tmpl-client")
                yield Label("Director", classes="field-label")
                yield Input(value=self._template.get("director") or "", placeholder="Agency / director (optional)", id="tmpl-director")
                yield Label("Producer", classes="field-label")
                yield Input(value=self._template.get("producer") or "", placeholder="Producer (optional)", id="tmpl-producer")
                yield Label("Folders (comma-separated)", classes="field-label")
                default_folders = "01_Intake, 02_Workfiles, 03_Exports"
                folders_val = ", ".join(self._template.get("folders", [])) or default_folders
                yield Input(value=folders_val, placeholder=default_folders, id="tmpl-folders")
            with Horizontal(id="tmpl-edit-buttons"):
                yield Button("Save", variant="primary", id="btn-tmpl-save")
                yield Button("Cancel", variant="default", id="btn-tmpl-cancel")

    def on_mount(self) -> None:
        self.query_one("#tmpl-name", Input).focus()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-tmpl-cancel":
            self.dismiss(None)
        elif event.button.id == "btn-tmpl-save":
            name = self.query_one("#tmpl-name", Input).value.strip()
            if not name:
                self.notify("Name is required.", severity="error")
                self.query_one("#tmpl-name", Input).focus()
                return
            raw     = self.query_one("#tmpl-folders", Input).value
            folders = [f.strip() for f in raw.split(",") if f.strip()]
            if not folders:
                folders = ["01_Intake", "02_Workfiles", "03_Exports"]
            self.dismiss({
                "name":          name,
                "type":          self.query_one("#tmpl-type",     Select).value,
                "category":      self.query_one("#tmpl-category", Select).value,
                "client":        self.query_one("#tmpl-client",   Input).value.strip() or None,
                "director":      self.query_one("#tmpl-director", Input).value.strip() or None,
                "producer":      self.query_one("#tmpl-producer", Input).value.strip() or None,
                "collaborators": self._template.get("collaborators", []),
                "folders":       folders,
            })


class TemplateScreen(Screen):
    """Manage project templates."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("n",      "new_template",    "New"),
        Binding("e",      "edit_template",   "Edit"),
        Binding("d",      "delete_template", "Delete"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._templates: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield DataTable(id="tmpl-table")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns("Name", "Type", "Category", "Client", "Folders")
        self._refresh()

    def _refresh(self) -> None:
        self._templates = load_templates()
        table           = self.query_one(DataTable)
        table.clear()
        for i, t in enumerate(self._templates):
            folders_str = ", ".join(t.get("folders", []))
            table.add_row(
                t.get("name", "—"),
                t.get("type", "—"),
                t.get("category", "—"),
                t.get("client") or "—",
                folders_str,
                key=str(i),
            )

    def _selected_index(self) -> int | None:
        table = self.query_one(DataTable)
        key   = table.cursor_row_key
        if key is None:
            return None
        try:
            return int(key.value)
        except Exception:
            return None

    def action_new_template(self) -> None:
        def on_result(t: dict | None) -> None:
            if t is None:
                return
            self._templates.append(t)
            save_templates(self._templates)
            self._refresh()

        self.app.push_screen(EditTemplateModal(), on_result)

    def action_edit_template(self) -> None:
        idx = self._selected_index()
        if idx is None or idx >= len(self._templates):
            return
        template = self._templates[idx].copy()

        def on_result(t: dict | None) -> None:
            if t is None:
                return
            self._templates[idx] = t
            save_templates(self._templates)
            self._refresh()

        self.app.push_screen(EditTemplateModal(template), on_result)

    def action_delete_template(self) -> None:
        idx = self._selected_index()
        if idx is None or idx >= len(self._templates):
            return
        name = self._templates[idx].get("name", "?")
        del self._templates[idx]
        save_templates(self._templates)
        self._refresh()
        self.notify(f"Template '{name}' deleted.")


class ProjectDetailScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen",  "Back"),
        Binding("t",      "toggle_timer",    "Start/stop timer", priority=True),
        Binding("p",      "push_copalvx",    "Push"),
        Binding("l",      "pull_copalvx",    "Pull"),
        Binding("f",      "files_copalvx",   "Files/Diff"),
        Binding("n",      "rename_copalvx",  "Rename CVX"),
        Binding("x",      "delete_copalvx",  "Delete CVX"),
        Binding("d",      "delete_project",  "Delete project"),
        Binding("r",      "refresh",         "Refresh"),
    ]

    def __init__(self, project: dict, auto_push: bool = False) -> None:
        super().__init__()
        self._project = project
        self._data: dict = {}
        self._auto_push = auto_push
        self._cvx_stats: dict | None = None
        self._cvx_events: list[dict] | None = None
        self._session_cache: dict | None = None
        self._stop_session_poller = threading.Event()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield ScrollableContainer(id="detail-body")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_data()
        self.set_interval(1, self._tick_timer)
        self.query_one("#detail-body", ScrollableContainer).focus()
        if self._auto_push:
            self._auto_push = False
            self.set_timer(0.3, self._do_auto_push)
        # Background: fetch CopalVX server stats + activity log for this project
        cvx_name = self._data.get("copalvx", {}).get("project_name")
        if cvx_name:
            threading.Thread(target=self._fetch_cvx_stats, args=(cvx_name,), daemon=True).start()
            threading.Thread(target=self._fetch_cvx_events, args=(cvx_name,), daemon=True).start()
        threading.Thread(target=self._session_poll_loop, daemon=True).start()

    def on_unmount(self) -> None:
        self._stop_session_poller.set()

    def _session_poll_loop(self) -> None:
        while not self._stop_session_poller.is_set():
            self._session_cache = _active_session()
            self._stop_session_poller.wait(5.0)

    def _fetch_cvx_stats(self, cvx_name: str) -> None:
        stats = copalvx_api.get_project_stats(cvx_name)
        if stats:
            self._cvx_stats = stats
            self.app.call_from_thread(self._build)

    def _fetch_cvx_events(self, cvx_name: str) -> None:
        events = copalvx_api.get_events(cvx_name, limit=20)
        self._cvx_events = events
        self.app.call_from_thread(self._build)

    def _do_auto_push(self) -> None:
        """Push v1.0 automatically after project init."""
        project_name = self._cvx_project_name()
        project_path = self._project.get("path", "")
        tag = "v1.0"

        progress = CopalVXProgressModal(f"Push: {project_name} @ {tag}")
        self.app.push_screen(progress)

        def _run():
            try:
                proc = copalvx_api.run_push(project_name, tag, project_path, "Initial version", "")
                self._cvx_stream_subprocess(proc, progress)
            except Exception as e:
                self.app.call_from_thread(progress.write_line, f"[red]{e}[/red]")
                self.app.call_from_thread(progress.mark_done, False)

        threading.Thread(target=_run, daemon=True).start()

    def _refresh_data(self) -> None:
        self._data = _detail_data(self._project)
        self._build()

    def _build(self) -> None:
        d    = self._data
        body = self.query_one("#detail-body", ScrollableContainer)
        body.remove_children()

        def row(label: str, value: str) -> Static:
            return Static(f"  [dim]{label:<14}[/dim] {value}")

        # ── Overview ──────────────────────────────────────────────────────────
        body.mount(Label(f"  [bold]{d['name']}[/bold]", classes="section-title"))
        body.mount(Rule())
        body.mount(row("ID",       d["id"]))
        body.mount(row("Type",     f"{d['type']} / {d['category']}"))
        body.mount(row("Client",   d["client"]))
        body.mount(row("Director", d["director"]))
        body.mount(row("Producer", d["producer"]))
        body.mount(row("Created",  d["created_at"]))
        body.mount(row("Deadline", d["deadline"]))

        # ── Phase ─────────────────────────────────────────────────────────────
        body.mount(Static(""))
        body.mount(Label("  [bold]PHASE[/bold]", classes="section-title"))
        body.mount(Rule())
        body.mount(row("Current",  f"{d['phase']}  ({d['days_in_phase']} days)"))

        # ── Time ──────────────────────────────────────────────────────────────
        body.mount(Static(""))
        body.mount(Label("  [bold]TIME[/bold]", classes="section-title"))
        body.mount(Rule())
        body.mount(row("Total", fmt_h(d["total_sec"]) if d["total_sec"] else "—"))
        for phase, sec in d["by_phase"].items():
            body.mount(row(f"  {phase}", fmt_h(sec)))

        # ── Financial ─────────────────────────────────────────────────────────
        fin = d["financial"]
        if any(fin.get(k) for k in ("quoted_budget", "invoiced_amount", "paid")):
            body.mount(Static(""))
            body.mount(Label("  [bold]FINANCIAL[/bold]", classes="section-title"))
            body.mount(Rule())
            cur = fin.get("currency", "EUR")
            def money(v):
                return f"{cur} {v:,.0f}" if v is not None else "—"
            body.mount(row("Budget",   money(fin.get("quoted_budget"))))
            body.mount(row("Invoiced", money(fin.get("invoiced_amount"))))
            body.mount(row("Paid",     str(fin.get("paid") or "—")))

        # ── Deliverables ──────────────────────────────────────────────────────
        body.mount(Static(""))
        body.mount(Label("  [bold]DELIVERABLES[/bold]", classes="section-title"))
        body.mount(Rule())
        if d["deliverables"]:
            for deliv in d["deliverables"]:
                rel  = days_ago(deliv.get("delivered_at", ""))
                body.mount(Static(
                    f"  {deliv.get('name','?')}  "
                    f"[dim]{deliv.get('type','?')} -> {deliv.get('recipient','?')}  {rel}[/dim]"
                ))
        else:
            body.mount(Static("  [dim]No deliverables yet.[/dim]"))

        # ── CopalVX ───────────────────────────────────────────────────────────
        cvx = d["copalvx"]
        if cvx.get("project_name"):
            body.mount(Static(""))
            body.mount(Label("  [bold]COPALVX[/bold]", classes="section-title"))
            body.mount(Rule())
            body.mount(row("Project",   cvx.get("project_name", "—")))
            body.mount(row("Last push", cvx.get("last_push_version", "—")))
            body.mount(row("Pushed at", str(cvx.get("last_push", "—"))[:10]))
            if self._cvx_stats:
                s  = self._cvx_stats
                lv = s.get("latest_version") or "—"
                vc = str(s.get("version_count") or "?")
                sz = _fmt_size(s.get("total_storage_bytes"))
                body.mount(row("Server ver", lv))
                body.mount(row("Versions",   vc))
                body.mount(row("Storage",    sz))
            else:
                body.mount(Static("  [dim]fetching server stats…[/dim]"))

            # Activity log (server-recorded push/pull events)
            if self._cvx_events is not None:
                last_push = next((e for e in self._cvx_events if e.get("kind") == "push"), None)
                last_pull = next((e for e in self._cvx_events if e.get("kind") == "pull"), None)
                if last_push:
                    rel = days_ago(last_push.get("created_at", ""))
                    body.mount(row(
                        "Recent push",
                        f"{last_push.get('version_tag','?')}  "
                        f"[dim]by {last_push.get('user','?')}  {rel}[/dim]",
                    ))
                if last_pull:
                    rel = days_ago(last_pull.get("created_at", ""))
                    body.mount(row(
                        "Recent pull",
                        f"{last_pull.get('version_tag','?')}  "
                        f"[dim]by {last_pull.get('user','?')}@{last_pull.get('host','?')}  {rel}[/dim]",
                    ))

        # ── Notes ─────────────────────────────────────────────────────────────
        if d["notes"]:
            body.mount(Static(""))
            body.mount(Label("  [bold]NOTES[/bold]", classes="section-title"))
            body.mount(Rule())
            body.mount(Static(f"  {d['notes']}"))

        body.mount(Static(""))

    def _tick_timer(self) -> None:
        session = self._session_cache
        name    = self._data.get("name", "")
        if session and session.get("project_id") == self._data.get("id"):
            elapsed        = _elapsed(session.get("start", ""))
            self.app.title = f"●  {name}  {elapsed}"
        else:
            self.app.title = f"⊘  {name}"

    def action_refresh(self) -> None:
        self._refresh_data()

    def action_toggle_timer(self) -> None:
        session = _active_session()
        pid     = self._data.get("id")

        if session and session.get("project_id") == pid:
            # Timer running on this project — stop immediately, no modal needed
            try:
                resp = _service_call("POST", "/stop", {"reason": "manual"})
                if resp.get("stopped"):
                    self.notify(
                        f"{fmt_h(resp.get('duration_sec', 0))} logged.",
                        title="■ Stopped",
                    )
            except ServiceUnavailable as e:
                self.notify(str(e), title="Service not running", severity="error")
            except Exception as e:
                self.notify(str(e), title="Error", severity="error")
            self._refresh_data()
        else:
            # Show description modal; start timer in the dismiss callback
            def on_description(description: str | None) -> None:
                if description is None:
                    return  # user cancelled with Esc
                try:
                    resp = _service_call("POST", "/start", {
                        "projectId":   pid,
                        "description": description or None,
                        "phase":       self._data.get("phase"),
                    })
                    stopped_prev = resp.get("stopped_prev")
                    if stopped_prev:
                        prev_pid = stopped_prev.get("project_id", "")
                        prev_name = next(
                            (p.get("name") for p in load_registry() if p.get("id") == prev_pid),
                            prev_pid,
                        )
                        self.notify(
                            f"{prev_name} — {fmt_h(int(stopped_prev.get('duration_sec', 0)))} logged.",
                            title="■ Stopped",
                        )
                    label = f" — {description}" if description else ""
                    self.notify(f"{self._data.get('name','')}{label}", title="● Started")
                except ServiceUnavailable as e:
                    self.notify(str(e), title="Service not running", severity="error")
                except Exception as e:
                    self.notify(str(e), title="Error", severity="error")
                self._refresh_data()

            self.app.push_screen(TimerStartModal(), on_description)

    def _cvx_project_name(self) -> str:
        """CopalVX project name from copalvx block, else folder name."""
        cvx_name = self._data.get("copalvx", {}).get("project_name")
        if cvx_name:
            return cvx_name
        path = self._project.get("path", "")
        return Path(path).name if path else self._data.get("name", "unknown")

    def _cvx_stream_subprocess(self, proc, progress_modal: "CopalVXProgressModal") -> None:
        _cvx_stream(proc, progress_modal, self.app, self._refresh_data)

    def action_push_copalvx(self) -> None:
        project_name = self._cvx_project_name()
        project_path = self._project.get("path", "")

        versions     = copalvx_api.get_versions(project_name)
        suggested    = _cvx_next_tag(versions)

        def on_confirm(result: dict | None) -> None:
            if result is None:
                return
            tag = result["tag"]
            msg = result.get("message", "")

            progress = CopalVXProgressModal(f"Push: {project_name} @ {tag}")
            self.app.push_screen(progress)

            def _run():
                try:
                    proc = copalvx_api.run_push(project_name, tag, project_path, msg, "")
                    self._cvx_stream_subprocess(proc, progress)
                except Exception as e:
                    self.app.call_from_thread(progress.write_line, f"[red]{e}[/red]")
                    self.app.call_from_thread(progress.mark_done, False)

            threading.Thread(target=_run, daemon=True).start()

        self.app.push_screen(
            CopalVXPushModal(project_name, suggested),
            on_confirm,
        )

    def action_pull_copalvx(self) -> None:
        project_name  = self._cvx_project_name()
        project_path  = self._project.get("path", "")
        local_version = self._data.get("copalvx", {}).get("last_push_version")

        versions = copalvx_api.get_versions(project_name)
        if not versions:
            self.notify("No versions found on server.", title="CopalVX", severity="warning")
            return

        def on_confirm(result: dict | None) -> None:
            if result is None:
                return
            tag = result["tag"]

            def _fetch_diff() -> None:
                folders = []
                if local_version and local_version != tag:
                    try:
                        diff = copalvx_api.get_diff(project_name, local_version, tag)
                        if diff:
                            folders = copalvx_api.extract_changed_folders(diff)
                    except Exception:
                        pass
                if folders:
                    modal = SelectivePullModal(project_name, tag, folders)
                    self.app.call_from_thread(self.app.push_screen, modal, on_folder_select)
                else:
                    self.app.call_from_thread(_start_pull, [])

            def on_folder_select(sel: dict | None) -> None:
                if sel is None:
                    return
                _start_pull(sel["prefixes"])

            def _start_pull(prefixes: list[str]) -> None:
                progress = CopalVXProgressModal(f"Pull: {project_name} @ {tag}")
                self.app.push_screen(progress)
                def _run() -> None:
                    try:
                        proc = copalvx_api.run_pull(project_name, tag, project_path, prefixes=prefixes)
                        self._cvx_stream_subprocess(proc, progress)
                    except Exception as e:
                        self.app.call_from_thread(progress.write_line, f"[red]{e}[/red]")
                        self.app.call_from_thread(progress.mark_done, False)
                threading.Thread(target=_run, daemon=True).start()

            threading.Thread(target=_fetch_diff, daemon=True).start()

        self.app.push_screen(
            CopalVXPullModal(project_name, versions, project_path),
            on_confirm,
        )

    def _update_cvx_project_name(self, new_name: str) -> None:
        """Update copalvx.project_name in project.yaml after a server rename."""
        yaml_path = Path(self._project.get("path", "")) / "project.yaml"
        if not yaml_path.exists():
            return
        try:
            record = load_project_yaml(yaml_path)
            if "copalvx" not in record:
                record["copalvx"] = {}
            record["copalvx"]["project_name"] = new_name
            yaml_path.write_text(
                _YAML_HEADER + yaml.dump(
                    record, default_flow_style=False, allow_unicode=True, sort_keys=False,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass  # best-effort; non-fatal

    def action_files_copalvx(self) -> None:
        project_name  = self._cvx_project_name()
        local_version = self._data.get("copalvx", {}).get("last_push_version")

        versions = copalvx_api.get_versions(project_name)
        if not versions:
            self.notify("No versions found on server.", title="CopalVX", severity="warning")
            return

        self.app.push_screen(
            CopalVXFilesModal(project_name, versions, local_version)
        )

    def action_rename_copalvx(self) -> None:
        project_name = self._cvx_project_name()

        def on_confirm(new_name: str | None) -> None:
            if not new_name or new_name == project_name:
                return
            try:
                copalvx_api.rename_project(project_name, new_name)
                self._update_cvx_project_name(new_name)
                self.notify(f"Renamed to '{new_name}'", title="CopalVX")
                self._refresh_data()
            except Exception as e:
                self.notify(str(e), title="Rename failed", severity="error")

        self.app.push_screen(CopalVXRenameModal(project_name), on_confirm)

    def action_delete_copalvx(self) -> None:
        project_name = self._cvx_project_name()

        def on_confirm(result: dict | None) -> None:
            if result is None:
                return
            try:
                copalvx_api.delete_project(project_name, result.get("delete_orphans", False))
                self.notify(f"'{project_name}' deleted from server.", title="CopalVX")
                self._refresh_data()
            except Exception as e:
                self.notify(str(e), title="Delete failed", severity="error")

        self.app.push_screen(CopalVXDeleteModal(project_name), on_confirm)

    def action_delete_project(self) -> None:
        project_id   = self._data.get("id")
        project_name = self._data.get("name", project_id)
        cvx_name     = self._data.get("copalvx", {}).get("project_name") or None

        def on_confirm(result: dict | None) -> None:
            if result is None:
                return

            errors = []

            # CopalVX server delete first — if it fails, local data is still intact
            if result.get("delete_cvx") and cvx_name:
                try:
                    copalvx_api.delete_project(cvx_name, result.get("delete_blobs", False))
                except Exception as e:
                    errors.append(f"CopalVX: {e}")

            # Remove from local registry
            save_registry([p for p in load_registry() if p.get("id") != project_id])

            # Delete local folder
            if result.get("delete_folder"):
                try:
                    path = Path(self._project.get("path", ""))
                    if path.exists():
                        shutil.rmtree(path)
                except Exception as e:
                    errors.append(f"Folder: {e}")

            if errors:
                self.notify("\n".join(errors), title="Partial delete", severity="warning")
            else:
                self.notify(f"'{project_name}' deleted.", title="Project deleted")

            self.app.pop_screen()

        path_str            = self._project.get("path", "")
        local_folder_exists = bool(path_str) and Path(path_str).exists()
        self.app.push_screen(
            DeleteProjectModal(project_name, cvx_name, local_folder_exists),
            on_confirm,
        )


# ── App ────────────────────────────────────────────────────────────────────────

class PMApp(App):
    TITLE = "PM"

    def __init__(self, initial_screen: str | None = None, initial_dir: str | None = None) -> None:
        super().__init__()
        self._initial_screen = initial_screen
        self._initial_dir    = initial_dir

    CSS = """
    Screen {
        background: $surface;
    }
    DataTable {
        height: 1fr;
    }
    #search-row {
        height: 3;
        padding: 0 1;
        align: left middle;
    }
    #search-input {
        width: 1fr;
        margin-right: 1;
    }
    #project-list {
        height: 1fr;
        padding-top: 1;
    }
    #detail-body {
        height: 1fr;
        padding-top: 1;
    }
    .section-title {
        color: $accent;
        padding-top: 1;
    }
    Rule {
        color: $panel;
        margin: 0;
    }
    """

    def on_mount(self) -> None:
        self.push_screen(DashboardScreen())
        if self._initial_screen == "init":
            self.push_screen(InitScreen(initial_dir=self._initial_dir))


def main(initial_screen: str | None = None, initial_dir: str | None = None) -> None:
    PMApp(initial_screen=initial_screen, initial_dir=initial_dir).run()


if __name__ == "__main__":
    main()
