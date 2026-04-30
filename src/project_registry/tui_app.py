"""
pm-tui — Textual TUI for ProjectRegistry.

Screens:
  DashboardScreen     — all registered projects, live timer, click to open
  ProjectDetailScreen — full project view + actions
"""

import json
import urllib.request
import yaml
from datetime import datetime, timezone
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical, Horizontal
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button, DataTable, Footer, Header, Input, Label,
    RadioButton, RadioSet, Rule, Select, Static,
)

from project_registry.config import DATA_DIR
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
            pid, root = compute_id_and_path(name, base_dir, use_increment=True)
            root.mkdir(parents=True, exist_ok=True)
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
            self.app.push_screen(ProjectDetailScreen(project_info))

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


class ProjectDetailScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("t",      "toggle_timer",   "Start/stop timer", priority=True),
        Binding("r",      "refresh",        "Refresh"),
    ]

    def __init__(self, project: dict) -> None:
        super().__init__()
        self._project = project
        self._data: dict = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield ScrollableContainer(Vertical(id="detail-body"))
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_data()
        self.set_interval(1, self._tick_timer)

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
