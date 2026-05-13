from textual.app import App, ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Header, Footer, Button, Static, Label, Log
from textual.binding import Binding

class CopalVX(App):
    """The Copal-VX Commander Interface."""
    
    CSS = """
    Screen {
        layout: grid;
        grid-size: 2;
        grid-columns: 1fr 2fr;
    }
    
    #sidebar {
        height: 100%;
        background: $panel;
        border-right: vkey $accent;
        padding: 1;
    }

    #main_area {
        height: 100%;
        padding: 1;
    }
    
    .menu_btn {
        width: 100%;
        margin-bottom: 1;
    }
    
    Log {
        border: solid $accent;
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("d", "toggle_dark", "Toggle Dark Mode"),
    ]

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header(show_clock=True)
        
        # Left Column: Sidebar Menu
        with Container(id="sidebar"):
            yield Label("📁 PROJECTS", id="lbl_projects")
            yield Button("New / Upload", id="btn_push", classes="menu_btn", variant="primary")
            yield Button("Restore / Pull", id="btn_pull", classes="menu_btn", variant="success")
            yield Button("Settings", id="btn_settings", classes="menu_btn")
            
        # Right Column: Main Content
        with Container(id="main_area"):
            yield Label("📟 SYSTEM LOG")
            yield Log(id="sys_log")
            
        yield Footer()

    def on_mount(self) -> None:
        """Called when app starts."""
        self.title = "Copal-VX Commander"
        self.query_one(Log).write_line("✅ System initialized.")
        self.query_one(Log).write_line("ℹ️  Ready for commands.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks."""
        log = self.query_one(Log)
        if event.button.id == "btn_push":
            log.write_line("🚀 Starting Upload Wizard...")
        elif event.button.id == "btn_pull":
            log.write_line("⬇️  Starting Restore Wizard...")

if __name__ == "__main__":
    app = CopalVX()
    app.run()