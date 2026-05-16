"""Addon preferences for Copal: time tracking.

Exposed under Edit → Preferences → Add-ons → "Copal: time tracking".
The tracker reads these values via ``ctx.prefs`` each tick — changing
them in the UI takes effect on the next tick (no restart required).
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty
from bpy.types import AddonPreferences


# bl_idname must equal the addon's package name (the directory name under
# scripts/addons/). For us that's "copal_blender". The string is the exact
# key Blender uses to look up our preferences in
# `bpy.context.preferences.addons[...]`.
ADDON_PACKAGE = "copal_blender"


class CopalAddonPreferences(AddonPreferences):
    bl_idname = ADDON_PACKAGE

    enabled: BoolProperty(  # type: ignore[valid-type]
        name="Enabled",
        description="Master switch. When disabled, the addon stays loaded but issues no /start, /stop, or /ping calls.",
        default=True,
    )
    ping_interval_sec: IntProperty(  # type: ignore[valid-type]
        name="Ping interval (seconds)",
        description="How often the addon pings the CopalPM daemon and re-checks cursor + focus.",
        default=60,
        min=10,
        max=3600,
    )
    unfocus_stop_sec: IntProperty(  # type: ignore[valid-type]
        name="Unfocus stop threshold (seconds)",
        description="Stop the session after Blender stays unfocused continuously for this many seconds.",
        default=300,
        min=30,
        max=86400,
    )
    cursor_static_pings: IntProperty(  # type: ignore[valid-type]
        name="Cursor-static threshold (pings)",
        description="Stop the session after this many consecutive ticks show no cursor movement.",
        default=2,
        min=2,
        max=50,
    )
    copalpm_path_override: StringProperty(  # type: ignore[valid-type]
        name="copalpm path override",
        description=(
            "Absolute path to the copalpm binary. Leave empty to auto-detect via PATH and "
            "common install locations. Required when Blender is launched from Finder on macOS "
            "with a stripped PATH."
        ),
        default="",
        subtype="FILE_PATH",
    )

    def draw(self, context):  # type: ignore[no-untyped-def]
        layout = self.layout
        layout.prop(self, "enabled")
        col = layout.column(align=True)
        col.prop(self, "ping_interval_sec")
        col.prop(self, "unfocus_stop_sec")
        col.prop(self, "cursor_static_pings")
        layout.separator()
        layout.prop(self, "copalpm_path_override")
        layout.label(
            text="Tip: leave the path override empty unless `copalpm` isn't on PATH inside Blender.",
            icon="INFO",
        )


def get(context) -> CopalAddonPreferences:
    """Return the addon's preferences object for the given Blender context."""
    return context.preferences.addons[ADDON_PACKAGE].preferences  # type: ignore[return-value]
