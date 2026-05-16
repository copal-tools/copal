"""Optional 3D-View N-panel showing the active CopalPM session.

Visible under the "Copal" tab in the 3D Viewport sidebar (press N to toggle).
Provides a Start / Stop pair of buttons. Both operators keep ``tracker.state``
in sync with what they tell the daemon — without that, the periodic timer
would either send pings for a session the user just manually started but
the tracker doesn't know about, or re-start one the user just stopped.
"""

from __future__ import annotations

import bpy
from bpy.types import Operator, Panel

from . import copalpm_client


class COPAL_OT_start_session(Operator):
    bl_idname = "copal.start_session"
    bl_label = "Start session"
    bl_description = "Start a CopalPM session for the project this .blend belongs to"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):  # type: ignore[no-untyped-def]
        from . import tracker  # local import for the same reasons as the panel
        if tracker.state.get("tracking_project_id"):
            return False  # already tracking — Stop is the visible action
        return bool(bpy.data.filepath)  # nothing to resolve for an untitled file

    def execute(self, context):  # type: ignore[no-untyped-def]
        from . import tracker
        from . import preferences

        filepath = bpy.data.filepath
        if not filepath:
            self.report({"WARNING"}, "Save the .blend file inside a registered project first")
            return {"CANCELLED"}

        # Resolve the project via the same subprocess path the timer uses.
        try:
            prefs = preferences.get(context)
            override = prefs.copalpm_path_override or None
        except Exception:
            override = None
        try:
            match = copalpm_client.whose(filepath, copalpm_path_override=override)
        except copalpm_client.NotInstalledError:
            self.report({"WARNING"}, "CopalPM is not installed or not on PATH")
            return {"CANCELLED"}
        except Exception as e:
            self.report({"WARNING"}, f"copalpm whose failed: {e!r}")
            return {"CANCELLED"}

        if match is None:
            self.report({"WARNING"}, "This .blend is not inside any registered CopalPM project")
            return {"CANCELLED"}

        pid = match["project_id"]
        try:
            copalpm_client.start(pid, tool="blender")
        except copalpm_client.ServiceDownError:
            self.report({"WARNING"}, "CopalPM service is not running")
            return {"CANCELLED"}
        except copalpm_client.NotInstalledError:
            self.report({"WARNING"}, "CopalPM is not installed")
            return {"CANCELLED"}
        except copalpm_client.ApiError as e:
            self.report({"WARNING"}, f"CopalPM error {e.code}: {e.message}")
            return {"CANCELLED"}

        # Keep tracker state in sync. Reset idle signals so the unfocus
        # timer doesn't carry over from a prior session, and start the
        # cursor-static run at 0 so the next tick simply establishes a baseline.
        tracker.state["tracking_project_id"] = pid
        tracker.state["cursor_static_run"] = 0
        tracker.state["unfocus_since"] = None
        self.report({"INFO"}, f"Started session for {pid}")
        return {"FINISHED"}


class COPAL_OT_stop_session(Operator):
    bl_idname = "copal.stop_session"
    bl_label = "Stop session"
    bl_description = "Manually stop the active CopalPM time-tracking session"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):  # type: ignore[no-untyped-def]
        from . import tracker
        return bool(tracker.state.get("tracking_project_id"))

    def execute(self, context):  # type: ignore[no-untyped-def]
        from . import tracker
        try:
            copalpm_client.stop(reason="manual")
        except copalpm_client.ServiceDownError:
            # The daemon is gone — local state is moot, but clear it anyway
            # so the panel reflects "no session" until something resumes.
            tracker.state["tracking_project_id"] = None
            tracker.state["cursor_static_run"] = 0
            tracker.state["unfocus_since"] = None
            self.report({"WARNING"}, "CopalPM service is not running")
            return {"CANCELLED"}
        except copalpm_client.NotInstalledError:
            tracker.state["tracking_project_id"] = None
            self.report({"WARNING"}, "CopalPM is not installed")
            return {"CANCELLED"}
        except copalpm_client.ApiError as e:
            self.report({"WARNING"}, f"CopalPM error {e.code}: {e.message}")
            return {"CANCELLED"}

        # Clear tracker state. Drop last_cursor too — that gives the idle
        # tick handler one "baseline" pass before it can auto-restart on
        # cursor movement, which matches the user's intent ("I just stopped
        # this, give me a moment before resuming").
        tracker.state["tracking_project_id"] = None
        tracker.state["last_cursor"] = None
        tracker.state["cursor_static_run"] = 0
        tracker.state["unfocus_since"] = None
        self.report({"INFO"}, "Stopped session")
        return {"FINISHED"}


class COPAL_PT_status_panel(Panel):
    bl_label = "Copal time tracking"
    bl_idname = "COPAL_PT_status_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Copal"

    def draw(self, context):  # type: ignore[no-untyped-def]
        layout = self.layout

        # The tracker's last-known project_id is used as a cheap proxy for
        # session state — explicitly NOT hitting /state on every panel
        # redraw. The state stays correct because both the timer and the
        # manual operators update it after every transition.
        from . import tracker  # local import to keep panel registration lazy

        pid = tracker.state.get("tracking_project_id")
        if pid:
            layout.label(text=f"Tracking: {pid}", icon="REC")
            layout.operator(COPAL_OT_stop_session.bl_idname, icon="PAUSE")
        else:
            layout.label(text="No active session", icon="DOT")
            row = layout.row()
            row.operator(COPAL_OT_start_session.bl_idname, icon="PLAY")
            # Add an unobtrusive hint when the button is greyed-out because
            # there's no saved .blend yet. poll() handles disabling the
            # button itself; this just tells the user why.
            if not bpy.data.filepath:
                layout.label(text="(save the .blend to a project folder first)", icon="INFO")


_classes = (COPAL_OT_start_session, COPAL_OT_stop_session, COPAL_PT_status_panel)


def register_panel() -> None:
    for cls in _classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            # Already registered from a previous failed register() — re-register clean.
            try:
                bpy.utils.unregister_class(cls)
            except RuntimeError:
                pass
            bpy.utils.register_class(cls)


def unregister_panel() -> None:
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
