"""
pm-tui — Textual TUI for ProjectRegistry.

Screens:
  DashboardScreen     — all registered projects, live timer, click to open
  ProjectDetailScreen — full project view + actions
"""

import json
import threading
import urllib.request
import yaml
from datetime import datetime, timezone
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical, Horizontal
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button, Checkbox, DataTable, Footer, Header, Input, Label,
    ProgressBar, RadioButton, RadioSet, RichLog, Rule, Select, Static,
)

from project_registry.config import DATA_DIR
from project_registry import copalvx_api
from project_registry.pm import (
    _YAML_HEADER, build_project_record, compute_id_and_path,
    days_ago, fmt_h, load_project_yaml, load_registry,
    QUICK_PRESETS, upsert_registry,
)


# ── Service helpers ────────────────────────────────────────────────────────────

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
    cfg  = json.loads((DATA_DIR / "config.json").read_text(encoding="utf-8"))
    port = cfg.get("port", 5123)
    data = json.dumps(body).encode() if body is not None else None
    req  = urllib.request.Request(
        f"http://127.0.0.1:{port}{endpoint}",
        data=data, method=method,
        headers={"X-API-Key": cfg["api_key"], "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def _elapsed(start_iso: str) -> str:
    try:
        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        secs  = int((datetime.now(timezone.utc) - start).total_seconds())
        h, m  = divmod(secs // 60, 60)
        s     = secs % 60
        return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"
    except Exception:
        return "?"


# ── Data loaders ───────────────────────────────────────────────────────────────

def _dashboard_rows() -> list[dict]:
    rows = []
    for entry in load_registry():
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

        rows.append({
            "id":            pid,
            "name":          entry.get("name", pid),
            "phase":         phase,
            "total_sec":     total_sec,
            "time_str":      fmt_h(total_sec) if total_sec else "—",
            "deadline":      deadline,
            "last_delivery": last_deliv,
            "path":          str(path),
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
        height: auto;
        max-height: 90vh;
        padding: 1 2;
        background: $surface;
        border: solid $accent;
    }
    #init-box .field-label {
        color: $text-muted;
        margin-top: 1;
    }
    #init-buttons {
        margin-top: 1;
        height: auto;
    }
    #init-buttons Button {
        margin-right: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._preset_index = 0  # 0=Custom, 1=Tactical, 2=DS

    def compose(self) -> ComposeResult:
        with ScrollableContainer():
            with Vertical(id="init-box"):
                yield Label("[bold]New Project[/bold]")
                yield Rule()
                yield Label("Name *", classes="field-label")
                yield Input(placeholder="Project name", id="name-input")
                yield Label("Preset", classes="field-label")
                yield RadioSet(
                    RadioButton("Custom"),
                    RadioButton("Tactical"),
                    RadioButton("Digital Signage"),
                    id="preset-radio",
                )
                with Vertical(id="custom-fields"):
                    yield Label("Type", classes="field-label")
                    yield Select(
                        [("TLC", "tlc"), ("Client", "client"), ("Personal", "personal")],
                        value="tlc", allow_blank=False, id="type-select",
                    )
                    yield Label("Category", classes="field-label")
                    yield Select(
                        [("TVC", "tvc"), ("Digital Signage", "digital-signage"),
                         ("B2B", "b2b"), ("Digital", "digital")],
                        value="tvc", allow_blank=False, id="category-select",
                    )
                    yield Label("Client", classes="field-label")
                    yield Input(placeholder="Client name (optional)", id="client-input")
                    yield Label("Director", classes="field-label")
                    yield Input(placeholder="e.g.  (optional)", id="director-input")
                    yield Label("Producer", classes="field-label")
                    yield Input(placeholder="e.g.  (optional)", id="producer-input")
                    yield Label("Deadline", classes="field-label")
                    yield Input(placeholder="YYYY-MM-DD (optional)", id="deadline-input")
                yield Label("Project folder", classes="field-label")
                yield Input(id="dir-input")
                yield Checkbox("Append _NNN suffix to folder name", id="inc-check")
                with Horizontal(id="init-buttons"):
                    yield Button("Create", variant="primary", id="btn-create")
                    yield Button("Cancel", variant="default", id="btn-cancel")

    def on_mount(self) -> None:
        self.query_one("#dir-input", Input).value = self._default_dir()
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
        self.query_one("#custom-fields").display = (event.index == 0)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.app.pop_screen()
        elif event.button.id == "btn-create":
            self._do_create()

    def _do_create(self) -> None:
        name = self.query_one("#name-input", Input).value.strip()
        if not name:
            self.notify("Project name is required.", severity="error")
            self.query_one("#name-input", Input).focus()
            return

        base_dir = Path(self.query_one("#dir-input", Input).value.strip() or self._default_dir())

        idx = self._preset_index
        if idx == 1:
            p             = QUICK_PRESETS["tactical"]
            proj_type     = p["type"];    category      = p["category"]
            client        = p["client"];  director      = p["director"]
            producer      = p["producer"]; collaborators = p["collaborators"]
            deadline      = None
        elif idx == 2:
            p             = QUICK_PRESETS["ds"]
            proj_type     = p["type"];    category      = p["category"]
            client        = p["client"];  director      = p["director"]
            producer      = p["producer"]; collaborators = p["collaborators"]
            deadline      = None
        else:
            proj_type     = self.query_one("#type-select",     Select).value
            category      = self.query_one("#category-select", Select).value
            client        = self.query_one("#client-input",    Input).value.strip() or None
            director      = self.query_one("#director-input",  Input).value.strip() or None
            producer      = self.query_one("#producer-input",  Input).value.strip() or None
            deadline      = self.query_one("#deadline-input",  Input).value.strip() or None
            collaborators = None

        try:
            use_inc = self.query_one("#inc-check", Checkbox).value
            pid, root = compute_id_and_path(name, base_dir, use_increment=use_inc)
            if not use_inc and root.exists():
                raise ValueError(f"Folder '{root.name}' already exists.")
            root.mkdir(parents=True, exist_ok=use_inc)
            for d in ["01_Intake", "02_Workfiles", "03_Exports"]:
                (root / d).mkdir(exist_ok=True)

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
        Binding("n", "new_project", "New project"),
        Binding("r", "refresh",     "Refresh"),
        Binding("q", "app.quit",    "Quit"),
    ]

    _rows: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield DataTable(id="projects-table")
        yield Footer()

    def on_mount(self) -> None:
        table              = self.query_one(DataTable)
        table.cursor_type  = "row"
        table.add_columns("Name", "Phase", "Time", "Deadline", "Last delivery")
        self._refresh_data()
        self.set_interval(1,  self._tick_timer)
        self.set_interval(30, self._refresh_data)

    def _refresh_data(self) -> None:
        self._rows   = _dashboard_rows()
        table        = self.query_one(DataTable)
        session      = _active_session()
        active_pid   = session.get("project_id") if session else None
        table.clear()
        for row in self._rows:
            marker = "● " if row["id"] == active_pid else "  "
            table.add_row(
                marker + row["name"],
                row["phase"],
                row["time_str"],
                row["deadline"],
                row["last_delivery"],
                key=row["id"],
            )

    def _tick_timer(self) -> None:
        session = _active_session()
        if session:
            pid     = session.get("project_id", "")
            name    = next((r["name"] for r in self._rows if r["id"] == pid), pid)
            elapsed = _elapsed(session.get("start", ""))
            self.app.title = f"PM  ●  {name}  {elapsed}"
        else:
            self.app.title = "PM"

    def action_refresh(self) -> None:
        self._refresh_data()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        pid     = event.row_key.value
        project = next((r for r in self._rows if r["id"] == pid), None)
        if project:
            self.app.push_screen(ProjectDetailScreen(project))

    def action_new_project(self) -> None:
        self.app.push_screen(InitScreen())


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


class CopalVXProgressModal(ModalScreen):
    """Shows streaming progress for a CopalVX push/pull subprocess."""

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

    def compose(self) -> ComposeResult:
        with Vertical(id="cvx-progress-box"):
            yield Label(f"[bold]{self._title}[/bold]")
            yield ProgressBar(total=100, show_eta=False, id="cvx-progress-bar")
            yield RichLog(highlight=True, markup=True, id="cvx-progress-log")
            yield Static("[dim]Esc to dismiss when done[/dim]", id="cvx-progress-hint")

    def on_key(self, event) -> None:
        if event.key == "escape" and self._done:
            self.dismiss(None)
            event.stop()

    def update_progress(self, completed: int, total: int) -> None:
        bar = self.query_one("#cvx-progress-bar", ProgressBar)
        bar.update(total=total, progress=completed)

    def write_line(self, text: str) -> None:
        self.query_one("#cvx-progress-log", RichLog).write(text)

    def mark_done(self, success: bool) -> None:
        self._done = True
        log = self.query_one("#cvx-progress-log", RichLog)
        if success:
            log.write("[green bold]Done.[/green bold]")
        else:
            log.write("[red bold]Failed — see above.[/red bold]")
        self.query_one("#cvx-progress-hint", Static).update("[dim]Esc to close[/dim]")


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


class ProjectDetailScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen",  "Back"),
        Binding("t",      "toggle_timer",    "Start/stop timer", priority=True),
        Binding("p",      "push_copalvx",    "Push"),
        Binding("l",      "pull_copalvx",    "Pull"),
        Binding("n",      "rename_copalvx",  "Rename"),
        Binding("x",      "delete_copalvx",  "Delete"),
        Binding("r",      "refresh",         "Refresh"),
    ]

    def __init__(self, project: dict, auto_push: bool = False) -> None:
        super().__init__()
        self._project = project
        self._data: dict = {}
        self._auto_push = auto_push

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield ScrollableContainer(Vertical(id="detail-body"))
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_data()
        self.set_interval(1, self._tick_timer)
        if self._auto_push:
            self._auto_push = False
            self.set_timer(0.3, self._do_auto_push)

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
        body = self.query_one("#detail-body", Vertical)
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

        # ── Notes ─────────────────────────────────────────────────────────────
        if d["notes"]:
            body.mount(Static(""))
            body.mount(Label("  [bold]NOTES[/bold]", classes="section-title"))
            body.mount(Rule())
            body.mount(Static(f"  {d['notes']}"))

        body.mount(Static(""))

    def _tick_timer(self) -> None:
        session = _active_session()
        if session and session.get("project_id") == self._data.get("id"):
            elapsed        = _elapsed(session.get("start", ""))
            self.app.title = f"{self._data.get('name', '')}  ●  {elapsed}"
        else:
            self.app.title = self._data.get("name", "")

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
            except Exception as e:
                self.notify(str(e), title="Error", severity="error")
            self._refresh_data()
        else:
            # Show description modal; start timer in the dismiss callback
            def on_description(description: str | None) -> None:
                if description is None:
                    return  # user cancelled with Esc
                try:
                    _service_call("POST", "/start", {
                        "projectId":   pid,
                        "description": description or None,
                        "phase":       self._data.get("phase"),
                    })
                    label = f" — {description}" if description else ""
                    self.notify(f"{self._data.get('name','')}{label}", title="● Started")
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

    def _cvx_next_tag(self, versions: list[str]) -> str:
        if not versions:
            return "v1.0"
        latest = versions[0]
        try:
            parts = latest.lstrip("v").split(".")
            parts[-1] = str(int(parts[-1]) + 1)
            return "v" + ".".join(parts)
        except Exception:
            return "v1.0"

    def _cvx_stream_subprocess(self, proc, progress_modal: "CopalVXProgressModal") -> None:
        """Read subprocess stdout line-by-line, update progress modal. Runs in a thread."""
        import re
        pattern = re.compile(r"\[(UPLOAD|DOWNLOAD)\]\s+(\d+)/(\d+)\s+(.*)")
        try:
            for line in proc.stdout:
                line = line.rstrip("\n")
                m = pattern.match(line)
                if m:
                    done, total = int(m.group(2)), int(m.group(3))
                    self.app.call_from_thread(progress_modal.update_progress, done, total)
                    self.app.call_from_thread(progress_modal.write_line, m.group(4))
                else:
                    self.app.call_from_thread(progress_modal.write_line, line)
            proc.wait()
            success = proc.returncode == 0
            self.app.call_from_thread(progress_modal.mark_done, success)
            if success:
                self.app.call_from_thread(self._refresh_data)
        except Exception as e:
            self.app.call_from_thread(progress_modal.write_line, f"[red]{e}[/red]")
            self.app.call_from_thread(progress_modal.mark_done, False)

    def action_push_copalvx(self) -> None:
        project_name = self._cvx_project_name()
        project_path = self._project.get("path", "")

        versions     = copalvx_api.get_versions(project_name)
        suggested    = self._cvx_next_tag(versions)

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
        project_name = self._cvx_project_name()
        project_path = self._project.get("path", "")

        versions = copalvx_api.get_versions(project_name)
        if not versions:
            self.notify("No versions found on server.", title="CopalVX", severity="warning")
            return

        def on_confirm(result: dict | None) -> None:
            if result is None:
                return
            tag = result["tag"]

            progress = CopalVXProgressModal(f"Pull: {project_name} @ {tag}")
            self.app.push_screen(progress)

            def _run():
                try:
                    proc = copalvx_api.run_pull(project_name, tag, project_path)
                    self._cvx_stream_subprocess(proc, progress)
                except Exception as e:
                    self.app.call_from_thread(progress.write_line, f"[red]{e}[/red]")
                    self.app.call_from_thread(progress.mark_done, False)

            threading.Thread(target=_run, daemon=True).start()

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


# ── App ────────────────────────────────────────────────────────────────────────

class PMApp(App):
    TITLE = "PM"
    CSS = """
    Screen {
        background: $surface;
    }
    DataTable {
        height: 1fr;
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


def main() -> None:
    PMApp().run()


if __name__ == "__main__":
    main()
