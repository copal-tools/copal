"""Pytest config for copalblender unit tests.

Adds the vendored Blender-addon source directory to sys.path so unit tests can
`import tracker`, `import copalpm_client`, and `import activity` directly,
without going through Blender's bundled Python.
"""

import sys
from pathlib import Path

_ADDON_SRC = Path(__file__).resolve().parents[2] / "src" / "copalblender" / "assets" / "addon" / "copal_blender"
if str(_ADDON_SRC) not in sys.path:
    sys.path.insert(0, str(_ADDON_SRC))
